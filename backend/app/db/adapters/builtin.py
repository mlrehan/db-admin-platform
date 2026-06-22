"""Registration of the built-in engine adapters.

Called once at application startup (from the lifespan) so the registry knows every supported
engine. Importing the adapter classes does **not** import their DBAPI drivers — SQLAlchemy
loads the driver lazily when an engine is first created. That means the app boots and
registers all engines even on a host missing, say, the MSSQL ODBC driver; only an actual
connection to that engine would then fail (cleanly, as a ``CONNECTION_FAILED`` error).
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.db.adapters.mssql import MSSQLAdapter
from app.db.adapters.mysql import MySQLAdapter
from app.db.adapters.postgresql import PostgreSQLAdapter
from app.db.adapters.registry import register_adapter, supported_engines
from app.db.engines import EngineType

logger = get_logger(__name__)


def register_builtin_adapters() -> None:
    register_adapter(EngineType.POSTGRESQL, PostgreSQLAdapter)
    register_adapter(EngineType.MYSQL, MySQLAdapter)
    register_adapter(EngineType.MSSQL, MSSQLAdapter)
    logger.info(
        "Registered built-in adapters",
        extra={"engines": sorted(e.value for e in supported_engines())},
    )
