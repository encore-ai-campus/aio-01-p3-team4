from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.core.config import SERVICE, DATABASE_URL, REDIS_URL, SEEDS_DIR
from app.clients.channels import ChannelRepository
from app.routers.api import router

@asynccontextmanager
async def lifespan(app: FastAPI):
    await app.state.repository.seed()
    yield


app = FastAPI(title=f"생활 민원 길잡이 / {SERVICE}", lifespan=lifespan)
app.state.repository = ChannelRepository(DATABASE_URL, SEEDS_DIR, REDIS_URL)
app.include_router(router)
