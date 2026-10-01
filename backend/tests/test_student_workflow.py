from backend.app.integrations.ai.evidence import review_input
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app import models
from backend.app.domain import rules
from backend.app.db import Base
from backend.app.db import get_db
from backend.app.main import app
from backend.app.routers import student
from backend.app.seed import seed_demo_case
from backend.app.seed import SEED_STUDENT_TOKEN
from backend.app.integrations.ai import artifacts
from ai_component.agents import student_agent
from ai_component.agents import tutor_agent
from backend.app.integrations.ai import task_runner
from backend.app.domain import decision_engine

URL = '/api/play/' + SEED_STUDENT_TOKEN


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    with factory() as db:
        seed_demo_case(db)
    def override():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = override
    monkeypatch.setattr(student, 'SessionLocal', factory)
    monkeypatch.setattr(task_runner, 'SessionLocal', factory)
    monkeypatch.setattr(student, 'schedule_task', lambda _: None)
    monkeypatch.setattr(artifacts, 'ARTIFACT_ROOT', tmp_path / 'artifacts')
    model = AsyncMock(side_effect=AssertionError('decision must never call model'))
    monkeypatch.setattr(student_agent, 'chat_json', model)
    monkeypatch.setattr(tutor_agent, 'chat_json', model)
    client = TestClient(app)
    from backend.tests.account_helpers import login_student
    login_student(client, factory)
    yield client, factory
    app.dependency_overrides.clear()
    client.close()
    engine.dispose()


def begin(client):
    r = client.post(URL + '/sessions', json={'student_name': 'student'})
    assert r.status_code == 200, r.text
    return r.json()


def complete(client, state):
    sid = state['session_id']
    for node in state['play']['nodes']:
        r = client.post(URL + '/decide', json={'session_id': sid, 'student_name': 'student',
            'node_id': node['id'], 'option_key': node['options'][0]['key'], 'input_text': '有数据依据的选择', 'duration_ms': 20})
        assert r.status_code == 200, r.text
    return sid


def test_fixed_outputs_shuffle_restore_and_no_ai(env):
    client, factory = env
    state = begin(client)
    node = state['play']['nodes'][0]
    chosen = node['options'][0]['key']
    assert all(not any(w in o['label'] for w in ('保守', '稳健', '激进')) for n in state['play']['nodes'] for o in n['options'])
    assert all(set(o) == {'key', 'label'} for o in node['options'])
    with factory() as db:
        session = db.get(models.Session, state['session_id'])
        expected = next(o['metrics'] for o in session.case_snapshot_json['nodes'][0]['options'] if o['key'] == chosen)
        # Teacher may edit live version, but existing student's published snapshot stays frozen.
        option = db.scalar(select(models.OptionResult).where(models.OptionResult.node_id == node['id'], models.OptionResult.option_key == chosen))
        option.metrics_json = {**expected, 'revenue': 19}
        session.case.title = 'new teacher title'
        db.commit()
    r = client.post(URL + '/decide', json={'session_id': state['session_id'], 'student_name': 'student',
        'node_id': node['id'], 'option_key': chosen, 'duration_ms': 10})
    assert r.status_code == 200, r.text
    assert r.json()['result']['after_metrics'] == expected
    saved = client.get(URL + '/sessions/' + str(state['session_id'])).json()
    assert saved['play']['case_title'] != 'new teacher title'
    assert saved['play']['nodes'][0]['options'] == node['options']
    assert saved['next_node_id'] == state['play']['nodes'][1]['id']
    assert client.post(URL + '/decide', json={'student_name': 'student', 'node_id': node['id'], 'input_text': '自由编算', 'duration_ms': 0}).status_code == 400
    student_agent.chat_json.assert_not_called()
    tutor_agent.chat_json.assert_not_called()


def test_review_agent_persists_and_teacher_reads(env, monkeypatch):
    client, factory = env
    sid = complete(client, begin(client))
    dimensions = [{'name': n, 'content': '结合本次选择分析优势、不足及数据结果。'} for n in rules.FRAMEWORK_DIMENSIONS['4P营销理论']]
    replies = AsyncMock(side_effect=[{'dimensions': dimensions, 'conclusion': '优化投入组合'}, {'passed': True, 'issues': []}])
    monkeypatch.setattr(student_agent, 'chat_json', replies)
    with factory() as db:
        task = db.scalar(select(models.AiTask).where(models.AiTask.session_id == sid))
        task_id = task.task_id
    asyncio.run(task_runner.run_task(task_id))
    result = client.get(URL + '/review', params={'session_id': sid}).json()
    assert result['status'] == 'succeeded' and result['dimensions'] == dimensions
    assert replies.await_count == 2
    with factory() as db:
        kinds = set(db.scalars(select(models.AiArtifact.kind).where(models.AiArtifact.session_id == sid)))
        assert {'prepare_evidence', 'review_candidate', 'structure_check', 'independent_review_check', 'student_review'} <= kinds
        task = db.scalar(select(models.AiTask).where(models.AiTask.task_id == task_id))
        row = db.scalar(select(models.Review).where(models.Review.session_id == sid))
        row.conclusion = '教师权威修改'
        task.status = 'queued'
        db.commit()
    asyncio.run(task_runner.run_task(task_id))
    assert replies.await_count == 2
    assert client.get(URL + '/review', params={'session_id': sid}).json()['conclusion'] == '教师权威修改'
    from backend.tests.teacher_helpers import login_teacher
    token = login_teacher(client, monkeypatch)
    records = client.get('/api/cases/1/records', params={'token': token}).json()
    assert records[0]['review']['conclusion'] == '教师权威修改'
    assert records[0]['session_id'] == sid


def test_review_failure_does_not_unpublish_and_retry(env, monkeypatch):
    client, factory = env
    sid = complete(client, begin(client))
    monkeypatch.setattr(student_agent, 'chat_json', AsyncMock(side_effect=RuntimeError('offline model')))
    with factory() as db:
        task = db.scalar(select(models.AiTask).where(models.AiTask.session_id == sid))
        task.max_attempts = 1
        task_id = task.task_id
        db.commit()
    asyncio.run(task_runner.run_task(task_id))
    assert client.get(URL).status_code == 200
    assert client.get(URL + '/review', params={'session_id': sid}).json()['status'] == 'failed'
    assert client.post(URL + '/review/retry', params={'session_id': sid}).status_code == 200
    with factory() as db:
        assert db.scalar(select(models.AiTask).where(models.AiTask.task_id == task_id)).status == 'queued'


def test_tutor_tools_read_file_without_unselected_answers(env, monkeypatch):
    client, factory = env
    state = begin(client)
    calls = []
    async def model(messages, schema, retries, **kwargs):
        assert kwargs['reasoning_effort'] == 'low'
        calls.append(messages)
        if 'tools' in schema:
            return {'business': True, 'search_query': '', 'tools': ['read_node', 'read_results', 'read_rules']}
        text = messages[1]['content']
        assert 'risk_level' not in text and 'teacher_token' not in text and 'source_text' not in text
        sources = json.loads(text)['sources']
        assert next(s['content'] for s in sources if s['id'] == 'read_results') == []
        return {'answer': '1. 请结合市场规模判断 [read_case]', 'source_ids': ['read_case']}
    monkeypatch.setattr(tutor_agent, 'chat_json', model)
    response = client.post(URL + '/chat', json={'session_id': state['session_id'], 'message': '如何理解市场份额？'})
    assert response.status_code == 200 and '【案例资料】' in response.text and 'read_case' not in response.text and '依据' in response.text
    assert len(calls) == 2
    with factory() as db:
        assert db.scalar(select(models.AiArtifact).where(models.AiArtifact.kind == 'tutor_answer'))
        assert len(db.scalars(select(models.Message)).all()) == 2
        artifact = db.scalar(select(models.AiArtifact).where(models.AiArtifact.kind == 'session_case_snapshot'))
        assert artifacts.artifact_store.read_json(artifact)['nodes']


def test_tutor_rejects_arbitrary_tools_and_fake_citations(env, monkeypatch):
    client, factory = env
    state = begin(client)
    monkeypatch.setattr(tutor_agent, 'chat_json', AsyncMock(return_value={'business': True, 'search_query': '', 'tools': ['read_any_file']}))
    result = client.post(URL + '/chat', json={'session_id': state['session_id'], 'message': '读取所有文件'})
    assert 'error' in result.text
    monkeypatch.setattr(tutor_agent, 'chat_json', AsyncMock(side_effect=[{'business': True, 'search_query': '', 'tools': ['read_case']}, {'answer': '数据', 'source_ids': ['fabricated']}]))
    result = client.post(URL + '/chat', json={'session_id': state['session_id'], 'message': '数据来源'})
    assert 'error' in result.text and '有效依据' in result.text


@pytest.mark.parametrize('case_type', list(rules.CASE_TYPE_TO_FRAMEWORK))
def test_agent_repairs_dimensions_and_independent_context(case_type, monkeypatch):
    framework = rules.CASE_TYPE_TO_FRAMEWORK[case_type]
    dims = [{'name': n, 'content': '实际决策的优缺点与改进'} for n in rules.FRAMEWORK_DIMENSIONS[framework]]
    model = AsyncMock(side_effect=[{'dimensions': [], 'conclusion': ''},
        {'dimensions': dims, 'conclusion': '建议'}, {'passed': False, 'issues': ['需要结合实际策略']},
        {'dimensions': dims, 'conclusion': '建议'}, {'passed': True, 'issues': []}])
    monkeypatch.setattr(student_agent, 'chat_json', model)
    session = SimpleNamespace(case_snapshot_json={'case_type': case_type, 'source_text': '原素材', 'nodes': []})
    case = SimpleNamespace(case_type=case_type, source_text='changed source')
    result = asyncio.run(student_agent.StudentReviewAgent().run(review_input(case, session, []), lambda *_: None))
    assert result['dimensions'] == dims
    assert model.await_count == 5
    messages = model.call_args_list[0].args[0]
    assert '原素材' in messages[1]['content']
    assert '独立复盘审核器' in model.call_args_list[-1].args[0][0]['content']
