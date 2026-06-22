"""Pydantic DTOs for query execution."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    sql: str = Field(min_length=1)
    params: dict[str, Any] | None = None
    max_rows: int | None = Field(default=None, ge=1)


class QueryColumnOut(BaseModel):
    name: str
    type: str | None = None


class QueryResultOut(BaseModel):
    query_id: uuid.UUID
    columns: list[QueryColumnOut]
    rows: list[list[Any]]
    row_count: int
    rows_affected: int | None
    execution_ms: float
    truncated: bool
    returns_rows: bool
    category: str
    destructive: bool


class RunningQueryOut(BaseModel):
    query_id: uuid.UUID
    session_id: uuid.UUID
    category: str
    started_at: str


class ScriptRequest(BaseModel):
    sql: str = Field(min_length=1)
    params: dict[str, Any] | None = None
    max_rows: int | None = Field(default=None, ge=1)


class StatementResultOut(BaseModel):
    sql: str
    success: bool
    returns_rows: bool
    columns: list[QueryColumnOut]
    rows: list[list[Any]]
    row_count: int
    rows_affected: int | None
    execution_ms: float
    truncated: bool
    category: str
    destructive: bool
    error_code: str | None = None
    error: str | None = None


class ScriptResultOut(BaseModel):
    success: bool
    statements: list[StatementResultOut]
