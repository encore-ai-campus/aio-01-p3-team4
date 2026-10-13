import json
import psycopg
import pytest
from fastapi.testclient import TestClient
from app.core.config import CONTRACTS, SEEDS_DIR
from app.main import app
from app.services.catalog import load_catalog, seed_paths
from app.schemas.contract import StageResult


@pytest.fixture
def catalog():
    return load_catalog(seed_paths(SEEDS_DIR))


@pytest.fixture
def client(monkeypatch, catalog):
    class Repository:
        async def seed(self):
            return 0
        async def candidates(self, inputs):
            return catalog
    monkeypatch.setattr(app.state, "repository", Repository())
    with TestClient(app) as client:
        yield client


def request(category="streetlight"):
    value = json.loads((CONTRACTS / "samples" / "locator_input.json").read_text(encoding="utf-8"))
    value["inputs"]["category"] = category
    return value


@pytest.mark.parametrize("category", ["streetlight", "road_damage", "illegal_dumping"])
def test_three_categories_and_fallback(client, catalog, category):
    body = request(category)
    for level in ("district", "city", "national"):
        response = client.post("/run", json=body)
        assert response.status_code == 200
        result = StageResult.model_validate(response.json())
        assert result.status == "success"
        assert result.task_id == body["task_id"]
        assert result.data["fallback_level"] == level
        assert 1 <= len(result.data["channels"]) <= (5 if level == "district" else 3)
        if level == "district":
            assert result.data["channels"][0]["channel_id"] == f"dj-{category}-01"
        body["inputs"]["exclude_channel_ids"].extend(row["channel_id"] for row in catalog if (level == "district" and row["sigungu"]) or (level == "city" and row["sido"] and not row["sigungu"]) or (level == "national" and not row["sido"]))
    result = client.post("/run", json=body).json()
    assert result["status"] == "failed" and result["failure"] == "NO_CHANNEL"
    assert result["data"] == {"channels": [], "fallback_level": "national"}


@pytest.mark.parametrize("region", [{"sido": "서울특별시", "sigungu": "관악구"}, {"sido": None, "sigungu": None}, {"sido": "부산광역시", "sigungu": "동작구"}])
def test_unknown_or_unsupported_region_uses_national(client, region):
    body = request()
    body["inputs"]["region"].update(region)
    result = client.post("/run", json=body).json()
    assert result["data"]["fallback_level"] == "national"
    assert all(c["jurisdiction"] == "전국" for c in result["data"]["channels"])


def test_out_of_scope(client):
    assert client.post("/run", json=request("out_of_scope")).json()["data"]["fallback_level"] == "national"


def test_input_contract(client):
    body = request()
    body["inputs"]["unexpected"] = True
    assert client.post("/run", json=body).status_code == 422
    assert client.post("/run", json=request("all")).status_code == 422
    body = json.loads((CONTRACTS / "samples" / "classifier_input.json").read_text(encoding="utf-8"))
    assert client.post("/run", json=body).status_code == 422


def test_db_failure_is_503_not_no_channel(client, monkeypatch):
    async def broken(inputs):
        raise psycopg.OperationalError("secret connection detail")
    monkeypatch.setattr(app.state.repository, "candidates", broken)
    response = client.post("/run", json=request())
    assert response.status_code == 503
    assert "secret" not in response.text


def test_inactive_are_never_returned(client, catalog):
    for row in catalog:
        row["active"] = False
    assert client.post("/run", json=request()).json()["failure"] == "NO_CHANNEL"


def test_phone_recommendations_can_still_be_disabled(client, monkeypatch):
    from app.routers import api
    monkeypatch.setattr(api, "INCLUDE_PHONE", False)
    result = client.post("/run", json=request()).json()
    assert [row["channel_id"] for row in result["data"]["channels"]] == ["dj-all-01"]
