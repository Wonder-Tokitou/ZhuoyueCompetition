"""教师端 9 条接口（T1–T9）。

鉴权：全部教师端接口只认全局令牌（T1 返回的固定值），不按每案随机 teacher_token 鉴权。
T1–T9 均为真实实现；生成链路复用 services/{llm,generator,validator}.py。
"""
import os
import secrets
import uuid
import math
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from sqlalchemy import delete, select
from sqlalchemy.orm import Session as OrmSession

from backend.app import models
from backend.app.domain import rules
from backend.app.domain.student_choices import choice_receipt
from backend.app import schemas
from backend.app.db import get_db
from backend.app.domain import validator
from contracts.errors import LLMFailed
from backend.app.integrations.ai.task_runner import schedule_task
from backend.app.integrations.ai.artifacts import artifact_store
from backend.app.services.extractors import extract_document
from backend.app.services.extractors import extract_pdf_text
from backend.app.observability import event

from backend.app.integrations.ai.workflow import persist_generated_case
from backend.app.integrations.ai.teacher_advice import suggest_fix

router = APIRouter(prefix="/api", tags=["教师端"])
logger = logging.getLogger(__name__)

# T1 固定返回的教师令牌（SPEC.md T1）。任务状态不再保存在进程内。
#: 进程内缓存（快路径 + 测试兼容）。**它不是权威来源**：多 worker 部署或进程
#: 重启后必然缺失，鉴权会回查 teacher_session 表，因此令牌不会随进程内存丢失。
TEACHER_TOKENS: set[str] = set()

#: 教师令牌有效期（小时）。与前端一次 sessionStorage 会话的周期相当。
TEACHER_TOKEN_TTL_HOURS = 12


def is_valid_teacher_token(db: OrmSession, token: str) -> bool:
    """教师令牌校验：先查进程内缓存，未命中再回查 teacher_session 表。

    多 worker 时登录落在 worker A、后续请求轮询到 worker B —— B 的缓存里没有
    该令牌，但数据库里有，因此仍然放行。这正是「登录后被踢出」的根治点。
    """
    if not token:
        return False
    if token in TEACHER_TOKENS:
        return True
    row = db.get(models.TeacherSession, token)
    if row is None:
        return False
    return row.expires_at > datetime.now()


def issue_teacher_token(db: OrmSession) -> str:
    """签发并持久化教师令牌；顺手清理过期记录，避免表单调增长。"""
    now = datetime.now()
    db.execute(delete(models.TeacherSession).where(models.TeacherSession.expires_at <= now))
    token = secrets.token_urlsafe(32)
    db.add(models.TeacherSession(token=token, created_at=now,
                                 expires_at=now + timedelta(hours=TEACHER_TOKEN_TTL_HOURS)))
    db.commit()
    TEACHER_TOKENS.add(token)
    return token

# 后端生成的资源目录，由 main.py 挂到 /media
STATIC_DIR = Path(os.getenv("CASE_SIM_STATIC_DIR") or Path(__file__).resolve().parents[1] / "static")
QRCODE_DIR = STATIC_DIR / "qrcode"

#: 素材与主题关键词的分界：短于 30 字按主题关键词处理
TOPIC_MAX_CHARS = 30
#: 可推演素材的字数下限
MATERIAL_MIN_CHARS = 200
#: 生成失败时的统一提示
GENERATION_WARNING = "AI 生成失败，可重试"
#: 校验不通过时允许的重新生成次数（增加到 4 次，给模型更多自我修正机会）
MAX_REGENERATE = 4


# ─────────────────────────── 通用小工具 ───────────────────────────

def _get_case_or_fail(db: OrmSession, case_id: int, token: str) -> models.Case:
    """教师端鉴权只认全局令牌（T1 返回的固定值）。"""
    if not is_valid_teacher_token(db, token):
        raise HTTPException(status_code=403, detail="令牌无效")
    case = db.get(models.Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="案例不存在")
    return case


def _node_or_fail(db: OrmSession, case: models.Case, node_id: int) -> models.Node:
    node = db.get(models.Node, node_id)
    if node is None or node.case_id != case.id:
        raise HTTPException(status_code=404, detail="节点不存在")
    return node


def _sorted_options(node: models.Node) -> List[models.OptionResult]:
    return sorted(node.option_results, key=lambda o: o.option_key)


def _teacher_node(node: models.Node) -> schemas.TeacherNode:
    """教师端节点：选项带完整后果（risk_level / metrics / summary），供校准。"""
    return schemas.TeacherNode(
        id=node.id,
        idx=node.idx,
        scenario=node.scenario,
        node_role=node.node_role,
        title=node.title,
        background=node.background,
        options=[
            schemas.TeacherOption(
                key=o.option_key,
                label=o.label,
                risk_level=o.risk_level,
                metrics=o.metrics_json,
                net_profit=o.net_profit,
                financial_assumptions=o.financial_basis_json.get("assumptions") if o.financial_basis_json else None,
                financial_basis=o.financial_basis_json,
                calculated_net_profit=(float(o.metrics_json.get("revenue", 0))-
                    float((o.financial_basis_json or {}).get("assumptions", {}).get("operating_cost", 0))-
                    float((o.financial_basis_json or {}).get("assumptions", {}).get("operating_expense", 0))),
                summary=o.summary,
            )
            for o in _sorted_options(node)
        ],
    )


def _case_detail(case: models.Case) -> schemas.CaseDetail:
    """T4 的 case 对象：case 表全部 15 列。"""
    return schemas.CaseDetail(
        id=case.id,
        title=case.title,
        source_text=case.source_text,
        status=case.status,
        framework_id=case.framework_id,
        teacher_token=case.teacher_token,
        student_token=case.student_token,
        created_at=case.created_at,
        published_at=case.published_at,
        case_type=case.case_type,
        source_kind=case.source_kind,
        base_metrics=case.base_metrics_json,
        base_net_profit=case.base_net_profit,
        financial_assumptions=case.financial_assumptions_json,
        owner_type=case.owner_type,
        background=case.background,
        dilemma=case.dilemma,
        version=case.version or 1,
    )


# ─────────────────────── T2 的素材抽取与生成链路 ───────────────────────

def _extract_pdf_text(raw: bytes) -> str:
    """用 pypdf 抽 PDF 文字层；扫描版 PDF 抽不出文字会返回空串。"""
    return extract_pdf_text(raw)


async def _resolve_source(
    text: Optional[str], file: Optional[UploadFile]
) -> Tuple[str, str]:
    """把请求里的素材归一成 (source_kind, source_text)。"""
    if file is not None:
        raw = await file.read()
        suffix = Path(file.filename or "").suffix.lower()
        try:
            content = extract_document(raw, suffix)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        content = content.strip()
        if len(content) < MATERIAL_MIN_CHARS:
            raise HTTPException(
                status_code=400,
                detail="请提供可复制的文字版素材，或改用主题关键词模式",
            )
        return schemas.SourceKind.material.value, content

    content = (text or "").strip()
    if not content:
        raise HTTPException(
            status_code=400,
            detail="请提供可复制的文字版素材，或改用主题关键词模式",
        )
    # 短输入视作主题关键词：不要求成篇素材，基准数据由模型按行业区间补
    if len(content) < TOPIC_MAX_CHARS:
        return schemas.SourceKind.topic.value, content
    if len(content) < MATERIAL_MIN_CHARS:
        raise HTTPException(
            status_code=400,
            detail="请提供可复制的文字版素材，或改用主题关键词模式",
        )
    return schemas.SourceKind.material.value, content


def _resolve_case_type(raw: Optional[str]) -> str:
    """定案例类型：必填；缺失或取值非法一律 400。"""
    if not raw:
        raise HTTPException(status_code=400, detail="请指定案例类型")
    try:
        return schemas.CaseType(raw).value
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="案例类型不合法，可选：%s" % "、".join(m.value for m in schemas.CaseType),
        )


def _resolve_framework(db: OrmSession, framework_id: Optional[int]) -> Optional[models.Framework]:
    """取框架行；未指定则返回 None，由 generator 用内置兜底模板。"""
    if framework_id is None:
        return None
    framework = db.get(models.Framework, framework_id)
    if framework is None:
        raise HTTPException(status_code=404, detail="框架不存在")
    return framework


def _review_placeholder(case_type: str) -> List[Dict[str, str]]:
    """复盘占位：提供基础分析项提示，生成后的复盘允许动态增补。"""
    framework = rules.CASE_TYPE_TO_FRAMEWORK.get(case_type, "")
    return [{"name": name, "content": ""} for name in rules.FRAMEWORK_DIMENSIONS.get(framework, [])]


# ─────────────────────────── T1 登录 ───────────────────────────

@router.post("/teacher/login", response_model=schemas.LoginResponse)
def teacher_login(payload: schemas.LoginRequest,
                  db: OrmSession = Depends(get_db)) -> schemas.LoginResponse:
    """比对 TEACHER_PASSWORD，成功签发**持久化**令牌（多 worker 下同样有效）。"""
    expected = os.getenv("TEACHER_PASSWORD")
    if not expected:
        raise HTTPException(status_code=503, detail="未配置 TEACHER_PASSWORD")
    if payload.password != expected:
        raise HTTPException(status_code=401, detail="口令错误")
    return schemas.LoginResponse(teacher_token=issue_teacher_token(db))


@router.post("/teacher/logout", response_model=schemas.OkResponse)
def teacher_logout(token: str = Query(..., description="教师端令牌"),
                   db: OrmSession = Depends(get_db)) -> schemas.OkResponse:
    """登出：立即吊销令牌（数据库与进程内缓存双清）。

    令牌持久化后有效期默认 12 小时，必须有主动吊销入口，否则退出登录只是
    前端删 local 副本、服务端仍认。幂等：重复登出同样返回 200。
    """
    TEACHER_TOKENS.discard(token)
    db.execute(delete(models.TeacherSession).where(models.TeacherSession.token == token))
    db.commit()
    return schemas.OkResponse(ok=True)


# ─────────────────────────── T2 新建案例 ───────────────────────────

@router.post(
    "/cases",
    response_model=schemas.CreateCaseResponse,
    response_model_exclude_none=True,  # warning / errors 只在失败时出现
)
async def create_case(
    title: str = Form(..., description="案例标题"),
    text: Optional[str] = Form(None, description="素材文本；短于 30 字按主题关键词处理"),
    file: Optional[UploadFile] = File(None, description="素材文件 .txt/.md/.pdf，与 text 二选一"),
    framework_id: Optional[int] = Form(None, description="指定分析框架，可空"),
    case_type: Optional[str] = Form(None, description="案例类型，必填；缺失返回 400"),
    db: OrmSession = Depends(get_db),
) -> schemas.CreateCaseResponse:
    """素材 / 主题关键词 -> 调模型 -> 校验 -> 落库。

    模型或校验失败一律返回 200 + warning + errors，绝不返回 500。
    """
    filename = file.filename if file is not None else None
    mime = file.content_type if file is not None else None
    raw_upload = None
    if file is not None:
        raw_upload = await file.read()
        await file.seek(0)
        event("教师端：文件已接收，开始解析", filename=Path(filename or "").name,
              bytes=len(raw_upload), mime=mime)
    source_kind, source_text = await _resolve_source(text, file)
    event("教师端：素材解析成功", source_kind=source_kind, characters=len(source_text))
    resolved_type = _resolve_case_type(case_type)
    framework = _resolve_framework(db, framework_id)

    case = models.Case(
        title=title,
        source_text=source_text,
        status=schemas.CaseStatus.generating.value,
        framework_id=framework.id if framework is not None else None,
        teacher_token=secrets.token_hex(16),
        student_token=secrets.token_hex(16),
        case_type=resolved_type,
        source_kind=source_kind,
        owner_type=schemas.OwnerType.teacher.value,
        # 两段题面文案要等模型产出，先写空串占位（列约束为非空）
        background="",
        dilemma="",
        input_filename=filename,
        input_mime=mime,
        input_size=len(source_text.encode("utf-8")),
    )
    db.add(case)
    db.commit()
    db.refresh(case)

    task_id = uuid.uuid4().hex
    task = models.AiTask(
        task_id=task_id,
        case_id=case.id,
        kind="case_generation",
        status="queued",
        stage="queued",
        input_json={
            "source_text": source_text,
            "source_kind": source_kind,
            "case_type": resolved_type,
            "framework_id": framework.id if framework is not None else None,
            "previous_status": "draft",
            "case_version": case.version or 1,
        },
    )
    db.add(task)
    db.flush()
    if raw_upload is not None:
        suffix = Path(filename or "").suffix.lower() or ".bin"
        artifact_store.save_bytes(
            db,
            raw_upload,
            kind="original_upload",
            task_id=task.id,
            case_id=case.id,
            suffix=suffix,
            mime=mime,
        )
    db.commit()
    # 任务先落库，再交给当前进程 worker；服务重启时 lifespan 会自动恢复。
    event("教师端：上传及入库成功，AI任务已入队", case_id=case.id, task_id=task_id,
          original_saved=raw_upload is not None)
    schedule_task(task_id)
    return schemas.CreateCaseResponse(
        case_id=case.id,
        status=schemas.CaseStatus.generating,
        task_id=task_id,
    )

@router.post("/cases/{case_id}/ai-tasks", response_model=schemas.AiTaskResponse)
async def submit_ai_task(case_id: int, payload: schemas.AiTaskSubmitRequest, token: str = Query(...), db: OrmSession = Depends(get_db)):
    case = _get_case_or_fail(db, case_id, token)
    previous_status = case.status
    if payload.idempotency_key:
        existing = db.scalar(select(models.AiTask).where(
            models.AiTask.case_id == case.id,
            models.AiTask.kind == "case_generation",
            models.AiTask.idempotency_key == payload.idempotency_key,
        ))
        if existing is not None:
            return _task_response(existing)
    task_id = uuid.uuid4().hex
    task = models.AiTask(
        task_id=task_id,
        case_id=case.id,
        kind="case_generation",
        status="queued",
        stage="queued",
        idempotency_key=payload.idempotency_key,
        input_json={
            "source_text": case.source_text,
            "source_kind": case.source_kind,
            "case_type": case.case_type,
            "framework_id": case.framework_id,
            "previous_status": previous_status,
            "case_version": case.version or 1,
        },
    )
    db.add(task)
    case.status = schemas.CaseStatus.generating.value
    db.commit()
    schedule_task(task_id)
    return _task_response(task)


def _task_response(task: models.AiTask) -> schemas.AiTaskResponse:
    return schemas.AiTaskResponse(
        task_id=task.task_id,
        case_id=task.case_id or 0,
        kind=task.kind,
        status=task.status,
        stage=task.stage,
        attempts=task.attempts,
        idempotency_key=task.idempotency_key,
        error=task.error,
        output_url="/api/ai-tasks/%s/output" % task.task_id,
    )

@router.get("/cases/{case_id}/ai-tasks", response_model=List[schemas.AiTaskResponse])
def list_case_ai_tasks(case_id: int, token: str = Query(...), db: OrmSession = Depends(get_db)):
    if not is_valid_teacher_token(db, token):
        raise HTTPException(status_code=403, detail="令牌无效")
    _get_case_or_fail(db, case_id, token)
    tasks = db.scalars(select(models.AiTask).where(
        models.AiTask.case_id == case_id, models.AiTask.kind.in_(('case_generation', 'case_nodes'))
    ).order_by(models.AiTask.id.desc()).limit(20)).all()
    return [_task_response(task) for task in tasks]


@router.get("/ai-tasks/{task_id}", response_model=schemas.AiTaskResponse)
def get_ai_task(task_id: str, token: str = Query(...), db: OrmSession = Depends(get_db)):
    if not is_valid_teacher_token(db, token): raise HTTPException(status_code=403, detail="令牌无效")
    task = db.scalar(select(models.AiTask).where(models.AiTask.task_id == task_id))
    if task is None: raise HTTPException(status_code=404, detail="AI任务不存在")
    _get_case_or_fail(db, task.case_id, token)
    return _task_response(task)


@router.get("/ai-tasks/{task_id}/steps", response_model=List[schemas.AiTaskStepResponse])
def get_ai_task_steps(task_id: str, token: str = Query(...), db: OrmSession = Depends(get_db)):
    if not is_valid_teacher_token(db, token):
        raise HTTPException(status_code=403, detail="令牌无效")
    task = db.scalar(select(models.AiTask).where(models.AiTask.task_id == task_id))
    if task is None:
        raise HTTPException(status_code=404, detail="AI任务不存在")
    _get_case_or_fail(db, task.case_id, token)
    return [schemas.AiTaskStepResponse(
        stage=s.stage,
        status=s.status,
        attempt=s.attempt,
        detail=s.detail_json,
        error=s.error,
        started_at=s.started_at,
        finished_at=s.finished_at,
    ) for s in task.steps]

@router.get("/ai-tasks/{task_id}/output")
def get_ai_task_output(task_id: str, token: str = Query(...), db: OrmSession = Depends(get_db)):
    if not is_valid_teacher_token(db, token): raise HTTPException(status_code=403, detail="令牌无效")
    task = db.scalar(select(models.AiTask).where(models.AiTask.task_id == task_id))
    if task is None: raise HTTPException(status_code=404, detail="AI任务不存在")
    _get_case_or_fail(db, task.case_id, token)
    if task.status != "succeeded": raise HTTPException(status_code=409, detail="AI任务尚未成功完成")
    return task.output_json


@router.get("/ai-tasks/{task_id}/artifacts", response_model=List[schemas.AiArtifactResponse])
def get_ai_task_artifacts(task_id: str, token: str = Query(...), db: OrmSession = Depends(get_db)):
    if not is_valid_teacher_token(db, token):
        raise HTTPException(status_code=403, detail="令牌无效")
    task = db.scalar(select(models.AiTask).where(models.AiTask.task_id == task_id))
    if task is None:
        raise HTTPException(status_code=404, detail="AI任务不存在")
    _get_case_or_fail(db, task.case_id, token)
    return [schemas.AiArtifactResponse(
        id=item.id,
        kind=item.kind,
        path=item.path,
        mime=item.mime,
        size=item.size,
        sha256=item.sha256,
        created_at=item.created_at,
    ) for item in task.artifacts]

@router.post("/ai-tasks/{task_id}/complete", response_model=schemas.AiTaskResponse)
def complete_ai_task(task_id: str, output: Dict[str, Any], token: str = Query(...), db: OrmSession = Depends(get_db)):
    if not is_valid_teacher_token(db, token): raise HTTPException(status_code=403, detail="令牌无效")
    task = db.scalar(select(models.AiTask).where(models.AiTask.task_id == task_id))
    if task is None: raise HTTPException(status_code=404, detail="AI任务不存在")
    case = _get_case_or_fail(db, task.case_id, token)
    if task.kind != "case_generation":
        raise HTTPException(status_code=400, detail="此接口只接受案例生成任务")
    if task.status == "succeeded":
        return _task_response(task)
    ok, errors = validator.validate_case(output)
    if not ok:
        task.status = "failed"; task.error = "；".join(errors)
        db.commit()
        return _task_response(task)
    from contracts.errors import ValidationExhausted
    try:
        persist_generated_case(db, case, output, expected_version=int(
            (task.input_json or {}).get("case_version") or case.version or 1))
    except ValidationExhausted as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    case.status = schemas.CaseStatus.ready.value
    task.status = "succeeded"; task.output_json = output; task.error = None; task.stage = "completed"; task.finished_at = datetime.now()
    db.commit()
    return _task_response(task)


# ─────────────────────────── T3 案例列表 ───────────────────────────

@router.get("/cases", response_model=List[schemas.CaseSummary])
def list_cases(
    token: str = Query(..., description="教师端令牌"),
    db: OrmSession = Depends(get_db),
) -> List[schemas.CaseSummary]:
    """案例列表（裸数组）。只列教师正式案例，学生自助试算不进列表。"""
    if not is_valid_teacher_token(db, token):
        raise HTTPException(status_code=403, detail="令牌无效")
    rows = db.scalars(
        select(models.Case)
        .where(models.Case.owner_type == schemas.OwnerType.teacher.value)
        .order_by(models.Case.id.desc())
    ).all()
    return [
        schemas.CaseSummary(id=c.id, title=c.title, status=c.status, created_at=c.created_at)
        for c in rows
    ]


# ─────────────────────────── T4 案例详情 ───────────────────────────

@router.get("/cases/{case_id}", response_model=schemas.CaseDetailResponse)
def get_case(
    case_id: int,
    token: str = Query(..., description="教师端令牌"),
    db: OrmSession = Depends(get_db),
) -> schemas.CaseDetailResponse:
    """案例详情 + 全部节点；教师端选项带完整后果，供校准使用。"""
    case = _get_case_or_fail(db, case_id, token)
    return schemas.CaseDetailResponse(
        case=_case_detail(case),
        nodes=[_teacher_node(n) for n in case.nodes],
    )


@router.delete("/cases/{case_id}", response_model=schemas.OkResponse)
def delete_case(case_id: int, token: str = Query(...), db: OrmSession = Depends(get_db)):
    case = _get_case_or_fail(db, case_id, token)
    pending = db.scalars(select(models.AiTask).where(models.AiTask.case_id == case.id,
        models.AiTask.status.in_(("queued", "running")))).all()
    if pending:
        raise HTTPException(status_code=409, detail="案例生成或复盘任务仍在运行，请等待任务结束后删除。")
    task_ids = select(models.AiTask.id).where(models.AiTask.case_id == case.id)
    artifacts = db.scalars(select(models.AiArtifact).where(
        (models.AiArtifact.case_id == case.id) | models.AiArtifact.task_id.in_(task_ids)
    )).all()
    artifact_paths = [row.path for row in artifacts]
    for artifact in artifacts:
        db.delete(artifact)
    tasks = db.scalars(select(models.AiTask).where(models.AiTask.case_id == case.id)).all()
    for task in tasks:
        db.delete(task)
    db.flush()
    simulation_config = db.get(models.SimulationConfig, case.id)
    if simulation_config:
        db.delete(simulation_config)
        db.flush()
    db.delete(case)
    db.commit()
    remaining_paths = set(db.scalars(select(models.AiArtifact.path)).all())
    for path in set(artifact_paths) - remaining_paths:
        try:
            artifact_store.delete(path)
        except FileNotFoundError:
            pass
        except OSError:
            # The DB deletion is already committed; report cleanup failures
            # without turning a successful delete into a misleading HTTP 500.
            logger.exception("案例制品文件清理失败 case_id=%s path=%s", case_id, path)
    event("教师端：案例及关联记录已删除", case_id=case_id, artifacts=len(artifacts))
    return schemas.OkResponse(ok=True)


@router.post("/cases/{case_id}/delete", response_model=schemas.OkResponse)
def delete_case_post(case_id: int, token: str = Query(...), db: OrmSession = Depends(get_db)):
    return delete_case(case_id, token, db)


@router.post("/cases/{case_id}/generate-nodes", response_model=schemas.AiTaskResponse)
async def generate_nodes(case_id: int, payload: schemas.GenerateNodesRequest,
                         token: str = Query(...), db: OrmSession = Depends(get_db)):
    case = _get_case_or_fail(db, case_id, token)
    if case.status not in ("ready", "draft"):
        raise HTTPException(409, "案例正在生成中，暂不能启动决策节点生成。")
    if case.nodes:
        raise HTTPException(409, "决策节点已存在；已保存的教师校准内容不会再次交给 AI 覆盖。")
    assumptions = payload.financial_assumptions
    if assumptions is None or not all(k in assumptions and isinstance(assumptions[k], (int, float))
                                      for k in ("operating_cost", "operating_expense")):
        raise HTTPException(400, "净利润计算需要提供营业成本和运营费用假设。")
    from backend.app.domain.financials import net_profit
    computed_net_profit = net_profit(payload.base_metrics.revenue,
        float(assumptions["operating_cost"]), float(assumptions["operating_expense"]))
    if payload.net_profit is not None and abs(payload.net_profit - computed_net_profit) > 0.01:
        raise HTTPException(400, "净利润由系统按公式计算，请检查基准指标与成本假设。")
    key = payload.idempotency_key or "nodes-v%d" % ((case.version or 1) + 1)
    existing = db.scalar(select(models.AiTask).where(models.AiTask.case_id == case.id,
        models.AiTask.kind == "case_nodes", models.AiTask.idempotency_key == key))
    if existing is not None:
        return _task_response(existing)
    case.base_metrics_json = payload.base_metrics.model_dump()
    case.financial_assumptions_json = assumptions
    case.base_net_profit = computed_net_profit
    case.version = (case.version or 1) + 1
    task_id = uuid.uuid4().hex
    task = models.AiTask(task_id=task_id, case_id=case.id, kind="case_nodes", status="queued",
        stage="queued", idempotency_key=key,
        input_json={"case_version": case.version, "baseline_payload": {"base_metrics": case.base_metrics_json,
            "financial_assumptions": assumptions}, "previous_status": "ready"})
    db.add(task)
    case.status = "generating"
    db.commit()
    event("教师端：基准指标已确认，决策节点生成任务已入队", case_id=case.id, task_id=task_id,
        version=case.version, net_profit=case.base_net_profit)
    schedule_task(task_id)
    return _task_response(task)


# ─────────────────────────── T5 改案例级信息 ───────────────────────────

@router.patch("/cases/{case_id}", response_model=schemas.OkResponse)
def update_case(
    case_id: int,
    payload: schemas.UpdateCaseRequest,
    token: str = Query(..., description="教师端令牌"),
    db: OrmSession = Depends(get_db),
) -> schemas.OkResponse:
    """保存教师人工校准；已有节点只执行确定性数值重算，不调用 AI。"""
    case = _get_case_or_fail(db, case_id, token)
    if case.status == "generating":
        raise HTTPException(409, "案例后台任务仍在运行，请等待完成后再保存校准。")
    baseline_changed = payload.base_metrics is not None or payload.financial_assumptions is not None
    old_metrics = dict(case.base_metrics_json or {})
    old_assumptions = dict(case.financial_assumptions_json or {})
    new_metrics = payload.base_metrics.model_dump() if payload.base_metrics is not None else old_metrics
    new_assumptions = payload.financial_assumptions if payload.financial_assumptions is not None else old_assumptions
    if payload.financial_assumptions is not None:
        for key in ("operating_cost", "operating_expense"):
            value = payload.financial_assumptions.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise HTTPException(400, "营业成本与运营费用必须是有限的非负数值。")
    if payload.base_metrics is not None and case.nodes:
        if not all(key in new_assumptions for key in ("operating_cost", "operating_expense")):
            raise HTTPException(409, "请先补齐基准营业成本和运营费用，再重算已有节点。")
    recalculated = 0
    simulation_config = db.get(models.SimulationConfig, case.id)
    progressive_enabled = bool(simulation_config and simulation_config.draft_json.get("enabled"))
    if baseline_changed and case.nodes and not progressive_enabled:
        try:
            from backend.app.services.case_recalibration import recalculate_option_results
            recalculated = recalculate_option_results(
                case.nodes, old_metrics, new_metrics, old_assumptions, new_assumptions,
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise HTTPException(422, f"确定性重算未执行，原节点保持不变：{exc}") from exc
    if payload.title is not None:
        case.title = payload.title
    if payload.background is not None:
        case.background = payload.background
    if payload.dilemma is not None:
        case.dilemma = payload.dilemma
    if baseline_changed:
        case.base_metrics_json = new_metrics
        case.financial_assumptions_json = new_assumptions or None
        if all(key in new_assumptions for key in ("operating_cost", "operating_expense")):
            from backend.app.domain.financials import net_profit
            case.base_net_profit = net_profit(float(new_metrics["revenue"]),
                float(new_assumptions["operating_cost"]), float(new_assumptions["operating_expense"]))
        else:
            case.base_net_profit = None
    if payload.financial_assumptions is not None:
        if not all(k in payload.financial_assumptions for k in ("operating_cost", "operating_expense")):
            raise HTTPException(400, "财务假设需包含营业成本与运营费用")
    case.version = (case.version or 1) + 1
    db.commit()
    event("教师端：人工校准已保存", case_id=case.id, version=case.version,
          fields=list(payload.model_fields_set), recalculated_options=recalculated,
          ai_called=False)
    return schemas.OkResponse(ok=True)


# ─────────────────────────── T6 改节点 ───────────────────────────

@router.patch("/cases/{case_id}/nodes/{node_id}", response_model=schemas.OkResponse)
def update_node(
    case_id: int,
    node_id: int,
    payload: schemas.NodePatchRequest,
    token: str = Query(..., description="教师端令牌"),
    db: OrmSession = Depends(get_db),
) -> schemas.OkResponse:
    """覆盖节点的标题 / 背景，或整组替换该节点的选项。

    已发布案例同样允许编辑，学生端下次进入即看到新版本。
    """
    case = _get_case_or_fail(db, case_id, token)
    node = _node_or_fail(db, case, node_id)

    if payload.title is not None:
        node.title = payload.title

    # scenario 与 background 同源：传任意一个都同时写两列
    new_background = payload.background if payload.background is not None else payload.scenario
    if new_background is not None:
        node.background = new_background
        node.scenario = new_background

    if payload.options is not None:
        from backend.app.domain.financials import net_profit
        previous_by_key = {old.option_key: old for old in node.option_results}
        # 先删后插，避免与 (node_id, option_key) / (node_id, risk_level) 唯一键冲突
        for old in list(node.option_results):
            db.delete(old)
        db.flush()
        for option in payload.options:
            assumptions = option.financial_assumptions or {}
            if not all(key in assumptions and isinstance(assumptions[key], (int, float))
                       and not isinstance(assumptions[key], bool)
                       and math.isfinite(assumptions[key]) and assumptions[key] >= 0
                       for key in ("operating_cost", "operating_expense")):
                raise HTTPException(400, f"选项 {option.key.value} 请填写有效的营业成本与运营费用教学假设。")
            computed_profit = net_profit(option.metrics.revenue,
                float(assumptions["operating_cost"]), float(assumptions["operating_expense"]))
            basis = dict(option.financial_basis or {})
            basis["source"] = "教师人工校准"
            basis["assumptions"] = dict(assumptions)
            indicators = dict(basis.get("indicators") or {})
            previous = previous_by_key.get(option.key.value)
            previous_metrics = dict(previous.metrics_json or {}) if previous else {}
            next_metrics = option.metrics.model_dump()
            metric_labels = {"revenue": "营收", "gross_margin": "毛利率",
                             "market_share": "市场份额", "cash_flow": "现金流"}
            for metric_key, label in metric_labels.items():
                current_indicator = indicators.get(metric_key)
                if not isinstance(current_indicator, dict) or previous_metrics.get(metric_key) != next_metrics.get(metric_key):
                    indicators[metric_key] = {"label": label,
                        "before": previous_metrics.get(metric_key), "after": next_metrics.get(metric_key),
                        "method": "教师人工校准，保存值直接采用，不交由 AI 覆写"}
                else:
                    indicators[metric_key] = {**current_indicator, "label": label,
                                              "after": next_metrics.get(metric_key)}
            indicators["net_profit"] = {"label": "净利润", "after": computed_profit,
                "formula": "营收－营业成本－运营费用",
                "inputs": {"revenue": option.metrics.revenue, **assumptions},
                "method": "教师确认教学假设后由确定性脚本计算"}
            basis["indicators"] = indicators
            db.add(
                models.OptionResult(
                    node_id=node.id,
                    option_key=option.key.value,
                    label=option.label,
                    metrics_json=option.metrics.model_dump(),
                    net_profit=computed_profit,
                    financial_basis_json=basis,
                    risk_level=option.risk_level.value,
                    summary=option.summary,
                )
            )
        # 学生端读的是 node.options_json，必须同步，否则改完文案学生端还是旧的
        node.options_json = [{"key": o.key.value, "label": o.label} for o in payload.options]

    case.version = (case.version or 1) + 1
    db.commit()
    event("教师端：节点校准已保存", case_id=case.id, node_id=node.id,
          version=case.version, fields=list(payload.model_fields_set))
    return schemas.OkResponse(ok=True)


# ─────────────────────────── T7 AI 修正建议 ───────────────────────────

@router.post("/cases/{case_id}/ai-fix", response_model=schemas.AiFixResponse)
async def ai_fix(
    case_id: int,
    payload: schemas.AiFixRequest,
    token: str = Query(..., description="教师端令牌"),
    db: OrmSession = Depends(get_db),
) -> schemas.AiFixResponse:
    """把老师的问题与该节点当前内容发给模型，返回修正建议；只出建议，不落库。"""
    case = _get_case_or_fail(db, case_id, token)

    node = _node_or_fail(db, case, payload.node_id) if payload.node_id is not None else None
    try:
        data = await suggest_fix(case, node, payload.message)
    except LLMFailed as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    event("教师端：AI校准建议已返回（未自动修改案例）",
          case_id=case.id, output_characters=len(str(data.get("reply") or "")))
    return schemas.AiFixResponse(reply=str(data.get("reply") or ""))


# ─────────────────────────── T8 发布 ───────────────────────────

def _qr_code_url(request: Request, student_token: str, student_path: str) -> str:
    """生成二维码 PNG 并返回可访问路径。

    二维码内容 = {PUBLIC_BASE_URL}/student/{student_token}；
    未配置 PUBLIC_BASE_URL 时回退用 request.base_url。代码里不硬编码任何主机名。

    返回路径统一带 `/api` 前缀（见 main.py 的 `GET /api/media/{path}`）：
    线上 Nginx 若用 `root …/frontend/dist` + `try_files` 且只反代 `/api/`，
    `/media/*` 会被兜底成 index.html，导致「发布成功但二维码打不开」。
    走 `/api/` 前缀则一定被反代，不依赖 Nginx 配置。
    """
    import qrcode  # 延迟导入，避免 import 阶段多加载一个依赖

    base = (os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if not base:
        base = str(request.base_url).rstrip("/")
    target = "%s%s" % (base, student_path)

    QRCODE_DIR.mkdir(parents=True, exist_ok=True)
    qrcode.make(target).save(QRCODE_DIR / ("%s.png" % student_token))
    return "/api/media/qrcode/%s.png" % student_token


@router.post("/cases/{case_id}/publish", response_model=schemas.PublishResponse)
def publish_case(
    case_id: int,
    request: Request,
    token: str = Query(..., description="教师端令牌"),
    db: OrmSession = Depends(get_db),
) -> schemas.PublishResponse:
    """置 published，生成二维码 PNG，返回学生端链接与二维码地址。"""
    case = _get_case_or_fail(db, case_id, token)
    payload = {"title": case.title, "background": case.background, "dilemma": case.dilemma,
               "case_type": case.case_type, "base_metrics": case.base_metrics_json,
               "nodes": [{"idx": n.idx, "node_role": n.node_role, "title": n.title, "background": n.background,
                          "options": [{"key": o.option_key, "risk_level": o.risk_level,
                                       "label": o.label, "summary": o.summary, "metrics": o.metrics_json}
                                      for o in _sorted_options(n)]} for n in sorted(case.nodes, key=lambda x: x.idx)],
               "review": _review_placeholder(case.case_type)}
    simulation_config = db.get(models.SimulationConfig, case.id)
    simulation_snapshot = None
    if simulation_config and simulation_config.draft_json.get("enabled"):
        from backend.app.services.simulation import prepare
        try:
            simulation_snapshot, _ = prepare(case, simulation_config.draft_json)
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(422, "递进规则未通过发布校验：" + str(exc)) from exc
        # Absolute legacy outcomes are not progressive outcomes. Keep structure,
        # risk and numeric validation; path arithmetic was checked above.
        errors = validator._v1_structure(payload) + validator._v2_risk_gradient(payload)
        valid = not errors
    else:
        valid, errors = validator.validate_case(payload, check_industry_range=False)
    if not valid:
        raise HTTPException(status_code=400, detail="案例未通过发布校验：" + "；".join(errors))

    student_path = "/student/%s" % case.student_token
    # Generate all external artifacts before committing the published state.
    # If QR generation/dependencies fail, the case remains publishable instead
    # of being persisted as published while this request returns HTTP 500.
    qr_code_url = _qr_code_url(request, case.student_token, student_path)

    case.status = schemas.CaseStatus.published.value
    if simulation_config:
        from backend.app.services.simulation import live_snapshot
        case.version = (case.version or 1) + 1
        simulation_config.published_snapshot_json = simulation_snapshot or live_snapshot(case)
        simulation_config.published_snapshot_json = {**simulation_config.published_snapshot_json, "version": case.version}
    case.published_at = case.published_at or datetime.now()
    db.commit()
    db.refresh(case)

    event("教师端：案例发布成功", case_id=case.id, version=case.version)
    return schemas.PublishResponse(
        student_url=student_path,
        qr_code_url=qr_code_url,
    )


# ─────────────────────────── T9 推演记录 ───────────────────────────

from backend.app.domain.progressive import Configuration as SimulationConfiguration


@router.get("/cases/{case_id}/simulation")
def get_simulation(case_id: int, token: str = Query(...), db: OrmSession = Depends(get_db)):
    _get_case_or_fail(db, case_id, token)
    row = db.get(models.SimulationConfig, case_id)
    return {"draft": row.draft_json if row else None,
            "published": bool(row and row.published_snapshot_json and row.published_snapshot_json.get("simulation", {}).get("enabled"))}


@router.post("/cases/{case_id}/simulation/preview")
def preview_simulation(case_id: int, payload: SimulationConfiguration,
                       token: str = Query(...), db: OrmSession = Depends(get_db)):
    case = _get_case_or_fail(db, case_id, token)
    from backend.app.services.simulation import prepare
    try:
        _, result = prepare(case, payload.model_dump())
        return result
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, "递进规则校验失败：" + str(exc)) from exc


@router.post("/cases/{case_id}/simulation/suggest")
async def suggest_simulation(case_id: int, retry: bool = Query(False),
                             token: str = Query(...), db: OrmSession = Depends(get_db)):
    case = _get_case_or_fail(db, case_id, token)
    from backend.app.integrations.ai.impact_rules import ensure_task
    try:
        task = ensure_task(db, case, retry)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if task is None:
        return {"protected": True, "task": None}
    if task.status in ("queued", "running"):
        schedule_task(task.task_id)
    return {"protected": False, "task": _task_response(task)}


@router.put("/cases/{case_id}/simulation")
def save_simulation(case_id: int, payload: SimulationConfiguration,
                    token: str = Query(...), db: OrmSession = Depends(get_db)):
    case = _get_case_or_fail(db, case_id, token)
    if case.status == "generating":
        raise HTTPException(409, "请等待节点生成完成后保存递进规则")
    if payload.enabled:
        preview_simulation(case_id, payload, token, db)
    row = db.get(models.SimulationConfig, case_id)
    if row is None:
        from backend.app.services.simulation import live_snapshot
        row = models.SimulationConfig(case_id=case_id,
            published_snapshot_json=live_snapshot(case) if case.status == "published" else None)
        db.add(row)
    row.draft_json = payload.model_dump()
    summaries = {}
    if payload.enabled:
        from backend.app.services.rule_summary import sync_rule_summaries
        summaries = sync_rule_summaries(case, row.draft_json)
    db.commit()
    event("教师端：递进规则已保存，待发布", case_id=case_id, ai_called=False)
    return {"ok": True, "summaries": summaries}

@router.put("/cases/{case_id}/simulation/reference")
def save_reference_path(case_id: int, payload: dict, token: str = Query(...), db: OrmSession = Depends(get_db)):
    from backend.app.domain.progressive import Configuration
    from backend.app.services.simulation import prepare
    case = _get_case_or_fail(db, case_id, token)
    row = db.get(models.SimulationConfig, case_id)
    if case.status == 'generating' or not row or not row.draft_json.get('enabled'):
        raise HTTPException(409, '请先保存递进规则，再指定标准路径')
    if payload.get('rules') != row.draft_json.get('rules') or payload.get('cash_flow_amount') != row.draft_json.get('cash_flow_amount'):
        raise HTTPException(409, '递进指标尚未保存，请先保存递进规则再指定标准路径')
    try:
        config = Configuration.model_validate({**row.draft_json, 'reference_path': payload.get('reference_path')}).model_dump()
        snapshot, _ = prepare(case, config)
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    row.draft_json = config
    from backend.app.integrations.ai.artifacts import artifact_store
    artifact_store.save_json(db, snapshot.get('reference_result') or {}, kind='teacher_reference_result', case_id=case_id)
    db.commit()
    return {'ok': True, 'reference_path': config.get('reference_path'), 'reference_result': snapshot.get('reference_result')}

@router.get("/cases/{case_id}/records", response_model=List[schemas.StudentRecord])
def list_records(
    case_id: int,
    token: str = Query(..., description="教师端令牌"),
    db: OrmSession = Depends(get_db),
) -> List[schemas.StudentRecord]:
    """按「session × attempt」分组返回 turn 列表。"""
    case = _get_case_or_fail(db, case_id, token)

    records: List[schemas.StudentRecord] = []
    sessions = db.scalars(
        select(models.Session).where(models.Session.case_id == case.id).order_by(models.Session.id)
    ).all()
    for session in sessions:
        attempts = sorted({t.attempt_no for t in session.turns}) or [session.attempt_no]
        for attempt_no in attempts:
            turns = [
                t
                for t in sorted(session.turns, key=lambda x: (x.attempt_no, x.id))
                if t.attempt_no == attempt_no
            ]
            if not turns:
                continue
            records.append(
                schemas.StudentRecord(
                    student_name=session.student_name,
                    student_id=session.student_id,
                    attempt_no=attempt_no,
                    session_id=getattr(session, "id", None),
                    review_id=next((r.id for r in getattr(session, "reviews", []) if r.attempt_no == attempt_no), None),
                    review_status=("succeeded" if any(r.attempt_no == attempt_no for r in getattr(session, "reviews", []))
                        else next((t.status for t in reversed(getattr(session, "ai_tasks", [])) if t.kind == "student_review"), "not_started")),
                    review=next(({"framework_type": r.framework_type, "dimensions": r.dimensions_json, "conclusion": r.conclusion}
                        for r in getattr(session, "reviews", []) if r.attempt_no == attempt_no), None),
                    turns=[
                        schemas.TurnItem(
                            id=t.id,
                            node_id=t.node_id,
                            idx=t.node.idx if t.node is not None else 0,
                            chosen_option=t.chosen_option,
                            **choice_receipt(session, t.node_id, t.chosen_option),
                            input_text=t.input_text,
                            before_metrics=t.before_metrics_json,
                            after_metrics=t.after_metrics_json,
                            delta_metrics=t.delta_metrics_json,
                            result_source=t.result_source,
                            result_json={
                                "metrics": (
                                    t.result_json.get("metrics")
                                    or t.result_json.get("after_metrics")
                                    or t.after_metrics_json
                                ),
                                "summary": t.result_json.get("summary", ""),
                            },
                            duration_ms=t.duration_ms,
                            created_at=t.created_at,
                        )
                        for t in turns
                    ],
                )
            )
    return records

@router.patch("/reviews/{review_id}", response_model=schemas.OkResponse)
def patch_review(review_id: int, payload: schemas.ReviewPatchRequest, token: str = Query(...), db: OrmSession = Depends(get_db)):
    if not is_valid_teacher_token(db, token): raise HTTPException(status_code=403, detail="令牌无效")
    row = db.get(models.Review, review_id)
    if row is None: raise HTTPException(status_code=404, detail="复盘不存在")
    from backend.app.domain.review_contract import validate_review
    dimensions = [d.model_dump() for d in payload.dimensions] if payload.dimensions is not None else row.dimensions_json
    conclusion = payload.conclusion if payload.conclusion is not None else row.conclusion
    issues = validate_review({'dimensions': dimensions, 'conclusion': conclusion}, row.framework_type)
    if issues: raise HTTPException(status_code=400, detail='；'.join(issues))
    row.dimensions_json, row.conclusion = dimensions, conclusion
    artifact_store.save_json(db, {"review_id": row.id, "framework_type": row.framework_type,
        "dimensions": row.dimensions_json, "conclusion": row.conclusion, "authority": "teacher"},
        kind="teacher_review_final", case_id=row.session.case_id, session_id=row.session_id)
    db.commit()
    return schemas.OkResponse(ok=True)
