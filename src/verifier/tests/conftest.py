import json
from datetime import date
import httpx
import pytest
from fastapi.testclient import TestClient
from app.clients import links
from app.clients.chaos import ChaosFlag
from app.core.config import CONTRACTS
from app.main import app
from app.schemas.contract import validate
from app.services import channel, verifier


def web_handler(request):
    host = request.url.host
    if host in ("www.epeople.go.kr", "example.com"):
        return httpx.Response(200)
    if host == "www.safetyreport.go.kr":
        return httpx.Response(405 if request.method == "HEAD" else 200)
    raise httpx.ConnectError("모의 연결 실패", request=request)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    async def forbid_network(*args, **kwargs):
        pytest.fail("실제 HTTP 요청 금지")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbid_network)
    monkeypatch.setattr(links, "TRANSPORT", httpx.MockTransport(web_handler))
    monkeypatch.setattr(channel, "today", lambda: date(2026, 10, 7))
    monkeypatch.setattr(verifier, "REDIS_URL", "")


@pytest.fixture
def handoff():
    def load(mode="channel"):
        name = "verifier_streetlight" + ("_draft" if mode == "draft" else "")
        return json.loads((CONTRACTS / "samples" / f"{name}_input.json").read_text())
    return load


@pytest.fixture
def post():
    with TestClient(app) as client:
        def call(value):
            response = client.post("/run", json=value)
            assert response.status_code == 200, response.text
            body = response.json()
            validate("stage_result", body)
            assert body["task_id"] == value["task_id"]
            return body
        yield call


class FakeRedis:
    def __init__(self, flag):
        self.values = {"chaos:verifier": flag.model_dump_json()}
        self.closed = False

    async def getdel(self, key):
        return self.values.pop(key, None)

    async def set(self, key, value):
        self.values[key] = value

    async def aclose(self):
        self.closed = True


@pytest.fixture
def chaos(monkeypatch):
    def install(kind):
        flag = ChaosFlag(incident_id=f"incident-{kind}", kind=kind,
                         injected_at="2026-10-07T00:00:00Z", params={})
        redis = FakeRedis(flag)

        def from_url(url, **kwargs):
            assert url == "redis://fake"
            assert kwargs == {"socket_connect_timeout": 0.5, "socket_timeout": 0.5}
            return redis

        monkeypatch.setattr(verifier, "REDIS_URL", "redis://fake")
        monkeypatch.setattr(verifier.Redis, "from_url", from_url)
        return redis, flag
    return install
