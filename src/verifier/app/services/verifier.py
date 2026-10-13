from copy import deepcopy
from fastapi import HTTPException
from redis.asyncio import Redis
from redis.exceptions import RedisError
from app.clients.chaos import ChaosClient
from app.core.config import REDIS_URL, SERVICE
from app.schemas.contract import Handoff, StageResult
from app.services import channel, draft


async def run(handoff: Handoff) -> StageResult:
    if handoff.to != SERVICE:
        raise HTTPException(422, "수신 서비스가 다릅니다")
    inputs = deepcopy(handoff.inputs)
    redis = Redis.from_url(REDIS_URL, socket_connect_timeout=0.5, socket_timeout=0.5) if REDIS_URL else None

    async def inspect(value, break_links=False):
        result = await channel.check(value, break_links=break_links) if value["mode"] == "channel" else draft.check(value)
        failure = result.pop("failure")
        status = "success" if failure in (None, "STALE_INFO") else "failed" if value["mode"] == "channel" else "retry"
        return {"task_id": handoff.task_id, "author": SERVICE, "status": status,
                "data": {"verdict": {"success": "PASS", "retry": "RETRY", "failed": "FAIL"}[status], **result},
                "failure": failure, "incident_id": None}

    async def operation():
        return await inspect(inputs)

    async def inject(flag):
        channel_kinds = ("link_dead", "not_official", "stale_info")
        if flag.kind not in channel_kinds:
            return await operation()
        applicable = inputs["mode"] == "channel" and bool(inputs["channels"])
        if not applicable:
            try:
                await redis.set("chaos:verifier", flag.model_dump_json())
            except RedisError:
                pass
            return await operation()
        value = deepcopy(inputs)
        if flag.kind == "not_official":
            for item in value["channels"]:
                item["source_url" if item["method"] == "phone" else "url"] = "https://epeople-go.kr.example/report"
        elif flag.kind == "stale_info":
            value["channels"][0]["verified_at"] = "2020-01-01"
        output = await inspect(value, break_links=flag.kind == "link_dead")
        output["incident_id"] = flag.incident_id
        return output

    try:
        output = await ChaosClient(redis).run(SERVICE, operation, inject)
    finally:
        if redis:
            await redis.aclose()
    return StageResult.model_validate(output)
