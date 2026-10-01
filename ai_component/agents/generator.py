"""案例生成链路：素材 / 主题关键词 -> 模型 -> 结构化案例。

Prompt 由「框架自带的 prompt_template」+「商科规则.json 的三张规则表」拼成，
输出结构固定，硬约束写死在 system prompt 里，模型跑偏由 validator.py 兜住。
"""
from typing import Any, Dict, List

from contracts.ai import NODE_ROLES, TeachingPolicy
from ai_component.prompts.case_generation import build_messages
from ai_component.providers.llm import chat_json

#: 模型必须返回的顶层键（同时作为 chat_json 的 schema）
REQUIRED_KEYS = ("title", "background", "dilemma", "base_metrics", "nodes")
OUTPUT_SCHEMA: Dict[str, Any] = {k: None for k in REQUIRED_KEYS}
BASELINE_SCHEMA: Dict[str, Any] = {"title": None, "background": None, "dilemma": None,
    "base_metrics": None, "financial_assumptions": {"operating_cost": None, "operating_expense": None}}

#: 节点角色按序号固定


#: 指标固定四键


async def generate_case(
    source_text: str,
    source_kind: str,
    case_type: Any,
    framework: dict | None,
    extra_hint: str = "",
    *, policy: TeachingPolicy, model=None,
) -> Dict[str, Any]:
    """调模型生成案例结构。

    case_type  : 字符串或 schemas.CaseType 枚举。
    framework  : 仅含 prompt_template 的字典；传 None 用内置兜底模板。
    extra_hint : 可选，上一轮的校验错误说明，用于重新生成时定向纠偏。
    返回：符合 OUTPUT_SCHEMA 的 dict；失败时抛 llm.LLMFailed。
    """
    case_type_value = getattr(case_type, "value", None) or str(case_type)
    if isinstance(framework, dict):
        prompt_template = framework.get("prompt_template") or ""
    else:
        prompt_template = ""

    messages = build_messages(
        source_text, source_kind, case_type_value, prompt_template, extra_hint, policy=policy
    )
    return await (model or chat_json)(messages, OUTPUT_SCHEMA, retries=1, stage='case_generation')


async def generate_baseline(source_text: str, source_kind: str, case_type: Any, framework: dict | None,
                            *, policy: TeachingPolicy, model=None, extra_hint: str = "") -> Dict[str, Any]:
    """Generate only the case brief and financial baseline; never generate decision nodes here."""
    case_type_value = getattr(case_type, "value", None) or str(case_type)
    prompt_template = framework.get("prompt_template", "") if isinstance(framework, dict) else ""
    messages = build_messages(source_text, source_kind, case_type_value, prompt_template,
                              extra_hint=extra_hint, policy=policy)
    messages[0]["content"] = messages[0]["content"].split("【输出结构】", 1)[0] + (
        '\n【本阶段输出结构】只返回一个 JSON 对象，不要 Markdown，不要输出 nodes 或任何决策选项：\n'
        '{"title":"案例标题","background":"企业背景","dilemma":"核心经营困境",'
        '"base_metrics":{"revenue":数值(万元),"gross_margin":"百分比字符串",'
        '"market_share":"百分比字符串","cash_flow":"现金流方向描述"},'
        '"financial_assumptions":{"operating_cost":数值(万元),"operating_expense":数值(万元)}}\n'
        '【基准数据硬约束】base_metrics 必须是对象，revenue、gross_margin、market_share、cash_flow 四个键全部必填且不得为空；'
        '不得把四项合并成自然语言或省略。素材没有给出数值时，依据上面的行业区间生成合理的教学估算，并明确这是教学假设。\n'
        'financial_assumptions 必须同时包含 operating_cost 与 operating_expense 两个数值型万元假设；不得省略。')
    messages[1]["content"] += "\n本阶段只输出案例基准字段，不输出 nodes。"
    return await (model or chat_json)(messages, BASELINE_SCHEMA, retries=1, stage='case_baseline')
