from copy import deepcopy
import pytest
from sqlalchemy import select
from backend.app import models
from backend.app.domain import progressive
from backend.app.services.simulation import prepare, live_snapshot, published_snapshot
from backend.tests.test_student_workflow import env, begin, URL


def setup_case(factory):
    with factory() as db:
        case = db.scalar(select(models.Case))
        case.base_metrics_json = dict(revenue=100, gross_margin='60.0%', market_share='20.0%', cash_flow='原文字说明')
        case.financial_assumptions_json = dict(operating_cost=40, operating_expense=20)
        config = dict(enabled=True, cash_flow_amount=10, rules={
            f'{n.id}:{o.option_key}': dict(revenue_pct=10 if o.option_key == 'A' else 20,
                margin_pp=-2, expense_pct=5, cash_pct=-10, share_pp=0)
            for n in case.nodes for o in n.option_results})
        snap, result = prepare(case, config)
        db.add(models.SimulationConfig(case_id=case.id, draft_json=config, published_snapshot_json=snap))
        db.commit()
        return case.id, config, result


def test_chain_and_all_paths(env):
    client, factory = env
    _, _, preview = setup_case(factory)
    assert len(preview['paths']) == 27
    state = begin(client)
    assert state['financial_state']['cash_flow_amount'] == 10
    assert state['financial_state']['net_profit'] == 40
    expected = [110, 121, 133.1]
    for i, node in enumerate(state['play']['nodes']):
        response = client.post(URL + '/decide', json=dict(session_id=state['session_id'],
            student_name='student', node_id=node['id'], option_key='A', duration_ms=1))
        assert response.status_code == 200, response.text
        result = response.json()['result']
        assert result['source'] == 'progressive'
        assert result['after_metrics']['revenue'] == expected[i]
        assert result['financial_state']['net_profit'] == progressive.money(expected[i]-result['financial_state']['operating_cost']-result['financial_state']['operating_expense'])
    assert result['financial_state'] == preview['paths'][0]['final']
    restored = client.get(URL + f'/sessions/{state["session_id"]}').json()
    assert restored['financial_state'] == result['financial_state']
    assert restored['financial_state']['cash_flow_amount'] == 7.29


def test_snapshot_and_rollback(env):
    client, factory = env
    cid, config, _ = setup_case(factory)
    state = begin(client)
    with factory() as db:
        case = db.get(models.Case, cid)
        case.base_metrics_json = {**case.base_metrics_json, 'revenue': 200}
        row = db.get(models.SimulationConfig, cid)
        config['cash_flow_amount'] = 999
        row.draft_json = config
        db.commit()
        assert published_snapshot(case)['base_metrics']['revenue'] == 100
    n1 = state['play']['nodes'][0]
    payload = dict(session_id=state['session_id'], student_name='student', node_id=n1['id'], option_key='A', duration_ms=1)
    assert client.post(URL+'/decide', json=payload).json()['result']['after_metrics']['revenue'] == 110
    rolled = client.post(URL+f'/sessions/{state["session_id"]}/rollback/1').json()
    assert rolled['financial_state']['revenue'] == 100
    payload['option_key'] = 'B'
    assert client.post(URL+'/decide', json=payload).json()['result']['after_metrics']['revenue'] == 120


def test_old_session_keeps_preset(env):
    client, factory = env
    state = begin(client)
    with factory() as db:
        s = db.get(models.Session, state['session_id'])
        old = deepcopy(s.case_snapshot_json)
    setup_case(factory)
    node = old['nodes'][0]
    response = client.post(URL+'/decide', json=dict(session_id=state['session_id'], student_name='student', node_id=node['id'], option_key='A', duration_ms=1))
    assert response.status_code == 200, response.text
    assert response.json()['result']['source'] == 'preset'
    assert response.json()['result']['after_metrics'] == node['options'][0]['metrics']


def test_invalid_rules_and_negative_cash(env):
    _, factory = env
    cid, config, _ = setup_case(factory)
    with factory() as db:
        case = db.get(models.Case, cid)
        config['cash_flow_amount'] = -10
        for r in config['rules'].values(): r['cash_pct'] = 20
        _, result = prepare(case, config)
        assert result['paths'][0]['final']['cash_flow_amount'] == -17.28
        first = next(iter(config['rules']))
        config['rules'][first]['margin_pp'] = 90
        with pytest.raises(ValueError, match='路径'): prepare(case, config)
        del config['rules'][first]
        with pytest.raises(ValueError, match='9个'): prepare(case, config)


def test_zero_cash_nonfinite_and_cost_mismatch(env):
    _, factory = env
    cid, config, _ = setup_case(factory)
    with factory() as db:
        case = db.get(models.Case, cid)
        config['cash_flow_amount'] = 0
        assert prepare(case, config)[1]['paths'][0]['final']['cash_flow_amount'] == 0
        invalid = deepcopy(config)
        invalid['cash_flow_amount'] = float('inf')
        with pytest.raises(ValueError): prepare(case, invalid)
        case.financial_assumptions_json = dict(operating_cost=1, operating_expense=20)
        with pytest.raises(ValueError, match='不一致'): prepare(case, config)


def test_restart_and_ai_evidence_use_saved_results(env):
    client, factory = env
    cid, _, _ = setup_case(factory)
    state = begin(client)
    node = state['play']['nodes'][0]
    client.post(URL+'/decide', json=dict(session_id=state['session_id'], student_name='student',
        node_id=node['id'], option_key='A', duration_ms=1))
    # A fresh ORM session reads the same durable rules, not process state.
    with factory() as db:
        from backend.app.integrations.ai.evidence import evidence
        from backend.app.integrations.ai.tutor import execute_tools
        case = db.get(models.Case, cid)
        session = db.get(models.Session, state['session_id'])
        assert published_snapshot(case)['simulation']['version'] == 1
        data = evidence(case, session, session.turns)
        assert data['decisions'][0]['after']['net_profit'] == 42.8
        assert 'metrics' not in data['published_case']['nodes'][0]['options'][0]
        sources = execute_tools(db, session, ['read_rules', 'read_results'])
        assert sources[0]['content']['submitted_impacts'][0]['impact']['revenue_pct'] == 10
        assert sources[1]['content'][0]['after']['cash_flow_amount'] == 9


def test_teacher_endpoints_auth_and_publish_boundary(env):
    client, factory = env
    cid, config, _ = setup_case(factory)
    with factory() as db:
        case = db.get(models.Case, cid)
        from backend.app.routers.teacher import issue_teacher_token
        token = issue_teacher_token(db)
        case.status = 'published'
        db.commit()
    endpoint = f'/api/cases/{cid}/simulation'
    assert client.put(endpoint, json=config, params={'token':'invalid'}).status_code in (401,403,404)
    config['cash_flow_amount'] = 25
    response = client.put(endpoint, json=config, params={'token':token})
    assert response.status_code == 200, response.text
    with factory() as db:
        assert published_snapshot(db.get(models.Case,cid))['base_metrics']['cash_flow_amount'] == 10
    response = client.post(f'/api/cases/{cid}/publish', params={'token':token})
    assert response.status_code == 200, response.text
    with factory() as db:
        assert published_snapshot(db.get(models.Case,cid))['base_metrics']['cash_flow_amount'] == 25
