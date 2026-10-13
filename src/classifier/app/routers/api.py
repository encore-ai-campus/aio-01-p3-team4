from fastapi import APIRouter
from app.core.config import SERVICE
from app.schemas.contract import Handoff, StageResult
from app.services.classification import run

router = APIRouter()

@router.get("/health")
def health():
    return {"status": "ok", "service": SERVICE, "mode": "agent"}

@router.post("/run", response_model=StageResult)
async def run_stage(handoff: Handoff):
    return await run(handoff)
