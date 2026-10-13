"""실제 HTTP 경계와 장애 통로를 검사하며 외부 네트워크는 사용하지 않는다."""
import asyncio
import importlib
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

from app.clients import llm
from app.core import config
from app.main import app
from app.schemas.contract import Handoff
from app.services.recovery import recover

AGENT = importlib.import_module(
    "app.services.classification" if config.SERVICE == "classifier" else "app.services.drafting"
)


def payload():
    return json.loads((config.CONTRACTS / f"samples/{config.SERVICE}_input.json").read_text())


class FakeRedis:
    def __init__(self, kind=None, broken=False, params=None):
        self.flag = json.dumps({
            "incident_id": "incident-test", "kind": kind,
            "injected_at": "2026-10-08T00:00:00Z", "params": params or {},
        }) if kind else None
        self.broken = broken
        self.closed = False
        self.reads = 0
        self.writes = []

    async def getdel(self, key):
        assert key == f"chaos:{config.SERVICE}"
        self.reads += 1
        if self.broken:
            raise RedisConnectionError("offline")
        flag, self.flag = self.flag, None
        return flag

    async def aclose(self):
        self.closed = True

    async def set(self, key, value, **kwargs):
        self.writes.append((key, json.loads(value), kwargs))


@pytest.mark.parametrize("kind", ["llm_invalid", None])
@pytest.mark.parametrize("model_enabled", [False, True])
@pytest.mark.parametrize("obeys", [False, True])
def test_chaos_is_consumed_once_and_result_recovers(monkeypatch, kind, model_enabled, obeys):
    redis = FakeRedis(kind)
    monkeypatch.setattr(config, "REDIS_URL", "redis://test")
    monkeypatch.setattr(AGENT.Redis, "from_url", lambda *args, **kwargs: redis)
    calls = []
    original_client = httpx.AsyncClient
    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        broken = kind and len(calls) == 1
        assert ("text" not in body) == bool(broken)
        assert ("한국어 산문" in body["instructions"]) == bool(broken)
        data = json.loads((config.CONTRACTS / f"samples/{config.SERVICE}_output.json").read_text())["data"]
        return httpx.Response(200, json={"status": "completed", "output": [{
            "type": "message", "content": [{"type": "output_text", "text":
                "가로등 고장 신고를 도와드릴게요." if broken and obeys else json.dumps(data)}],
        }]})
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(respond), **kwargs))
    if model_enabled:
        monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    result = TestClient(app).post("/run", json=payload()).json()
    failed = bool(kind and model_enabled and obeys)
    assert result["status"] == "success" and result["failure"] is None
    assert result["incident_id"] == ("incident-test" if failed else None)
    assert ('recovery' in result['data']) == failed
    if failed:
        trace = result['data']['recovery']
        assert trace['attempts'][0]['model'] == config.OPENAI_MODEL
        assert trace['attempts'][0]['error_type'] == 'JSONDecodeError'
        assert trace['recovered_by'] == config.OPENAI_FALLBACK_MODEL
    assert redis.flag is None and redis.closed and redis.reads == 1
    assert [call["model"] for call in calls] == (
        [config.OPENAI_MODEL, config.OPENAI_FALLBACK_MODEL] if failed else
        [config.OPENAI_MODEL] if model_enabled else [])


def test_service_delay_exceeds_model_budget_and_retries_after_cancellation(monkeypatch):
    redis = FakeRedis("delay", params={"seconds": 0.2})
    monkeypatch.setattr(config, "REDIS_URL", "redis://test")
    monkeypatch.setattr(AGENT.Redis, "from_url", lambda *args, **kwargs: redis)
    monkeypatch.setattr(config, "LLM_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(config, "LLM_BUDGET_SECONDS", 0.02)
    async def call_with_supervisor_timeout():
        return await asyncio.wait_for(AGENT.run(Handoff.model_validate(payload())), 0.05)
    with pytest.raises(TimeoutError):
        asyncio.run(call_with_supervisor_timeout())
    assert redis.flag is None and redis.reads == 1 and redis.closed
    key, evidence, options = redis.writes[0]
    assert key == f"evidence:delay:{payload()['task_id']}" and options == {"ex": 60}
    assert evidence['seconds'] == 0.2 and evidence['incident_id'] == 'incident-test'
    assert evidence['observed_at'] > 0
    retry = {**payload(), "attempt": 2, "last_failure": "SERVICE_UNAVAILABLE"}
    result = TestClient(app).post("/run", json=retry).json()
    assert result["status"] == "success" and result["incident_id"] is None
    assert redis.reads == 2


def test_redis_outage_does_not_stop_agent(monkeypatch):
    redis = FakeRedis(broken=True)
    monkeypatch.setattr(config, "REDIS_URL", "redis://test")
    monkeypatch.setattr(AGENT.Redis, "from_url", lambda *args, **kwargs: redis)
    assert TestClient(app).post("/run", json=payload()).json()["status"] == "success"
    assert redis.closed


def test_slow_model_is_cancelled_and_uses_local_fallback(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config, "LLM_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(config, "LLM_BUDGET_SECONDS", 0.025)
    cancelled = []
    async def slow(*args):
        try:
            await asyncio.sleep(1)
        finally:
            cancelled.append(True)
    monkeypatch.setattr(llm, "generate", slow)
    assert TestClient(app).post("/run", json=payload()).json()["status"] == "success"
    assert 1 <= len(cancelled) <= 3


def test_second_supervisor_attempt_uses_fallback_model(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    calls = []
    async def generate(model):
        calls.append(model)
        return '{"ok":true}'
    result = asyncio.run(recover(generate, lambda: {"ok": False}, json.loads,
                                 attempt=2, last_failure="LLM_OUTPUT_INVALID"))
    assert result == {"ok": True}
    assert calls == [config.OPENAI_FALLBACK_MODEL]


def test_responses_api_request_and_output_parsing(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    original_client = httpx.AsyncClient
    def handler(request):
        assert request.url == "https://api.openai.com/v1/responses"
        assert request.headers["authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["model"] == "test-model"
        assert body["store"] is False
        assert body["text"]["format"]["type"] == "json_object"
        heading, _, input_json = body["input"].partition("\n")
        assert "json" in heading.lower()
        assert json.loads(input_json) == {"text": "민원 설명"}
        assert request.extensions["timeout"] == dict.fromkeys(
            ("connect", "read", "write", "pool"), 10.0
        )
        return httpx.Response(200, json={
            "status": "completed",
            "output": [
                {"type": "reasoning"},
                {"type": "message", "content": [
                    {"type": "output_text", "text": '{"ok":'},
                    {"type": "output_text", "text": 'true}'},
                ]},
            ],
        })
    monkeypatch.setattr(llm.httpx, "AsyncClient",
                        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs))
    assert asyncio.run(llm.generate("JSON으로 반환", {"text": "민원 설명"}, "test-model")) == '{"ok":true}'


@pytest.mark.parametrize("response", [
    {"status": "incomplete", "output": []},
    {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal"}]}]},
    [],
])
def test_incomplete_or_refused_api_output_is_invalid(monkeypatch, response):
    original_client = httpx.AsyncClient
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response)), **kwargs))
    with pytest.raises(ValueError):
        asyncio.run(llm.generate("JSON", {}, "test-model"))


def test_api_outage_uses_local_fallback(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    async def unavailable(*args):
        raise httpx.ConnectError("offline")
    monkeypatch.setattr(llm, "generate", unavailable)
    result = TestClient(app).post("/run", json=payload()).json()
    assert result["status"] == "success"
    assert result["failure"] is None
    assert result["incident_id"].startswith("incident-")


@pytest.mark.parametrize("error", [TimeoutError, httpx.ReadTimeout])
def test_primary_timeout_keeps_remaining_budget_for_fallback(monkeypatch, error):
    recovery = importlib.import_module("app.services.recovery")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config, "LLM_TIMEOUT_SECONDS", 10.0)
    monkeypatch.setattr(config, "LLM_BUDGET_SECONDS", 13.0)
    now, calls, limits = [0.0], [], []
    async def generate(model):
        calls.append(model)
        if model == config.OPENAI_MODEL:
            now[0] += 10.0
            raise error("timeout")
        return '{"ok":true}'
    async def wait_for(awaitable, timeout):
        limits.append(timeout)
        return await awaitable
    monkeypatch.setattr(recovery, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(recovery, "asyncio", SimpleNamespace(wait_for=wait_for))
    result = asyncio.run(recover(generate, lambda: {"ok": False}, json.loads))
    assert result == {"ok": True}
    assert calls == [config.OPENAI_MODEL, config.OPENAI_FALLBACK_MODEL]
    assert limits == [10.0, 3.0]
