"""Regression: five-store totals must not be forced into per-store benchmarks."""
import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from ai_component.agents import generator
from backend.app import models
from backend.app.domain import validator
from backend.app.integrations.ai import workflow, task_runner
from backend.app.integrations.ai.evidence import teaching_policy
from backend.app.routers import teacher
from backend.tests.test_student_workflow import env
from contracts.ai import NODE_ROLES


BASE = dict(revenue=60.0, gross_margin='62.0%', market_share='20.0%', cash_flow='经营现金流为正')


def candidate(revenue=60):
    return dict(title='研咖咖啡', background='五店合计月营收60万元，单店12万元',
        dilemma='经营增长与费用控制', base_metrics={**BASE, 'revenue': revenue},
        nodes=[dict(idx=i, node_role=role, title=role, background='保持企业整体月度口径',
            options=[dict(key=k, risk_level=r, label='成本控制', summary='营收保持稳定，毛利率提升',
                metrics={**BASE, 'revenue': revenue, 'gross_margin': '63.0%'},
                financial_assumptions=dict(operating_cost=22.2, operating_expense=22))
                for k,r in zip('ABC', ['保守','稳健','激进'])])
            for i,role in enumerate(NODE_ROLES,1)])


def test_confirmed_total_passes_without_changing_teacher_baseline(monkeypatch):
    model=AsyncMock(return_value=candidate())
    monkeypatch.setattr(generator,'generate_case',model)
    base=deepcopy(BASE)
    payload,errors=asyncio.run(workflow.generate_valid_case('五店，每店月营收12万元',
        'material','战略决策类',None,base_metrics=base))
    assert errors == []
    assert payload['base_metrics'] == BASE == base
    assert model.await_count == 1
    assert '同一经营范围' in model.call_args.kwargs['extra_hint']


def test_wrong_model_scope_is_repaired_not_silently_rebased(monkeypatch):
    model=AsyncMock(side_effect=[candidate(12),candidate(60)])
    monkeypatch.setattr(generator,'generate_case',model)
    payload,errors=asyncio.run(workflow.generate_valid_case('五店合计月营收60万元',
        'material','战略决策类',None,base_metrics=BASE))
    assert errors == [] and payload['base_metrics']['revenue'] == 60
    assert model.await_count == 2
    hint=model.call_args_list[1].kwargs['extra_hint']
    assert '教师已确认' in hint and '口径' in hint
    assert '未落在任何行业' not in hint


def test_confirmed_baseline_does_not_disable_transmission_checks(monkeypatch):
    data=candidate()
    data['nodes'][1]['options'][0]['metrics']['revenue']=12
    monkeypatch.setattr(generator,'generate_case',AsyncMock(side_effect=lambda *a,**k:deepcopy(data)))
    payload,errors=asyncio.run(workflow.generate_valid_case('五店合计60万元',
        'material','战略决策类',None,base_metrics=BASE))
    assert payload is None
    assert any('V4' in e and '-80.0%' in e for e in errors)
    assert not any('未落在任何行业' in e for e in errors)


def test_unconfirmed_defaults_still_use_industry_validation():
    assert validator._v3_industry_range({'base_metrics': BASE})


def test_prompt_does_not_force_confirmed_total_into_per_store_range():
    system = generator.build_messages('五家门店', 'material', '战略决策类', '',
        policy=teaching_policy())[0]['content']
    assert '若已提供教师基准，必须保持其经营范围和时间口径' in system
    assert '毛利率与营收必须落在上面给出的行业基准区间内' not in system


def test_model_cannot_disable_industry_validation_itself():
    data=candidate()
    data['case_type']='战略决策类'
    data['review']=workflow.review_placeholder('战略决策类')
    data['check_industry_range']=False
    ok,errors=validator.validate_case(data)
    assert not ok and any('未落在任何行业' in e for e in errors)


@pytest.mark.parametrize('value',[float('nan'),float('inf'),-1])
def test_confirmed_baseline_still_rejects_invalid_numbers(value):
    data=candidate(value)
    data['review']=workflow.review_placeholder('战略决策类')
    data['case_type']='战略决策类'
    ok,errors=validator.validate_case(data,check_industry_range=False)
    assert not ok and any('数值' in e for e in errors)


def test_save_generate_publish_confirmed_total(env,monkeypatch,tmp_path):
    client,factory=env
    with factory() as db:
        case=db.get(models.Case,1)
        for node in list(case.nodes): db.delete(node)
        case.status='ready'
        case.source_text='五家门店，每店月营收12万元'
        db.commit()
    scheduled=[]
    monkeypatch.setattr(teacher,'schedule_task',scheduled.append)
    monkeypatch.setattr(teacher,'QRCODE_DIR',tmp_path/'qr')
    model=AsyncMock(return_value=candidate())
    monkeypatch.setattr(generator,'generate_case',model)
    token='scope-regression'
    teacher.TEACHER_TOKENS.add(token)
    body=dict(base_metrics=BASE, financial_assumptions=dict(operating_cost=22.8,operating_expense=22))
    try:
        saved=client.patch('/api/cases/1',params={'token':token},json={**body,
            'background':'教师确认五店合计月营收60万元','dilemma':'教师确认的困境'})
        assert saved.status_code==200,saved.text
        assert not model.called
        response=client.post('/api/cases/1/generate-nodes',params={'token':token},json=body)
        assert response.status_code==200,response.text
        with factory() as db:
            task=db.query(models.AiTask).filter_by(task_id=scheduled[0]).one()
            asyncio.run(task_runner._run_case_nodes(db,task))
            case=db.get(models.Case,1)
            assert task.status=='succeeded'
            assert case.base_metrics_json==BASE
            assert case.background=='教师确认五店合计月营收60万元'
            assert case.financial_assumptions_json==body['financial_assumptions']
            hint=model.call_args.kwargs['extra_hint']
            assert '教师确认五店合计月营收60万元' in hint
            assert '教师确认的困境' in hint
        response=client.post('/api/cases/1/publish',params={'token':token})
        assert response.status_code==200,response.text
    finally:
        teacher.TEACHER_TOKENS.discard(token)
