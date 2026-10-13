import json
import pytest
from fastapi.testclient import TestClient
from app.clients import agents, store
from app.core.config import CONTRACTS
from app.main import app


class FakeDB:
    """incidents 및 supervisor 동작 검증을 위한 FakeDB."""
    def __init__(self):
        self.jobs = {}
        self.timeline = []
        self.incidents = {}
        self.sql_history = []

    async def query(self, sql, params=(), fetch=False):
        self.sql_history.append((sql, params))
        if sql.startswith("INSERT INTO jobs(id,text,status)"):
            job_id, text, status = params
            self.jobs[job_id] = {"id": job_id, "text": text, "status": status, "state": {}, "completed_at": None}
        elif sql.startswith("UPDATE jobs SET status=%s,state=%s,completed_at=CASE WHEN %s IN ('success','failed') THEN now() ELSE NULL END WHERE id=%s"):
            status, state, case_status, job_id = params
            self.jobs[job_id].update(status=status, state=json.loads(json.dumps(state.obj)),
                                     completed_at="now()" if case_status in ("success", "failed") else None)
        elif sql.startswith("INSERT INTO timeline_events(job_id,agent,event_type,attempt,payload)"):
            job_id, agent, event_type, attempt, payload = params
            self.timeline.append({"id": len(self.timeline) + 1, "job_id": job_id, "agent": agent,
                                  "event_type": event_type, "attempt": attempt, "payload": payload.obj})
        elif sql.startswith("INSERT INTO incidents (id, job_id, target, fault, injected_at, detected_at, recovered_at, human_intervention, result) VALUES (%s, NULL, %s, %s, %s, NULL, NULL, false, 'injected')"):
            incident_id, target, fault, injected_at = params
            if incident_id not in self.incidents:
                self.incidents[incident_id] = {
                    "id": incident_id, "job_id": None, "target": target, "fault": fault,
                    "injected_at": injected_at, "detected_at": None, "recovered_at": None,
                    "human_intervention": False, "result": "injected", "run_id": None
                }
            else:
                if self.incidents[incident_id]["injected_at"] is None:
                    self.incidents[incident_id]["injected_at"] = injected_at
        elif sql.startswith("INSERT INTO incidents (id, job_id, target, fault, injected_at, detected_at, recovered_at, human_intervention, result) VALUES (%s, %s, %s, %s, NULL, COALESCE(%s::timestamptz, now()), NULL, false, %s)"):
            incident_id, job_id, target, fault, detected_at, res_status = params
            if incident_id not in self.incidents:
                self.incidents[incident_id] = {
                    "id": incident_id, "job_id": job_id, "target": target, "fault": fault,
                    "injected_at": None, "detected_at": detected_at or "now()", "recovered_at": None,
                    "human_intervention": False, "result": res_status, "run_id": None
                }
            else:
                row = self.incidents[incident_id]
                if row["job_id"] is None:
                    row["job_id"] = job_id
                if row["target"] is None:
                    row["target"] = target
                if row["fault"] is None:
                    row["fault"] = fault
                if row["detected_at"] is None:
                    row["detected_at"] = detected_at or "now()"
                if row["result"] not in ("recovered", "failed"):
                    row["result"] = res_status
        elif sql.startswith("UPDATE incidents SET recovered_at = COALESCE(recovered_at, now()), result = 'recovered', human_intervention = false WHERE id = %s"):
            incident_id = params[0]
            if incident_id in self.incidents:
                row = self.incidents[incident_id]
                if row["recovered_at"] is None:
                    row["recovered_at"] = "now()"
                row["result"] = "recovered"
                row["human_intervention"] = False
        elif sql.startswith("UPDATE incidents SET result = 'failed' WHERE id = %s AND (result IS NULL OR result != 'recovered')"):
            incident_id = params[0]
            if incident_id in self.incidents:
                row = self.incidents[incident_id]
                if row["result"] != "recovered":
                    row["result"] = "failed"
        elif sql.startswith("UPDATE incidents SET job_id=%s, detected_at=now()"):
            job_id, target = params
            pending = [row for row in self.incidents.values()
                       if row["target"] == target and row.get("injected_at")
                       and row["detected_at"] is None and row["job_id"] is None
                       and row["result"] == "injected"]
            if not pending:
                return []
            row = max(pending, key=lambda item: (item["injected_at"], item["id"]))
            row.update(job_id=job_id, detected_at="now()", result="detected")
            return [{"id": row["id"]}]
        elif sql.startswith("UPDATE incidents SET result=%s, recovered_at="):
            result, incident_id = params
            row = self.incidents.get(incident_id)
            if row:
                row.update(result=result, recovered_at=row.get("recovered_at") or "now()", human_intervention=False)
            return None
        elif sql == "SELECT * FROM jobs WHERE id=%s":
            return [self.jobs[params[0]]] if params[0] in self.jobs else []
        elif sql == "SELECT * FROM timeline_events WHERE job_id=%s ORDER BY id":
            return sorted((r for r in self.timeline if r["job_id"] == params[0]), key=lambda r: r["id"])
        elif sql == ("SELECT * FROM incidents "
                     "ORDER BY COALESCE(detected_at, injected_at) DESC NULLS LAST, id DESC"):
            return list(self.incidents.values())
        else:
            raise AssertionError("예상하지 못한 SQL: " + sql)


def _output(name, task_id):
    path = CONTRACTS / "samples" / f"{name}_output.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["task_id"] = task_id
    return data


def _setup_env(monkeypatch, custom_call):
    db = FakeDB()
    monkeypatch.setattr(store, "query", db.query)
    monkeypatch.setattr(agents, "call", custom_call)
    return db


# 3. retry 장애 감지 테스트
def test_retry_incident_detected_and_timeline_recorded(monkeypatch):
    calls = []
    async def call(handoff):
        calls.append(handoff)
        if handoff.to == "classifier":
            return {
                "task_id": handoff.task_id, "author": "classifier", "status": "retry",
                "data": {}, "failure": "LLM_OUTPUT_INVALID", "incident_id": "incident-llm-1"
            }
        return _output(f"{handoff.to}_streetlight", handoff.task_id)

    db = _setup_env(monkeypatch, call)
    client = TestClient(app)
    res = client.post("/jobs?wait=true", json={"text": "가로등 고장 신고"})
    assert res.status_code == 201

    assert "incident-llm-1" in db.incidents
    inc = db.incidents["incident-llm-1"]
    assert inc["id"] == "incident-llm-1"
    assert inc["job_id"] == res.json()["id"]
    assert inc["target"] == "classifier"
    assert inc["fault"] == "LLM_OUTPUT_INVALID"
    assert inc["detected_at"] == "now()"
    assert inc["result"] == "failed"
    assert inc["recovered_at"] is None

    timeline = db.timeline
    assert len(timeline) == 3
    assert all(e["agent"] == "classifier" and e["event_type"] == "retry" for e in timeline)


# 4. retry 후 자동 복구 테스트
def test_retry_then_auto_recovered(monkeypatch):
    responses = [
        {"author": "classifier", "status": "retry", "data": {}, "failure": "LLM_OUTPUT_INVALID", "incident_id": "incident-recover-1"},
        _output("classifier_streetlight", "temp")
    ]
    async def call(handoff):
        if handoff.to == "classifier":
            out = dict(responses.pop(0))
            out["task_id"] = handoff.task_id
            return out
        name = f"{handoff.to}_streetlight" + ("_draft" if handoff.inputs.get("mode") == "draft" else "")
        return _output(name, handoff.task_id)

    db = _setup_env(monkeypatch, call)
    client = TestClient(app)
    res = client.post("/jobs?wait=true", json={"text": "가로등 고장 신고"})
    assert res.status_code == 201
    assert res.json()["status"] == "success"

    assert len(db.incidents) == 1
    inc = db.incidents["incident-recover-1"]
    assert inc["result"] == "recovered"
    assert inc["recovered_at"] == "now()"
    assert inc["detected_at"] == "now()"
    assert inc["human_intervention"] is False


def test_natural_retry_without_incident_id_gets_generated_id(monkeypatch):
    responses = [
        {"author": "classifier", "status": "retry", "data": {},
         "failure": "LLM_OUTPUT_INVALID", "incident_id": None},
        _output("classifier_streetlight", "temp"),
    ]

    async def call(handoff):
        if handoff.to == "classifier":
            out = dict(responses.pop(0))
            out["task_id"] = handoff.task_id
            return out
        name = f"{handoff.to}_streetlight" + ("_draft" if handoff.inputs.get("mode") == "draft" else "")
        return _output(name, handoff.task_id)

    db = _setup_env(monkeypatch, call)
    response = TestClient(app).post("/jobs?wait=true", json={"text": "가로등 고장 신고"})

    assert response.status_code == 201
    assert response.json()["status"] == "success"
    assert len(db.incidents) == 1
    generated_id, incident = next(iter(db.incidents.items()))
    assert generated_id.startswith("incident-")
    assert incident["target"] == "classifier"
    assert incident["fault"] == "LLM_OUTPUT_INVALID"
    assert incident["result"] == "recovered"
    assert incident["recovered_at"] == "now()"


# 5. 같은 incident_id 반복 테스트
def test_same_incident_repeated_preserves_initial_detected_at(monkeypatch):
    responses = [
        {"author": "classifier", "status": "retry", "data": {}, "failure": "LLM_OUTPUT_INVALID", "incident_id": "incident-repeat-1"},
        {"author": "classifier", "status": "retry", "data": {}, "failure": "LLM_OUTPUT_INVALID", "incident_id": "incident-repeat-1"},
        _output("classifier_streetlight", "temp")
    ]
    async def call(handoff):
        if handoff.to == "classifier":
            out = dict(responses.pop(0))
            out["task_id"] = handoff.task_id
            return out
        name = f"{handoff.to}_streetlight" + ("_draft" if handoff.inputs.get("mode") == "draft" else "")
        return _output(name, handoff.task_id)

    db = _setup_env(monkeypatch, call)
    client = TestClient(app)
    res = client.post("/jobs?wait=true", json={"text": "가로등 고장 신고"})
    assert res.status_code == 201
    assert res.json()["status"] == "success"

    assert len(db.incidents) == 1
    inc = db.incidents["incident-repeat-1"]
    assert inc["result"] == "recovered"
    assert inc["detected_at"] == "now()"


# 6. 최대 3회 retry 실패 테스트
def test_max_three_retries_fails_without_fourth_call(monkeypatch):
    calls = []
    async def call(handoff):
        calls.append(handoff)
        return {
            "task_id": handoff.task_id, "author": "classifier", "status": "retry",
            "data": {}, "failure": "LLM_OUTPUT_INVALID", "incident_id": "incident-exhaust-1"
        }

    db = _setup_env(monkeypatch, call)
    client = TestClient(app)
    res = client.post("/jobs?wait=true", json={"text": "가로등 고장 신고"})
    assert res.status_code == 201
    assert res.json()["status"] == "failed"

    assert len(calls) == 3
    assert [h.attempt for h in calls] == [1, 2, 3]

    inc = db.incidents["incident-exhaust-1"]
    assert inc["result"] == "failed"
    assert inc["recovered_at"] is None


# 7. failed 응답 테스트
def test_failed_response_records_detected_and_failed_without_recovery(monkeypatch):
    async def call(handoff):
        if handoff.to == "verifier" and handoff.inputs.get("mode") == "channel":
            out = _output("verifier_link_dead", handoff.task_id)
            out["incident_id"] = "incident-deadlink-1"
            return out
        name = f"{handoff.to}_streetlight"
        return _output(name, handoff.task_id)

    db = _setup_env(monkeypatch, call)
    client = TestClient(app)
    res = client.post("/jobs?wait=true", json={"text": "가로등 고장 신고"})
    assert res.status_code == 201
    assert res.json()["status"] == "failed"

    assert "incident-deadlink-1" in db.incidents
    inc = db.incidents["incident-deadlink-1"]
    assert inc["target"] == "verifier"
    assert inc["fault"] == "LINK_DEAD"
    assert inc["detected_at"] == "now()"
    assert inc["recovered_at"] is None
    assert inc["result"] == "failed"


# 8. STALE_INFO 테스트
def test_stale_info_records_degraded_with_handled_time(monkeypatch):
    async def call(handoff):
        if handoff.to == "verifier" and handoff.inputs.get("mode") == "channel":
            data = _output("verifier_streetlight", handoff.task_id)["data"]
            return {
                "task_id": handoff.task_id, "author": "verifier", "status": "success",
                "data": data, "failure": "STALE_INFO", "incident_id": "incident-stale-1"
            }
        name = f"{handoff.to}_streetlight" + ("_draft" if handoff.inputs.get("mode") == "draft" else "")
        return _output(name, handoff.task_id)

    db = _setup_env(monkeypatch, call)
    client = TestClient(app)
    res = client.post("/jobs?wait=true", json={"text": "가로등 고장 신고"})
    assert res.status_code == 201
    job = res.json()
    assert job["status"] == "success"
    assert "신고 창구 확인일이 오래되었습니다" in job["state"]["confirm_needed"]

    inc = db.incidents["incident-stale-1"]
    assert inc["result"] == "degraded"
    assert inc["detected_at"] == "now()"
    assert inc["recovered_at"] == "now()"
    assert inc["human_intervention"] is False


# 9. 정상 성공 테스트 (incidents 쓰기 없음)
def test_clean_success_creates_no_incidents(monkeypatch):
    async def call(handoff):
        name = f"{handoff.to}_streetlight" + ("_draft" if handoff.inputs.get("mode") == "draft" else "")
        return _output(name, handoff.task_id)

    db = _setup_env(monkeypatch, call)
    client = TestClient(app)
    res = client.post("/jobs?wait=true", json={"text": "가로등 고장 신고"})
    assert res.status_code == 201
    assert res.json()["status"] == "success"

    assert len(db.incidents) == 0
    incident_queries = [sql for sql, _ in db.sql_history if "incidents" in sql]
    assert incident_queries == []


# 9-1. NEEDS_MORE_INFO 및 OUT_OF_SCOPE: incident_id가 없으면 incidents 행을 만들지 않음
def test_needs_more_info_and_out_of_scope_without_incident_id_create_no_incidents(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(store, "query", db.query)

    # 1. NEEDS_MORE_INFO (incident_id=None)
    async def call_needs_user(handoff):
        return _output("classifier_streetlight_needs_user", handoff.task_id)

    monkeypatch.setattr(agents, "call", call_needs_user)
    client = TestClient(app)
    res1 = client.post("/jobs?wait=true", json={"text": "가로등 고장"})
    assert res1.status_code == 201
    assert res1.json()["status"] == "needs_user"
    assert len(db.incidents) == 0
    assert not any("incidents" in q[0] for q in db.sql_history)

    # 2. OUT_OF_SCOPE (incident_id=None)
    async def call_out_of_scope(handoff):
        return _output("classifier_out_of_scope", handoff.task_id)

    monkeypatch.setattr(agents, "call", call_out_of_scope)
    res2 = client.post("/jobs?wait=true", json={"text": "범위 밖 민원"})
    assert res2.status_code == 201
    assert res2.json()["status"] == "failed"
    assert len(db.incidents) == 0
    assert not any("incidents" in q[0] for q in db.sql_history)


# 10. SERVICE_UNAVAILABLE 테스트
def test_service_unavailable_without_incident_id_tracked_and_recovered(monkeypatch):
    responses = [
        {"author": "backend", "status": "retry", "data": {}, "failure": "SERVICE_UNAVAILABLE", "incident_id": None},
        {"author": "backend", "status": "retry", "data": {}, "failure": "SERVICE_UNAVAILABLE", "incident_id": None},
        _output("classifier_streetlight", "temp")
    ]
    async def call(handoff):
        if handoff.to == "classifier":
            out = dict(responses.pop(0))
            out["task_id"] = handoff.task_id
            return out
        name = f"{handoff.to}_streetlight" + ("_draft" if handoff.inputs.get("mode") == "draft" else "")
        return _output(name, handoff.task_id)

    db = _setup_env(monkeypatch, call)
    client = TestClient(app)
    res = client.post("/jobs?wait=true", json={"text": "가로등 고장 신고"})
    assert res.status_code == 201
    assert res.json()["status"] == "success"

    assert len(db.incidents) == 1
    inc_id, inc = next(iter(db.incidents.items()))
    assert inc_id.startswith("incident-")
    assert inc["target"] == "classifier"
    assert inc["fault"] == "SERVICE_UNAVAILABLE"
    assert inc["injected_at"] is None
    assert inc["result"] == "recovered"
    assert inc["recovered_at"] == "now()"

    cls_events = [e for e in db.timeline if e["agent"] == "classifier" and e["event_type"] == "retry"]
    assert len(cls_events) == 2
    assert all(e["payload"]["incident_id"] == inc_id for e in cls_events)
    assert all(e["payload"]["author"] == "backend" for e in cls_events)



# 11. chaos PUT 기록 테스트
def test_chaos_put_success_records_injected(monkeypatch):
    import httpx
    db = FakeDB()
    monkeypatch.setattr(store, "query", db.query)

    def mock_send(request):
        assert request.method == "PUT"
        assert "flags/verifier" in str(request.url)
        return httpx.Response(200, json={"status": "ok"})

    transport = httpx.MockTransport(mock_send)
    orig_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: orig_client(transport=transport, **{k: v for k, v in kw.items() if k != "transport"}))

    client = TestClient(app)
    body = {
        "incident_id": "incident-demo-put-1",
        "kind": "link_dead",
        "injected_at": "2026-10-08T10:00:00+09:00",
        "params": {}
    }
    res = client.put("/chaos/flags/verifier", json=body)
    assert res.status_code == 200

    assert "incident-demo-put-1" in db.incidents
    inc = db.incidents["incident-demo-put-1"]
    assert inc["id"] == "incident-demo-put-1"
    assert inc["job_id"] is None
    assert inc["target"] == "verifier"
    assert inc["fault"] == "link_dead"
    assert inc["injected_at"] == "2026-10-08T10:00:00+09:00"
    assert inc["detected_at"] is None
    assert inc["recovered_at"] is None
    assert inc["human_intervention"] is False
    assert inc["result"] == "injected"


# 12. chaos PUT 실패 테스트
def test_chaos_put_failure_does_not_record_injected(monkeypatch):
    import httpx
    db = FakeDB()
    monkeypatch.setattr(store, "query", db.query)

    def mock_send(request):
        return httpx.Response(502, json={"error": "chaos unreachable"})

    transport = httpx.MockTransport(mock_send)
    orig_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: orig_client(transport=transport, **{k: v for k, v in kw.items() if k != "transport"}))

    client = TestClient(app)
    body = {
        "incident_id": "incident-failed-put-1",
        "kind": "link_dead",
        "injected_at": "2026-10-08T10:00:00+09:00",
        "params": {}
    }
    res = client.put("/chaos/flags/verifier", json=body)
    assert res.status_code == 502
    assert res.json() == {"error": "chaos unreachable"}
    assert "incident-failed-put-1" not in db.incidents
    assert len(db.incidents) == 0


# 주입 -> 감지 -> 복구 전체 사이클 테스트
def test_full_injected_detected_recovered_lifecycle(monkeypatch):
    import httpx
    db = FakeDB()
    monkeypatch.setattr(store, "query", db.query)

    def mock_chaos(request):
        return httpx.Response(200, json={"status": "ok"})
    transport = httpx.MockTransport(mock_chaos)
    orig_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: orig_client(transport=transport, **{k: v for k, v in kw.items() if k != "transport"}))

    client = TestClient(app)
    client.put("/chaos/flags/classifier", json={
        "incident_id": "incident-full-cycle",
        "kind": "llm_invalid",
        "injected_at": "2026-10-08T12:00:00+09:00",
        "params": {}
    })
    assert db.incidents["incident-full-cycle"]["result"] == "injected"
    assert db.incidents["incident-full-cycle"]["target"] == "classifier"
    assert db.incidents["incident-full-cycle"]["fault"] == "llm_invalid"

    responses = [
        {"author": "classifier", "status": "retry", "data": {}, "failure": "LLM_OUTPUT_INVALID", "incident_id": "incident-full-cycle"},
        _output("classifier_streetlight", "temp")
    ]
    async def call(handoff):
        if handoff.to == "classifier":
            out = dict(responses.pop(0))
            out["task_id"] = handoff.task_id
            return out
        name = f"{handoff.to}_streetlight" + ("_draft" if handoff.inputs.get("mode") == "draft" else "")
        return _output(name, handoff.task_id)

    monkeypatch.setattr(agents, "call", call)
    res = client.post("/jobs?wait=true", json={"text": "가로등 고장"})
    assert res.status_code == 201
    assert res.json()["status"] == "success"

    inc = db.incidents["incident-full-cycle"]
    assert inc["id"] == "incident-full-cycle"
    assert inc["target"] == "classifier"
    assert inc["fault"] == "llm_invalid"
    assert inc["injected_at"] == "2026-10-08T12:00:00+09:00"
    assert inc["detected_at"] == "now()"
    assert inc["recovered_at"] == "now()"
    assert inc["result"] == "recovered"
    assert inc["human_intervention"] is False


def test_redteam_receipt_records_injected_without_rereading_consumable_flag(monkeypatch):
    import httpx
    from unittest.mock import AsyncMock
    from app.services import incidents
    receipt = dict(target='classifier', kind='delay', reason='timeout', incident_id='redteam-receipt', injected_at='2026-10-12T05:00:00Z')
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=receipt)), **kwargs))
    recorded = AsyncMock()
    monkeypatch.setattr(incidents, 'record_injected', recorded)
    assert TestClient(app).post('/chaos/run', json={}).json() == receipt
    recorded.assert_awaited_once_with('redteam-receipt', 'classifier', 'delay', receipt['injected_at'])


def test_incident_list_carries_server_cutoff_without_changing_storage(monkeypatch):
    from app.services import incidents
    monkeypatch.setattr(incidents, 'METRICS_SINCE', '2026-10-13T00:00:00Z')
    row = dict(id='natural', injected_at=None, detected_at='2026-10-12T05:00:00Z')
    assert incidents.normalize_incident_for_api(row)['metrics_since'] == '2026-10-13T00:00:00Z'
    assert 'metrics_since' not in row
