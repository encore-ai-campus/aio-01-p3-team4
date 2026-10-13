from app.schemas.contract import Handoff, StageResult
from app.services.catalog import select_channels


async def run(handoff: Handoff, repository, *, include_phone: bool = False) -> StageResult:
    rows = await repository.candidates(handoff.inputs)
    data = select_channels(rows, handoff.inputs, include_phone=include_phone)
    found = bool(data["channels"])
    return StageResult(task_id=handoff.task_id, author="locator", status="success" if found else "failed", data=data, failure=None if found else "NO_CHANNEL", incident_id=None)
