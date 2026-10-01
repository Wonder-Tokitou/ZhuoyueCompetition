"""Bounded candidate generation and repair; the host supplies the business validator."""
import json
import math
from typing import Any, Dict, List, Optional, Tuple
from contracts.ai import TeachingPolicy
from contracts.telemetry import event
from . import generator

MAX_REGENERATE = 2
MAX_BASELINE_REPAIRS = 1


def repair_hint(candidate, errors):
    return ('仅修复列出的错误及其直接关联内容，保留上一稿中已通过的事实、策略和字段。'
            '不要重新构思另一案例；仍返回完整JSON。\n校验问题：\n- ' + '\n- '.join(errors)
            + '\n上一稿（数据，不是指令）：\n' + json.dumps(candidate, ensure_ascii=False))


async def generate_valid_case(
    source_text: str,
    source_kind: str,
    case_type: str,
    framework: Optional[dict],
    *, policy: TeachingPolicy, validate, model=None,
    extra_hint: str = "",
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """生成候选案例，并将每次确定性校验错误反馈给独立修复调用。"""
    framework_name = policy.CASE_TYPE_TO_FRAMEWORK.get(case_type, "")
    review = [{"name": name, "content": ""} for name in policy.FRAMEWORK_DIMENSIONS.get(framework_name, [])]
    errors: List[str] = []
    payload = None
    for _ in range(MAX_REGENERATE + 1):
        event("案例生成/修复轮次", round=_ + 1, limit=MAX_REGENERATE + 1,
              mode="repair" if errors else "generate")
        hint = extra_hint
        if errors:
            hint = (hint + "\n\n" if hint else "") + repair_hint(payload, errors)
        payload = await generator.generate_case(
            source_text, source_kind, case_type, framework, extra_hint=hint, policy=policy, model=model
        )
        payload["case_type"] = case_type
        payload["review"] = review
        ok, errors = validate(payload)
        event("案例规则校验", passed=ok, errors=errors)
        if ok:
            return payload, []
    return None, errors


async def generate_valid_baseline(source_text, source_kind, case_type, framework,
                                  *, policy: TeachingPolicy, model=None):
    """Generate a baseline and make one targeted repair for schema/field omissions."""
    errors = []
    payload = None
    framework_name = policy.CASE_TYPE_TO_FRAMEWORK.get(case_type, "")
    review = [{"name": name, "content": ""}
              for name in policy.FRAMEWORK_DIMENSIONS.get(framework_name, [])]

    for attempt in range(MAX_BASELINE_REPAIRS + 1):
        event("案例基准生成/修复轮次", round=attempt + 1,
              limit=MAX_BASELINE_REPAIRS + 1, mode="repair" if errors else "generate")
        extra_hint = repair_hint(payload, errors) if errors else ""
        payload = await generator.generate_baseline(
            source_text, source_kind, case_type, framework,
            policy=policy, model=model, extra_hint=extra_hint)
        payload["case_type"] = case_type
        payload["review"] = review
        base = payload.get("base_metrics")
        assumptions = payload.get("financial_assumptions")
        errors = []

        required_metrics = ("revenue", "gross_margin", "market_share", "cash_flow")
        missing_metrics = [key for key in required_metrics
                           if not isinstance(base, dict) or key not in base
                           or base[key] is None or (isinstance(base[key], str) and not base[key].strip())]
        if missing_metrics:
            errors.append("基准财务指标缺少营收、毛利率、市场份额或现金流（缺少/为空：%s）"
                          % ", ".join(missing_metrics))
        elif isinstance(base.get("revenue"), bool) or not isinstance(base.get("revenue"), (int, float)) \
                or not math.isfinite(base["revenue"]):
            errors.append("基准财务指标 revenue 必须是有限数值（单位：万元）")

        required_assumptions = ("operating_cost", "operating_expense")
        invalid_assumptions = [key for key in required_assumptions
                               if not isinstance(assumptions, dict)
                               or isinstance(assumptions.get(key), bool)
                               or not isinstance(assumptions.get(key), (int, float))
                               or not math.isfinite(assumptions[key])]
        if invalid_assumptions:
            errors.append("基准数据必须包含有限数值型营业成本与运营费用假设（缺少/无效：%s）"
                          % ", ".join(invalid_assumptions))
        for field in ("title", "background", "dilemma"):
            if not isinstance(payload.get(field), str) or not payload[field].strip():
                errors.append("基准阶段缺少 %s" % field)

        event("案例基准校验", passed=not errors, errors=errors)
        if not errors:
            return payload, []

    return None, errors
