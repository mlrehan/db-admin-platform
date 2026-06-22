"""Schema-introspection value objects.

Engine-agnostic descriptions of the database hierarchy the Schema Explorer walks:
``schema → tables → (columns, indexes, foreign keys) → routines``. Adapters populate these;
the API layer maps them to response schemas.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DatabaseInfo:
    name: str
    is_active: bool = False


@dataclass(frozen=True)
class SchemaInfo:
    name: str
    is_default: bool = False


@dataclass(frozen=True)
class TableInfo:
    name: str
    schema: str | None
    kind: str  # "table" | "view"
    comment: str | None = None


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    data_type: str
    nullable: bool
    default: str | None
    primary_key: bool
    autoincrement: bool = False
    comment: str | None = None


@dataclass(frozen=True)
class IndexInfo:
    name: str
    columns: list[str]
    unique: bool
    primary: bool = False


@dataclass(frozen=True)
class ForeignKeyInfo:
    name: str | None
    columns: list[str]
    referred_schema: str | None
    referred_table: str
    referred_columns: list[str]


@dataclass(frozen=True)
class TableDetail:
    table: TableInfo
    columns: list[ColumnInfo]
    primary_key: list[str]
    indexes: list[IndexInfo]
    foreign_keys: list[ForeignKeyInfo] = field(default_factory=list)


@dataclass(frozen=True)
class RoutineInfo:
    name: str
    kind: str  # "procedure" | "function"
    return_type: str | None = None
