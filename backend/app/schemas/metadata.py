"""Pydantic DTOs for schema introspection responses."""

from __future__ import annotations

from pydantic import BaseModel, Field


class DatabaseOut(BaseModel):
    name: str
    is_active: bool


class SwitchDatabaseRequest(BaseModel):
    # None / empty reverts to the connection's default/system database.
    database: str | None = Field(default=None, max_length=255)


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
