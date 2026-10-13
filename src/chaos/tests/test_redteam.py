import json

import httpx
import pytest
from conftest import MemoryRedis
from fastapi.testclient import TestClient

from app.core import config
from app.main import app

REQUEST = {'candidates': [{'target': 'classifier', 'kind': 'llm_invalid'}]}
SELECTION = {'target': 'classifier', 'kind': 'llm_invalid', 'reason': 'schema check'}


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    calls: list[httpx.Request] = []
    original = httpx.AsyncClient

    def reply(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={
            'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(SELECTION)}}],
        })

    transport = httpx.MockTransport(reply)
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=transport, **kwargs))
    monkeypatch.setattr(config, 'OPENAI_API_KEY', 'test-provider-key', raising=False)
    return calls


def test_redteam_stores_only_validated_selection(provider: list[httpx.Request], redis: MemoryRedis) -> None:
    # Given: a provider returns one allowed candidate.
    # When: the request is sent to the public route.
    response = TestClient(app).post('/run', json=REQUEST)
    # Then: selection is returned and a generated one-shot flag is stored.
    assert response.status_code == 200
    assert {k: response.json()[k] for k in SELECTION} == SELECTION
    stored = json.loads(redis.values['chaos:classifier'])
    assert stored['kind'] == 'llm_invalid' and stored['incident_id']
    assert stored['injected_at'].endswith('Z') and stored['params'] == {}
    assert len(provider) == 1
    assert response.json()['incident_id'] == stored['incident_id']
    assert response.json()['injected_at'] == stored['injected_at']


@pytest.mark.parametrize('body', [{'candidates': [{'target': 'verifier', 'kind': 'pii_leak'}]}, {'candidates': []}, {'candidates': [{'target': 'frontend', 'kind': 'delay'}]}, {'candidates': [{'target': 'mailer', 'kind': 'llm_invalid'}]}])
def test_redteam_rejects_invalid_candidates_without_model_call(provider: list[httpx.Request], body: dict[str, object]) -> None:
    # Given: invalid or unsupported candidate input.
    # When: the public route receives it.
    response = TestClient(app).post('/run', json=body)
    # Then: it fails at the input boundary without model calls.
    assert response.status_code == 422
    assert provider == []


@pytest.mark.parametrize('case,status', [
    ('deadline', 504), ('http', 502), ('connection', 502), ('timeout', 504), ('json', 502),
    ('truncated', 502), ('empty', 502), ('unlisted', 502), ('unsupported', 502),
    ('blank', 502), ('extra', 502), ('refusal', 502),
])
def test_model_failure_never_stores_flag(monkeypatch: pytest.MonkeyPatch, redis: MemoryRedis, case: str, status: int) -> None:
    original = httpx.AsyncClient
    calls: list[httpx.Request] = []

    def reply(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if case == 'deadline':
            raise TimeoutError('test secret')
        if case == 'timeout':
            raise httpx.ReadTimeout('test secret', request=request)
        if case == 'connection':
            raise httpx.ConnectError('test secret', request=request)
        if case == 'http':
            return httpx.Response(401, text='test secret')
        if case == 'json':
            return httpx.Response(200, text='invalid json test secret')
        selection = dict(SELECTION)
        if case == 'unlisted':
            selection['target'] = 'writer'
        if case == 'unsupported':
            selection['target'] = 'mailer'
        if case == 'blank':
            selection['reason'] = '   '
        if case == 'extra':
            selection['command'] = 'test secret'
        choice = {'finish_reason': 'length' if case == 'truncated' else 'stop', 'message': {'content': None if case == 'refusal' else json.dumps(selection)}}
        return httpx.Response(200, json={'choices': [] if case == 'empty' else [choice]})

    monkeypatch.setattr(config, 'OPENAI_API_KEY', 'test-provider-key')
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(reply), **kwargs))
    response = TestClient(app).post('/run', json=REQUEST)
    assert response.status_code == status
    assert 'test secret' not in response.text
    assert redis.values == {} and len(calls) == 1


def test_missing_key_returns_503_without_provider_call(provider: list[httpx.Request], redis: MemoryRedis, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, 'OPENAI_API_KEY', '')
    response = TestClient(app).post('/run', json=REQUEST)
    assert response.status_code == 503
    assert provider == [] and redis.values == {}


def test_provider_receives_strict_schema_and_only_candidates(provider: list[httpx.Request], redis: MemoryRedis) -> None:
    assert TestClient(app).post('/run', json=REQUEST).status_code == 200
    body = json.loads(provider[0].content)
    assert json.loads(body['messages'][1]['content']) == REQUEST
    schema = body['response_format']['json_schema']
    assert schema['strict'] is True and schema['schema']['additionalProperties'] is False
    assert set(schema['schema']['required']) == {'target', 'kind', 'reason'}


def test_model_success_but_redis_failure_returns_503(provider: list[httpx.Request], monkeypatch: pytest.MonkeyPatch) -> None:
    from redis.exceptions import ConnectionError

    from app.services import flags

    def unavailable():
        raise ConnectionError('test secret')

    monkeypatch.setattr(flags, 'redis_client', unavailable)
    response = TestClient(app).post('/run', json=REQUEST)
    assert response.status_code == 503 and len(provider) == 1
    assert response.json() == {'detail': 'Redis unavailable'}


def test_empty_request_uses_effective_defaults_and_records_receipt(provider, redis):
    response = TestClient(app).post('/run', json={})
    assert response.status_code == 200
    sent = json.loads(json.loads(provider[0].content)['messages'][1]['content'])['candidates']
    assert len(sent) == 11 and all(c['target'] != 'backend' for c in sent)
    assert {'target': 'mailer', 'kind': 'delay'} not in sent
    assert {'target': 'writer', 'kind': 'pii_leak'} in sent
    assert {'target': 'verifier', 'kind': 'pii_leak'} not in sent
    assert response.json()['incident_id'] == json.loads(redis.values['chaos:classifier'])['incident_id']


def test_redteam_delay_matches_lab_timeout(monkeypatch, redis):
    from unittest.mock import AsyncMock

    from app.clients import redteam
    from app.schemas.redteam import Selection
    monkeypatch.setattr(redteam, 'choose', AsyncMock(return_value=Selection(target='classifier', kind='delay', reason='timeout')))
    assert TestClient(app).post('/run', json={}).status_code == 200
    assert json.loads(redis.values['chaos:classifier'])['params'] == {'seconds': 20}
