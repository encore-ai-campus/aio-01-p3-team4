import asyncio
from contextlib import asynccontextmanager, suppress
from fastapi import FastAPI
from app.core import config
from app.routers.api import router
from app.services import outbox


async def resend():
    while True:
        await asyncio.sleep(config.OUTBOX_INTERVAL_SEC)
        try:
            await outbox.flush(outbox.now())
        except Exception as exc:
            print(f"mailer outbox error={type(exc).__name__}: {exc}")


@asynccontextmanager
async def lifespan(app):
    outbox.get()
    task = asyncio.create_task(resend())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title=f"생활 민원 길잡이 / {config.SERVICE}", lifespan=lifespan)
app.include_router(router)
