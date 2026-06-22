"""Audit sink abstraction.

Every query execution emits a :class:`QueryAuditEvent`. The Query Engine depends only on the
:class:`AuditSink` protocol, so Phase 6 can substitute a durable, append-only database sink
for the :class:`LoggingAuditSink` used here — without touching the engine.

The event captures exactly what the spec mandates for every query: user identity, timestamp,
target database, query text, and execution duration (plus outcome metadata).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from app.core.logging import get_logger
from app.db.engines import EngineType

logger = get_logger("app.audit")


@dataclass(frozen=True)
class QueryAuditEvent:
    user_id: uuid.UUID
    session_id: uuid.UUID
    connection_id: uuid.UUID
    engine: EngineType
    statement: str
    category: str
    destructive: bool
    success: bool
    duration_ms: float
    started_at: datetime
    finished_at: datetime
    user_email: str | None = None
    row_count: int | None = None
    rows_affected: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    request_id: str | None = None

    @staticmethod
    def now() -> datetime:
        return datetime.now(tz=timezone.utc)


@runtime_checkable
class AuditSink(Protocol):
    async def record_query(self, event: QueryAuditEvent) -> None: ...


class LoggingAuditSink:
    """Writes audit events to the structured application log.

    A real append-only persistence sink replaces this in Phase 6; the interface is identical.
    """

    async def record_query(self, event: QueryAuditEvent) -> None:
        logger.info(
            "query.audit",
            extra={
                "audit": True,
                "user_id": str(event.user_id),
                "session_id": str(event.session_id),
                "connection_id": str(event.connection_id),
                "engine": event.engine.value,
                "category": event.category,
                "destructive": event.destructive,
                "success": event.success,
                "duration_ms": round(event.duration_ms, 3),
                "row_count": event.row_count,
                "rows_affected": event.rows_affected,
                "error_code": event.error_code,
                # Statement text is truncated in the log line; the durable sink keeps it whole.
                "statement_preview": event.statement[:500],
            },
        )
