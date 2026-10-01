import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select
from backend.app import models
from backend.app.integrations.ai import impact_rules, task_runner
from backend.tests.test_student_workflow import env
from backend.tests.test_progressive import setup_case
from contracts.errors import LLMFailed


def test_agent_stage_registered_and_protocol(monkeypatch):
    import httpx
    from ai_component.agents.impact_rules import generate_impact_rules
    from ai_component.providers import llm
    assert 'impact_rules' in llm.PROFILES
    for key, value in {'LLM_API_KEY':'test-only', 'LLM_BASE_URL':'https://api.deepseek.com', 'LLM_MODEL':'deepseek-chat'}.items():
        monkeypatch.setenv(key,value)
    real_client = httpx.AsyncClient
    def handler(request):
        import json
        payload = json.loads(request.content)
        assert payload['model'] == 'deepseek-chat'
        assert request.url.path == '/chat/completions'
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'{"rules":{},"reasoning":{}}'}}]})
    monkeypatch.setattr(llm.httpx,'AsyncClient',lambda **kwargs:real_client(transport=httpx.MockTransport(handler),**kwargs))
    assert asyncio.run(generate_impact_rules({'test':'evidence'})) == {'rules':{},'reasoning':{}}


def ready(factory):
    cid, config, _ = setup_case(factory)
    with factory() as db:
        db.delete(db.get(models.SimulationConfig, cid))
        db.commit()
    reply = {"rules": config['rules'], "reasoning": {k: {
        "basis": "依据渠道扩张规则，增长幅度为教学假设", "is_assumption": True,
        "caveat": "需教师核对，现金金额尚未提供"} for k in config['rules']}}
    return cid, reply


def task(factory, cid, retry=False):
    with factory() as db:
        return impact_rules.ensure_task(db, db.get(models.Case, cid), retry).task_id


def test_draft_persisted_idempotent_no_teacher_write(env, monkeypatch):
    _, factory = env
    cid, reply = ready(factory)
    model = AsyncMock(return_value=reply)
    monkeypatch.setattr(impact_rules, 'generate_impact_rules', model)
    tid = task(factory, cid)
    assert task(factory, cid) == tid
    asyncio.run(task_runner.run_task(tid))
    with factory() as db:
        row = db.scalar(select(models.AiTask).where(models.AiTask.task_id == tid))
        assert row.status == 'succeeded'
        assert row.output_json['validated_paths'] == 27
        assert row.output_json['cash_flow_amount'] is None
        assert db.get(models.SimulationConfig, cid) is None
        assert row.artifacts
    assert task(factory, cid) == tid
    assert model.await_count == 1


def test_manual_saved_during_generation_wins(env, monkeypatch):
    _, factory = env
    cid, reply = ready(factory)
    async def model(*args):
        with factory() as db:
            db.add(models.SimulationConfig(case_id=cid, draft_json={'human': 'authoritative'}))
            db.commit()
        return reply
    monkeypatch.setattr(impact_rules, 'generate_impact_rules', model)
    tid = task(factory,cid)
    asyncio.run(task_runner.run_task(tid))
    with factory() as db:
        assert db.get(models.SimulationConfig, cid).draft_json == {'human':'authoritative'}
        assert impact_rules.ensure_task(db, db.get(models.Case,cid)) is None
        assert db.scalar(select(models.AiTask).where(models.AiTask.task_id==tid)).status == 'failed'


def test_changed_case_and_validation_errors(env, monkeypatch):
    _, factory = env
    cid, reply = ready(factory)
    with factory() as db:
        data = impact_rules.context(db.get(models.Case,cid))
    broken = deepcopy(reply)
    broken['rules'].pop(next(iter(broken['rules'])))
    with pytest.raises(ValueError): impact_rules.validate_candidate(data, broken)
    broken = deepcopy(reply)
    broken['reasoning'] = {}
    with pytest.raises(ValueError): impact_rules.validate_candidate(data, broken)
    broken = deepcopy(reply)
    next(iter(broken['rules'].values()))['margin_pp'] = 100
    with pytest.raises(ValueError): impact_rules.validate_candidate(data, broken)
    async def model(*args):
        with factory() as db:
            case = db.get(models.Case,cid)
            case.title = 'teacher revised'
            db.commit()
        return reply
    monkeypatch.setattr(impact_rules,'generate_impact_rules',model)
    tid = task(factory,cid)
    asyncio.run(task_runner.run_task(tid))
    assert task(factory,cid) != tid
    with factory() as db:
        assert db.scalar(select(models.AiTask).where(models.AiTask.task_id==tid)).status == 'failed'


def test_retry_and_repair(env, monkeypatch):
    _, factory = env
    cid, reply = ready(factory)
    monkeypatch.setattr(impact_rules,'generate_impact_rules',AsyncMock(side_effect=LLMFailed('timeout', retryable=False)))
    tid = task(factory,cid)
    asyncio.run(task_runner.run_task(tid))
    assert task(factory,cid,True) == tid
    model = AsyncMock(side_effect=[{'rules': {}}, reply])
    monkeypatch.setattr(impact_rules,'generate_impact_rules',model)
    asyncio.run(task_runner.run_task(tid))
    with factory() as db:
        assert db.scalar(select(models.AiTask).where(models.AiTask.task_id==tid)).status == 'succeeded'
    assert model.await_count == 2


def test_endpoint_auth_and_existing_saved(env, monkeypatch):
    client, factory = env
    cid, reply = ready(factory)
    from backend.app.routers import teacher
    monkeypatch.setattr(teacher, 'schedule_task', lambda _: None)
    with factory() as db:
        token = teacher.issue_teacher_token(db)
    url = f'/api/cases/{cid}/simulation/suggest'
    assert client.post(url, params={'token':'bad'}).status_code == 403
    a = client.post(url,params={'token':token}).json()['task']
    b = client.post(url,params={'token':token}).json()['task']
    assert a['task_id'] == b['task_id']
    with factory() as db:
        db.add(models.SimulationConfig(case_id=cid, draft_json={'rules':reply['rules']}))
        db.commit()
    assert client.post(url,params={'token':token}).json()['protected']
