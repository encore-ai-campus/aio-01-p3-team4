from decimal import Decimal
import asyncio
from fastapi.testclient import TestClient
from app.clients import store
from app.main import app
from app.services import incidents as incident_service


def test_metrics_empty_data_returns_all_zeros_and_http_200(monkeypatch):
    """1. 데이터가 0건인 경우 (COUNT(*) -> 0, auto_recovered -> 0, rate -> 0, avg -> 0)."""
    async def mock_query(sql, params=(), fetch=False):
        return [{
            "total": 0,
            "auto_recovered": 0,
            "auto_recovery_rate": 0.0,
            "avg_recovery_sec": 0.0,
        }]

    monkeypatch.setattr(store, "query", mock_query)
    client = TestClient(app)
    res = client.get("/metrics")

    assert res.status_code == 200
    data = res.json()
    assert data == {
        "total": 0,
        "auto_recovered": 0,
        "auto_recovery_rate": 0,
        "avg_recovery_sec": 0,
    }
    assert set(data.keys()) == {"total", "auto_recovered", "auto_recovery_rate", "avg_recovery_sec"}


def test_metrics_partial_auto_recovery_rate(monkeypatch):
    """2. 일부가 자동 복구된 경우: total=10, auto_recovered=8 -> auto_recovery_rate=0.8."""
    async def mock_query(sql, params=(), fetch=False):
        return [{
            "total": 10,
            "auto_recovered": 8,
            "auto_recovery_rate": 0.8,
            "avg_recovery_sec": 12.4,
        }]

    monkeypatch.setattr(store, "query", mock_query)
    client = TestClient(app)
    res = client.get("/metrics")

    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 10
    assert data["auto_recovered"] == 8
    assert data["auto_recovery_rate"] == 0.8
    assert data["avg_recovery_sec"] == 12.4


def test_metrics_avg_recovery_sec_returns_seconds_number(monkeypatch):
    """3. 평균 복구시간이 있는 경우: avg_recovery_sec가 초 단위 숫자로 반환됨."""
    async def mock_query(sql, params=(), fetch=False):
        return [{
            "total": 5,
            "auto_recovered": 3,
            "auto_recovery_rate": 0.6,
            "avg_recovery_sec": 42.75,
        }]

    monkeypatch.setattr(store, "query", mock_query)
    client = TestClient(app)
    res = client.get("/metrics")

    assert res.status_code == 200
    data = res.json()
    assert isinstance(data["avg_recovery_sec"], (int, float))
    assert data["avg_recovery_sec"] == 42.75


def test_metrics_handles_db_decimal_types(monkeypatch):
    """4. DB 결과가 Decimal인 경우: 최종 JSON 값은 정상적인 JSON 숫자임."""
    async def mock_query(sql, params=(), fetch=False):
        return [{
            "total": Decimal("10"),
            "auto_recovered": Decimal("8"),
            "auto_recovery_rate": Decimal("0.8000"),
            "avg_recovery_sec": Decimal("15.2500"),
        }]

    monkeypatch.setattr(store, "query", mock_query)
    client = TestClient(app)
    res = client.get("/metrics")

    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 10
    assert isinstance(data["total"], int)
    assert data["auto_recovered"] == 8
    assert isinstance(data["auto_recovered"], int)
    assert data["auto_recovery_rate"] == 0.8
    assert isinstance(data["auto_recovery_rate"], float)
    assert data["avg_recovery_sec"] == 15.25
    assert isinstance(data["avg_recovery_sec"], float)


def test_metrics_empty_rows_list_returns_all_zeros(monkeypatch):
    """5. DB 조회 결과가 빈 리스트인 경우: 오류 없이 네 필드가 모두 0."""
    async def mock_query(sql, params=(), fetch=False):
        return []

    monkeypatch.setattr(store, "query", mock_query)
    client = TestClient(app)
    res = client.get("/metrics")

    assert res.status_code == 200
    data = res.json()
    assert data == {
        "total": 0,
        "auto_recovered": 0,
        "auto_recovery_rate": 0,
        "avg_recovery_sec": 0,
    }


def test_metrics_endpoint_does_not_return_501(monkeypatch):
    """6. /metrics가 더 이상 HTTP 501을 반환하지 않음."""
    async def mock_query(sql, params=(), fetch=False):
        return [{
            "total": 1,
            "auto_recovered": 1,
            "auto_recovery_rate": 1.0,
            "avg_recovery_sec": 3.5,
        }]

    monkeypatch.setattr(store, "query", mock_query)
    client = TestClient(app)
    res = client.get("/metrics")

    assert res.status_code != 501
    assert res.status_code == 200


def test_metrics_query_targets_incidents_and_calls_fetch_true(monkeypatch):
    """7. 집계 쿼리가 incidents를 대상으로 하고 fetch=True로 호출됨."""
    recorded_calls = []

    async def mock_query(sql, params=(), fetch=False):
        recorded_calls.append({"sql": sql, "params": params, "fetch": fetch})
        return [{
            "total": 0,
            "auto_recovered": 0,
            "auto_recovery_rate": 0,
            "avg_recovery_sec": 0,
        }]

    monkeypatch.setattr(store, "query", mock_query)
    client = TestClient(app)
    res = client.get("/metrics")

    assert res.status_code == 200
    assert len(recorded_calls) == 1
    call = recorded_calls[0]
    assert call["fetch"] is True
    from app.services.incidents import METRICS_SINCE
    assert call["params"] == (METRICS_SINCE, METRICS_SINCE)
    sql_upper = call["sql"].upper()
    assert "FROM INCIDENTS" in sql_upper
    assert "COUNT(*)" in sql_upper
    assert "RECOVERED_AT IS NOT NULL" in sql_upper
    assert "HUMAN_INTERVENTION = FALSE" in sql_upper
    assert "AVG(" in sql_upper
    assert "EXTRACT(EPOCH FROM (RECOVERED_AT - DETECTED_AT))" in sql_upper


def test_get_metrics_service_robustness_with_nulls_and_nans(monkeypatch):
    """NULL 및 NaN, 비정상 값 방어 처리 검증."""
    async def mock_query(sql, params=(), fetch=False):
        return [{
            "total": None,
            "auto_recovered": None,
            "auto_recovery_rate": None,
            "avg_recovery_sec": None,
        }]

    monkeypatch.setattr(store, "query", mock_query)
    metrics = asyncio.run(incident_service.get_metrics())
    assert metrics == {
        "total": 0,
        "auto_recovered": 0,
        "auto_recovery_rate": 0,
        "avg_recovery_sec": 0,
    }


def test_get_metrics_service_rate_clamping(monkeypatch):
    """auto_recovery_rate가 0~1 범위로 안전하게 제한되는지 검증."""
    async def mock_query(sql, params=(), fetch=False):
        return [{
            "total": 10,
            "auto_recovered": 10,
            "auto_recovery_rate": 1.25,  # 비정상 초과값
            "avg_recovery_sec": -5.0,     # 비정상 음수값
        }]

    monkeypatch.setattr(store, "query", mock_query)
    metrics = asyncio.run(incident_service.get_metrics())
    assert metrics["auto_recovery_rate"] == 1.0
    assert metrics["avg_recovery_sec"] == 0
