import logging
import psycopg
from fastapi import APIRouter, HTTPException, Request
from app.core.config import SERVICE, INCLUDE_PHONE
from app.schemas.contract import Handoff, StageResult
from app.services.locator import run

router = APIRouter()

@router.get("/health")
def health():
    return {"status": "ok", "service": SERVICE, "mode": "database"}

@router.post("/run", response_model=StageResult)
async def run_stage(handoff: Handoff, request: Request):
    if handoff.to != SERVICE:
        raise HTTPException(status_code=422, detail="handoff.to must be locator")
    try:
        return await run(handoff, request.app.state.repository, include_phone=INCLUDE_PHONE)
    except (psycopg.Error, OSError, TimeoutError) as exc:
        # No connection string or raw DB exception in public responses/logs.
        logging.getLogger(__name__).warning("channel database unavailable (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="channel database unavailable") from None
