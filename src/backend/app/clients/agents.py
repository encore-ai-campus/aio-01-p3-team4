import os
import json
from time import perf_counter, time
from asyncio import sleep
import httpx
from redis.asyncio import Redis
from redis.exceptions import RedisError
from app.clients.chaos import ChaosClient
from app.core.config import REDIS_URL
from app.schemas.contract import Handoff, StageResult

AGENT_TIMEOUT_SEC = 15


async def wait_before_retry(failure: str | None, attempt: int) -> None:
    if failure == "SERVICE_UNAVAILABLE":
        await sleep(3 * 2 ** (attempt - 1))  # 3초 + 6초: 5초 중단과 앱 초기화까지 대기


async def call(handoff: Handoff) -> dict:
    started, requested_at = perf_counter(), time()
    async def operation():
        url = os.getenv(handoff.to.upper() + "_URL", f"http://{handoff.to}:8000")
        async with httpx.AsyncClient(timeout=AGENT_TIMEOUT_SEC) as client:
            response = await client.post(url + "/run", json=handoff.model_dump(by_alias=True))
            response.raise_for_status()
            result = StageResult.model_validate(response.json())
            if result.task_id != handoff.task_id or result.author != handoff.to:
                raise ValueError("응답 task_id/author 불일치")
            return result.model_dump()
    redis = Redis.from_url(REDIS_URL, socket_connect_timeout=0.5, socket_timeout=0.5) if REDIS_URL else None
    try:
        return await ChaosClient(redis).run("backend", operation)
    except httpx.HTTPError as exc:
        broken = f"{type(exc).__name__} · {perf_counter() - started:.1f}초 경과"
        if isinstance(exc, httpx.TimeoutException):
            broken += f" · 제한 {AGENT_TIMEOUT_SEC}초"
        if redis and handoff.to == "classifier":
            try:
                delay = json.loads(await redis.getdel(f"evidence:delay:{handoff.task_id}") or "null")
                if delay and delay["observed_at"] >= requested_at:
                    broken = f"{delay['seconds']:g}초 지연 주입 · {broken}"
            except (RedisError, ValueError, TypeError, KeyError):
                pass
        return dict(task_id=handoff.task_id, author="backend", status="retry", data={"evidence": {"broken": broken}}, failure="SERVICE_UNAVAILABLE", incident_id=None)
    finally:
        if redis:
            await redis.aclose()
