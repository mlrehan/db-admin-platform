"""Pydantic DTOs for audit log responses."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID | None
    user_email: str | None
    connection_id: uuid.UUID | None
    session_id: uuid.UUID | None
    engine: str | None
    statement: str
    category: str | None
    destructive: bool
    success: bool
    duration_ms: float
    row_count: int | None
    rows_affected: int | None
    error_code: str | None
    error_message: str | None
    request_id: str | None
    ip_address: str | None = None
    created_at: datetime
