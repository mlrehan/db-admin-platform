"""Saved connection ORM model (control plane).

A *saved connection* is a reusable, owner-scoped definition of how to reach a target
database. The credential is stored **only** as an AES-256-GCM envelope-encrypted blob in
``encrypted_credentials`` — never in plaintext, and never returned to clients.

Dialect-agnostic column types (``Uuid``, generic ``Enum``, ``JSON``) keep the model working
against PostgreSQL in production and SQLite in tests.
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    JSON,
    Boolean,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.db.engines import EngineType


class Connection(Base, TimestampMixin):
    __tablename__ = "connections"
    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="uq_connections_owner_id_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)

    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    name: Mapped[str] = mapped_column(String(128), nullable=False)

    engine: Mapped[EngineType] = mapped_column(
        Enum(
            EngineType,
            name="db_engine",
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
    )

    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    # Nullable: NULL means a server-level connection (no specific database).
    database: Mapped[str | None] = mapped_column(String(255), nullable=True)
    username: Mapped[str] = mapped_column(String(255), nullable=False)

    # AES-256-GCM envelope-encrypted password blob (see app.security.encryption).
    encrypted_credentials: Mapped[str] = mapped_column(Text, nullable=False)

    ssl_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Extra driver options (e.g. schema search_path, application_name).
    options: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<Connection id={self.id} name={self.name!r} "
            f"engine={self.engine.value} host={self.host}>"
        )
