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
    # host/username are required for server engines; SQLite (a file) has neither.
    host: str = Field(default="", max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    # Optional: leave empty for a server-level connection (browse all databases). For SQLite
    # it is a database file name inside the server's SQLite data directory.
    database: str | None = Field(default=None, max_length=255)
    username: str = Field(default="", max_length=255)
    ssl_mode: str | None = Field(default=None, max_length=32)
    options: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _normalize(self) -> ConnectionBase:
        # Treat an empty/whitespace database as "server-level" (None).
        if self.database is not None and not self.database.strip():
            object.__setattr__(self, "database", None)
        if self.engine.is_file_based:
            # SQLite: a file in the server's data directory — no network endpoint or login.
            if self.database and (
                any(sep in self.database for sep in ("/", "\\", ":")) or ".." in self.database
            ):
                raise ValueError(
                    "SQLite database must be a plain file name (e.g. 'sales.db'), not a path."
                )
            object.__setattr__(self, "host", "localhost")
            object.__setattr__(self, "port", self.engine.default_port)
            return self
        if not self.host.strip():
            raise ValueError("host is required.")
        if not self.username.strip():
            raise ValueError("username is required.")
        if self.port is None:
            object.__setattr__(self, "port", self.engine.default_port)
        return self


class ConnectionCreate(ConnectionBase):
    password: SecretStr | None = None

    @model_validator(mode="after")
    def _require_password(self) -> ConnectionCreate:
        if self.engine.is_file_based:
            # Credentials don't apply to SQLite; store an (encrypted) empty secret.
            if self.password is None:
                object.__setattr__(self, "password", SecretStr(""))
        elif self.password is None or not self.password.get_secret_value():
            raise ValueError("password is required.")
        return self


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


class ConnectionDatabasesResponse(BaseModel):
    databases: list[str]


class GrantTableOut(BaseModel):
    schema_name: str | None
    name: str


class ConnectionTablesResponse(BaseModel):
    tables: list[GrantTableOut]
