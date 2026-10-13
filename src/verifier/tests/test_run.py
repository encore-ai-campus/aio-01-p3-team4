import json
import pytest
from fastapi.testclient import TestClient
from app.core.config import CONTRACTS
from app.main import app
from app.schemas.contract import validate


def scenario(path):
    name = path.stem.removeprefix("verifier_").removesuffix("_input")
    prefix = {"link_dead": "E1", "not_official": "B3", "stale_info": "E2", "pii_in_draft": "C7"}.get(name)
    return f"{prefix}-{name}" if prefix else name


@pytest.mark.parametrize("path", sorted((CONTRACTS / "samples").glob("verifier*input.json")), ids=scenario)
def test_run_sample(path, post):
    handoff = json.loads(path.read_text())
    expected = json.loads(path.with_name(path.name.replace("_input.json", "_output.json")).read_text())
    body = post(handoff)
    assert (body["status"], body["failure"], body["data"]["verdict"], body["data"]["passed_channel_ids"]) == (
        expected["status"], expected["failure"], expected["data"]["verdict"], expected["data"]["passed_channel_ids"])
    validate("stage_result", body)
    handoff["inputs"]["unexpected"] = True
    assert TestClient(app).post("/run", json=handoff).status_code == 422


def test_wrong_recipient(handoff):
    value = handoff()
    value["to"] = "writer"
    value["inputs"] = {"category": "streetlight", "region": value["inputs"]["region"],
                       "details": {"location_detail": None, "occurred_at": None, "has_photo": None},
                       "channel": value["inputs"]["channels"][0]}
    response = TestClient(app).post("/run", json=value)
    assert response.status_code == 422
    assert response.json()["detail"] == "수신 서비스가 다릅니다"
