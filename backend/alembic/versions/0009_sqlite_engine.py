"""connections: add 'sqlite' to the db_engine enum

SQLite is now a supported target engine. The connections.engine column is a native PostgreSQL
ENUM, so the new value must be added to the type. ``ALTER TYPE ... ADD VALUE`` cannot run inside
a transaction block on PostgreSQL < 12 (and the value is unusable in the same transaction on
12+), so it runs in an autocommit block. Other control-plane backends store the enum as text.

Revision ID: 0009_sqlite_engine
Revises: 0008_audit_private_ip
Create Date: 2026-11-11
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0009_sqlite_engine"
down_revision: str | None = "0008_audit_private_ip"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE db_engine ADD VALUE IF NOT EXISTS 'sqlite'")


def downgrade() -> None:
    # PostgreSQL can't drop a value from an enum; refuse while SQLite connections exist so a
    # downgrade never leaves rows with an unrepresentable value.
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM connections WHERE engine = 'sqlite') THEN "
        "RAISE EXCEPTION 'Delete SQLite connections before downgrading'; END IF; END $$;"
    )
