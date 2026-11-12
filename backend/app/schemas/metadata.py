"""Pydantic DTOs for schema introspection responses."""

from __future__ import annotations

from pydantic import BaseModel, Field


class DatabaseOut(BaseModel):
    name: str
    is_active: bool


class SwitchDatabaseRequest(BaseModel):
    # None / empty reverts to the connection's default/system database.
    database: str | None = Field(default=None, max_length=255)


class CreateDatabaseRequest(BaseModel):
    name: str = Field(min_length=1, max_length=63)


class SchemaOut(BaseModel):
    name: str
    is_default: bool


class TableOut(BaseModel):
    name: str
    schema_name: str | None
    kind: str
    comment: str | None = None


class ColumnOut(BaseModel):
    name: str
    data_type: str
    nullable: bool
    default: str | None
    primary_key: bool
    autoincrement: bool
    comment: str | None = None


class IndexOut(BaseModel):
    name: str
    columns: list[str]
    unique: bool
    primary: bool


class ForeignKeyOut(BaseModel):
    name: str | None
    columns: list[str]
    referred_schema: str | None
    referred_table: str
    referred_columns: list[str]


class TableDetailOut(BaseModel):
    table: TableOut
    columns: list[ColumnOut]
    primary_key: list[str]
    indexes: list[IndexOut]
    foreign_keys: list[ForeignKeyOut]


class RoutineOut(BaseModel):
    name: str
    kind: str
    return_type: str | None = None


class CatalogColumnOut(BaseModel):
    name: str
    type: str
    nullable: bool = True


class CatalogObjectOut(BaseModel):
    schema_name: str | None = None
    name: str
    kind: str  # "table" | "view"
    columns: list[CatalogColumnOut]


class CatalogRoutineOut(BaseModel):
    schema_name: str | None = None
    name: str
    kind: str  # "procedure" | "function"
    params: list[str] | None = None  # e.g. ["@CustomerId int", "@Total money OUTPUT"]
    returns: str | None = None


class CatalogOut(BaseModel):
    """Everything SQL IntelliSense needs for the session's active database, in one response."""

    dialect: str
    database: str | None = None
    default_schema: str | None = None
    objects: list[CatalogObjectOut]
    routines: list[CatalogRoutineOut]
    truncated: bool = False


class RelationshipOut(BaseModel):
    name: str | None = None
    child_schema: str | None = None
    child_table: str
    child_columns: list[str]
    parent_schema: str | None = None
    parent_table: str
    parent_columns: list[str]
    cardinality: str  # "one-to-one" | "one-to-many"
    on_delete: str | None = None
    on_update: str | None = None


class JunctionOut(BaseModel):
    schema_name: str | None = None
    table: str
    parents: list[list[str | None]]  # [[schema, table], …] — the tables it links many-to-many


class RelationshipsOut(BaseModel):
    relationships: list[RelationshipOut]
    junctions: list[JunctionOut]


class ObjectDefinitionOut(BaseModel):
    """The editable DDL/definition script for a database object (table/view/procedure/
    function/trigger/index)."""

    object_type: str
    name: str
    schema_name: str | None = None
    dialect: str
    definition: str | None = None
    available: bool
