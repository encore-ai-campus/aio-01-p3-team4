"""Opt-in PostgreSQL checks, isolated in a fresh schema (no production tables)."""
import asyncio
import os
import shutil
from datetime import date
from uuid import uuid4

import psycopg
import httpx
from psycopg import sql
import pytest
from fastapi.testclient import TestClient

from app.clients.channels import ChannelRepository
from app.core.config import PROJECT_ROOT, SEEDS_DIR
from app.main import app
from app.services.catalog import load_catalog, seed_paths, select_channels
from app.schemas.contract import StageResult
from test_run import request
from test_check_channels import checker

pytestmark = pytest.mark.skipif(not os.getenv("LOCATOR_TEST_DATABASE_URL"), reason="set LOCATOR_TEST_DATABASE_URL for isolated PostgreSQL integration checks")


@pytest.fixture
def database(monkeypatch):
    dsn = os.environ["LOCATOR_TEST_DATABASE_URL"]
    schema = "locator_test_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    repo = ChannelRepository(dsn, SEEDS_DIR)
    original = repo._connect

    def connect():
        conn = original()
        conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        return conn

    monkeypatch.setattr(repo, "_connect", connect)
    try:
        with connect() as conn:
            conn.execute((PROJECT_ROOT / "database" / "schema.sql").read_text(encoding="utf-8"))
            # Resolve vector from public, but never run test writes against its channels.
            assert conn.execute("SELECT to_regclass(%s) = 'channels'::regclass AS isolated", (f"{schema}.channels",)).fetchone()["isolated"]
        yield repo, connect
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_seed_restart_preserves_operator_changes(database):
    repo, connect = database
    expected = len(load_catalog(seed_paths(SEEDS_DIR)))
    assert asyncio.run(repo.seed()) == expected
    with connect() as conn:
        conn.execute("UPDATE channels SET active=false WHERE channel_id='dj-all-01'")
    assert asyncio.run(repo.seed()) == 0
    with connect() as conn:
        assert conn.execute("SELECT active FROM channels WHERE channel_id='dj-all-01'").fetchone()["active"] is False
    assert asyncio.run(repo.seed(upsert=True)) == expected
    with connect() as conn:
        assert conn.execute("SELECT active FROM channels WHERE channel_id='dj-all-01'").fetchone()["active"] is True


def test_concurrent_startup_inserts_once(database):
    repo, connect = database
    async def both():
        return await asyncio.gather(repo.seed(), repo.seed())
    expected = len(load_catalog(seed_paths(SEEDS_DIR)))
    assert sorted(asyncio.run(both())) == [0, expected]
    with connect() as conn:
        assert conn.execute("SELECT count(*) AS count FROM channels").fetchone()["count"] == expected


def test_populated_startup_does_not_wait_for_checker_write_transaction(database):
    repo, connect = database
    asyncio.run(repo.seed())
    with connect() as checker_transaction:
        checker_transaction.execute("UPDATE channels SET active=false WHERE channel_id='dj-all-01'")
        # No exclusive startup lock should block behind this in-flight maintenance write.
        assert asyncio.run(repo.seed()) == 0


def test_invalid_second_file_has_no_partial_insert(database, tmp_path):
    repo, connect = database
    for path in seed_paths(SEEDS_DIR):
        shutil.copyfile(path, tmp_path / path.name)
    with (tmp_path / "integrated_channels.csv").open("a", encoding="utf-8") as stream:
        stream.write("bad,row\n")
    repo.seeds_dir = tmp_path
    with pytest.raises(ValueError, match="column count"):
        asyncio.run(repo.seed())
    with connect() as conn:
        assert conn.execute("SELECT count(*) AS count FROM channels").fetchone()["count"] == 0


def test_database_api_and_fallback(database, monkeypatch):
    repo, connect = database
    monkeypatch.setattr(app.state, "repository", repo)
    with TestClient(app) as client:
        assert client.get("/health").json()["mode"] == "database"
        for category in ("streetlight", "road_damage", "illegal_dumping"):
            response = client.post("/run", json=request(category))
            assert response.status_code == 200
            assert StageResult.model_validate(response.json()).data["fallback_level"] == "district"
        with connect() as conn:
            conn.execute("UPDATE channels SET active=false WHERE sigungu IS NOT NULL")
        assert client.post("/run", json=request()).json()["data"]["fallback_level"] == "city"
        with connect() as conn:
            conn.execute("UPDATE channels SET active=false WHERE sido IS NOT NULL")
        assert client.post("/run", json=request()).json()["data"]["fallback_level"] == "national"
        body = request()
        body["inputs"]["exclude_channel_ids"] = [row["channel_id"] for row in load_catalog(seed_paths(SEEDS_DIR))]
        assert client.post("/run", json=body).json()["failure"] == "NO_CHANNEL"


def test_query_parameters_treat_sql_as_data(database):
    repo, connect = database
    asyncio.run(repo.seed())
    body = request()["inputs"]
    body["region"]["sigungu"] = "동작구'; DROP TABLE channels; --"
    body["exclude_channel_ids"] = ["national-all-01' OR true --"]
    candidates = asyncio.run(repo.candidates(body))
    assert select_channels(candidates, body)["fallback_level"] == "national"
    with connect() as conn:
        assert conn.execute("SELECT count(*) AS count FROM channels").fetchone()["count"] >= 14


def test_link_check_updates_database_and_moves_api_up_the_ladder(database, monkeypatch, tmp_path):
    """Mock only remote HTTP; use the real checker, DB, API and CSV exporter."""
    repo, connect = database
    monkeypatch.setattr(app.state, "repository", repo)
    # This checker test exercises the web-only ladder; telephone rows remain active.
    monkeypatch.setattr("app.routers.api.INCLUDE_PHONE", False)
    # DNS/network security has separate unit coverage; never contact an agency here.
    monkeypatch.setattr(checker, "_resolve_official_url", lambda value: (httpx.URL(value), httpx.URL(value)))
    dead_urls = set()

    def respond(http_request):
        return httpx.Response(404 if str(http_request.url) in dead_urls else 200)

    seeds = load_catalog(seed_paths(SEEDS_DIR))
    checked_on = date(2026, 10, 7)
    old_date = date(2026, 9, 1)
    with TestClient(app) as api, httpx.Client(transport=httpx.MockTransport(respond)) as remote:
        with connect() as conn:
            conn.execute("UPDATE channels SET verified_at=%s", (old_date,))
            reports = checker.check_channels(conn, client=remote, today=checked_on)
        assert len(reports) == len(seeds)
        with connect() as conn:
            values = conn.execute("SELECT method, verified_at FROM channels").fetchall()
        assert all(row["verified_at"] == (old_date if row["method"] == "phone" else checked_on) for row in values)
        for dead_level, next_level in (("district", "city"), ("city", "national"), ("national", None)):
            for row in seeds:
                level = "district" if row["sigungu"] else "city" if row["sido"] else "national"
                if level == dead_level and row["method"] != "phone":
                    dead_urls.add(row["url"])
            with connect() as conn:
                checker.check_channels(conn, client=remote, today=checked_on)
            for category in ("streetlight", "road_damage", "illegal_dumping"):
                response = api.post("/run", json=request(category))
                assert response.status_code == 200
                result = StageResult.model_validate(response.json())
                if next_level:
                    assert result.status == "success"
                    assert result.data["fallback_level"] == next_level
                else:
                    assert result.failure == "NO_CHANNEL"
                    assert result.data == {"channels": [], "fallback_level": "national"}
        with connect() as conn:
            checker.export_channels(conn, tmp_path / "channels.csv")
    exported = load_catalog(seed_paths(tmp_path))
    assert len(exported) == len(seeds)
    assert all(not row["active"] for row in exported if row["method"] != "phone")
    assert all(row["active"] for row in exported if row["method"] == "phone")
