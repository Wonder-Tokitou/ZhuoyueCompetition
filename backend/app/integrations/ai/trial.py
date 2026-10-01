"""Legacy self-study generation, separate from deterministic classroom decisions."""
from typing import Any, Dict, List, Optional, Tuple
from backend.app.domain import rules, validator
from ai_component.agents import generator
from .evidence import teaching_policy

TRY_MAX_REGENERATE = 2

def _try_structure_ok(payload: Dict[str, Any]) -> bool:
    """结构兜底：3 个节点、每节点 3 个选项，且 summary / metrics 齐备。

    V2/V3 之外必须加这道闸，否则后面按字段取值会直接抛异常。
    """
    nodes = payload.get("nodes")
    if not isinstance(nodes, list) or len(nodes) != 3:
        return False
    for node in nodes:
        if not isinstance(node, dict):
            return False
        options = node.get("options")
        if not isinstance(options, list) or len(options) != 3:
            return False
        for option in options:
            if not isinstance(option, dict):
                return False
            if not str(option.get("summary") or "").strip():
                return False
            if not isinstance(option.get("metrics"), dict):
                return False
    return True


async def generate_try_case(
    source_text: str, source_kind: str, case_type: str
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """复用 generate_case 生成试跑结构；只以 V2 / V3（外加结构兜底）为准判通过。"""
    expected = rules.FRAMEWORK_DIMENSIONS.get(
        rules.CASE_TYPE_TO_FRAMEWORK.get(case_type, ""), []
    )
    review_placeholder = [{"name": name, "content": ""} for name in expected]
    errors: List[str] = []
    payload = None

    for _ in range(TRY_MAX_REGENERATE + 1):
        hint = ""
        if errors:
            from ai_component.agents.case_workflow import repair_hint
            hint = repair_hint(payload, errors)
        payload = await generator.generate_case(
            source_text, source_kind, case_type, None, extra_hint=hint, policy=teaching_policy()
        )
        payload["case_type"] = case_type
        payload["review"] = review_placeholder

        if not _try_structure_ok(payload):
            errors = ["结构不完整：必须包含 3 个节点、每个节点 3 个选项，且每个选项的 summary 与 metrics 不得缺失"]
            continue

        _, all_errors = validator.validate_case(payload)
        errors = [e for e in all_errors if e.startswith("V2") or e.startswith("V3")]
        if not errors:
            return payload, []
    return None, errors
