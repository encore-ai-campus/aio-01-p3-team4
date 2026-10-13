from fastapi import FastAPI
from app.core.config import SERVICE
from app.routers.api import router

app = FastAPI(title=f"생활 민원 길잡이 / {SERVICE}")
app.include_router(router)
