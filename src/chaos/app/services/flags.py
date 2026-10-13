import httpx
from fastapi import HTTPException
from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import ConnectionError

from app.clients import mailpit
from app.clients.chaos import ChaosFlag, Target
from app.core.config import REDIS_URL
from app.core.errors import CorruptFlagError


def redis_client() -> Redis:
    if not REDIS_URL:
        raise ConnectionError("Redis is not configured")
    return Redis.from_url(REDIS_URL, socket_connect_timeout=1, socket_timeout=1)


async def read_flag(target: Target) -> ChaosFlag | None:
    async with redis_client() as redis:
        raw = await redis.get(f'chaos:{target}')
        if raw is None:
            return None
        try:
            return ChaosFlag.model_validate_json(raw)
        except ValidationError as exc:
            raise CorruptFlagError() from exc


async def write_flag(target: Target, flag: ChaosFlag) -> ChaosFlag:
    async with mailpit.lock:
        seconds = await mailpit.prepare(flag) if target == "mailer" and flag.kind == "mail_down" else None
        async with redis_client() as redis:
            await redis.set(f'chaos:{target}', flag.model_dump_json())
            if seconds is not None:
                try:
                    await mailpit.stop(seconds)
                except BaseException as error:
                    await redis.delete(f'chaos:{target}')
                    if isinstance(error, httpx.HTTPError):
                        raise HTTPException(503, "Mailpit Docker control unavailable") from None
                    raise
            return flag


async def delete_flag(target: Target) -> None:
    async with redis_client() as redis:
        await redis.delete(f'chaos:{target}')
