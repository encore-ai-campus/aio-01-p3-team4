import json
from datetime import datetime
from email.parser import BytesParser
from email.policy import default
from zoneinfo import ZoneInfo
import pytest
from fastapi.testclient import TestClient
from app.clients import smtp
from app.clients.chaos import ChaosFlag
from app.core import config
from app.main import app
from app.schemas.contract import validate
from app.services import contacts, mailer, outbox


class FakeSMTP:
    opened = []
    sent = []
    failures = 0

    def __init__(self, host, port, timeout):
        self.opened.append((host, port, timeout))
        if self.failures:
            type(self).failures -= 1
            raise ConnectionRefusedError("모의 SMTP 연결 실패")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def send_message(self, msg, to_addrs):
        assert all(smtp.is_virtual(address) for address in to_addrs)
        self.sent.append((BytesParser(policy=default).parsebytes(msg.as_bytes()), to_addrs))

    def login(self, *args):
        pytest.fail("SMTP login 금지")

    def starttls(self, *args):
        pytest.fail("SMTP starttls 금지")


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    FakeSMTP.opened, FakeSMTP.sent, FakeSMTP.failures = [], [], 0
    monkeypatch.setattr(smtp.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(config, "OUTBOX_PATH", tmp_path / "outbox.json")
    monkeypatch.setattr(config, "REPLY_DELAY_SEC", 0)
    monkeypatch.setattr(config, "OUTBOX_INTERVAL_SEC", 100000)
    monkeypatch.setattr(config, "MAIL_RELAY_HOST", "")
    monkeypatch.setattr(outbox, "box", None)
    monkeypatch.setattr(outbox, "now", lambda: datetime(2026, 10, 7, 12, tzinfo=ZoneInfo("Asia/Seoul")))
    monkeypatch.setattr(mailer, "REDIS_URL", "")
    monkeypatch.setattr(mailer, "RETRY_DELAYS", (0, 0))
    contacts.load.cache_clear()


@pytest.fixture
def handoff():
    def load(name="mailer"):
        return json.loads((config.CONTRACTS / "samples" / f"{name}_input.json").read_text())
    return load


@pytest.fixture
def client():
    with TestClient(app) as client:
        yield client


@pytest.fixture
def post(client):
    def call(value):
        response = client.post("/run", json=value)
        assert response.status_code == 200, response.text
        body = response.json()
        validate("stage_result", body)
        assert body["task_id"] == value["task_id"]
        return body
    return call


class FakeRedis:
    def __init__(self, flag):
        self.values = {"chaos:mailer": flag.model_dump_json()}
        self.closed = False

    async def getdel(self, key):
        return self.values.pop(key, None)

    async def set(self, key, value):
        self.values[key] = value

    async def aclose(self):
        self.closed = True


@pytest.fixture
def chaos(monkeypatch):
    def install(kind="mail_down"):
        flag = ChaosFlag(incident_id=f"incident-{kind}", kind=kind,
                         injected_at="2026-10-07T00:00:00Z", params={})
        redis = FakeRedis(flag)

        def from_url(url, **kwargs):
            assert url == "redis://fake"
            assert kwargs == {"socket_connect_timeout": 0.5, "socket_timeout": 0.5}
            return redis

        monkeypatch.setattr(mailer, "REDIS_URL", "redis://fake")
        monkeypatch.setattr(mailer.Redis, "from_url", from_url)
        return redis, flag
    return install
