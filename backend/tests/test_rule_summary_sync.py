from types import SimpleNamespace as NS
from backend.app.services.rule_summary import summary_for_rule, sync_rule_summaries, MARKER

RULE = dict(revenue_pct=10, margin_pp=1, expense_pct=-5, cash_pct=20, share_pp=0)
BASE = dict(revenue=100, gross_margin='20%', market_share='10%', operating_expense=10, cash_flow_amount=-10)

def test_first_round_and_legacy_replacement():
    text = summary_for_rule('营收升至60000000万元，毛利率15.2%。有助于扩大用户基础。', RULE, BASE)
    assert '60000000' not in text and '15.2%' not in text
    assert '有助于扩大用户基础。' in text
    assert '营收110.00万元' in text and '毛利率21.0%' in text
    assert '现金净流量-12.00万元' in text
    assert summary_for_rule(text, RULE, BASE) == text

def test_later_round_and_changed_rules():
    old = summary_for_rule('有助于扩大用户基础。', RULE)
    text = summary_for_rule(old, {**RULE, 'revenue_pct': -3})
    assert MARKER not in text and text.count('较上一轮：') == 1 and '营收减少3%' in text
    assert '增加10%' not in text and '前序选择' in text
    assert '按当前基准计算' not in text

def test_all_options_persist_and_no_other_fields_change():
    nodes = [NS(id=i,idx=i,option_results=[NS(option_key=k,summary='旧营收100万元。策略说明。',label='原策略') for k in 'ABC']) for i in range(1,4)]
    case = NS(nodes=nodes,base_metrics_json=BASE,financial_assumptions_json={'operating_cost':80,'operating_expense':10})
    config={'cash_flow_amount':-10,'rules':{f'{i}:{k}':RULE for i in range(1,4) for k in 'ABC'}}
    saved=sync_rule_summaries(case,config)
    assert len(saved)==9
    for node in nodes:
        for option in node.option_results:
            assert option.summary==saved[f'{node.id}:{option.option_key}']
            assert option.label=='原策略'
