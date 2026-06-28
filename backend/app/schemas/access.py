"""Pydantic DTOs for access grants (granular RBAC)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.services.sql_introspect import GRANTABLE_OPERATIONS, SqlOperation

_VALID_OPS = {op.value for op in GRANTABLE_OPERATIONS}


def _validate_operations(value: list[str]) -> list[str]:
    normalized = [str(v).upper() for v in value]
    invalid = [v for v in normalized if v not in _VALID_OPS]
    if invalid:
        raise ValueError(f"Invalid operation(s): {', '.join(invalid)}")
    return list(dict.fromkeys(normalized))  # de-duplicate, preserve order


def _clean_scope_list(value: list[str] | None) -> list[str]:
    if not value:
        return []
    seen: list[str] = []
    for v in value:
        s = (str(v) or "").strip()
        if s and s != "*" and s not in seen:
            seen.append(s)
    return seen


class AccessGrantCreate(BaseModel):
    subject_type: Literal["user", "role"]
    subject_id: str = Field(min_length=1, max_length=64)
    connection_id: uuid.UUID
    operations: list[str] = Field(min_length=1)
    # Preferred: arrays (empty = any). Legacy scalars still accepted for backward compatibility.
    databases: list[str] | None = None
    tables: list[str] | None = None
    database: str | None = Field(default=None, max_length=255)
    table_schema: str | None = Field(default=None, max_length=255)
    table_name: str | None = Field(default=None, max_length=255)

    _ops = field_validator("operations")(classmethod(lambda cls, v: _validate_operations(v)))
    _dbs = field_validator("databases", "tables")(
        classmethod(lambda cls, v: _clean_scope_list(v))
    )

    @field_validator("database", "table_schema", "table_name")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        return value.strip() or None if value else None


class AccessGrantUpdate(BaseModel):
    operations: list[str] = Field(min_length=1)
    databases: list[str] | None = None
    tables: list[str] | None = None
    database: str | None = Field(default=None, max_length=255)
    table_schema: str | None = Field(default=None, max_length=255)
    table_name: str | None = Field(default=None, max_length=255)

    _ops = field_validator("operations")(classmethod(lambda cls, v: _validate_operations(v)))
    _dbs = field_validator("databases", "tables")(
        classmethod(lambda cls, v: _clean_scope_list(v))
    )

    @field_validator("database", "table_schema", "table_name")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        return value.strip() or None if value else None


class AccessGrantOut(BaseModel):
    id: uuid.UUID
    subject_type: str
    subject_id: str
    connection_id: uuid.UUID
    databases: list[str]
    tables: list[str]
    # Legacy single-value mirrors (populated only when the scope has exactly one value), kept
    # so older clients keep working.
    database: str | None
    table_schema: str | None
    table_name: str | None
    operations: list[str]
    created_at: datetime

    @classmethod
    def from_model(cls, grant) -> "AccessGrantOut":
        dbs = _clean_scope_list(
            grant.databases if grant.databases is not None else (
                [grant.database] if grant.database else []
            )
        )
        tbls = _clean_scope_list(
            grant.tables if grant.tables is not None else (
                [grant.table_name] if grant.table_name else []
            )
        )
        return cls(
            id=grant.id,
            subject_type=grant.subject_type,
            subject_id=grant.subject_id,
            connection_id=grant.connection_id,
            databases=dbs,
            tables=tbls,
            database=dbs[0] if len(dbs) == 1 else None,
            table_schema=grant.table_schema,
            table_name=tbls[0] if len(tbls) == 1 else None,
            operations=grant.operations,
            created_at=grant.created_at,
        )


class OperationsResponse(BaseModel):
    operations: list[str]


def grantable_operations() -> list[str]:
    return [op.value for op in GRANTABLE_OPERATIONS if op != SqlOperation.OTHER]
