"""Durable audit persistence and querying.

* :class:`DatabaseAuditSink` implements the :class:`AuditSink` protocol by inserting an
  :class:`AuditLog` row. It writes in its **own** session (independent of the request
  transaction) so an audit record persists even if the originating request later rolls back —
  append-only durability.
* :class:`AuditService` provides read-only, filtered access for the audit API. There is
  deliberately no update/delete path: the log is immutable.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.models.audit import AuditLog
from app.services.audit_sink import QueryAuditEvent

logger = get_logger("app.audit")


class DatabaseAuditSink:
    """Append-only sink that persists audit events to the control-plane database."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def record_query(self, event: QueryAuditEvent) -> None:
        record = AuditLog(
            user_id=event.user_id,
            user_email=event.user_email,
            connection_id=event.connection_id,
            session_id=event.session_id,
            engine=event.engine.value,
            statement=event.statement,
            category=event.category,
            destructive=event.destructive,
            success=event.success,
            duration_ms=event.duration_ms,
            row_count=event.row_count,
            rows_affected=event.rows_affected,
            error_code=event.error_code,
            error_message=event.error_message,
            request_id=event.request_id,
        )
        async with self._sessionmaker() as session:
            session.add(record)
            await session.commit()


class AuditService:
    """Read-only, filtered access to the immutable audit log."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, audit_id: uuid.UUID) -> AuditLog | None:
        return await self._session.get(AuditLog, audit_id)

    async def search(
        self,
        *,
        user_id: uuid.UUID | None = None,
        connection_id: uuid.UUID | None = None,
        category: str | None = None,
        success: bool | None = None,
        destructive: bool | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditLog]:
        stmt = select(AuditLog)
        if user_id is not None:
            stmt = stmt.where(AuditLog.user_id == user_id)
        if connection_id is not None:
            stmt = stmt.where(AuditLog.connection_id == connection_id)
        if category is not None:
            stmt = stmt.where(AuditLog.category == category)
        if success is not None:
            stmt = stmt.where(AuditLog.success == success)
        if destructive is not None:
            stmt = stmt.where(AuditLog.destructive == destructive)
        if since is not None:
            stmt = stmt.where(AuditLog.created_at >= since)
        if until is not None:
            stmt = stmt.where(AuditLog.created_at <= until)
        stmt = stmt.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


def build_database_audit_sink(
    sessionmaker_provider: Callable[[], async_sessionmaker[AsyncSession]],
) -> DatabaseAuditSink:
    """Construct a sink bound to the control-plane session factory (resolved lazily)."""
    return DatabaseAuditSink(sessionmaker_provider())
