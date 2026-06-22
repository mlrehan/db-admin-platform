"""make connections.database nullable (server-level connections)

Revision ID: 0004_connection_database_optional
Revises: 0003_audit_logs
Create Date: 2026-06-17

A NULL database means a server-level connection: the user can browse and select any database
on the server.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_connection_database"
down_revision: str | None = "0003_audit_logs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "connections",
        "database",
        existing_type=sa.String(length=255),
        nullable=True,
    )


def downgrade() -> None:
    # Backfill any NULLs before restoring NOT NULL.
    op.execute("UPDATE connections SET database = '' WHERE database IS NULL")
    op.alter_column(
        "connections",
        "database",
        existing_type=sa.String(length=255),
        nullable=False,
    )
