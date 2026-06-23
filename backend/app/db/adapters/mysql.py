"""MySQL adapter (aiomysql driver).

aiomysql is pure-Python (PyMySQL-based), so it installs without a compiler and runs anywhere
the platform does.
"""

from __future__ import annotations

from typing import Any

from app.db.adapters._ssl import build_ssl_context
from app.db.adapters.sqlalchemy_adapter import SQLAlchemyAdapter


class MySQLAdapter(SQLAlchemyAdapter):
    dialect = "mysql"
    driver = "aiomysql"
    server_version_sql = "SELECT VERSION()"
    # MySQL connections are not bound to a database; None connects at the server level.
    system_database = None
    databases_sql = "SHOW DATABASES"
    hidden_databases = frozenset(
        {"information_schema", "mysql", "performance_schema", "sys"}
    )
    # In MySQL a "schema" is a database, so the system schemas mirror the hidden databases.
    system_schemas = frozenset({"information_schema", "mysql", "performance_schema", "sys"})

    def _url_query(self) -> dict[str, str]:
        # Full Unicode (incl. 4-byte) support.
        return {"charset": "utf8mb4"}

    def _connect_args(self) -> dict[str, Any]:
        args: dict[str, Any] = {
            "connect_timeout": max(int(self._config.connect_timeout), 1),
        }
        ssl_context = build_ssl_context(self._config.ssl_mode)
        if ssl_context is not None:
            args["ssl"] = ssl_context
        return args
