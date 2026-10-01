"""数据库驱动的轻量 AI worker。

本地开发时 worker 与 FastAPI 进程同进程运行；任务记录本身落库，启动恢复
时会重新拾取 queued/running 任务。云端可以将同一 run_task 函数放到独立
worker 进程，不改变业务接口。
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import select

from backend.app import models
from backend.app.db import SessionLocal
from backend.app.domain import financials
from backend.app.integrations.ai import workflow
from backend.app.integrations.ai.artifacts import artifact_store
from contracts.errors import LLMFailed
from contracts.errors import ValidationExhausted
from backend.app.observability import event
from backend.app.observability import log
from backend.app.observability import task_id as log_task_id
from backend.app.observability import request_id as log_request_id


# ⚠️ 多 worker 部署约束：本注册表是进程内 asyncio.Task 句柄，无法序列化/跨进程共享。
# 必须保持 gunicorn `--workers 1`（见 deploy/systemd/case-sim.service 第 4 条）：
# 多 worker 时 schedule_task 去重失效、cancel_task 静默失效，且每个 worker 启动都会
# 各跑一次 recover_pending_tasks()，同一任务被重复调度 → 重复调用模型、双倍消耗额度。
_running: Dict[str, asyncio.Task[Any]] = {}


def schedule_task(task_id: str) -> None:
    """将任务加入当前事件循环；重复调用不会重复执行。"""
    if task_id in _running and not _running[task_id].done():
        return
    task = asyncio.create_task(run_task(task_id), name="case-sim-ai-%s" % task_id[:12])
    _running[task_id] = task
    task.add_done_callback(lambda _: _running.pop(task_id, None))


async def cancel_task(task_id: str) -> None:
    """Cancel an in-process worker before its owning student submission changes."""
    task = _running.get(task_id)
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def recover_pending_tasks() -> None:
    """应用启动时恢复上次进程中断的任务。"""
    with SessionLocal() as db:
        ids = db.scalars(
            select(models.AiTask.task_id).where(models.AiTask.status.in_(("queued", "running")))
        ).all()
    for task_id in ids:
        event("AI任务：重启恢复", task_id=task_id)
        schedule_task(task_id)


def _step(db, task: models.AiTask, stage: str, status: str = "running", detail: Optional[dict] = None, error: Optional[str] = None):
    row = models.AiTaskStep(
        task_id=task.id,
        stage=stage,
        status=status,
        attempt=task.attempts or 1,
        detail_json=detail,
        error=error,
        finished_at=datetime.now() if status in ("succeeded", "failed") else None,
    )
    db.add(row)
    task.stage = stage
    db.commit()
    event("AI工作流阶段", task_id=task.task_id, case_id=task.case_id,
          session_id=task.session_id, stage=stage, status=status,
          attempt=task.attempts, detail=detail, error=error)
    return row


async def run_task(task_id: str) -> None:
    context = log_task_id.set(task_id)
    request_context = log_request_id.set("-")
    try:
        await _run_task(task_id)
    finally:
        log_task_id.reset(context)
        log_request_id.reset(request_context)


async def _run_task(task_id: str) -> None:
    with SessionLocal() as db:
        task = db.scalar(select(models.AiTask).where(models.AiTask.task_id == task_id))
        if task is None or task.status in ("succeeded", "failed", "cancelled"):
            return
        task.status = "running"
        task.started_at = task.started_at or datetime.now()
        task.attempts = (task.attempts or 0) + 1
        task.error = None
        db.commit()
        event("AI任务开始", kind=task.kind, case_id=task.case_id,
              session_id=task.session_id, attempt=task.attempts)
        try:
            if task.kind == "case_generation":
                await _run_case_generation(db, task)
            elif task.kind == "case_nodes":
                await _run_case_nodes(db, task)
            elif task.kind == "student_review":
                await _run_student_review(db, task)
            elif task.kind == "impact_rules":
                from .impact_rules import run
                _step(db, task, "generate_impact_rules")
                await run(db, task)
            else:
                raise RuntimeError("未知 AI 任务类型：%s" % task.kind)
        except LLMFailed as exc:
            log.warning("AI任务调用失败：%s", exc)
            if exc.retryable:
                _retry_or_fail(db, task, str(exc))
            else:
                _fail(db, task, str(exc))
        except ValidationExhausted as exc:
            _fail(db, task, str(exc))
        except Exception as exc:  # noqa: BLE001 - 任务边界必须落库失败原因
            log.exception("AI任务执行异常")
            _retry_or_fail(db, task, "%s: %s" % (exc.__class__.__name__, exc))
        else:
            event("AI任务结束", status=task.status, stage=task.stage)


def _fail(db, task: models.AiTask, error: str) -> None:
    task.status = "failed"
    task.stage = "failed"
    task.error = error[:4000]
    task.finished_at = datetime.now()
    if task.kind in ("case_generation", "case_nodes") and task.case is not None and task.case.status == "generating":
        # 重新生成已发布案例失败时，保留上一版学生可用快照；新草稿由教师确认后再发布。
        task.case.status = str((task.input_json or {}).get("previous_status") or "ready")
    _step(db, task, "failed", status="failed", error=task.error)
    db.commit()


def _retry_or_fail(db, task: models.AiTask, error: str) -> None:
    """有限重试；最终失败才把案例退回 draft。"""
    if (task.attempts or 0) < (task.max_attempts or 1):
        task.status = "queued"
        task.stage = "retrying"
        task.error = error[:4000]
        _step(db, task, "retrying", status="failed", error=task.error)
        db.commit()
        asyncio.get_running_loop().call_later(0.5, schedule_task, task.task_id)
        return
    _fail(db, task, error)


async def _run_case_generation(db, task: models.AiTask) -> None:
    case = db.get(models.Case, task.case_id)
    if case is None:
        raise RuntimeError("案例不存在")
    expected_version = int((task.input_json or {}).get("case_version") or case.version or 1)
    source_text = str((task.input_json or {}).get("source_text") or "")
    source_kind = str((task.input_json or {}).get("source_kind") or "material")
    case_type = str((task.input_json or {}).get("case_type") or case.case_type)
    framework = db.get(models.Framework, case.framework_id) if case.framework_id else None

    _step(db, task, "extract_source", status="succeeded", detail={"chars": len(source_text)})
    existing_source = db.scalar(select(models.AiArtifact).where(
        models.AiArtifact.task_id == task.id,
        models.AiArtifact.kind == "source_text",
    ))
    if existing_source is None:
        artifact_store.save_text(db, source_text, kind="source_text", task_id=task.id, case_id=case.id)
        db.commit()

    raw_financials = (task.input_json or {}).get("financial_data")
    if isinstance(raw_financials, dict):
        calculated = financials.calculate(raw_financials)
        artifact_store.save_json(db, calculated, kind="financial_calculation", task_id=task.id, case_id=case.id)
        db.commit()
        if calculated.get("errors"):
            raise RuntimeError("财务数据计算失败：" + "；".join(calculated["errors"]))

    _step(db, task, "generate_case")
    payload, errors = await workflow.generate_valid_baseline(source_text, source_kind, case_type, framework)
    if payload is None:
        artifact_store.save_json(db, {"errors": errors}, kind="validation_report", task_id=task.id, case_id=case.id)
        db.commit()
        raise ValidationExhausted("案例生成未通过校验：" + "；".join(errors or ["未知错误"]))

    artifact_store.save_json(db, payload, kind="generated_case", task_id=task.id, case_id=case.id)
    _step(db, task, "validate_case", status="succeeded", detail={"errors": []})
    _step(db, task, "persist_draft")
    # The comparison and update are atomic, including edits made while the model awaited.
    workflow.persist_generated_case(db, case, payload, expected_version=expected_version, persist_nodes=False)
    case.status = "ready"
    task.status = "succeeded"
    task.stage = "completed"
    task.output_json = payload
    task.finished_at = datetime.now()
    _step(db, task, "persist_draft", status="succeeded", detail={"case_id": case.id})
    db.commit()


async def _run_case_nodes(db, task: models.AiTask) -> None:
    case = db.get(models.Case, task.case_id)
    if case is None:
        raise RuntimeError("案例不存在")
    expected_version = int((task.input_json or {}).get("case_version") or case.version or 1)
    baseline_payload = dict((task.input_json or {}).get("baseline_payload") or {})
    baseline_payload["base_metrics"] = case.base_metrics_json
    baseline_payload["financial_assumptions"] = case.financial_assumptions_json or {}
    _step(db, task, "generate_nodes")
    framework = db.get(models.Framework, case.framework_id) if case.framework_id else None
    hint = ("教师已确认案例基准指标：%s。以其作为决策递进基准，保持同一经营范围和时间口径；"
            "不要把企业总营收改写成单店营收，不要用行业单店区间替换教师数据；"
            "每个选项必须提供教学假设 operating_cost、operating_expense，金额单位万元。"
            % baseline_payload["base_metrics"])
    hint += "\n教师已保存的案例背景（数据）：%s\n经营困境（数据）：%s\n基准营业成本和费用（数据）：%s" % (
        case.background or "", case.dilemma or "", baseline_payload["financial_assumptions"])
    payload, errors = await workflow.generate_valid_case(case.source_text, case.source_kind, case.case_type,
        framework, extra_hint=hint, base_metrics=baseline_payload["base_metrics"])
    if payload is None:
        artifact_store.save_json(db, {"errors": errors}, kind="validation_report", task_id=task.id, case_id=case.id)
        db.commit()
        raise ValidationExhausted("决策节点生成未通过校验（教师已确认的基准数据仍保留）：" + "；".join(errors or ["未知错误"]))
    payload["base_metrics"] = baseline_payload["base_metrics"]
    payload["financial_assumptions"] = baseline_payload["financial_assumptions"]
    artifact_store.save_json(db, payload, kind="generated_case_nodes", task_id=task.id, case_id=case.id)
    workflow.persist_generated_case(db, case, payload, expected_version=expected_version, persist_nodes=True, update_baseline=False)
    case.status = "ready"
    task.status = "succeeded"; task.stage = "completed"; task.output_json = payload; task.finished_at = datetime.now()
    _step(db, task, "persist_nodes", status="succeeded", detail={"case_id": case.id, "nodes": len(payload.get("nodes", []))})
    db.commit()


async def _run_student_review(db, task: models.AiTask) -> None:
    case = db.get(models.Case, task.case_id)
    session = db.get(models.Session, task.session_id)
    if case is None or session is None:
        raise RuntimeError("复盘关联的案例或推演不存在")
    attempt_no = int((task.input_json or {}).get("attempt_no") or session.attempt_no)
    turns = [t for t in sorted(session.turns, key=lambda item: item.id) if t.attempt_no == attempt_no]
    if len(turns) < 3:
        raise RuntimeError("推演尚未完成，不能生成复盘")
    existing = db.scalar(select(models.Review).where(models.Review.session_id == session.id,
                                                     models.Review.attempt_no == attempt_no))
    if existing is not None:
        task.status, task.stage, task.finished_at = "succeeded", "completed", datetime.now()
        db.commit()
        return
    _step(db, task, "prepare_review", status="succeeded", detail={"turns": len(turns)})
    _step(db, task, "generate_review")
    from ai_component import StudentReviewAgent
    def record(stage, data):
        artifact = artifact_store.save_json(db, data, kind=stage, task_id=task.id, case_id=case.id, session_id=session.id)
        _step(db, task, stage, status="succeeded", detail={"artifact_id": artifact.id})
    from .evidence import review_input
    payload = await StudentReviewAgent().run(review_input(case, session, turns), record)
    artifact_store.save_json(db, payload, kind="student_review", task_id=task.id,
                             case_id=case.id, session_id=session.id)
    # 生成期间可能已有人工/其他任务写入；已有记录始终权威，不能覆盖。
    db.expire_all()
    existing = db.scalar(select(models.Review).where(
        models.Review.session_id == session.id,
        models.Review.attempt_no == attempt_no,
    ))
    if existing is None:
        existing = models.Review(
            session_id=session.id,
            attempt_no=attempt_no,
            framework_type=payload["framework_type"],
            dimensions_json=payload["dimensions"],
            conclusion=payload["conclusion"],
        )
        db.add(existing)
    _step(db, task, "generate_review", status="succeeded")
    task.status = "succeeded"
    task.stage = "completed"
    task.output_json = payload
    task.finished_at = datetime.now()
    db.commit()
