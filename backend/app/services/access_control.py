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

import time
import uuid
from dataclasses import dataclass

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.roles import Role
from app.core.exceptions import AuthorizationError, NotFoundError
from app.db.session import release_connection
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


def _normalize_scope(values) -> tuple[str, ...]:
    """Clean a scope list: drop blanks and the '*' wildcard (which means "any" → empty)."""
    out: list[str] = []
    for v in values or []:
        s = (str(v) or "").strip()
        if s and s != "*":
            out.append(s)
    return tuple(out)


def _list_match(grant_values: tuple[str, ...], actual: str | None) -> bool:
    """A scope dimension matches if the grant lists nothing (any) or contains ``actual``."""
    if not grant_values:
        return True
    if actual is None:
        return False
    al = actual.lower()
    return any(v.lower() == al for v in grant_values)


@dataclass(frozen=True)
class GrantSpec:
    operations: frozenset[SqlOperation]
    databases: tuple[str, ...]  # empty = any database
    tables: tuple[str, ...]  # empty = any table
    table_schema: str | None = None

    def scope_covers(self, database: str | None, schema: str | None, table: str | None) -> bool:
        if not _list_match(self.databases, database):
            return False
        if not _list_match(self.tables, table):
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
        # Prefer the JSON arrays; fall back to the legacy scalar columns for old rows.
        raw_dbs = grant.databases if grant.databases is not None else (
            [grant.database] if grant.database else []
        )
        raw_tbls = grant.tables if grant.tables is not None else (
            [grant.table_name] if grant.table_name else []
        )
        return cls(
            operations=ops,
            databases=_normalize_scope(raw_dbs),
            tables=_normalize_scope(raw_tbls),
            table_schema=grant.table_schema,
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
        return any(_list_match(g.databases, database) for g in self.grants)

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
            SqlOperation.CREATE in g.operations and not g.databases and not g.tables
            for g in self.grants
        )

    def table_visible(self, database: str | None, schema: str | None, table: str) -> bool:
        if self.is_admin:
            return True
        if not self.has_grants:
            return False
        return any(g.scope_covers(database, schema, table) for g in self.grants)

    def enforce_script(self, engine: EngineType, database: str | None, sql: str) -> list[str]:
        """Authorize a whole multi-statement script (temp tables, variables, procedural blocks,
        read-only dynamic SQL) as a unit. Admins bypass; non-admins are default-deny.

        Session-local objects (#temp / @var / CREATE TEMP) need no grant; every real-table
        access the script performs must be covered by a grant. Read-only dynamic SQL is allowed
        only when it can be statically proven safe.

        Returns the names of any **named routines** the script EXEC/CALLs — those can't be
        validated here (they need catalog access), so the query engine authorizes each routine's
        body separately. Admins return an empty list."""
        if self.is_admin:
            return []
        if not self.has_grants:
            raise AuthorizationError(
                "You have not been granted access to run queries on this connection.",
                code="ACCESS_DENIED",
            )
        # Imported here to avoid a module import cycle.
        from app.services.sql_script_analyzer import analyze_script_access

        access = analyze_script_access(sql, engine)
        if access.denied_reason:
            raise AuthorizationError(access.denied_reason, code="ACCESS_DENIED")
        for req in access.requirements:
            self.require_grant(req.operation, database, req.table)
        return list(access.routines)

    def require_grant(self, operation: SqlOperation, database: str | None, table) -> None:
        """Raise ACCESS_DENIED unless some grant covers (operation, database, table)."""
        schema = table.schema if table else None
        name = table.name if table else None
        if not any(g.covers(operation, database, schema, name) for g in self.grants):
            op = operation.value
            where = f" on table '{name}'" if name else ""
            raise AuthorizationError(
                f"You don't have permission to run {op} statements{where}. "
                f"Ask an administrator to grant you {op} access on this database.",
                code="ACCESS_DENIED",
                details={"operation": op, "database": database, "table": name},
            )

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
        from app.services.sql_batches import strip_go_lines  # local: avoid cycle

        try:
            statements = extract_access(strip_go_lines(sql), engine)
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


# --- grant cache -------------------------------------------------------------------------
#
# Every query/schema request needs the caller's grants for the connection. Re-reading them from
# the control DB on each request costs a pool checkout + round trips + ORM work, which caps the
# single-worker API's throughput under many concurrent users. They're cached briefly per
# (user, role, connection). Grant changes made through the app invalidate the cache at once, so
# revoking access takes effect immediately; the TTL only bounds staleness for changes made
# directly in the database.

_GRANT_TTL_SECONDS = 10.0
_GRANT_CACHE_MAX = 20_000
_grant_cache: dict[tuple, tuple[float, tuple[GrantSpec, ...]]] = {}


def invalidate_grant_cache() -> None:
    """Forget every cached grant set (call after any grant / user / connection change)."""
    _grant_cache.clear()


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
        grants = list(result.scalars().all())
        # Permission checks are read-only and usually precede slow target-database work:
        # return the control-plane connection to the pool now (no-op if the request wrote).
        await release_connection(self._session)
        return grants

    async def _grant_specs(self, user: User, connection_id: uuid.UUID) -> tuple[GrantSpec, ...]:
        """The caller's grants for a connection, briefly cached (see ``invalidate_grant_cache``)."""
        key = (user.id, Role(user.role).value, connection_id)
        now = time.monotonic()
        hit = _grant_cache.get(key)
        if hit is not None and hit[0] > now:
            return hit[1]
        specs = tuple(GrantSpec.from_model(g) for g in await self._grants_for(user, connection_id))
        if len(_grant_cache) >= _GRANT_CACHE_MAX:
            _grant_cache.clear()
        _grant_cache[key] = (now + _GRANT_TTL_SECONDS, specs)
        return specs

    async def can_access_connection(self, user: User, connection_id: uuid.UUID) -> bool:
        """Whether a (non-owner) user may *use* a connection because a grant gives them
        access to it. Owners and admins are handled by the caller."""
        if Role(user.role) == Role.ADMIN:
            return True
        return bool(await self._grant_specs(user, connection_id))

    async def granted_databases(self, user: User, connection_id: uuid.UUID) -> set[str]:
        """The distinct, specific databases a non-admin is granted on a connection.

        Grants with no database scope (wildcard) contribute nothing here. Admins return an
        empty set (they are not constrained to any database)."""
        if Role(user.role) == Role.ADMIN:
            return set()
        specs = await self._grant_specs(user, connection_id)
        return {db for g in specs for db in g.databases}

    async def granted_connection_ids(self, user: User) -> set[uuid.UUID]:
        """Connection ids the user has any grant on (i.e. connections shared with them)."""
        result = await self._session.execute(
            select(AccessGrant.connection_id).where(self._subject_filter(user)).distinct()
        )
        ids = {row[0] for row in result.all()}
        await release_connection(self._session)
        return ids

    async def policy_for(self, user: User, connection_id: uuid.UUID) -> AccessPolicy:
        if Role(user.role) == Role.ADMIN:
            return AccessPolicy(is_admin=True, has_grants=False, grants=())
        specs = await self._grant_specs(user, connection_id)
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
        databases: list[str] | None = None,
        tables: list[str] | None = None,
        database: str | None = None,
        table_schema: str | None = None,
        table_name: str | None = None,
    ) -> AccessGrant:
        dbs = _normalize_scope(
            databases if databases is not None else ([database] if database else [])
        )
        tbls = _normalize_scope(
            tables if tables is not None else ([table_name] if table_name else [])
        )
        grant = AccessGrant(
            subject_type=subject_type,
            subject_id=subject_id,
            connection_id=connection_id,
            databases=list(dbs) or None,
            tables=list(tbls) or None,
            table_schema=table_schema or None,
            operations=operations,
        )
        self._session.add(grant)
        await self._session.flush()
        invalidate_grant_cache()
        return grant

    async def get_grant(self, grant_id: uuid.UUID) -> AccessGrant | None:
        return await self._session.get(AccessGrant, grant_id)

    async def update_grant(
        self,
        grant_id: uuid.UUID,
        *,
        operations: list[str] | None = None,
        databases: list[str] | None = None,
        tables: list[str] | None = None,
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
        # When the form submits scope fields, an empty selection means "any".
        if clear_scope:
            dbs = _normalize_scope(
                databases if databases is not None else ([database] if database else [])
            )
            tbls = _normalize_scope(
                tables if tables is not None else ([table_name] if table_name else [])
            )
            grant.databases = list(dbs) or None
            grant.tables = list(tbls) or None
            grant.table_schema = table_schema or None
            # Clear legacy scalar columns; the JSON arrays are authoritative for new writes.
            grant.database = None
            grant.table_name = None
        await self._session.flush()
        invalidate_grant_cache()
        return grant

    async def delete_grant(self, grant_id: uuid.UUID) -> None:
        result = await self._session.execute(
            delete(AccessGrant).where(AccessGrant.id == grant_id)
        )
        invalidate_grant_cache()
        if result.rowcount == 0:
            raise NotFoundError("Access grant not found.")
