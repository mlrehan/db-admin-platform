"""access_grants: multi-database/table scope (JSON arrays)

Adds ``databases`` and ``tables`` JSON array columns so a single grant can cover multiple
databases and/or tables, and drops the scalar-scope unique constraint (which would otherwise
block multiple array-based grants for the same subject+connection). The legacy scalar columns
(``database``/``table_name``) are kept so pre-existing rows keep working.

Revision ID: 0006_grant_multi_scope
Revises: 0005_access_grants
Create Date: 2026-06-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_grant_multi_scope"
down_revision: str | None = "0005_access_grants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("access_grants", sa.Column("databases", sa.JSON(), nullable=True))
    op.add_column("access_grants", sa.Column("tables", sa.JSON(), nullable=True))
    # Drop the scalar-scope uniqueness; array-based grants make it meaningless (and it would
    # block multiple grants whose scalar scope is all-NULL).
    op.drop_constraint("uq_access_grants_subject_scope", "access_grants", type_="unique")


def downgrade() -> None:
    op.create_unique_constraint(
        "uq_access_grants_subject_scope",
        "access_grants",
        ["subject_type", "subject_id", "connection_id", "database", "table_schema", "table_name"],
    )
    op.drop_column("access_grants", "tables")
    op.drop_column("access_grants", "databases")
