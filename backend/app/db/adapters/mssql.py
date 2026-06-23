"""Microsoft SQL Server adapter (aioodbc / ODBC driver).

Requires an ODBC driver to be present on the host (e.g. "ODBC Driver 18 for SQL Server"). The
adapter auto-detects the newest installed "ODBC Driver N for SQL Server" so it works whether
the host has Driver 17 or 18; a specific driver can still be forced per connection via
``options['odbc_driver']``. TLS is expressed through ODBC DSN keywords (``Encrypt`` /
``TrustServerCertificate``) rather than a Python SSLContext.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from app.db.adapters.sqlalchemy_adapter import SQLAlchemyAdapter

_DEFAULT_ODBC_DRIVER = "ODBC Driver 18 for SQL Server"


@lru_cache(maxsize=1)
def detect_odbc_driver() -> str:
    """Return the best available SQL Server ODBC driver installed on the host.

    Prefers the highest-numbered "ODBC Driver N for SQL Server"; falls back to any installed
    SQL Server driver, then to the default name (so a clear "driver not found" error surfaces
    if none are present)."""
    try:
        import pyodbc

        def version_of(name: str) -> int:
            match = re.search(r"\d+", name)
            return int(match.group()) if match else 0

        drivers = [d for d in pyodbc.drivers() if "SQL Server" in d]
        numbered = [d for d in drivers if d.startswith("ODBC Driver")]
        if numbered:
            return max(numbered, key=version_of)
        if drivers:
            return drivers[0]
    except Exception:  # noqa: BLE001 - pyodbc missing or no drivers; fall through
        pass
    return _DEFAULT_ODBC_DRIVER


class MSSQLAdapter(SQLAlchemyAdapter):
    dialect = "mssql"
    driver = "aioodbc"
    server_version_sql = "SELECT @@VERSION"
    # "master" is the system database used for server-level connections.
    system_database = "master"
    databases_sql = "SELECT name FROM sys.databases"
    hidden_databases = frozenset({"master", "tempdb", "model", "msdb"})
    # SQL Server system schemas + the fixed database-role schemas (no user objects).
    system_schemas = frozenset(
        {
            "sys",
            "information_schema",
            "guest",
            "db_owner",
            "db_accessadmin",
            "db_securityadmin",
            "db_ddladmin",
            "db_backupoperator",
            "db_datareader",
            "db_datawriter",
            "db_denydatareader",
            "db_denydatawriter",
        }
    )

    def _url_query(self) -> dict[str, str]:
        mode = self._config.ssl_mode
        encrypt = "no" if mode in (None, "", "disable") else "yes"
        # Without a verifying mode, trust the server cert (typical for internal SQL Servers).
        trust = "no" if mode in ("verify-ca", "verify-full") else "yes"
        driver = self._config.options.get("odbc_driver") or detect_odbc_driver()
        return {
            "driver": str(driver),
            "Encrypt": encrypt,
            "TrustServerCertificate": trust,
        }

    def _connect_args(self) -> dict[str, Any]:
        # ODBC login timeout (seconds).
        return {"timeout": max(int(self._config.connect_timeout), 1)}
