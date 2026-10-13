from app.clients import store
from app.core.config import METRICS_SINCE
import math
from typing import Any

SQL_INJECTED = (
    "INSERT INTO incidents (id, job_id, target, fault, injected_at, detected_at, recovered_at, human_intervention, result) "
    "VALUES (%s, NULL, %s, %s, %s, NULL, NULL, false, 'injected') "
    "ON CONFLICT (id) DO UPDATE SET "
    "injected_at = COALESCE(incidents.injected_at, EXCLUDED.injected_at)"
)

SQL_DETECTED = (
    "INSERT INTO incidents (id, job_id, target, fault, injected_at, detected_at, recovered_at, human_intervention, result) "
    "VALUES (%s, %s, %s, %s, NULL, COALESCE(%s::timestamptz, now()), NULL, false, %s) "
    "ON CONFLICT (id) DO UPDATE SET "
    "job_id = COALESCE(incidents.job_id, EXCLUDED.job_id), "
    "target = COALESCE(incidents.target, EXCLUDED.target), "
    "fault = COALESCE(incidents.fault, EXCLUDED.fault), "
    "detected_at = COALESCE(incidents.detected_at, EXCLUDED.detected_at), "
    "result = CASE WHEN incidents.result IN ('recovered', 'failed') THEN incidents.result ELSE EXCLUDED.result END"
)

SQL_RECOVERED = (
    "UPDATE incidents SET recovered_at = COALESCE(recovered_at, now()), result = 'recovered', human_intervention = false "
    "WHERE id = %s"
)

SQL_FAILED = (
    "UPDATE incidents SET result = 'failed' "
    "WHERE id = %s AND (result IS NULL OR result != 'recovered')"
)

def normalize_incident_for_api(row: dict) -> dict:
    """DB 원본 장애 코드를 유지하며 JSON 응답에 필요한 값만 정규화한다."""
    item = dict(row)
    item["human_intervention"] = bool(item.get("human_intervention", False))
    item["metrics_since"] = METRICS_SINCE  # 화면도 서버와 같은 집계 시작값을 사용한다.
    return item


SQL_METRICS = (
    "SELECT "
    "COUNT(*) AS total, "
    "COUNT(*) FILTER ("
    "    WHERE result IN ('recovered', 'blocked', 'degraded') "
    "      AND human_intervention = false"
    ") AS auto_recovered, "
    "COALESCE("
    "    COUNT(*) FILTER ("
    "        WHERE result IN ('recovered', 'blocked', 'degraded') "
    "          AND human_intervention = false"
    "    )::double precision / NULLIF(COUNT(*), 0), "
    "    0"
    ") AS auto_recovery_rate, "
    "COALESCE("
    "    AVG("
    "        EXTRACT(EPOCH FROM (recovered_at - detected_at))"
    "    ) FILTER ("
    "        WHERE recovered_at IS NOT NULL "
    "          AND result = 'recovered' "
    "          AND detected_at IS NOT NULL "
    "          AND human_intervention = false"
    "    ), "
    "    0"
    ") AS avg_recovery_sec "
    "FROM incidents WHERE (injected_at >= %s::timestamptz AND detected_at IS NOT NULL) "
    "OR (result = 'blocked' AND fault = 'INJECTION_BLOCKED' AND detected_at >= %s::timestamptz)"
)


def _safe_int(val: Any) -> int:
    if val is None:
        return 0
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


def _safe_float(val: Any) -> float | int:
    if val is None:
        return 0
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return 0
        if f == 0.0:
            return 0
        return f
    except (TypeError, ValueError):
        return 0


async def record_injected(incident_id: str, target: str, fault: str, injected_at: str) -> None:
    """프론트엔드/chaos 요청으로 주입된 장애 정보를 기록한다."""
    await store.query(SQL_INJECTED, (incident_id, target, fault, injected_at))


async def claim_pending(job_id: str, target: str) -> str | None:
    """응답이 없는 장애도 같은 대상의 주입 기록에 연결한다."""
    rows = await store.query(
        "UPDATE incidents SET job_id=%s, detected_at=now(), result='detected' WHERE id=("
        "SELECT id FROM incidents WHERE target=%s AND injected_at IS NOT NULL "
        "AND detected_at IS NULL AND job_id IS NULL AND result='injected' "
        "ORDER BY injected_at DESC, id DESC LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING id",
        (job_id, target), fetch=True,
    )
    return rows[0]["id"] if rows else None


async def record_detected(incident_id: str, job_id: str, target: str, fault: str, result: str = "detected", detected_at: str | None = None) -> None:
    """에이전트 응답 또는 Supervisor에서 장애가 감지되었을 때 기록한다 (최초 detected_at 보존)."""
    await store.query(SQL_DETECTED, (incident_id, job_id, target, fault, detected_at, result))


async def record_recovered(incident_id: str) -> None:
    """동일 단계의 후속 시도가 성공했을 때 recovered_at과 result='recovered'를 기록한다 (기존 recovered_at 보존)."""
    await store.query(SQL_RECOVERED, (incident_id,))


async def record_failed(incident_id: str) -> None:
    """재시도 한도 초과 또는 실패로 단계가 종료되었을 때 result='failed'로 기록한다 (recovered 상태는 보존)."""
    await store.query(SQL_FAILED, (incident_id,))


async def record_handled(incident_id: str, result: str) -> None:
    """자동 통과·차단 완료 시각을 남기되 처리 의미를 복구로 덮어쓰지 않는다."""
    await store.query(
        "UPDATE incidents SET result=%s, recovered_at=COALESCE(recovered_at, now()), "
        "human_intervention=false WHERE id=%s", (result, incident_id),
    )


async def record_degraded(incident_id: str, job_id: str, target: str, fault: str = "STALE_INFO") -> None:
    """STALE_INFO 등 완전 복구는 아니지만 처리는 가능한 상태를 degraded로 기록한다."""
    await record_detected(incident_id, job_id, target, fault, result="degraded")
    await record_handled(incident_id, "degraded")


async def get_metrics() -> dict[str, int | float]:
    """incidents 테이블을 집계하여 장애 복구 지표를 반환한다."""
    rows = await store.query(SQL_METRICS, params=(METRICS_SINCE, METRICS_SINCE), fetch=True)
    if not rows or not isinstance(rows[0], dict):
        return {
            "total": 0,
            "auto_recovered": 0,
            "auto_recovery_rate": 0,
            "avg_recovery_sec": 0,
        }

    row = rows[0]
    total = _safe_int(row.get("total"))
    auto_recovered = _safe_int(row.get("auto_recovered"))

    if total == 0:
        auto_recovery_rate = 0
    else:
        raw_rate = _safe_float(row.get("auto_recovery_rate"))
        auto_recovery_rate = max(0.0, min(1.0, float(raw_rate)))
        if auto_recovery_rate == 0.0:
            auto_recovery_rate = 0

    raw_avg = _safe_float(row.get("avg_recovery_sec"))
    avg_recovery_sec = 0 if raw_avg == 0.0 else max(0.0, float(raw_avg))

    return {
        "total": total,
        "auto_recovered": auto_recovered,
        "auto_recovery_rate": auto_recovery_rate,
        "avg_recovery_sec": avg_recovery_sec,
    }
