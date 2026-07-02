"""audit_logs: add ip_private column and allow admin deletion

Adds ``ip_private`` (the client's LAN address, reported via the X-Private-IP header) alongside
the existing ``ip_address`` (public/edge IP). Also drops the append-only DELETE rule so admins
can delete audit records through the authorized API path. The UPDATE rule is kept — records
still cannot be modified, only removed by an administrator.

Revision ID: 0008_audit_private_ip
Revises: 0007_audit_ip_address
Create Date: 2026-07-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_audit_private_ip"
down_revision: str | None = "0007_audit_ip_address"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("audit_logs", sa.Column("ip_private", sa.String(length=64), nullable=True))
    # Allow admin-initiated deletion (via the authorized audit API). Immutability of existing
    # rows is still guaranteed by the retained no-update rule.
    op.execute("DROP RULE IF EXISTS audit_logs_no_delete ON audit_logs")


def downgrade() -> None:
    op.execute(
        "CREATE RULE audit_logs_no_delete AS ON DELETE TO audit_logs DO INSTEAD NOTHING"
    )
    op.drop_column("audit_logs", "ip_private")
