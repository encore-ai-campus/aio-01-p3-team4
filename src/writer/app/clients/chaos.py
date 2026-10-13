from collections.abc import Awaitable, Callable
from typing import Literal, TypeVar
import asyncio
import json
from time import time
from pydantic import AwareDatetime, BaseModel, ConfigDict, field_validator
from redis.asyncio import Redis
from redis.exceptions import RedisError

Target = Literal["backend", "classifier", "locator", "verifier", "writer", "mailer"]
Kind = Literal["llm_invalid", "link_dead", "stale_info", "pii_leak", "not_official", "injection", "delay", "mail_down"]
T = TypeVar("T")

class ChaosFlag(BaseModel):
    model_config = ConfigDict(extra="forbid")
    incident_id: str
    kind: Kind
    injected_at: AwareDatetime
    params: dict

    @field_validator("params")
    @classmethod
    def validate_repeat(cls, params):
        repeat = params.get("repeat", 1)
        if type(repeat) is not int or not 1 <= repeat <= 10:
            raise ValueError("repeat must be an integer from 1 to 10")
        return params

class ChaosClient:
    """PL 관리 복사본: Redis를 확인한 뒤 로컬 주입 함수 또는 원래 작업을 실행."""
    def __init__(self, redis: Redis | None):
        self.redis = redis

    async def run(self, target: Target, operation: Callable[[], Awaitable[T]], inject=None, *, task_id=None) -> T:
        if target not in ("backend", "classifier", "locator", "verifier", "writer", "mailer"):
            raise ValueError("알 수 없는 장애 대상")
        try:
            raw = await self.redis.getdel(f"chaos:{target}") if self.redis else None
        except RedisError:
            raw = None
        flag = ChaosFlag.model_validate_json(raw) if raw else None
        if flag and flag.params.get("repeat", 1) > 1:
            remaining = flag.model_copy(update={"params": {**flag.params, "repeat": flag.params["repeat"] - 1}})
            try:
                await self.redis.set(f"chaos:{target}", remaining.model_dump_json())
            except RedisError:
                pass
        if flag and flag.kind == "delay":
            seconds = min(max(float(flag.params.get("seconds", 1)), 0), 30)
            if task_id:
                try:
                    await self.redis.set(f"evidence:delay:{task_id}", json.dumps({"seconds": seconds,
                        "incident_id": flag.incident_id, "observed_at": time()}), ex=60)
                except RedisError:
                    pass
            await asyncio.sleep(seconds)
        elif flag and inject:
            return await inject(flag)
        return await operation()
