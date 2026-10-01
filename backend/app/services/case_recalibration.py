"""Deterministically rebase existing decision outcomes after teacher edits.

No model/provider imports belong here. Numeric option outcomes retain their
previous delta/ratio relative to the teacher-confirmed baseline. Qualitative
cash-flow descriptions are preserved and explicitly flagged for teacher review.
"""
from __future__ import annotations

import math
import re
from typing import Any

from backend.app import models
from backend.app.domain.financials import net_profit


_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} 必须是有限数值")
    return float(value)


def _percentage(value: Any, label: str) -> tuple[float, int, bool]:
    if not isinstance(value, str):
        raise ValueError(f"{label} 需要使用百分比文本，无法安全传导")
    match = _NUMBER.search(value)
    if not match:
        raise ValueError(f"{label} 缺少可计算的百分比数字，请先校准为百分比格式")
    precision = len(match.group(0).partition(".")[2])
    return float(match.group(0)), precision, "%" in value


def _write_percentage(original: str, value: float, precision: int, has_percent: bool) -> str:
    bounded = min(100.0, max(0.0, value))
    text = f"{bounded:.{precision}f}"
    return text + ("%" if has_percent else "")


def _carry_cost(old_baseline: float, new_baseline: float, option_value: float) -> float:
    if old_baseline == 0:
        return round(new_baseline + option_value, 2)
    return round(new_baseline * option_value / old_baseline, 2)


def recalculate_option_results(
    nodes: list[models.Node],
    old_metrics: dict[str, Any],
    new_metrics: dict[str, Any],
    old_assumptions: dict[str, Any],
    new_assumptions: dict[str, Any],
) -> int:
    """Apply a deterministic rebase while leaving option text/risk untouched.

    All calculations are staged and validated before mutating ORM objects, so a
    malformed historical value cannot leave a partially recalculated case.
    """
    old_revenue = _number(old_metrics.get("revenue"), "原基准营收")
    new_revenue = _number(new_metrics.get("revenue"), "新基准营收")
    old_cost = _number(old_assumptions.get("operating_cost"), "原基准营业成本")
    old_expense = _number(old_assumptions.get("operating_expense"), "原基准运营费用")
    new_cost = _number(new_assumptions.get("operating_cost"), "新基准营业成本")
    new_expense = _number(new_assumptions.get("operating_expense"), "新基准运营费用")

    old_percentages = {
        key: _percentage(old_metrics.get(key), f"原基准{label}")
        for key, label in (("gross_margin", "毛利率"), ("market_share", "市场份额"))
    }
    new_percentages = {
        key: _percentage(new_metrics.get(key), f"新基准{label}")
        for key, label in (("gross_margin", "毛利率"), ("market_share", "市场份额"))
    }

    staged = []
    for node in nodes:
        for option in node.option_results:
            metrics = dict(option.metrics_json or {})
            option_revenue = _number(metrics.get("revenue"), f"节点{node.idx}选项{option.option_key}营收")
            next_revenue = max(0.0, round(new_revenue + option_revenue - old_revenue, 2))
            next_metrics = dict(metrics, revenue=next_revenue)
            for key, label in (("gross_margin", "毛利率"), ("market_share", "市场份额")):
                old_base_value, _, _ = old_percentages[key]
                new_base_value, _, _ = new_percentages[key]
                option_value, precision, has_percent = _percentage(metrics.get(key), f"节点{node.idx}选项{option.option_key}{label}")
                next_metrics[key] = _write_percentage(metrics[key], new_base_value + option_value - old_base_value,
                                                       precision, has_percent)

            basis = dict(option.financial_basis_json or {})
            assumptions = dict(basis.get("assumptions") or {})
            option_cost = _number(assumptions.get("operating_cost", old_cost),
                                  f"节点{node.idx}选项{option.option_key}营业成本")
            option_expense = _number(assumptions.get("operating_expense", old_expense),
                                     f"节点{node.idx}选项{option.option_key}运营费用")
            next_assumptions = {
                "operating_cost": _carry_cost(old_cost, new_cost, option_cost),
                "operating_expense": _carry_cost(old_expense, new_expense, option_expense),
            }
            next_profit = round(net_profit(next_revenue, next_assumptions["operating_cost"],
                                           next_assumptions["operating_expense"]), 2)
            previous_indicators = dict(basis.get("indicators") or {})
            next_indicators = dict(previous_indicators)
            for key, label in (("revenue", "营收"), ("gross_margin", "毛利率"),
                               ("market_share", "市场份额")):
                indicator = dict(next_indicators.get(key) or {})
                indicator.update({"label": label, "before": new_metrics.get(key),
                                  "after": next_metrics[key],
                                  "formula": "保留原选项相对基准的差值，叠加教师新基准",
                                  "method": "教师修改基准后由确定性脚本传导；选项策略、风险等级与文字不变"})
                next_indicators[key] = indicator
            cash = dict(next_indicators.get("cash_flow") or {})
            cash.update({"label": "现金流", "before": new_metrics.get("cash_flow"),
                         "after": metrics.get("cash_flow"),
                         "method": "现金流为定性文本，不进行算术改写；保留原选项内容，请教师核对"})
            next_indicators["cash_flow"] = cash
            next_indicators["net_profit"] = {
                "label": "净利润", "before": round(new_revenue - new_cost - new_expense, 2),
                "after": next_profit, "formula": "营收－营业成本－运营费用",
                "inputs": {"revenue": next_revenue, **next_assumptions},
                "method": "按新基准同比传导选项成本/费用假设后，由确定性脚本计算",
            }
            basis.update({"source": "教师基准修改后的确定性重算", "assumptions": next_assumptions,
                          "indicators": next_indicators})
            staged.append((option, next_metrics, next_profit, basis))

    for option, metrics, profit, basis in staged:
        option.metrics_json = metrics
        option.net_profit = profit
        option.financial_basis_json = basis
    return len(staged)
