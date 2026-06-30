"""Test doubles.

``FakeAdapter`` is a fully-functional in-memory :class:`DatabaseAdapter` used to exercise the
orchestrator and session endpoints without a real target database. It lives in ``tests/`` (not
``app/``) so it is never part of the production system.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from typing import Any

from app.db.adapters.base import (
    ConnectionConfig,
    ConnectionTestResult,
    DatabaseAdapter,
    QueryBatch,
    QueryColumn,
    QueryResult,
)
from app.db.adapters.metadata import (
    ColumnInfo,
    IndexInfo,
    RoutineInfo,
    SchemaInfo,
    TableDetail,
    TableInfo,
)


class FakeAdapter(DatabaseAdapter):
    """A connect/close adapter whose behaviour is driven by ``options``.

    ``options={"fail_connect": True}`` makes :meth:`connect` raise, to test error paths.
    ``options={"exec_delay": <seconds>}`` makes execute/stream sleep, to test cancellation.
    ``options={"row_count": <n>}`` controls how many synthetic rows are produced.
    """

    # Mirrors the real adapters: a "system" schema non-admins must never see.
    system_schemas = frozenset({"pg_catalog"})
    system_schema_prefixes: tuple[str, ...] = ("pg_",)

    def __init__(self, config: ConnectionConfig) -> None:
        super().__init__(config)
        self._connected = False
        self.close_calls = 0
        self._active_db = None
        self.created_databases: list[str] = []

    def is_system_schema(self, name: str | None) -> bool:
        if not name:
            return False
        lowered = name.lower()
        if lowered in {s.lower() for s in self.system_schemas}:
            return True
        return any(lowered.startswith(p) for p in self.system_schema_prefixes)

    async def create_database(self, name: str) -> str:
        self.created_databases.append(name)
        return name

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def active_database(self) -> str | None:
        return self._active_db or self._config.database or None

    async def list_databases(self):
        from app.db.adapters.metadata import DatabaseInfo

        active = self.active_database
        return [
            DatabaseInfo(name=n, is_active=(n == active))
            for n in ("appdb", "analytics", "reporting")
        ]

    async def use_database(self, database: str | None) -> None:
        self._active_db = database or None

    async def connect(self) -> None:
        if self._config.options.get("fail_connect"):
            raise RuntimeError("simulated connect failure")
        self._connected = True

    async def close(self) -> None:
        self.close_calls += 1
        self._connected = False

    async def ping(self) -> bool:
        return self._connected

    async def test_connection(self) -> ConnectionTestResult:
        # Self-contained, mirroring the real adapters: connect if needed, report ok/false.
        try:
            if not self._connected:
                await self.connect()
        except Exception as exc:  # noqa: BLE001
            return ConnectionTestResult(ok=False, message=str(exc))
        return ConnectionTestResult(
            ok=True, message="ok", server_version="fake-1.0", latency_ms=0.01
        )

    # --- execution: synthesize rows or report affected counts ----------------------------

    def _returns_rows(self, statement: str) -> bool:
        head = statement.lstrip().split(None, 1)[0].lower() if statement.strip() else ""
        return head in {"select", "with", "show", "explain", "values"}

    def _synth_rows(self, n: int) -> list[tuple[Any, ...]]:
        return [(i, f"row-{i}") for i in range(n)]

    async def get_routine_definition(self, name: str) -> str | None:
        # Routine bodies are supplied via options={"routines": {name: definition}}.
        routines = self._config.options.get("routines") or {}
        return routines.get(name.split(".")[-1])

    async def run_script(self, sql: str, *, max_rows: int = 1000):
        """Single-session script run: one result set per row-returning statement, a message
        per non-returning one (mirrors the real adapter's contract)."""
        from app.db.adapters.base import ScriptResultSet, ScriptRun
        from app.services.sql_guard import split_sql_statements

        result_sets: list[ScriptResultSet] = []
        messages: list[str] = []
        for stmt in split_sql_statements(sql, self.engine):
            if self._returns_rows(stmt):
                total = int(self._config.options.get("row_count", 3))
                all_rows = self._synth_rows(total)
                result_sets.append(
                    ScriptResultSet(
                        columns=[QueryColumn("id"), QueryColumn("label")],
                        rows=all_rows[:max_rows],
                        truncated=len(all_rows) > max_rows,
                    )
                )
            else:
                messages.append(f"{int(self._config.options.get('affected', 1))} row(s) affected")
        return ScriptRun(result_sets=result_sets, messages=messages, execution_ms=0.5)

    async def execute(
        self,
        statement: str,
        parameters: Mapping[str, Any] | None = None,
        *,
        max_rows: int = 1000,
    ) -> QueryResult:
        delay = self._config.options.get("exec_delay")
        if delay:
            await asyncio.sleep(delay)
        if self._returns_rows(statement):
            total = int(self._config.options.get("row_count", 3))
            all_rows = self._synth_rows(total)
            truncated = len(all_rows) > max_rows
            rows = all_rows[:max_rows]
            return QueryResult(
                columns=[QueryColumn("id"), QueryColumn("label")],
                rows=rows,
                row_count=len(rows),
                rows_affected=None,
                execution_ms=0.5,
                truncated=truncated,
                returns_rows=True,
            )
        return QueryResult(
            columns=[],
            rows=[],
            row_count=0,
            rows_affected=int(self._config.options.get("affected", 1)),
            execution_ms=0.5,
            truncated=False,
            returns_rows=False,
        )

    async def stream(
        self,
        statement: str,
        parameters: Mapping[str, Any] | None = None,
        *,
        batch_size: int = 500,
    ) -> AsyncIterator[QueryBatch]:
        delay = self._config.options.get("exec_delay")
        if delay:
            await asyncio.sleep(delay)
        if not self._returns_rows(statement):
            yield QueryBatch(
                rows=[],
                columns=[],
                rows_affected=int(self._config.options.get("affected", 1)),
                returns_rows=False,
            )
            return
        total = int(self._config.options.get("row_count", 3))
        all_rows = self._synth_rows(total)
        columns = [QueryColumn("id"), QueryColumn("label")]
        emitted = False
        for i in range(0, max(len(all_rows), 1), batch_size):
            batch = all_rows[i : i + batch_size]
            yield QueryBatch(rows=batch, columns=columns if not emitted else None)
            emitted = True
        if not emitted:
            yield QueryBatch(rows=[], columns=columns)

    # --- metadata: canned schema --------------------------------------------------------

    async def list_schemas(self) -> list[SchemaInfo]:
        # "pg_catalog" is a system schema — the API hides it from non-admins.
        return [
            SchemaInfo(name="public", is_default=True),
            SchemaInfo(name="reporting"),
            SchemaInfo(name="pg_catalog"),
        ]

    async def list_tables(self, schema: str | None = None) -> list[TableInfo]:
        target = schema or "public"
        return [
            TableInfo(name="users", schema=target, kind="table"),
            TableInfo(name="orders", schema=target, kind="table"),
            TableInfo(name="active_users", schema=target, kind="view"),
        ]

    async def describe_table(self, table: str, schema: str | None = None) -> TableDetail:
        target = schema or "public"
        columns = [
            ColumnInfo(
                name="id", data_type="INTEGER", nullable=False, default=None,
                primary_key=True, autoincrement=True,
            ),
            ColumnInfo(
                name="label", data_type="VARCHAR(255)", nullable=True, default=None,
                primary_key=False,
            ),
        ]
        return TableDetail(
            table=TableInfo(name=table, schema=target, kind="table"),
            columns=columns,
            primary_key=["id"],
            indexes=[IndexInfo(name=f"ix_{table}_label", columns=["label"], unique=False)],
            foreign_keys=[],
        )

    async def list_routines(self, schema: str | None = None) -> list[RoutineInfo]:
        return [
            RoutineInfo(name="recalc_totals", kind="procedure", return_type=None),
            RoutineInfo(name="user_count", kind="function", return_type="integer"),
        ]
