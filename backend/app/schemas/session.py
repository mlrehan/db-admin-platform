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
