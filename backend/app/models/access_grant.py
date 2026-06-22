"""Access-grant ORM model — granular RBAC.

A grant authorizes a **subject** (a specific user, or a role) to perform a set of SQL
**operations** on a scope defined by (connection, database, schema, table). ``NULL`` in a
scope field means "any":

* ``database = NULL``   → any database on the connection
* ``table_name = NULL`` → any table (database-level grant)

Enforcement is allow-only and default-deny *for non-admin subjects that have at least one
grant*: a request must be covered by some grant. Admins bypass grants; subjects with no
grants fall back to the coarse role-based permissions (preserving prior behaviour).
"""

from __future__ import annotations

import uuid

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class AccessGrant(Base, TimestampMixin):
    __tablename__ = "access_grants"
    __table_args__ = (
        UniqueConstraint(
            "subject_type",
            "subject_id",
            "connection_id",
            "database",
            "table_schema",
            "table_name",
            name="uq_access_grants_subject_scope",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)

    # Subject: "user" → subject_id is a user UUID; "role" → subject_id is a role name.
    subject_type: Mapped[str] = mapped_column(String(8), index=True, nullable=False)
    subject_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)

    # Scope.
    connection_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey("connections.id", ondelete="CASCADE"), index=True, nullable=False
    )
    database: Mapped[str | None] = mapped_column(String(255), nullable=True)
    table_schema: Mapped[str | None] = mapped_column(String(255), nullable=True)
    table_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Allowed operations: a JSON array of SqlOperation values, e.g. ["SELECT", "INSERT"].
    operations: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<AccessGrant {self.subject_type}:{self.subject_id} "
            f"db={self.database} table={self.table_name} ops={self.operations}>"
        )
