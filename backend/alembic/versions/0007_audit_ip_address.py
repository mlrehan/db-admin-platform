"""audit_logs: add ip_address column

Best-effort client IP captured per request (honours X-Forwarded-For from the trusted proxy).

Revision ID: 0007_audit_ip_address
Revises: 0006_grant_multi_scope
Create Date: 2026-06-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_audit_ip_address"
down_revision: str | None = "0006_grant_multi_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("audit_logs", sa.Column("ip_address", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_logs", "ip_address")
