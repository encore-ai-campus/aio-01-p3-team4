from fastapi import APIRouter

from app.clients.chaos import ChaosFlag, Target
from app.schemas.redteam import RedteamRequest, RedteamResult
from app.services import flags
from app.services import redteam as redteam_service

router = APIRouter()

@router.get("/health")
def health():
    return {"status": "ok", "service": "chaos"}

@router.get("/flags/{target}")
async def get_flag(target: Target) -> ChaosFlag | None:
    return await flags.read_flag(target)

@router.put("/flags/{target}")
async def put_flag(target: Target, flag: ChaosFlag) -> ChaosFlag:
    return await flags.write_flag(target, flag)

@router.delete("/flags/{target}")
async def delete_flag(target: Target) -> None:
    return await flags.delete_flag(target)

@router.post("/run")
async def redteam(body: RedteamRequest) -> RedteamResult:
    return await redteam_service.run(body)
