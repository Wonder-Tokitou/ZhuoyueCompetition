"""Version 1 progressive simulation. Pure arithmetic, no model or database calls.

Amounts are 万元 per case accounting period. Rates are percent; margin/share
changes are percentage points. Cash is a signed period cash-flow amount, not
the cash balance. Negative cash × a positive growth rate becomes more negative.
"""
from decimal import Decimal, ROUND_HALF_UP
from itertools import product
import math
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from backend.app.domain.decision_engine import percentage


class Impact(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    revenue_pct: float = Field(ge=-100, le=1000)
    margin_pp: float = Field(ge=-100, le=100)
    expense_pct: float = Field(ge=-100, le=1000)
    cash_pct: float = Field(ge=-100, le=1000)
    share_pp: float = Field(ge=-100, le=100)


class ReferencePath(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(pattern=r'^[ABC]→[ABC]→[ABC]$')
    kind: Literal['recommended', 'historical'] = 'recommended'


class Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    enabled: bool = True
    cash_flow_amount: float
    rules: dict[str, Impact]
    reasoning: dict[str, dict] = Field(default_factory=dict)
    reference_path: ReferencePath | None = None


def money(value):
    if not math.isfinite(float(value)) or abs(float(value)) > 1e12:
        raise ValueError("财务金额超出可计算范围")
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def baseline(metrics, assumptions, cash):
    revenue = money(metrics["revenue"])
    margin = percentage(metrics["gross_margin"])
    share = percentage(metrics["market_share"])
    expense = money(assumptions["operating_expense"])
    cost = money(revenue * (1 - margin / 100))
    if revenue < 0 or expense < 0 or not 0 <= margin <= 100 or not 0 <= share <= 100:
        raise ValueError("基准营收/费用须非负，毛利率/市场份额须在0—100%内")
    if abs(cost - float(assumptions["operating_cost"])) > 0.02:
        raise ValueError("基准营业成本与营收×(1−毛利率)不一致，请先核对保存")
    return {**metrics, "revenue": revenue, "gross_margin": f"{margin:.1f}%",
            "market_share": f"{share:.1f}%", "operating_cost": cost,
            "operating_expense": expense, "net_profit": money(revenue-cost-expense),
            "cash_flow_amount": money(cash)}


def apply_impact(before, impact):
    rule = Impact.model_validate(impact)
    revenue = money(before["revenue"] * (1 + rule.revenue_pct / 100))
    margin = round(percentage(before["gross_margin"]) + rule.margin_pp, 1)
    share = round(percentage(before["market_share"]) + rule.share_pp, 1)
    if not 0 <= margin <= 100 or not 0 <= share <= 100:
        raise ValueError("递进结果的毛利率或市场份额超出0—100%，请调整影响规则")
    cost = money(revenue * (1 - margin / 100))
    expense = money(before["operating_expense"] * (1 + rule.expense_pct / 100))
    cash = money(before["cash_flow_amount"] * (1 + rule.cash_pct / 100))
    return {"revenue": revenue, "gross_margin": f"{margin:.1f}%",
            "market_share": f"{share:.1f}%", "operating_cost": cost,
            "operating_expense": expense, "net_profit": money(revenue-cost-expense),
            "cash_flow_amount": cash,
            "cash_flow": f"本期现金净流量 {cash:.2f} 万元"}


def preview(snapshot, configuration, assumptions):
    config = Configuration.model_validate(configuration)
    nodes = sorted(snapshot["nodes"], key=lambda n: n["idx"])
    if [n["idx"] for n in nodes] != [1, 2, 3] or any(
        {o["key"] for o in n["options"]} != {"A", "B", "C"} or len(n["options"]) != 3 for n in nodes
    ):
        raise ValueError("递进模式需要完整的3节点×3选项")
    expected = {f'{n["id"]}:{o["key"]}' for n in nodes for o in n["options"]}
    if set(config.rules) != expected:
        raise ValueError("影响规则需与当前9个选项一一对应；重新生成节点后请重新确认规则")
    initial = baseline(snapshot["base_metrics"], assumptions, config.cash_flow_amount)
    paths = []
    for choices in product("ABC", repeat=3):
        state = initial
        steps = []
        for node, key in zip(nodes, choices):
            try:
                state = apply_impact(state, config.rules[f'{node["id"]}:{key}'])
            except ValueError as exc:
                raise ValueError(f'路径 {"→".join(choices)} 第{node["idx"]}轮：{exc}') from exc
            steps.append(state)
        paths.append({"path": "→".join(choices), "steps": steps, "final": state})
    return {"baseline": initial, "paths": paths}
