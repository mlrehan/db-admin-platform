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

import asyncio
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
    Catalog,
    CatalogColumn,
    CatalogObject,
    CatalogRoutine,
    ColumnInfo,
    DatabaseInfo,
    JunctionTable,
    Relationship,
    RelationshipGraph,
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
    r"RESTORE\s+(?:DATABASE|LOG|FILELISTONLY|HEADERONLY|VERIFYONLY)|"
    r"BACKUP\s+(?:DATABASE|LOG)|"
    r"CREATE\s+TABLESPACE|DROP\s+TABLESPACE|"
    r"VACUUM|ALTER\s+SYSTEM|"
    r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+CONCURRENTLY|DROP\s+INDEX\s+CONCURRENTLY"
    r")\b",
    re.IGNORECASE,
)


def _requires_autocommit(statement: str) -> bool:
    return bool(_AUTOCOMMIT_RE.match(statement or ""))


def build_relationship_graph(
    fks: dict[tuple, list],
    pks: dict[tuple, Any],
    uniques: dict[tuple, list[tuple[str, ...]]],
    default_schema: str | None,
) -> RelationshipGraph:
    """Derive relationships + cardinalities + junction tables from reflected keys (pure)."""

    def norm(cols) -> frozenset[str]:
        return frozenset(c.lower() for c in cols)

    rels: list[Relationship] = []
    for (schema, table), items in fks.items():
        pk_cols = norm((pks.get((schema, table)) or {}).get("constrained_columns") or ())
        unique_sets = [norm(u) for u in uniques.get((schema, table), []) if u]
        for fk in items:
            cols = tuple(fk.get("constrained_columns") or ())
            if not cols or not fk.get("referred_table"):
                continue
            key = norm(cols)
            one_to_one = (pk_cols and key == pk_cols) or key in unique_sets
            opts = fk.get("options") or {}
            rels.append(
                Relationship(
                    name=fk.get("name"),
                    child_schema=schema,
                    child_table=table,
                    child_columns=cols,
                    parent_schema=fk.get("referred_schema") or schema or default_schema,
                    parent_table=fk["referred_table"],
                    parent_columns=tuple(fk.get("referred_columns") or ()),
                    cardinality="one-to-one" if one_to_one else "one-to-many",
                    on_delete=opts.get("ondelete"),
                    on_update=opts.get("onupdate"),
                )
            )
    junctions: list[JunctionTable] = []
    by_child: dict[tuple, list[Relationship]] = {}
    for r in rels:
        by_child.setdefault((r.child_schema, r.child_table), []).append(r)
    for key, items in by_child.items():
        pk_cols = norm((pks.get(key) or {}).get("constrained_columns") or ())
        if len(pk_cols) < 2:
            continue
        fk_cols = frozenset().union(*(norm(r.child_columns) for r in items))
        parents = tuple(dict.fromkeys((r.parent_schema, r.parent_table) for r in items))
        if pk_cols <= fk_cols and len(parents) >= 2:
            junctions.append(JunctionTable(schema=key[0], table=key[1], parents=parents))
    return RelationshipGraph(relationships=tuple(rels), junctions=tuple(junctions))


def _format_odbc_error(text: str) -> str:
    """Turn a pyodbc diagnostic string into an SSMS-style message.

    ``[42000] [Microsoft][ODBC Driver 17 for SQL Server][SQL Server]Must declare the scalar
    variable "@x". (137) (SQLExecDirectW)`` → ``Msg 137: Must declare the scalar variable "@x".``
    Multiple diagnostic records (pyodbc joins them with "; ") are each cleaned."""
    parts = []
    for record in re.split(r";\s*(?=\[[0-9A-Z]{5}\])", text):
        native = re.search(r"\((\d+)\)\s*(?:\(SQL\w+\))?\s*$", record)
        body = re.sub(r"\s*\(\d+\)\s*(?:\(SQL\w+\))?\s*$", "", record)
        body = re.sub(r"^\s*\[[0-9A-Z]{5}\]\s*", "", body)
        body = re.sub(r"^(\[[^\]]*\])+", "", body).strip()
        if not body:
            continue
        code = native.group(1) if native else None
        parts.append(f"Msg {code}: {body}" if code and code != "0" else body)
    return "\n".join(parts) or text


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
        # The session's dedicated script connection (SSMS "query window" semantics): every
        # Run of the editor uses this ONE connection, so temp tables, SET options, the USE'd
        # database and explicit transactions persist across Runs. It is never returned to the
        # pool (it may hold session state); it's invalidated when released.
        self._script_conn: AsyncConnection | None = None
        # The driver cursor currently executing a batch (SQL Server), for ODBC-level cancel.
        self._active_cursor: Any = None
        # Set by cancel_running(): the current script stops before its next statement/batch.
        self._cancel_requested = False

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
        # Closing the session discards its script connection; the server rolls back any
        # transaction the user left open (same as closing an SSMS query window).
        await self._release_script_connection()
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
        await self._retarget_script_connection(target)

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

    # SQL returning a routine's definition/body (one ``:name`` param). Set per engine; used to
    # authorize EXEC/CALL of read-only stored routines.
    routine_definition_sql: ClassVar[str | None] = None

    async def get_routine_definition(self, name: str) -> str | None:
        """Fetch a stored routine's source/body from the catalog (for read-only validation).

        Returns ``None`` if the engine doesn't expose it or the routine isn't found."""
        if not self.routine_definition_sql:
            return None
        cleaned = name.replace("[", "").replace("]", "").replace('"', "").replace("`", "")
        # SQL Server's OBJECT_ID resolves a schema-qualified name; other catalogs key on the
        # bare routine name.
        param = cleaned if self.dialect == "mssql" else cleaned.split(".")[-1]
        try:
            async with self.acquire() as conn:
                row = (await conn.execute(text(self.routine_definition_sql), {"name": param})).first()
        except Exception:  # noqa: BLE001 - treat catalog errors as "cannot verify" (deny upstream)
            return None
        return str(row[0]) if row and row[0] is not None else None

    # Per-object-type "get definition" SQL, one ``:name`` VALUE parameter (safely bound). Keys:
    # view / procedure / function / trigger / index. Engines set what their catalog exposes;
    # TABLES are generated from ``describe_table`` instead (uniform across all engines).
    object_definition_sql: ClassVar[dict[str, str]] = {}

    def _quote_ident(self, name: str) -> str:
        """Dialect-correct identifier quoting (defence-in-depth for the few object-definition
        queries that must interpolate an identifier, e.g. MySQL ``SHOW CREATE``)."""
        engine = self._engine_for(None)
        return engine.dialect.identifier_preparer.quote(name)

    async def get_object_definition(
        self, object_type: str, name: str, schema: str | None = None
    ) -> str | None:
        """Return an editable DDL/definition script for a database object so the user can view
        and modify its structure. ``object_type`` is one of table/view/procedure/function/
        trigger/index. Works across SQL Server, PostgreSQL and MySQL/MariaDB (per-engine catalog
        queries; tables are reconstructed from introspection). Returns ``None`` when the engine
        cannot produce a definition for that object."""
        otype = (object_type or "").strip().lower()
        if otype in ("table", "base table", "view") and otype != "view":
            return await self._generate_table_ddl(name, schema)

        # MySQL exposes everything via SHOW CREATE (identifier, not a bind value).
        if self.dialect == "mysql":
            defn = await self._mysql_show_create(otype, name, schema)
            if defn is not None:
                return defn

        sql = self.object_definition_sql.get(otype)
        if sql:
            cleaned = name.replace("[", "").replace("]", "").replace('"', "").replace("`", "")
            param = cleaned if self.dialect == "mssql" else cleaned.split(".")[-1]
            try:
                async with self.acquire() as conn:
                    row = (await conn.execute(text(sql), {"name": param})).first()
            except Exception:  # noqa: BLE001 - catalog error → "no definition available"
                row = None
            if row and row[0] is not None:
                return str(row[0])

        # Views on engines without a dedicated query, and proc/func fallbacks.
        if otype == "view":
            return await self._generate_view_ddl(name, schema)
        if otype in ("procedure", "function", "trigger"):
            return await self.get_routine_definition(name)
        return None

    async def _mysql_show_create(
        self, object_type: str, name: str, schema: str | None
    ) -> str | None:
        """MySQL/MariaDB ``SHOW CREATE {TABLE|VIEW|PROCEDURE|FUNCTION|TRIGGER}``. The object name
        is an identifier (not bindable) so it is validated + dialect-quoted."""
        kind = {
            "table": "TABLE", "base table": "TABLE", "view": "VIEW",
            "procedure": "PROCEDURE", "function": "FUNCTION", "trigger": "TRIGGER",
        }.get(object_type)
        if kind is None:
            return None
        ident = self._quote_ident(name.split(".")[-1])
        if schema:
            ident = f"{self._quote_ident(schema)}.{ident}"
        try:
            async with self.acquire() as conn:
                row = (await conn.execute(text(f"SHOW CREATE {kind} {ident}"))).first()
        except Exception:  # noqa: BLE001
            return None
        if not row:
            return None
        # SHOW CREATE returns the DDL in the last-but-one column for tables/views and the
        # "Create ..." column for routines; pick the longest string column defensively.
        mapping = row._mapping
        candidates = [v for v in mapping.values() if isinstance(v, str)]
        return max(candidates, key=len) if candidates else None

    async def _generate_view_ddl(self, name: str, schema: str | None) -> str | None:
        defn = await self.get_routine_definition(name)
        return defn

    async def _generate_table_ddl(self, name: str, schema: str | None) -> str | None:
        """Reconstruct a ``CREATE TABLE`` script from introspection (columns, PK, FKs) plus any
        secondary indexes as ``CREATE INDEX`` statements. Engine-neutral, so it works uniformly
        across SQL Server, PostgreSQL and MySQL/MariaDB."""
        try:
            detail = await self.describe_table(name, schema)
        except Exception:  # noqa: BLE001
            return None
        q = self._quote_ident
        full = f"{q(schema)}.{q(detail.table.name)}" if schema else q(detail.table.name)
        lines: list[str] = []
        for c in detail.columns:
            parts = [f"  {q(c.name)} {c.data_type}"]
            if not c.nullable:
                parts.append("NOT NULL")
            if c.default is not None:
                parts.append(f"DEFAULT {c.default}")
            lines.append(" ".join(parts))
        if detail.primary_key:
            pk_cols = ", ".join(q(c) for c in detail.primary_key)
            lines.append(f"  PRIMARY KEY ({pk_cols})")
        for fk in detail.foreign_keys:
            cols = ", ".join(q(c) for c in fk.columns)
            ref_tbl = (
                f"{q(fk.referred_schema)}.{q(fk.referred_table)}"
                if fk.referred_schema else q(fk.referred_table)
            )
            ref_cols = ", ".join(q(c) for c in fk.referred_columns)
            name_clause = f"CONSTRAINT {q(fk.name)} " if fk.name else ""
            lines.append(f"  {name_clause}FOREIGN KEY ({cols}) REFERENCES {ref_tbl} ({ref_cols})")
        ddl = f"CREATE TABLE {full} (\n" + ",\n".join(lines) + "\n);"
        # Secondary (non-primary) indexes as separate statements.
        for idx in detail.indexes:
            if idx.primary:
                continue
            unique = "UNIQUE " if idx.unique else ""
            cols = ", ".join(q(c) for c in idx.columns)
            ddl += f"\n\nCREATE {unique}INDEX {q(idx.name)} ON {full} ({cols});"
        return ddl

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
    # Query returning the connection's current database (engines with a USE statement).
    current_database_sql: ClassVar[str | None] = None
    # Whether the engine can switch databases on an existing connection with USE.
    supports_use_statement: ClassVar[bool] = False

    # --- the session's pinned script connection ------------------------------------------

    async def _script_connection(self) -> AsyncConnection:
        """Return the session's dedicated script connection, opening it on first use (or
        after it was invalidated). Runs in AUTOCOMMIT at the driver level, so explicit
        ``BEGIN``/``COMMIT``/``ROLLBACK`` issued by the user are honoured verbatim."""
        if not self._connected:
            await self.connect()
        conn = self._script_conn
        if conn is not None and (conn.closed or conn.invalidated):
            conn = None
        if conn is None:
            conn = await self._engine_for(self._active_db).connect()
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            self._script_conn = conn
        return conn

    async def _release_script_connection(self) -> None:
        """Discard the script connection. It is invalidated rather than returned to the pool,
        because it may carry session state (USE'd database, temp tables, open transaction)."""
        conn, self._script_conn = self._script_conn, None
        if conn is None:
            return
        try:
            await conn.invalidate()
        except Exception:  # noqa: BLE001 - best effort
            pass
        try:
            await conn.close()
        except Exception:  # noqa: BLE001
            pass

    async def _retarget_script_connection(self, target: str | None) -> None:
        """Point the script connection at a newly selected database: ``USE`` on engines that
        support it (keeps temp tables / session state, like SSMS), otherwise reconnect."""
        conn = self._script_conn
        if conn is None or conn.closed or conn.invalidated:
            return
        if self.supports_use_statement and target:
            try:
                await conn.exec_driver_sql(f"USE {self._quote_ident(target)}")
                return
            except Exception:  # noqa: BLE001 - fall back to a fresh connection
                pass
        await self._release_script_connection()

    # SQL that cancels the statement running on another backend, given its server id ({id}).
    cancel_backend_sql: ClassVar[str | None] = None

    async def cancel_running(self) -> None:
        """Best-effort *server-side* cancel of the statement running on the script connection
        (cancelling the asyncio task alone would leave the database still working):
        SQL Server → ODBC ``SQLCancel`` on the executing cursor; PostgreSQL / MySQL → cancel
        the session's backend from a separate pooled connection. Remaining statements/batches
        of the script are skipped."""
        self._cancel_requested = True
        impl = getattr(self._active_cursor, "_impl", None)
        if impl is not None and callable(getattr(impl, "cancel", None)):
            try:
                await asyncio.to_thread(impl.cancel)
            except Exception:  # noqa: BLE001
                logger.warning("ODBC cancel failed", exc_info=True)
            return
        conn = self._script_conn
        if conn is None or not self.cancel_backend_sql or conn.closed:
            return
        try:
            raw = await conn.get_raw_connection()
            driver = raw.driver_connection
            backend_id = None
            for attr in ("get_server_pid", "thread_id"):  # asyncpg / aiomysql
                getter = getattr(driver, attr, None)
                if callable(getter):
                    backend_id = getter()
                    break
            if backend_id is None:
                return
            async with self._engine_for(self._active_db).connect() as side:
                side = await side.execution_options(isolation_level="AUTOCOMMIT")
                await side.exec_driver_sql(self.cancel_backend_sql.format(id=int(backend_id)))
        except Exception:  # noqa: BLE001 - the connection is released by the caller anyway
            logger.warning("Backend cancel failed", exc_info=True)

    async def _current_database(self, conn: AsyncConnection) -> str | None:
        if not self.current_database_sql or conn.closed or conn.invalidated:
            return None
        try:
            value = (await conn.exec_driver_sql(self.current_database_sql)).scalar()
        except Exception:  # noqa: BLE001
            return None
        return str(value) if value else None

    # SQL returning a positive number while a transaction is open (engines whose driver
    # doesn't expose the state natively — SQL Server: @@TRANCOUNT).
    transaction_probe_sql: ClassVar[str | None] = None

    async def _in_transaction(self, conn: AsyncConnection) -> bool:
        """Whether the script connection is inside an explicit, still-open transaction."""
        if conn.closed or conn.invalidated:
            return False
        try:
            raw = await conn.get_raw_connection()
            driver = raw.driver_connection
            for attr in ("is_in_transaction", "get_transaction_status"):  # asyncpg / aiomysql
                probe = getattr(driver, attr, None)
                if callable(probe):
                    return bool(probe())
            flag = getattr(driver, "in_transaction", None)  # aiosqlite / sqlite3
            if isinstance(flag, bool):
                return flag
            if self.transaction_probe_sql:
                return bool((await conn.exec_driver_sql(self.transaction_probe_sql)).scalar())
        except Exception:  # noqa: BLE001
            return False
        return False

    # --- whole-script execution --------------------------------------------------------

    async def run_script(self, sql: str, *, max_rows: int = 1000) -> ScriptRun:
        start = time.perf_counter()
        result_sets: list[ScriptResultSet] = []
        messages: list[str] = []
        errors: list[str] = []
        self._cancel_requested = False
        conn = await self._script_connection()
        try:
            if self.script_mode == "batch":
                await self._run_script_batch(conn, sql, max_rows, result_sets, messages, errors)
            else:
                await self._run_script_sequential(conn, sql, max_rows, result_sets, messages, errors)
        finally:
            # A connection that broke (network/server restart) must not be reused.
            if conn.closed or conn.invalidated:
                await self._release_script_connection()

        # Follow a USE in the script: the session now targets that database (as SSMS updates
        # its database selector), so the explorer / data viewer / next run agree with it.
        changed_to: str | None = None
        current = await self._current_database(conn)
        if current and current.lower() != str(self._resolve_db(self._active_db) or "").lower():
            try:
                await self.use_database(current)
                changed_to = current
            except Exception:  # noqa: BLE001 - keep the previous selection if it's unusable
                changed_to = None
        open_tx = await self._in_transaction(conn) if self._script_conn is conn else False
        if open_tx:
            messages.append(
                "Warning: a transaction is still open on this session. "
                "Run COMMIT or ROLLBACK to end it."
            )
        elapsed = (time.perf_counter() - start) * 1000
        return ScriptRun(
            result_sets=result_sets,
            messages=messages,
            execution_ms=round(elapsed, 3),
            error=errors[0] if errors else None,
            errors=errors,
            database_changed_to=changed_to,
            open_transaction=open_tx,
        )

    @staticmethod
    def _script_error(exc: Exception) -> str:
        # Prefer the driver's own message (SQLAlchemy wraps it in .orig).
        orig = getattr(exc, "orig", None) or exc
        args = getattr(orig, "args", ())
        if (
            len(args) >= 2 and isinstance(args[0], str) and isinstance(args[1], str)
            and re.fullmatch(r"[0-9A-Z]{5}", args[0])
        ):
            return _format_odbc_error(args[1])
        if len(args) >= 2 and isinstance(args[0], int) and isinstance(args[1], str):
            return f"Error {args[0]}: {args[1]}"  # PyMySQL / aiomysql: (errno, message)
        # asyncpg & co. prepend "<class 'asyncpg.exceptions.X'>: ".
        message = re.sub(r"^<class '[^']+'>:\s*", "", str(orig).strip())
        return message or exc.__class__.__name__

    _DML_RE: ClassVar[re.Pattern[str]] = re.compile(
        r"^\s*(?:with\b[\s\S]*?\b)?(insert|update|delete|merge|replace|upsert)\b", re.IGNORECASE
    )

    def _collect_result(
        self,
        result: Any,
        max_rows: int,
        result_sets: list[ScriptResultSet],
        messages: list[str],
        statement: str = "",
    ) -> None:
        if result.returns_rows:
            fetched = result.fetchmany(max_rows + 1)
            truncated = len(fetched) > max_rows
            rows = [tuple(r) for r in fetched[:max_rows]]
            columns = [QueryColumn(name=str(k)) for k in result.keys()]
            result_sets.append(ScriptResultSet(columns, rows, truncated))
        else:
            affected = self._rows_affected(result.rowcount)
            # Like SSMS: report row counts for data changes; DDL/USE/SET "0 rows" is noise.
            if affected is not None and (affected > 0 or self._DML_RE.match(statement or "")):
                messages.append(f"{affected} row(s) affected")

    async def _run_script_sequential(
        self,
        conn: AsyncConnection,
        sql: str,
        max_rows: int,
        result_sets: list[ScriptResultSet],
        messages: list[str],
        errors: list[str],
    ) -> None:
        """Run each statement in order on the session's script connection (PostgreSQL, MySQL,
        SQLite — temp tables and session variables are connection-scoped). Stops at the first
        error, like pgAdmin / MySQL Workbench; result sets produced before it are kept."""
        from app.services.sql_guard import split_sql_statements  # local: avoid layering cycle

        statements = split_sql_statements(sql, self.engine)
        for idx, stmt in enumerate(statements, start=1):
            if self._cancel_requested:
                errors.append("Query was cancelled by the user.")
                return
            try:
                result = await conn.exec_driver_sql(stmt)
            except Exception as exc:  # noqa: BLE001 - report + keep prior result sets
                label = f"Statement {idx}: " if len(statements) > 1 else ""
                message = self._script_error(exc)
                if self._cancel_requested:
                    message = f"Query was cancelled by the user. ({message})"
                errors.append(f"{label}{message}")
                return
            self._collect_result(result, max_rows, result_sets, messages, stmt)
        if statements and not errors and not result_sets and not messages:
            messages.append("Commands completed successfully.")

    async def _run_script_batch(
        self,
        conn: AsyncConnection,
        sql: str,
        max_rows: int,
        result_sets: list[ScriptResultSet],
        messages: list[str],
        errors: list[str],
    ) -> None:
        """SQL Server: split the script into batches on ``GO`` (a *client* separator that must
        never reach the server), then send each batch — repeated ``n`` times for ``GO n`` — in
        order on the session's script connection, walking every result set with ``nextset()``.

        Mirrors SSMS: a failing batch is reported against its batch number and script line,
        and execution continues with the next batch. ``PRINT``/informational output is
        captured into the Messages list."""
        from app.services.sql_batches import BatchParseError, split_go_batches

        try:
            batches = split_go_batches(sql)
        except BatchParseError as exc:
            errors.append(str(exc))
            return
        multi = len(batches) > 1 or any(b.repeat > 1 for b in batches)
        raw = await conn.get_raw_connection()
        driver_conn = raw.driver_connection
        for batch in batches:
            label = f"Batch {batch.index} (line {batch.start_line})" if multi else ""
            for _ in range(batch.repeat):
                # A cancel can arrive before any batch is running (e.g. while the session's
                # connection is still being opened) — never start new work after it.
                if self._cancel_requested:
                    break
                failed = await self._exec_one_batch(
                    driver_conn, batch.sql, max_rows, result_sets, messages, errors, label
                )
                if failed or self._cancel_requested:
                    break  # stop repeating a failing batch; continue with the next batch
            if self._cancel_requested:
                # Unlike an ordinary error (SSMS continues with the next batch), a cancel stops
                # the whole script; results produced so far are kept.
                errors.append("Query was cancelled by the user.")
                break
            if conn.invalidated or conn.closed:
                break
        if multi and not errors:
            messages.append(f"{len(batches)} batch(es) completed successfully.")
        elif not errors and not result_sets and not messages:
            messages.append("Commands completed successfully.")

    async def _exec_one_batch(
        self,
        driver_conn: Any,
        batch_sql: str,
        max_rows: int,
        result_sets: list[ScriptResultSet],
        messages: list[str],
        errors: list[str],
        label: str,
    ) -> bool:
        """Execute one batch on the raw driver connection. Returns True if it failed."""
        cursor = await driver_conn.cursor()
        self._active_cursor = cursor
        try:
            await cursor.execute(batch_sql)
            while True:
                self._drain_driver_messages(cursor, messages)
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
            self._drain_driver_messages(cursor, messages)
            return False
        except asyncio.CancelledError:
            # Task cancelled (timeout or hard stop): the statement is still running in the ODBC
            # worker thread — cancel it at the server NOW, while we still hold the cursor.
            impl = getattr(cursor, "_impl", None)
            if impl is not None and callable(getattr(impl, "cancel", None)):
                try:
                    await asyncio.shield(asyncio.to_thread(impl.cancel))
                except Exception:  # noqa: BLE001
                    pass
            raise
        except Exception as exc:  # noqa: BLE001 - keep the result sets gathered before the error
            self._drain_driver_messages(cursor, messages)
            if self._cancel_requested:
                return True  # reported once by the caller as "cancelled by the user"
            message = self._script_error(exc)
            errors.append(f"{label}: {message}" if label else message)
            return True
        finally:
            self._active_cursor = None
            try:
                await cursor.close()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _drain_driver_messages(cursor: Any, messages: list[str]) -> None:
        """Move server informational messages (T-SQL ``PRINT``, low-severity ``RAISERROR``)
        exposed by pyodbc as ``cursor.messages`` into the Messages list, minus ODBC prefixes."""
        impl = getattr(cursor, "_impl", cursor)
        pending = getattr(impl, "messages", None)
        if not pending:
            return
        for item in list(pending):
            text_ = item[1] if isinstance(item, (tuple, list)) and len(item) > 1 else item
            text_ = re.sub(r"^(\[[^\]]*\])+", "", str(text_)).strip()
            if text_:
                messages.append(text_)
        try:
            pending.clear()
        except Exception:  # noqa: BLE001 - read-only on some driver versions
            pass

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

    # --- bulk catalog (IntelliSense) --------------------------------------------------------
    #
    # ONE query returns every column of every user table/view in the active database:
    # (schema, table, table_type, column, data_type, char_len, precision, scale, is_nullable).
    # The generic version uses INFORMATION_SCHEMA; engines override with faster native catalogs.
    catalog_columns_sql: ClassVar[str] = (
        "SELECT c.TABLE_SCHEMA, c.TABLE_NAME, t.TABLE_TYPE, c.COLUMN_NAME, c.DATA_TYPE, "
        "c.CHARACTER_MAXIMUM_LENGTH, c.NUMERIC_PRECISION, c.NUMERIC_SCALE, c.IS_NULLABLE "
        "FROM INFORMATION_SCHEMA.COLUMNS c "
        "JOIN INFORMATION_SCHEMA.TABLES t "
        "ON t.TABLE_SCHEMA = c.TABLE_SCHEMA AND t.TABLE_NAME = c.TABLE_NAME "
        "ORDER BY c.TABLE_SCHEMA, c.TABLE_NAME, c.ORDINAL_POSITION"
    )
    # (schema, routine name, 'PROCEDURE' | 'FUNCTION'); None when the engine has none.
    catalog_routines_sql: ClassVar[str | None] = (
        "SELECT ROUTINE_SCHEMA, ROUTINE_NAME, ROUTINE_TYPE FROM INFORMATION_SCHEMA.ROUTINES "
        "ORDER BY ROUTINE_SCHEMA, ROUTINE_NAME"
    )
    # Safety cap for enormous databases (the editor still works; the result is flagged).
    catalog_max_columns: ClassVar[int] = 150_000

    @staticmethod
    def _type_label(data_type: Any, char_len: Any, precision: Any, scale: Any) -> str:
        label = str(data_type or "").strip()
        if "(" in label:  # already fully formatted (MySQL column_type, PG format_type)
            return label
        if char_len is not None:
            try:
                n = int(char_len)
            except (TypeError, ValueError):
                n = None
            if n is not None and n != 0:
                return f"{label}({'max' if n < 0 else n})"
        if precision is not None and label.lower() in ("decimal", "numeric"):
            return f"{label}({precision},{scale or 0})"
        return label

    async def get_catalog(self) -> Catalog:
        """Every table/view (with its columns) and routine in the active database, in a
        single round trip per kind — the source for SQL IntelliSense."""
        async with self.acquire() as conn:
            default_schema = await self._default_schema(conn)
            result = await conn.execute(text(self.catalog_columns_sql))
            rows = result.fetchmany(self.catalog_max_columns + 1)
            routine_rows = (
                (await conn.execute(text(self.catalog_routines_sql))).fetchall()
                if self.catalog_routines_sql
                else []
            )
        truncated = len(rows) > self.catalog_max_columns
        objects: list[CatalogObject] = []
        current_key: tuple[Any, Any] | None = None
        columns: list[CatalogColumn] = []
        kind = "table"

        def flush() -> None:
            if current_key is not None:
                objects.append(
                    CatalogObject(schema=current_key[0], name=current_key[1], kind=kind,
                                  columns=tuple(columns))
                )

        for r in rows[: self.catalog_max_columns]:
            key = (r[0], r[1])
            if key != current_key:
                flush()
                current_key = key
                columns = []
                kind = "view" if "VIEW" in str(r[2] or "").upper() else "table"
            columns.append(
                CatalogColumn(
                    name=str(r[3]),
                    data_type=self._type_label(r[4], r[5], r[6], r[7]),
                    nullable=str(r[8]).upper() != "NO",
                )
            )
        flush()
        routines = tuple(
            CatalogRoutine(
                schema=(str(r[0]) if r[0] is not None else None),
                name=str(r[1]),
                kind="procedure" if "PROC" in str(r[2] or "").upper() else "function",
            )
            for r in routine_rows
        )
        return Catalog(default_schema=default_schema, objects=tuple(objects),
                       routines=routines, truncated=truncated)

    # --- relationship graph (database diagram) ----------------------------------------------

    max_relationship_schemas: ClassVar[int] = 50

    async def get_relationships(self) -> RelationshipGraph:
        """Every foreign key in the active database (all user schemas) with its cardinality,
        plus detected many-to-many junction tables — using SQLAlchemy's batched ``get_multi_*``
        reflection (a handful of queries per schema, not one per table)."""

        def _reflect(sync_conn: SyncConnection) -> dict[str, Any]:
            insp = inspect(sync_conn)
            default = insp.default_schema_name
            schemas = [s for s in insp.get_schema_names() if not self.is_system_schema(s)]
            schemas = schemas[: self.max_relationship_schemas] or [default]
            fks: dict[tuple, list] = {}
            pks: dict[tuple, Any] = {}
            uniques: dict[tuple, list[tuple[str, ...]]] = {}
            for schema in schemas:
                # Keys come back as (schema, table) where schema is None for the default one.
                for key, items in insp.get_multi_foreign_keys(schema=schema).items():
                    fks[(key[0] or default, key[1])] = items
                for key, pk in insp.get_multi_pk_constraint(schema=schema).items():
                    pks[(key[0] or default, key[1])] = pk
                try:
                    multi_uc = insp.get_multi_unique_constraints(schema=schema)
                except NotImplementedError:
                    # SQL Server's dialect doesn't reflect UNIQUE constraints separately; they
                    # are always backed by unique indexes, which are collected just below.
                    multi_uc = {}
                for key, ucs in multi_uc.items():
                    uniques.setdefault((key[0] or default, key[1]), []).extend(
                        tuple(u.get("column_names") or ()) for u in ucs
                    )
                for key, idxs in insp.get_multi_indexes(schema=schema).items():
                    uniques.setdefault((key[0] or default, key[1]), []).extend(
                        tuple(c for c in (i.get("column_names") or ()) if c)
                        for i in idxs if i.get("unique")
                    )
            return {"default": default, "fks": fks, "pks": pks, "uniques": uniques}

        async with self.acquire() as conn:
            data = await conn.run_sync(_reflect)
        return build_relationship_graph(data["fks"], data["pks"], data["uniques"], data["default"])

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
