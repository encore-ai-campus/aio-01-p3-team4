"""Read-only operational aggregates; complaint text never leaves these queries."""
import asyncio
import os
from datetime import datetime, timedelta, timezone
from time import perf_counter

import httpx
from redis.asyncio import Redis
from app.clients import store
from app.core.config import REDIS_URL

AGENTS = ("classifier", "locator", "verifier", "writer", "mailer")
SERVICES = ("backend", *AGENTS, "chaos", "data_pg", "data_redis")
WINDOW = "created_at >= COALESCE(%s, '-infinity'::timestamptz) AND created_at <= %s"
COUNTS = ", ".join(f"count(*) FILTER (WHERE status='{s}') AS {s}"
                   for s in ("success", "needs_user", "failed", "running"))
JOBS_SQL = f"""SELECT count(*) AS total, {COUNTS},
    avg(extract(epoch FROM completed_at-created_at)) AS avg_duration_sec,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM completed_at-created_at)) AS p95_duration_sec,
    least(min(created_at), (SELECT min(COALESCE(detected_at,injected_at)) FROM incidents)) AS first_at
    FROM jobs WHERE {WINDOW}"""
AGENTS_SQL = f"""SELECT agent, count(*) AS calls,
    count(*) FILTER (WHERE payload->>'status'='success') AS success,
    count(*) FILTER (WHERE payload->>'status'='retry') AS retry,
    count(*) FILTER (WHERE payload->>'status'='failed') AS failed,
    count(*) FILTER (WHERE payload->>'status'='needs_user') AS needs_user
    FROM timeline_events WHERE {WINDOW} GROUP BY agent ORDER BY agent"""
RECENT_SQL = f"""WITH recent AS (
    SELECT id,created_at,completed_at,status,
      COALESCE(state->'classification',state->'result',state#>'{{last_result,data}}','{{}}'::jsonb) AS classified
    FROM jobs WHERE {WINDOW} ORDER BY created_at DESC,id DESC LIMIT 30)
    SELECT id,created_at,status,extract(epoch FROM completed_at-created_at) AS duration_sec,
      classified->>'category' AS category,
      NULLIF(concat_ws(' ',classified#>>'{{region,sido}}',classified#>>'{{region,sigungu}}',
                          classified#>>'{{region,dong}}'),'') AS region,
      COALESCE((SELECT jsonb_agg(jsonb_build_object('agent',agent,'status',payload->>'status') ORDER BY id)
        FROM timeline_events WHERE job_id=recent.id),'[]'::jsonb) AS stages,
      (SELECT count(*) FROM incidents WHERE job_id=recent.id) AS incident_count
    FROM recent ORDER BY created_at DESC,id DESC"""
EVENTS_SQL = f"""SELECT created_at AS at,job_id,agent,payload->>'status' AS status,
    payload->>'failure' AS failure,attempt,(payload->>'review'='true') AS review FROM timeline_events WHERE {WINDOW}
    ORDER BY created_at DESC,id DESC LIMIT 80"""
SERIES_SQL = """WITH samples AS (
    SELECT created_at AS at,status,0 AS incident FROM jobs WHERE created_at >= %s AND created_at <= %s
    UNION ALL SELECT COALESCE(detected_at,injected_at),NULL,1 FROM incidents
      WHERE COALESCE(detected_at,injected_at) >= %s AND COALESCE(detected_at,injected_at) <= %s)
    SELECT floor(extract(epoch FROM at)/%s)::bigint AS bucket,
      count(*) FILTER (WHERE status='success') AS success,
      count(*) FILTER (WHERE status='needs_user') AS needs_user,
      count(*) FILTER (WHERE status='failed') AS failed,sum(incident) AS incidents
    FROM samples GROUP BY bucket ORDER BY bucket"""


async def probe(service, client, errors):
    started = perf_counter()

    async def check():
        if service == "data_pg":
            await store.query("SELECT 1", fetch=True)
        elif service == "data_redis":
            async with Redis.from_url(REDIS_URL) as redis:
                if not await redis.ping():
                    raise ValueError("Redis PING failed")
        else:
            default = "http://127.0.0.1:8000" if service == "backend" else f"http://{service}:8000"
            url = os.getenv(f"{service.upper()}_URL", default).rstrip("/")
            response = await client.get(f"{url}/health")
            response.raise_for_status()
    try:
        await asyncio.wait_for(check(), timeout=1.5)
        ok = True
    except Exception:
        ok = False
        errors.append(f"{service}: 상태를 확인하지 못했어요")
    return {"id": service, "ok": ok, "latency_ms": round((perf_counter()-started)*1000, 1)}


async def overview(hours):
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours) if hours else None
    errors = []

    async def query(name, sql, params):
        try:
            return await asyncio.wait_for(store.query(sql, params, fetch=True), timeout=3) or []
        except Exception:
            errors.append(f"{name}: 기록을 불러오지 못했어요")
            return []

    async with httpx.AsyncClient(timeout=1.5) as client:
        values = await asyncio.gather(
            query("jobs", JOBS_SQL, (since, now)), query("agents", AGENTS_SQL, (since, now)),
            query("recent_jobs", RECENT_SQL, (since, now)), query("recent_events", EVENTS_SQL, (since, now)),
            asyncio.gather(*(probe(service, client, errors) for service in SERVICES)))
    job_rows, agents, recent, events, services = values
    jobs = dict(job_rows[0]) if job_rows else dict(total=0, success=0, needs_user=0, failed=0,
                                                 running=0, avg_duration_sec=None, p95_duration_sec=None)
    first = since or jobs.get("first_at") or now
    jobs.pop("first_at", None)
    end = now.timestamp()
    start = min(first.timestamp(), end)
    step = next((s for s in (300, 900, 3600, 21600, 86400)
                 if int(end//s)-int(start//s) < 48), 86400)
    # All-time totals are retained; a history over 48 days shows the latest 48 daily buckets.
    first_bucket, last_bucket = max(int(start//step), int(end//step)-47), int(end//step)
    chart_since = max(first, datetime.fromtimestamp(first_bucket*step, timezone.utc))
    rows = await query("series", SERIES_SQL, (chart_since, now, chart_since, now, step))
    buckets = {int(row["bucket"]): row for row in rows}
    points = [{"t": datetime.fromtimestamp(b*step, timezone.utc),
               **{key: buckets.get(b, {}).get(key, 0) for key in ("success", "needs_user", "failed", "incidents")}}
              for b in range(first_bucket, last_bucket+1)]
    by_agent = {row["agent"]: row for row in agents}
    agent_rows = [by_agent.get(agent, dict(agent=agent, calls=0, success=0, retry=0, failed=0, needs_user=0))
                  for agent in (*AGENTS, *(a for a in by_agent if a not in AGENTS))]
    return {"generated_at": now, "window_hours": hours, "services": services, "jobs": jobs,
            "agents": agent_rows, "series": {"step_sec": step, "points": points},
            "recent_jobs": recent, "recent_events": events, "errors": errors}
