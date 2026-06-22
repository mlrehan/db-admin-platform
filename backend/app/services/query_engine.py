"""Query Engine.

Executes SQL over a live :class:`~app.services.orchestrator.LiveSession`, providing:

* **Async buffered execution** with a row cap and per-statement timeout.
* **Streaming execution** (async generator of typed events) for large result sets.
* **Cancellation** — running buffered queries are tracked by id and can be cancelled; the
  streaming path is cancelled by the consumer (the WebSocket handler) tearing down iteration.
* **Safety enforcement** — every statement passes through :class:`SqlGuard` first.
* **Auditing** — every execution emits a :class:`QueryAuditEvent` to the injected sink.

Per-session serialization is guaranteed by holding the session's lock during execution, so a
single live session never runs two statements concurrently (isolation).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.core.config import QuerySettings
from app.core.exceptions import (
    AppError,
    NotFoundError,
    QueryCancelledError,
    QueryExecutionError,
    QueryTimeoutError,
    ValidationError,
)
from app.core.context import get_request_id
from app.core.logging import get_logger
from app.db.adapters.base import QueryColumn
from app.models.user import User
from app.services.access_control import AccessPolicy
from app.services.audit_sink import AuditSink, QueryAuditEvent
from app.services.orchestrator import LiveSession
from app.services.sql_guard import SqlAnalysis, SqlGuard, split_sql_statements
from app.utils.serialization import row_to_list

logger = get_logger(__name__)


@dataclass
class _RunningQuery:
    id: uuid.UUID
    user_id: uuid.UUID
    session_id: uuid.UUID
    task: asyncio.Task[Any]
    started_at: datetime
    category: str


@dataclass(frozen=True)
class ExecuteResult:
    query_id: uuid.UUID
    columns: list[QueryColumn]
    rows: list[list[Any]]
    row_count: int
    rows_affected: int | None
    execution_ms: float
    truncated: bool
    returns_rows: bool
    category: str
    destructive: bool


@dataclass(frozen=True)
class StatementOutcome:
    sql: str
    success: bool
    returns_rows: bool
    columns: list[QueryColumn]
    rows: list[list[Any]]
    row_count: int
    rows_affected: int | None
    execution_ms: float
    truncated: bool
    category: str
    destructive: bool
    error_code: str | None
    error: str | None


@dataclass(frozen=True)
class ScriptResult:
    statements: list[StatementOutcome]
    success: bool


class QueryEngine:
    def __init__(self, guard: SqlGuard, audit: AuditSink, settings: QuerySettings) -> None:
        self._guard = guard
        self._audit = audit
        self._settings = settings
        self._running: dict[uuid.UUID, _RunningQuery] = {}
        self._lock = asyncio.Lock()

    # --- authorization -------------------------------------------------------------------

    def _authorize(
        self, user: User, sql: str, session: LiveSession, policy: AccessPolicy | None
    ) -> SqlAnalysis:
        """Classify the SQL and authorize it, returning the analysis for auditing."""
        analysis = self._guard.analyze(sql)
        self._enforce(user, sql, analysis, session, policy)
        return analysis

    def _enforce(
        self,
        user: User,
        sql: str,
        analysis: SqlAnalysis,
        session: LiveSession,
        policy: AccessPolicy | None,
    ) -> None:
        """Authorization gate. When a policy is supplied (all user-facing paths), the access
        grants are the single source of truth — admins bypass, non-admins are default-deny.
        Only when no policy is available (defensive fallback) does the coarse role check apply.
        """
        if policy is not None:
            policy.enforce_query(session.adapter.engine, session.adapter.active_database, sql)
        else:
            self._guard.enforce(user, analysis)

    # --- buffered execution --------------------------------------------------------------

    async def execute(
        self,
        *,
        user: User,
        session: LiveSession,
        sql: str,
        parameters: Mapping[str, Any] | None = None,
        max_rows: int | None = None,
        policy: AccessPolicy | None = None,
    ) -> ExecuteResult:
        analysis = self._authorize(user, sql, session, policy)
        capped = self._cap_rows(max_rows)
        query_id = uuid.uuid4()
        started = QueryAuditEvent.now()

        async with session.lock:  # serialize per session
            session.touch()
            task: asyncio.Task[Any] = asyncio.create_task(
                session.adapter.execute(sql, parameters, max_rows=capped)
            )
            self._register(query_id, user, session, task, analysis)
            try:
                result = await asyncio.wait_for(task, self._settings.statement_timeout_seconds)
            except TimeoutError as exc:
                await self._audit_event(
                    user, session, analysis, started, success=False,
                    error_code="QUERY_TIMEOUT", error="statement timeout",
                )
                raise QueryTimeoutError() from exc
            except asyncio.CancelledError:
                await self._audit_event(
                    user, session, analysis, started, success=False,
                    error_code="QUERY_CANCELLED", error="cancelled by user",
                )
                raise QueryCancelledError()
            except AppError:
                raise
            except Exception as exc:
                await self._audit_event(
                    user, session, analysis, started, success=False,
                    error_code="QUERY_EXECUTION_ERROR", error=str(exc),
                )
                raise QueryExecutionError(self._safe_error(exc)) from exc
            finally:
                self._unregister(query_id)

        await self._audit_event(
            user, session, analysis, started, success=True,
            row_count=result.row_count, rows_affected=result.rows_affected,
        )
        return ExecuteResult(
            query_id=query_id,
            columns=result.columns,
            rows=[row_to_list(r) for r in result.rows],
            row_count=result.row_count,
            rows_affected=result.rows_affected,
            execution_ms=result.execution_ms,
            truncated=result.truncated,
            returns_rows=result.returns_rows,
            category=analysis.category.value,
            destructive=analysis.destructive,
        )

    # --- script execution (multiple statements) ------------------------------------------

    async def execute_script(
        self,
        *,
        user: User,
        session: LiveSession,
        sql: str,
        parameters: Mapping[str, Any] | None = None,
        max_rows: int | None = None,
        policy: AccessPolicy | None = None,
    ) -> ScriptResult:
        """Run a batch of statements sequentially (SSMS/DataGrip-style script execution).

        Each statement is independently role- and access-checked, executed and audited.
        Execution stops at the first failing statement; the outcomes gathered so far are
        returned so the editor can show a per-statement messages log.
        """
        statements = split_sql_statements(sql)
        if not statements:
            raise ValidationError("No SQL statement to execute.")
        capped = self._cap_rows(max_rows)
        outcomes: list[StatementOutcome] = []
        overall_ok = True

        async with session.lock:
            for stmt in statements:
                session.touch()
                started = QueryAuditEvent.now()
                analysis = self._guard.analyze(stmt)
                try:
                    self._enforce(user, stmt, analysis, session, policy)
                    result = await asyncio.wait_for(
                        session.adapter.execute(stmt, parameters, max_rows=capped),
                        self._settings.statement_timeout_seconds,
                    )
                except (TimeoutError, AppError, Exception) as exc:  # noqa: BLE001
                    code, message = self._classify_error(exc)
                    await self._audit_event(
                        user, session, analysis, started, success=False,
                        error_code=code, error=message,
                    )
                    outcomes.append(
                        StatementOutcome(
                            sql=stmt, success=False, returns_rows=False, columns=[], rows=[],
                            row_count=0, rows_affected=None, execution_ms=0.0, truncated=False,
                            category=analysis.category.value, destructive=analysis.destructive,
                            error_code=code, error=message,
                        )
                    )
                    overall_ok = False
                    break

                await self._audit_event(
                    user, session, analysis, started, success=True,
                    row_count=result.row_count, rows_affected=result.rows_affected,
                )
                outcomes.append(
                    StatementOutcome(
                        sql=stmt, success=True, returns_rows=result.returns_rows,
                        columns=result.columns, rows=[row_to_list(r) for r in result.rows],
                        row_count=result.row_count, rows_affected=result.rows_affected,
                        execution_ms=result.execution_ms, truncated=result.truncated,
                        category=analysis.category.value, destructive=analysis.destructive,
                        error_code=None, error=None,
                    )
                )

        return ScriptResult(statements=outcomes, success=overall_ok)

    @staticmethod
    def _classify_error(exc: Exception) -> tuple[str, str]:
        if isinstance(exc, TimeoutError):
            return "QUERY_TIMEOUT", "Statement exceeded the time limit."
        if isinstance(exc, AppError):
            return exc.code, exc.message
        return "QUERY_EXECUTION_ERROR", str(exc).strip() or exc.__class__.__name__

    # --- streaming execution -------------------------------------------------------------

    async def stream(
        self,
        *,
        user: User,
        session: LiveSession,
        sql: str,
        parameters: Mapping[str, Any] | None = None,
        batch_size: int | None = None,
        policy: AccessPolicy | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield JSON-ready event dicts: ``columns`` → ``rows``* → ``end`` (or ``error``).

        Cancellation is cooperative: if the consumer stops iterating (or the surrounding task
        is cancelled), the ``finally`` releases the session lock and the adapter's connection,
        which aborts the server-side operation.
        """
        analysis = self._authorize(user, sql, session, policy)
        size = batch_size or self._settings.stream_batch_size
        query_id = uuid.uuid4()
        started = QueryAuditEvent.now()
        total_rows = 0
        rows_affected: int | None = None

        await session.lock.acquire()
        session.touch()
        try:
            yield {"type": "accepted", "query_id": str(query_id), "category": analysis.category.value}
            async for batch in session.adapter.stream(sql, parameters, batch_size=size):
                if batch.columns is not None:
                    yield {
                        "type": "columns",
                        "query_id": str(query_id),
                        "columns": [
                            {"name": c.name, "type": c.type_name} for c in batch.columns
                        ],
                        "returns_rows": batch.returns_rows,
                    }
                if batch.rows_affected is not None:
                    rows_affected = batch.rows_affected
                if batch.rows:
                    total_rows += len(batch.rows)
                    yield {
                        "type": "rows",
                        "query_id": str(query_id),
                        "rows": [row_to_list(r) for r in batch.rows],
                    }
            yield {
                "type": "end",
                "query_id": str(query_id),
                "row_count": total_rows,
                "rows_affected": rows_affected,
                "category": analysis.category.value,
                "destructive": analysis.destructive,
            }
            await self._audit_event(
                user, session, analysis, started, success=True,
                row_count=total_rows, rows_affected=rows_affected,
            )
        except asyncio.CancelledError:
            await self._audit_event(
                user, session, analysis, started, success=False,
                error_code="QUERY_CANCELLED", error="cancelled by user",
            )
            raise
        except Exception as exc:
            await self._audit_event(
                user, session, analysis, started, success=False,
                error_code="QUERY_EXECUTION_ERROR", error=str(exc),
            )
            yield {
                "type": "error",
                "query_id": str(query_id),
                "code": "QUERY_EXECUTION_ERROR",
                "message": self._safe_error(exc),
            }
        finally:
            session.lock.release()

    # --- cancellation --------------------------------------------------------------------

    async def cancel(self, query_id: uuid.UUID, *, user_id: uuid.UUID) -> None:
        async with self._lock:
            running = self._running.get(query_id)
            if running is None or running.user_id != user_id:
                raise NotFoundError("No such running query.")
            running.task.cancel()

    async def list_running(self, *, user_id: uuid.UUID) -> list[_RunningQuery]:
        async with self._lock:
            return [q for q in self._running.values() if q.user_id == user_id]

    # --- internals -----------------------------------------------------------------------

    def _cap_rows(self, requested: int | None) -> int:
        value = requested if requested is not None else self._settings.default_max_rows
        return max(1, min(value, self._settings.max_rows_limit))

    def _register(
        self,
        query_id: uuid.UUID,
        user: User,
        session: LiveSession,
        task: asyncio.Task[Any],
        analysis: SqlAnalysis,
    ) -> None:
        self._running[query_id] = _RunningQuery(
            id=query_id,
            user_id=user.id,
            session_id=session.id,
            task=task,
            started_at=QueryAuditEvent.now(),
            category=analysis.category.value,
        )

    def _unregister(self, query_id: uuid.UUID) -> None:
        self._running.pop(query_id, None)

    async def _audit_event(
        self,
        user: User,
        session: LiveSession,
        analysis: SqlAnalysis,
        started: datetime,
        *,
        success: bool,
        row_count: int | None = None,
        rows_affected: int | None = None,
        error_code: str | None = None,
        error: str | None = None,
    ) -> None:
        finished = QueryAuditEvent.now()
        event = QueryAuditEvent(
            user_id=user.id,
            user_email=getattr(user, "email", None),
            session_id=session.id,
            connection_id=session.connection_id,
            engine=session.adapter.engine,
            statement=analysis.original_sql,
            category=analysis.category.value,
            destructive=analysis.destructive,
            success=success,
            duration_ms=(finished - started).total_seconds() * 1000,
            started_at=started,
            finished_at=finished,
            row_count=row_count,
            rows_affected=rows_affected,
            error_code=error_code,
            error_message=(error[:500] if error else None),
            request_id=get_request_id(),
        )
        try:
            await self._audit.record_query(event)
        except Exception:  # auditing must never break query handling
            logger.exception("Failed to record audit event")

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        message = str(exc).strip()
        return message or exc.__class__.__name__
