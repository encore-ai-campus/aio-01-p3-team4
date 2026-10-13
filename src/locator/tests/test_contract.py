import json
import pytest
from fastapi.testclient import TestClient
from app.core.config import CONTRACTS, SERVICE
from app.main import app
from app.schemas.contract import Handoff, StageResult, validate

SAMPLES = CONTRACTS / "samples"
PREFIX = SERVICE if SERVICE in ("classifier", "locator", "verifier", "writer") else "*"

@pytest.mark.parametrize("path", sorted(SAMPLES.glob(f"{PREFIX}_*put.json")), ids=lambda p: p.stem)
def test_contract_roundtrip(path):
    value = json.loads(path.read_text(encoding="utf-8"))
    if path.name.startswith("final_"):
        assert validate("final", value) == value
        return
    model = Handoff if path.name.endswith("_input.json") else StageResult
    assert model.model_validate(value).model_dump(by_alias=True) == value


def test_reject_invalid_contract():
    value = json.loads((SAMPLES / "classifier_input.json").read_text(encoding="utf-8"))
    value["inputs"] = {"text": 42}
    with pytest.raises(ValueError):
        Handoff.model_validate(value)
    value = json.loads((SAMPLES / "verifier_pii_in_draft_output.json").read_text(encoding="utf-8"))
    value["status"] = "failed"
    with pytest.raises(ValueError):
        StageResult.model_validate(value)


def test_health():
    assert TestClient(app).get("/health").json()["status"] == "ok"
