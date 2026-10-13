import pytest
from conftest import MemoryRedis
from fastapi.testclient import TestClient

from app.main import app

FLAG = {
    'incident_id': 'test-crud',
    'kind': 'llm_invalid',
    'injected_at': '2026-10-08T00:00:00Z',
    'params': {},
}


def test_get_returns_null_when_flag_absent(redis: MemoryRedis) -> None:
    # Given: an empty Redis store.
    client = TestClient(app)
    # When: the flag is queried.
    response = client.get('/flags/classifier')
    # Then: no flag is returned.
    assert response.status_code == 200 and response.json() is None


def test_put_returns_flag_when_consumer_removes_it(redis: MemoryRedis) -> None:
    # Given: another worker consumes the flag immediately after storage.
    redis.consume_on_write = True
    # When: the flag is saved through the API.
    response = TestClient(app).put('/flags/classifier', json=FLAG)
    # Then: successful storage returns the written flag, even if already consumed.
    assert response.status_code == 200
    assert response.json()['incident_id'] == FLAG['incident_id']


def test_get_preserves_flag_when_present(redis: MemoryRedis) -> None:
    # Given: an existing Redis flag.
    import json

    redis.values['chaos:classifier'] = json.dumps(FLAG)
    # When: the flag is queried.
    response = TestClient(app).get('/flags/classifier')
    # Then: the read returns the flag without consuming it.
    assert response.json()['incident_id'] == FLAG['incident_id']
    assert 'chaos:classifier' in redis.values


def test_put_overwrites_existing_flag(redis: MemoryRedis) -> None:
    # Given: a prior flag.
    redis.values['chaos:classifier'] = '{}'
    # When: a new flag replaces it.
    response = TestClient(app).put('/flags/classifier', json=FLAG)
    # Then: the response represents the new flag.
    assert response.status_code == 200
    assert response.json()['incident_id'] == FLAG['incident_id']


def test_delete_returns_null_when_flag_present(redis: MemoryRedis) -> None:
    # Given: a stored flag.
    redis.values['chaos:classifier'] = '{}'
    # When: the flag is cleared.
    response = TestClient(app).delete('/flags/classifier')
    # Then: the existing API contract returns null.
    assert response.status_code == 200 and response.json() is None


def test_delete_returns_null_when_flag_absent(redis: MemoryRedis) -> None:
    # Given: no stored flag.
    # When: clearing an absent flag.
    response = TestClient(app).delete('/flags/classifier')
    # Then: clearing is idempotent.
    assert response.status_code == 200 and response.json() is None


@pytest.mark.parametrize('field,value', [('kind', 'service_down'), ('injected_at', '2026-10-08T00:00:00'), ('extra', 'invalid')])
def test_put_rejects_invalid_body(redis: MemoryRedis, field: str, value: str) -> None:
    # Given: a body outside the existing flag contract.
    body = {**FLAG, field: value}
    # When: the body is submitted.
    response = TestClient(app).put('/flags/classifier', json=body)
    # Then: framework validation rejects it before storage.
    assert response.status_code == 422
    assert redis.values == {}


@pytest.mark.parametrize('target', ['backend', 'classifier', 'locator', 'verifier', 'writer', 'mailer'])
def test_put_uses_target_specific_key(redis: MemoryRedis, target: str) -> None:
    # Given: an empty store and a valid target.
    # When: the API stores a flag for that target.
    response = TestClient(app).put(f'/flags/{target}', json=FLAG)
    # Then: only that target's key is written.
    assert response.status_code == 200
    assert set(redis.values) == {f'chaos:{target}'}


@pytest.mark.parametrize('raw', ['', '{}', 'invalid', '{"kind":"unknown"}'])
def test_corrupt_flag_returns_safe_500(redis: MemoryRedis, raw: str) -> None:
    redis.values['chaos:classifier'] = raw
    response = TestClient(app).get('/flags/classifier')
    assert response.status_code == 500
    assert response.json() == {'detail': 'Stored chaos flag is invalid'}
    assert redis.values['chaos:classifier'] == raw


@pytest.mark.parametrize('method,path', [('get', '/flags/classifier'), ('put', '/flags/classifier'), ('delete', '/flags/classifier')])
def test_redis_failure_returns_safe_503(monkeypatch: pytest.MonkeyPatch, method: str, path: str) -> None:
    from redis.exceptions import ConnectionError

    from app.services import flags as service

    def unavailable():
        raise ConnectionError('test secret')

    monkeypatch.setattr(service, 'redis_client', unavailable)
    response = TestClient(app).request(method, path, json=FLAG if method == 'put' else None)
    assert response.status_code == 503
    assert response.json() == {'detail': 'Redis unavailable'}


def test_missing_redis_configuration_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import flags as service

    monkeypatch.setattr(service, 'REDIS_URL', '')
    assert TestClient(app).get('/flags/classifier').status_code == 503


@pytest.mark.parametrize('mode', ['normal', 'no_socket', 'stop_error', 'overlap', 'redteam'])
def test_mail_down_controls_docker_and_cleans_up(redis, monkeypatch, mode):
    import asyncio

    import httpx

    from app.clients import mailpit, redteam
    from app.schemas.redteam import Selection
    calls = []
    original = httpx.AsyncClient
    monkeypatch.setattr(mailpit.Path, 'is_socket', lambda _: mode != 'no_socket')
    monkeypatch.setattr(mailpit, 'tasks', {object()} if mode == 'overlap' else set())
    def respond(request):
        action = request.url.path.rsplit('/', 1)[-1]
        calls.append(action)
        if action == 'json':
            return httpx.Response(200, json={'State': {'Running': True}})
        if action == 'stop':
            assert 'chaos:mailer' in redis.values
            if mode == 'stop_error':
                raise httpx.ConnectError('Docker unavailable', request=request)
        return httpx.Response(204)
    monkeypatch.setattr(mailpit.httpx, 'AsyncClient', lambda **kwargs: original(
        **{**kwargs, 'transport': httpx.MockTransport(respond)}))
    async def choose(body):
        return Selection(target='mailer', kind='mail_down', reason='SMTP recovery')
    monkeypatch.setattr(redteam, 'choose', choose)
    body = {**FLAG, 'kind': 'mail_down', 'params': {'seconds': 60}}
    with TestClient(app) as client:
        response = (client.post('/run', json={'candidates': [{'target': 'mailer', 'kind': 'mail_down'}]})
                    if mode == 'redteam' else client.put('/flags/mailer', json=body))
        expected = 503 if mode in ('no_socket', 'stop_error') else 409 if mode == 'overlap' else 200
        assert response.status_code == expected
        if expected != 200:
            assert not redis.values
        else:
            assert calls == ['json', 'stop']
            assert 'chaos:mailer' in redis.values
            # 백그라운드 태스크를 시작한 뒤 종료 복구도 확인한다.
            client.portal.call(asyncio.sleep, 0)
        if mode == 'overlap':
            mailpit.tasks.clear()
    if mode in ('normal', 'redteam', 'stop_error'):
        assert calls[-1] == 'start'


@pytest.mark.parametrize('seconds,expected', [(None, 20), (100, 60), (0.01, 0.01)])
def test_mail_down_duration_and_timer(monkeypatch, seconds, expected):
    import asyncio

    import httpx

    from app.clients import mailpit
    from app.clients.chaos import ChaosFlag
    monkeypatch.setattr(mailpit.Path, 'is_socket', lambda _: True)
    actions = []
    async def command(action):
        actions.append(action)
        return httpx.Response(200, json={'State': {'Running': True}})
    monkeypatch.setattr(mailpit, 'command', command)
    async def scenario():
        flag = ChaosFlag.model_validate({**FLAG, 'kind': 'mail_down',
            'params': {} if seconds is None else {'seconds': seconds}})
        assert await mailpit.prepare(flag) == expected
        if seconds == 0.01:
            await mailpit.stop(seconds)
            await asyncio.gather(*mailpit.tasks)
            assert actions == ['json', 'stop', 'start']
    asyncio.run(scenario())
