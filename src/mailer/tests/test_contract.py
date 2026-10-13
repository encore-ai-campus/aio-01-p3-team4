import json
import pytest
from app.core.config import CONTRACTS
from app.schemas.contract import Handoff, StageResult

SAMPLES = CONTRACTS / "samples"
PREFIX = "mailer"

@pytest.mark.parametrize("path", sorted(SAMPLES.glob(f"{PREFIX}*_*put.json")), ids=lambda p: p.stem)
def test_contract_roundtrip(path):
    value = json.loads(path.read_text())
    model = Handoff if path.name.endswith("_input.json") else StageResult
    assert model.model_validate(value).model_dump(by_alias=True) == value


def test_health_and_reject_invalid_contract(client, handoff):
    assert client.get("/health").json() == {"status": "ok", "service": "mailer", "mode": "live"}
    value = handoff()
    value["inputs"]["unexpected"] = True
    assert client.post("/run", json=value).status_code == 422
    value = json.loads((SAMPLES / "writer_streetlight_input.json").read_text())
    response = client.post("/run", json=value)
    assert response.status_code == 422
    assert response.json()["detail"] == "수신 서비스가 다릅니다"
    for name in ("mailer_mail_down", "mailer_blocked"):
        value = json.loads((SAMPLES / f"{name}_output.json").read_text())
        value["status"] = "success"
        with pytest.raises(ValueError):
            StageResult.model_validate(value)
        value["status"] = "retry" if name.endswith("down") else "failed"
        value["data"]["unexpected"] = True
        with pytest.raises(ValueError):
            StageResult.model_validate(value)
