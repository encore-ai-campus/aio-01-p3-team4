import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from fastapi import FastAPI
from app.services import dispatch
from app.core.config import SERVICE
from app.routers.api import router

async def reconcile_mail():
    while True:
        await asyncio.sleep(5)
        try:
            await dispatch.reconcile_outbox()
        except Exception as error:
            logging.getLogger(__name__).warning("메일 복구 확인 실패 error_type=%s", type(error).__name__)


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(reconcile_mail())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title=f"생활 민원 길잡이 / {SERVICE}", lifespan=lifespan)
app.include_router(router)
