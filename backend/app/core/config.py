"""Application configuration.

Configuration is env-driven and validated at startup (fail-fast). Each concern is a
dedicated :class:`pydantic_settings.BaseSettings` subclass with its own ``env_prefix`` so
the environment stays conventional and self-documenting (see ``.env.example``).

Nothing in this module performs I/O or imports business logic — it is pure configuration.
"""

from __future__ import annotations

import base64
from enum import Enum
from functools import lru_cache

from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Shared loader config: read `.env`, ignore unknown keys, case-insensitive env names.
_ENV_FILE = ".env"
_BASE_CONFIG = SettingsConfigDict(
    env_file=_ENV_FILE,
    env_file_encoding="utf-8",
    extra="ignore",
    case_sensitive=False,
)


class Environment(str, Enum):
    LOCAL = "local"
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class AppSettings(BaseSettings):
    model_config = _BASE_CONFIG | SettingsConfigDict(env_prefix="APP_")

    name: str = "DB Admin Platform"
    version: str = "0.1.0"
    environment: Environment = Environment.LOCAL
    debug: bool = False

    @property
    def is_production(self) -> bool:
        return self.environment == Environment.PRODUCTION


class ServerSettings(BaseSettings):
    model_config = _BASE_CONFIG | SettingsConfigDict(env_prefix="SERVER_")

    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    # Trusted reverse-proxy CIDRs uvicorn honours for X-Forwarded-* headers.
    forwarded_allow_ips: str = "127.0.0.1"


class APISettings(BaseSettings):
    model_config = _BASE_CONFIG | SettingsConfigDict(env_prefix="API_")

    prefix: str = "/api/v1"
    # Comma-separated list in the env. `NoDecode` disables pydantic-settings' default JSON
    # decoding of complex types so the validator below can split a plain CSV string.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )
    request_timeout_seconds: float = Field(default=30.0, gt=0)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


class ControlPlaneDBSettings(BaseSettings):
    """Connection settings for the platform's *own* PostgreSQL database.

    This is never a target database — it stores users, roles, saved connections and the
    immutable audit log.
    """

    model_config = _BASE_CONFIG | SettingsConfigDict(env_prefix="CONTROL_DB_")

    host: str = "localhost"
    port: int = Field(default=5432, ge=1, le=65535)
    user: str = "postgres"
    password: SecretStr = SecretStr("")
    name: str = "db_admin_platform"

    # Async connection-pool tuning.
    pool_size: int = Field(default=10, ge=1)
    max_overflow: int = Field(default=20, ge=0)
    pool_timeout: float = Field(default=30.0, gt=0)
    pool_recycle: int = Field(default=1800, ge=0)
    echo_sql: bool = False

    def dsn(self, *, driver: str = "asyncpg") -> str:
        """Build a SQLAlchemy URL for the control-plane database.

        The password is URL-encoded to tolerate special characters.
        """
        from urllib.parse import quote

        pwd = quote(self.password.get_secret_value(), safe="")
        return (
            f"postgresql+{driver}://{self.user}:{pwd}"
            f"@{self.host}:{self.port}/{self.name}"
        )


class SecuritySettings(BaseSettings):
    model_config = _BASE_CONFIG | SettingsConfigDict(env_prefix="SECURITY_")

    # --- JWT ---
    jwt_secret: SecretStr = SecretStr("")
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    access_token_ttl_seconds: int = Field(default=900, gt=0)  # 15 min
    refresh_token_ttl_seconds: int = Field(default=1_209_600, gt=0)  # 14 days
    jwt_issuer: str = "db-admin-platform"

    # --- Credential envelope encryption ---
    # Master key (KEK) that wraps per-connection data keys. Base64-encoded 32 raw bytes.
    master_encryption_key: SecretStr = SecretStr("")

    @field_validator("jwt_secret")
    @classmethod
    def _jwt_secret_strength(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if secret and len(secret) < 32:
            raise ValueError("SECURITY_JWT_SECRET must be at least 32 characters")
        return value

    @field_validator("master_encryption_key")
    @classmethod
    def _validate_master_key(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if not raw:
            return value
        try:
            decoded = base64.b64decode(raw, validate=True)
        except Exception as exc:  # noqa: BLE001 - re-raised as a config error
            raise ValueError(
                "SECURITY_MASTER_ENCRYPTION_KEY must be valid base64"
            ) from exc
        if len(decoded) != 32:
            raise ValueError(
                "SECURITY_MASTER_ENCRYPTION_KEY must decode to exactly 32 bytes (AES-256)"
            )
        return value

    def master_key_bytes(self) -> bytes:
        return base64.b64decode(self.master_encryption_key.get_secret_value(), validate=True)


class ConnectionSettings(BaseSettings):
    """Tuning for the Connection Orchestrator (live target-DB sessions)."""

    model_config = _BASE_CONFIG | SettingsConfigDict(env_prefix="CONN_")

    # Per-user cap on concurrent live database sessions (resource guard).
    max_sessions_per_user: int = Field(default=10, ge=1)
    # Close a live session after this much inactivity.
    session_idle_ttl_seconds: int = Field(default=1800, gt=0)
    # How often the background reaper scans for idle sessions.
    reaper_interval_seconds: int = Field(default=60, gt=0)
    # Timeout when opening a connection to a target database.
    connect_timeout_seconds: float = Field(default=10.0, gt=0)
    # Private per-session pool sizing for adapters that pool.
    session_pool_min_size: int = Field(default=1, ge=1)
    session_pool_max_size: int = Field(default=5, ge=1)


class QuerySettings(BaseSettings):
    """Tuning for the Query Engine."""

    model_config = _BASE_CONFIG | SettingsConfigDict(env_prefix="QUERY_")

    # Default and hard-cap on rows returned by a buffered execute.
    default_max_rows: int = Field(default=1000, ge=1)
    max_rows_limit: int = Field(default=100_000, ge=1)
    # Server-side cursor batch size for streaming.
    stream_batch_size: int = Field(default=500, ge=1)
    # Abort a statement after this many seconds.
    statement_timeout_seconds: float = Field(default=60.0, gt=0)


class LoggingSettings(BaseSettings):
    model_config = _BASE_CONFIG | SettingsConfigDict(env_prefix="LOG_")

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    format: Literal["json", "console"] = "json"


class Settings(BaseSettings):
    """Root settings aggregate. Construct once via :func:`get_settings`."""

    model_config = _BASE_CONFIG

    app: AppSettings = Field(default_factory=AppSettings)
    server: ServerSettings = Field(default_factory=ServerSettings)
    api: APISettings = Field(default_factory=APISettings)
    control_db: ControlPlaneDBSettings = Field(default_factory=ControlPlaneDBSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    connections: ConnectionSettings = Field(default_factory=ConnectionSettings)
    query: QuerySettings = Field(default_factory=QuerySettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)

    @model_validator(mode="after")
    def _enforce_production_secrets(self) -> Settings:
        """In non-local environments, required secrets must be present.

        We keep local/dev permissive so the app can boot for the first time, but refuse to
        start a staging/production process with missing crypto material.
        """
        if self.app.environment in (Environment.LOCAL, Environment.DEVELOPMENT):
            return self

        missing: list[str] = []
        if not self.security.jwt_secret.get_secret_value():
            missing.append("SECURITY_JWT_SECRET")
        if not self.security.master_encryption_key.get_secret_value():
            missing.append("SECURITY_MASTER_ENCRYPTION_KEY")
        if not self.control_db.password.get_secret_value():
            missing.append("CONTROL_DB_PASSWORD")
        if missing:
            raise ValueError(
                f"Missing required secrets for environment "
                f"'{self.app.environment.value}': {', '.join(missing)}"
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton (cached)."""
    return Settings()
