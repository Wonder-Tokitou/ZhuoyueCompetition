from backend.app.integrations.ai.evidence import teaching_policy
import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from ai_component.providers import llm
from ai_component.agents import generator
from backend.app.integrations.ai import workflow
from ai_component.agents import student_agent
from backend.app.domain import rules
from backend.tests.test_student_workflow import env
from backend.tests.test_student_workflow import begin
from backend.tests.test_student_workflow import complete
from backend.tests.test_external_adapters import mock_http


def config(monkeypatch):
    for key, value in {'LLM_BASE_URL': 'https://api.deepseek.com', 'LLM_API_KEY': 'fake-test', 'LLM_MODEL': 'configured-model', 'LLM_PROVIDER': 'openai_compatible'}.items():
        monkeypatch.setenv(key, value)


def test_stage_budget_and_low_default_are_explicit(monkeypatch):
    config(monkeypatch)
    def provider(request):
        body = json.loads(request.content)
        assert body['reasoning_effort'] == 'low'
        assert body['max_tokens'] >= 8000  # Whole case must fit, never truncate to speed up.
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': '{"nodes":[]}'}}]})
    mock_http(monkeypatch, provider)
    assert asyncio.run(llm.chat_json([], {'nodes': None}, retries=0, stage='case_generation')) == {'nodes': []}


def test_case_baseline_workflow_uses_a_supported_model_stage(monkeypatch):
    config(monkeypatch)
    result = {'title': '咖啡案例', 'background': '企业背景', 'dilemma': '经营决策问题',
        'base_metrics': {'revenue': 100, 'gross_margin': '30%', 'market_share': '5%', 'cash_flow': '稳定'},
        'financial_assumptions': {'operating_cost': 50, 'operating_expense': 20}}
    first_result = {**result, 'base_metrics': {'revenue': 100}}
    calls = []
    def provider(request):
        body = json.loads(request.content)
        assert body['max_tokens'] == 8192
        calls.append(body)
        system_prompt = body['messages'][0]['content']
        assert '"gross_margin":"百分比字符串"' in system_prompt
        assert '"market_share":"百分比字符串"' in system_prompt
        reply = first_result if len(calls) == 1 else result
        if len(calls) == 2:
            assert '缺少/为空：gross_margin, market_share, cash_flow' in body['messages'][1]['content']
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
            'message': {'content': json.dumps(reply, ensure_ascii=False)}}]})
    mock_http(monkeypatch, provider)
    case_type = next(iter(rules.CASE_TYPE_TO_FRAMEWORK))
    payload, errors = asyncio.run(workflow.generate_valid_baseline(
        '案例素材', 'material', case_type, None))
    assert errors == []
    assert payload['title'] == '咖啡案例'
    assert len(calls) == 2


def test_unknown_model_stage_is_written_to_runtime_log(monkeypatch, caplog):
    config(monkeypatch)
    with caplog.at_level('ERROR', logger='case_sim'):
        with pytest.raises(llm.LLMFailed, match='未知模型任务阶段：typo'):
            asyncio.run(llm.chat_json([], {}, retries=0, stage='typo'))
    assert 'stage=typo' in caplog.text
    assert '未发出外部请求' in caplog.text


def test_truncated_valid_json_is_not_accepted(monkeypatch):
    config(monkeypatch)
    mock_http(monkeypatch, lambda request: httpx.Response(200, json={'choices': [{'finish_reason': 'length', 'message': {'content': '{"answer":"partial"}'}}]}))
    with pytest.raises(llm.LLMFailed, match='截断'):
        asyncio.run(llm.chat_json([], {'answer': None}, retries=0))


def test_auth_failure_is_not_retried(monkeypatch):
    config(monkeypatch)
    calls = []
    def provider(request):
        calls.append(request)
        return httpx.Response(401, json={'error': {'message': 'invalid key'}})
    mock_http(monkeypatch, provider)
    with pytest.raises(llm.LLMFailed):
        asyncio.run(llm.chat_json([], {}, retries=2))
    assert len(calls) == 1


def test_case_repair_has_previous_candidate_and_only_selected_framework(monkeypatch):
    case_type = list(rules.CASE_TYPE_TO_FRAMEWORK)[0]
    candidate = {'title': '必须保留的标题', 'nodes': []}
    model = AsyncMock(side_effect=[candidate.copy(), candidate.copy()])
    monkeypatch.setattr(generator, 'generate_case', model)
    checks = iter([(False, ['V2: 缺少节点']), (True, [])])
    monkeypatch.setattr(workflow.validator, 'validate_case', lambda _: next(checks))
    asyncio.run(workflow.generate_valid_case('素材', 'material', case_type, None))
    hint = model.call_args_list[1].kwargs['extra_hint']
    assert '必须保留的标题' in hint and 'V2: 缺少节点' in hint
    prompt = generator.build_messages('素材', 'material', case_type, '', policy=teaching_policy())[0]['content']
    for other in rules.CASE_TYPE_TO_FRAMEWORK:
        if other != case_type:
            assert other not in prompt


@pytest.mark.parametrize('stage', list(llm.PROFILES))
def test_every_stage_has_bounded_output_and_overridable_effort(stage, monkeypatch):
    config(monkeypatch)
    monkeypatch.setenv('LLM_' + stage.upper() + '_REASONING_EFFORT', 'high')
    def provider(request):
        body = json.loads(request.content)
        assert 512 <= body['max_tokens'] <= 32768
        assert body['reasoning_effort'] == 'high'
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': '{}'}}]})
    mock_http(monkeypatch, provider)
    asyncio.run(llm.chat_json([], {}, retries=0, stage=stage))


def test_review_repair_exhaustion_fails_once_without_worker_restart(env, monkeypatch):
    from sqlalchemy import select
    from backend.app import models
    from backend.app.integrations.ai import task_runner
    client, factory = env
    sid = complete(client, begin(client))
    model = AsyncMock(return_value={'dimensions': [], 'conclusion': ''})
    monkeypatch.setattr(student_agent, 'chat_json', model)
    with factory() as db:
        task_id = db.scalar(select(models.AiTask.task_id).where(models.AiTask.session_id == sid))
    asyncio.run(task_runner.run_task(task_id))
    assert model.await_count == 3  # initial + two targeted repairs, no whole-workflow retry
    assert json.loads(model.call_args_list[1].args[0][1]['content'])['previous_candidate'] == {'dimensions': [], 'conclusion': ''}
    with factory() as db:
        task = db.scalar(select(models.AiTask).where(models.AiTask.task_id == task_id))
        assert task.status == 'failed' and task.attempts == 1


def test_network_retry_keeps_prompt_and_never_adds_false_json_errors(monkeypatch):
    config(monkeypatch)
    bodies = []
    def provider(request):
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            raise httpx.ReadTimeout('test transient timeout')
        return httpx.Response(200, json={'choices': [{'message': {'content': '{}'}}]})
    mock_http(monkeypatch, provider)
    asyncio.run(llm.chat_json([{'role': 'user', 'content': 'JSON任务'}], {}, retries=1))
    assert bodies[0]['messages'] == bodies[1]['messages']


def test_total_deadline_cancels_a_provider_that_never_finishes(monkeypatch):
    config(monkeypatch)
    setting = llm._bounded_setting
    monkeypatch.setattr(llm, '_bounded_setting', lambda name, *args: 0.01 if name == 'LLM_TUTOR_PLAN_TIMEOUT' else setting(name, *args))
    cancelled = []
    async def provider(request):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)
    mock_http(monkeypatch, provider)
    with pytest.raises(llm.LLMFailed, match='TimeoutError'):
        asyncio.run(llm.chat_json([], {}, retries=0, stage='tutor_plan'))
    assert cancelled == [True]
