"""Unit tests for the access-control policy logic."""

from __future__ import annotations

import pytest

from app.core.exceptions import AuthorizationError
from app.db.engines import EngineType
from app.services.access_control import AccessPolicy, GrantSpec
from app.services.sql_introspect import SqlOperation

PG = EngineType.POSTGRESQL


def _policy(*grants: GrantSpec, admin: bool = False) -> AccessPolicy:
    return AccessPolicy(is_admin=admin, has_grants=bool(grants), grants=tuple(grants))


def test_admin_bypasses_everything() -> None:
    policy = _policy(admin=True)
    policy.enforce_query(PG, "appdb", "DROP TABLE anything")  # no raise
    assert policy.database_allowed("whatever")
    assert policy.table_visible("db", "public", "secret")


def test_no_grants_is_default_deny() -> None:
    # A non-admin with no grants is denied everything (admin-controlled, default-deny model).
    policy = _policy()
    with pytest.raises(AuthorizationError) as exc:
        policy.enforce_query(PG, "appdb", "SELECT 1")
    assert exc.value.code == "ACCESS_DENIED"
    assert not policy.database_allowed("appdb")
    assert not policy.table_visible("appdb", "public", "users")


def test_grant_allows_matching_operation_and_table() -> None:
    grant = GrantSpec(
        operations=frozenset({SqlOperation.SELECT}),
        database="appdb", table_schema=None, table_name="users",
    )
    policy = _policy(grant)
    policy.enforce_query(PG, "appdb", "SELECT * FROM users")  # allowed


def test_grant_denies_other_table() -> None:
    grant = GrantSpec(frozenset({SqlOperation.SELECT}), "appdb", None, "users")
    policy = _policy(grant)
    with pytest.raises(AuthorizationError) as exc:
        policy.enforce_query(PG, "appdb", "SELECT * FROM orders")
    assert exc.value.code == "ACCESS_DENIED"


def test_grant_denies_other_operation() -> None:
    grant = GrantSpec(frozenset({SqlOperation.SELECT}), "appdb", None, "users")
    policy = _policy(grant)
    with pytest.raises(AuthorizationError):
        policy.enforce_query(PG, "appdb", "DELETE FROM users")


def test_grant_denies_other_database() -> None:
    grant = GrantSpec(frozenset({SqlOperation.SELECT}), "appdb", None, "users")
    policy = _policy(grant)
    with pytest.raises(AuthorizationError):
        policy.enforce_query(PG, "otherdb", "SELECT * FROM users")


def test_wildcard_table_grant() -> None:
    grant = GrantSpec(frozenset({SqlOperation.SELECT}), "appdb", None, None)  # any table
    policy = _policy(grant)
    policy.enforce_query(PG, "appdb", "SELECT * FROM users")
    policy.enforce_query(PG, "appdb", "SELECT * FROM orders")


def test_unparseable_query_denied_when_grants_present() -> None:
    grant = GrantSpec(frozenset({SqlOperation.SELECT}), None, None, None)
    policy = _policy(grant)
    with pytest.raises(AuthorizationError):
        policy.enforce_query(PG, "appdb", "this is not ;; valid sql ((")


def test_database_allowed_and_table_visible() -> None:
    grant = GrantSpec(frozenset({SqlOperation.SELECT}), "appdb", None, "users")
    policy = _policy(grant)
    assert policy.database_allowed("appdb")
    assert not policy.database_allowed("otherdb")
    assert policy.table_visible("appdb", "public", "users")
    assert not policy.table_visible("appdb", "public", "orders")


# --- can_create_database (database creation authorization) -------------------------------


def test_admin_can_create_database() -> None:
    assert _policy(admin=True).can_create_database()


def test_no_grants_cannot_create_database() -> None:
    assert not _policy().can_create_database()


def test_connection_scope_create_grant_allows_create_database() -> None:
    # Broad CREATE on the whole connection (no db/table scope) → may create databases.
    grant = GrantSpec(frozenset({SqlOperation.CREATE}), None, None, None)
    assert _policy(grant).can_create_database()


def test_narrow_create_grant_does_not_allow_create_database() -> None:
    # CREATE scoped to a single database/table is NOT server-level create-database rights.
    scoped_db = GrantSpec(frozenset({SqlOperation.CREATE}), "appdb", None, None)
    assert not _policy(scoped_db).can_create_database()
    scoped_table = GrantSpec(frozenset({SqlOperation.CREATE}), None, None, "users")
    assert not _policy(scoped_table).can_create_database()


def test_select_grant_does_not_allow_create_database() -> None:
    grant = GrantSpec(frozenset({SqlOperation.SELECT}), None, None, None)
    assert not _policy(grant).can_create_database()
