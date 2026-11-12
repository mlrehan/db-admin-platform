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
    routine_definition_sql = (
        "SELECT routine_definition FROM information_schema.routines "
        "WHERE routine_schema = DATABASE() AND routine_name = :name LIMIT 1"
    )
    # USE <db> works on an open connection; a script's USE is followed by the session.
    current_database_sql = "SELECT DATABASE()"
    supports_use_statement = True
    # Cancel the script connection's running statement from a side connection.
    cancel_backend_sql = "KILL QUERY {id}"
    # IntelliSense catalog: the selected database (or every user database at server level);
    # column_type is already fully formatted ("varchar(50)", "decimal(18,2)").
    catalog_columns_sql = (
        "SELECT c.table_schema, c.table_name, t.table_type, c.column_name, c.column_type, "
        "NULL, NULL, NULL, c.is_nullable "
        "FROM information_schema.columns c "
        "JOIN information_schema.tables t "
        "ON t.table_schema = c.table_schema AND t.table_name = c.table_name "
        "WHERE (DATABASE() IS NULL AND c.table_schema NOT IN "
        "('information_schema','mysql','performance_schema','sys')) "
        "OR c.table_schema = DATABASE() "
        "ORDER BY c.table_schema, c.table_name, c.ordinal_position"
    )
    catalog_routines_sql = (
        "SELECT routine_schema, routine_name, routine_type FROM information_schema.routines "
        "WHERE routine_schema = DATABASE() ORDER BY routine_name"
    )
    # DTD_IDENTIFIER is the full declared type, e.g. varchar(50) / decimal(10,2).
    catalog_routine_params_sql = (
        "SELECT SPECIFIC_SCHEMA, SPECIFIC_NAME, ORDINAL_POSITION, PARAMETER_MODE, PARAMETER_NAME, "
        "DTD_IDENTIFIER, NULL, NULL, NULL FROM information_schema.parameters "
        "WHERE SPECIFIC_SCHEMA = DATABASE() ORDER BY SPECIFIC_NAME, ORDINAL_POSITION"
    )

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
