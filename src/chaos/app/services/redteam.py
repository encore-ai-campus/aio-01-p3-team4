from datetime import datetime, timezone
from uuid import uuid4

from app.clients import redteam
from app.clients.chaos import ChaosFlag
from app.core.errors import SelectionError
from app.schemas.redteam import RedteamRequest, RedteamResult
from app.services import flags


async def run(body: RedteamRequest) -> RedteamResult:
    selection = await redteam.choose(body)
    if not any(item.target == selection.target and item.kind == selection.kind for item in body.candidates):
        raise SelectionError(502, 'Redteam model selected an unlisted candidate')
    flag = ChaosFlag(
        incident_id=uuid4().hex,
        kind=selection.kind,
        injected_at=datetime.now(timezone.utc),
        params={"seconds": 20} if selection.kind == "delay" else {},
    )
    await flags.write_flag(selection.target, flag)
    return RedteamResult(**selection.model_dump(), incident_id=flag.incident_id, injected_at=flag.injected_at)
