import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock
import pytest
from fastapi.testclient import TestClient

from app.clients import agents, store
from app.main import app
from app.schemas.contract import Handoff, StageResult
from app.services import dispatch, incidents


class FakeDispatchDB:
    def __init__(self):
        self.jobs = {}
        self.timeline = []
        self.incidents = []
        self.queries = []

    async def query(self, sql, params=(), fetch=False):
        self.queries.append((sql, params))

        if sql == "SELECT * FROM jobs WHERE id=%s":
            job_id = params[0]
            if job_id in self.jobs:
                return [dict(self.jobs[job_id])]
            return []

        if "FROM timeline_events WHERE job_id=%s" in sql:
            job_id = params[0]
            return [dict(e) for e in self.timeline if e["job_id"] == job_id]

        if sql.startswith("INSERT INTO timeline_events"):
            job_id, agent, event_type, attempt, payload = params
            raw_payload = payload.obj if hasattr(payload, "obj") else payload
            entry = {
                "id": len(self.timeline) + 1,
                "job_id": job_id,
                "agent": agent,
                "event_type": event_type,
                "attempt": attempt,
                "payload": json.loads(json.dumps(raw_payload)),
                "created_at": datetime.now(timezone.utc),
            }
            self.timeline.append(entry)
            return None

        if sql.startswith("UPDATE jobs SET state = state || %s WHERE id=%s"):
            payload, job_id = params
            raw = payload.obj if hasattr(payload, "obj") else payload
            if job_id in self.jobs:
                current_state = dict(self.jobs[job_id].get("state", {}))
                current_state.update(json.loads(json.dumps(raw)))
                self.jobs[job_id]["state"] = current_state
            return None

        if "INSERT INTO incidents" in sql and "detected_at" in sql:
            inc_id, job_id, target, fault, detected_at, res_status = params
            existing = next((r for r in self.incidents if r["id"] == inc_id), None)
            if existing is None:
                self.incidents.append({
                    "id": inc_id,
                    "job_id": job_id,
                    "target": target,
                    "fault": fault,
                    "injected_at": None,
                    "detected_at": detected_at or datetime.now(timezone.utc),
                    "recovered_at": None,
                    "human_intervention": False,
                    "result": res_status,
                })
            else:
                existing.update({"job_id": job_id, "target": target, "fault": fault})
            return None

        if sql.startswith("UPDATE incidents SET recovered_at"):
            inc_id = params[0]
            existing = next((r for r in self.incidents if r["id"] == inc_id), None)
            if existing:
                existing["recovered_at"] = datetime.now(timezone.utc)
                existing["result"] = "recovered"
            return None

        if sql.startswith("UPDATE incidents SET result = 'failed'"):
            inc_id = params[0]
            existing = next((r for r in self.incidents if r["id"] == inc_id), None)
            if existing and existing.get("result") != "recovered":
                existing["result"] = "failed"
            return None

        return []


@pytest.fixture
def db(monkeypatch):
    fake = FakeDispatchDB()
    monkeypatch.setattr(store, "query", fake.query)
    return fake


@pytest.fixture
def client():
    return TestClient(app)


def make_success_job(job_id="job-100", **custom_state_fields):
    state = {
        "result": {
            "category": "streetlight",
            "region": {"sido": "서울특별시", "sigungu": "동작구", "dong": "상도동", "place_text": "상도동 주민센터 앞"},
            "channels": [{
                "channel_id": "national-all-01",
                "name": "국민신문고",
                "org": "국민권익위원회",
                "method": "web",
                "url": "https://www.epeople.go.kr/",
                "jurisdiction": "전국",
                "verified_at": "2026-10-06",
                "source_url": "https://www.epeople.go.kr/",
            }],
            "draft": {"title": "최초 생성 제목", "body": "최초 생성 본문"},
        },
        "classification": {"category": "streetlight", "confidence": 0.95},
        "confirm_needed": [],
    }
    state.update(custom_state_fields)
    return {
        "id": job_id,
        "text": "가로등 고장 신고",
        "status": "success",
        "state": state,
        "created_at": datetime.now(timezone.utc),
        "completed_at": datetime.now(timezone.utc),
    }


# 1. 성공 작업의 수정된 title/body가 mailer Handoff에 전달됨
def test_send_job_modified_title_and_body_passed_to_mailer(client, db, monkeypatch):
    db.jobs["job-1"] = make_success_job("job-1")
    captured_handoff = None

    async def fake_call(handoff: Handoff):
        nonlocal captured_handoff
        captured_handoff = handoff
        return {
            "task_id": handoff.task_id,
            "author": "mailer",
            "status": "success",
            "data": {
                "message_id": "<job-1.report@minwon.example>",
                "to": "epeople-desk@minwon.example",
                "dept": "국민신문고 민원처리과(가상)",
                "officer": "김가상 주무관",
                "status": "sent",
                "receipt_no": "2026-1009-0001",
                "attempts": 1,
            },
            "failure": None,
            "incident_id": None,
        }

    monkeypatch.setattr(agents, "call", fake_call)
    res = client.post("/jobs/job-1/send", json={"title": "사용자가 고친 제목", "body": "사용자가 고친 본문"})
    assert res.status_code == 200
    assert captured_handoff is not None
    assert captured_handoff.inputs["draft"]["title"] == "사용자가 고친 제목"
    assert captured_handoff.inputs["draft"]["body"] == "사용자가 고친 본문"
    assert captured_handoff.inputs["draft"]["title"] != "최초 생성 제목"
    assert captured_handoff.from_agent == "supervisor"
    assert captured_handoff.to == "mailer"
    assert any(".example" in c for c in captured_handoff.constraints)


# 2. 정상 성공은 1회 호출, success timeline 1행
def test_send_job_clean_success_calls_once_and_records_one_timeline(client, db, monkeypatch):
    db.jobs["job-2"] = make_success_job("job-2")
    call_count = 0

    async def fake_call(handoff: Handoff):
        nonlocal call_count
        call_count += 1
        return {
            "task_id": handoff.task_id,
            "author": "mailer",
            "status": "success",
            "data": {
                "message_id": "<job-2.report@minwon.example>",
                "to": "desk@minwon.example",
                "dept": "민원과",
                "officer": "담당자",
                "status": "sent",
                "receipt_no": "2026-1009-0002",
                "attempts": 1,
            },
            "failure": None,
            "incident_id": None,
        }

    monkeypatch.setattr(agents, "call", fake_call)
    res = client.post("/jobs/job-2/send", json={"title": "제목", "body": "본문"})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["mail"]["receipt_no"] == "2026-1009-0002"
    assert call_count == 1
    events = [e for e in db.timeline if e["job_id"] == "job-2"]
    assert len(events) == 1
    assert events[0]["agent"] == "mailer"
    assert events[0]["event_type"] == "success"
    assert events[0]["attempt"] == 1
    assert events[0]["payload"]["data"]["receipt_no"] == "2026-1009-0002"


# 3. MAIL_SEND_FAILED 후 성공하면 attempt 1, 2와 last_failure가 정확함
def test_send_job_mail_send_failed_then_succeeds_tracks_attempts(client, db, monkeypatch):
    db.jobs["job-3"] = make_success_job("job-3")
    handoffs = []

    async def fake_call(handoff: Handoff):
        handoffs.append(handoff)
        if len(handoffs) == 1:
            return {
                "task_id": handoff.task_id,
                "author": "mailer",
                "status": "retry",
                "data": {
                    "message_id": "<job-3.report@minwon.example>",
                    "to": "desk@minwon.example",
                    "dept": "민원과",
                    "officer": "담당자",
                    "status": "queued",
                    "receipt_no": None,
                    "attempts": 3,
                },
                "failure": "MAIL_SEND_FAILED",
                "incident_id": "inc-mail-down-3",
            }
        return {
            "task_id": handoff.task_id,
            "author": "mailer",
            "status": "success",
            "data": {
                "message_id": "<job-3.report@minwon.example>",
                "to": "desk@minwon.example",
                "dept": "민원과",
                "officer": "담당자",
                "status": "sent",
                "receipt_no": "2026-1009-0003",
                "attempts": 1,
            },
            "failure": None,
            "incident_id": None,
        }

    monkeypatch.setattr(agents, "call", fake_call)
    res = client.post("/jobs/job-3/send", json={"title": "제목", "body": "본문"})
    assert res.status_code == 200
    assert len(handoffs) == 2
    assert handoffs[0].attempt == 1
    assert handoffs[0].last_failure is None
    assert handoffs[1].attempt == 2
    assert handoffs[1].last_failure == "MAIL_SEND_FAILED"

    events = [e for e in db.timeline if e["job_id"] == "job-3"]
    assert len(events) == 2
    assert [(e["attempt"], e["event_type"]) for e in events] == [(1, "retry"), (2, "success")]
    assert db.jobs["job-3"]["state"]["mail"]["status"] == "success"


# 4. MAIL_SEND_FAILED가 계속되면 정확히 3회 후 종료
def test_send_job_mail_send_failed_persists_stops_at_max_attempts(client, db, monkeypatch):
    db.jobs["job-4"] = make_success_job("job-4")
    call_count = 0

    async def fake_call(handoff: Handoff):
        nonlocal call_count
        call_count += 1
        return {
            "task_id": handoff.task_id,
            "author": "mailer",
            "status": "retry",
            "data": {
                "message_id": "<job-4.report@minwon.example>",
                "to": "desk@minwon.example",
                "dept": "민원과",
                "officer": "담당자",
                "status": "queued",
                "receipt_no": None,
                "attempts": 3,
            },
            "failure": "MAIL_SEND_FAILED",
            "incident_id": "inc-mail-down-4",
        }

    monkeypatch.setattr(agents, "call", fake_call)
    res = client.post("/jobs/job-4/send", json={"title": "제목", "body": "본문"})
    assert res.status_code == 200
    assert call_count == 3
    data = res.json()
    assert data["status"] == "retry"
    assert data["mail"]["status"] == "queued"
    assert data["failure"] == "MAIL_SEND_FAILED"

    events = [e for e in db.timeline if e["job_id"] == "job-4"]
    assert len(events) == 3
    assert all(e["event_type"] == "retry" for e in events)
    assert [e["attempt"] for e in events] == [1, 2, 3]


# 5. SERVICE_UNAVAILABLE도 최대 3회이며 500 오류가 발생하지 않음
def test_send_job_service_unavailable_stops_at_max_3_without_500(client, db, monkeypatch):
    db.jobs["job-5"] = make_success_job("job-5")
    call_count = 0

    async def fake_call(handoff: Handoff):
        nonlocal call_count
        call_count += 1
        # agents.call returns this on httpx error
        return {
            "task_id": handoff.task_id,
            "author": "backend",
            "status": "retry",
            "data": {},
            "failure": "SERVICE_UNAVAILABLE",
            "incident_id": None,
        }

    monkeypatch.setattr(agents, "call", fake_call)
    res = client.post("/jobs/job-5/send", json={"title": "제목", "body": "본문"})
    assert res.status_code == 200
    assert call_count == 3
    data = res.json()
    assert data["status"] == "retry"
    assert data["mail"] == {}
    assert data["failure"] == "SERVICE_UNAVAILABLE"

    # Target must be recorded as 'mailer', not 'backend'
    incident = next((i for i in db.incidents if i["job_id"] == "job-5"), None)
    assert incident is not None
    assert incident["target"] == "mailer"
    assert incident["fault"] == "SERVICE_UNAVAILABLE"
    assert incident["result"] == "failed"


# 6. MAIL_BLOCKED는 한 번만 호출하고 failed timeline 기록
def test_send_job_mail_blocked_calls_once_and_records_failed(client, db, monkeypatch):
    db.jobs["job-6"] = make_success_job("job-6")
    call_count = 0

    async def fake_call(handoff: Handoff):
        nonlocal call_count
        call_count += 1
        return {
            "task_id": handoff.task_id,
            "author": "mailer",
            "status": "failed",
            "data": {
                "message_id": None,
                "to": "officer@real.gov.kr",
                "dept": "민원과",
                "officer": "담당자",
                "status": "blocked",
                "receipt_no": None,
                "attempts": 0,
            },
            "failure": "MAIL_BLOCKED",
            "incident_id": None,
        }

    monkeypatch.setattr(agents, "call", fake_call)
    res = client.post("/jobs/job-6/send", json={"title": "제목", "body": "본문"})
    assert res.status_code == 200
    assert call_count == 1
    data = res.json()
    assert data["status"] == "failed"
    assert data["mail"]["status"] == "blocked"
    assert data["failure"] == "MAIL_BLOCKED"

    events = [e for e in db.timeline if e["job_id"] == "job-6"]
    assert len(events) == 1
    assert events[0]["event_type"] == "failed"
    assert events[0]["attempt"] == 1
    assert db.jobs["job-6"]["state"]["mail"]["status"] == "failed"
    incident = next((i for i in db.incidents if i["job_id"] == "job-6"), None)
    assert incident is not None
    assert incident["target"] == "mailer"
    assert incident["fault"] == "MAIL_BLOCKED"
    assert incident["result"] == "failed"
    assert events[0]["payload"]["incident_id"] == incident["id"]


# 7. incident_id가 있는 장애의 detected → recovered 흐름
def test_send_job_incident_detected_then_recovered(client, db, monkeypatch):
    db.jobs["job-7"] = make_success_job("job-7")
    calls = 0

    async def fake_call(handoff: Handoff):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "task_id": handoff.task_id,
                "author": "mailer",
                "status": "retry",
                "data": {
                    "message_id": "<job-7.report@minwon.example>",
                    "to": "desk@minwon.example",
                    "dept": "민원과",
                    "officer": "담당자",
                    "status": "queued",
                    "receipt_no": None,
                    "attempts": 3,
                },
                "failure": "MAIL_SEND_FAILED",
                "incident_id": "inc-7",
            }
        return {
            "task_id": handoff.task_id,
            "author": "mailer",
            "status": "success",
            "data": {
                "message_id": "<job-7.report@minwon.example>",
                "to": "desk@minwon.example",
                "dept": "민원과",
                "officer": "담당자",
                "status": "sent",
                "receipt_no": "2026-1009-0007",
                "attempts": 1,
            },
            "failure": None,
            "incident_id": None,
        }

    monkeypatch.setattr(agents, "call", fake_call)
    res = client.post("/jobs/job-7/send", json={"title": "제목", "body": "본문"})
    assert res.status_code == 200

    incident = next((i for i in db.incidents if i["id"] == "inc-7"), None)
    assert incident is not None
    assert incident["result"] == "recovered"
    assert incident["recovered_at"] is not None


# 8. queued 발송은 실제 재발송까지 detected 유지
def test_send_job_incident_waits_for_outbox_after_max_attempts(client, db, monkeypatch):
    db.jobs["job-8"] = make_success_job("job-8")

    async def fake_call(handoff: Handoff):
        return {
            "task_id": handoff.task_id,
            "author": "mailer",
            "status": "retry",
            "data": {
                "message_id": "<job-8.report@minwon.example>",
                "to": "desk@minwon.example",
                "dept": "민원과",
                "officer": "담당자",
                "status": "queued",
                "receipt_no": None,
                "attempts": 3,
            },
            "failure": "MAIL_SEND_FAILED",
            "incident_id": "inc-8",
        }

    monkeypatch.setattr(agents, "call", fake_call)
    res = client.post("/jobs/job-8/send", json={"title": "제목", "body": "본문"})
    assert res.status_code == 200

    incident = next((i for i in db.incidents if i["id"] == "inc-8"), None)
    assert incident is not None
    assert incident["result"] == "detected"
    assert incident["recovered_at"] is None


# 9. jobs.state의 기존 필드가 보존되고 mail만 추가됨
def test_send_job_preserves_existing_state_fields_and_adds_mail(client, db, monkeypatch):
    db.jobs["job-9"] = make_success_job("job-9", custom_key="custom_val", verification={"done": True})

    async def fake_call(handoff: Handoff):
        return {
            "task_id": handoff.task_id,
            "author": "mailer",
            "status": "success",
            "data": {
                "message_id": "<job-9.report@minwon.example>",
                "to": "desk@minwon.example",
                "dept": "민원과",
                "officer": "담당자",
                "status": "sent",
                "receipt_no": "2026-1009-0009",
                "attempts": 1,
            },
            "failure": None,
            "incident_id": None,
        }

    monkeypatch.setattr(agents, "call", fake_call)
    res = client.post("/jobs/job-9/send", json={"title": "제목", "body": "본문"})
    assert res.status_code == 200

    saved_state = db.jobs["job-9"]["state"]
    assert "result" in saved_state
    assert "classification" in saved_state
    assert "confirm_needed" in saved_state
    assert saved_state["custom_key"] == "custom_val"
    assert saved_state["verification"] == {"done": True}
    assert "mail" in saved_state
    assert saved_state["mail"]["data"]["receipt_no"] == "2026-1009-0009"


def test_jsonb_merge_semantics_preserves_nested_state_fields():
    from psycopg.types.json import Jsonb
    original_state = {
        "result": {"category": "streetlight", "channels": [{"id": "c1"}]},
        "classification": {"category": "streetlight", "confidence": 0.9},
        "confirm_needed": [],
        "custom_info": {"extra": 123},
    }
    mail_update = {"mail": {"status": "sent", "receipt_no": "2026-1009-0099"}}
    merged = {**original_state, **mail_update}
    assert merged["result"] == original_state["result"]
    assert merged["classification"] == original_state["classification"]
    assert merged["confirm_needed"] == []
    assert merged["custom_info"] == {"extra": 123}
    assert merged["mail"] == mail_update["mail"]
    jsonb_obj = Jsonb(mail_update)
    assert jsonb_obj.obj == mail_update


# 10. 존재하지 않는 작업은 404
def test_send_job_not_found_returns_404(client, db):
    res = client.post("/jobs/job-non-existent/send", json={"title": "제목", "body": "본문"})
    assert res.status_code == 404


# 11. 미완료 작업 또는 채널 없는 작업은 409
@pytest.mark.parametrize("status,state", [
    ("running", {"result": {"channels": [{"channel_id": "c1"}]}}),
    ("needs_user", {"result": {"channels": [{"channel_id": "c1"}]}}),
    ("failed", {"result": {"channels": [{"channel_id": "c1"}]}}),
    ("success", {}),
    ("success", {"result": {}}),
    ("success", {"result": {"channels": []}}),
])
def test_send_job_unfinished_or_no_channels_returns_409(client, db, status, state):
    db.jobs["job-invalid"] = {
        "id": "job-invalid",
        "text": "미완료 민원",
        "status": status,
        "state": state,
        "created_at": datetime.now(timezone.utc),
        "completed_at": None,
    }
    res = client.post("/jobs/job-invalid/send", json={"title": "제목", "body": "본문"})
    assert res.status_code == 409


# 12. 빈 값, 공백 값, 길이 초과 title/body는 422이며 부작용 없음
@pytest.mark.parametrize("payload", [
    {"title": "", "body": "유효한 본문"},
    {"title": "   ", "body": "유효한 본문"},
    {"title": "a" * 201, "body": "유효한 본문"},
    {"title": "유효한 제목", "body": ""},
    {"title": "유효한 제목", "body": "   "},
    {"title": "유효한 제목", "body": "a" * 5001},
    {"title": "유효한 제목"},
    {"body": "유효한 본문"},
])
def test_send_job_validation_errors_return_422_without_side_effects(client, db, monkeypatch, payload):
    db.jobs["job-val"] = make_success_job("job-val")
    agent_called = False

    async def fake_call(*args, **kwargs):
        nonlocal agent_called
        agent_called = True
        raise AssertionError("에이전트가 호출되면 안 됩니다")

    monkeypatch.setattr(agents, "call", fake_call)
    res = client.post("/jobs/job-val/send", json=payload)
    assert res.status_code == 422
    assert not agent_called
    assert len(db.timeline) == 0


# 13. 같은 job은 모든 Handoff에서 동일한 task_id 사용
def test_send_job_same_task_id_used_across_all_handoffs(client, db, monkeypatch):
    db.jobs["job-13"] = make_success_job("job-13")
    task_ids = []

    async def fake_call(handoff: Handoff):
        task_ids.append(handoff.task_id)
        if len(task_ids) == 1:
            return {
                "task_id": handoff.task_id,
                "author": "mailer",
                "status": "retry",
                "data": {
                    "message_id": "<job-13.report@minwon.example>",
                    "to": "desk@minwon.example",
                    "dept": "민원과",
                    "officer": "담당자",
                    "status": "queued",
                    "receipt_no": None,
                    "attempts": 3,
                },
                "failure": "MAIL_SEND_FAILED",
                "incident_id": "inc-13",
            }
        return {
            "task_id": handoff.task_id,
            "author": "mailer",
            "status": "success",
            "data": {
                "message_id": "<job-13.report@minwon.example>",
                "to": "desk@minwon.example",
                "dept": "민원과",
                "officer": "담당자",
                "status": "sent",
                "receipt_no": "2026-1009-0013",
                "attempts": 1,
            },
            "failure": None,
            "incident_id": None,
        }

    monkeypatch.setattr(agents, "call", fake_call)
    res = client.post("/jobs/job-13/send", json={"title": "제목", "body": "본문"})
    assert res.status_code == 200
    assert len(task_ids) == 2
    assert all(tid == "job-13" for tid in task_ids)


# 14. mailer 응답이 StageResult 계약을 위반하면 조용히 성공 처리하지 않음
def test_send_job_invalid_mailer_response_returns_502(client, db, monkeypatch):
    db.jobs["job-14"] = make_success_job("job-14")

    async def fake_call(handoff: Handoff):
        # 계약 위반 응답 (필수 필드 결여, status 오류 등)
        return {"task_id": handoff.task_id, "status": "not_a_valid_status"}

    monkeypatch.setattr(agents, "call", fake_call)
    response = client.post("/jobs/job-14/send", json={"title": "제목", "body": "본문"})
    assert response.status_code == 502
    assert response.json() == {"detail": "가상 발송 서비스 응답이 계약과 맞지 않습니다"}
    assert not db.timeline
    assert "mail" not in db.jobs["job-14"]["state"]


# 15. 기존 /jobs/{id}/timeline에서 mailer 이벤트가 정상 JSON으로 반환됨
def test_timeline_returns_mailer_event_with_valid_json(client, db, monkeypatch):
    db.jobs["job-15"] = make_success_job("job-15")

    async def fake_call(handoff: Handoff):
        return {
            "task_id": handoff.task_id,
            "author": "mailer",
            "status": "success",
            "data": {
                "message_id": "<job-15.report@minwon.example>",
                "to": "desk@minwon.example",
                "dept": "국민신문고 민원처리과(가상)",
                "officer": "김가상 주무관",
                "status": "sent",
                "receipt_no": "2026-1009-0015",
                "attempts": 1,
            },
            "failure": None,
            "incident_id": None,
        }

    monkeypatch.setattr(agents, "call", fake_call)
    send_res = client.post("/jobs/job-15/send", json={"title": "제목", "body": "본문"})
    assert send_res.status_code == 200

    timeline_res = client.get("/jobs/job-15/timeline")
    assert timeline_res.status_code == 200
    events = timeline_res.json()
    assert len(events) >= 1
    mailer_event = next(e for e in events if e["agent"] == "mailer")
    assert mailer_event["event_type"] == "success"
    assert isinstance(mailer_event["payload"], dict)
    assert mailer_event["payload"]["data"]["receipt_no"] == "2026-1009-0015"


@pytest.mark.parametrize("status", ["queued", "sent"])
def test_outbox_recovery_records_timeline_state_and_actual_send_time(monkeypatch, status):
    import asyncio
    import httpx
    from app.services import dispatch
    calls = []
    original = httpx.AsyncClient
    sent_at = "2026-10-12T00:00:20+00:00"
    item = {"status": status, "incident_id": "inc-mail", "task_id": "job-mail:1", "attempts": 14,
            "message_id": "<job-mail:1.report@minwon.example>", "to": "desk@minwon.example", "dept": "가상 민원실",
            "officer": "가상 담당자", "receipt_no": "2026-1012-0003", "sent_at": sent_at if status == "sent" else None}
    def respond(request):
        assert request.url.path == "/outbox"
        return httpx.Response(200, json={"items": [item]})
    queued = {"task_id": "job-mail:1", "author": "mailer", "status": "retry", "data": {"status": "queued"},
              "failure": "MAIL_SEND_FAILED", "incident_id": "inc-mail"}
    async def query(sql, params, fetch=False):
        calls.append((sql, params))
        if sql.startswith("SELECT * FROM jobs"):
            return [{"id": "job-mail", "state": {"results": [{}, {}], "mails": {"1": queued}}}]
    monkeypatch.setattr(dispatch.httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    monkeypatch.setattr(dispatch.store, "query", query)
    asyncio.run(dispatch.reconcile_outbox())
    if status == "queued":
        assert calls == []
        return
    kinds = [sql.split("(")[0].split(" SET ")[0] for sql, _ in calls]
    assert kinds == ["SELECT * FROM jobs WHERE id=%s", "INSERT INTO timeline_events", "UPDATE jobs", "UPDATE incidents"]
    event = calls[1][1]
    assert (event[0], event[1], event[2], event[3]) == ("job-mail", "mailer", "success", 14)
    assert event[4].obj["data"]["receipt_no"] == "2026-1012-0003" and event[4].obj["status"] == "success"
    assert event[4].obj["data"]["evidence"]["fixed"] == f"편지함 대기 후 재발송 성공 {str(sent_at)[11:19]} · 접수번호 2026-1012-0003"
    assert calls[2][1][0].obj == {"1": event[4].obj}
    assert calls[3][1] == (sent_at, "inc-mail", "job-mail")
    assert "detected_at IS NOT NULL" in calls[3][0]


def test_outbox_recovery_skips_jobs_already_sent(monkeypatch):
    import asyncio
    import httpx
    from app.services import dispatch
    calls = []
    original = httpx.AsyncClient
    item = {"status": "sent", "incident_id": None, "task_id": "job-ok", "attempts": 1, "sent_at": "2026-10-12T00:00:20+00:00"}
    monkeypatch.setattr(dispatch.httpx, "AsyncClient", lambda **kw: original(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"items": [item]})), **kw))
    async def query(sql, params, fetch=False):
        calls.append(sql)
        if sql.startswith("SELECT * FROM jobs"):
            return [{"id": "job-ok", "state": {"mail": {"status": "success", "data": {"status": "sent"}}}}]
    monkeypatch.setattr(dispatch.store, "query", query)
    asyncio.run(dispatch.reconcile_outbox())
    assert calls == ["SELECT * FROM jobs WHERE id=%s"]


@pytest.mark.parametrize("citizen", [None, {"name": "김민원", "email": "minwon.kim@minwon.example"}])
def test_send_citizen_reaches_mailer_and_default_stays_optional(client, db, monkeypatch, citizen):
    db.jobs["job-account"] = make_success_job("job-account")
    captured = []

    async def call(handoff):
        captured.append(handoff.inputs)
        from app.core.config import CONTRACTS
        result = json.loads((CONTRACTS / "samples/mailer_output.json").read_text())
        return {**result, "task_id": handoff.task_id}

    monkeypatch.setattr(agents, "call", call)
    draft = {"title": "가로등 확인", "body": "상도동 가로등을 확인해 주세요"}
    if citizen:
        draft["citizen"] = citizen
    assert client.post("/jobs/job-account/send", json=draft).status_code == 200
    if citizen:
        assert captured[0]["citizen"] == citizen
    else:
        assert "citizen" not in captured[0]


@pytest.mark.parametrize("email", ["person@gmail.com", "a@minwon.example.com", "a@b.com@minwon.example", "a@minwon.example\n"])
def test_send_rejects_nonvirtual_citizen_before_dispatch(client, db, email):
    response = client.post("/jobs/job-account/send", json={"title": "제목", "body": "본문",
                           "citizen": {"name": "김민원", "email": email}})
    assert response.status_code == 422
    assert not db.queries
