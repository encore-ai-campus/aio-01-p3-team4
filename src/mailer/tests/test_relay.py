import asyncio
import json
import smtplib
import ssl
from email.utils import parseaddr
from unittest.mock import MagicMock
import pytest
from app.clients import relay
from app.core import config
from app.schemas.contract import Handoff
from app.services import mailer, outbox
from conftest import FakeSMTP


def enable(monkeypatch):
    for name, value in {"HOST": "smtp.relay.test", "PORT": 587, "USER": "demo@relay.test",
                        "PASSWORD": "mock-password", "TO": "team@relay.test", "TIMEOUT_SEC": 8}.items():
        monkeypatch.setattr(config, "MAIL_RELAY_" + name, value)


async def drain():
    while relay._pending:
        await asyncio.gather(*list(relay._pending.values()))


@pytest.mark.parametrize("missing", [None, "HOST", "USER", "PASSWORD", "TO"])
def test_disabled_never_connects(monkeypatch, missing, capsys):
    if missing:
        enable(monkeypatch)
        monkeypatch.setattr(config, "MAIL_RELAY_" + missing, "")
    smtp = MagicMock()
    monkeypatch.setattr(relay.smtplib, "SMTP", smtp)
    assert asyncio.run(relay.send_copy({})) is False
    smtp.assert_not_called()
    assert capsys.readouterr().out == ""


def test_copies_headers_content_and_starttls(monkeypatch):
    enable(monkeypatch)
    item = {"task_id": "demo-1", "to": "desk@minwon.example", "citizen": "citizen@minwon.example",
            "subject": "[가상 민원] 가로등", "body": "가로등 수리를 부탁합니다.\n\n" + outbox.FOOTER,
            "message_id": "<demo-1.report@minwon.example>", "receipt_no": "2026-1007-0001",
            "dept": "가상 민원실", "officer": "김담당"}
    smtp = MagicMock()
    monkeypatch.setattr(relay.smtplib, "SMTP", smtp)

    async def scenario():
        assert await relay.send_copy(item)
        assert await relay.send_copy(item, reply=True)

    asyncio.run(scenario())
    smtp.assert_called_with("smtp.relay.test", 587, timeout=8)
    client = smtp.return_value.__enter__.return_value
    assert isinstance(client.starttls.call_args.kwargs["context"], ssl.SSLContext)
    client.login.assert_called_with("demo@relay.test", "mock-password")
    report, reply = [call.args[0] for call in client.send_message.call_args_list]
    for copy, is_reply in ((report, False), (reply, True)):
        source = outbox.message(item, is_reply)
        assert copy["Subject"] == source["Subject"] and copy["To"] == config.MAIL_RELAY_TO
        assert source.get_content().rstrip("\n") in copy.get_content()
        assert relay.COPY_NOTICE in copy.get_content()
    assert parseaddr(report["From"]) == ("생활 민원 길잡이 (시연)", config.MAIL_RELAY_USER)
    assert parseaddr(reply["From"]) == ("가상 민원실 김담당 (가상 담당자)", config.MAIL_RELAY_USER)
    assert report["Message-ID"] == "<demo-1.relay@minwon.example>"
    assert reply["In-Reply-To"] == reply["References"] == report["Message-ID"]
    assert reply["Message-ID"] != report["Message-ID"]
    for call in client.send_message.call_args_list:
        assert call.kwargs == {"from_addr": config.MAIL_RELAY_USER, "to_addrs": [config.MAIL_RELAY_TO]}


@pytest.mark.parametrize("queued", [False, True])
def test_run_schedules_once_and_recovery_copies_reply(monkeypatch, handoff, queued):
    enable(monkeypatch)
    calls = []
    tasks = []
    create_task = asyncio.create_task

    def track(coro):
        task = create_task(coro)
        tasks.append(task)
        return task

    async def scenario():
        gate, started = asyncio.Event(), asyncio.Event()

        async def send_copy(item, reply=False):
            calls.append(reply)
            started.set()
            await gate.wait()
            return True

        monkeypatch.setattr(relay, "send_copy", send_copy)
        monkeypatch.setattr(relay.asyncio, "create_task", track)
        value = Handoff.model_validate(handoff())
        FakeSMTP.failures = 3 if queued else 0
        first = await mailer.run(value)
        if queued:
            assert first.status == "retry" and calls == [] and tasks == []
            # Reload JSON with no relay keys, as in an existing outbox.
            outbox.box = outbox.Outbox(config.OUTBOX_PATH)
            await outbox.flush(outbox.now())
        success = await mailer.run(value)
        assert success.status == "success" and success.data["receipt_no"] == "2026-1007-0001"
        await started.wait()
        for _ in range(2):
            assert await mailer.run(value) == success
            await outbox.flush(outbox.now())
        gate.set()
        await drain()
        await outbox.flush(outbox.now())
        await drain()
        assert calls == [False, True] and len(tasks) == 2
        item = json.loads(config.OUTBOX_PATH.read_text())[value.task_id]
        assert item["relay_sent_at"] and item["relay_reply_sent_at"]
        assert item["relay_attempts"] == item["relay_reply_attempts"] == 1
        assert len(FakeSMTP.sent) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [smtplib.SMTPAuthenticationError(535, b"private error"), TimeoutError()])
def test_relay_failure_preserves_result_and_caps_retries(monkeypatch, handoff, capsys, failure):
    enable(monkeypatch)
    connections = []

    def connect(host, port, timeout):
        if host == config.SMTP_HOST:
            return FakeSMTP(host, port, timeout)
        connections.append(host)
        raise failure

    monkeypatch.setattr(relay.smtplib, "SMTP", connect)

    async def scenario():
        value = Handoff.model_validate(handoff())
        first = await mailer.run(value)
        assert first.status == "success" and first.data["receipt_no"] == "2026-1007-0001"
        await drain()
        for _ in range(5):
            await outbox.flush(outbox.now())
            await drain()
        assert await mailer.run(value) == first
        assert len(connections) == 6 and len(FakeSMTP.sent) == 2
        item = outbox.Outbox(config.OUTBOX_PATH).items[value.task_id]
        assert item["relay_attempts"] == item["relay_reply_attempts"] == 3
        assert item["relay_error"] == item["relay_reply_error"] == "MAIL_RELAY_FAILED"
        assert item["relay_sent_at"] is item["relay_reply_sent_at"] is None
        assert item["status"] == "sent" and item["reply_sent_at"] and item["last_error"] is None

    asyncio.run(scenario())
    logs = capsys.readouterr().out
    assert "relay error=" in logs and "relay_reply error=" in logs
    assert "private error" not in logs and "mock-password" not in logs
