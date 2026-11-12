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


# --- bulk catalog (IntelliSense) --------------------------------------------------------
#
# One round trip returns every table/view with its columns for the active database, so the
# editor's autocomplete never needs one describe-table call per table (N+1).


@dataclass(frozen=True)
class CatalogColumn:
    name: str
    data_type: str
    nullable: bool = True


@dataclass(frozen=True)
class CatalogObject:
    schema: str | None
    name: str
    kind: str  # "table" | "view"
    columns: tuple[CatalogColumn, ...] = ()


@dataclass(frozen=True)
class CatalogRoutine:
    schema: str | None
    name: str
    kind: str  # "procedure" | "function"
    # Parameter labels in order, e.g. ("@CustomerId int", "@Total money OUTPUT"); None when the
    # engine/permissions don't expose them (IntelliSense then shows the name only).
    params: tuple[str, ...] | None = None
    returns: str | None = None


@dataclass(frozen=True)
class Relationship:
    """A foreign key, with its cardinality derived from the child's keys:
    ``one-to-one`` when the FK columns are the child's primary key or a unique key,
    otherwise ``one-to-many`` (one parent row, many child rows)."""

    name: str | None
    child_schema: str | None
    child_table: str
    child_columns: tuple[str, ...]
    parent_schema: str | None
    parent_table: str
    parent_columns: tuple[str, ...]
    cardinality: str
    on_delete: str | None = None
    on_update: str | None = None


@dataclass(frozen=True)
class JunctionTable:
    """A many-to-many link table: its primary key is made entirely of FK columns that point
    at two or more different tables (e.g. TutorCourse(TutorId, CourseCode))."""

    schema: str | None
    table: str
    parents: tuple[tuple[str | None, str], ...]


@dataclass(frozen=True)
class RelationshipGraph:
    relationships: tuple[Relationship, ...]
    junctions: tuple[JunctionTable, ...]


@dataclass(frozen=True)
class Catalog:
    default_schema: str | None
    objects: tuple[CatalogObject, ...]
    routines: tuple[CatalogRoutine, ...]
    truncated: bool = False  # hit the column cap on a very large database
