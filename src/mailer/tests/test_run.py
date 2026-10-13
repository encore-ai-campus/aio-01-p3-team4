import asyncio
import json
from datetime import timedelta
from email.message import EmailMessage
from zoneinfo import ZoneInfo
import pytest
from app.clients import smtp
from app.core import config
from app.services import contacts, outbox
from conftest import FakeSMTP


@pytest.mark.parametrize("name", ["mailer", "mailer_mail_down", "mailer_blocked"])
def test_run_sample(name, handoff, post, chaos):
    if name.endswith("down"):
        chaos()
        FakeSMTP.failures = 3
    expected = json.loads((config.CONTRACTS / "samples" / f"{name}_output.json").read_text())
    assert post(handoff(name)) == expected


def test_is_virtual():
    for address in ("user@minwon.example", "A.B+demo@SUB.MINWON.EXAMPLE"):
        assert smtp.is_virtual(address)
    for address in ("x@example.com", "x@minwon.example.com", "x@minwon.test",
                    "a@b.com@minwon.example", "x@minwon.example\n", "x\r\n@minwon.example"):
        assert not smtp.is_virtual(address)


@pytest.mark.parametrize("header", ["To", "Cc", "Bcc", "From"])
def test_smtp_blocks_before_connection(header):
    msg = EmailMessage()
    msg["From"], msg["To"] = "sender@minwon.example", "recipient@minwon.example"
    if header in msg:
        del msg[header]
    msg[header] = "virtual@minwon.example, unsafe@minwon.test"
    with pytest.raises(smtp.Blocked):
        asyncio.run(smtp.send(msg))
    assert not FakeSMTP.opened


def test_invalid_contact_blocked(handoff, post, monkeypatch):
    monkeypatch.setattr(contacts, "find", lambda _: {"dept": "가상 민원실", "officer": "가상 담당자",
                                                   "email": "officer@minwon.test"})
    body = post(handoff())
    assert body["failure"] == "MAIL_BLOCKED"
    assert body["data"]["to"] == "officer@minwon.test" and body["data"]["attempts"] == 0
    assert not FakeSMTP.opened and not outbox.get().items


def flush(client):
    client.portal.call(outbox.flush, outbox.now())


def test_report_then_receipt_reply(handoff, post, client):
    body = post(handoff())
    assert config.SMTP_HOST == "mailpit"
    assert FakeSMTP.opened == [("mailpit", 1025, 2)]
    assert len(FakeSMTP.sent) == 1
    report, recipients = FakeSMTP.sent[0]
    assert recipients == ["epeople-desk@minwon.example"]
    assert report["Subject"].startswith("[가상 민원]")
    assert report["Message-ID"] == body["data"]["message_id"]
    assert report["Reply-To"] == "citizen@minwon.example"
    assert report["X-Minwon-Task"] == handoff()["task_id"]
    assert "유형: 가로등 고장 / 지역: 서울특별시 동작구 상도동" in report.get_content()
    assert outbox.FOOTER in report.get_content()
    flush(client)
    assert len(FakeSMTP.sent) == 2
    reply, recipients = FakeSMTP.sent[1]
    assert recipients == ["citizen@minwon.example"]
    assert reply["From"] == report["To"]
    assert "접수번호 2026-1007-0001" in reply["Subject"]
    assert reply["In-Reply-To"] == report["Message-ID"]
    assert body["data"]["receipt_no"] == "2026-1007-0001"
    assert handoff()["inputs"]["draft"]["title"] in reply.get_content()
    assert outbox.FOOTER in reply.get_content()
    flush(client)
    assert len(FakeSMTP.sent) == 2


def test_two_failures_then_success(handoff, post):
    FakeSMTP.failures = 2
    body = post(handoff())
    assert body["status"] == "success" and body["data"]["attempts"] == 3
    assert len(FakeSMTP.opened) == 3 and len(FakeSMTP.sent) == 1


def test_queued_restore_and_background_recovery(handoff, post, client):
    FakeSMTP.failures = 3
    body = post(handoff())
    assert body["status"] == "retry" and body["failure"] == "MAIL_SEND_FAILED"
    assert body["data"]["status"] == "queued" and body["data"]["attempts"] == 3
    assert body["data"]["receipt_no"] is None
    outbox.box = outbox.Outbox(config.OUTBOX_PATH)
    assert outbox.get().items[handoff()["task_id"]]["status"] == "queued"
    FakeSMTP.failures = 1
    flush(client)
    assert outbox.get().items[handoff()["task_id"]]["attempts"] == 4
    assert outbox.get().items[handoff()["task_id"]]["last_error"]
    flush(client)
    assert len(FakeSMTP.sent) == 2
    item = client.get("/outbox").json()["items"][0]
    assert item["status"] == "sent" and item["receipt_no"] == "2026-1007-0001"
    assert item["attempts"] == 5 and item["reply_sent_at"] and "body" not in item


def test_sent_task_not_sent_twice(handoff, post):
    value = handoff()
    first = post(value)
    value["inputs"]["draft"]["title"] = "사용자가 바꾼 제목"
    assert post(value) == first
    assert len(FakeSMTP.sent) == len(FakeSMTP.opened) == 1


def test_queued_task_retries_immediately(handoff, post):
    FakeSMTP.failures = 3
    assert post(handoff())["status"] == "retry"
    body = post(handoff())
    assert body["status"] == "success" and body["data"]["attempts"] == 4
    assert len(FakeSMTP.sent) == 1


def test_mail_down_consumed_once_and_recovers(handoff, post, client, chaos):
    redis, flag = chaos()
    FakeSMTP.failures = 3
    body = post(handoff())
    assert body["status"] == "retry" and body["incident_id"] == flag.incident_id
    assert redis.closed and not redis.values and len(FakeSMTP.opened) == 3
    flush(client)
    assert len(FakeSMTP.sent) == 2
    assert post(handoff())["status"] == "success"
    next_job = handoff()
    next_job["task_id"] = "next-job"
    assert post(next_job)["incident_id"] is None


def test_contacts_fallback_retention_and_kst_receipts(handoff, post, client, monkeypatch):
    value = handoff()
    value["inputs"]["channel"]["channel_id"] = "unknown-all-01"
    body = post(value)
    assert body["data"]["to"] == "unknown-all-01@minwon.example"
    assert body["data"]["dept"] == "가상 민원실" and body["data"]["officer"] == "가상 담당자"
    store = outbox.get()
    original = store.items[value["task_id"]].copy()
    for index in range(101):
        store.save({**original, "task_id": f"queued-{index}", "status": "queued", "receipt_no": None,
                    "queued_at": (outbox.now() + timedelta(seconds=index + 1)).isoformat()})
    assert len(store.items) == len(outbox.Outbox(config.OUTBOX_PATH).items) == 100
    assert client.get("/outbox").json()["items"][0]["task_id"] == "queued-100"
    store.items.clear()
    fixed = outbox.now().replace(hour=0, minute=30)
    monkeypatch.setattr(outbox, "now", lambda: fixed.astimezone(ZoneInfo("UTC")))
    value["task_id"] = "kst-boundary"
    assert post(value)["data"]["receipt_no"] == "2026-1007-0001"


def test_reply_failure_retried_on_next_flush(handoff, post, client):
    post(handoff())
    FakeSMTP.failures = 1
    flush(client)
    item = next(iter(outbox.get().items.values()))
    assert item["status"] == "sent" and not item["reply_sent_at"] and item["last_error"]
    flush(client)
    assert item["reply_sent_at"] and item["last_error"] is None and len(FakeSMTP.sent) == 2


def test_background_loop_survives_error_and_cancels(monkeypatch):
    from app import main
    from fastapi.testclient import TestClient
    calls = []

    async def fail_then_succeed(time):
        calls.append(time)
        if len(calls) == 1:
            raise OSError("모의 편지함 장애")

    async def wait_for_two_calls():
        while len(calls) < 2:
            await asyncio.sleep(0.001)

    monkeypatch.setattr(config, "OUTBOX_INTERVAL_SEC", 0.001)
    monkeypatch.setattr(outbox, "flush", fail_then_succeed)
    with TestClient(main.app) as client:
        client.portal.call(asyncio.wait_for, wait_for_two_calls(), 1)
    assert len(calls) >= 2


def test_flag_alone_does_not_fake_smtp_failure(handoff, post, chaos):
    _, flag = chaos()
    result = post(handoff())
    assert result["status"] == "success" and len(FakeSMTP.sent) == 1
    assert result["incident_id"] == flag.incident_id
    assert "evidence" not in result["data"]


def test_failure_evidence_survives_outbox_restore_and_actual_resend(handoff, post, client, monkeypatch):
    async def fail(message):
        raise ConnectionRefusedError("private message 010-9876-5432")
    original = smtp.send
    monkeypatch.setattr(smtp, "send", fail)
    failed = post(handoff())
    evidence = failed["data"]["evidence"]
    assert evidence == {"broken": "메일 발송 실패 · 1차 시도 ConnectionRefusedError"}
    assert "010-9876-5432" not in json.dumps(evidence)
    outbox.box = outbox.Outbox(config.OUTBOX_PATH)
    monkeypatch.setattr(smtp, "send", original)
    flush(client)
    recovered = post(handoff())["data"]
    assert recovered["evidence"]["broken"] == evidence["broken"]
    assert recovered["evidence"]["fixed"] == (
        "편지함 대기 후 재발송 성공 12:00:00 · 접수번호 " + recovered["receipt_no"])


def test_demo_citizen_is_named_in_report_and_receives_reply(handoff, post, client):
    value = handoff()
    value["inputs"]["citizen"] = {"name": "김민원", "email": "minwon.kim@minwon.example"}
    assert post(value)["status"] == "success"
    report = FakeSMTP.sent[0][0]
    assert report["From"] == config.MAIL_SENDER
    assert report["Reply-To"] == "minwon.kim@minwon.example"
    assert "보낸 사람: 김민원 <minwon.kim@minwon.example>" in report.get_content()
    flush(client)
    assert FakeSMTP.sent[1][1] == ["minwon.kim@minwon.example"]
