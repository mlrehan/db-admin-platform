"""Pydantic DTOs for access grants (granular RBAC)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.sql_introspect import GRANTABLE_OPERATIONS, SqlOperation

_VALID_OPS = {op.value for op in GRANTABLE_OPERATIONS}


class AccessGrantCreate(BaseModel):
    subject_type: Literal["user", "role"]
    subject_id: str = Field(min_length=1, max_length=64)
    connection_id: uuid.UUID
    operations: list[str] = Field(min_length=1)
    database: str | None = Field(default=None, max_length=255)
    table_schema: str | None = Field(default=None, max_length=255)
    table_name: str | None = Field(default=None, max_length=255)

    @field_validator("operations")
    @classmethod
    def _validate_ops(cls, value: list[str]) -> list[str]:
        normalized = [str(v).upper() for v in value]
        invalid = [v for v in normalized if v not in _VALID_OPS]
        if invalid:
            raise ValueError(f"Invalid operation(s): {', '.join(invalid)}")
        # De-duplicate, preserve order.
        return list(dict.fromkeys(normalized))

    @field_validator("database", "table_schema", "table_name")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        return value.strip() or None if value else None


class AccessGrantUpdate(BaseModel):
    operations: list[str] = Field(min_length=1)
    database: str | None = Field(default=None, max_length=255)
    table_schema: str | None = Field(default=None, max_length=255)
    table_name: str | None = Field(default=None, max_length=255)

    @field_validator("operations")
    @classmethod
    def _validate_ops(cls, value: list[str]) -> list[str]:
        normalized = [str(v).upper() for v in value]
        invalid = [v for v in normalized if v not in _VALID_OPS]
        if invalid:
            raise ValueError(f"Invalid operation(s): {', '.join(invalid)}")
        return list(dict.fromkeys(normalized))

    @field_validator("database", "table_schema", "table_name")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        return value.strip() or None if value else None


class AccessGrantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    subject_type: str
    subject_id: str
    connection_id: uuid.UUID
    database: str | None
    table_schema: str | None
    table_name: str | None
    operations: list[str]
    created_at: datetime


class OperationsResponse(BaseModel):
    operations: list[str]


def grantable_operations() -> list[str]:
    return [op.value for op in GRANTABLE_OPERATIONS if op != SqlOperation.OTHER]
