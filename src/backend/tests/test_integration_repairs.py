import asyncio
import json
import re
from unittest.mock import AsyncMock, MagicMock
from psycopg import OperationalError, ProgrammingError

import pytest

from app.clients import agents, store
from app.core.config import CONTRACTS
from app.schemas.contract import Handoff, validate
from app.services import incidents, supervisor
from test_incidents import FakeDB, _output


@pytest.mark.parametrize("agent", ["classifier", "writer"])
def test_internal_llm_recovery_is_detected_and_recovered(monkeypatch, agent):
    db = FakeDB()
    response = _output(f"{agent}_streetlight", "job-internal")
    response["incident_id"] = f"inc-internal-{agent}"
    monkeypatch.setattr(store, "query", db.query)
    monkeypatch.setattr(agents, "call", AsyncMock(return_value=response))
    inputs = json.loads((CONTRACTS / "samples" / f"{agent}_streetlight_input.json").read_text())["inputs"]
    asyncio.run(supervisor.execute_stage("job-internal", agent, inputs, "검증", []))
    incident = db.incidents[response["incident_id"]]
    assert incident["target"] == agent
    assert incident["fault"] == "LLM_OUTPUT_INVALID"
    assert incident["detected_at"] and incident["recovered_at"]
    assert incident["result"] == "recovered"
    assert len(db.timeline) == 1


def test_timeout_claims_latest_pending_injection_for_same_target(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(store, "query", db.query)
    monkeypatch.setattr(agents, "sleep", AsyncMock())

    async def run():
        await incidents.record_injected("inc-old", "classifier", "delay", "2026-10-10T00:00:00Z")
        await incidents.record_injected("inc-delay", "classifier", "delay", "2026-10-11T00:00:00Z")
        await incidents.record_injected("inc-other", "writer", "delay", "2026-10-12T00:00:00Z")
        responses = [
            {"task_id": "job-delay", "author": "backend", "status": "retry", "data": {},
             "failure": "SERVICE_UNAVAILABLE", "incident_id": None},
            _output("classifier_streetlight", "job-delay"),
        ]
        monkeypatch.setattr(agents, "call", AsyncMock(side_effect=responses))
        return await supervisor.execute_stage("job-delay", "classifier", {"text": "가로등 고장"}, "검증", [])

    assert asyncio.run(run())["status"] == "success"
    assert len(db.incidents) == 3
    assert db.incidents["inc-delay"]["result"] == "recovered"
    assert db.incidents["inc-delay"]["fault"] == "delay"
    assert db.incidents["inc-old"]["result"] == db.incidents["inc-other"]["result"] == "injected"
    assert db.timeline[0]["payload"]["incident_id"] == "inc-delay"


def test_five_second_service_outage_recovers_within_three_attempts(monkeypatch):
    elapsed, attempts = [0], []
    db = FakeDB()

    async def sleep(seconds):
        elapsed[0] += seconds

    async def call(handoff):
        attempts.append((handoff.attempt, elapsed[0]))
        if elapsed[0] < 5:
            return {"task_id": handoff.task_id, "author": "backend", "status": "retry", "data": {},
                    "failure": "SERVICE_UNAVAILABLE", "incident_id": None}
        return _output("classifier_streetlight", handoff.task_id)

    monkeypatch.setattr(store, "query", db.query)
    monkeypatch.setattr(agents, "call", call)
    monkeypatch.setattr(agents, "sleep", sleep)
    result = asyncio.run(supervisor.execute_stage("job-outage", "classifier", {"text": "가로등 고장"}, "검증", []))
    assert result["status"] == "success"
    assert attempts == [(1, 0), (2, 3), (3, 9)]
    assert len(db.incidents) == 1
    assert next(iter(db.incidents.values()))["result"] == "recovered"


def test_db_operations_leave_backend_flag_for_agent_boundary(monkeypatch):
    import httpx
    original = httpx.AsyncClient
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=_output("classifier_streetlight", "job-flag"))
    monkeypatch.setattr(agents.httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(respond), **kwargs))
    db, cursor = MagicMock(), AsyncMock()
    db.__aenter__.return_value = db
    db.execute = AsyncMock(return_value=cursor)
    cursor.fetchone.return_value = {"id": "job-flag"}
    cursor.fetchall.return_value = [{"question_id": "location_detail", "answer": "상도동"}]
    monkeypatch.setattr(store.AsyncConnection, "connect", AsyncMock(return_value=db))
    redis = AsyncMock()
    redis.getdel.return_value = json.dumps({
        "incident_id": "inc-flag", "kind": "injection",
        "injected_at": "2026-10-11T00:00:00Z", "params": {},
    })
    monkeypatch.setattr(agents, "REDIS_URL", "redis://unused")
    monkeypatch.setattr(agents.Redis, "from_url", lambda *args, **kwargs: redis)

    async def run():
        await store.query("INSERT INTO jobs(id,text,status) VALUES (%s,%s,%s)", ("job-flag", "가로등", "running"))
        answers = await store.claim_and_store_answers("job-flag", {"location_detail": "상도동"})
        assert answers == cursor.fetchall.return_value
        redis.getdel.assert_not_awaited()
        handoff = Handoff.model_validate({
            "task_id": "job-flag", "from": "supervisor", "to": "classifier", "objective": "검증",
            "inputs": {"text": "가로등"}, "constraints": [], "attempt": 1,
            "last_failure": None, "memory_refs": [],
        })
        return await agents.call(handoff)

    result = asyncio.run(run())
    assert result["status"] == "success" and result["failure"] is None
    assert len(requests) == 1
    assert result["incident_id"] is None
    redis.getdel.assert_awaited_once_with("chaos:backend")
    assert db.execute.await_count == 4


def test_writer_retry_limit_preserves_verified_result(monkeypatch):
    db = FakeDB()
    db.jobs["job-partial"] = {}

    async def call(handoff):
        name = f"{handoff.to}_streetlight"
        if handoff.to == "writer":
            name = "writer_llm_invalid"
        return _output(name, handoff.task_id)

    monkeypatch.setattr(store, "query", db.query)
    monkeypatch.setattr(agents, "call", call)
    job = asyncio.run(supervisor.run_job("job-partial", "가로등 고장"))
    partial = job["state"]["result"]
    assert job["status"] == "failed"
    assert partial["category"] == "streetlight"
    assert partial["channels"] == job["state"]["channels"]
    assert partial["channels"] and partial["sources"]
    assert partial["draft"]["body"] == ""
    assert partial["confirm_needed"] == ["응답 내용을 확인하지 못해 확인된 데까지만 안내합니다"]
    assert job["state"]["last_result"]["failure"] == "LLM_OUTPUT_INVALID"
    validate("final", partial)


@pytest.mark.parametrize(("failure", "message"), [
    ("SERVICE_UNAVAILABLE", "응답이 없는 단계가 있어 확인된 데까지만 안내합니다"),
    ("INJECTION_BLOCKED", "공격 의심 문구가 감지되어 처리를 중단했습니다"),
    ("PII_IN_DRAFT", "검증을 통과하지 못한 부분은 공식 페이지에서 확인해 주세요"),
])
def test_failed_job_guidance_hides_codes_and_preserves_failure(monkeypatch, failure, message):
    db = FakeDB()
    db.jobs["job-guidance"] = {}

    async def call(handoff):
        if failure != "PII_IN_DRAFT" and handoff.to == "classifier":
            return {"task_id": handoff.task_id, "author": "backend",
                    "status": "retry" if failure == "SERVICE_UNAVAILABLE" else "failed",
                    "data": {}, "failure": failure, "incident_id": None}
        name = f"{handoff.to}_streetlight"
        if handoff.inputs.get("mode") == "draft":
            name = "verifier_pii_in_draft"
        return _output(name, handoff.task_id)

    monkeypatch.setattr(store, "query", db.query)
    monkeypatch.setattr(agents, "call", call)
    monkeypatch.setattr(agents, "sleep", AsyncMock())
    job = asyncio.run(supervisor.run_job("job-guidance", "가로등 고장"))
    assert job["status"] == "failed"
    assert job["state"]["last_result"]["failure"] == failure
    guidance = job["state"]["result"]["confirm_needed"]
    assert message in guidance
    assert all(not re.search(r"[A-Z][A-Z_]+", reason) for reason in guidance)
    assert db.jobs["job-guidance"]["state"]["result"]["confirm_needed"] == guidance


def test_store_connection_retry_stops_after_ten_second_backoff(monkeypatch):
    cursor, db = AsyncMock(), MagicMock()
    db.__aenter__.return_value = db
    db.execute = AsyncMock(return_value=cursor)
    cursor.fetchall.return_value = [{"id": "job-db"}]
    connect = AsyncMock(side_effect=[OperationalError("offline"), OperationalError("offline"), db])
    pauses = AsyncMock()
    monkeypatch.setattr(store.AsyncConnection, "connect", connect)
    monkeypatch.setattr(store, "sleep", pauses)
    assert asyncio.run(store.query("SELECT id FROM jobs", fetch=True)) == [{"id": "job-db"}]
    assert [call.args[0] for call in pauses.await_args_list] == [1, 2]

    connect.reset_mock(side_effect=True)
    pauses.reset_mock()
    connect.side_effect = OperationalError("still offline")
    with pytest.raises(OperationalError, match="still offline"):
        asyncio.run(store.query("SELECT id FROM jobs"))
    assert connect.await_count == 5
    assert [call.args[0] for call in pauses.await_args_list] == [1, 2, 3, 4]

    connect.reset_mock(side_effect=True)
    pauses.reset_mock()
    connect.side_effect = ProgrammingError("bad SQL")
    with pytest.raises(ProgrammingError, match="bad SQL"):
        asyncio.run(store.query("INVALID"))
    assert connect.await_count == 1
    pauses.assert_not_awaited()


def test_answer_transaction_retries_operational_failure_as_a_whole(monkeypatch):
    first, second = MagicMock(), MagicMock()
    for db in (first, second):
        db.__aenter__.return_value = db
    cursor = AsyncMock()
    cursor.fetchone.return_value = {"id": "job-answers"}
    cursor.fetchall.return_value = [{"question_id": "location_detail", "answer": "상도동"}]
    first.execute = AsyncMock(side_effect=[cursor, OperationalError("connection lost")])
    second.execute = AsyncMock(return_value=cursor)
    connect = AsyncMock(side_effect=[first, second])
    monkeypatch.setattr(store.AsyncConnection, "connect", connect)
    monkeypatch.setattr(store, "sleep", AsyncMock())
    answers = asyncio.run(store.claim_and_store_answers("job-answers", {"location_detail": "상도동"}))
    assert answers == cursor.fetchall.return_value
    assert connect.await_count == 2
    assert first.__aexit__.await_args.args[0] is OperationalError
    assert first.execute.await_count == 2 and second.execute.await_count == 3
    assert first.execute.await_args_list[0] == second.execute.await_args_list[0]


@pytest.mark.parametrize('sample_name', ['classifier_streetlight_needs_user', 'classifier_out_of_scope'])
def test_internal_recovery_survives_business_outcome(monkeypatch, sample_name):
    db = FakeDB()
    response = _output(sample_name, 'job-business')
    response['incident_id'] = 'internal-business'
    monkeypatch.setattr(store, 'query', db.query)
    monkeypatch.setattr(agents, 'call', AsyncMock(return_value=response))
    result = asyncio.run(supervisor.execute_stage('job-business', 'classifier', {'text': '민원'}, '검증', []))
    assert result['failure'] == response['failure']
    assert db.incidents['internal-business']['result'] == 'recovered'
    assert db.incidents['internal-business']['fault'] == 'LLM_OUTPUT_INVALID'


@pytest.mark.parametrize('sample_name', ['classifier_streetlight_needs_user', 'classifier_out_of_scope', 'classifier_llm_invalid'])
def test_service_outage_recovers_as_soon_as_response_arrives(monkeypatch, sample_name):
    db = FakeDB()
    response = _output(sample_name, 'job-response')
    monkeypatch.setattr(store, 'query', db.query)
    monkeypatch.setattr(agents, 'sleep', AsyncMock())
    outage = dict(task_id='job-response', author='backend', status='retry', data={}, failure='SERVICE_UNAVAILABLE', incident_id='outage')
    monkeypatch.setattr(agents, 'call', AsyncMock(side_effect=[outage, response, response]))
    asyncio.run(supervisor.execute_stage('job-response', 'classifier', {'text': '민원'}, '검증', []))
    assert db.incidents['outage']['result'] == 'recovered'
    if response['failure'] == 'LLM_OUTPUT_INVALID':
        invalid = [i for i in db.incidents.values() if i['fault'] == 'LLM_OUTPUT_INVALID']
        assert len(invalid) == 1 and invalid[0]['result'] == 'failed'
