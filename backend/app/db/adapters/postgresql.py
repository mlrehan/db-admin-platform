"""PostgreSQL adapter (asyncpg driver)."""

from __future__ import annotations

from typing import Any

from app.db.adapters._ssl import build_ssl_context
from app.db.adapters.sqlalchemy_adapter import SQLAlchemyAdapter


class PostgreSQLAdapter(SQLAlchemyAdapter):
    dialect = "postgresql"
    driver = "asyncpg"
    server_version_sql = "SELECT version()"
    # PostgreSQL connections are database-scoped; "postgres" is the maintenance database used
    # for server-level connections and for enumerating databases.
    system_database = "postgres"
    databases_sql = "SELECT datname FROM pg_database WHERE datistemplate = false"
    hidden_databases = frozenset()
    # Catalog/metadata schemas non-admins must not see (pg_catalog, pg_toast, pg_temp_*, …).
    system_schemas = frozenset({"information_schema"})
    system_schema_prefixes = ("pg_",)
    # pg_proc.prosrc is the bare routine body (SQL or pl/pgsql).
    routine_definition_sql = (
        "SELECT prosrc FROM pg_proc WHERE proname = :name "
        "ORDER BY oid LIMIT 1"
    )

    def _connect_args(self) -> dict[str, Any]:
        args: dict[str, Any] = {
            "timeout": self._config.connect_timeout,
            "server_settings": {"application_name": self.application_name},
        }
        ssl_context = build_ssl_context(self._config.ssl_mode)
        if ssl_context is not None:
            # asyncpg accepts an SSLContext directly.
            args["ssl"] = ssl_context
        return args
