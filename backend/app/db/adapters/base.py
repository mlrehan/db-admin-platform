"""DB Adapter Layer — abstract contract.

This module defines the **interface** every database adapter must satisfy. The Connection
Orchestrator (Phase 3) and Query Engine (Phase 5) depend only on this abstraction, never on a
concrete driver — dependency inversion that keeps multi-engine support pluggable.

Phase 3 establishes the *connection-lifecycle* surface (connect / close / ping /
test_connection). Later phases extend the same base class with execution and introspection
methods (Phase 4–6): adding abstract methods is additive and does not alter this contract's
shape. Concrete adapters (PostgreSQL/MySQL/MSSQL) arrive in Phase 4.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.db.adapters.metadata import (
    DatabaseInfo,
    RoutineInfo,
    SchemaInfo,
    TableDetail,
    TableInfo,
)
from app.db.engines import EngineType


@dataclass(frozen=True)
class ConnectionConfig:
    """Fully-resolved, decrypted connection parameters handed to an adapter.

    Instances are short-lived and never persisted. The plaintext ``password`` exists only in
    memory for the duration of a session's lifetime.
    """

    engine: EngineType
    host: str
    port: int
    # ``None``/empty means a *server-level* connection (no specific database): the adapter
    # connects to the engine's system database and the user can browse/select any database.
    database: str | None
    username: str
    password: str
    options: dict[str, Any] = field(default_factory=dict)
    ssl_mode: str | None = None
    connect_timeout: float = 10.0
    # Private per-session pool sizing (each session owns its own pool — isolation).
    pool_min_size: int = 1
    pool_max_size: int = 5

    def redacted(self) -> dict[str, Any]:
        """A log-safe representation with the password removed."""
        return {
            "engine": self.engine.value,
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "username": self.username,
            "ssl_mode": self.ssl_mode,
        }


@dataclass(frozen=True)
class ConnectionTestResult:
    ok: bool
    message: str
    server_version: str | None = None
    latency_ms: float | None = None


@dataclass(frozen=True)
class QueryColumn:
    name: str
    type_name: str | None = None


@dataclass(frozen=True)
class QueryResult:
    """Buffered result of a single statement execution."""

    columns: list[QueryColumn]
    rows: list[tuple[Any, ...]]
    row_count: int
    rows_affected: int | None
    execution_ms: float
    truncated: bool
    returns_rows: bool


@dataclass(frozen=True)
class QueryBatch:
    """One streamed chunk. ``columns`` is populated only on the first batch."""

    rows: list[tuple[Any, ...]]
    columns: list[QueryColumn] | None = None
    rows_affected: int | None = None
    returns_rows: bool = True


@dataclass(frozen=True)
class ScriptResultSet:
    """One result set produced by a multi-statement script run."""

    columns: list[QueryColumn]
    rows: list[tuple[Any, ...]]
    truncated: bool


@dataclass(frozen=True)
class ScriptRun:
    """Outcome of running a whole script as one session: every result set, plus row-count
    messages from non-returning statements (INSERT/UPDATE/DDL/…)."""

    result_sets: list[ScriptResultSet]
    messages: list[str]
    execution_ms: float


class DatabaseAdapter(ABC):
    """Lifecycle contract for a connection to a single target database.

    One adapter instance owns one logically-isolated connection (or a private pool). Adapters
    are never shared across users or sessions — isolation is enforced by the orchestrator
    creating a distinct adapter per session.
    """

    def __init__(self, config: ConnectionConfig) -> None:
        self._config = config

    @property
    def engine(self) -> EngineType:
        return self._config.engine

    @property
    def config(self) -> ConnectionConfig:
        return self._config

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Whether the adapter currently holds an open connection/pool."""

    @abstractmethod
    async def connect(self) -> None:
        """Open the underlying connection/pool. Idempotent."""

    @abstractmethod
    async def close(self) -> None:
        """Close the underlying connection/pool and release all resources. Idempotent."""

    @abstractmethod
    async def ping(self) -> bool:
        """Cheap liveness check; never raises."""

    @abstractmethod
    async def test_connection(self) -> ConnectionTestResult:
        """Validate connectivity/credentials, returning a structured result."""

    @abstractmethod
    async def execute(
        self,
        statement: str,
        parameters: Mapping[str, Any] | None = None,
        *,
        max_rows: int = 1000,
    ) -> QueryResult:
        """Execute one statement and return a buffered result.

        Result-returning statements yield up to ``max_rows`` rows (``truncated=True`` if more
        were available); non-returning statements report ``rows_affected``. The statement runs
        in its own transaction, committed on success.
        """

    @abstractmethod
    def stream(
        self,
        statement: str,
        parameters: Mapping[str, Any] | None = None,
        *,
        batch_size: int = 500,
    ) -> AsyncIterator[QueryBatch]:
        """Execute one statement and stream batches of rows via a server-side cursor.

        Returns an async iterator; the first batch carries the column metadata. Intended for
        result-returning statements (the Query Engine routes non-returning ones to
        :meth:`execute`).
        """

    # --- schema introspection (Metadata service) -----------------------------------------

    @abstractmethod
    async def list_schemas(self) -> list[SchemaInfo]:
        """List schemas/namespaces visible to the connection."""

    @abstractmethod
    async def list_tables(self, schema: str | None = None) -> list[TableInfo]:
        """List tables and views in ``schema`` (default schema when ``None``)."""

    @abstractmethod
    async def describe_table(self, table: str, schema: str | None = None) -> TableDetail:
        """Return columns, primary key, indexes and foreign keys for one table/view."""

    @abstractmethod
    async def list_routines(self, schema: str | None = None) -> list[RoutineInfo]:
        """List stored procedures and functions in ``schema``."""

    # --- server-level / multi-database support -------------------------------------------

    @property
    @abstractmethod
    def active_database(self) -> str | None:
        """The database currently targeted by schema/query operations."""

    @abstractmethod
    async def list_databases(self) -> list[DatabaseInfo]:
        """List the databases visible on the server (for server-level connections)."""

    @abstractmethod
    async def use_database(self, database: str | None) -> None:
        """Switch the active database. Subsequent schema/query operations target it.

        Passing ``None`` reverts to the connection's default/system database. Implementations
        bind to the requested database (for engines where a connection is database-scoped,
        such as PostgreSQL, this transparently uses a separate pooled engine per database)."""
