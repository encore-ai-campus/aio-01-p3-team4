from fastapi import APIRouter
from app.core.config import SERVICE
from app.schemas.contract import Handoff, StageResult
from app.services import outbox
from app.services.mailer import run

router = APIRouter()

@router.get("/health")
def health():
    return {"status": "ok", "service": SERVICE, "mode": "live"}

@router.post("/run", response_model=StageResult)
async def run_stage(handoff: Handoff):
    return await run(handoff)

@router.get("/outbox")
def list_outbox():
    items = sorted(outbox.get().items.values(), key=lambda item: item["queued_at"], reverse=True)
    return {"items": [{key: value for key, value in item.items() if key != "body"} for item in items]}
