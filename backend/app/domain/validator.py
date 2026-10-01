"""商科规范校验：对「AI 生成的案例结构」逐条落实第九节 AI 输出验收清单。

对外只有一个函数 validate_case(payload) -> (ok, errors)。
payload 需额外携带两个上下文键（不是模型输出的一部分，由调用方注入）：
    case_type : 案例类型字符串，用于查行业基准与复盘模板
    review    : 复盘占位数组；生成阶段为空数组，S4 生成复盘后才填内容
"""
import re
import math
from typing import Any, Dict, List, Optional, Tuple

from backend.app.domain import rules
from backend.app import schemas
from contracts.ai import NODE_ROLES

# ── V1 结构完整性 ──────────────────────────────────────────────
REQUIRED_TOP_KEYS = ["title", "background", "dilemma", "base_metrics", "nodes", "review"]

# ── V6 表述专业性 ──────────────────────────────────────────────
COLLOQUIAL_WORDS = [
    "超级", "爆款", "爆卖", "血赚", "稳赚", "躺赢", "割韭菜", "一炮而红", "火出圈",
    "绝绝子", "yyds", "YYDS", "破防", "emo", "内卷", "卷王", "杠杠的", "牛逼", "牛批",
    "划算爆了", "冲鸭", "裂开", "无语子", "天花板级别", "杀疯了", "赢麻了",
]
EMOJI_PATTERN = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]")


def _as_float(value: Any) -> Optional[float]:
    """把 "62.0%" / 62 / "62" 统一成浮点数；解析不出来返回 None。"""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        found = re.findall(r"-?\d+(?:\.\d+)?", value)
        if found:
            return float(found[0])
    return None


def _non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _parse_range(text: str) -> Optional[Tuple[float, float]]:
    """从 "55%—70%" / "单店月营收8万—20万元" 这类文案里取出 (下界, 上界)。"""
    numbers = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", text or "")]
    if len(numbers) < 2:
        return None
    return min(numbers[0], numbers[1]), max(numbers[0], numbers[1])


def _industry_candidates() -> List[Dict[str, str]]:
    """行业基准的全部候选行。

    商科规则里没有「案例类型 → 行业」的映射（行业由案例本身的业务决定），
    所以这里把两条基准都作为候选：只要落在其中任一区间内即算通过。
    """
    return list(rules.INDUSTRY_BASELINE)


def _direction(text: str) -> int:
    """把传导逻辑表里的方向文案转成 +1 / 0 / -1（只看第一个分句）。

    先判「下降类」再判「上升类」：像「短期流出增加」这种同时含方向词与量词的表述，
    必须由「流出」定方向，不能被「增加」带偏。
    """
    head = re.split(r"[，,；;]", text or "")[0]
    if "影响小" in head or "持平" in head or "平稳" in head or "不变" in head:
        return 0
    for word in ("流出", "收紧", "下降", "下滑", "萎缩", "降低"):
        if word in head:
            return -1
    for word in ("上升", "提升", "增长", "改善", "扩大", "流入"):
        if word in head:
            return 1
    return 0


def _is_long_horizon(text: str) -> bool:
    """方向文案落在「中期/长期」时，不能用 6 个月的实测数据去判定（测量窗口不匹配）。"""
    head = re.split(r"[，,；;]", text or "")[0]
    return ("中期" in head) or ("长期" in head)


#: 从选项文案反推决策类型的关键词
DECISION_KEYWORDS: List[Tuple[str, Tuple[str, ...]]] = [
    ("高价策略", ("提价", "涨价", "高价", "溢价", "高端定价")),
    ("低价策略", ("降价", "低价", "平价", "让利", "价格战")),
    ("渠道扩张", ("扩张", "开店", "新店", "新增门店", "渠道", "进入新")),
    # 只认「确实在加大投放」的说法，光有「营销」两个字不算（低预算口碑营销不属于加大投入）
    ("加大营销投入", ("广告", "投放", "补贴", "赞助", "种草", "加大营销", "大规模促销")),
    ("成本控制", ("成本", "降本", "控本", "精简")),
]

#: 方向判定的容差（相对变化百分比）。规则原文多带「小幅」这类限定词，
#: 落在容差带内的反向变化不判为违规。
DIRECTION_TOLERANCE = 3.0


def _keyword_hit(label: str, keyword: str) -> bool:
    """关键词命中，但排除被否定的情况（如「暂不扩张」不算渠道扩张）。"""
    for match in re.finditer(re.escape(keyword), label):
        start = match.start()
        if start > 0 and label[start - 1] == "不":
            continue
        return True
    return False


def _infer_decision_type(label: str) -> Optional[str]:
    known = {item["decision_type"] for item in rules.TRANSMISSION_RULES}
    for decision_type, keywords in DECISION_KEYWORDS:
        if decision_type not in known:
            continue
        if any(_keyword_hit(label, word) for word in keywords):
            return decision_type
    return None


def _rule_of(decision_type: str) -> Optional[Dict[str, str]]:
    for item in rules.TRANSMISSION_RULES:
        if item["decision_type"] == decision_type:
            return item
    return None


def _relative_change(current: float, base: float) -> float:
    if base == 0:
        return 0.0
    return (current - base) / abs(base) * 100.0


# ─────────────────────────── 六项校验 ───────────────────────────

def _v1_structure(payload: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    keys = list(payload.keys())
    position = {k: keys.index(k) for k in REQUIRED_TOP_KEYS if k in keys}
    missing = [k for k in REQUIRED_TOP_KEYS if k not in payload]
    if missing:
        errors.append("V1 结构完整性：缺少部分 %s" % "、".join(missing))
        return errors

    order = [position[k] for k in REQUIRED_TOP_KEYS]
    if order != sorted(order):
        errors.append("V1 结构完整性：五个部分的顺序不正确（应为 企业背景 → 核心经营困境 → 节点一/二/三 → 复盘占位）")

    if not _non_empty_str(payload.get("title")):
        errors.append("V1 结构完整性：title 为空")
    if not _non_empty_str(payload.get("background")):
        errors.append("V1 结构完整性：企业背景（background）为空")
    if not _non_empty_str(payload.get("dilemma")):
        errors.append("V1 结构完整性：核心经营困境（dilemma）为空")
    if not isinstance(payload.get("review"), list):
        errors.append("V1 结构完整性：复盘占位（review）必须是数组")

    nodes = payload.get("nodes")
    if not isinstance(nodes, list) or len(nodes) != 3:
        errors.append("V1 结构完整性：nodes 必须恰好 3 个，实际 %s"
                      % (len(nodes) if isinstance(nodes, list) else "非法"))
        return errors

    for index, node in enumerate(nodes, start=1):
        if not isinstance(node, dict):
            errors.append("V1 结构完整性：节点%d 不是对象" % index)
            continue
        if node.get("idx") != index:
            errors.append("V1 结构完整性：节点%d 的 idx 应为 %d，实际 %s" % (index, index, node.get("idx")))
        expected_role = NODE_ROLES[index - 1]
        if node.get("node_role") != expected_role:
            errors.append("V1 结构完整性：节点%d 的 node_role 应为「%s」，实际「%s」"
                          % (index, expected_role, node.get("node_role")))
        if not _non_empty_str(node.get("title")):
            errors.append("V1 结构完整性：节点%d 的 title 为空" % index)
        if not _non_empty_str(node.get("background")):
            errors.append("V1 结构完整性：节点%d 的 background 为空" % index)

        options = node.get("options")
        if not isinstance(options, list) or len(options) != 3:
            errors.append("V1 结构完整性：节点%d 必须恰好 3 个选项，实际 %s"
                          % (index, len(options) if isinstance(options, list) else "非法"))
            continue
        for option in options:
            if not isinstance(option, dict):
                errors.append("V1 结构完整性：节点%d 存在非对象选项" % index)
                continue
            key = option.get("key")
            if not _non_empty_str(option.get("label")):
                errors.append("V1 结构完整性：节点%d 选项%s 的 label 为空" % (index, key))
            if not _non_empty_str(option.get("summary")):
                errors.append("V1 结构完整性：节点%d 选项%s 的 summary 缺失或为空（必填）" % (index, key))
            metrics = option.get("metrics")
            if not isinstance(metrics, dict):
                errors.append("V1 结构完整性：节点%d 选项%s 缺少 metrics" % (index, key))
                continue
            lack = [k for k in rules.METRIC_KEYS if k not in metrics]
            if lack:
                errors.append("V1 结构完整性：节点%d 选项%s 的 metrics 缺 %s"
                              % (index, key, "、".join(lack)))
    return errors


def _v2_risk_gradient(payload: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    expected = {m.value for m in schemas.RiskLevel}
    for index, node in enumerate(payload.get("nodes") or [], start=1):
        if not isinstance(node, dict):
            continue
        risks = [o.get("risk_level") for o in (node.get("options") or []) if isinstance(o, dict)]
        if set(risks) != expected or len(risks) != 3:
            errors.append("V2 选项风险梯度：节点%d 的 risk_level 为 %s，必须恰为 %s 各一个"
                          % (index, "/".join(str(r) for r in risks), "/".join(sorted(expected))))
    return errors


def _v3_numeric_sanity(payload: Dict[str, Any]) -> List[str]:
    """确认教师基准仍是可计算的数值；不判断它属于哪种行业规模。"""
    errors: List[str] = []
    base = payload.get("base_metrics")
    if not isinstance(base, dict):
        return ["V3 财务数据有效性：缺少基准数值 base_metrics"]
    revenue = _as_float(base.get("revenue"))
    margin = _as_float(base.get("gross_margin"))
    share = _as_float(base.get("market_share"))
    if revenue is None or not math.isfinite(revenue) or revenue < 0:
        errors.append("V3 财务数据有效性：revenue 必须是有限的非负数值")
    if margin is None or not (0 <= margin <= 100):
        errors.append("V3 财务数据有效性：gross_margin 必须在 0%—100% 之间")
    if share is None or not (0 <= share <= 100):
        errors.append("V3 财务数据有效性：market_share 必须在 0%—100% 之间")
    return errors


def _v3_industry_range(payload: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    base = payload.get("base_metrics")
    if not isinstance(base, dict):
        errors.append("V3 财务数据区间：缺少 base_metrics，无法比对行业基准")
        return errors

    margin = _as_float(base.get("gross_margin"))
    revenue = _as_float(base.get("revenue"))
    if margin is None or revenue is None:
        errors.append("V3 财务数据区间：base_metrics 的 gross_margin / revenue 无法解析为数值")
        return errors

    candidates = _industry_candidates()
    hits: List[str] = []
    tried: List[str] = []
    for row in candidates:
        margin_range = _parse_range(row["gross_margin_range"])
        if margin_range is None:
            continue
        margin_ok = margin_range[0] <= margin <= margin_range[1]
        # 营收规模列只有写明 万元/月营收 时才可比（制造类的「年增速」不是营收口径）
        scale = row.get("revenue_scale", "")
        revenue_range = _parse_range(scale) if ("万元" in scale or "月营收" in scale) else None
        revenue_ok = True if revenue_range is None else revenue_range[0] <= revenue <= revenue_range[1]
        tried.append("%s（毛利率 %s）" % (row["industry"], row["gross_margin_range"]))
        if margin_ok and revenue_ok:
            hits.append(row["industry"])

    if not hits:
        errors.append(
            "V3 财务数据区间：毛利率 %s%%、单月营收 %s 万元未落在任何行业基准区间内（已比对：%s）"
            % (margin, revenue, "；".join(tried) or "无可用基准")
        )
    return errors


def _cash_flow_direction(text: str) -> int:
    """仅识别明确的现金流短语；不把「流入减少」误认成改善。

    否定、混合方向或不同时间范围的描述不据关键词武断拒绝。
    此处不是完整自然语言推理，无法判断时返回 0。
    """
    signs = set()
    for clause in re.split(r"[，,；;。\n]", text):
        if re.search(r"中期|长期|未来|后期|不|未|无|尚难|难以", clause):
            continue
        # 优先读取流入/流出后面的变化，再处理状态词。
        for match in re.finditer(r"(流入|流出)(?:金额|规模|量)?(?:短期|明显|小幅|大幅|有所|持续)?(增加|增长|上升|减少|下降|降低)", clause):
            direction = 1 if match[2] in ("增加", "增长", "上升") else -1
            signs.add(direction if match[1] == "流入" else -direction)
        if re.search(r"改善|充沛|好转|回笼加快", clause):
            signs.add(1)
        if re.search(r"收紧|紧张|恶化|承压|回笼放缓", clause):
            signs.add(-1)
    return next(iter(signs)) if len(signs) == 1 else 0


def _v4_transmission(payload: Dict[str, Any]) -> List[str]:
    """按选项文案反推决策类型，再比对结果方向。反推不出来就跳过（不误判）。"""
    errors: List[str] = []
    base = payload.get("base_metrics")
    if not isinstance(base, dict):
        return errors

    base_values = {k: _as_float(base.get(k)) for k in ("revenue", "gross_margin", "market_share")}

    for index, node in enumerate(payload.get("nodes") or [], start=1):
        if not isinstance(node, dict):
            continue
        for option in node.get("options") or []:
            if not isinstance(option, dict):
                continue
            label = option.get("label") or ""
            decision_type = _infer_decision_type(label)
            if decision_type is None:
                continue
            rule = _rule_of(decision_type)
            metrics = option.get("metrics") or {}
            if not isinstance(metrics, dict) or rule is None:
                continue

            for metric_key in ("revenue", "gross_margin", "market_share"):
                # 规则写的是「中期/长期」，而测算口径是 6 个月，窗口不匹配就不判
                if _is_long_horizon(rule[metric_key]):
                    continue
                actual = _as_float(metrics.get(metric_key))
                reference = base_values.get(metric_key)
                if actual is None or reference is None:
                    continue
                expected = _direction(rule[metric_key])
                change = _relative_change(actual, reference)
                if expected > 0 and change < -DIRECTION_TOLERANCE:
                    errors.append(
                        "V4 传导逻辑方向：节点%d 选项%s（%s）的 %s 应为上升，实际 %s→%s"
                        % (index, option.get("key"), decision_type, metric_key, reference, actual))
                elif expected < 0 and change > DIRECTION_TOLERANCE:
                    errors.append(
                        "V4 传导逻辑方向：节点%d 选项%s（%s）的 %s 应为下降，实际 %s→%s"
                        % (index, option.get("key"), decision_type, metric_key, reference, actual))
                elif expected == 0 and abs(change) > 10:
                    errors.append(
                        "V4 传导逻辑方向：节点%d 选项%s（%s）对 %s 应为「影响小」，实际变化 %.1f%%"
                        % (index, option.get("key"), decision_type, metric_key, change))

            # 现金流是文案，只查明显相反的说法
            expected_cash = _direction(rule["cash_flow"])
            cash_text = str(metrics.get("cash_flow") or "")
            actual_cash = _cash_flow_direction(cash_text)
            if expected_cash * actual_cash < 0:
                errors.append("V4 传导逻辑方向：节点%d 选项%s 的现金流表述「%s」与规则「%s」方向相反"
                              % (index, option.get("key"), cash_text, rule["cash_flow"]))
    return errors


def _v5_review_dimensions(payload: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    case_type = str(payload.get("case_type") or "")
    framework = rules.CASE_TYPE_TO_FRAMEWORK.get(case_type)
    if not framework:
        errors.append("V5 复盘维度：案例类型「%s」没有对应的复盘模板" % case_type)
        return errors

    expected = rules.FRAMEWORK_DIMENSIONS.get(framework) or []
    required = [name for name in expected if name != "综合结论"]
    if not expected:
        errors.append("V5 复盘维度：框架「%s」在 review_templates 里没有维度定义" % framework)
        return errors

    review = payload.get("review")
    if not isinstance(review, list):
        errors.append("V5 复盘维度：复盘占位（review）必须是数组")
        return errors

    names = [item.get("name") for item in review if isinstance(item, dict)]
    # Drafts may carry the source template's placeholder and optional additions;
    # require the baseline concepts, but do not constrain count or order.
    if any(not isinstance(name, str) or not name.strip() for name in names) or len(names) != len(set(names)):
        errors.append("V5 复盘维度：分析项名称不能为空且不得重复")
        return errors
    normalized = {str(name).replace(" ", "") for name in names}
    missing = [name for name in required if name.replace(" ", "") not in normalized]
    if missing:
        errors.append("V5 复盘维度：缺少基础分析项：" + "、".join(missing))
        return errors

    # 占位阶段 content 允许为空；一旦有内容，就要求全维度都填满
    contents = [str(item.get("content") or "") for item in review if isinstance(item, dict)]
    if any(c.strip() for c in contents) and any(not c.strip() for c in contents):
        errors.append("V5 复盘维度：存在空的维度内容，复盘必须按模板全维度输出")
    return errors


def _v6_professionalism(payload: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    texts: List[Tuple[str, str]] = []

    for key in ("title", "background", "dilemma"):
        if isinstance(payload.get(key), str):
            texts.append((key, payload[key]))
    for index, node in enumerate(payload.get("nodes") or [], start=1):
        if not isinstance(node, dict):
            continue
        texts.append(("节点%d 标题" % index, str(node.get("title") or "")))
        texts.append(("节点%d 背景" % index, str(node.get("background") or "")))
        for option in node.get("options") or []:
            if isinstance(option, dict):
                texts.append(("节点%d 选项%s" % (index, option.get("key")), str(option.get("label") or "")))
                texts.append(("节点%d 选项%s 摘要" % (index, option.get("key")), str(option.get("summary") or "")))
    for item in payload.get("review") or []:
        if isinstance(item, dict):
            texts.append(("复盘维度 %s" % item.get("name"), str(item.get("content") or "")))

    for where, text in texts:
        for word in COLLOQUIAL_WORDS:
            if word in text:
                errors.append("V6 表述专业性：%s 出现口语化/网络化词「%s」" % (where, word))
        if EMOJI_PATTERN.search(text):
            errors.append("V6 表述专业性：%s 出现表情符号" % where)

    # 违背商业常识：提价类选项不应同时出现「市场份额明显上升」
    base = payload.get("base_metrics")
    base_share = _as_float(base.get("market_share")) if isinstance(base, dict) else None
    if base_share:
        for index, node in enumerate(payload.get("nodes") or [], start=1):
            if not isinstance(node, dict):
                continue
            for option in node.get("options") or []:
                if not isinstance(option, dict):
                    continue
                label = option.get("label") or ""
                if not any(w in label for w in ("提价", "涨价", "溢价", "高端定价")):
                    continue
                share = _as_float((option.get("metrics") or {}).get("market_share"))
                if share is None:
                    continue
                if _relative_change(share, base_share) > 5:
                    errors.append(
                        "V6 表述专业性：节点%d 选项%s 提价的同时市场份额反而上升（%s→%s），违背商业常识"
                        % (index, option.get("key"), base_share, share))
    return errors


# ─────────────────────────── 对外入口 ───────────────────────────

def validate_case(payload: Dict[str, Any], *, check_industry_range: bool = True) -> Tuple[bool, List[str]]:
    """按商科规范第九节六项逐条校验。

    返回 (是否通过, 错误说明列表)。错误说明可直接回填给模型做重新生成。
    check_industry_range 仅由宿主控制；教师确认/发布时关闭行业参考区间限制，
    数值有效性、结构、风险与传导检查仍执行，不读取模型提供的同名字段。
    """
    if not isinstance(payload, dict):
        return False, ["V1 结构完整性：模型输出不是 JSON 对象"]

    errors: List[str] = []
    errors += _v1_structure(payload)
    errors += _v2_risk_gradient(payload)
    errors += _v3_numeric_sanity(payload)
    if check_industry_range:
        errors += _v3_industry_range(payload)
    errors += _v4_transmission(payload)
    errors += _v5_review_dimensions(payload)
    errors += _v6_professionalism(payload)
    return (not errors), errors
