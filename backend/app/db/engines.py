"""Supported target-database engine identifiers.

Defined in its own leaf module (no heavy imports) so models, schemas, adapters and the
orchestrator can all share the enum without import cycles.
"""

from __future__ import annotations

from enum import Enum


class EngineType(str, Enum):
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    MSSQL = "mssql"
    SQLITE = "sqlite"

    @property
    def default_port(self) -> int:
        return {
            EngineType.POSTGRESQL: 5432,
            EngineType.MYSQL: 3306,
            EngineType.MSSQL: 1433,
            EngineType.SQLITE: 0,  # file-based: no network endpoint
        }[self]

    @property
    def is_file_based(self) -> bool:
        """SQLite is a file, not a server: no host/port/credentials."""
        return self is EngineType.SQLITE
