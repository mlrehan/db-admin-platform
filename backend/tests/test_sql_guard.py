"""Phase 5 — SQL safety layer tests."""

from __future__ import annotations

import pytest

from app.auth.roles import Permission
from app.core.exceptions import AuthorizationError, ValidationError
from app.services.sql_guard import SqlGuard, StatementCategory, split_statements


@pytest.fixture
def guard() -> SqlGuard:
    return SqlGuard()


@pytest.mark.parametrize(
    "sql,category,perm",
    [
        ("SELECT * FROM users", StatementCategory.READ, Permission.QUERY_READ),
        ("  select 1  ", StatementCategory.READ, Permission.QUERY_READ),
        ("SHOW TABLES", StatementCategory.READ, Permission.QUERY_READ),
        ("EXPLAIN SELECT 1", StatementCategory.READ, Permission.QUERY_READ),
        ("INSERT INTO t VALUES (1)", StatementCategory.WRITE, Permission.QUERY_WRITE),
        ("UPDATE t SET x=1", StatementCategory.WRITE, Permission.QUERY_WRITE),
        ("DELETE FROM t", StatementCategory.WRITE, Permission.QUERY_WRITE),
        ("DROP TABLE t", StatementCategory.DDL, Permission.QUERY_DESTRUCTIVE),
        ("TRUNCATE t", StatementCategory.DDL, Permission.QUERY_DESTRUCTIVE),
        ("ALTER TABLE t ADD c int", StatementCategory.DDL, Permission.QUERY_DESTRUCTIVE),
        ("CREATE TABLE t (id int)", StatementCategory.DDL, Permission.QUERY_DESTRUCTIVE),
        ("GRANT SELECT ON t TO r", StatementCategory.DDL, Permission.QUERY_DESTRUCTIVE),
        ("FLARP something weird", StatementCategory.UNKNOWN, Permission.QUERY_DESTRUCTIVE),
    ],
)
def test_classification(guard: SqlGuard, sql, category, perm) -> None:
    analysis = guard.analyze(sql)
    assert analysis.category == category
    assert analysis.required_permission == perm


def test_destructive_flag(guard: SqlGuard) -> None:
    assert guard.analyze("DROP TABLE t").destructive is True
    assert guard.analyze("TRUNCATE t").destructive is True
    assert guard.analyze("ALTER TABLE t ADD c int").destructive is True
    assert guard.analyze("SELECT 1").destructive is False
    assert guard.analyze("INSERT INTO t VALUES (1)").destructive is False


def test_cte_with_select_is_read(guard: SqlGuard) -> None:
    sql = "WITH x AS (SELECT 1) SELECT * FROM x"
    assert guard.analyze(sql).category == StatementCategory.READ


def test_cte_with_delete_is_write(guard: SqlGuard) -> None:
    sql = "WITH x AS (SELECT id FROM t) DELETE FROM t WHERE id IN (SELECT id FROM x)"
    assert guard.analyze(sql).category == StatementCategory.WRITE


def test_batch_takes_most_privileged(guard: SqlGuard) -> None:
    analysis = guard.analyze("SELECT 1; DROP TABLE t;")
    assert analysis.category == StatementCategory.DDL
    assert analysis.destructive is True
    assert analysis.statement_count == 2


def test_drop_hidden_in_string_is_not_destructive(guard: SqlGuard) -> None:
    # The DROP is inside a string literal → still just a SELECT.
    analysis = guard.analyze("SELECT 'DROP TABLE users' AS note")
    assert analysis.category == StatementCategory.READ
    assert analysis.destructive is False


def test_drop_in_comment_ignored(guard: SqlGuard) -> None:
    analysis = guard.analyze("SELECT 1 -- DROP TABLE users\n")
    assert analysis.category == StatementCategory.READ


def test_semicolon_in_string_not_split() -> None:
    parts = split_statements("SELECT ';' AS s")
    assert len(parts) == 1


def test_empty_sql_rejected(guard: SqlGuard) -> None:
    with pytest.raises(ValidationError):
        guard.analyze("   ")
    with pytest.raises(ValidationError):
        guard.analyze("-- just a comment")


# --- enforcement -------------------------------------------------------------------------


def _user(role: str):
    from types import SimpleNamespace

    return SimpleNamespace(role=role)


def test_enforce_blocks_viewer_from_write(guard: SqlGuard) -> None:
    analysis = guard.analyze("DELETE FROM t")
    with pytest.raises(AuthorizationError):
        guard.enforce(_user("viewer"), analysis)


def test_enforce_blocks_developer_from_destructive(guard: SqlGuard) -> None:
    analysis = guard.analyze("DROP TABLE t")
    with pytest.raises(AuthorizationError):
        guard.enforce(_user("developer"), analysis)


def test_enforce_allows_admin_anything(guard: SqlGuard) -> None:
    # The coarse role gate is only a fallback; admins hold every query permission.
    guard.enforce(_user("admin"), guard.analyze("DROP TABLE t"))  # no raise
    guard.enforce(_user("admin"), guard.analyze("SELECT 1"))  # no raise


def test_enforce_blocks_nonadmin_without_grants(guard: SqlGuard) -> None:
    # Non-admin roles no longer carry query permissions — their access comes from grants.
    with pytest.raises(AuthorizationError):
        guard.enforce(_user("dba"), guard.analyze("SELECT 1"))
