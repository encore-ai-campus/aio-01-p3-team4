import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient
from app.clients import agents, store
from app.main import app
from app.routers import api
from app.services import ops
from test_live_api import FakeLiveDB, _sample


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("fail", [False, True])
def test_background_response_precedes_pipeline_and_persists_finish(monkeypatch, resume, fail):
    async def scenario():
        db, release = FakeLiveDB(), asyncio.Event()
        job_id = "job-resume"
        if resume:
            db.jobs[job_id] = {"id": job_id, "text": "가로등 고장", "status": "needs_user",
                "state": {"question_round": 1, "last_result": {"data": {"questions": [{"id": "q1"}]}}}}

        async def query(sql, params=(), fetch=False):
            if sql.startswith("UPDATE jobs SET status='failed'"):
                assert "completed_at=now()" in sql
                db.jobs[params[1]].update(status="failed", state=params[0].obj,
                                         completed_at=datetime.now(timezone.utc))
                return
            return await db.query(sql, params, fetch)

        async def call(handoff):
            await release.wait()
            if fail:
                raise RuntimeError("unexpected pipeline failure")
            name = f"{handoff.to}_streetlight" + ("_draft" if handoff.inputs.get("mode") == "draft" else "")
            return _sample(name, handoff.task_id)

        monkeypatch.setattr(store, "query", query)
        monkeypatch.setattr(store, "claim_and_store_answers", db.claim_and_store_answers)
        monkeypatch.setattr(agents, "call", call)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            path = f"/jobs/{job_id}/answers" if resume else "/jobs"
            body = {"answers": {"q1": "서울 동작구"}} if resume else {"text": "가로등 고장"}
            response = await asyncio.wait_for(client.post(path, json=body), 1)
            job = response.json()
            assert response.status_code == (200 if resume else 201)
            assert job == {"id": job["id"], "status": "running",
                           "state": {"question_round": int(resume), "confirm_needed": []}}
            assert api.background_jobs and not release.is_set()
            tasks = tuple(api.background_jobs)
            release.set()
            await asyncio.gather(*tasks)
            await asyncio.sleep(0)
            assert not api.background_jobs
            final = (await client.get(f"/jobs/{job['id']}")).json()
            assert final["status"] == ("failed" if fail else "success")
            assert final["completed_at"]
            assert isinstance(final["state"]["confirm_needed"], list)
            if not fail:
                assert final["state"]["result"]["category"] == "streetlight"
                assert final["state"]["last_result"]["status"] == "success"
    asyncio.run(scenario())


@pytest.mark.parametrize("hours", [0, 24, 168])
def test_overview_window_counts_probes_and_bounded_series(monkeypatch, hours):
    calls = []
    now = datetime.now(timezone.utc)

    async def query(sql, params=(), fetch=False):
        calls.append((sql, params))
        if sql == ops.JOBS_SQL:
            return [dict(total=6, success=3, needs_user=1, failed=1, running=1,
                         avg_duration_sec=12, p95_duration_sec=20, first_at=now-timedelta(days=120))]
        if sql == ops.AGENTS_SQL:
            return [dict(agent="classifier", calls=8, success=4, retry=2, failed=1, needs_user=1)]
        if sql == ops.EVENTS_SQL:
            return [dict(at=now, job_id="job-1", agent="classifier", status="success", failure=None, attempt=1)]
        if sql == ops.SERIES_SQL:
            return [dict(bucket=int(params[1].timestamp()//params[-1]), success=3, needs_user=1, failed=1, incidents=2)]
        return []

    real_client = httpx.AsyncClient
    seen = []

    async def respond(request):
        seen.append(str(request.url))
        # Every HTTP probe must start before any finishes: serial probing would time out.
        while len(seen) < 7:
            await asyncio.sleep(0)
        return httpx.Response(503 if request.url.host == "writer" else 200, json={"status": "ok"})

    class RedisProbe:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        ping = AsyncMock(return_value=True)

    monkeypatch.setattr(store, "query", query)
    monkeypatch.setattr(ops.Redis, "from_url", lambda _: RedisProbe())
    monkeypatch.setattr(ops.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw))
    for service in ops.SERVICES:
        monkeypatch.delenv(f"{service.upper()}_URL", raising=False)
    response = TestClient(app).get(f"/ops/overview?hours={hours}")
    assert response.status_code == 200
    body = response.json()
    assert body["window_hours"] == hours and body["jobs"]["total"] == 6
    assert body["jobs"]["success"] == 3 and body["jobs"]["avg_duration_sec"] == 12
    assert body["agents"][0]["retry"] == 2
    assert body["recent_events"][0]["failure"] is None
    assert len(body["series"]["points"]) <= 48
    assert sum(p["success"] for p in body["series"]["points"]) == 3
    assert sum(p["incidents"] for p in body["series"]["points"]) == 2
    assert {s["id"] for s in body["services"]} == set(ops.SERVICES)
    assert [s["id"] for s in body["services"] if not s["ok"]] == ["writer"]
    assert any(e.startswith("writer:") for e in body["errors"])
    assert len(seen) == 7 and "http://127.0.0.1:8000/health" in seen
    for sql, params in calls:
        if sql in (ops.JOBS_SQL, ops.AGENTS_SQL, ops.RECENT_SQL, ops.EVENTS_SQL):
            assert (params[0] is None) if not hours else params[1]-params[0] == timedelta(hours=hours)
    assert "text" not in body and "place_text" not in ops.RECENT_SQL


def test_overview_database_failure_is_partial_and_invalid_window_is_422(monkeypatch):
    monkeypatch.setattr(store, "query", AsyncMock(side_effect=RuntimeError("database unavailable")))
    async def probe(service, client, errors):
        return {"id": service, "ok": service != "data_pg", "latency_ms": 1}
    monkeypatch.setattr(ops, "probe", probe)
    client = TestClient(app)
    response = client.get("/ops/overview")
    assert response.status_code == 200
    assert response.json()["jobs"]["total"] == 0
    assert len(response.json()["errors"]) == 5
    for hours in (1, -1, "invalid"):
        assert client.get(f"/ops/overview?hours={hours}").status_code == 422


def test_database_probe_timeout_cancels_store_retries(monkeypatch):
    async def scenario():
        cancelled = asyncio.Event()
        async def hanging_query(*args, **kwargs):
            try:
                await asyncio.sleep(60)
            finally:
                cancelled.set()
        monkeypatch.setattr(store, "query", hanging_query)
        errors = []
        result = await asyncio.wait_for(ops.probe("data_pg", None, errors), timeout=2)
        assert not result["ok"] and cancelled.is_set() and errors
    asyncio.run(scenario())
