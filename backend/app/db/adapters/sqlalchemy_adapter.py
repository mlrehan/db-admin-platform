"""SQLAlchemy-async adapter substrate.

Concrete engine adapters (PostgreSQL/MySQL/MSSQL) share their connection, pooling, ping and
test logic through :class:`SQLAlchemyAdapter`, which drives a private async engine per
adapter instance. Per-engine subclasses only supply what genuinely differs:

* ``dialect`` / ``driver`` — the SQLAlchemy URL scheme.
* ``server_version_sql`` — engine-specific version query.
* ``_url_query`` / ``_connect_args`` — DSN params and driver connect arguments (SSL, timeouts).

Each adapter owns one private pool, so a live session never shares connections with any other
session or user — the isolation guarantee the orchestrator relies on. Statement execution and
schema introspection are layered on top in Phases 5 and 6.
"""

from __future__ import annotations

import re
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any, ClassVar

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection as SyncConnection
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from app.core.exceptions import ConnectionFailedError, ValidationError
from app.core.logging import get_logger
from app.db.adapters.base import (
    ConnectionConfig,
    ConnectionTestResult,
    DatabaseAdapter,
    QueryBatch,
    QueryColumn,
    QueryResult,
    ScriptResultSet,
    ScriptRun,
)
from app.db.adapters.metadata import (
    ColumnInfo,
    DatabaseInfo,
    ForeignKeyInfo,
    IndexInfo,
    RoutineInfo,
    SchemaInfo,
    TableDetail,
    TableInfo,
)

logger = get_logger(__name__)

_POOL_RECYCLE_SECONDS = 1800

# A conservative database-name policy: starts with a letter/underscore, then letters, digits,
# underscore, '$' or '-' (max 63 chars). Names are also dialect-quoted before use, so this is
# defence-in-depth against injection, not the only guard.
_DB_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$-]{0,62}$")

# Statements that some engines (notably PostgreSQL and SQL Server) refuse to run inside a
# transaction block. They must execute in AUTOCOMMIT instead. Running a single statement in
# autocommit is equivalent to begin()+commit(), so this never changes transactional semantics
# for the statements listed here — it only avoids the "cannot run inside a transaction" error.
_AUTOCOMMIT_RE = re.compile(
    r"^\s*(?:"
    r"CREATE\s+DATABASE|DROP\s+DATABASE|ALTER\s+DATABASE|"
    r"CREATE\s+TABLESPACE|DROP\s+TABLESPACE|"
    r"VACUUM|ALTER\s+SYSTEM|"
    r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+CONCURRENTLY|DROP\s+INDEX\s+CONCURRENTLY"
    r")\b",
    re.IGNORECASE,
)


def _requires_autocommit(statement: str) -> bool:
    return bool(_AUTOCOMMIT_RE.match(statement or ""))


class SQLAlchemyAdapter(DatabaseAdapter):
    # Subclasses must set these.
    dialect: ClassVar[str]
    driver: ClassVar[str]
    server_version_sql: ClassVar[str] = "SELECT 1"
    application_name: ClassVar[str] = "db-admin-platform"

    # System database to connect to for a server-level connection (no specific database).
    system_database: ClassVar[str | None] = None
    # SQL returning one column of database names (set per engine).
    databases_sql: ClassVar[str] = "SELECT NULL WHERE 1=0"
    # Databases hidden from the user-facing listing (engine system databases).
    hidden_databases: ClassVar[frozenset[str]] = frozenset()
    # Engine "system" schemas (catalogs/metadata) that non-admins must never see.
    system_schemas: ClassVar[frozenset[str]] = frozenset()
    # Name prefixes that identify dynamic system schemas (e.g. PostgreSQL's pg_*).
    system_schema_prefixes: ClassVar[tuple[str, ...]] = ()

    def is_system_schema(self, name: str | None) -> bool:
        """Whether ``name`` is an engine-internal schema (hidden from non-admins)."""
        if not name:
            return False
        lowered = name.lower()
        if lowered in {s.lower() for s in self.system_schemas}:
            return True
        return any(lowered.startswith(p.lower()) for p in self.system_schema_prefixes)

    def __init__(self, config: ConnectionConfig) -> None:
        super().__init__(config)
        # One pooled engine per targeted database (keyed by resolved database name). A
        # PostgreSQL connection is database-scoped, so browsing multiple databases means
        # multiple engines; MySQL/MSSQL behave the same way uniformly here.
        self._engines: dict[str | None, AsyncEngine] = {}
        self._active_db: str | None = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def active_database(self) -> str | None:
        return self._active_db or self._config.database or None

    # --- per-engine hooks ----------------------------------------------------------------

    def _url_query(self) -> dict[str, str]:
        """Extra URL query parameters (e.g. charset, odbc driver)."""
        return {}

    def _connect_args(self) -> dict[str, Any]:
        """DBAPI connect arguments (SSL context, timeouts, app name)."""
        return {}

    # --- engine construction -------------------------------------------------------------

    def _resolve_db(self, database: str | None) -> str | None:
        """The database actually connected to: explicit > configured > system default."""
        return database or self._config.database or self.system_database

    def _build_url(self, database: str | None = None) -> URL:
        return URL.create(
            drivername=f"{self.dialect}+{self.driver}",
            username=self._config.username,
            password=self._config.password,
            host=self._config.host,
            port=self._config.port,
            database=self._resolve_db(database),
            query=self._url_query(),
        )

    def _create_engine(self, database: str | None) -> AsyncEngine:
        cfg = self._config
        max_overflow = max(cfg.pool_max_size - cfg.pool_min_size, 0)
        return create_async_engine(
            self._build_url(database),
            pool_size=cfg.pool_min_size,
            max_overflow=max_overflow,
            pool_timeout=cfg.connect_timeout,
            pool_pre_ping=True,
            pool_recycle=_POOL_RECYCLE_SECONDS,
            connect_args=self._connect_args(),
        )

    def _engine_for(self, database: str | None) -> AsyncEngine:
        key = self._resolve_db(database)
        engine = self._engines.get(key)
        if engine is None:
            engine = self._create_engine(database)
            self._engines[key] = engine
        return engine

    # --- lifecycle -----------------------------------------------------------------------

    async def connect(self) -> None:
        if self._connected:
            return
        engine: AsyncEngine | None = None
        try:
            engine = self._create_engine(None)
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as exc:
            if engine is not None:
                await engine.dispose()
            logger.warning("Adapter connect failed", extra=self._config.redacted())
            raise ConnectionFailedError(
                "Could not establish a connection to the target database.",
                details=self._config.redacted(),
            ) from exc
        self._engines[self._resolve_db(None)] = engine
        self._connected = True

    async def close(self) -> None:
        engines = list(self._engines.values())
        self._engines.clear()
        self._connected = False
        for engine in engines:
            await engine.dispose()

    async def ping(self) -> bool:
        if not self._connected:
            return False
        try:
            async with self._engine_for(self._active_db).connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[AsyncConnection]:
        """Yield a pooled connection to the *active* database."""
        if not self._connected:
            await self.connect()
        async with self._engine_for(self._active_db).connect() as conn:
            yield conn

    async def use_database(self, database: str | None) -> None:
        target = database or None
        if target == self._active_db:
            return
        # Validate connectivity to the target before switching (immediate feedback).
        try:
            async with self._engine_for(target).connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as exc:
            broken = self._engines.pop(self._resolve_db(target), None)
            if broken is not None:
                await broken.dispose()
            raise ConnectionFailedError(
                f"Cannot access database '{target}'.",
                details={**self._config.redacted(), "database": target},
            ) from exc
        self._active_db = target

    async def list_databases(self) -> list[DatabaseInfo]:
        if not self._connected:
            await self.connect()
        async with self._engine_for(None).connect() as conn:
            rows = (await conn.execute(text(self.databases_sql))).fetchall()
        active = self.active_database
        databases = [
            DatabaseInfo(name=str(r[0]), is_active=(str(r[0]) == active))
            for r in rows
            if str(r[0]) not in self.hidden_databases
        ]
        databases.sort(key=lambda d: d.name)
        return databases

    async def create_database(self, name: str) -> str:
        """Create a new database on the server and return its name.

        Runs in **AUTOCOMMIT** because ``CREATE DATABASE`` cannot execute inside a transaction
        on PostgreSQL. The name is validated against a strict identifier policy and then quoted
        with the dialect's identifier preparer (defence-in-depth against SQL injection).
        """
        candidate = (name or "").strip()
        if not _DB_NAME_RE.match(candidate):
            raise ValidationError(
                "Invalid database name. Use a letter or underscore followed by letters, "
                "digits, underscore, '$' or '-' (max 63 characters)."
            )
        if not self._connected:
            await self.connect()
        engine = self._engine_for(None)
        quoted = engine.dialect.identifier_preparer.quote(candidate)
        async with engine.connect() as conn:
            autocommit = await conn.execution_options(isolation_level="AUTOCOMMIT")
            await autocommit.execute(text(f"CREATE DATABASE {quoted}"))
        return candidate

    async def test_connection(self) -> ConnectionTestResult:
        """Self-contained connectivity check. Never raises — failures return ``ok=False``.

        If the adapter is not already connected, a throwaway engine is created and disposed,
        leaving adapter state untouched.
        """
        own_engine = not self._connected
        engine: AsyncEngine | None = None
        start = time.perf_counter()
        try:
            engine = self._engines.get(self._resolve_db(None)) or self._create_engine(None)
            async with engine.connect() as conn:
                version = (await conn.execute(text(self.server_version_sql))).scalar()
            latency_ms = (time.perf_counter() - start) * 1000
            return ConnectionTestResult(
                ok=True,
                message="Connection successful.",
                server_version=str(version) if version is not None else None,
                latency_ms=round(latency_ms, 2),
            )
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            return ConnectionTestResult(ok=False, message=self._redact(str(exc)))
        finally:
            if own_engine and engine is not None:
                await engine.dispose()

    # --- execution -----------------------------------------------------------------------

    @staticmethod
    def _rows_affected(rowcount: int | None) -> int | None:
        return rowcount if rowcount is not None and rowcount >= 0 else None

    def _result_from(self, result: Any, start: float, max_rows: int) -> QueryResult:
        """Build a :class:`QueryResult` from a freshly-executed cursor result."""
        if result.returns_rows:
            # Fetch one extra row to detect truncation without scanning everything.
            fetched = result.fetchmany(max_rows + 1)
            truncated = len(fetched) > max_rows
            rows = [tuple(row) for row in fetched[:max_rows]]
            columns = [QueryColumn(name=str(k)) for k in result.keys()]
            elapsed = (time.perf_counter() - start) * 1000
            return QueryResult(
                columns=columns,
                rows=rows,
                row_count=len(rows),
                rows_affected=None,
                execution_ms=round(elapsed, 3),
                truncated=truncated,
                returns_rows=True,
            )
        affected = self._rows_affected(result.rowcount)
        elapsed = (time.perf_counter() - start) * 1000
        return QueryResult(
            columns=[],
            rows=[],
            row_count=0,
            rows_affected=affected,
            execution_ms=round(elapsed, 3),
            truncated=False,
            returns_rows=False,
        )

    async def execute(
        self,
        statement: str,
        parameters: Mapping[str, Any] | None = None,
        *,
        max_rows: int = 1000,
    ) -> QueryResult:
        start = time.perf_counter()
        if _requires_autocommit(statement):
            # CREATE DATABASE / VACUUM / … cannot run inside a transaction on some engines.
            async with self.acquire() as conn:
                autocommit = await conn.execution_options(isolation_level="AUTOCOMMIT")
                result = await autocommit.execute(text(statement), dict(parameters or {}))
                return self._result_from(result, start, max_rows)
        async with self.acquire() as conn:
            async with conn.begin():
                result = await conn.execute(text(statement), dict(parameters or {}))
                return self._result_from(result, start, max_rows)

    # --- whole-script (single session) execution -----------------------------------------
    #
    # A multi-statement script must run on ONE connection so temporary tables, session
    # variables and procedural blocks share scope, and ALL result sets are returned. Two
    # strategies, chosen per engine:
    #   • "sequential" (PostgreSQL/MySQL): run each statement on the same connection. Temp
    #     tables and @session variables are connection-scoped, so this preserves them.
    #   • "batch" (SQL Server): send the WHOLE script in one driver call and walk nextset() —
    #     required because T-SQL variables / table variables / cursors are *batch*-scoped.
    script_mode: ClassVar[str] = "sequential"

    async def run_script(self, sql: str, *, max_rows: int = 1000) -> ScriptRun:
        start = time.perf_counter()
        if self.script_mode == "batch":
            result_sets, messages = await self._run_script_batch(sql, max_rows)
        else:
            result_sets, messages = await self._run_script_sequential(sql, max_rows)
        elapsed = (time.perf_counter() - start) * 1000
        return ScriptRun(result_sets=result_sets, messages=messages, execution_ms=round(elapsed, 3))

    async def _run_script_sequential(
        self, sql: str, max_rows: int
    ) -> tuple[list[ScriptResultSet], list[str]]:
        from app.services.sql_guard import split_sql_statements  # local: avoid layering cycle

        result_sets: list[ScriptResultSet] = []
        messages: list[str] = []
        async with self.acquire() as conn:
            # AUTOCOMMIT so each statement commits on the shared connection (temp tables created
            # this way persist for the connection's lifetime, just like a real session).
            run = await conn.execution_options(isolation_level="AUTOCOMMIT")
            for stmt in split_sql_statements(sql, self.engine):
                result = await run.exec_driver_sql(stmt)
                if result.returns_rows:
                    fetched = result.fetchmany(max_rows + 1)
                    truncated = len(fetched) > max_rows
                    rows = [tuple(r) for r in fetched[:max_rows]]
                    columns = [QueryColumn(name=str(k)) for k in result.keys()]
                    result_sets.append(ScriptResultSet(columns, rows, truncated))
                else:
                    affected = self._rows_affected(result.rowcount)
                    if affected is not None:
                        messages.append(f"{affected} row(s) affected")
        return result_sets, messages

    async def _run_script_batch(
        self, sql: str, max_rows: int
    ) -> tuple[list[ScriptResultSet], list[str]]:
        """Send the whole script as one batch and collect every result set via the driver's
        native ``nextset()`` (SQL Server / MySQL async DBAPI cursors support this)."""
        result_sets: list[ScriptResultSet] = []
        messages: list[str] = []
        async with self.acquire() as conn:
            autoconn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            raw = await autoconn.get_raw_connection()
            cursor = await raw.driver_connection.cursor()
            try:
                await cursor.execute(sql)
                while True:
                    if cursor.description:
                        columns = [QueryColumn(name=str(d[0])) for d in cursor.description]
                        fetched = await cursor.fetchmany(max_rows + 1)
                        truncated = len(fetched) > max_rows
                        rows = [tuple(r) for r in fetched[:max_rows]]
                        result_sets.append(ScriptResultSet(columns, rows, truncated))
                    else:
                        affected = self._rows_affected(getattr(cursor, "rowcount", -1))
                        if affected is not None:
                            messages.append(f"{affected} row(s) affected")
                    if not await cursor.nextset():
                        break
            finally:
                await cursor.close()
        return result_sets, messages

    async def stream(
        self,
        statement: str,
        parameters: Mapping[str, Any] | None = None,
        *,
        batch_size: int = 500,
    ) -> AsyncIterator[QueryBatch]:
        async with self.acquire() as conn:
            async with conn.begin():
                result = await conn.stream(text(statement), dict(parameters or {}))
                # The streaming AsyncResult has no `returns_rows`; probe via keys() instead.
                try:
                    keys = list(result.keys())
                except Exception:
                    keys = []
                if not keys:
                    # Non-returning statement (DML/DDL). The engine routes these to execute(),
                    # but handle it here too for a complete contract.
                    await result.close()
                    yield QueryBatch(rows=[], columns=[], returns_rows=False)
                    return
                columns = [QueryColumn(name=str(k)) for k in keys]
                emitted = False
                async for partition in result.partitions(batch_size):
                    rows = [tuple(row) for row in partition]
                    yield QueryBatch(rows=rows, columns=columns if not emitted else None)
                    emitted = True
                if not emitted:
                    # Result set was empty: still emit the column metadata once.
                    yield QueryBatch(rows=[], columns=columns)

    # --- schema introspection ------------------------------------------------------------
    #
    # SQLAlchemy's dialect-aware Inspector provides uniform schema/table/column/index/FK
    # reflection across PostgreSQL, MySQL and MSSQL (run via run_sync over the async
    # connection). Stored routines aren't covered by the Inspector, so they're read from the
    # standard ``information_schema.routines`` view (supported by all three engines).

    # Override per engine if the version query / routine source must differ.
    routines_sql: ClassVar[str] = (
        "SELECT routine_name, routine_type, data_type "
        "FROM information_schema.routines WHERE routine_schema = :schema "
        "ORDER BY routine_name"
    )

    async def list_schemas(self) -> list[SchemaInfo]:
        def _inspect(sync_conn: SyncConnection) -> tuple[list[str], str | None]:
            insp = inspect(sync_conn)
            return insp.get_schema_names(), insp.default_schema_name

        async with self.acquire() as conn:
            names, default = await conn.run_sync(_inspect)
        return [SchemaInfo(name=n, is_default=(n == default)) for n in names]

    async def _default_schema(self, conn: AsyncConnection) -> str | None:
        return await conn.run_sync(lambda c: inspect(c).default_schema_name)

    async def list_tables(self, schema: str | None = None) -> list[TableInfo]:
        def _inspect(sync_conn: SyncConnection) -> tuple[str | None, list[str], list[str]]:
            insp = inspect(sync_conn)
            target = schema or insp.default_schema_name
            tables = insp.get_table_names(schema=target)
            views = insp.get_view_names(schema=target)
            return target, tables, views

        async with self.acquire() as conn:
            target, tables, views = await conn.run_sync(_inspect)
        result = [TableInfo(name=t, schema=target, kind="table") for t in tables]
        result += [TableInfo(name=v, schema=target, kind="view") for v in views]
        result.sort(key=lambda t: t.name)
        return result

    async def describe_table(self, table: str, schema: str | None = None) -> TableDetail:
        def _inspect(sync_conn: SyncConnection) -> dict[str, Any]:
            insp = inspect(sync_conn)
            target = schema or insp.default_schema_name
            is_view = table in set(insp.get_view_names(schema=target))
            return {
                "schema": target,
                "is_view": is_view,
                "columns": insp.get_columns(table, schema=target),
                "pk": insp.get_pk_constraint(table, schema=target),
                "indexes": insp.get_indexes(table, schema=target),
                "fks": insp.get_foreign_keys(table, schema=target),
            }

        async with self.acquire() as conn:
            data = await conn.run_sync(_inspect)

        pk_columns: list[str] = list(data["pk"].get("constrained_columns") or [])
        pk_set = set(pk_columns)
        columns = [
            ColumnInfo(
                name=col["name"],
                data_type=str(col.get("type")),
                nullable=bool(col.get("nullable", True)),
                default=None if col.get("default") is None else str(col.get("default")),
                primary_key=col["name"] in pk_set,
                autoincrement=bool(col.get("autoincrement", False)),
                comment=col.get("comment"),
            )
            for col in data["columns"]
        ]
        indexes = [
            IndexInfo(
                name=idx.get("name") or "",
                columns=[c for c in (idx.get("column_names") or []) if c is not None],
                unique=bool(idx.get("unique", False)),
            )
            for idx in data["indexes"]
        ]
        foreign_keys = [
            ForeignKeyInfo(
                name=fk.get("name"),
                columns=list(fk.get("constrained_columns") or []),
                referred_schema=fk.get("referred_schema"),
                referred_table=fk.get("referred_table") or "",
                referred_columns=list(fk.get("referred_columns") or []),
            )
            for fk in data["fks"]
        ]
        return TableDetail(
            table=TableInfo(
                name=table,
                schema=data["schema"],
                kind="view" if data["is_view"] else "table",
            ),
            columns=columns,
            primary_key=pk_columns,
            indexes=indexes,
            foreign_keys=foreign_keys,
        )

    async def list_routines(self, schema: str | None = None) -> list[RoutineInfo]:
        async with self.acquire() as conn:
            target = schema or await self._default_schema(conn)
            result = await conn.execute(text(self.routines_sql), {"schema": target})
            rows = result.fetchall()
        routines: list[RoutineInfo] = []
        for name, routine_type, data_type in rows:
            kind = "procedure" if str(routine_type).upper() == "PROCEDURE" else "function"
            routines.append(RoutineInfo(name=name, kind=kind, return_type=data_type))
        return routines

    # --- helpers -------------------------------------------------------------------------

    def _redact(self, message: str) -> str:
        """Strip the plaintext password out of a driver error message, just in case."""
        pwd = self._config.password
        if pwd and pwd in message:
            message = message.replace(pwd, "***")
        return message
