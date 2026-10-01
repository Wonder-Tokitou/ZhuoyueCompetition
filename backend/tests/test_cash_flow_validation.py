import pytest
from backend.app.domain.validator import _v4_transmission


def cash_errors(text, label='提价策略'):
    payload={'base_metrics': {'revenue':60,'gross_margin':'62%','market_share':'20%'},
        'nodes':[{'options':[{'key':'C','label':label,'metrics':{'cash_flow':text}}]}]}
    return _v4_transmission(payload)


@pytest.mark.parametrize('text,label',[
    ('短期收紧，提价导致销量下降，现金流入减少，现金回笼放缓','提价策略'),
    ('现金流入减少','提价策略'),
    ('现金流出增加','提价策略'),
    ('现金流未改善，短期收紧','提价策略'),
    ('短期收紧，长期现金流改善','提价策略'),
    ('现金流出减少，现金流改善','成本控制'),
    ('现金流未恶化','成本控制'),
])
def test_compatible_or_uncertain_cash_text_is_not_rejected(text,label):
    assert cash_errors(text,label)==[]


@pytest.mark.parametrize('text,label',[
    ('现金流改善','提价策略'),
    ('现金流入增加','提价策略'),
    ('现金流出减少','提价策略'),
    ('现金流恶化','成本控制'),
    ('现金流入减少','成本控制'),
    ('现金流出增加','成本控制'),
])
def test_explicit_opposite_cash_direction_is_still_rejected(text,label):
    assert any('现金流表述' in e for e in cash_errors(text,label))
