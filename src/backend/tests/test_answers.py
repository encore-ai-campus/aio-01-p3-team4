import json

from fastapi.testclient import TestClient

from app.clients import agents, store
from app.core.config import CONTRACTS
from app.main import app


def _sample(name: str, task_id: str) -> dict:
    value = json.loads((CONTRACTS / "samples" / f"{name}_output.json").read_text(encoding="utf-8"))
    value["task_id"] = task_id
    return value


def _needs_user(task_id: str, fields: list[str]) -> dict:
    value = _sample("classifier_streetlight_needs_user", task_id)
    value["data"]["region"] = {
        "sido": "서울특별시",
        "sigungu": "동작구",
        "dong": "상도동",
        "place_text": "상도동 주민센터 앞",
    }
    value["data"]["missing"] = fields
    value["data"]["questions"] = [
        {"id": f"q{index}", "text": f"{field}을 알려 주세요", "field": field}
        for index, field in enumerate(fields, start=1)
    ]
    return value


class AnswerDB:
    def __init__(self):
        self.jobs = {}
        self.timeline = []
        self.inputs = []
        self.claim_calls = 0
        self.force_conflict = False

    async def query(self, sql, params=(), fetch=False):
        if sql.startswith("INSERT INTO jobs(id,text,status)"):
            job_id, text, status = params
            self.jobs[job_id] = {
                "id": job_id,
                "text": text,
                "status": status,
                "state": {},
                "completed_at": None,
            }
            return None
        if sql.startswith("INSERT INTO timeline_events(job_id,agent,event_type,attempt,payload)"):
            job_id, agent, event_type, attempt, payload = params
            self.timeline.append({
                "id": len(self.timeline) + 1,
                "job_id": job_id,
                "agent": agent,
                "event_type": event_type,
                "attempt": attempt,
                "payload": payload.obj,
            })
            return None
        if sql.startswith("UPDATE jobs SET status=%s,state=%s,completed_at=CASE"):
            status, state, case_status, job_id = params
            self.jobs[job_id].update(
                status=status,
                state=json.loads(json.dumps(state.obj)),
                completed_at="now()" if case_status in {"success", "failed"} else None,
            )
            return None
        if sql == "SELECT * FROM jobs WHERE id=%s":
            job = self.jobs.get(params[0])
            return [job] if job else []
        if sql == "SELECT * FROM timeline_events WHERE job_id=%s ORDER BY id":
            return [event for event in self.timeline if event["job_id"] == params[0]]
        raise AssertionError("예상하지 못한 SQL: " + sql)

    async def claim_and_store_answers(self, job_id: str, answers: dict[str, str]):
        self.claim_calls += 1
        job = self.jobs[job_id]
        if self.force_conflict or job["status"] != "needs_user":
            return None
        job["status"] = "running"
        for question_id, answer in answers.items():
            self.inputs.append({"job_id": job_id, "question_id": question_id, "answer": answer})
        return [
            {"question_id": row["question_id"], "answer": row["answer"]}
            for row in self.inputs
            if row["job_id"] == job_id
        ]


def _setup(monkeypatch, classifier_results):
    db = AnswerDB()
    classifier_queue = list(classifier_results)
    classifier_inputs = []
    locator_inputs = []

    async def call(handoff):
        if handoff.to == "classifier":
            classifier_inputs.append(handoff.inputs)
            result = classifier_queue.pop(0)
            result["task_id"] = handoff.task_id
            return result
        if handoff.to == "locator":
            locator_inputs.append(handoff.inputs)
            return _sample("locator_out_of_scope", handoff.task_id)
        if handoff.to == "verifier" and handoff.inputs.get("mode") == "channel":
            return _sample("verifier_out_of_scope", handoff.task_id)
        if handoff.to == "writer":
            return _sample("writer_streetlight", handoff.task_id)
        if handoff.to == "verifier" and handoff.inputs.get("mode") == "draft":
            return _sample("verifier_streetlight_draft", handoff.task_id)
        raise AssertionError("예상하지 못한 에이전트 호출: " + handoff.to)

    monkeypatch.setattr(store, "query", db.query)
    monkeypatch.setattr(store, "claim_and_store_answers", db.claim_and_store_answers)
    monkeypatch.setattr(agents, "call", call)
    return db, classifier_inputs, locator_inputs


def test_two_answer_rounds_preserve_known_district(monkeypatch):
    results = [
        _needs_user("temp", ["location_detail"]),
        _needs_user("temp", ["category"]),
        _needs_user("temp", ["occurred_at"]),
    ]
    db, classifier_inputs, locator_inputs = _setup(monkeypatch, results)
    client = TestClient(app)

    first = client.post("/jobs?wait=true", json={"text": "상도동 가로등 문제"})
    assert first.status_code == 201
    first_job = first.json()
    job_id = first_job["id"]
    assert first_job["status"] == "needs_user"
    assert first_job["state"]["question_round"] == 1
    # classifier가 q1을 반환해도 backend가 field 기반 안정 ID로 정규화한다.
    assert first_job["state"]["last_result"]["data"]["questions"][0]["id"] == "location_detail"

    second = client.post(f"/jobs/{job_id}/answers?wait=true", json={"answers": {"location_detail": "상도동 주민센터 앞"}})
    assert second.status_code == 200
    second_job = second.json()
    assert second_job["status"] == "needs_user"
    assert second_job["state"]["question_round"] == 2
    assert second_job["state"]["last_result"]["data"]["questions"][0]["id"] == "category"

    final = client.post(f"/jobs/{job_id}/answers?wait=true", json={"answers": {"category": "가로등 고장"}})
    assert final.status_code == 200
    final_job = final.json()
    assert final_job["status"] == "success"
    assert final_job["state"]["question_round"] == 2
    assert final_job["state"]["question_limit_reached"] is True
    assert any("추가 질문 2회" in message for message in final_job["state"]["result"]["confirm_needed"])
    assert all(channel["jurisdiction"] == "전국" for channel in final_job["state"]["result"]["channels"])

    assert classifier_inputs[1]["answers"] == {"location_detail": "상도동 주민센터 앞"}
    assert classifier_inputs[2]["answers"] == {
        "location_detail": "상도동 주민센터 앞",
        "category": "가로등 고장",
    }
    assert locator_inputs[0]["region"]["sido"] == "서울특별시"
    assert locator_inputs[0]["region"]["sigungu"] == "동작구"
    assert [row["question_id"] for row in db.inputs] == ["location_detail", "category"]

    third = client.post(f"/jobs/{job_id}/answers?wait=true", json={"answers": {"occurred_at": "어제"}})
    assert third.status_code == 409
    assert len(db.inputs) == 2


def test_answer_validation_has_no_side_effects(monkeypatch):
    db, _, _ = _setup(monkeypatch, [_needs_user("temp", ["location_detail", "occurred_at"])])
    client = TestClient(app)
    created = client.post("/jobs?wait=true", json={"text": "상도동 가로등 문제"}).json()
    path = f"/jobs/{created['id']}/answers?wait=true"

    assert client.post(path, json={"answers": {}}).status_code == 422
    assert client.post(path, json={"answers": {"location_detail": " "}}).status_code == 422
    assert client.post(path, json={"answers": {"location_detail": "가" * 501}}).status_code == 422
    assert client.post(path, json={"answers": {"unknown": "답변"}}).status_code == 422
    assert client.post(path, json={"answers": {"location_detail": "상도동"}}).status_code == 422
    assert db.claim_calls == 0
    assert db.inputs == []


def test_answer_claim_conflict_returns_409_without_insert(monkeypatch):
    db, _, _ = _setup(monkeypatch, [_needs_user("temp", ["location_detail"])])
    client = TestClient(app)
    created = client.post("/jobs?wait=true", json={"text": "상도동 가로등 문제"}).json()
    db.force_conflict = True

    response = client.post(
        f"/jobs/{created['id']}/answers?wait=true",
        json={"answers": {"location_detail": "상도동 주민센터 앞"}},
    )

    assert response.status_code == 409
    assert db.claim_calls == 1
    assert db.inputs == []
