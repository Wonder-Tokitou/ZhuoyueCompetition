"""Host validation and durable draft tasks. Never writes teacher SimulationConfig."""
import hashlib
import json
import uuid
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from backend.app import models
from backend.app.domain import progressive, rules
from backend.app.services.simulation import live_snapshot
from ai_component.agents.impact_rules import generate_impact_rules
from contracts.errors import ValidationExhausted
from .artifacts import artifact_store


def context(case):
    snapshot = live_snapshot(case)
    return {"snapshot": snapshot, "assumptions": case.financial_assumptions_json or {},
            "policy": {"transmission": rules.TRANSMISSION_RULES,
                       "industry_reference": rules.INDUSTRY_BASELINE}, "prompt_version": 1}


def fingerprint(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def ensure_task(db, case, retry=False):
    saved = db.get(models.SimulationConfig, case.id)
    if saved and saved.draft_json:
        return None  # Human-owned saved rules always win, including all-zero rules.
    if case.status == "generating" or len(case.nodes) != 3:
        raise ValueError("请先完成基准保存和三个决策节点生成")
    data = context(case)
    key = fingerprint(data)
    existing = db.scalar(select(models.AiTask).where(models.AiTask.case_id == case.id,
        models.AiTask.kind == "impact_rules", models.AiTask.idempotency_key == key)
        .order_by(models.AiTask.id.desc()))
    if existing:
        if retry and existing.status in ("failed", "cancelled"):
            existing.status, existing.stage = "queued", "queued"
            existing.attempts = 0
            existing.error = existing.finished_at = None
            db.commit()
        return existing
    task = models.AiTask(task_id=uuid.uuid4().hex, case_id=case.id, kind="impact_rules",
        status="queued", stage="queued", input_json=data, idempotency_key=key, max_attempts=2)
    db.add(task)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return db.scalar(select(models.AiTask).where(models.AiTask.case_id == case.id,
            models.AiTask.kind == "impact_rules", models.AiTask.idempotency_key == key))
    return task


def validate_candidate(data, candidate):
    if not isinstance(candidate, dict) or set(candidate) != {"rules", "reasoning"}:
        raise ValueError("只允许rules和reasoning，不能更改基准")
    config = progressive.Configuration(enabled=True, cash_flow_amount=0, rules=candidate["rules"])
    # Cash zero is a validation placeholder only, never persisted as a baseline.
    result = progressive.preview(data["snapshot"], config.model_dump(), data["assumptions"])
    reasoning = candidate["reasoning"]
    if not isinstance(reasoning, dict) or set(reasoning) != set(config.rules):
        raise ValueError("9条规则必须逐条提供依据")
    for item in reasoning.values():
        if not isinstance(item, dict) or not isinstance(item.get("is_assumption"), bool) or any(
            not isinstance(item.get(k), str) or not item[k].strip() for k in ("basis", "caveat")
        ):
            raise ValueError("每条依据需有basis、is_assumption布尔值、caveat")
    if all(all(v == 0 for v in impact.model_dump().values()) for impact in config.rules.values()):
        raise ValueError("不能以全0代替有依据的规则建议")
    return {"rules": {k:v.model_dump() for k,v in config.rules.items()}, "reasoning": reasoning,
            "cash_flow_amount": None, "cash_validation": "等待教师补充金额后校验",
            "validated_paths": len(result["paths"]), "fingerprint": fingerprint(data)}


async def run(db, task):
    data = task.input_json
    errors = ""
    for attempt in range(2):
        candidate = await generate_impact_rules(data, errors)
        artifact_store.save_json(db, candidate, kind="impact_rule_candidate", task_id=task.id, case_id=task.case_id)
        try:
            output = validate_candidate(data, candidate)
        except (ValueError, KeyError, TypeError) as exc:
            errors = str(exc)
            artifact_store.save_json(db, {"error": errors}, kind="impact_rule_validation", task_id=task.id, case_id=task.case_id)
            db.commit()
            continue
        db.expire_all()
        case = db.get(models.Case, task.case_id)
        if case is None or fingerprint(context(case)) != task.idempotency_key:
            raise ValidationExhausted("案例已修改，本次AI建议已归档，未填入；请重新生成")
        saved = db.get(models.SimulationConfig, case.id)
        if saved and saved.draft_json:
            raise ValidationExhausted("教师已保存规则，本次AI建议未覆盖人工内容")
        task.output_json = output
        task.status = "succeeded"
        task.stage = "completed"
        task.finished_at = datetime.now()
        db.commit()
        return
    raise ValidationExhausted("AI影响规则未通过校验：" + errors)
