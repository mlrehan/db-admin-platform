"""access_grants table (granular RBAC)

Revision ID: 0005_access_grants
Revises: 0004_connection_database_optional
Create Date: 2026-06-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_access_grants"
down_revision: str | None = "0004_connection_database"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "access_grants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject_type", sa.String(length=8), nullable=False),
        sa.Column("subject_id", sa.String(length=64), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("database", sa.String(length=255), nullable=True),
        sa.Column("table_schema", sa.String(length=255), nullable=True),
        sa.Column("table_name", sa.String(length=255), nullable=True),
        sa.Column("operations", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"], ["connections.id"],
            name="fk_access_grants_connection_id_connections", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_access_grants"),
        sa.UniqueConstraint(
            "subject_type", "subject_id", "connection_id", "database",
            "table_schema", "table_name", name="uq_access_grants_subject_scope",
        ),
    )
    op.create_index("ix_access_grants_subject_type", "access_grants", ["subject_type"])
    op.create_index("ix_access_grants_subject_id", "access_grants", ["subject_id"])
    op.create_index("ix_access_grants_connection_id", "access_grants", ["connection_id"])


def downgrade() -> None:
    op.drop_index("ix_access_grants_connection_id", table_name="access_grants")
    op.drop_index("ix_access_grants_subject_id", table_name="access_grants")
    op.drop_index("ix_access_grants_subject_type", table_name="access_grants")
    op.drop_table("access_grants")
