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
import dataclasses
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.core.config import QuerySettings
from app.core.exceptions import (
    AppError,
    AuthorizationError,
    NotFoundError,
    QueryCancelledError,
    QueryExecutionError,
    QueryTimeoutError,
    ValidationError,
)
from app.core.context import get_client_ip, get_client_private_ip, get_request_id
from app.core.logging import get_logger
from app.db.adapters.base import QueryColumn
from app.models.user import User
from app.services.access_control import AccessPolicy
from app.services.sql_introspect import SqlOperation
from app.services.audit_sink import AuditSink, QueryAuditEvent
from app.services.orchestrator import LiveSession
from app.services.sql_guard import SqlAnalysis, SqlGuard
from app.utils.serialization import infer_column_kinds, row_to_list

logger = get_logger(__name__)


@dataclass
class _RunningQuery:
    id: uuid.UUID
    user_id: uuid.UUID
    session_id: uuid.UUID
    task: asyncio.Task[Any]
    started_at: datetime
    category: str
    # The session's adapter, for a driver-level (server-side) cancel.
    adapter: Any = None


# After a driver-level cancel, how long a run may take to wind down by itself (keeping the
# results it already produced) before its task is hard-cancelled.
_CANCEL_GRACE_SECONDS = 3.0


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
    query_id: uuid.UUID | None = None
    # The session's database after the run (a script's USE switches it, like SSMS).
    active_database: str | None = None
    database_changed: bool = False
    # The script left an explicit transaction open on the session.
    open_transaction: bool = False
    execution_ms: float = 0.0



def _with_kinds(columns: list[QueryColumn], rows: Sequence[Sequence[Any]]) -> list[QueryColumn]:
    """Attach the value kind of every column, inferred from the driver values (before they are
    serialized: decimals, big integers and dates all travel as strings)."""
    kinds = infer_column_kinds(rows, len(columns))
    return [dataclasses.replace(c, kind=k) if c.kind is None else c for c, k in zip(columns, kinds)]

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

    async def _enforce_script(
        self,
        user: User,
        sql: str,
        analysis: SqlAnalysis,
        session: LiveSession,
        policy: AccessPolicy | None,
    ) -> None:
        """Authorize a whole multi-statement script. With a policy (all user paths) the
        script-aware analyzer governs (temp tables/variables need no grant, dynamic SQL is
        validated). Named routines the script EXEC/CALLs are authorized by fetching and
        validating each routine's body (must be read-only over tables the caller may read).
        Without a policy (defensive fallback) the coarse role check applies."""
        if policy is None:
            self._guard.enforce(user, analysis)
            return
        routines = policy.enforce_script(
            session.adapter.engine, session.adapter.active_database, sql
        )
        for name in routines:
            await self._authorize_routine(name, session, policy)

    async def _authorize_routine(
        self,
        name: str,
        session: LiveSession,
        policy: AccessPolicy,
        _seen: set[str] | None = None,
    ) -> None:
        """Authorize EXEC/CALL of a stored routine against the caller's ACTUAL grants.

        A user may execute a routine iff they hold every permission the routine's body needs —
        exactly what they'd need to run the same statements directly. So we fetch the routine
        body, analyze the operations it performs (SELECT for reads, INSERT/UPDATE/DELETE/CREATE/
        ALTER/DROP for writes), and require a matching grant for each. A user with INSERT can run
        a procedure that inserts; a user without DROP cannot run one that drops. Routines the body
        itself calls are authorized recursively. Fail-closed if the body can't be read or its
        operations can't be statically determined. Admins never reach here (they return no
        routines). Works for SQL Server, PostgreSQL and MySQL/MariaDB (per-engine body fetch)."""
        from app.services.sql_script_analyzer import analyze_routine_definition

        seen = _seen if _seen is not None else set()
        key = name.lower()
        if key in seen:  # guard against recursive / mutually-recursive routine references
            return
        seen.add(key)

        engine = session.adapter.engine
        database = session.adapter.active_database
        get_def = getattr(session.adapter, "get_routine_definition", None)
        definition = await get_def(name) if get_def else None
        if not definition:
            raise AuthorizationError(
                f"Stored routine '{name}' could not be found or read for authorization; "
                f"execution denied.",
                code="ACCESS_DENIED",
            )
        access = analyze_routine_definition(definition, engine)
        if access.denied_reason:
            raise AuthorizationError(
                f"Stored routine '{name}' contains operations that could not be verified "
                f"for access control and cannot be executed with your access.",
                code="ACCESS_DENIED",
            )
        # Require a grant for every operation the body performs — matching the caller's actual
        # permissions rather than demanding the routine be read-only.
        for req in access.requirements:
            policy.require_grant(req.operation, database, req.table)
        # Authorize any routine this routine itself calls (recursively, cycle-guarded).
        for nested in access.routines:
            await self._authorize_routine(nested, session, policy, seen)

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
        self._invalidate_metadata_if_ddl(session, analysis)
        return ExecuteResult(
            query_id=query_id,
            columns=_with_kinds(result.columns, result.rows),
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
        query_id: uuid.UUID | None = None,
    ) -> ScriptResult:
        """Run a whole multi-statement script in ONE database session (SSMS/DataGrip style).

        The entire script runs on a single connection so temp tables, session/batch variables,
        cursors and procedural blocks keep their scope, and **every** result set is returned.
        Access control authorizes the script as a unit (temp tables/variables need no grant;
        read-only dynamic SQL is validated). The whole run is audited as one event.
        """
        if not sql or not sql.strip():
            raise ValidationError("No SQL statement to execute.")
        capped = self._cap_rows(max_rows)
        analysis = self._guard.analyze(sql)  # category/destructive for audit
        started = QueryAuditEvent.now()
        query_id = query_id or uuid.uuid4()

        async with session.lock:
            session.touch()
            task: asyncio.Task[Any] | None = None
            try:
                await self._enforce_script(user, sql, analysis, session, policy)
                # Run as a registered task so the user can cancel it (POST /queries/{id}/cancel)
                # and it shows in the running-queries list, exactly like single statements.
                task = asyncio.create_task(session.adapter.run_script(sql, max_rows=capped))
                self._register(query_id, user, session, task, analysis, script=True)
                run = await asyncio.wait_for(task, self._settings.statement_timeout_seconds)
            except (TimeoutError, asyncio.CancelledError, AppError, Exception) as exc:  # noqa: BLE001
                if isinstance(exc, (TimeoutError, asyncio.CancelledError)):
                    # Stop the work server-side (driver-level cancel) and discard the session's
                    # script connection — it may still be busy or mid-transaction.
                    await self._abort_script(session)
                    # A cancelled/timed-out DDL script may have partly applied.
                    self._invalidate_metadata_if_ddl(session, analysis)
                code, message = self._classify_error(exc)
                await self._audit_event(
                    user, session, analysis, started, success=False,
                    error_code=code, error=message,
                )
                return ScriptResult(
                    statements=[
                        StatementOutcome(
                            sql=sql, success=False, returns_rows=False, columns=[], rows=[],
                            row_count=0, rows_affected=None, execution_ms=0.0, truncated=False,
                            category=analysis.category.value, destructive=analysis.destructive,
                            error_code=code, error=message,
                        )
                    ],
                    success=False,
                    query_id=query_id,
                    active_database=session.adapter.active_database,
                )
            finally:
                self._unregister(query_id)

        self._invalidate_metadata_if_ddl(session, analysis)
        errors = run.all_errors
        total_rows = sum(len(rs.rows) for rs in run.result_sets)
        cancelled = any("cancelled by the user" in e for e in errors)
        await self._audit_event(
            user, session, analysis, started, success=not errors, row_count=total_rows,
            error_code=("QUERY_CANCELLED" if cancelled else "QUERY_EXECUTION_ERROR") if errors else None,
            error="\n".join(errors) if errors else None,
        )

        # Map each result set to a row-returning outcome and each row-count to a message
        # outcome, so the editor shows EVERY result set (SSMS-style) plus messages. If the run
        # errored mid-script, the result sets produced before the error are still returned,
        # followed by a failed outcome carrying the error.
        outcomes: list[StatementOutcome] = []
        for rs in run.result_sets:
            outcomes.append(
                StatementOutcome(
                    sql=sql, success=True, returns_rows=True,
                    columns=_with_kinds(rs.columns, rs.rows),
                    rows=[row_to_list(r) for r in rs.rows], row_count=len(rs.rows),
                    rows_affected=None, execution_ms=run.execution_ms, truncated=rs.truncated,
                    category=analysis.category.value, destructive=analysis.destructive,
                    error_code=None, error=None,
                )
            )
        for msg in run.messages:
            outcomes.append(
                StatementOutcome(
                    sql=msg, success=True, returns_rows=False, columns=[], rows=[],
                    row_count=0, rows_affected=None, execution_ms=0.0, truncated=False,
                    category=analysis.category.value, destructive=analysis.destructive,
                    error_code=None, error=None,
                )
            )
        # One failed outcome per error — for GO scripts each is attributed to its batch.
        for err in errors:
            code = (
                "QUERY_CANCELLED" if "cancelled by the user" in err else "QUERY_EXECUTION_ERROR"
            )
            outcomes.append(
                StatementOutcome(
                    sql=sql, success=False, returns_rows=False, columns=[], rows=[],
                    row_count=0, rows_affected=None, execution_ms=run.execution_ms,
                    truncated=False, category=analysis.category.value,
                    destructive=analysis.destructive,
                    error_code=code, error=err,
                )
            )
        if not outcomes:  # ran cleanly but produced no rows / row-count (e.g. pure DDL)
            outcomes.append(
                StatementOutcome(
                    sql=sql, success=True, returns_rows=False, columns=[], rows=[],
                    row_count=0, rows_affected=None, execution_ms=run.execution_ms,
                    truncated=False, category=analysis.category.value,
                    destructive=analysis.destructive, error_code=None, error=None,
                )
            )
        return ScriptResult(
            statements=outcomes,
            success=not errors,
            query_id=query_id,
            active_database=session.adapter.active_database,
            database_changed=bool(getattr(run, "database_changed_to", None)),
            open_transaction=bool(getattr(run, "open_transaction", False)),
            execution_ms=run.execution_ms,
        )

    @staticmethod
    def _invalidate_metadata_if_ddl(session: LiveSession, analysis: SqlAnalysis) -> None:
        """Structure may have changed (CREATE/ALTER/DROP/… or something unclassifiable): drop
        the connection's cached metadata so every user's explorer, diagram and IntelliSense
        see the change on their next read."""
        from app.services.metadata_cache import metadata_cache, server_identity  # local: avoid cycle
        from app.services.sql_guard import StatementCategory

        if analysis.destructive or any(
            s.category in (StatementCategory.DDL, StatementCategory.UNKNOWN)
            for s in analysis.statements
        ):
            metadata_cache.invalidate(
                session.connection_id, server=server_identity(session.adapter.config)
            )

    async def _abort_script(self, session: LiveSession) -> None:
        """Best-effort hard stop of a cancelled / timed-out script: ask the driver to cancel
        the in-flight statement, then discard the session's script connection."""
        adapter = session.adapter
        cancel = getattr(adapter, "cancel_running", None)
        if cancel is not None:
            try:
                await cancel()
            except Exception:  # noqa: BLE001
                logger.warning("Driver-level cancel failed", exc_info=True)
        release = getattr(adapter, "_release_script_connection", None)
        if release is not None:
            try:
                await release()
            except Exception:  # noqa: BLE001
                logger.warning("Releasing script connection failed", exc_info=True)

    @staticmethod
    def _classify_error(exc: BaseException) -> tuple[str, str]:
        if isinstance(exc, TimeoutError):
            return "QUERY_TIMEOUT", "Statement exceeded the time limit."
        if isinstance(exc, asyncio.CancelledError):
            return "QUERY_CANCELLED", "Query was cancelled."
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
                        # Kinds come from the first batch (columns arrive with it).
                        "columns": [
                            {"name": c.name, "type": c.type_name, "kind": c.kind}
                            for c in _with_kinds(batch.columns, batch.rows)
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

    # --- out-of-band auditing ------------------------------------------------------------

    async def audit_action(
        self,
        *,
        user: User,
        session: LiveSession,
        statement: str,
        category: str = "ddl",
        destructive: bool = True,
        success: bool,
        started: datetime,
        error_code: str | None = None,
        error: str | None = None,
    ) -> None:
        """Record an audit event for an action that doesn't go through the query path
        (e.g. CREATE DATABASE, which runs in autocommit outside the engine)."""
        finished = QueryAuditEvent.now()
        event = QueryAuditEvent(
            user_id=user.id,
            user_email=getattr(user, "email", None),
            session_id=session.id,
            connection_id=session.connection_id,
            engine=session.adapter.engine,
            statement=statement,
            category=category,
            destructive=destructive,
            success=success,
            duration_ms=(finished - started).total_seconds() * 1000,
            started_at=started,
            finished_at=finished,
            error_code=error_code,
            error_message=(error[:500] if error else None),
            request_id=get_request_id(),
            ip_address=get_client_ip(),
            ip_private=get_client_private_ip(),
        )
        try:
            await self._audit.record_query(event)
        except Exception:  # auditing must never break the action
            logger.exception("Failed to record audit event")

    # --- cancellation --------------------------------------------------------------------

    async def cancel(self, query_id: uuid.UUID, *, user_id: uuid.UUID) -> None:
        """Cancel a running query.

        The statement is cancelled **at the database first** (ODBC ``SQLCancel`` /
        ``pg_cancel_backend`` / ``KILL QUERY`` / sqlite ``interrupt``): cancelling only the
        asyncio task would leave SQL Server still executing inside the ODBC worker thread. The
        run then ends on its own — keeping any result sets produced before the cancel, like
        SSMS. If it hasn't stopped within a short grace period, the task is hard-cancelled."""
        async with self._lock:
            running = self._running.get(query_id)
            if running is None or running.user_id != user_id:
                raise NotFoundError("No such running query.")
        driver_cancel = getattr(running.adapter, "cancel_running", None)
        if driver_cancel is not None:
            try:
                await driver_cancel()
            except Exception:  # noqa: BLE001 - fall back to cancelling the task
                logger.warning("Driver-level cancel failed", exc_info=True)
            try:
                await asyncio.wait_for(asyncio.shield(running.task), _CANCEL_GRACE_SECONDS)
            except BaseException:  # noqa: BLE001 - finished with an error / still running
                pass
        if not running.task.done():
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
        *,
        script: bool = False,
    ) -> None:
        self._running[query_id] = _RunningQuery(
            id=query_id,
            user_id=user.id,
            session_id=session.id,
            task=task,
            started_at=QueryAuditEvent.now(),
            category=analysis.category.value,
            # Scripts run on the session's dedicated connection, so they can be cancelled at
            # the driver level; single statements use a pooled connection (task cancel).
            adapter=session.adapter if script else None,
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
            ip_address=get_client_ip(),
            ip_private=get_client_private_ip(),
        )
        try:
            await self._audit.record_query(event)
        except Exception:  # auditing must never break query handling
            logger.exception("Failed to record audit event")

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        message = str(exc).strip()
        return message or exc.__class__.__name__
