"""Immutable audit log ORM model (control plane).

Records every query execution. The table is **append-only**: the application never issues
``UPDATE``/``DELETE`` against it, and the PostgreSQL migration installs rules that block those
operations at the database level (defence in depth).

Identity is denormalized (``user_email`` stored alongside ``user_id``, no foreign key) so the
audit trail survives deletion of the referenced user — an audit record must outlive the
entities it describes.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)

    # Identity (denormalized; intentionally no FK so logs are retained after user deletion).
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(), index=True, nullable=True)
    user_email: Mapped[str | None] = mapped_column(String(320), nullable=True)

    # Target.
    connection_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(), index=True, nullable=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(), nullable=True)
    engine: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Operation.
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(String(16), index=True, nullable=True)
    destructive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Outcome.
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, index=True)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rows_affected: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Correlation + ordering.
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True, nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AuditLog id={self.id} user={self.user_email!r} category={self.category}>"
