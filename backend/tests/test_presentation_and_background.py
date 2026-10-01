import asyncio
import json
import time
from unittest.mock import AsyncMock

from backend.app import models
from ai_component.agents import tutor_agent
from backend.tests.test_student_workflow import env
from backend.tests.test_student_workflow import begin
from backend.tests.test_student_workflow import complete
from backend.tests.test_student_workflow import URL
from backend.tests.teacher_helpers import login_teacher


def test_tutor_answer_and_history_are_readable_but_artifact_keeps_evidence(env, monkeypatch):
    client, factory = env
    sid = complete(client, begin(client))
    monkeypatch.setattr(tutor_agent, 'chat_json', AsyncMock(side_effect=[
        {'business': True, 'tools': ['read_results'], 'search_query': ''},
        {'answer': '请比较决策前后营收。[read_results]', 'source_ids': ['read_results']},
    ]))
    response = client.post(URL + '/chat', json={'session_id': sid, 'message': '分析实际结果'})
    text = ''.join(json.loads(line[6:]).get('delta', '') for line in response.text.splitlines() if line.startswith('data: {'))
    assert '营收' in text and '万元' in text and '教师版本' in text
    assert '"before"' not in text and '"revenue"' not in text
    assert 'read_results' not in text and '【已提交结果】' in text
    with factory() as db:
        from sqlalchemy import select
        from backend.app.integrations.ai.artifacts import artifact_store
        artifact = db.scalar(select(models.AiArtifact).where(models.AiArtifact.kind == 'tutor_answer'))
        assert artifact_store.read_json(artifact)['sources'][0]['content'][0]['before']['revenue'] is not None
        assert '[read_results]' in artifact_store.read_json(artifact)['answer']['answer']
        db.add(models.Message(session_id=sid, role='assistant', content='旧回答\n\n依据：\n[read_case] 案例（教师版本 1）\n{"base_metrics":{"revenue":12,"gross_margin":"58.0%","cash_flow":"改善"}}'))
        db.commit()
    history = client.get(URL + f'/sessions/{sid}/messages').json()[-1]['content']
    assert '营收' in history and '12 万元' in history and '"base_metrics"' not in history
    assert 'read_case' not in history and '【案例资料】' in history


def test_internal_citation_labels_are_readable_without_losing_external_reference():
    from contracts.presentation import readable_tutor_history
    text = r'结果[read\_result]；节点[read_node]；规则[read_rules]；来源[web_2] https://example.com [普通文字]'
    displayed = readable_tutor_history(text)
    assert displayed == '结果【已提交结果】；节点【当前节点】；规则【教学规则】；来源【外部资料 2】 https://example.com [普通文字]'


def test_teacher_can_rediscover_task_after_losing_page_state(env, monkeypatch):
    client, factory = env
    token = login_teacher(client, monkeypatch)
    with factory() as db:
        db.add_all([
            models.AiTask(task_id='persistent-job', case_id=1, kind='case_generation', status='running', stage='generate_case'),
            models.AiTask(task_id='persistent-node-job', case_id=1, kind='case_nodes', status='running', stage='generate_nodes'),
        ])
        db.commit()
    url = '/api/cases/1/ai-tasks'
    assert client.get(url, params={'token': 'invalid'}).status_code == 403
    response = client.get(url, params={'token': token})
    assert response.status_code == 200, response.text
    assert [item['task_id'] for item in response.json()] == ['persistent-node-job', 'persistent-job']
    assert response.json()[0]['stage'] == 'generate_nodes'


def test_generate_nodes_schedules_worker_from_request_thread(env, monkeypatch):
    from datetime import datetime
    from backend.app.integrations.ai import task_runner
    client, factory = env
    token = login_teacher(client, monkeypatch)
    with factory() as db:
        case = models.Case(
            title='后台节点测试', source_text='测试材料', status='ready',
            teacher_token='generate-nodes-teacher', student_token='generate-nodes-student',
            case_type='市场营销类', source_kind='material', background='背景', dilemma='困境',
            base_metrics_json={'revenue': 100, 'gross_margin': '40%',
                'market_share': '10%', 'cash_flow': '稳定'},
        )
        db.add(case)
        db.commit()
        case_id = case.id

    async def complete_without_provider(db, task):
        task.status = 'succeeded'
        task.stage = 'completed'
        task.finished_at = datetime.now()
        task.case.status = 'ready'
        db.commit()

    monkeypatch.setattr(task_runner, '_run_case_nodes', complete_without_provider)
    response = client.post(f'/api/cases/{case_id}/generate-nodes', params={'token': token}, json={
        'base_metrics': {'revenue': 100, 'gross_margin': '40%',
            'market_share': '10%', 'cash_flow': '稳定'},
        'financial_assumptions': {'operating_cost': 50, 'operating_expense': 20},
        'idempotency_key': 'request-thread-regression',
    })
    assert response.status_code == 200, response.text
    task_id = response.json()['task_id']
    for _ in range(50):
        with factory() as db:
            task = db.query(models.AiTask).filter_by(task_id=task_id).one()
            status = task.status
        if status in ('succeeded', 'failed'):
            break
        time.sleep(0.01)
    assert status == 'succeeded'
    with factory() as db:
        assert db.get(models.Case, case_id).status == 'ready'


def test_accepted_generation_continues_after_request_client_closes(env, monkeypatch):
    import httpx
    from sqlalchemy import select
    from backend.app.main import app
    from backend.app.integrations.ai import task_runner
    from backend.app.integrations.ai import workflow
    _, factory = env
    token = login_teacher(env[0], monkeypatch)

    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        async def generate_baseline(*args):
            started.set()
            await release.wait()
            return {'title': '后台案例', 'background': '已生成', 'dilemma': '教学困境',
                'nodes': [], 'base_metrics': {'revenue': 100, 'gross_margin': '30%',
                    'market_share': '5%', 'cash_flow': '稳定'},
                'financial_assumptions': {'operating_cost': 50, 'operating_expense': 20}}, []
        monkeypatch.setattr(workflow, 'generate_valid_baseline', generate_baseline)
        job = None
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as browser:
                response = await browser.post('/api/cases', data={'title': '后台测试', 'text': '校园咖啡店', 'case_type': '市场营销类'})
                assert response.status_code == 200, response.text
                created = response.json()
                job = task_runner._running[created['task_id']]
                await asyncio.wait_for(started.wait(), timeout=2)
            # Simulate leaving/closing the page: no request is alive and no polling occurs.
            assert not job.done()
            release.set()
            await asyncio.wait_for(job, timeout=2)
            with factory() as db:
                task = db.scalar(select(models.AiTask).where(models.AiTask.task_id == created['task_id']))
                assert task.status == 'succeeded' and task.case.status == 'ready'
            # A fresh client can rediscover the same completed job after navigation.
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as browser:
                tasks = await browser.get(f"/api/cases/{created['case_id']}/ai-tasks", params={'token': token})
                assert tasks.json()[0]['task_id'] == created['task_id']
                assert tasks.json()[0]['status'] == 'succeeded'
        finally:
            release.set()
            if job and not job.done():
                job.cancel()
                await asyncio.gather(job, return_exceptions=True)

    asyncio.run(scenario())
