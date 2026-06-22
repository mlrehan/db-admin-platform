"""Unit tests for sqlglot-based SQL access introspection."""

from __future__ import annotations

import pytest

from app.db.engines import EngineType
from app.services.sql_introspect import SqlOperation, SqlParseError, extract_access

PG = EngineType.POSTGRESQL


def test_select_extracts_table() -> None:
    stmts = extract_access("SELECT * FROM users WHERE id = 1", PG)
    assert len(stmts) == 1
    assert stmts[0].operation == SqlOperation.SELECT
    assert [(t.schema, t.name) for t in stmts[0].tables] == [(None, "users")]


def test_schema_qualified_table() -> None:
    stmts = extract_access("SELECT * FROM public.orders", PG)
    assert (stmts[0].tables[0].schema, stmts[0].tables[0].name) == ("public", "orders")


@pytest.mark.parametrize(
    "sql,op",
    [
        ("INSERT INTO t (a) VALUES (1)", SqlOperation.INSERT),
        ("UPDATE t SET a = 1", SqlOperation.UPDATE),
        ("DELETE FROM t", SqlOperation.DELETE),
        ("CREATE TABLE t (id int)", SqlOperation.CREATE),
        ("DROP TABLE t", SqlOperation.DROP),
        ("ALTER TABLE t ADD COLUMN c int", SqlOperation.ALTER),
    ],
)
def test_operation_classification(sql: str, op: SqlOperation) -> None:
    stmts = extract_access(sql, PG)
    assert stmts[0].operation == op
    assert any(t.name == "t" for t in stmts[0].tables)


def test_join_collects_all_tables() -> None:
    stmts = extract_access(
        "SELECT * FROM users u JOIN orders o ON o.user_id = u.id", PG
    )
    names = {t.name for t in stmts[0].tables}
    assert names == {"users", "orders"}


def test_cte_select_is_select() -> None:
    stmts = extract_access("WITH x AS (SELECT 1) SELECT * FROM x", PG)
    assert stmts[0].operation == SqlOperation.SELECT


def test_multi_statement() -> None:
    stmts = extract_access("SELECT * FROM a; DELETE FROM b;", PG)
    assert [s.operation for s in stmts] == [SqlOperation.SELECT, SqlOperation.DELETE]


def test_unparseable_raises() -> None:
    with pytest.raises(SqlParseError):
        extract_access("SELECT FROM WHERE ;;; not valid (", PG)
