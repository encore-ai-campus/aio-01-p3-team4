import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.exceptions import RedisError

from app.clients import mailpit
from app.core.config import SERVICE
from app.core.errors import (
    CorruptFlagError,
    SelectionError,
    corrupt_flag_error,
    redis_error,
    selection_error,
)
from app.routers.api import router


@asynccontextmanager
async def lifespan(app):
    try:
        yield
    finally:
        pending = list(mailpit.tasks)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


app = FastAPI(title=f"생활 민원 길잡이 / {SERVICE}", lifespan=lifespan)
app.add_exception_handler(RedisError, redis_error)
app.add_exception_handler(CorruptFlagError, corrupt_flag_error)
app.add_exception_handler(SelectionError, selection_error)
app.include_router(router)
