"""确定性财务计算工具。

模型只能提出原始经营数据或假设；所有派生指标通过这里计算，避免把算术
交给模型。输入字段缺失时不猜值，只返回可审计的 errors。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def gross_margin(revenue: float, operating_cost: float) -> float:
    if revenue == 0:
        raise ValueError("revenue 不能为 0")
    return (revenue - operating_cost) / revenue


def net_profit(revenue: float, operating_cost: float, operating_expense: float) -> float:
    return revenue - operating_cost - operating_expense


def market_share(company_revenue: float, target_market_size: float) -> float:
    if target_market_size == 0:
        raise ValueError("target_market_size 不能为 0")
    return company_revenue / target_market_size


def calculate(values: Dict[str, Any]) -> Dict[str, Any]:
    """根据存在的原始字段计算派生字段；不完整输入不会静默补数。"""
    result = dict(values)
    errors: List[str] = []
    try:
        if "revenue" in values and "operating_cost" in values:
            result["gross_margin_ratio"] = gross_margin(float(values["revenue"]), float(values["operating_cost"]))
        if all(key in values for key in ("revenue", "operating_cost", "operating_expense")):
            result["net_profit"] = net_profit(float(values["revenue"]), float(values["operating_cost"]), float(values["operating_expense"]))
        if "company_revenue" in values and "target_market_size" in values:
            result["market_share_ratio"] = market_share(float(values["company_revenue"]), float(values["target_market_size"]))
    except (TypeError, ValueError) as exc:
        errors.append(str(exc))
    result["errors"] = errors
    return result
