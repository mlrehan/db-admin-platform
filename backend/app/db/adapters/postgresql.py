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
    # Cancel the script connection's running statement from a side connection.
    cancel_backend_sql = "SELECT pg_cancel_backend({id})"
    # IntelliSense catalog straight from pg_catalog (much faster than information_schema on
    # large databases; format_type yields full types such as "character varying(50)").
    catalog_columns_sql = (
        "SELECT n.nspname, c.relname, "
        "CASE WHEN c.relkind IN ('v','m') THEN 'VIEW' ELSE 'BASE TABLE' END, "
        "a.attname, format_type(a.atttypid, a.atttypmod), NULL, NULL, NULL, "
        "CASE WHEN a.attnotnull THEN 'NO' ELSE 'YES' END "
        "FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped "
        "WHERE c.relkind IN ('r','p','v','m','f') "
        "AND n.nspname NOT IN ('pg_catalog','information_schema') "
        "AND n.nspname NOT LIKE 'pg\\_%' "
        "ORDER BY n.nspname, c.relname, a.attnum"
    )
    # Five columns: the argument list and result type come inline (pg_get_function_*), which
    # also handles overloads and defaults better than information_schema.parameters.
    catalog_routine_params_sql = None
    catalog_routines_sql = (
        "SELECT n.nspname, p.proname, "
        "CASE p.prokind WHEN 'p' THEN 'PROCEDURE' ELSE 'FUNCTION' END, "
        "pg_get_function_arguments(p.oid), pg_get_function_result(p.oid) "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname NOT IN ('pg_catalog','information_schema') "
        "AND n.nspname NOT LIKE 'pg\\_%' AND p.prokind IN ('f','p') "
        "ORDER BY n.nspname, p.proname"
    )
    # Editable-DDL sources (each takes a single :name value, safely bound and cast).
    object_definition_sql = {
        "view": (
            "SELECT 'CREATE OR REPLACE VIEW ' || (:name)::regclass || ' AS\n' "
            "|| pg_get_viewdef((:name)::regclass, true)"
        ),
        "index": "SELECT pg_get_indexdef((:name)::regclass) || ';'",
        "procedure": "SELECT pg_get_functiondef((:name)::regproc)",
        "function": "SELECT pg_get_functiondef((:name)::regproc)",
        "trigger": (
            "SELECT pg_get_triggerdef(oid) || ';' FROM pg_trigger "
            "WHERE tgname = :name AND NOT tgisinternal ORDER BY oid LIMIT 1"
        ),
    }

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
