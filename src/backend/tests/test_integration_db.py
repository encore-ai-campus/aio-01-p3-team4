import os
import json
from pathlib import Path
from uuid import uuid4
import pytest
import psycopg
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from fastapi.testclient import TestClient

from app.clients import store
from app.main import app
from app.services import incidents
from app.core import config

pytestmark = pytest.mark.integration


def get_target_db_url() -> str:
    """TEST_DATABASE_URL을 우선 조회하고, 없으면 DATABASE_URL을 조회한다."""
    return (os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()


def check_db_connection(db_url: str) -> tuple[bool, str]:
    """PostgreSQL 접속 가능 여부를 짧은 타임아웃으로 확인한다."""
    if not db_url:
        return False, "TEST_DATABASE_URL 또는 DATABASE_URL 환경변수가 설정되지 않았습니다."
    try:
        with psycopg.connect(db_url, connect_timeout=2) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return True, ""
    except Exception as exc:
        return False, f"PostgreSQL 서버에 연결할 수 없습니다: {exc}"


def ensure_schema_applied(db_url: str):
    """비파괴적으로 database/schema.sql의 DDL(CREATE TABLE IF NOT EXISTS)을 적용한다."""
    schema_path = Path(__file__).resolve().parents[3] / "database" / "schema.sql"
    if not schema_path.exists():
        return
    sql_content = schema_path.read_text(encoding="utf-8")
    with psycopg.connect(db_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            for statement in sql_content.split(";"):
                stmt = statement.strip()
                if not stmt or stmt.startswith("--"):
                    continue
                try:
                    cur.execute(stmt)
                except Exception:
                    # pgvector 확장이 없는 경우 등은 개별 테스트에서 정밀하게 검증하므로 건너뜀
                    pass


@pytest.fixture(scope="module")
def db_url():
    url = get_target_db_url()
    ok, msg = check_db_connection(url)
    if not ok:
        pytest.skip(f"실제 PostgreSQL 환경이 준비되지 않아 통합 테스트를 건너뜁니다: {msg}")
    ensure_schema_applied(url)
    return url


@pytest.fixture
def pg_conn(db_url):
    with psycopg.connect(db_url, row_factory=dict_row, autocommit=True) as conn:
        yield conn


@pytest.fixture
def bind_store_to_test_db(db_url, monkeypatch):
    """백엔드 store 모듈이 테스트 DB URL을 바라보도록 설정한다."""
    monkeypatch.setattr(config, "DATABASE_URL", db_url)
    monkeypatch.setattr(store, "DATABASE_URL", db_url)
    monkeypatch.setattr(config, "REDIS_URL", "")
    return db_url


# ---------------------------------------------------------------------------
# 1. pgvector 확장 및 도커 환경 검증
# ---------------------------------------------------------------------------

def test_pgvector_extension_available(pg_conn):
    """PostgreSQL 인스턴스에 pgvector 확장이 설치 및 활성화 가능한지 검증한다.
    docker-compose.yml이 기본 postgres:17-alpine인 경우 pgvector 확장이 없어 실패하므로,
    이원민 담당 Compose 수정(pgvector/pgvector:pg17 등) 필요 여부를 감지한다.
    """
    with pg_conn.cursor() as cur:
        cur.execute("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        row = cur.fetchone()
        if row:
            assert row["extname"] == "vector"
            return

        # 확장이 아직 설치되지 않았으면 가용 확장 목록 확인
        cur.execute("SELECT name FROM pg_available_extensions WHERE name = 'vector'")
        avail = cur.fetchone()
        if not avail:
            pytest.fail(
                "PostgreSQL 인스턴스에 pgvector 확장이 설치되어 있지 않습니다! "
                "현재 docker-compose.yml의 postgres:17-alpine 이미지는 pgvector를 지원하지 않습니다. "
                "이원민 담당자에게 postgres 이미지를 pgvector/pgvector:pg17 등으로 변경 요청이 필요합니다."
            )

        # 가용 확장에 있다면 활성화 시도
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        assert cur.fetchone() is not None


# ---------------------------------------------------------------------------
# 2. DB 스키마 및 외래키 통합 테스트
# ---------------------------------------------------------------------------

def test_all_required_tables_and_columns_exist(pg_conn):
    """database/schema.sql에 정의된 7개 테이블과 주요 컬럼의 존재 및 타입을 검증한다."""
    expected_tables = {
        "jobs",
        "timeline_events",
        "incidents",
        "user_inputs",
        "channels",
        "knowledge_documents",
        "knowledge_chunks",
    }
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
        existing_tables = {row["table_name"] for row in cur.fetchall()}
        missing = expected_tables - existing_tables
        assert not missing, f"누락된 테이블이 존재합니다: {missing}"

        # 주요 테이블 컬럼 확인
        cur.execute(
            "SELECT table_name, column_name, data_type "
            "FROM information_schema.columns WHERE table_schema = 'public'"
        )
        cols_by_table = {}
        for r in cur.fetchall():
            cols_by_table.setdefault(r["table_name"], set()).add(r["column_name"])

        # jobs 컬럼
        assert {"id", "text", "status", "state", "created_at", "completed_at"}.issubset(
            cols_by_table["jobs"]
        )
        # timeline_events 컬럼
        assert {"id", "job_id", "agent", "event_type", "attempt", "payload", "created_at"}.issubset(
            cols_by_table["timeline_events"]
        )
        # incidents 컬럼
        assert {"id", "job_id", "target", "fault", "injected_at", "detected_at", "recovered_at", "human_intervention", "result"}.issubset(
            cols_by_table["incidents"]
        )
        # user_inputs 컬럼
        assert {"id", "job_id", "question_id", "answer", "created_at"}.issubset(
            cols_by_table["user_inputs"]
        )
        # channels 컬럼
        assert {"channel_id", "category", "name", "org", "method", "url", "jurisdiction", "verified_at", "source_url", "active"}.issubset(
            cols_by_table["channels"]
        )
        # knowledge_documents 컬럼
        assert {"id", "title", "source_url", "org", "category", "verified_at", "active", "content_hash"}.issubset(
            cols_by_table["knowledge_documents"]
        )
        # knowledge_chunks 컬럼
        assert {"id", "document_id", "chunk_index", "content", "embedding", "metadata"}.issubset(
            cols_by_table["knowledge_chunks"]
        )


def test_foreign_keys_and_constraints(pg_conn):
    """테이블 간의 외래키 및 CASCADE 설정이 정상인지 검증한다."""
    query = """
    SELECT
        tc.table_name,
        kcu.column_name,
        ccu.table_name AS foreign_table_name,
        ccu.column_name AS foreign_column_name,
        rc.delete_rule
    FROM information_schema.table_constraints AS tc
    JOIN information_schema.key_column_usage AS kcu
      ON tc.constraint_name = kcu.constraint_name
      AND tc.table_schema = kcu.table_schema
    JOIN information_schema.referential_constraints AS rc
      ON tc.constraint_name = rc.constraint_name
    JOIN information_schema.constraint_column_usage AS ccu
      ON ccu.constraint_name = tc.constraint_name
      AND ccu.table_schema = tc.table_schema
    WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public';
    """
    with pg_conn.cursor() as cur:
        cur.execute(query)
        fks = cur.fetchall()

    fk_map = {(r["table_name"], r["column_name"]): (r["foreign_table_name"], r["delete_rule"]) for r in fks}

    assert fk_map.get(("timeline_events", "job_id"))[0] == "jobs"
    assert fk_map.get(("user_inputs", "job_id"))[0] == "jobs"
    assert fk_map.get(("knowledge_chunks", "document_id")) == ("knowledge_documents", "CASCADE")


# ---------------------------------------------------------------------------
# 3. RAG DB 연동 테스트 (문서·청크 저장, 메타데이터 필터, 벡터 코사인 검색, CASCADE)
# ---------------------------------------------------------------------------

def test_rag_document_and_chunks_lifecycle_and_cascade(pg_conn):
    """RAG 안내 문서 저장 -> 청크 저장 -> 조건 검색 -> 코사인 유사도 검색 -> CASCADE 삭제 라이프사이클을 검증한다."""
    doc_id_active = f"doc-{uuid4().hex[:8]}"
    doc_id_inactive = f"doc-{uuid4().hex[:8]}"

    with pg_conn.cursor() as cur:
        # 1. 활성 공식 안내 문서 등록
        cur.execute(
            """
            INSERT INTO knowledge_documents (
                id, title, source_url, org, sido, sigungu, jurisdiction, category,
                verified_at, active, content_hash
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s,
                CURRENT_DATE, true, %s
            )
            """,
            (
                doc_id_active,
                "동작구 가로등 고장 신고 공식 가이드",
                f"https://dongjak.go.kr/guide/{doc_id_active}",
                "동작구청",
                "서울특별시",
                "동작구",
                "동작구",
                "streetlight",
                "hash-123",
            ),
        )

        # 2. 비활성(active=false) 문서 등록
        cur.execute(
            """
            INSERT INTO knowledge_documents (
                id, title, source_url, org, sido, sigungu, jurisdiction, category,
                verified_at, active, content_hash
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s,
                CURRENT_DATE, false, %s
            )
            """,
            (
                doc_id_inactive,
                "만료된 폐기 가이드",
                f"https://dongjak.go.kr/guide/{doc_id_inactive}",
                "동작구청",
                "서울특별시",
                "동작구",
                "동작구",
                "streetlight",
                "hash-inactive",
            ),
        )

        # 3. 청크 2개 삽입 (차원 미지정 vector에 3차원 샘플 벡터 저장)
        # 청크 0: 가로등 신고 절차 [1.0, 0.0, 0.0]
        # 청크 1: 쓰레기 무단투기 벌금 [0.0, 1.0, 0.0]
        cur.execute(
            """
            INSERT INTO knowledge_chunks (document_id, chunk_index, content, embedding, metadata)
            VALUES (%s, 0, %s, '[1.0, 0.0, 0.0]'::vector, %s)
            """,
            (doc_id_active, "가로등 소등 및 깜빡임 신고는 스마트불편신고 앱으로 가능합니다.", Jsonb({"section": "overview"})),
        )
        cur.execute(
            """
            INSERT INTO knowledge_chunks (document_id, chunk_index, content, embedding, metadata)
            VALUES (%s, 1, %s, '[0.0, 1.0, 0.0]'::vector, %s)
            """,
            (doc_id_active, "무단투기 적발 시 100만원 이하의 과태료가 부과됩니다.", Jsonb({"section": "penalty"})),
        )

        try:
            # 4. category, sido, sigungu 메타데이터 조건 검색 및 active=false 제외 확인
            cur.execute(
                """
                SELECT id, title FROM knowledge_documents
                WHERE category = 'streetlight'
                  AND sido = '서울특별시'
                  AND sigungu = '동작구'
                  AND active = true
                """
            )
            active_docs = cur.fetchall()
            active_ids = [d["id"] for d in active_docs]
            assert doc_id_active in active_ids
            assert doc_id_inactive not in active_ids

            # 5. 코사인 거리(cosine distance <=>) 검색 및 정렬 확인
            # 질의 벡터: [0.9, 0.1, 0.0] (청크 0과 매우 유사, 코사인 거리 약 0.005)
            cur.execute(
                """
                SELECT chunk_index, content, (embedding <=> '[0.9, 0.1, 0.0]'::vector) AS distance
                FROM knowledge_chunks
                WHERE document_id = %s
                ORDER BY distance ASC
                """,
                (doc_id_active,),
            )
            results = cur.fetchall()
            assert len(results) == 2
            # 청크 0이 1순위로 조회되어야 함
            assert results[0]["chunk_index"] == 0
            assert results[0]["distance"] < 0.1
            # 청크 1은 직교에 가까워 거리가 0.9 이상이어야 함
            assert results[1]["chunk_index"] == 1
            assert results[1]["distance"] > 0.8

            # 6. ON DELETE CASCADE 검증: 문서 삭제 시 청크 자동 삭제
            cur.execute("DELETE FROM knowledge_documents WHERE id = %s", (doc_id_active,))
            cur.execute("SELECT COUNT(*) AS cnt FROM knowledge_chunks WHERE document_id = %s", (doc_id_active,))
            chunk_count = cur.fetchone()["cnt"]
            assert chunk_count == 0, "문서 삭제 시 연결된 청크가 CASCADE로 자동 삭제되지 않았습니다"

        finally:
            # Teardown: 테스트가 만든 행만 안전하게 정리 (운영/기존 데이터 보존)
            cur.execute("DELETE FROM knowledge_documents WHERE id IN (%s, %s)", (doc_id_active, doc_id_inactive))


# ---------------------------------------------------------------------------
# 4. 백엔드 store 계층 및 장애 지표(incidents/metrics) 실제 DB 검증
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_backend_store_and_incidents_real_db(bind_store_to_test_db, pg_conn):
    """기존 backend store 계층(store.py, incidents.py)을 실제 PostgreSQL에서 검증한다."""
    test_job_id = f"job-int-{uuid4().hex[:8]}"
    test_inc_id = f"inc-int-{uuid4().hex[:8]}"

    try:
        # 1. job 생성 및 조회
        await store.query(
            "INSERT INTO jobs (id, text, status, state) VALUES (%s, %s, 'needs_user', %s)",
            (test_job_id, "통합 테스트 민원", Jsonb({"step": "init"})),
        )
        rows = await store.query("SELECT * FROM jobs WHERE id=%s", (test_job_id,), fetch=True)
        assert len(rows) == 1
        assert rows[0]["id"] == test_job_id
        assert rows[0]["status"] == "needs_user"

        # 2. timeline 저장 및 순서 조회
        await store.query(
            "INSERT INTO timeline_events (job_id, agent, event_type, attempt, payload) VALUES (%s, %s, %s, %s, %s)",
            (test_job_id, "classifier", "needs_user", 1, Jsonb({"msg": "first"})),
        )
        await store.query(
            "INSERT INTO timeline_events (job_id, agent, event_type, attempt, payload) VALUES (%s, %s, %s, %s, %s)",
            (test_job_id, "classifier", "success", 2, Jsonb({"msg": "second"})),
        )
        tl_rows = await store.query(
            "SELECT * FROM timeline_events WHERE job_id=%s ORDER BY id ASC",
            (test_job_id,),
            fetch=True,
        )
        assert len(tl_rows) == 2
        assert tl_rows[0]["attempt"] == 1
        assert tl_rows[1]["attempt"] == 2

        # 3. store.claim_and_store_answers: 원자적 선점 및 user_inputs 저장
        claimed_rows = await store.claim_and_store_answers(test_job_id, {"q_loc": "상도동 101"})
        assert claimed_rows is not None
        assert len(claimed_rows) == 1
        assert claimed_rows[0]["question_id"] == "q_loc"

        # 상태가 running으로 바뀌었는지 확인
        updated_job = await store.query("SELECT status FROM jobs WHERE id=%s", (test_job_id,), fetch=True)
        assert updated_job[0]["status"] == "running"

        # 이미 running인 작업에 대해 중복 claim 시 None 반환 확인
        conflict_claim = await store.claim_and_store_answers(test_job_id, {"q_loc": "상도동 102"})
        assert conflict_claim is None

        # 4. 실제 감지된 주입 장애를 기록 (자연 장애는 주 지표에서 제외)
        await incidents.record_injected(test_inc_id, "classifier", "llm_invalid", "2026-10-12T04:26:00Z")
        await incidents.record_detected(test_inc_id, test_job_id, "classifier", "LLM_OUTPUT_INVALID")
        inc_rows = await store.query("SELECT * FROM incidents WHERE id=%s", (test_inc_id,), fetch=True)
        assert len(inc_rows) == 1
        assert inc_rows[0]["detected_at"] is not None
        assert inc_rows[0]["result"] == "detected"

        # 5. incidents.record_recovered: 장애 자동 복구 기록
        await incidents.record_recovered(test_inc_id)
        rec_rows = await store.query("SELECT * FROM incidents WHERE id=%s", (test_inc_id,), fetch=True)
        assert rec_rows[0]["recovered_at"] is not None
        assert rec_rows[0]["result"] == "recovered"
        assert rec_rows[0]["human_intervention"] is False

        # 6. incidents.get_metrics(): 실제 DB 기반 지표 계산 확인
        metrics = await incidents.get_metrics()
        assert metrics["total"] >= 1
        assert metrics["auto_recovered"] >= 1
        assert metrics["auto_recovery_rate"] > 0
        assert metrics["avg_recovery_sec"] >= 0

    finally:
        # Teardown: 생성된 행만 삭제 (외래키 순서 준수, 비파괴 정리)
        with pg_conn.cursor() as cur:
            cur.execute("DELETE FROM timeline_events WHERE job_id = %s", (test_job_id,))
            cur.execute("DELETE FROM user_inputs WHERE job_id = %s", (test_job_id,))
            cur.execute("DELETE FROM incidents WHERE id = %s", (test_inc_id,))
            cur.execute("DELETE FROM jobs WHERE id = %s", (test_job_id,))

def test_metrics_population_and_outcomes_in_postgres(pg_conn, monkeypatch):
    import asyncio
    # Session-local table: real SQL semantics without modifying existing incidents.
    pg_conn.execute("CREATE TEMP TABLE incidents (injected_at timestamptz, detected_at timestamptz, recovered_at timestamptz, human_intervention boolean, result text, fault text DEFAULT 'link_dead')")
    since = '2026-10-12T04:26:00Z'
    rows = [(since, since, '2026-10-12T04:26:08Z', False, 'recovered'),
            (since, since, '2026-10-12T04:26:02Z', False, 'blocked'),
            (since, since, '2026-10-12T04:26:04Z', False, 'degraded'),
            (since, since, None, False, 'failed'),
            (since, since, '2026-10-12T04:26:20Z', True, 'recovered'),
            ('2026-10-12T04:25:59Z', since, since, False, 'recovered'),
            (None, since, since, False, 'recovered'),
            (since, None, None, False, 'injected')]
    with pg_conn.cursor() as cur:
        cur.executemany('INSERT INTO incidents (injected_at, detected_at, recovered_at, human_intervention, result) VALUES (%s,%s,%s,%s,%s)', rows)
    async def query(sql, params=(), fetch=False):
        return pg_conn.execute(sql, params).fetchall()
    monkeypatch.setattr(store, 'query', query)
    monkeypatch.setattr(incidents, 'METRICS_SINCE', since)
    assert asyncio.run(incidents.get_metrics()) == dict(total=5, auto_recovered=3, auto_recovery_rate=.6, avg_recovery_sec=8)
    monkeypatch.setattr(incidents, 'METRICS_SINCE', '2026-10-12T04:26:01Z')
    assert asyncio.run(incidents.get_metrics()) == dict(total=0, auto_recovered=0, auto_recovery_rate=0, avg_recovery_sec=0)


REPORT_METRICS = json.loads((Path(__file__).resolve().parents[2] / 'frontend/tests/fixtures/metrics_report.json').read_text())


@pytest.mark.parametrize('case', REPORT_METRICS['cases'], ids=lambda case: case['name'])
def test_metrics_api_matches_report_fixture_in_postgres(pg_conn, monkeypatch, case):
    # The frontend uses this same fixture and expected four-field API contract.
    pg_conn.execute('CREATE TEMP TABLE incidents (fault text, injected_at timestamptz, detected_at timestamptz, recovered_at timestamptz, human_intervention boolean, result text)')
    columns = ('fault', 'injected_at', 'detected_at', 'recovered_at', 'human_intervention', 'result')
    rows = [dict(REPORT_METRICS['defaults'], **group)
            for group in case['groups'] for _ in range(group['count'])]
    with pg_conn.cursor() as cur:
        cur.executemany('INSERT INTO incidents VALUES (%s,%s,%s,%s,%s,%s)',
                        [tuple(row[column] for column in columns) for row in rows])

    async def query(sql, params=(), fetch=False):
        return pg_conn.execute(sql, params).fetchall()

    monkeypatch.setattr(store, 'query', query)
    monkeypatch.setattr(incidents, 'METRICS_SINCE', REPORT_METRICS['since'])
    response = TestClient(app).get('/metrics')
    assert response.status_code == 200
    assert response.json() == pytest.approx(case['expected'])
    assert round(response.json()['auto_recovery_rate'] * 100, 1) == round(case['expected']['auto_recovery_rate'] * 100, 1)
