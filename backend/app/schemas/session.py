"""Pydantic DTOs for live database sessions (orchestrator)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.db.engines import EngineType


class OpenSessionRequest(BaseModel):
    connection_id: uuid.UUID


class SessionRead(BaseModel):
    id: uuid.UUID
    connection_id: uuid.UUID
    engine: EngineType
    created_at: datetime
    last_used_at: datetime
    idle_seconds: float
    connected: bool
    active_database: str | None = None
    # Whether the current user may create a database on this connection (admin or granted).
    can_create_database: bool = False


class AdminSessionRead(BaseModel):
    """A live session as seen by an admin overseeing every user's sessions."""

    id: uuid.UUID
    user_id: uuid.UUID
    user_email: str | None = None
    connection_id: uuid.UUID
    connection_name: str | None = None
    engine: EngineType
    database: str | None = None
    created_at: datetime
    last_used_at: datetime
    idle_seconds: float
    idle: bool  # convenience flag: idle beyond the configured threshold
    connected: bool


class CloseAllSessionsRequest(BaseModel):
    # When true (default), the admin's own current sessions are preserved.
    exclude_self: bool = True


class CloseSessionsResult(BaseModel):
    closed: int
