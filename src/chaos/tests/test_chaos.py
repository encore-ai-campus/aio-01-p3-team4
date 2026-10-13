import asyncio
import json
from fastapi.testclient import TestClient
from app.clients.chaos import ChaosClient
from app.main import app


def test_wrapper_reads_named_key_and_injects_locally():
    class Redis:
        async def getdel(self, key):
            assert key == "chaos:verifier"
            return json.dumps({"incident_id": "test-1", "kind": "link_dead", "injected_at": "2026-10-06T00:00:00Z", "params": {}})
    async def operation():
        raise AssertionError("외부 작업이 실행되면 안 됨")
    async def inject(flag):
        return flag.kind
    assert asyncio.run(ChaosClient(Redis()).run("verifier", operation, inject)) == "link_dead"


def test_reject_unknown_flag_contract():
    response = TestClient(app).put("/flags/database", json={})
    assert response.status_code == 422


def test_redis_unavailable_passes_through():
    from redis.exceptions import ConnectionError
    class Redis:
        async def getdel(self, key):
            raise ConnectionError("[TEST] redis down")
    async def operation():
        return "continued"
    assert asyncio.run(ChaosClient(Redis()).run("backend", operation)) == "continued"


def test_flag_fires_once_then_clears():
    class Redis:
        def __init__(self):
            self.store = {"chaos:writer": json.dumps({"incident_id": "test-2", "kind": "llm_invalid", "injected_at": "2026-10-06T00:00:00Z", "params": {}})}
        async def getdel(self, key):
            return self.store.pop(key, None)
    async def operation():
        return "real"
    async def inject(flag):
        return "injected"
    client = ChaosClient(Redis())
    assert asyncio.run(client.run("writer", operation, inject)) == "injected"
    assert asyncio.run(client.run("writer", operation, inject)) == "real"


def test_repeat_api_validation_and_roundtrip(redis):
    client = TestClient(app)
    flag = dict(incident_id="test-repeat", kind="link_dead", injected_at="2026-10-12T00:00:00Z", params={})
    for value in (0, 11, -1, 1.5, 3.0, True, "3", None):
        response = client.put("/flags/verifier", json={**flag, "params": {"repeat": value}})
        assert response.status_code == 422
        assert not redis.values
    for value in (1, 3, 10):
        response = client.put("/flags/verifier", json={**flag, "params": {"repeat": value}})
        assert response.status_code == 200
        assert client.get("/flags/verifier").json()["params"] == {"repeat": value}
