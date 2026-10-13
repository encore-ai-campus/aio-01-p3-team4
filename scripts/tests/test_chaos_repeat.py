import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("service", ["backend", "classifier", "locator", "verifier", "writer", "mailer", "chaos"])
def test_repeat_contract_for_every_copy(service):
    path = ROOT / f"src/{service}/app/clients/chaos.py"
    spec = importlib.util.spec_from_file_location(f"chaos_{service}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert path.read_bytes() == (ROOT / "src/backend/app/clients/chaos.py").read_bytes()

    class Redis:
        def __init__(self, flag):
            self.values = {"chaos:verifier": json.dumps(flag)}

        async def getdel(self, key):
            return self.values.pop(key, None)

        async def set(self, key, value):
            self.values[key] = value

    async def exercise(params):
        flag = dict(incident_id="test-repeat", kind="link_dead", injected_at="2026-10-12T00:00:00Z", params=params)
        redis = Redis(flag)
        client = module.ChaosClient(redis)
        async def operation():
            return "real"
        async def inject(value):
            assert value.incident_id == flag["incident_id"]
            return "injected"
        for remaining in range(params.get("repeat", 1), 0, -1):
            assert await client.run("verifier", operation, inject) == "injected"
            if remaining > 1:
                assert json.loads(redis.values["chaos:verifier"])["params"]["repeat"] == remaining - 1
        assert not redis.values
        assert await client.run("verifier", operation, inject) == "real"

    for params in ({}, {"repeat": 1}, {"repeat": 3}, {"repeat": 10}):
        asyncio.run(exercise(params))
