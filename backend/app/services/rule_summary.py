"""Deterministic teacher summaries; never invent a unique later-round outcome."""
import re
from backend.app.domain.progressive import apply_impact, baseline

MARKER = '【递进指标同步】'

def summary_for_rule(original, impact, initial=None):
    # Replace old generated block, not append another copy on each save.
    prose = (original or '').split(MARKER)[0].split('较上一轮：')[0].strip()
    # Legacy AI summaries mix preset metrics into prose. Retain only complete
    # qualitative sentences without metric claims which could contradict rules.
    sentences = re.split(r'(?<=[。！？])', prose)
    prose = ''.join(s for s in sentences if not re.search(
        r'(营收|收入|毛利率|份额|现金流|现金净流|净利润|营业成本|运营费用).*(?:\d|上升|下降|增加|减少|改善|持平|维持|流出|流入|升至|降至)', s)).strip()
    def change(value, unit):
        return ('增加' if value > 0 else '减少' if value < 0 else '不变') + (f'{abs(value):g}{unit}' if value else '')
    text = '较上一轮：' + '；'.join(f'{label}{change(impact[key], unit)}' for key,label,unit in [
        ('revenue_pct','营收','%'),('margin_pp','毛利率','个百分点'),
        ('expense_pct','运营费用','%'),('cash_pct','现金净流量','%'),('share_pp','市场份额','个百分点')]) + '。'
    if initial is not None:
        after = apply_impact(initial, impact)
        text += f'按当前基准计算：营收{after["revenue"]:.2f}万元，毛利率{after["gross_margin"]}，市场份额{after["market_share"]}，运营费用{after["operating_expense"]:.2f}万元，净利润{after["net_profit"]:.2f}万元，现金净流量{after["cash_flow_amount"]:.2f}万元。'
    else:
        text += '具体结果随前序选择而变化。'
    return (prose + '\n\n' if prose else '') + text

def sync_rule_summaries(case, config):
    initial = baseline(case.base_metrics_json, case.financial_assumptions_json, config['cash_flow_amount'])
    summaries = {}
    for node in case.nodes:
        for option in node.option_results:
            key = f'{node.id}:{option.option_key}'
            option.summary = summary_for_rule(option.summary, config['rules'][key], initial if node.idx == 1 else None)
            summaries[key] = option.summary
    return summaries
