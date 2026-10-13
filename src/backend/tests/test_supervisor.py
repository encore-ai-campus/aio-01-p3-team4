import json
import pytest
from fastapi.testclient import TestClient
from app.clients import agents, store
from app.core.config import CONTRACTS
from app.main import app
from app.schemas.contract import validate


def test_agent_timeout_matches_project_policy():
    import re
    assert agents.AGENT_TIMEOUT_SEC == 15
    catalogue = (CONTRACTS.parent / "src/frontend/src/lib/failures.ts").read_text()
    delay = int(re.search(r"kind: 'delay'.*seconds: (\d+)", catalogue).group(1))
    assert delay == 20 and delay > agents.AGENT_TIMEOUT_SEC

@pytest.mark.parametrize("category", ["streetlight", "road_damage", "illegal_dumping"])
def test_http_pipeline_and_timeline(category, monkeypatch):
    timeline, requests = [], []
    async def query(sql, params=(), fetch=False):
        if sql.startswith("INSERT INTO timeline_events"):
            timeline.append(params)
    def respond(request):
        assert request.extensions["timeout"]["read"] == 15
        handoff = json.loads(request.content)
        validate("handoff", handoff)
        requests.append(handoff["to"])
        name = handoff["to"] + "_" + category
        if handoff["inputs"].get("mode") == "draft":
            name += "_draft"
        result = json.loads((CONTRACTS / "samples" / f"{name}_output.json").read_text(encoding="utf-8"))
        result["task_id"] = handoff["task_id"]
        return agents.httpx.Response(200, json=result)
    client_class = agents.httpx.AsyncClient
    monkeypatch.setattr(agents.httpx, "AsyncClient", lambda **kw: client_class(transport=agents.httpx.MockTransport(respond), **kw))
    monkeypatch.setattr(agents, "REDIS_URL", "")
    monkeypatch.setattr(store, "query", query)
    sample = json.loads((CONTRACTS / "samples" / f"classifier_{category}_input.json").read_text(encoding="utf-8"))
    response = TestClient(app).post("/jobs?wait=true", json=sample["inputs"])
    assert response.status_code == 201
    job = response.json()
    assert job["status"] == "success"
    assert job["state"]["result"]["category"] == category
    assert requests == ["classifier", "locator", "verifier", "writer", "verifier"]
    assert len(timeline) == 5 and all(p[0] == job["id"] for p in timeline)
    validate("final", job["state"]["result"])
    assert job["state"]["result"]["confirm_needed"] == []


def test_input_limit_prevents_agent_calls(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("500자 초과 입력은 DB·에이전트 호출 금지")
    monkeypatch.setattr(store, "query", forbidden)
    monkeypatch.setattr(agents, "call", forbidden)
    assert TestClient(app).post("/jobs?wait=true", json={"text": "가" * 501}).status_code == 422


def test_two_question_rounds_preserve_district_for_location_only(monkeypatch):
    import asyncio
    from app.services.supervisor import run_job
    async def query(*args, **kwargs):
        pass
    async def call(handoff):
        name = handoff.to + "_streetlight"
        if handoff.to == "classifier":
            name += "_needs_user"
        if handoff.inputs.get("mode") == "draft":
            name += "_draft"
        value = json.loads((CONTRACTS / "samples" / f"{name}_output.json").read_text(encoding="utf-8"))
        value["task_id"] = handoff.task_id
        if handoff.to == "classifier":
            value["data"]["region"].update(sido="서울특별시", sigungu="동작구", dong="상도동")
        if handoff.to == "locator":
            assert handoff.inputs["region"]["sigungu"] == "동작구"
        return value
    monkeypatch.setattr(store, "query", query)
    monkeypatch.setattr(agents, "call", call)
    for round in (0, 1, 2):
        job = asyncio.run(run_job("job-limit", "위치 없음", question_round=round))
        assert job["status"] == ("needs_user" if round < 2 else "success")
    result = job["state"]["result"]
    assert result["category"] == "streetlight"
    assert any("2회" in reason for reason in result["confirm_needed"])


def test_send_calls_mailer_and_records_timeline(monkeypatch):
    final = json.loads((CONTRACTS / "samples" / "final_streetlight_output.json").read_text(encoding="utf-8"))
    job = {"id": "job-send", "text": "가로등", "status": "success", "state": {"result": final}}
    sql, calls = [], []
    async def query(statement, params=(), fetch=False):
        sql.append((statement, params))
        return [job] if fetch else None
    outputs = ["mailer_mail_down", "mailer"]
    async def call(handoff):
        calls.append((handoff.attempt, handoff.last_failure, handoff.inputs["draft"]["title"]))
        value = json.loads((CONTRACTS / "samples" / f"{outputs[len(calls) - 1]}_output.json").read_text(encoding="utf-8"))
        value["task_id"] = handoff.task_id
        return value
    monkeypatch.setattr(store, "query", query)
    monkeypatch.setattr(agents, "call", call)
    response = TestClient(app).post("/jobs/job-send/send", json={"title": "고친 제목", "body": "고친 본문"})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "success" and response.json()["mail"]["status"] == "sent"
    assert calls == [(1, None, "고친 제목"), (2, "MAIL_SEND_FAILED", "고친 제목")]
    timeline = [p for s, p in sql if s.startswith("INSERT INTO timeline_events")]
    assert [(p[1], p[2], p[3]) for p in timeline] == [("mailer", "retry", 1), ("mailer", "success", 2)]
    assert any(s.startswith("UPDATE jobs SET state") for s, _ in sql)


def test_send_rejects_unfinished_job(monkeypatch):
    async def query(statement, params=(), fetch=False):
        return [{"id": "job-x", "text": "t", "status": "needs_user", "state": {}}] if fetch else None
    async def forbidden(*args, **kwargs):
        raise AssertionError("결과 없는 작업은 발송 금지")
    monkeypatch.setattr(store, "query", query)
    monkeypatch.setattr(agents, "call", forbidden)
    assert TestClient(app).post("/jobs/job-x/send", json={"title": "t", "body": "b"}).status_code == 409


# --- 공통 단계 실행기(execute_stage) — 같은 단계 재시도 ---

def _output(name, task_id):
    value = json.loads((CONTRACTS / "samples" / f"{name}_output.json").read_text(encoding="utf-8"))
    value["task_id"] = task_id
    return value


def _fake_stage(monkeypatch, outputs):
    """agents.call이 outputs를 차례로 돌려주게 하고, 받은 Handoff·DB 문장을 기록한다(실제 DB·에이전트 없음)."""
    handoffs, sql = [], []
    async def call(handoff):
        handoffs.append(handoff)
        if len(handoffs) > len(outputs):
            raise AssertionError(f"예상보다 많이 호출됨: {len(handoffs)}번째 호출")
        value = outputs[len(handoffs) - 1]
        return _output(value, handoff.task_id) if isinstance(value, str) else {**value, "task_id": handoff.task_id}
    async def query(statement, params=(), fetch=False):
        sql.append((statement, params))
    monkeypatch.setattr(agents, "call", call)
    monkeypatch.setattr(store, "query", query)
    timeline = lambda: [p for s, p in sql if s.startswith("INSERT INTO timeline_events")]
    return handoffs, sql, timeline


def _classifier_stage(job_id="job-stage"):
    import asyncio
    from app.services.supervisor import execute_stage
    inputs = {"text": "서울 동작구 상도동 주민센터 앞 가로등 고장"}
    result = asyncio.run(execute_stage(job_id, "classifier", inputs, "민원 유형과 지역 파악", ["개인정보를 묻지 말 것"]))
    return inputs, result


def test_stage_retry_then_success(monkeypatch):
    handoffs, sql, timeline = _fake_stage(monkeypatch, ["classifier_llm_invalid", "classifier_streetlight"])
    inputs, result = _classifier_stage()
    assert len(handoffs) == 2
    assert [h.attempt for h in handoffs] == [1, 2]
    assert [h.last_failure for h in handoffs] == [None, "LLM_OUTPUT_INVALID"]
    assert all(h.task_id == "job-stage" and h.inputs == inputs and h.to == "classifier" for h in handoffs)
    rows = timeline()
    assert [(p[0], p[1], p[2], p[3]) for p in rows] == [("job-stage", "classifier", "retry", 1), ("job-stage", "classifier", "success", 2)]
    assert rows[1][4].obj == result
    assert result == _output("classifier_streetlight", "job-stage")
    assert not any(s.startswith("UPDATE jobs") for s, _ in sql)  # execute_stage는 작업 상태를 바꾸지 않음


def test_stage_stops_after_max_attempts(monkeypatch):
    from app.services.supervisor import MAX_STAGE_ATTEMPTS
    assert MAX_STAGE_ATTEMPTS == 3
    handoffs, sql, timeline = _fake_stage(monkeypatch, ["classifier_llm_invalid"] * 3)  # 4번째 호출이면 AssertionError
    _, result = _classifier_stage()
    assert len(handoffs) == 3
    assert [h.attempt for h in handoffs] == [1, 2, 3]
    assert [h.last_failure for h in handoffs] == [None, "LLM_OUTPUT_INVALID", "LLM_OUTPUT_INVALID"]
    assert [(p[1], p[2], p[3]) for p in timeline()] == [("classifier", "retry", 1), ("classifier", "retry", 2), ("classifier", "retry", 3)]
    assert result["status"] == "retry" and result["failure"] == "LLM_OUTPUT_INVALID"
    assert not any(s.startswith("UPDATE jobs") for s, _ in sql)


def test_run_job_fails_after_max_attempts_without_next_stage(monkeypatch):
    import asyncio
    from app.services.supervisor import run_job
    handoffs, sql, timeline = _fake_stage(monkeypatch, ["classifier_llm_invalid"] * 3)
    job = asyncio.run(run_job("job-limit3", "가로등 고장"))
    assert job["status"] == "failed"  # 시도 한도 초과를 success로 처리하지 않음
    assert job["state"]["result"]["confirm_needed"]
    assert "category" not in job["state"]["result"]
    assert [h.to for h in handoffs] == ["classifier"] * 3
    updates = [p for s, p in sql if s.startswith("UPDATE jobs")]
    assert len(updates) == 1 and updates[0][0] == "failed"


def test_stage_failed_is_not_retried(monkeypatch):
    import asyncio
    from app.services.supervisor import run_job
    # execute_stage가 failed를 만났을 때 attempt를 반복하지 않고 1회로 끝나는지 검증
    handoffs, sql, timeline = _fake_stage(monkeypatch, ["classifier_out_of_scope", {"author": "backend", "status": "failed", "data": {}, "failure": "INJECTION_BLOCKED", "incident_id": None}])
    _, result = _classifier_stage()
    assert len(handoffs) == 1 and handoffs[0].attempt == 1 and handoffs[0].last_failure is None
    assert result["status"] == "failed" and result["failure"] == "OUT_OF_SCOPE"
    assert [(p[1], p[2], p[3]) for p in timeline()] == [("classifier", "failed", 1)]
    # run_job도 재계획 없는 실패 시 추가 에이전트 호출 없이 1번만 호출하고 failed 종료
    job = asyncio.run(run_job("job-failed", "공격 입력"))
    assert job["status"] == "failed" and len(handoffs) == 2  # run_job에서도 추가 호출 없이 1번만
    updates = [p for s, p in sql if s.startswith("UPDATE jobs")]
    assert len(updates) == 1 and updates[0][0] == "failed"  # 중복 종료 처리 없음


def test_stage_needs_user_is_not_retried(monkeypatch):
    import asyncio
    from app.services.supervisor import run_job
    outputs = ["classifier_streetlight_needs_user"] * 2
    handoffs, sql, timeline = _fake_stage(monkeypatch, outputs)
    _, result = _classifier_stage()
    assert len(handoffs) == 1
    assert result["status"] == "needs_user" and result["failure"] == "NEEDS_MORE_INFO"
    assert [(p[1], p[2], p[3]) for p in timeline()] == [("classifier", "needs_user", 1)]
    job = asyncio.run(run_job("job-ask", "집 앞 가로등"))
    assert job["status"] == "needs_user" and job["state"]["question_round"] == 1 and len(handoffs) == 2
    updates = [p for s, p in sql if s.startswith("UPDATE jobs")]
    assert len(updates) == 1 and updates[0][0] == "needs_user"


def test_stage_timeline_tracks_unavailable_target_and_preserves_author(monkeypatch):
    import asyncio
    from app.services import supervisor
    from app.services.supervisor import execute_stage
    clock = iter([10, 20, 22.3])
    monkeypatch.setattr(supervisor, "perf_counter", lambda: next(clock))
    unavailable = dict(author="backend", status="retry", data={}, failure="SERVICE_UNAVAILABLE", incident_id=None)
    handoffs, sql, timeline = _fake_stage(monkeypatch, [unavailable, "classifier_streetlight"])
    result = asyncio.run(execute_stage("job-author", "classifier", {"text": "가로등"}, "민원 유형과 지역 파악", []))
    assert [(h.to, h.attempt, h.last_failure) for h in handoffs] == [("classifier", 1, None), ("classifier", 2, "SERVICE_UNAVAILABLE")]
    assert [(p[1], p[2], p[3]) for p in timeline()] == [("classifier", "retry", 1), ("classifier", "success", 2)]
    assert timeline()[0][4].obj["author"] == "backend"
    assert result["status"] == "success"
    assert result["data"]["evidence"]["fixed"] == "재호출 2.3초 · 성공"


def test_stage_rejects_result_violating_contract(monkeypatch):
    import asyncio
    from app.services.supervisor import execute_stage
    broken = dict(author="classifier", status="retry", data={}, failure=None, incident_id=None)  # retry인데 failure 없음
    handoffs, sql, timeline = _fake_stage(monkeypatch, [broken])
    with pytest.raises(ValueError):
        asyncio.run(execute_stage("job-bad", "classifier", {"text": "가로등"}, "민원 유형과 지역 파악", []))
    assert len(handoffs) == 1 and timeline() == []  # 계약 위반 결과는 기록·재시도하지 않음



# --- jobs 상태·timeline_events 저장 (메모리 가짜 DB, 실제 PostgreSQL·에이전트 없음) ---

class FakeDB:
    """backend가 쓰는 SQL만 흉내 낸다. 모르는 SQL이면 실패시켜 테스트가 몰래 통과하지 않게 한다."""
    def __init__(self, log):
        self.jobs, self.timeline, self.log, self.updates, self.status_at_call = {}, [], log, [], []
        self.incidents = {}
    async def query(self, sql, params=(), fetch=False):
        self.log.append(("sql", sql.split("(")[0].split(" WHERE")[0]))
        if sql.startswith("INSERT INTO jobs(id,text,status)"):
            job_id, text, status = params
            self.jobs[job_id] = {"id": job_id, "text": text, "status": status, "state": {}, "completed_at": None}
        elif sql.startswith("UPDATE jobs SET status=%s,state=%s,completed_at=CASE WHEN %s IN ('success','failed') THEN now() ELSE NULL END WHERE id=%s"):
            status, state, case_status, job_id = params
            self.updates.append(status)
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


def _job_env(monkeypatch, classifier_outputs, category="streetlight"):
    """classifier는 classifier_outputs를 차례로, 나머지 에이전트는 category 정상 샘플을 돌려준다."""
    log, handoffs = [], []
    db = FakeDB(log)
    queue = list(classifier_outputs)
    async def call(handoff):
        handoffs.append(handoff)
        log.append(("call", handoff.to))
        db.status_at_call.append(db.jobs.get(handoff.task_id, {}).get("status"))  # 호출 시점의 jobs 상태
        if handoff.to == "classifier":
            if not queue:
                raise AssertionError("classifier가 예상보다 많이 호출됨")
            return _output(queue.pop(0), handoff.task_id)
        name = f"{handoff.to}_{category}" + ("_draft" if handoff.inputs.get("mode") == "draft" else "")
        return _output(name, handoff.task_id)
    monkeypatch.setattr(store, "query", db.query)
    monkeypatch.setattr(agents, "call", call)
    return db, log, handoffs


def _post_job(text="서울 동작구 상도동 주민센터 앞 가로등 고장"):
    response = TestClient(app).post("/jobs?wait=true", json={"text": text})
    assert response.status_code == 201, response.text
    return response.json()


def test_job_row_created_running_before_first_agent_call(monkeypatch):
    db, log, _ = _job_env(monkeypatch, ["classifier_streetlight"])
    job = _post_job()
    assert log[0] == ("sql", "INSERT INTO jobs")
    assert log.index(("call", "classifier")) > 0
    assert db.status_at_call[0] == "running"  # 에이전트 호출 전에 running 행이 이미 있음
    assert db.updates == ["success"]  # 최초 running은 INSERT로만, 종료 UPDATE는 한 번
    assert db.jobs[job["id"]]["text"] == "서울 동작구 상도동 주민센터 앞 가로등 고장"




def test_job_success_lifecycle_and_state(monkeypatch):
    db, log, _ = _job_env(monkeypatch, ["classifier_streetlight"])
    job = _post_job()
    assert job["status"] == "success"
    assert db.updates == ["success"]  # finish UPDATE 중복 없음
    stored = db.jobs[job["id"]]
    assert stored["status"] == "success"
    assert stored["completed_at"] == "now()"
    state = stored["state"]
    assert "result" in state and state["result"] == job["state"]["result"]
    assert "classification" in state and "channels" in state
    assert state["question_round"] == 0
    assert "last_result" in state and state["last_result"]["status"] == "success"
    assert state["result"]["category"] == "streetlight"


def test_job_needs_user_lifecycle_and_state(monkeypatch):
    db, log, handoffs = _job_env(monkeypatch, ["classifier_streetlight_needs_user"])
    job = _post_job("집 앞 가로등")
    assert job["status"] == "needs_user"
    assert db.updates == ["needs_user"]  # finish UPDATE 1회만
    stored = db.jobs[job["id"]]
    assert stored["status"] == "needs_user"
    assert stored["completed_at"] is None  # needs_user는 completed_at이 NULL
    state = stored["state"]
    assert "result" not in state  # 아직 가짜 result를 만들지 않음
    assert state["question_round"] == 1  # 0에서 1로 증가
    assert state["last_result"]["status"] == "needs_user"
    assert state["last_result"]["failure"] == "NEEDS_MORE_INFO"


def test_job_failed_lifecycle_and_state(monkeypatch):
    log, handoffs = [], []
    db = FakeDB(log)
    async def call(handoff):
        handoffs.append(handoff)
        log.append(("call", handoff.to))
        db.status_at_call.append(db.jobs.get(handoff.task_id, {}).get("status"))
        if handoff.to == "classifier":
            return _output("classifier_out_of_scope", handoff.task_id)
        if handoff.to == "locator":
            return _output("locator_no_channel", handoff.task_id)
        raise AssertionError("호출되지 않아야 하는 에이전트: " + handoff.to)
    monkeypatch.setattr(store, "query", db.query)
    monkeypatch.setattr(agents, "call", call)

    job = _post_job("범위 밖 민원")
    assert job["status"] == "failed"
    assert db.updates == ["failed"]
    stored = db.jobs[job["id"]]
    assert stored["status"] == "failed"
    assert stored["completed_at"] == "now()"
    state = stored["state"]
    assert state["result"]["category"] == "out_of_scope"
    assert state["result"]["channels"] == []
    assert state["last_result"]["status"] == "failed"
    assert state["last_result"]["failure"] == "NO_CHANNEL"
    assert any("창구를 찾지 못했습니다" in c for c in state["confirm_needed"])


def test_job_retry_then_success_timeline_lifecycle(monkeypatch):
    db, log, handoffs = _job_env(monkeypatch, ["classifier_llm_invalid", "classifier_streetlight"])
    job = _post_job()
    assert job["status"] == "success"
    # classifier가 2번(retry -> success), locator, verifier, writer, verifier => 총 6개 호출
    cls_events = [e for e in db.timeline if e["job_id"] == job["id"] and e["payload"]["author"] == "classifier"]
    assert len(cls_events) == 2
    assert [e["attempt"] for e in cls_events] == [1, 2]
    assert [e["event_type"] for e in cls_events] == ["retry", "success"]
    assert cls_events[0]["payload"]["failure"] == "LLM_OUTPUT_INVALID"
    assert cls_events[1]["payload"]["failure"] is None


def test_job_max_retries_fails_without_fourth_call(monkeypatch):
    db, log, handoffs = _job_env(monkeypatch, ["classifier_llm_invalid"] * 3)
    job = _post_job()
    assert job["status"] == "failed"
    assert db.updates == ["failed"]
    stored = db.jobs[job["id"]]
    assert stored["status"] == "failed"
    assert stored["completed_at"] == "now()"
    assert stored["state"]["result"]["confirm_needed"]
    assert [h.attempt for h in handoffs] == [1, 2, 3]  # 4번째 호출 없음
    cls_events = [e for e in db.timeline if e["job_id"] == job["id"]]
    assert len(cls_events) == 3
    assert [e["attempt"] for e in cls_events] == [1, 2, 3]
    assert all(e["event_type"] == "retry" for e in cls_events)


def test_get_job_endpoints(monkeypatch):
    db, log, _ = _job_env(monkeypatch, ["classifier_streetlight"])
    job = _post_job()
    client = TestClient(app)
    # 존재하는 작업 조회
    res = client.get(f"/jobs/{job['id']}")
    assert res.status_code == 200
    assert res.json()["id"] == job["id"]
    assert res.json()["status"] == "success"
    # 존재하지 않는 작업 조회 -> 404
    assert client.get("/jobs/job-not-exist").status_code == 404


def test_get_timeline_endpoints(monkeypatch):
    db, log, _ = _job_env(monkeypatch, ["classifier_llm_invalid", "classifier_streetlight"])
    job = _post_job()
    client = TestClient(app)
    # 타임라인 조회: 해당 job_id만, id 오름차순
    res = client.get(f"/jobs/{job['id']}/timeline")
    assert res.status_code == 200
    events = res.json()
    assert len(events) == 6
    assert [e["id"] for e in events] == sorted(e["id"] for e in events)
    assert all(e["job_id"] == job["id"] for e in events)
    # 첫 2개 이벤트는 classifier의 retry(attempt 1), success(attempt 2)
    assert events[0]["agent"] == "classifier" and events[0]["event_type"] == "retry" and events[0]["attempt"] == 1
    assert events[1]["agent"] == "classifier" and events[1]["event_type"] == "success" and events[1]["attempt"] == 2



# --- 실패 코드별 재시도·재계획 단위 테스트 (요구사항 10) ---

def test_replan_link_dead_excludes_channel_and_recovers_incident(monkeypatch):
    dead_chan = {
        "channel_id": "seoul-dongjak-streetlight-01", "name": "동작구청 도로과", "org": "동작구청",
        "method": "web", "url": "https://dongjak.go.kr/streetlight", "jurisdiction": "동작구",
        "verified_at": "2026-10-06", "source_url": "https://dongjak.go.kr"
    }
    live_chan = {
        "channel_id": "seoul-dongjak-streetlight-02", "name": "서울스마트불편신고", "org": "서울특별시",
        "method": "app", "url": "https://smartreport.seoul.go.kr", "jurisdiction": "서울특별시",
        "verified_at": "2026-10-06", "source_url": "https://smartreport.seoul.go.kr"
    }
    log, handoffs = [], []
    db = FakeDB(log)

    async def call(handoff):
        handoffs.append(handoff)
        log.append(("call", handoff.to))
        if handoff.to == "classifier":
            return _output("classifier_streetlight", handoff.task_id)
        if handoff.to == "locator":
            if "seoul-dongjak-streetlight-01" in handoff.inputs["exclude_channel_ids"]:
                return {
                    "task_id": handoff.task_id, "author": "locator", "status": "success",
                    "data": {"channels": [live_chan], "fallback_level": "district"},
                    "failure": None, "incident_id": None
                }
            return {
                "task_id": handoff.task_id, "author": "locator", "status": "success",
                "data": {"channels": [dead_chan], "fallback_level": "district"},
                "failure": None, "incident_id": None
            }
        if handoff.to == "verifier" and handoff.inputs.get("mode") == "channel":
            chan_ids = [c["channel_id"] for c in handoff.inputs["channels"]]
            if "seoul-dongjak-streetlight-01" in chan_ids:
                return {
                    "task_id": handoff.task_id, "author": "verifier", "status": "failed",
                    "data": {"verdict": "FAIL", "checks": [{"name": "link", "ok": False, "detail": "404"}],
                             "reasons": ["LINK_DEAD"], "passed_channel_ids": []},
                    "failure": "LINK_DEAD", "incident_id": None
                }
            return {
                "task_id": handoff.task_id, "author": "verifier", "status": "success",
                "data": {"verdict": "PASS", "checks": [{"name": "link", "ok": True, "detail": "200"}],
                         "reasons": [], "passed_channel_ids": ["seoul-dongjak-streetlight-02"]},
                "failure": None, "incident_id": None
            }
        name = f"{handoff.to}_streetlight" + ("_draft" if handoff.inputs.get("mode") == "draft" else "")
        return _output(name, handoff.task_id)

    monkeypatch.setattr(store, "query", db.query)
    monkeypatch.setattr(agents, "call", call)

    job = _post_job()
    assert job["status"] == "success"
    # locator가 2번 호출되었고 두 번째 호출 시 exclude_channel_ids에 첫 번째 창구가 포함됨
    loc_calls = [h for h in handoffs if h.to == "locator"]
    assert len(loc_calls) == 2
    assert "seoul-dongjak-streetlight-01" in loc_calls[1].inputs["exclude_channel_ids"]
    assert job["state"]["result"]["channels"][0]["channel_id"] == "seoul-dongjak-streetlight-02"
    # 장애 복구 검증
    assert len(db.incidents) == 1
    generated_id, inc = next(iter(db.incidents.items()))
    assert generated_id.startswith("incident-")
    assert inc["fault"] == "LINK_DEAD"
    assert inc["result"] == "recovered"
    assert inc["recovered_at"] == "now()"



def test_replan_not_official_excludes_channel_and_succeeds(monkeypatch):
    fake_chan = {
        "channel_id": "fake-01", "name": "가짜 사이트", "org": "미상",
        "method": "web", "url": "https://fake.example.com", "jurisdiction": "전국",
        "verified_at": "2026-10-06", "source_url": "https://fake.example.com"
    }
    real_chan = {
        "channel_id": "national-all-01", "name": "국민신문고", "org": "국민권익위원회",
        "method": "web", "url": "https://www.epeople.go.kr/", "jurisdiction": "전국",
        "verified_at": "2026-10-06", "source_url": "https://www.epeople.go.kr/"
    }
    log, handoffs = [], []
    db = FakeDB(log)

    async def call(handoff):
        handoffs.append(handoff)
        if handoff.to == "classifier":
            return _output("classifier_streetlight", handoff.task_id)
        if handoff.to == "locator":
            if "fake-01" in handoff.inputs["exclude_channel_ids"]:
                return {
                    "task_id": handoff.task_id, "author": "locator", "status": "success",
                    "data": {"channels": [real_chan], "fallback_level": "national"},
                    "failure": None, "incident_id": None
                }
            return {
                "task_id": handoff.task_id, "author": "locator", "status": "success",
                "data": {"channels": [fake_chan], "fallback_level": "district"},
                "failure": None, "incident_id": None
            }
        if handoff.to == "verifier" and handoff.inputs.get("mode") == "channel":
            if handoff.inputs["channels"][0]["channel_id"] == "fake-01":
                return {
                    "task_id": handoff.task_id, "author": "verifier", "status": "failed",
                    "data": {"verdict": "FAIL", "checks": [{"name": "official", "ok": False, "detail": "비공식"}],
                             "reasons": ["NOT_OFFICIAL"], "passed_channel_ids": []},
                    "failure": "NOT_OFFICIAL", "incident_id": "inc-not-off-1"
                }
            return {
                "task_id": handoff.task_id, "author": "verifier", "status": "success",
                "data": {"verdict": "PASS", "checks": [{"name": "official", "ok": True, "detail": "공식"}],
                         "reasons": [], "passed_channel_ids": ["national-all-01"]},
                "failure": None, "incident_id": None
            }
        name = f"{handoff.to}_streetlight" + ("_draft" if handoff.inputs.get("mode") == "draft" else "")
        return _output(name, handoff.task_id)

    monkeypatch.setattr(store, "query", db.query)
    monkeypatch.setattr(agents, "call", call)

    job = _post_job()
    assert job["status"] == "success"
    assert db.incidents["inc-not-off-1"]["result"] == "recovered"


def test_replan_link_dead_max_replans_exceeded_fails_job(monkeypatch):
    log, handoffs = [], []
    db = FakeDB(log)

    async def call(handoff):
        handoffs.append(handoff)
        if handoff.to == "classifier":
            return _output("classifier_streetlight", handoff.task_id)
        if handoff.to == "locator":
            cid = f"chan-{len([h for h in handoffs if h.to == 'locator'])}"
            c = {
                "channel_id": cid, "name": "청", "org": "청", "method": "web",
                "url": f"https://{cid}.go.kr", "jurisdiction": "동작구",
                "verified_at": "2026-10-06", "source_url": f"https://{cid}.go.kr"
            }
            return {
                "task_id": handoff.task_id, "author": "locator", "status": "success",
                "data": {"channels": [c], "fallback_level": "district"},
                "failure": None, "incident_id": None
            }
        if handoff.to == "verifier" and handoff.inputs.get("mode") == "channel":
            return {
                "task_id": handoff.task_id, "author": "verifier", "status": "failed",
                "data": {"verdict": "FAIL", "checks": [{"name": "link", "ok": False, "detail": "404"}],
                         "reasons": ["LINK_DEAD"], "passed_channel_ids": []},
                "failure": "LINK_DEAD", "incident_id": "inc-dead-loop"
            }
        raise AssertionError("호출 금지")

    monkeypatch.setattr(store, "query", db.query)
    monkeypatch.setattr(agents, "call", call)

    job = _post_job()
    assert job["status"] == "failed"
    assert job["state"]["result"]["confirm_needed"]
    # locator 및 verifier가 최초 1회 + 재계획 2회 = 총 3회 호출되고 멈춤
    loc_calls = [h for h in handoffs if h.to == "locator"]
    ver_calls = [h for h in handoffs if h.to == "verifier"]
    assert len(loc_calls) == 3
    assert len(ver_calls) == 3
    # 최종 실패로 마감되어 recovered_at 없음
    inc = db.incidents["inc-dead-loop"]
    assert inc["result"] == "failed"
    assert inc["recovered_at"] is None



def test_replan_no_channel_district_city_national_progression(monkeypatch):
    national_chan = {
        "channel_id": "national-all-01", "name": "국민신문고", "org": "국민권익위원회",
        "method": "web", "url": "https://www.epeople.go.kr/", "jurisdiction": "전국",
        "verified_at": "2026-10-06", "source_url": "https://www.epeople.go.kr/"
    }
    log, handoffs = [], []
    db = FakeDB(log)

    async def call(handoff):
        handoffs.append(handoff)
        if handoff.to == "classifier":
            return _output("classifier_streetlight", handoff.task_id)
        if handoff.to == "locator":
            loc_count = len([h for h in handoffs if h.to == "locator"])
            if loc_count == 1:
                # 1회차: district
                assert handoff.inputs["region"]["sido"] == "서울특별시"
                assert handoff.inputs["region"]["sigungu"] == "동작구"
                return {
                    "task_id": handoff.task_id, "author": "locator", "status": "failed",
                    "data": {"channels": [], "fallback_level": "district"},
                    "failure": "NO_CHANNEL", "incident_id": "inc-no-chan-1"
                }
            elif loc_count == 2:
                # 2회차: city (sigungu=None)
                assert handoff.inputs["region"]["sido"] == "서울특별시"
                assert handoff.inputs["region"]["sigungu"] is None
                return {
                    "task_id": handoff.task_id, "author": "locator", "status": "failed",
                    "data": {"channels": [], "fallback_level": "city"},
                    "failure": "NO_CHANNEL", "incident_id": "inc-no-chan-1"
                }
            else:
                # 3회차: national (sido=None, sigungu=None)
                assert handoff.inputs["region"]["sido"] is None
                assert handoff.inputs["region"]["sigungu"] is None
                return {
                    "task_id": handoff.task_id, "author": "locator", "status": "success",
                    "data": {"channels": [national_chan], "fallback_level": "national"},
                    "failure": None, "incident_id": None
                }
        if handoff.to == "verifier" and handoff.inputs.get("mode") == "channel":
            return {
                "task_id": handoff.task_id, "author": "verifier", "status": "success",
                "data": {"verdict": "PASS", "checks": [{"name": "link", "ok": True, "detail": "200"}],
                         "reasons": [], "passed_channel_ids": ["national-all-01"]},
                "failure": None, "incident_id": None
            }
        name = f"{handoff.to}_streetlight" + ("_draft" if handoff.inputs.get("mode") == "draft" else "")
        return _output(name, handoff.task_id)

    monkeypatch.setattr(store, "query", db.query)
    monkeypatch.setattr(agents, "call", call)

    job = _post_job()
    assert job["status"] == "success"
    loc_calls = [h for h in handoffs if h.to == "locator"]
    assert len(loc_calls) == 3
    inc = db.incidents["inc-no-chan-1"]
    assert inc["result"] == "recovered"
    assert inc["recovered_at"] == "now()"


def test_replan_no_channel_all_levels_fails_without_extra_calls(monkeypatch):
    log, handoffs = [], []
    db = FakeDB(log)

    async def call(handoff):
        handoffs.append(handoff)
        if handoff.to == "classifier":
            return _output("classifier_streetlight", handoff.task_id)
        if handoff.to == "locator":
            return {
                "task_id": handoff.task_id, "author": "locator", "status": "failed",
                "data": {"channels": [], "fallback_level": "national"},
                "failure": "NO_CHANNEL", "incident_id": "inc-no-chan-all"
            }
        raise AssertionError("호출 금지")

    monkeypatch.setattr(store, "query", db.query)
    monkeypatch.setattr(agents, "call", call)

    job = _post_job()
    assert job["status"] == "failed"
    assert job["state"]["result"]["confirm_needed"]
    loc_calls = [h for h in handoffs if h.to == "locator"]
    # locator가 national까지 확인했다고 명시했으므로 Supervisor가 같은 검색을 반복하지 않는다.
    assert len(loc_calls) == 1
    assert any("창구를 찾지 못했습니다" in c for c in job["state"]["confirm_needed"])
    inc = db.incidents["inc-no-chan-all"]
    assert inc["result"] == "failed"
    assert inc["recovered_at"] is None


def test_out_of_scope_proceeds_to_national_and_succeeds(monkeypatch):
    log, handoffs = [], []
    db = FakeDB(log)

    async def call(handoff):
        handoffs.append(handoff)
        if handoff.to == "classifier":
            return _output("classifier_out_of_scope", handoff.task_id)
        if handoff.to == "locator":
            assert handoff.inputs["region"]["sido"] is None
            assert handoff.inputs["category"] == "out_of_scope"
            return _output("locator_out_of_scope", handoff.task_id)
        name = f"{handoff.to}_out_of_scope" + ("_draft" if handoff.inputs.get("mode") == "draft" else "")
        return _output(name, handoff.task_id)

    monkeypatch.setattr(store, "query", db.query)
    monkeypatch.setattr(agents, "call", call)

    job = _post_job("공사 소음 피해보상 민원")
    assert job["status"] == "success"
    assert "result" in job["state"]
    assert job["state"]["result"]["category"] == "out_of_scope"
    assert [h.to for h in handoffs] == ["classifier", "locator", "verifier"]
    assert job["state"]["result"]["draft"]["body"] == ""
    assert any("지원 범위 밖" in c for c in job["state"]["confirm_needed"])
    assert len(db.incidents) == 0  # incident_id가 없는 OUT_OF_SCOPE는 incident 생성 안 함



def test_replan_pii_in_draft_rewrites_with_new_draft(monkeypatch):
    log, handoffs = [], []
    db = FakeDB(log)

    pii_draft = {
        "title": "가로등 고장 신고", "body": "가로등이 꺼져 있습니다. 연락처: 010-1234-5678",
        "fill_in": [], "removed_pii": []
    }
    clean_draft = {
        "title": "가로등 고장 신고", "body": "가로등이 꺼져 있습니다.",
        "fill_in": [], "removed_pii": ["010-1234-5678"]
    }

    async def call(handoff):
        handoffs.append(handoff)
        if handoff.to == "classifier":
            return _output("classifier_streetlight", handoff.task_id)
        if handoff.to == "locator":
            return _output("locator_streetlight", handoff.task_id)
        if handoff.to == "verifier" and handoff.inputs.get("mode") == "channel":
            return _output("verifier_streetlight", handoff.task_id)
        if handoff.to == "writer":
            writer_calls = [h for h in handoffs if h.to == "writer"]
            if len(writer_calls) == 1:
                assert handoff.attempt == 1
                assert handoff.last_failure is None
                return {
                    "task_id": handoff.task_id, "author": "writer", "status": "success",
                    "data": pii_draft, "failure": None, "incident_id": None
                }
            else:
                assert handoff.attempt == 1
                assert handoff.last_failure == "PII_IN_DRAFT"
                return {
                    "task_id": handoff.task_id, "author": "writer", "status": "success",
                    "data": clean_draft, "failure": None, "incident_id": None
                }
        if handoff.to == "verifier" and handoff.inputs.get("mode") == "draft":
            current_draft = handoff.inputs["draft"]
            if "010-1234-5678" in current_draft["body"]:
                return {
                    "task_id": handoff.task_id, "author": "verifier", "status": "retry",
                    "data": {"verdict": "RETRY", "checks": [{"name": "pii", "ok": False, "detail": "연락처 감지"}],
                             "reasons": ["PII_IN_DRAFT"], "passed_channel_ids": []},
                    "failure": "PII_IN_DRAFT", "incident_id": "inc-pii-1"
                }
            return {
                "task_id": handoff.task_id, "author": "verifier", "status": "success",
                "data": {"verdict": "PASS", "checks": [{"name": "pii", "ok": True, "detail": "통과"}],
                         "reasons": [], "passed_channel_ids": []},
                "failure": None, "incident_id": None
            }

    monkeypatch.setattr(store, "query", db.query)
    monkeypatch.setattr(agents, "call", call)

    job = _post_job()
    assert job["status"] == "success"
    # writer가 2번 호출되고 두 번째 writer의 draft가 최종 결과에 사용됨
    writer_calls = [h for h in handoffs if h.to == "writer"]
    assert len(writer_calls) == 2
    assert job["state"]["result"]["draft"] == clean_draft
    inc = db.incidents["inc-pii-1"]
    assert inc["result"] == "recovered"


def test_replan_draft_mismatch_rewrites_and_succeeds(monkeypatch):
    log, handoffs = [], []
    db = FakeDB(log)

    bad_draft = {"title": "잘못된 문구", "body": "엉뚱한 내용", "fill_in": [], "removed_pii": []}
    good_draft = {"title": "가로등 고장 신고", "body": "동작구 상도동 가로등 고장", "fill_in": [], "removed_pii": []}

    async def call(handoff):
        handoffs.append(handoff)
        if handoff.to == "classifier":
            return _output("classifier_streetlight", handoff.task_id)
        if handoff.to == "locator":
            return _output("locator_streetlight", handoff.task_id)
        if handoff.to == "verifier" and handoff.inputs.get("mode") == "channel":
            return _output("verifier_streetlight", handoff.task_id)
        if handoff.to == "writer":
            if len([h for h in handoffs if h.to == "writer"]) == 1:
                return {"task_id": handoff.task_id, "author": "writer", "status": "success", "data": bad_draft, "failure": None, "incident_id": None}
            assert handoff.last_failure == "DRAFT_MISMATCH"
            return {"task_id": handoff.task_id, "author": "writer", "status": "success", "data": good_draft, "failure": None, "incident_id": None}
        if handoff.to == "verifier" and handoff.inputs.get("mode") == "draft":
            if handoff.inputs["draft"]["title"] == "잘못된 문구":
                return {
                    "task_id": handoff.task_id, "author": "verifier", "status": "retry",
                    "data": {"verdict": "RETRY", "checks": [{"name": "category", "ok": False, "detail": "불일치"}],
                             "reasons": ["DRAFT_MISMATCH"], "passed_channel_ids": []},
                    "failure": "DRAFT_MISMATCH", "incident_id": "inc-mismatch-1"
                }
            return {
                "task_id": handoff.task_id, "author": "verifier", "status": "success",
                "data": {"verdict": "PASS", "checks": [{"name": "category", "ok": True, "detail": "일치"}],
                         "reasons": [], "passed_channel_ids": []},
                "failure": None, "incident_id": None
            }

    monkeypatch.setattr(store, "query", db.query)
    monkeypatch.setattr(agents, "call", call)

    job = _post_job()
    assert job["status"] == "success"
    assert db.incidents["inc-mismatch-1"]["result"] == "recovered"
    assert job["state"]["result"]["draft"] == good_draft



def test_replan_draft_max_replans_exceeded_fails_job(monkeypatch):
    log, handoffs = [], []
    db = FakeDB(log)

    pii_draft = {"title": "제목", "body": "내용 010-0000-0000", "fill_in": [], "removed_pii": []}

    async def call(handoff):
        handoffs.append(handoff)
        if handoff.to == "classifier":
            return _output("classifier_streetlight", handoff.task_id)
        if handoff.to == "locator":
            return _output("locator_streetlight", handoff.task_id)
        if handoff.to == "verifier" and handoff.inputs.get("mode") == "channel":
            return _output("verifier_streetlight", handoff.task_id)
        if handoff.to == "writer":
            return {"task_id": handoff.task_id, "author": "writer", "status": "success", "data": pii_draft, "failure": None, "incident_id": None}
        if handoff.to == "verifier" and handoff.inputs.get("mode") == "draft":
            return {
                "task_id": handoff.task_id, "author": "verifier", "status": "retry",
                "data": {"verdict": "RETRY", "checks": [{"name": "pii", "ok": False, "detail": "연락처"}],
                         "reasons": ["PII_IN_DRAFT"], "passed_channel_ids": []},
                "failure": "PII_IN_DRAFT", "incident_id": "inc-pii-loop"
            }

    monkeypatch.setattr(store, "query", db.query)
    monkeypatch.setattr(agents, "call", call)

    job = _post_job()
    assert job["status"] == "failed"
    assert job["state"]["result"]["confirm_needed"]
    assert job["state"]["result"]["channels"]
    assert job["state"]["result"]["draft"]["body"] == ""
    validate("final", job["state"]["result"])
    # writer가 최초 1회 + 재계획 2회 = 총 3회 호출되고 루프 종료
    writer_calls = [h for h in handoffs if h.to == "writer"]
    assert len(writer_calls) == 3
    inc = db.incidents["inc-pii-loop"]
    assert inc["result"] == "failed"
    assert inc["recovered_at"] is None
    assert any("안전한 신고 문구를 만들지 못했습니다" in c for c in job["state"]["confirm_needed"])


def test_exclude_channel_ids_deduplication(monkeypatch):
    dead_chan = {
        "channel_id": "duplicate-chan-01", "name": "동작구청", "org": "동작구청",
        "method": "web", "url": "https://dongjak.go.kr/streetlight", "jurisdiction": "동작구",
        "verified_at": "2026-10-06", "source_url": "https://dongjak.go.kr"
    }
    log, handoffs = [], []
    db = FakeDB(log)

    async def call(handoff):
        handoffs.append(handoff)
        if handoff.to == "classifier":
            return _output("classifier_streetlight", handoff.task_id)
        if handoff.to == "locator":
            # locator가 제외 목록을 무시하고 같은 channel_id를 또 반환하는 경우
            return {
                "task_id": handoff.task_id, "author": "locator", "status": "success",
                "data": {"channels": [dead_chan], "fallback_level": "district"},
                "failure": None, "incident_id": None
            }
        if handoff.to == "verifier" and handoff.inputs.get("mode") == "channel":
            return {
                "task_id": handoff.task_id, "author": "verifier", "status": "failed",
                "data": {"verdict": "FAIL", "checks": [{"name": "link", "ok": False, "detail": "404"}],
                         "reasons": ["LINK_DEAD"], "passed_channel_ids": []},
                "failure": "LINK_DEAD", "incident_id": "inc-dup-exclude"
            }

    monkeypatch.setattr(store, "query", db.query)
    monkeypatch.setattr(agents, "call", call)

    job = _post_job()
    assert job["status"] == "failed"
    loc_calls = [h for h in handoffs if h.to == "locator"]
    # 2번째 호출 시 exclude_channel_ids에 "duplicate-chan-01"이 1개만 있어야 함 (중복 없음)
    assert loc_calls[1].inputs["exclude_channel_ids"].count("duplicate-chan-01") == 1


def test_service_unavailable_retries_same_stage_up_to_3_and_recovers(monkeypatch):
    log, handoffs = [], []
    db = FakeDB(log)

    async def call(handoff):
        handoffs.append(handoff)
        if handoff.to == "classifier":
            cls_calls = [h for h in handoffs if h.to == "classifier"]
            if len(cls_calls) == 1:
                return {
                    "task_id": handoff.task_id, "author": "backend", "status": "retry",
                    "data": {}, "failure": "SERVICE_UNAVAILABLE", "incident_id": "inc-svc-down"
                }
            return _output("classifier_streetlight", handoff.task_id)
        name = f"{handoff.to}_streetlight" + ("_draft" if handoff.inputs.get("mode") == "draft" else "")
        return _output(name, handoff.task_id)

    monkeypatch.setattr(store, "query", db.query)
    monkeypatch.setattr(agents, "call", call)

    job = _post_job()
    assert job["status"] == "success"
    cls_calls = [h for h in handoffs if h.to == "classifier"]
    assert len(cls_calls) == 2
    assert cls_calls[0].attempt == 1
    assert cls_calls[1].attempt == 2
    assert cls_calls[1].last_failure == "SERVICE_UNAVAILABLE"
    inc = db.incidents["inc-svc-down"]
    assert inc["result"] == "recovered"


def test_internal_llm_recovery_then_needs_user_records_recovered(monkeypatch):
    """classifier가 AI 실패를 내부 복구(대체 모델/규칙)한 뒤 되묻기로 넘어가면 복구는 이미 끝난 것이다."""
    db, _, _ = _job_env(monkeypatch, ["classifier_streetlight_needs_user"])
    original = agents.call
    async def call(handoff):
        result = await original(handoff)
        if handoff.to == "classifier":
            result["incident_id"] = "incident-internal-1"
        return result
    monkeypatch.setattr(agents, "call", call)
    job = _post_job("집 앞 가로등")
    assert job["status"] == "needs_user"
    inc = db.incidents["incident-internal-1"]
    assert inc["fault"] == "LLM_OUTPUT_INVALID" and inc["target"] == "classifier"
    assert inc["result"] == "recovered" and inc["recovered_at"] == "now()"
