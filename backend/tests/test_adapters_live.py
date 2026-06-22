"""Phase 4 — live adapter integration tests.

These run only when a target database is provided via environment variables, so the default
test run (and CI without databases) stays green. Spin up disposable databases and point the
vars at them, e.g.::

    docker run -d --rm -e POSTGRES_PASSWORD=pw -p 55432:5432 postgres:16
    TEST_PG_HOST=localhost TEST_PG_PORT=55432 TEST_PG_USER=postgres \
        TEST_PG_PASSWORD=pw TEST_PG_DB=postgres pytest tests/test_adapters_live.py

Each engine block skips cleanly if its vars are unset.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from app.db.adapters.base import ConnectionConfig
from app.db.adapters.mysql import MySQLAdapter
from app.db.adapters.postgresql import PostgreSQLAdapter
from app.db.engines import EngineType


def _config_from_env(prefix: str, engine: EngineType) -> ConnectionConfig | None:
    host = os.environ.get(f"{prefix}_HOST")
    if not host:
        return None
    return ConnectionConfig(
        engine=engine,
        host=host,
        port=int(os.environ.get(f"{prefix}_PORT", engine.default_port)),
        database=os.environ.get(f"{prefix}_DB", ""),
        username=os.environ.get(f"{prefix}_USER", ""),
        password=os.environ.get(f"{prefix}_PASSWORD", ""),
        connect_timeout=10.0,
    )


async def _exercise(adapter, expected_version_token: str) -> None:
    assert not adapter.is_connected
    await adapter.connect()
    assert adapter.is_connected
    assert await adapter.ping() is True

    result = await adapter.test_connection()
    assert result.ok is True, result.message
    assert result.server_version and expected_version_token.lower() in result.server_version.lower()
    assert result.latency_ms is not None

    # acquire() yields a usable pooled connection.
    from sqlalchemy import text

    async with adapter.acquire() as conn:
        assert (await conn.execute(text("SELECT 1"))).scalar() == 1

    await adapter.close()
    assert not adapter.is_connected
    assert await adapter.ping() is False


@pytest.mark.asyncio
async def test_postgresql_live() -> None:
    config = _config_from_env("TEST_PG", EngineType.POSTGRESQL)
    if config is None:
        pytest.skip("TEST_PG_HOST not set")
    await _exercise(PostgreSQLAdapter(config), "postgresql")


@pytest.mark.asyncio
async def test_postgresql_bad_credentials() -> None:
    config = _config_from_env("TEST_PG", EngineType.POSTGRESQL)
    if config is None:
        pytest.skip("TEST_PG_HOST not set")
    from app.core.exceptions import ConnectionFailedError

    bad = ConnectionConfig(**{**config.__dict__, "password": "definitely-wrong"})
    adapter = PostgreSQLAdapter(bad)
    with pytest.raises(ConnectionFailedError):
        await adapter.connect()
    # test_connection reports failure rather than raising.
    assert (await adapter.test_connection()).ok is False


@pytest.mark.asyncio
async def test_mysql_live() -> None:
    config = _config_from_env("TEST_MYSQL", EngineType.MYSQL)
    if config is None:
        pytest.skip("TEST_MYSQL_HOST not set")
    await _exercise(MySQLAdapter(config), "")  # any version string is fine


@pytest.mark.asyncio
async def test_postgresql_execute_and_stream() -> None:
    config = _config_from_env("TEST_PG", EngineType.POSTGRESQL)
    if config is None:
        pytest.skip("TEST_PG_HOST not set")
    adapter = PostgreSQLAdapter(config)
    await adapter.connect()
    try:
        # DDL + DML report affected rows, not result rows.
        await adapter.execute("CREATE TEMP TABLE live_t (id int, label text)")
        ins = await adapter.execute(
            "INSERT INTO live_t SELECT g, 'r'||g FROM generate_series(1, 7) g"
        )
        assert ins.returns_rows is False
        assert ins.rows_affected == 7

        # Buffered read with truncation.
        res = await adapter.execute("SELECT * FROM live_t ORDER BY id", max_rows=5)
        assert res.returns_rows is True
        assert res.row_count == 5
        assert res.truncated is True
        assert [c.name for c in res.columns] == ["id", "label"]

        # Streamed read in small batches covers all 7 rows.
        total = 0
        saw_columns = False
        async for batch in adapter.stream("SELECT * FROM live_t ORDER BY id", batch_size=3):
            if batch.columns:
                saw_columns = True
            total += len(batch.rows)
        assert saw_columns and total == 7
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_postgresql_introspection() -> None:
    config = _config_from_env("TEST_PG", EngineType.POSTGRESQL)
    if config is None:
        pytest.skip("TEST_PG_HOST not set")
    adapter = PostgreSQLAdapter(config)
    await adapter.connect()
    try:
        await adapter.execute("DROP TABLE IF EXISTS child_t")
        await adapter.execute("DROP TABLE IF EXISTS parent_t")
        await adapter.execute(
            "CREATE TABLE parent_t (id int PRIMARY KEY, name text NOT NULL)"
        )
        await adapter.execute(
            "CREATE TABLE child_t (id int PRIMARY KEY, parent_id int REFERENCES parent_t(id))"
        )
        await adapter.execute("CREATE INDEX ix_child_parent ON child_t (parent_id)")

        schemas = await adapter.list_schemas()
        assert any(s.name == "public" and s.is_default for s in schemas)

        tables = {t.name: t for t in await adapter.list_tables("public")}
        assert "parent_t" in tables and tables["parent_t"].kind == "table"

        detail = await adapter.describe_table("child_t", "public")
        assert detail.primary_key == ["id"]
        col_names = [c.name for c in detail.columns]
        assert col_names == ["id", "parent_id"]
        assert any(not c.nullable and c.primary_key for c in detail.columns)
        assert detail.foreign_keys and detail.foreign_keys[0].referred_table == "parent_t"
        assert any(i.columns == ["parent_id"] for i in detail.indexes)
    finally:
        await adapter.execute("DROP TABLE IF EXISTS child_t")
        await adapter.execute("DROP TABLE IF EXISTS parent_t")
        await adapter.close()


@pytest.mark.asyncio
async def test_postgresql_server_level_connection() -> None:
    """A server-level PostgreSQL connection (no database) lists databases and switches."""
    config = _config_from_env("TEST_PG", EngineType.POSTGRESQL)
    if config is None:
        pytest.skip("TEST_PG_HOST not set")
    # Drop the database to make it server-level (connects to the "postgres" system DB).
    server_cfg = ConnectionConfig(**{**config.__dict__, "database": None})
    adapter = PostgreSQLAdapter(server_cfg)
    await adapter.connect()
    try:
        databases = await adapter.list_databases()
        names = {d.name for d in databases}
        assert "postgres" in names  # the maintenance DB is always present
        target = config.database or "postgres"
        assert target in names

        await adapter.use_database(target)
        assert adapter.active_database == target
        # Schema browsing now targets the selected database.
        schemas = await adapter.list_schemas()
        assert any(s.name == "public" for s in schemas)
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_postgresql_cancellation_recovers() -> None:
    config = _config_from_env("TEST_PG", EngineType.POSTGRESQL)
    if config is None:
        pytest.skip("TEST_PG_HOST not set")
    adapter = PostgreSQLAdapter(config)
    await adapter.connect()
    try:
        task = asyncio.create_task(adapter.execute("SELECT pg_sleep(10)"))
        await asyncio.sleep(0.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # The pool recovers: a subsequent query on the same adapter succeeds.
        res = await adapter.execute("SELECT 42 AS answer")
        assert res.rows[0][0] == 42
    finally:
        await adapter.close()
