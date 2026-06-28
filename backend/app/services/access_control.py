"""Granular access control — database/table/operation enforcement.

Builds an :class:`AccessPolicy` for a (user, connection) pair from stored
:class:`~app.models.access_grant.AccessGrant` rows and enforces it:

* **Admins** bypass grants (superuser).
* A non-admin **with no grants** falls back to the coarse role-based permissions (so existing
  behaviour is preserved until an admin configures grants).
* A non-admin **with grants** is default-deny: every (operation, table) a query touches, and
  every database/table they browse, must be covered by at least one grant.

Enforcement is performed at the API/service layer — never only in the UI.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.roles import Role
from app.core.exceptions import AuthorizationError, NotFoundError
from app.db.engines import EngineType
from app.models.access_grant import AccessGrant
from app.models.user import User
from app.services.sql_introspect import (
    SqlOperation,
    SqlParseError,
    extract_access,
)


def _ci_eq(a: str | None, b: str | None) -> bool:
    return (a or "").lower() == (b or "").lower()


def _scope_match(grant_value: str | None, actual: str | None) -> bool:
    """A scope field matches if the grant is wildcard (None/'*') or equals the actual value."""
    if grant_value in (None, "*"):
        return True
    if actual is None:
        return False
    return _ci_eq(grant_value, actual)


@dataclass(frozen=True)
class GrantSpec:
    operations: frozenset[SqlOperation]
    database: str | None
    table_schema: str | None
    table_name: str | None

    def scope_covers(self, database: str | None, schema: str | None, table: str | None) -> bool:
        if not _scope_match(self.database, database):
            return False
        if not _scope_match(self.table_name, table):
            return False
        # Only constrain schema when both grant and query specify it.
        if self.table_schema and schema and not _ci_eq(self.table_schema, schema):
            return False
        return True

    def covers(
        self, operation: SqlOperation, database: str | None, schema: str | None, table: str | None
    ) -> bool:
        return operation in self.operations and self.scope_covers(database, schema, table)

    @classmethod
    def from_model(cls, grant: AccessGrant) -> GrantSpec:
        ops = frozenset(
            SqlOperation(o) for o in (grant.operations or []) if o in SqlOperation._value2member_map_
        )
        return cls(
            operations=ops,
            database=grant.database,
            table_schema=grant.table_schema,
            table_name=grant.table_name,
        )


@dataclass(frozen=True)
class AccessPolicy:
    is_admin: bool
    has_grants: bool
    grants: tuple[GrantSpec, ...]

    def database_allowed(self, database: str | None) -> bool:
        # Admin: everything. Non-admin: only what a grant covers (default-deny — no grants
        # means no access at all).
        if self.is_admin:
            return True
        if not self.has_grants:
            return False
        return any(_scope_match(g.database, database) for g in self.grants)

    def can_create_database(self) -> bool:
        """Whether the subject may create a new database on the connection.

        Admins always may. A non-admin may only if an admin granted them the ``CREATE``
        operation at *connection scope* (no database/table restriction) — i.e. broad create
        rights on the whole server, not a single-table CREATE grant. Default-deny otherwise.
        """
        if self.is_admin:
            return True
        if not self.has_grants:
            return False
        return any(
            SqlOperation.CREATE in g.operations
            and g.database in (None, "*")
            and g.table_schema in (None, "*")
            and g.table_name in (None, "*")
            for g in self.grants
        )

    def table_visible(self, database: str | None, schema: str | None, table: str) -> bool:
        if self.is_admin:
            return True
        if not self.has_grants:
            return False
        return any(g.scope_covers(database, schema, table) for g in self.grants)

    def enforce_query(self, engine: EngineType, database: str | None, sql: str) -> None:
        """Raise :class:`AuthorizationError` if ``sql`` touches anything not granted.

        Admins bypass; non-admins are default-deny (no grants → nothing allowed). The grant set
        is the single source of truth — a non-admin's role does not widen or narrow it."""
        if self.is_admin:
            return
        if not self.has_grants:
            raise AuthorizationError(
                "You have not been granted access to run queries on this connection.",
                code="ACCESS_DENIED",
            )
        try:
            statements = extract_access(sql, engine)
        except SqlParseError as exc:
            raise AuthorizationError(
                "Query could not be analyzed for access control and was denied.",
                code="ACCESS_DENIED",
            ) from exc

        for st in statements:
            targets = st.tables or [None]
            for target in targets:
                schema = target.schema if target else None
                table = target.name if target else None
                if not any(g.covers(st.operation, database, schema, table) for g in self.grants):
                    op = st.operation.value
                    where = f" on table '{table}'" if table else ""
                    raise AuthorizationError(
                        f"You don't have permission to run {op} statements{where}. "
                        f"Ask an administrator to grant you {op} access on this database.",
                        code="ACCESS_DENIED",
                        details={
                            "operation": op,
                            "database": database,
                            "table": table,
                        },
                    )


class AccessControlService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- policy ---------------------------------------------------------------------------

    def _subject_filter(self, user: User):
        return or_(
            and_(
                AccessGrant.subject_type == "user",
                AccessGrant.subject_id == str(user.id),
            ),
            and_(
                AccessGrant.subject_type == "role",
                AccessGrant.subject_id == Role(user.role).value,
            ),
        )

    async def _grants_for(self, user: User, connection_id: uuid.UUID) -> list[AccessGrant]:
        result = await self._session.execute(
            select(AccessGrant).where(
                AccessGrant.connection_id == connection_id,
                self._subject_filter(user),
            )
        )
        return list(result.scalars().all())

    async def can_access_connection(self, user: User, connection_id: uuid.UUID) -> bool:
        """Whether a (non-owner) user may *use* a connection because a grant gives them
        access to it. Owners and admins are handled by the caller."""
        if Role(user.role) == Role.ADMIN:
            return True
        return bool(await self._grants_for(user, connection_id))

    async def granted_databases(self, user: User, connection_id: uuid.UUID) -> set[str]:
        """The distinct, specific databases a non-admin is granted on a connection.

        Grants with no database scope (wildcard) contribute nothing here. Admins return an
        empty set (they are not constrained to any database)."""
        if Role(user.role) == Role.ADMIN:
            return set()
        grants = await self._grants_for(user, connection_id)
        return {g.database for g in grants if g.database and g.database != "*"}

    async def granted_connection_ids(self, user: User) -> set[uuid.UUID]:
        """Connection ids the user has any grant on (i.e. connections shared with them)."""
        result = await self._session.execute(
            select(AccessGrant.connection_id).where(self._subject_filter(user)).distinct()
        )
        return {row[0] for row in result.all()}

    async def policy_for(self, user: User, connection_id: uuid.UUID) -> AccessPolicy:
        if Role(user.role) == Role.ADMIN:
            return AccessPolicy(is_admin=True, has_grants=False, grants=())
        grants = await self._grants_for(user, connection_id)
        specs = tuple(GrantSpec.from_model(g) for g in grants)
        return AccessPolicy(is_admin=False, has_grants=bool(specs), grants=specs)

    # --- CRUD (admin) ---------------------------------------------------------------------

    async def list_grants(self, *, connection_id: uuid.UUID | None = None) -> list[AccessGrant]:
        stmt = select(AccessGrant)
        if connection_id is not None:
            stmt = stmt.where(AccessGrant.connection_id == connection_id)
        stmt = stmt.order_by(AccessGrant.created_at.desc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create_grant(
        self,
        *,
        subject_type: str,
        subject_id: str,
        connection_id: uuid.UUID,
        operations: list[str],
        database: str | None = None,
        table_schema: str | None = None,
        table_name: str | None = None,
    ) -> AccessGrant:
        grant = AccessGrant(
            subject_type=subject_type,
            subject_id=subject_id,
            connection_id=connection_id,
            database=database or None,
            table_schema=table_schema or None,
            table_name=table_name or None,
            operations=operations,
        )
        self._session.add(grant)
        await self._session.flush()
        return grant

    async def get_grant(self, grant_id: uuid.UUID) -> AccessGrant | None:
        return await self._session.get(AccessGrant, grant_id)

    async def update_grant(
        self,
        grant_id: uuid.UUID,
        *,
        operations: list[str] | None = None,
        database: str | None = None,
        table_schema: str | None = None,
        table_name: str | None = None,
        clear_scope: bool = False,
    ) -> AccessGrant:
        grant = await self.get_grant(grant_id)
        if grant is None:
            raise NotFoundError("Access grant not found.")
        if operations is not None:
            grant.operations = operations
        # When the form submits scope fields, blanks mean "any" (None).
        if clear_scope:
            grant.database = database or None
            grant.table_schema = table_schema or None
            grant.table_name = table_name or None
        await self._session.flush()
        return grant

    async def delete_grant(self, grant_id: uuid.UUID) -> None:
        result = await self._session.execute(
            delete(AccessGrant).where(AccessGrant.id == grant_id)
        )
        if result.rowcount == 0:
            raise NotFoundError("Access grant not found.")
