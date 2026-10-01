"""商科规则载入层。

启动时读 docs/商科规则.json 与 docs/标杆案例.json，把内容暴露为模块级常量，
供后续步骤（LLM 提示词编排、财务数据校验、复盘维度校验）直接引用。
文件缺失或结构与预期不符时抛 RulesError，进程直接起不来——避免带着错误规则跑。
"""
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from backend.app import schemas

# Host paths stay stable when this rule loader moves between packages.
from backend.app.paths import PROJECT_ROOT as ROOT_DIR
RULES_PATH = ROOT_DIR / "docs" / "商科规则.json"
SAMPLE_CASE_PATH = ROOT_DIR / "docs" / "标杆案例.json"


class RulesError(RuntimeError):
    """商科规则文件缺失或结构不符时抛出，附带可读的定位信息。"""


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise RulesError(
            "找不到商科规则文件：%s\n"
            "请先执行 `python docs/build_docs.py` 从 docs/source/ 的两份 docx 生成。" % path
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RulesError("商科规则文件不是合法 JSON：%s（%s）" % (path, exc)) from exc
    if not isinstance(data, dict):
        raise RulesError("商科规则文件的顶层必须是 JSON 对象：%s" % path)
    return data


def _require_keys(data: Dict[str, Any], keys: Tuple[str, ...], source: Path) -> None:
    missing = [k for k in keys if k not in data]
    if missing:
        raise RulesError("%s 缺少必需键：%s" % (source.name, "、".join(missing)))


# ─────────────────────────── 载入原始内容 ───────────────────────────

RULES: Dict[str, Any] = _load_json(RULES_PATH)
SAMPLE_CASE: Dict[str, Any] = _load_json(SAMPLE_CASE_PATH)

_require_keys(
    RULES,
    (
        "case_types",
        "industry_baseline",
        "transmission_rules",
        "review_templates",
        "metric_formulas",
        "common_constraints",
        "input_formats",
        "material_requirements",
    ),
    RULES_PATH,
)
_require_keys(
    SAMPLE_CASE,
    (
        "title",
        "case_type",
        "framework",
        "background",
        "dilemma",
        "source_results",
        "base_metrics",
        "nodes",
        "financial_results",
        "standard_path",
        "standard_review",
        "simulation_results",
        "standard_final_metrics",
        "standard_path_reasons",
    ),
    SAMPLE_CASE_PATH,
)

# ─────────────────────────── 模块级常量 ───────────────────────────

#: 三类案例类型及其对应分析框架、决策设计核心维度
CASE_TYPES: List[Dict[str, str]] = RULES["case_types"]
#: 行业基准区间（校园餐饮/零售、制造类）
INDUSTRY_BASELINE: List[Dict[str, str]] = RULES["industry_baseline"]
#: 决策传导逻辑，固定 5 条
TRANSMISSION_RULES: List[Dict[str, str]] = RULES["transmission_rules"]
# 指标公式、通用约束、素材格式与最低材料要求，均由 build_docs.py 从源 docx 生成
METRIC_FORMULAS: List[Dict[str, str]] = RULES["metric_formulas"]
COMMON_CONSTRAINTS: List[Dict[str, str]] = RULES["common_constraints"]
INPUT_FORMATS: List[Dict[str, str]] = RULES["input_formats"]
MATERIAL_REQUIREMENTS: List[Dict[str, str]] = RULES["material_requirements"]
#: 三套复盘模板的维度名称与顺序（5 / 5 / 4）
REVIEW_TEMPLATES: List[Dict[str, Any]] = RULES["review_templates"]

#: 指标固定四键：营收 / 毛利率 / 市场份额 / 现金流
METRIC_KEYS: Tuple[str, ...] = tuple(k for k in TRANSMISSION_RULES[0] if k != "decision_type")

#: 案例类型 -> 核心分析框架
CASE_TYPE_TO_FRAMEWORK: Dict[str, str] = {
    item["case_type"]: item["framework"] for item in CASE_TYPES
}
#: 分析框架 -> 复盘维度名称与顺序
FRAMEWORK_DIMENSIONS: Dict[str, List[str]] = {
    item["template"]: list(item["dimensions"]) for item in REVIEW_TEMPLATES
}

#: 标杆案例（研咖咖啡）标准答案
SAMPLE_CASE_TYPE: str = SAMPLE_CASE["case_type"]
SAMPLE_BASE_METRICS: Dict[str, Any] = SAMPLE_CASE["base_metrics"]
SAMPLE_NODES: List[Dict[str, Any]] = SAMPLE_CASE["nodes"]
SAMPLE_FINANCIAL_RESULTS: List[Dict[str, Any]] = SAMPLE_CASE["financial_results"]
SAMPLE_STANDARD_PATH: str = SAMPLE_CASE["standard_path"]
SAMPLE_SIMULATION_RESULTS: List[Dict[str, Any]] = SAMPLE_CASE["simulation_results"]
SAMPLE_STANDARD_REVIEW: Dict[str, Any] = SAMPLE_CASE["standard_review"]


# ─────────────────────── 启动自检：枚举与规则文件对齐 ───────────────────────

def _self_check() -> None:
    """若 schemas 的枚举与商科规则文件不一致，立刻报错，避免规则悄悄漂移。"""
    problems: List[str] = []

    rule_case_types = {item["case_type"] for item in CASE_TYPES}
    schema_case_types = {m.value for m in schemas.CaseType}
    if rule_case_types != schema_case_types:
        problems.append(
            "CaseType 与 case_types 不一致：规则文件=%s，schemas=%s"
            % (sorted(rule_case_types), sorted(schema_case_types))
        )

    rule_frameworks = {item["template"] for item in REVIEW_TEMPLATES}
    schema_frameworks = {m.value for m in schemas.ReviewFramework}
    if rule_frameworks != schema_frameworks:
        problems.append(
            "ReviewFramework 与 review_templates 不一致：规则文件=%s，schemas=%s"
            % (sorted(rule_frameworks), sorted(schema_frameworks))
        )

    rule_risks = {o["risk_level"] for n in SAMPLE_NODES for o in n["options"]}
    schema_risks = {m.value for m in schemas.RiskLevel}
    if rule_risks != schema_risks:
        problems.append(
            "RiskLevel 与标杆案例不一致：规则文件=%s，schemas=%s"
            % (sorted(rule_risks), sorted(schema_risks))
        )

    rule_roles = {n["node_role"] for n in SAMPLE_NODES}
    schema_roles = {m.value for m in schemas.NodeRole}
    if rule_roles != schema_roles:
        problems.append(
            "NodeRole 与标杆案例不一致：规则文件=%s，schemas=%s"
            % (sorted(rule_roles), sorted(schema_roles))
        )

    if METRIC_KEYS != ("revenue", "gross_margin", "market_share", "cash_flow"):
        problems.append("transmission_rules 的指标键不是固定四键，实际为 %s" % (METRIC_KEYS,))

    review_counts = [len(item.get("dimensions", [])) for item in REVIEW_TEMPLATES]
    if review_counts != [5, 5, 4]:
        problems.append("review_templates 必须保持原文 5/5/4，实际为 %s" % review_counts)
    if SAMPLE_STANDARD_REVIEW.get("framework_type") != "4P营销理论":
        problems.append("标杆案例 standard_review.framework_type 必须为 4P营销理论")
    if len(SAMPLE_STANDARD_REVIEW.get("dimensions", [])) != 5:
        problems.append("标杆案例 standard_review 确认前必须保留 5 个原文维度")

    if len(SAMPLE_SIMULATION_RESULTS) != 9:
        problems.append("simulation_results 必须恰好 9 条")
    required_simulation_keys = {"option_key", "risk_level", "label", "summary", "metrics"}
    for item in SAMPLE_SIMULATION_RESULTS:
        missing = required_simulation_keys - set(item)
        if missing:
            problems.append("simulation_results 缺少字段：%s" % sorted(missing))
        if set(item.get("metrics", {})) != set(METRIC_KEYS):
            problems.append("simulation_results.metrics 必须固定为四键：%s" % (METRIC_KEYS,))

    if problems:
        raise RulesError("商科规则自检未通过：\n  - " + "\n  - ".join(problems))


_self_check()
