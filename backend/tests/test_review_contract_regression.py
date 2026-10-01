from backend.app.integrations.ai.evidence import review_input
"""Reproduce the saved SWOT response shape without copying student data."""
import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.app.domain import rules
from ai_component.agents import student_agent
from ai_component.providers.llm import ValidationExhausted
from backend.tests.test_student_workflow import env
from backend.tests.test_student_workflow import begin
from backend.tests.test_student_workflow import complete
from backend.tests.test_student_workflow import URL


@pytest.mark.parametrize('case_type', list(rules.CASE_TYPE_TO_FRAMEWORK))
@pytest.mark.parametrize('shape', ['core', 'reordered', 'extra', 'legacy', 'short_names'])
def test_dynamic_dimensions_are_preserved_before_independent_review(case_type, shape, monkeypatch):
    framework = rules.CASE_TYPE_TO_FRAMEWORK[case_type]
    expected = rules.FRAMEWORK_DIMENSIONS[framework]
    candidate = {'dimensions': [{'name': n, 'content': '基于已提交策略分析优缺点与改进。'} for n in expected[:-1]],
                 'conclusion': '根据本次结果控制投入，分阶段验证。'}
    if shape == 'reordered':
        candidate['dimensions'].reverse()
    elif shape == 'extra':
        candidate['dimensions'].extend([{'name': '执行建议', 'content': '分阶段投入。'}, {'name': '风险提示', 'content': '关注现金流。'}])
    elif shape == 'legacy':
        candidate['dimensions'].append({'name': '综合结论', 'content': candidate['conclusion']})
    elif shape == 'short_names':
        for d in candidate['dimensions']:
            d['name'] = d['name'].split('（')[0]
    original = copy.deepcopy(candidate)
    async def reply(messages, schema, **kwargs):
        if 'passed' in schema:
            checked = json.loads(messages[1]['content'])['candidate']
            assert checked == original
            return {'passed': True, 'issues': []}
        return copy.deepcopy(candidate)
    model = AsyncMock(side_effect=reply)
    monkeypatch.setattr(student_agent, 'chat_json', model)
    records = []
    session = SimpleNamespace(case_snapshot_json={'case_type': case_type, 'nodes': []})
    case = SimpleNamespace(case_type=case_type, source_text='教学素材')
    result = asyncio.run(student_agent.StudentReviewAgent().run(review_input(case, session, []), lambda *args: records.append(args)))
    assert model.await_count == 2
    assert result['dimensions'] == original['dimensions']
    assert next(data['candidate'] for stage, data in records if stage == 'review_candidate') == original


@pytest.mark.parametrize('kind', ['duplicate', 'missing', 'blank', 'blank_name', 'blank_conclusion', 'alias_duplicate'])
def test_real_dimension_errors_are_not_silently_filled(kind, monkeypatch):
    expected = rules.FRAMEWORK_DIMENSIONS['SWOT分析模型']
    dims = [{'name': n, 'content': '实际选择分析'} for n in expected[:-1]]
    if kind == 'duplicate':
        dims.append(copy.deepcopy(dims[-1]))
    elif kind == 'missing':
        dims.pop(1)
    elif kind == 'blank_name':
        dims.append({'name': '', 'content': '没有标题'})
    elif kind == 'alias_duplicate':
        dims.append({'name': '优势', 'content': '重复维度'})
    elif kind == 'blank':
        dims[0]['content'] = ''
    model = AsyncMock(return_value={'dimensions': dims, 'conclusion': '' if kind == 'blank_conclusion' else '改进建议'})
    monkeypatch.setattr(student_agent, 'chat_json', model)
    case = SimpleNamespace(case_type='战略决策类', source_text='素材')
    session = SimpleNamespace(case_snapshot_json={'nodes': []})
    with pytest.raises(ValidationExhausted):
        asyncio.run(student_agent.StudentReviewAgent().run(review_input(case, session, []), lambda *_: None))
    assert model.await_count == 3


def test_failed_review_retry_persists_dynamic_result_for_student_and_teacher(env, monkeypatch):
    from sqlalchemy import select
    from backend.app import models
    from backend.app.routers import teacher
    from backend.app.integrations.ai import task_runner
    from backend.tests.teacher_helpers import login_teacher
    client, factory = env
    sid = complete(client, begin(client))
    expected = rules.FRAMEWORK_DIMENSIONS['4P营销理论']
    with factory() as db:
        task = db.scalar(select(models.AiTask).where(models.AiTask.session_id == sid))
        task.status, task.error = 'failed', '维度缺失、混用或顺序错误'
        task_id = task.task_id
        db.commit()
    assert client.post(URL + '/review/retry', params={'session_id': sid}).status_code == 200
    candidate = {'dimensions': [{'name': n, 'content': '具体决策分析'} for n in expected[:-1]], 'conclusion': '减少无效投入'}
    model = AsyncMock(side_effect=[candidate, {'passed': True, 'issues': []}])
    monkeypatch.setattr(student_agent, 'chat_json', model)
    asyncio.run(task_runner.run_task(task_id))
    result = client.get(URL + '/review', params={'session_id': sid}).json()
    assert result['status'] == 'succeeded'
    assert result['dimensions'] == candidate['dimensions']
    token = login_teacher(client, monkeypatch)
    records = client.get('/api/cases/1/records', params={'token': token}).json()
    assert records[0]['review']['conclusion'] == '减少无效投入'
    assert records[0]['review']['dimensions'] == candidate['dimensions']
    updated = list(reversed(candidate['dimensions'])) + [{'name': '补充建议', 'content': '教师建议'}]
    url = '/api/reviews/' + str(records[0]['review_id'])
    patched = client.patch(url, params={'token': token}, json={'dimensions': updated})
    assert patched.status_code == 200, patched.text
    assert client.get(URL + '/review', params={'session_id': sid}).json()['dimensions'] == updated
    assert client.patch(url, params={'token': token}, json={'dimensions': updated[1:]}).status_code == 400
    assert client.patch(url, params={'token': token}, json={'conclusion': '  '}).status_code == 400
    # An AI task replay must never overwrite the teacher's dynamic report.
    with factory() as db:
        db.scalar(select(models.AiTask).where(models.AiTask.task_id == task_id)).status = 'queued'
        db.commit()
    asyncio.run(task_runner.run_task(task_id))
    assert model.await_count == 2
    assert client.get(URL + '/review', params={'session_id': sid}).json()['dimensions'] == updated


def test_duplicate_dimension_produces_actionable_repair_then_accepts_separate_conclusion(monkeypatch):
    expected = rules.FRAMEWORK_DIMENSIONS['SWOT分析模型']
    dims = [{'name': n, 'content': '具体决策分析'} for n in expected[:-1]]
    model = AsyncMock(side_effect=[
        {'dimensions': dims + [dims[-1]], 'conclusion': '改进建议'},
        {'dimensions': dims, 'conclusion': '改进建议'},
        {'passed': True, 'issues': []},
    ])
    monkeypatch.setattr(student_agent, 'chat_json', model)
    case = SimpleNamespace(case_type='战略决策类', source_text='素材')
    session = SimpleNamespace(case_snapshot_json={'nodes': []})
    result = asyncio.run(student_agent.StudentReviewAgent().run(review_input(case, session, []), lambda *_: None))
    repair = json.loads(model.call_args_list[1].args[0][1]['content'])['repair_issues'][0]
    assert '重复' in repair and '威胁' in repair
    assert result['dimensions'] == dims


def test_teacher_publication_is_visible_without_manually_entering_case_token(env, tmp_path, monkeypatch):
    from backend.app import models
    from backend.app.routers import teacher
    from backend.tests.teacher_helpers import login_teacher
    client, factory = env
    with factory() as db:
        case = db.get(models.Case, 1)
        case.status = 'ready'
        db.commit()
    assert client.get('/api/student/cases').json() == []
    monkeypatch.setattr(teacher, 'QRCODE_DIR', tmp_path / 'qr')
    token = login_teacher(client, monkeypatch)
    response = client.post('/api/cases/1/publish', params={'token': token})
    assert response.status_code == 200, response.text
    case = client.get('/api/student/cases').json()[0]
    assert 'teacher_token' not in case and 'source_text' not in case
    state = client.post('/api/play/' + case['student_token'] + '/sessions', json={'student_name': '同学'})
    assert state.status_code == 200 and len(state.json()['play']['nodes']) == 3
