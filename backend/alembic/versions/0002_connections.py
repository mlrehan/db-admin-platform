"""connections table

Revision ID: 0002_connections
Revises: 0001_initial_users
Create Date: 2026-06-16

Hand-authored to stay in sync with ``app.models.connection.Connection``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_connections"
down_revision: str | None = "0001_initial_users"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Must match EngineType.value (see app.db.engines.EngineType).
_db_engine = postgresql.ENUM(
    "postgresql", "mysql", "mssql", name="db_engine", create_type=False
)


def upgrade() -> None:
    bind = op.get_bind()
    _db_engine.create(bind, checkfirst=True)

    op.create_table(
        "connections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("engine", _db_engine, nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("database", sa.String(length=255), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("encrypted_credentials", sa.Text(), nullable=False),
        sa.Column("ssl_mode", sa.String(length=32), nullable=True),
        sa.Column("options", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name="fk_connections_owner_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_connections"),
        sa.UniqueConstraint("owner_id", "name", name="uq_connections_owner_id_name"),
    )
    op.create_index("ix_connections_owner_id", "connections", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_connections_owner_id", table_name="connections")
    op.drop_table("connections")
    _db_engine.drop(op.get_bind(), checkfirst=True)
