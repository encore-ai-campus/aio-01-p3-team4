"""PostgreSQL catalogue access. Schema remains owned by the backend."""
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeVar
import asyncio

import psycopg
from psycopg.rows import dict_row
from redis.asyncio import Redis

from app.clients.chaos import ChaosClient
from app.services.catalog import COLUMNS, load_catalog, seed_paths

T = TypeVar("T")
INSERT = f"INSERT INTO channels ({', '.join(COLUMNS)}) VALUES ({', '.join(['%s'] * len(COLUMNS))})"
UPSERT = INSERT + " ON CONFLICT (channel_id) DO UPDATE SET " + ", ".join(f"{col}=EXCLUDED.{col}" for col in COLUMNS if col != "channel_id")


class ChannelRepository:
    def __init__(self, database_url: str, seeds_dir: Path, redis_url: str = ""):
        self.database_url, self.seeds_dir, self.redis_url = database_url, seeds_dir, redis_url

    async def _run(self, operation: Callable[[], Awaitable[T]]) -> T:
        if not self.database_url:
            raise psycopg.OperationalError("DATABASE_URL is not configured")
        redis = Redis.from_url(self.redis_url, socket_connect_timeout=1, socket_timeout=1) if self.redis_url else None
        try:
            return await ChaosClient(redis).run("locator", operation)
        finally:
            if redis:
                await redis.aclose()

    def _connect(self):
        return psycopg.connect(self.database_url, connect_timeout=3, options="-c statement_timeout=5000 -c lock_timeout=5000", row_factory=dict_row)

    async def seed(self, *, upsert: bool = False) -> int:
        def operation():
            with self._connect() as conn:
                if not upsert:
                    # A scheduled link check may hold write locks while checking URLs.
                    # A populated catalogue needs no startup write or table lock.
                    if conn.execute("SELECT EXISTS (SELECT 1 FROM channels) AS populated").fetchone()["populated"]:
                        return 0
                # Prevent concurrent workers seeding the same empty table.
                conn.execute("LOCK TABLE channels IN SHARE ROW EXCLUSIVE MODE")
                if not upsert:
                    cursor = conn.execute("SELECT EXISTS (SELECT 1 FROM channels) AS populated")
                    if cursor.fetchone()["populated"]:
                        return 0
                rows = load_catalog(seed_paths(self.seeds_dir))
                with conn.cursor() as cursor:
                    cursor.executemany(UPSERT if upsert else INSERT, [tuple(row[col] for col in COLUMNS) for row in rows])
                return len(rows)
        # A worker thread keeps FastAPI nonblocking and works with Windows' default loop.
        return await self._run(lambda: asyncio.to_thread(operation))

    async def candidates(self, inputs: dict) -> list[dict]:
        def operation():
            with self._connect() as conn:
                cursor = conn.execute(
                    "SELECT * FROM channels WHERE active AND category = ANY(%s) "
                    "AND NOT (channel_id = ANY(%s)) "
                    "AND ((NULLIF(sido, '') IS NULL AND NULLIF(sigungu, '') IS NULL) "
                    "OR (sido = %s AND (NULLIF(sigungu, '') IS NULL OR sigungu = %s)))",
                    ([inputs["category"], "all"], inputs["exclude_channel_ids"], inputs["region"]["sido"], inputs["region"]["sigungu"]),
                )
                return cursor.fetchall()
        return await self._run(lambda: asyncio.to_thread(operation))
