"""学生端 6 条接口（S1–S6）。

S1–S4 为真实实现；S5 / S6（学生自助试算）仍为占位，属后续步骤范围。

红线：S1 的响应文本中不得出现带引号的键名 "risk_level" / "metrics" / "summary"。
（`base_metrics` 是题面基准数据，是合法字段，检查时不可用裸子串匹配。）
S2 的 result 会返回所选选项的 metrics 与 summary —— 这是学生**已提交**之后的结果，
按 SPEC 允许下发。
"""
import json
import os
import hashlib
import re
import threading
import uuid
from collections import OrderedDict
from datetime import datetime
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from backend.app import models
from backend.app.domain import rules
from backend.app import schemas
from backend.app.db import SessionLocal
from backend.app.db import get_db
from backend.app.domain import validator
from contracts.errors import LLMFailed
from backend.app.domain import decision_engine
from backend.app.integrations.ai.task_runner import schedule_task
from backend.app.services.student_auth import current_student
from backend.app.services.student_submissions import delete_submission, overwrite_submission, rollback_submission
from backend.app.observability import event
from backend.app.observability import log
from urllib.parse import urlsplit
import time

from backend.app.integrations.ai.trial import generate_try_case as _generate_try_case

router = APIRouter(prefix="/api/play", tags=["学生端"], dependencies=[Depends(current_student)])


async def _cancel_review_tasks(db: OrmSession, session: models.Session) -> None:
    """Cancel queued/running review work before the student edits the submission."""
    from backend.app.integrations.ai.task_runner import cancel_task
    task_ids = db.scalars(select(models.AiTask.task_id).where(
        models.AiTask.session_id == session.id,
        models.AiTask.kind == "student_review",
        models.AiTask.status.in_(("queued", "running")),
    )).all()
    for task_id in task_ids:
        await cancel_task(task_id)
    if task_ids:
        tasks = db.scalars(select(models.AiTask).where(models.AiTask.task_id.in_(task_ids))).all()
        now = datetime.now()
        for task in tasks:
            if task.status in ("queued", "running"):
                task.status = "cancelled"
                task.stage = "cancelled"
                task.error = "学生修改了推演记录，复盘任务已取消。"
                task.finished_at = now
        db.commit()

#: 流式转发用的超时，与 llm.REQUEST_TIMEOUT 保持一致
STREAM_TIMEOUT = 60.0
#: 复盘维度校验不通过时允许的重新生成次数
MAX_REVIEW_REGENERATE = 2


# ─────────────────────────── 小工具 ───────────────────────────

def _get_published_case(db: OrmSession, student_token: str) -> models.Case:
    """按 student_token 取已发布案例；不存在或未发布一律 404。"""
    case = db.scalar(
        select(models.Case).where(
            models.Case.student_token == student_token,
            models.Case.status == schemas.CaseStatus.published.value,
        )
    )
    if case is None:
        raise HTTPException(status_code=404, detail="推演链接无效")
    return case


def _get_session_or_fail(db: OrmSession, case: models.Case, session_id: int, student_id: int) -> models.Session:
    session = db.get(models.Session, session_id)
    if session is None or session.case_id != case.id or session.student_id != student_id:
        raise HTTPException(status_code=404, detail="推演记录不存在")
    return session


def _options_of(node: models.Node) -> List[models.OptionResult]:
    return sorted(node.option_results, key=lambda o: o.option_key)


from backend.app.domain.student_labels import student_label as _student_label
from backend.app.domain.student_choices import ordered_choices, choice_receipt


def _student_node(node: models.Node, student_token: str = "") -> schemas.StudentNode:
    """学生端节点：选项**只有** key 与 label，绝不带后果信息。"""
    options = [
        schemas.StudentOption(key=o.option_key, label=_student_label(o.label, o.option_key))
        for o in _options_of(node)
    ]
    # 对同一个案例/节点保持稳定的伪随机顺序：刷新不会改变答案 key 映射，
    # 但不再把 A/B/C 固定呈现为某种风险等级顺序。
    seed = "%s:%s:%s" % (student_token, node.case_id, node.id)
    options.sort(key=lambda option: hashlib.sha256((seed + ":" + option.key).encode()).hexdigest())
    return schemas.StudentNode(
        id=node.id,
        idx=node.idx,
        scenario=node.scenario,
        node_role=node.node_role,
        title=node.title,
        background=node.background,
        options=options,
    )


def _attempt_turns(session: models.Session, attempt_no: int) -> List[models.Turn]:
    """取该 session 指定 attempt 的回合，按 id 升序。"""
    return [
        t
        for t in sorted(session.turns, key=lambda x: (x.attempt_no, x.id))
        if t.attempt_no == attempt_no
    ]


def _enqueue_review_task(db: OrmSession, case: models.Case, session: models.Session) -> Optional[models.AiTask]:
    """为已完成推演创建可恢复的复盘任务；重复请求复用原任务。"""
    attempt_no = session.attempt_no
    existing_review = db.scalar(select(models.Review).where(
        models.Review.session_id == session.id,
        models.Review.attempt_no == attempt_no,
    ))
    if existing_review is not None:
        return None
    task = db.scalar(select(models.AiTask).where(
        models.AiTask.session_id == session.id,
        models.AiTask.kind == "student_review",
    ).order_by(models.AiTask.id.desc()))
    if task is not None and task.status not in ("queued", "running"):
        return task
    if task is None:
        task = models.AiTask(
            task_id=uuid.uuid4().hex,
            case_id=case.id,
            session_id=session.id,
            kind="student_review",
            status="queued",
            stage="queued",
            input_json={"attempt_no": attempt_no},
        )
        db.add(task)
        db.commit()
        db.refresh(task)
    schedule_task(task.task_id)
    return task

def _snapshot(case: models.Case) -> Dict[str, Any]:
    from backend.app.services.simulation import published_snapshot
    return published_snapshot(case)

def _snap_node(session: models.Session, idx: int) -> Dict[str, Any]:
    return next(n for n in session.case_snapshot_json.get("nodes", []) if n["idx"] == idx)

def _base_metrics(data: Dict[str, Any]) -> Dict[str, Any]:
    keys = {"revenue", "gross_margin", "market_share", "cash_flow"}
    if keys <= set(data): return {k:data[k] for k in keys | {"operating_cost", "operating_expense", "net_profit", "cash_flow_amount"} if k in data}
    return {"revenue": float(data.get("revenue", data.get("monthly_revenue_per_store", 0))),
            "gross_margin": data.get("gross_margin", "0.0%"), "market_share": data.get("market_share", "0.0%"),
            "cash_flow": str(data.get("cash_flow", "稳健"))}


# ─────────────────────────── S1 打开推演链接 ───────────────────────────

@router.get("/{student_token}", response_model=schemas.PlayResponse)
def play(
    student_token: str,
    db: OrmSession = Depends(get_db),
) -> schemas.PlayResponse:
    """一次下发全部节点，外加首屏所需的题面信息（标题/类型/背景/困境/基准数据）。

    注意红线：nodes 里的 options 只给 {key, label}。
    """
    case = _get_published_case(db, student_token)
    snap = _snapshot(case)
    return schemas.PlayResponse(
        case_title=snap["title"], case_type=snap["case_type"], background=snap["background"],
        dilemma=snap["dilemma"], base_metrics=snap["base_metrics"],
        nodes=[schemas.StudentNode(id=n["id"], idx=n["idx"], node_role=n["node_role"], title=n["title"],
            background=n["background"], scenario=n["background"], options=[
                schemas.StudentOption(key=o["key"], label=_student_label(o["label"], o["key"]))
                for o in sorted(n["options"], key=lambda o: hashlib.sha256(f'{student_token}:{case.id}:{n["id"]}:{o["key"]}'.encode()).hexdigest())])
            for n in snap["nodes"]],
    )


# ─────────────────────────── S2 提交决策 ───────────────────────────

def _session_state(session: models.Session) -> schemas.SessionState:
    snap = session.case_snapshot_json
    turns = _attempt_turns(session, session.attempt_no)
    nodes = []
    for n in snap["nodes"]:
        options = [schemas.StudentOption(key=o["key"], label=_student_label(o["label"], o["key"]))
                   for o in ordered_choices(session.id, n["id"], n["options"])]
        nodes.append(schemas.StudentNode(**{k: n[k] for k in ("id", "idx", "node_role", "title", "background")}, scenario=n["background"], options=options))
    return schemas.SessionState(
        session_id=session.id, student_name=session.student_name, case_version=session.case_version,
        current_metrics=_base_metrics(session.current_metrics_json), finished=session.finished_at is not None,
        financial_state=session.current_metrics_json if snap.get("simulation", {}).get("enabled") else None,
        next_node_id=next((n["id"] for n in snap["nodes"] if n["idx"] == len(turns) + 1), None),
        play=schemas.PlayResponse(case_title=snap["title"], case_type=snap["case_type"], background=snap["background"],
                                  dilemma=snap["dilemma"], base_metrics=snap["base_metrics"], nodes=nodes),
        turns=[{"node_id": t.node_id, "chosen_option": t.chosen_option, "input_text": t.input_text,
                **choice_receipt(session, t.node_id, t.chosen_option),
                "after_metrics": t.after_metrics_json, "summary": t.result_json.get("summary", "")} for t in turns],
    )


@router.post("/{student_token}/sessions", response_model=schemas.SessionState)
def start_session(student_token: str, payload: schemas.StartSessionRequest, db: OrmSession = Depends(get_db), account=Depends(current_student)):
    case = _get_published_case(db, student_token)
    name = account.real_name
    if not name:
        raise HTTPException(400, "请输入姓名")
    existing = db.scalar(select(models.Session).where(
        models.Session.case_id == case.id, models.Session.student_id == account.id
    ).order_by(models.Session.id.desc()))
    if existing is not None:
        raise HTTPException(409, detail={"code": "submission_exists", "session_id": existing.id,
            "message": "已有本案例推演记录，请从提交记录继续，或选择覆盖原记录后重做。"})
    attempt = db.scalar(select(func.count()).select_from(models.Session).where(
        models.Session.case_id == case.id, models.Session.student_id == account.id)) or 0
    session = models.Session(case_id=case.id, student_id=account.id, student_name=name, attempt_no=attempt + 1,
        case_version=_snapshot(case)["version"], case_snapshot_json=_snapshot(case),
        current_metrics_json=_base_metrics(_snapshot(case)["base_metrics"]))
    db.add(session)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        existing = db.scalar(select(models.Session).where(
            models.Session.case_id == case.id, models.Session.student_id == account.id
        ).order_by(models.Session.id.desc()))
        raise HTTPException(status_code=409, detail={"code": "submission_exists",
            "session_id": existing.id if existing else None,
            "message": "已有本案例推演记录，请从提交记录继续，或选择覆盖原记录后重做。"}) from exc
    from backend.app.integrations.ai.tutor import save_case_file
    save_case_file(db, session)
    db.commit()
    return _session_state(session)


@router.post("/{student_token}/sessions/{session_id}/reset", response_model=schemas.SessionState)
async def reset_session(student_token: str, session_id: int, db: OrmSession = Depends(get_db), account=Depends(current_student)):
    case = _get_published_case(db, student_token)
    session = _get_session_or_fail(db, case, session_id, account.id)
    await _cancel_review_tasks(db, session)
    snapshot = _snapshot(case)
    overwrite_submission(db, session, snapshot, _base_metrics(snapshot["base_metrics"]), snapshot["version"])
    event("学生端：覆盖原推演记录", case_id=case.id, session_id=session.id, case_version=session.case_version)
    return _session_state(session)


@router.delete("/{student_token}/sessions/{session_id}")
async def remove_session(student_token: str, session_id: int, db: OrmSession = Depends(get_db), account=Depends(current_student)):
    case = _get_published_case(db, student_token)
    session = _get_session_or_fail(db, case, session_id, account.id)
    await _cancel_review_tasks(db, session)
    delete_submission(db, session)
    event("学生端：删除推演记录", case_id=case.id, session_id=session_id)
    return {"ok": True}


@router.post("/{student_token}/sessions/{session_id}/rollback/{node_idx}", response_model=schemas.SessionState)
async def rollback_session(student_token: str, session_id: int, node_idx: int,
                     db: OrmSession = Depends(get_db), account=Depends(current_student)):
    case = _get_published_case(db, student_token)
    session = _get_session_or_fail(db, case, session_id, account.id)
    await _cancel_review_tasks(db, session)
    try:
        rollback_submission(db, session, node_idx)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    event("学生端：回退并修改推演", case_id=case.id, session_id=session.id, from_node=node_idx)
    return _session_state(session)


@router.get("/{student_token}/sessions/{session_id}", response_model=schemas.SessionState)
def session_state(student_token: str, session_id: int, db: OrmSession = Depends(get_db), account=Depends(current_student)):
    case = _get_published_case(db, student_token)
    session = _get_session_or_fail(db, case, session_id, account.id)
    if not (session.case_snapshot_json or {}).get("nodes"):
        raise HTTPException(409, "旧记录缺少题目快照，请重新开始")
    return _session_state(session)

@router.post("/{student_token}/decide", response_model=schemas.DecideResponse)
async def decide(
    student_token: str,
    payload: schemas.DecideRequest,
    db: OrmSession = Depends(get_db),
    account=Depends(current_student),
) -> schemas.DecideResponse:
    """提交一次决策：写 turn，返回该选项的指标与摘要，并给出下一节点。"""
    case = _get_published_case(db, student_token)

    has_option = payload.option_key is not None
    event("学生端：提交决策", case_id=case.id, session_id=payload.session_id,
          node_id=payload.node_id, option=payload.option_key,
          mode="已发布选项（确定性计算，不调用模型）")
    has_text = bool((payload.input_text or "").strip())
    if not has_option:
        raise HTTPException(status_code=400, detail="请选择教师已发布的策略；文字可作为决策理由保存，不进行未经教师确认的AI测算")
    if not payload.student_name.strip():
        raise HTTPException(400, "请输入姓名")

    # session_id 为空才新建；新 session 冻结完整题面
    if payload.session_id is None:
        done_count = db.scalar(
            select(func.count())
            .select_from(models.Session)
            .where(
                models.Session.case_id == case.id,
                models.Session.student_id == account.id,
            )
        ) or 0
        session = models.Session(
            case_id=case.id,
            student_name=account.real_name,
            student_id=account.id,
            attempt_no=done_count + 1,
            case_version=_snapshot(case)["version"],
            case_snapshot_json=_snapshot(case),
            current_metrics_json=_base_metrics(_snapshot(case)["base_metrics"]),
        )
        db.add(session)
        db.commit()
        db.refresh(session)
    else:
        session = _get_session_or_fail(db, case, payload.session_id, account.id)

    attempt_no = session.attempt_no
    if session.finished_at is not None:
        raise HTTPException(status_code=409, detail="本次推演已完成，不能再次提交")
    snap = session.case_snapshot_json or _snapshot(case)
    turns = _attempt_turns(session, attempt_no)
    expected_idx = len(turns) + 1
    if expected_idx > 3:
        raise HTTPException(status_code=409, detail="本次推演已完成，不能再次提交")
    node_data = next((n for n in snap.get("nodes", []) if n.get("id") == payload.node_id), None)
    if node_data is None:
        raise HTTPException(status_code=409, detail="节点不属于当前会话题目")
    if node_data.get("idx") != expected_idx:
        raise HTTPException(status_code=409, detail="请按顺序提交第 %d 个节点" % expected_idx)
    try:
        before = decision_engine.normalize_metrics(_base_metrics(session.current_metrics_json or snap.get("base_metrics") or {}))
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(422, "案例基准指标无法计算，请教师核对百分比与营收：" + str(exc)) from exc

    if has_option:
        option = next((o for o in node_data.get("options", []) if o.get("key") == payload.option_key.value), None)
        if option is None: raise HTTPException(status_code=404, detail="选项不存在")
        chosen_option = option["key"]; outcome = {"after_metrics": option.get("metrics"), "summary": option.get("summary") or "", "source":"preset", "warning":None}
        config = snap.get("simulation", {})
        if config.get("enabled"):
            from backend.app.domain.progressive import apply_impact
            try:
                after = apply_impact(before, config["rules"][f'{node_data["id"]}:{chosen_option}'])
            except (ValueError, KeyError, TypeError) as exc:
                raise HTTPException(422, "递进规则无法计算，请教师核对：" + str(exc)) from exc
            outcome = {"after_metrics": after, "source": "progressive", "warning": None,
                "summary": f'按上一轮数据与教师确认规则计算：营收 {after["revenue"]:.2f} 万元，'
                    f'营业成本 {after["operating_cost"]:.2f} 万元，运营费用 {after["operating_expense"]:.2f} 万元，'
                    f'净利润 {after["net_profit"]:.2f} 万元；{after["cash_flow"]}。'}
    try: after = decision_engine.normalize_metrics(_base_metrics(outcome["after_metrics"])); ok, reason = decision_engine.validate_metrics(after)
    except Exception: ok, reason = False, "结果指标结构无效"
    if not ok: raise HTTPException(status_code=502, detail=reason)
    d = decision_engine.delta(before, after)
    result_source = schemas.ResultSource(outcome.get("source") or ("preset" if chosen_option else "custom_llm"))
    result = schemas.DecideResult(before_metrics=schemas.Metrics.model_validate(before), after_metrics=schemas.Metrics.model_validate(after), delta_metrics=d, summary=outcome["summary"], source=result_source, warning=outcome.get("warning"))
    if outcome["source"] == "progressive":
        result.financial_state = after

    next_node_id = next((n.get("id") for n in snap.get("nodes", []) if n.get("idx") == expected_idx + 1), None)

    db.add(
        models.Turn(
            session_id=session.id,
            node_id=payload.node_id,
            input_text=(payload.input_text or None),
            chosen_option=chosen_option,
            result_json={"before_metrics": before, "after_metrics": after, "delta_metrics": d, "summary": result.summary, "source": outcome.get("source"), "warning": outcome.get("warning")},
            duration_ms=payload.duration_ms,
            attempt_no=attempt_no,
            before_metrics_json=before, after_metrics_json=after, delta_metrics_json=d,
            result_source=outcome.get("source"),
        )
    )
    session.current_metrics_json = after
    if next_node_id is None: session.finished_at = datetime.now()
    db.flush()
    db.expire(session, ["turns"])
    from backend.app.integrations.ai.artifacts import artifact_store
    artifact_store.save_json(db, {"session_id": session.id, "student_id": session.student_id,
        "student_name": session.student_name, "case_version": session.case_version,
        "attempt_no": session.attempt_no, "state": _session_state(session).model_dump(mode="json")},
        kind="student_submission", case_id=case.id, session_id=session.id)
    db.commit()

    if next_node_id is None:
        _enqueue_review_task(db, case, session)

    event("学生端：决策结果已保存", case_id=case.id, session_id=session.id,
          option_key=chosen_option, display_option=choice_receipt(session, payload.node_id, chosen_option)["display_option"],
          node_id=payload.node_id, source=outcome.get("source"), after_metrics=after,
          delta=d, next_node_id=next_node_id, review_queued=next_node_id is None)
    return schemas.DecideResponse(
        session_id=session.id, result=result, next_node_id=next_node_id
    )


# ─────────────────────────── S3 AI 助教对话（SSE） ───────────────────────────

@router.get("/{student_token}/sessions/{session_id}/messages")
def messages(student_token: str, session_id: int, db: OrmSession = Depends(get_db), account=Depends(current_student)):
    from contracts.presentation import readable_tutor_history
    case = _get_published_case(db, student_token)
    session = _get_session_or_fail(db, case, session_id, account.id)
    return [{"role": m.role, "content": readable_tutor_history(m.content) if m.role == 'assistant' else m.content}
            for m in sorted(session.messages, key=lambda m: m.id)[-30:]]

@router.post("/{student_token}/chat", response_class=StreamingResponse)
async def chat(
    student_token: str,
    payload: schemas.ChatRequest,
    db: OrmSession = Depends(get_db),
    account=Depends(current_student),
) -> StreamingResponse:
    """SSE 流式答疑；返回 text/event-stream，不适用 JSON 的 response_model。"""
    case = _get_published_case(db, student_token)
    session = _get_session_or_fail(db, case, payload.session_id, account.id)
    attempt_no = session.attempt_no
    session_id = session.id


    # 学生提问先落库
    db.add(models.Message(session_id=session_id, role=schemas.MessageRole.user.value,
                          content=payload.message))
    db.commit()

    async def event_stream() -> AsyncIterator[str]:
        chunks: List[str] = []
        started = time.perf_counter()
        try:
            from backend.app.integrations.ai.tutor import answer_question
            # 在独立的短生命周期数据会话中读取当前推演文件，输出校验后再发给学生。
            with SessionLocal() as agent_db:
                agent_session = agent_db.get(models.Session, session_id)
                reply = await answer_question(agent_db, agent_session, payload.message)
            for piece in [reply]:
                chunks.append(piece)
                yield "data: " + json.dumps({"delta": piece}, ensure_ascii=False) + "\n\n"
            event("学生答疑：输出完成", case_id=case.id, session_id=session_id,
                  output_characters=sum(len(c) for c in chunks),
                  elapsed_ms=round((time.perf_counter() - started) * 1000))
        except LLMFailed as exc:
            log.warning("学生答疑失败 session=%s reason=%s", session_id, exc)
            yield "data: " + json.dumps({"error": str(exc)}, ensure_ascii=False) + "\n\n"
        except Exception:
            log.exception("学生答疑网络或流式处理异常 session=%s", session_id)
            yield "data: " + json.dumps({"error": "答疑失败，请查看运行日志"}, ensure_ascii=False) + "\n\n"
        finally:
            # 用独立会话落库：请求级的 db 依赖此时可能已回收
            if chunks:
                try:
                    with SessionLocal() as write_db:
                        write_db.add(models.Message(
                            session_id=session_id,
                            role=schemas.MessageRole.assistant.value,
                            content="".join(chunks),
                        ))
                        write_db.commit()
                except Exception:  # noqa: BLE001 —— 落库失败不影响已发出的流
                    log.exception("学生答疑记录保存失败 session=%s", session_id)
        yield "data: " + json.dumps({"done": True}, ensure_ascii=False) + "\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ─────────────────────────── 复盘生成（S2 收尾自动触发 + S4 懒生成） ───────────────────────────

# ─────────────────────────── S4 复盘 ───────────────────────────

@router.get("/{student_token}/review", response_model=schemas.ReviewResponse)
async def review(
    student_token: str,
    session_id: int = Query(..., description="推演 ID"),
    db: OrmSession = Depends(get_db),
    account=Depends(current_student),
) -> schemas.ReviewResponse:
    """复盘报告：动态返回已保存分析项，不补齐项数或重排。"""
    case = _get_published_case(db, student_token)
    session = _get_session_or_fail(db, case, session_id, account.id)
    if session.finished_at is None:
        raise HTTPException(status_code=400, detail="推演尚未完成")

    from backend.app.domain.path_comparison import path_comparison
    comparison = path_comparison(session.case_snapshot_json or {}, [t for t in session.turns if t.attempt_no == session.attempt_no])

    row = db.scalar(select(models.Review).where(
        models.Review.session_id == session.id,
        models.Review.attempt_no == session.attempt_no,
    ))
    if row is None:
        task = _enqueue_review_task(db, case, session)
        if task is None:
            raise HTTPException(status_code=503, detail="复盘生成失败，请稍后重试")
        task_status = task.status if task.status in ("pending", "queued", "running", "failed") else "pending"
        task_message = "复盘生成失败：%s" % task.error if task_status == "failed" else "复盘正在生成，请稍后刷新。"
        return schemas.ReviewResponse(
            path_comparison=None,
            framework_type=rules.CASE_TYPE_TO_FRAMEWORK.get(case.case_type, "4P营销理论"),
            dimensions=[],
            conclusion=task_message,
            status="pending" if task_status == "queued" else task_status,
            task_id=task.task_id,
            stage=task.stage,
            attempts=task.attempts,
        )

    return schemas.ReviewResponse(
        framework_type=row.framework_type,
        path_comparison=comparison,
        dimensions=row.dimensions_json,
        conclusion=row.conclusion,
        status="succeeded",
    )


@router.post("/{student_token}/review/retry", response_model=schemas.OkResponse)
async def retry_review(student_token: str, session_id: int = Query(...), db: OrmSession = Depends(get_db), account=Depends(current_student)):
    case = _get_published_case(db, student_token)
    session = _get_session_or_fail(db, case, session_id, account.id)
    if session.finished_at is None:
        raise HTTPException(400, "推演尚未完成")
    if db.scalar(select(models.Review).where(models.Review.session_id == session.id, models.Review.attempt_no == session.attempt_no)):
        return schemas.OkResponse()
    task = db.scalar(select(models.AiTask).where(models.AiTask.session_id == session.id, models.AiTask.kind == "student_review").order_by(models.AiTask.id.desc()))
    if task and task.status == "failed":
        task.status, task.stage, task.error = "queued", "queued", None
        task.max_attempts = task.attempts + 3
        db.commit()
        schedule_task(task.task_id)
    return schemas.OkResponse()


# ─────────────────── 试跑：进程内存储（刻意不落库） ───────────────────

#: 进程内试跑存储：try_id -> (响应数据, 创建时间)。**刻意不落任何库表**。
#: ⚠️ 多 worker 部署约束：本存储不跨进程共享，必须保持 gunicorn `--workers 1`
#: （见 deploy/systemd/case-sim.service 第 4 条）。多 worker 时会出现「在 A 生成、
#: 切到 B 打开 → 试跑内容已失效」。若确需多 worker，先把本存储外置到 DB/Redis。
TRY_STORE: "OrderedDict[str, Tuple[Dict[str, Any], datetime]]" = OrderedDict()
#: 容量上限，超出时按创建时间淘汰最旧一条
TRY_STORE_LIMIT = 50
_TRY_LOCK = threading.Lock()
#: try_id 找不到（含被淘汰、进程重启）时的统一提示
TRY_EXPIRED_MESSAGE = "试跑内容已失效，请重新生成"
#: 与 T2 同口径：短于 30 字按主题关键词处理
TRY_TOPIC_MAX_CHARS = 30
#: 试跑生成的重试次数
TRY_MAX_REGENERATE = 2


def _try_store_put(try_id: str, data: Dict[str, Any]) -> None:
    with _TRY_LOCK:
        TRY_STORE[try_id] = (data, datetime.now())
        while len(TRY_STORE) > TRY_STORE_LIMIT:
            oldest_key = min(TRY_STORE.items(), key=lambda kv: kv[1][1])[0]
            TRY_STORE.pop(oldest_key, None)


def _try_store_get(try_id: str) -> Optional[Dict[str, Any]]:
    with _TRY_LOCK:
        item = TRY_STORE.get(try_id)
        return item[0] if item is not None else None


def _try_node_view(node_data: Dict[str, Any]) -> schemas.TryNode:
    """把生成结果转成试跑节点视图；试跑不落库，`id` 直接用 `idx`。"""
    idx = int(node_data.get("idx") or 0)
    return schemas.TryNode(
        id=idx,
        idx=idx,
        node_role=node_data.get("node_role"),
        title=node_data.get("title") or "",
        background=node_data.get("background") or "",
        options=[
            schemas.TryOption(
                key=option.get("key"),
                label=option.get("label") or "",
                risk_level=option.get("risk_level"),
                summary=option.get("summary") or "",
                metrics=option.get("metrics") or {},
            )
            for option in node_data.get("options") or []
        ],
    )


# ─────────────────── S5 学生自助试跑（唯一允许下发答案的接口） ───────────────────

@router.post("/{student_token}/try-case", response_model=schemas.TryCaseResponse)
async def try_case(
    student_token: str,
    payload: schemas.TryCaseRequest,
    db: OrmSession = Depends(get_db),
    account=Depends(current_student),
) -> schemas.TryCaseResponse:
    """学生自己贴素材/关键词生成试跑案例。

    结果只放进程内内存，**不写 case / node / option_result / session / turn / review 任何一张表**，
    也不生成 student_token 与二维码。进程重启后 try_id 全部失效，属预期行为。
    """
    _get_published_case(db, student_token)  # 鉴权沿用 student_token，不新增权限体系

    source_text = (payload.text or "").strip()
    if not source_text:
        raise HTTPException(status_code=400, detail="请提供案例素材或主题关键词")
    source_kind = (
        schemas.SourceKind.topic.value
        if len(source_text) < TRY_TOPIC_MAX_CHARS
        else schemas.SourceKind.material.value
    )

    case_type = payload.case_type.value
    generated, errors = await _generate_try_case(source_text, source_kind, case_type)
    if generated is None:
        raise HTTPException(
            status_code=503,
            detail="试跑生成失败，请稍后重试：" + "；".join(errors[:3]),
        )

    try_id = uuid.uuid4().hex
    data: Dict[str, Any] = {
        "try_id": try_id,
        "case_type": case_type,
        "background": generated.get("background") or "",
        "base_metrics": generated.get("base_metrics"),
        "nodes": [
            _try_node_view(node).model_dump() for node in generated.get("nodes") or []
        ],
    }
    _try_store_put(try_id, {**data, "student_id": account.id, "student_token": student_token})
    return schemas.TryCaseResponse(**data)


# ─────────────────── S6 回读试跑结果 ───────────────────

@router.get("/{student_token}/try/{try_id}", response_model=schemas.TryGetResponse)
def get_try_case(
    student_token: str,
    try_id: str,
    db: OrmSession = Depends(get_db),
    account=Depends(current_student),
) -> schemas.TryGetResponse:
    """从内存回读一次试跑结果；不存在（含被淘汰、进程重启）返回 404。"""
    _get_published_case(db, student_token)
    data = _try_store_get(try_id)
    if data is None or data.get("student_id") != account.id or data.get("student_token") != student_token:
        raise HTTPException(status_code=404, detail=TRY_EXPIRED_MESSAGE)
    return schemas.TryGetResponse(**data)
