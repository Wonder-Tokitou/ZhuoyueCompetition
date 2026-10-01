"""确定性指标校验与差值；学生决策不包含模型调用或模型降级。"""
import json, re, math
from typing import Any, Dict, List, Tuple
from backend.app.domain import rules
from backend.app.observability import log

KEYS = ("revenue", "gross_margin", "market_share", "cash_flow")

def percentage(value: Any) -> float:
    """兼容明确的历史百分比及括号注释，不从区间或任意句子猜数。"""
    match = re.fullmatch(r'\s*(?:约为|约)?\s*(-?\d+(?:\.\d+)?)\s*[%％]\s*(?:（[^（）]*）|\([^()]*\))?\s*', str(value))
    if not match:
        raise ValueError('百分比需为明确数值，例如22.0%，不能是区间或未知值')
    return float(match[1])

def normalize_metrics(metrics: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(metrics)
    revenue = result['revenue']
    if isinstance(revenue, bool) or not isinstance(revenue, (int, float)) or not math.isfinite(revenue):
        raise ValueError('营收必须是有限数值')
    for key in ('gross_margin', 'market_share'):
        number = percentage(result[key])
        if not math.isfinite(number):
            raise ValueError('百分比必须是有限数值')
        result[key] = f'{number:.1f}%'
    return result

def _pct(v: Any) -> bool:
    return isinstance(v, str) and re.fullmatch(r"-?\d+(?:\.\d)?%", v) is not None

def validate_metrics(m: Any) -> Tuple[bool, str]:
    if not isinstance(m, dict) or not set(KEYS) <= set(m) or set(m) - set(KEYS) - {"operating_cost", "operating_expense", "net_profit", "cash_flow_amount"}: return False, "指标结构无效"
    for key in ("operating_cost", "operating_expense", "net_profit", "cash_flow_amount"):
        if key in m and (isinstance(m[key], bool) or not isinstance(m[key], (int, float)) or not math.isfinite(m[key])):
            return False, "财务金额必须为有限数值"
    if not isinstance(m["revenue"], (int, float)): return False, "revenue 必须为数值"
    if not _pct(m["gross_margin"]) or not _pct(m["market_share"]): return False, "百分比必须保留一位小数"
    if not isinstance(m["cash_flow"], str) or not m["cash_flow"].strip(): return False, "cash_flow 必须为非空字符串"
    return True, ""

def delta(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    def p(v): return percentage(v)
    return {"revenue": float(after["revenue"])-float(before["revenue"]),
            "gross_margin": p(after["gross_margin"])-p(before["gross_margin"]),
            "market_share": p(after["market_share"])-p(before["market_share"]),
            "cash_flow": {"from": str(before["cash_flow"]), "to": str(after["cash_flow"])} }
