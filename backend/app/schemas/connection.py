"""Pydantic DTOs for saved connections.

The password appears only on input models (``ConnectionCreate``/``ConnectionUpdate``). Output
models never expose the credential or its ciphertext.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from app.db.engines import EngineType


class ConnectionBase(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    engine: EngineType
    host: str = Field(min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    # Optional: leave empty for a server-level connection (browse all databases).
    database: str | None = Field(default=None, max_length=255)
    username: str = Field(min_length=1, max_length=255)
    ssl_mode: str | None = Field(default=None, max_length=32)
    options: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _normalize(self) -> ConnectionBase:
        if self.port is None:
            object.__setattr__(self, "port", self.engine.default_port)
        # Treat an empty/whitespace database as "server-level" (None).
        if self.database is not None and not self.database.strip():
            object.__setattr__(self, "database", None)
        return self


class ConnectionCreate(ConnectionBase):
    password: SecretStr = Field(min_length=1)


class ConnectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    host: str | None = Field(default=None, min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str | None = Field(default=None, min_length=1, max_length=255)
    username: str | None = Field(default=None, min_length=1, max_length=255)
    password: SecretStr | None = None
    ssl_mode: str | None = Field(default=None, max_length=32)
    options: dict[str, Any] | None = None
    is_active: bool | None = None


class ConnectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_id: uuid.UUID
    name: str
    engine: EngineType
    host: str
    port: int
    database: str | None
    username: str
    ssl_mode: str | None
    options: dict[str, Any] | None
    is_active: bool
    created_at: datetime


class ConnectionTestResponse(BaseModel):
    ok: bool
    message: str
    server_version: str | None = None
    latency_ms: float | None = None
