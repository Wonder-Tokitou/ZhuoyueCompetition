import asyncio
import json

import httpx

from ai_component.providers import llm
from backend.app.integrations.ai import web_search


def mock_http(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs))


def test_low_reasoning_is_sent_to_deepseek_with_model_from_env(monkeypatch):
    for key, value in {'LLM_PROVIDER': 'openai_compatible', 'LLM_BASE_URL': 'https://api.deepseek.com',
        'LLM_MODEL': 'configurable-test-model', 'LLM_API_KEY': 'fake-key', 'LLM_READ_TIMEOUT': '120'}.items():
        monkeypatch.setenv(key, value)
    calls = []
    def provider(request):
        body = json.loads(request.content)
        calls.append(body)
        assert request.url == 'https://api.deepseek.com/chat/completions'
        assert request.headers['authorization'] == 'Bearer fake-key'
        assert body['model'] == 'configurable-test-model'
        assert body['thinking'] == {'type': 'enabled'}
        assert body['reasoning_effort'] == 'low'
        content = '{}' if len(calls) == 1 else '{"answer":"verified"}'
        return httpx.Response(200, json={'choices': [{'message': {'content': content}}]})
    mock_http(monkeypatch, provider)
    result = asyncio.run(llm.chat_json([{'role': 'user', 'content': '商业问题'}], {'answer': None}, retries=1, reasoning_effort='low'))
    assert result == {'answer': 'verified'} and len(calls) == 2


def test_default_read_timeout_matches_stage_deadline(monkeypatch):
    for key, value in {'LLM_PROVIDER': 'openai_compatible', 'LLM_BASE_URL': 'https://api.deepseek.com',
        'LLM_MODEL': 'configured-test-model', 'LLM_API_KEY': 'fake-key'}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv('LLM_READ_TIMEOUT', raising=False)
    captured = {}
    real = httpx.AsyncClient
    def capture_client(**kwargs):
        captured['timeout'] = kwargs['timeout']
        return real(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json={'choices': [{'message': {'content': '{"nodes": []}'}}]})), **kwargs)
    monkeypatch.setattr(httpx, 'AsyncClient', capture_client)
    assert asyncio.run(llm.chat_json([], {'nodes': None}, retries=0, stage='case_generation')) == {'nodes': []}
    assert captured['timeout'].read == 180


def test_search_budget_source_allowlist_and_private_key(monkeypatch):
    monkeypatch.setenv('TAVILY_API_KEY', 'fake-search-key')
    monkeypatch.setenv('TUTOR_SEARCH_DOMAINS', 'worldbank.org')
    calls = []
    def provider(request):
        body = json.loads(request.content)
        calls.append(request)
        assert request.url == 'https://api.tavily.com/search'
        assert request.headers['authorization'] == 'Bearer fake-search-key'
        assert body['max_results'] == 3 and body['search_depth'] == 'basic'
        assert body['include_domains'] == ['worldbank.org']
        assert body['include_raw_content'] is False
        return httpx.Response(200, json={'results': [
            {'url': 'https://www.worldbank.org/education', 'title': '商业资料', 'content': 'x' * 3000},
            {'url': 'http://127.0.0.1/private', 'content': 'no'},
            {'url': 'https://worldbank.org.attacker.example/x', 'content': 'no'},
            {'url': 'https://worldbank.org/too-many', 'content': 'no'},
        ]})
    mock_http(monkeypatch, provider)
    rows, notice = asyncio.run(web_search.search_business('现金流分析'))
    assert notice == '' and len(rows) == 1 and len(calls) == 1
    assert len(rows[0]['content']) == 1500 and 'fake-search-key' not in json.dumps(rows)


def test_search_missing_configuration_or_timeout_never_fabricates(monkeypatch):
    monkeypatch.setenv('TAVILY_API_KEY', '')
    rows, note = asyncio.run(web_search.search_business('现金流'))
    assert rows == [] and '未配置' in note
    monkeypatch.setenv('TAVILY_API_KEY', 'fake')
    def offline(request):
        raise httpx.ReadTimeout('offline', request=request)
    mock_http(monkeypatch, offline)
    rows, note = asyncio.run(web_search.search_business('现金流'))
    assert rows == [] and '暂不可用' in note
