"""Host adapter: bind teaching rules and validation, persist accepted drafts."""
from typing import Any, Dict, List
from copy import deepcopy
import json
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy import update
from contracts.errors import ValidationExhausted
from backend.app import models
from backend.app.domain import rules, validator
from backend.app.domain import financials
from ai_component import generate_valid_case as generate_candidate
from ai_component.agents.case_workflow import generate_valid_baseline as generate_baseline_candidate
from .evidence import teaching_policy

def review_placeholder(case_type: str) -> List[Dict[str, str]]:
    framework = rules.CASE_TYPE_TO_FRAMEWORK.get(case_type, "")
    return [{"name": name, "content": ""} for name in rules.FRAMEWORK_DIMENSIONS.get(framework, [])]



async def generate_valid_case(source_text, source_kind, case_type, framework, extra_hint="", base_metrics=None):
    template = {"prompt_template": framework.prompt_template} if framework is not None else None
    confirmed_base = deepcopy(base_metrics)
    def validate(payload):
        scope_errors = []
        if confirmed_base is not None:
            proposed = payload.get("base_metrics") or {}
            if not isinstance(proposed, dict) or any(
                validator._as_float(proposed.get(key)) != validator._as_float(confirmed_base.get(key))
                for key in ("revenue", "gross_margin", "market_share")
            ):
                scope_errors.append("教师已确认基准不可更改：请恢复基准并以同一经营范围、时间口径重新核对节点数值，不得把企业合计营收换成单店营收。")
            payload["base_metrics"] = deepcopy(confirmed_base)
        # 节点阶段使用教师已确认的企业口径；行业表仅约束 AI 自行生成的基准。
        if confirmed_base is None:
            ok, errors = validator.validate_case(payload)
        else:
            ok, errors = validator.validate_case(payload, check_industry_range=False)
        errors.extend(scope_errors)
        for index, node in enumerate(payload.get("nodes") or [], 1):
            for option in node.get("options") or []:
                assumptions = option.get("financial_assumptions") or {}
                if not all(k in assumptions and isinstance(assumptions[k], (int, float)) for k in ("operating_cost", "operating_expense")):
                    errors.append("节点%d选项%s缺少营业成本/运营费用数值教学假设，无法审计净利润" % (index, option.get("key", "?")))
        return (not errors, errors)
    if confirmed_base is not None:
        extra_hint = ((extra_hint + "\n\n") if extra_hint else "") + (
            "教师已确认基准数据：" + json.dumps(confirmed_base, ensure_ascii=False) + "。必须保持同一经营范围和时间口径。"
            "不要把企业总营收改写成单店营收，不要用行业单店区间替换教师数据；"
            "节点选项 metrics 的 revenue 以教师确认的基准为比较口径。"
        )
    return await generate_candidate(source_text, source_kind, case_type, template,
                                    policy=teaching_policy(), validate=validate,
                                    extra_hint=extra_hint)


async def generate_valid_baseline(source_text, source_kind, case_type, framework):
    template = {"prompt_template": framework.prompt_template} if framework is not None else None
    return await generate_baseline_candidate(source_text, source_kind, case_type, template,
        policy=teaching_policy())

def persist_generated_case(db: OrmSession, case: models.Case, payload: Dict[str, Any], expected_version=None, *, persist_nodes=True, update_baseline=True) -> None:
    """将通过校验的 AI 输出写入案例草稿。教师后续修改仍是最终权威。"""
    if expected_version is not None:
        changed = db.execute(update(models.Case).where(
            models.Case.id == case.id, models.Case.version == expected_version,
        ).values(version=expected_version + 1).execution_options(synchronize_session=False))
        if changed.rowcount != 1:
            raise ValidationExhausted("案例已被教师或另一任务更新；AI 候选稿已保留，未覆盖现有内容")
        db.refresh(case)
    original_baseline = case.base_metrics_json or {}
    if update_baseline:
        case.base_metrics_json = payload.get("base_metrics")
        base_financials = payload.get("financial_assumptions") or {}
        case.financial_assumptions_json = base_financials
        case.base_net_profit = (financials.net_profit(float(case.base_metrics_json["revenue"]),
            float(base_financials["operating_cost"]), float(base_financials["operating_expense"]))
            if all(k in base_financials for k in ("operating_cost", "operating_expense")) else None)
    else:
        base_financials = case.financial_assumptions_json or {}
    # The first AI baseline draft may populate the initial case brief. Later
    # node generation must never rewrite teacher-saved case text.
    if update_baseline:
        case.background = payload.get("background") or ""
        case.dilemma = payload.get("dilemma") or ""
    if not persist_nodes:
        return
    existing_nodes = {node.idx: node for node in case.nodes}
    for node_data in payload.get("nodes") or []:
        options = node_data.get("options") or []
        background = node_data.get("background") or ""
        idx = node_data.get("idx")
        node = existing_nodes.get(idx)
        if node is None:
            node = models.Node(case_id=case.id, idx=idx, scenario=background,
                options_json=[], node_role=node_data.get("node_role"), title=node_data.get("title") or "",
                background=background)
            db.add(node)
            db.flush()
        else:
            node.scenario = node.background = background
            node.node_role = node_data.get("node_role")
            node.title = node_data.get("title") or ""
            for old_option in list(node.option_results):
                db.delete(old_option)
            db.flush()
        node.options_json = [{"key": o.get("key"), "label": o.get("label")} for o in options]
        for option in options:
            db.add(
                models.OptionResult(
                    node_id=node.id,
                    option_key=option.get("key"),
                    label=option.get("label") or "",
                    metrics_json=option.get("metrics") or {},
                    net_profit=(financials.net_profit(float(option["metrics"]["revenue"]),
                        float(option["financial_assumptions"]["operating_cost"]),
                        float(option["financial_assumptions"]["operating_expense"]))
                        if all(k in (option.get("financial_assumptions") or {}) for k in ("operating_cost", "operating_expense")) else None),
                    financial_basis_json=build_financial_basis(option, payload.get("base_metrics") or {}, base_financials),
                    risk_level=option.get("risk_level"),
                    summary=option.get("summary") or "",
                )
            )


def build_financial_basis(option: Dict[str, Any], baseline: Dict[str, Any], baseline_assumptions: Dict[str, Any]) -> Dict[str, Any]:
    """Trace reported values to deterministic formulas or explicit teaching estimates."""
    assumptions = option.get("financial_assumptions") or {}
    base_cost = float(baseline_assumptions.get("operating_cost", 0))
    base_expense = float(baseline_assumptions.get("operating_expense", 0))
    # A proportional carry-forward keeps downstream profit assumptions aligned
    # with the teacher's revised baseline unless a node-specific assumption exists.
    ratio = (float(option.get("metrics", {}).get("revenue", 0)) / float(baseline.get("revenue", 1))) if float(baseline.get("revenue", 0)) else 1.0
    assumptions.setdefault("operating_cost", round(base_cost * ratio, 2))
    assumptions.setdefault("operating_expense", round(base_expense * ratio, 2))
    result = {"source": "AI情境预测，教师确认后用于教学推演", "assumptions": assumptions,
        "indicators": {}}
    for key, label in (("revenue", "营收"), ("gross_margin", "毛利率"),
                       ("market_share", "市场份额"), ("cash_flow", "现金流")):
        result["indicators"][key] = {"label": label, "before": baseline.get(key), "after": (option.get("metrics") or {}).get(key),
            "method": "基于案例素材与行业/传导规则的情境推测，非确定性财报事实"}
    if all(k in assumptions for k in ("operating_cost", "operating_expense")):
            result["indicators"]["net_profit"] = {
            "label": "净利润", "before": baseline.get("revenue", 0) - float(baseline_assumptions.get("operating_cost", 0)) - float(baseline_assumptions.get("operating_expense", 0)),
            "after": financials.net_profit(float(option["metrics"]["revenue"]), float(assumptions["operating_cost"]), float(assumptions["operating_expense"])),
            "formula": "营收－营业成本－运营费用",
            "inputs": {"revenue": option["metrics"]["revenue"], "operating_cost": assumptions["operating_cost"], "operating_expense": assumptions["operating_expense"]},
            "method": "按AI提出的教学假设输入，经确定性脚本计算；不是原始财报值"}
    else:
        result["indicators"]["net_profit"] = {"label": "净利润", "after": None,
            "method": "缺少营业成本或运营费用假设，无法计算；未虚构数值"}
    return result
