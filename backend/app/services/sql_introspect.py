"""SQL access introspection.

Parses a SQL batch with sqlglot to determine, per statement, the **operation** performed
(SELECT/INSERT/UPDATE/DELETE/CREATE/ALTER/DROP/...) and the **tables** referenced. This feeds
the granular access-control layer (table-level + operation-level grants).

Fail-closed: if the SQL cannot be parsed, :class:`SqlParseError` is raised and the caller
denies the request for users subject to grants.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import sqlglot
from sqlglot import exp

from app.db.engines import EngineType


class SqlOperation(str, Enum):
    SELECT = "SELECT"
    INSERT = "INSERT"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    CREATE = "CREATE"
    ALTER = "ALTER"
    DROP = "DROP"
    TRUNCATE = "TRUNCATE"
    OTHER = "OTHER"


# The user-configurable operation set (per the spec).
GRANTABLE_OPERATIONS = (
    SqlOperation.SELECT,
    SqlOperation.INSERT,
    SqlOperation.UPDATE,
    SqlOperation.DELETE,
    SqlOperation.CREATE,
    SqlOperation.ALTER,
    SqlOperation.DROP,
)

_DIALECT = {
    EngineType.POSTGRESQL: "postgres",
    EngineType.MYSQL: "mysql",
    EngineType.MSSQL: "tsql",
}


class SqlParseError(Exception):
    """Raised when SQL cannot be parsed for access analysis."""


@dataclass(frozen=True)
class TableRef:
    schema: str | None
    name: str


@dataclass(frozen=True)
class StatementAccess:
    operation: SqlOperation
    tables: list[TableRef] = field(default_factory=list)


def _operation_for(node: exp.Expression) -> SqlOperation:
    if isinstance(node, exp.With):
        inner = node.this
        return _operation_for(inner) if inner is not None else SqlOperation.OTHER
    if isinstance(node, (exp.Select, exp.Union, exp.Subquery)):
        return SqlOperation.SELECT
    if isinstance(node, exp.Insert):
        return SqlOperation.INSERT
    if isinstance(node, exp.Update):
        return SqlOperation.UPDATE
    if isinstance(node, exp.Delete):
        return SqlOperation.DELETE
    if isinstance(node, exp.Create):
        return SqlOperation.CREATE
    if isinstance(node, exp.Drop):
        return SqlOperation.DROP
    if isinstance(node, getattr(exp, "TruncateTable", exp.Command)):
        return SqlOperation.TRUNCATE
    # sqlglot represents ALTER as Alter (newer) or AlterTable (older).
    alter_types = tuple(
        t for t in (getattr(exp, "Alter", None), getattr(exp, "AlterTable", None)) if t
    )
    if alter_types and isinstance(node, alter_types):
        return SqlOperation.ALTER
    return SqlOperation.OTHER


def _tables_for(node: exp.Expression) -> list[TableRef]:
    refs: list[TableRef] = []
    seen: set[tuple[str | None, str]] = set()
    for table in node.find_all(exp.Table):
        name = table.name
        if not name:
            continue
        schema = table.db or None
        key = (schema, name)
        if key not in seen:
            seen.add(key)
            refs.append(TableRef(schema=schema, name=name))
    return refs


def extract_access(sql: str, engine: EngineType) -> list[StatementAccess]:
    """Parse ``sql`` and return per-statement operation + referenced tables."""
    dialect = _DIALECT.get(engine)
    try:
        statements = sqlglot.parse(sql, read=dialect)
    except Exception as exc:  # noqa: BLE001 - normalized to a fail-closed error
        raise SqlParseError(str(exc)) from exc

    out: list[StatementAccess] = []
    for stmt in statements:
        if stmt is None:
            continue
        out.append(StatementAccess(operation=_operation_for(stmt), tables=_tables_for(stmt)))
    if not out:
        raise SqlParseError("No analyzable statement found.")
    return out
