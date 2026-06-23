"""Phase 4 — DB Adapter Layer unit tests (no live database required).

Covers URL/DSN construction, SSL handling, registration of the built-ins, and the
unsupported-engine path. An autouse fixture snapshots and restores the global adapter
registry so these tests never leak registrations into other test modules.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.core.exceptions import UnsupportedEngineError
from app.db.adapters import registry
from app.db.adapters._ssl import build_ssl_context
from app.db.adapters.base import ConnectionConfig
from app.db.adapters.builtin import register_builtin_adapters
from app.db.adapters.mssql import MSSQLAdapter
from app.db.adapters.mysql import MySQLAdapter
from app.db.adapters.postgresql import PostgreSQLAdapter
from app.db.adapters.registry import create_adapter, supported_engines
from app.db.engines import EngineType


@pytest.fixture(autouse=True)
def _isolate_registry() -> Iterator[None]:
    saved = dict(registry._registry)  # noqa: SLF001
    yield
    registry._registry.clear()  # noqa: SLF001
    registry._registry.update(saved)  # noqa: SLF001


def _config(engine: EngineType, **over) -> ConnectionConfig:
    base = dict(
        engine=engine,
        host="db.example.com",
        port=engine.default_port,
        database="appdb",
        username="svc",
        password="p@ss:word/with-specials",
    )
    base.update(over)
    return ConnectionConfig(**base)  # type: ignore[arg-type]


# --- registration ------------------------------------------------------------------------


def test_register_builtins_registers_all_engines() -> None:
    register_builtin_adapters()
    assert supported_engines() == frozenset(EngineType)


def test_create_adapter_returns_correct_type() -> None:
    register_builtin_adapters()
    assert isinstance(create_adapter(_config(EngineType.POSTGRESQL)), PostgreSQLAdapter)
    assert isinstance(create_adapter(_config(EngineType.MYSQL)), MySQLAdapter)
    assert isinstance(create_adapter(_config(EngineType.MSSQL)), MSSQLAdapter)


def test_unsupported_engine_raises_when_empty() -> None:
    registry._registry.clear()  # noqa: SLF001
    with pytest.raises(UnsupportedEngineError):
        create_adapter(_config(EngineType.POSTGRESQL))


# --- URL / DSN construction --------------------------------------------------------------


def test_postgres_url_and_password_escaping() -> None:
    adapter = PostgreSQLAdapter(_config(EngineType.POSTGRESQL))
    url = adapter._build_url()  # noqa: SLF001
    assert url.drivername == "postgresql+asyncpg"
    assert url.host == "db.example.com"
    assert url.port == 5432
    assert url.database == "appdb"
    # The raw password is preserved on the URL object (escaping happens at render time).
    assert url.password == "p@ss:word/with-specials"
    rendered = url.render_as_string(hide_password=False)
    assert "p%40ss%3Aword%2Fwith-specials" in rendered  # percent-encoded


def test_mysql_url_has_utf8mb4_charset() -> None:
    adapter = MySQLAdapter(_config(EngineType.MYSQL))
    url = adapter._build_url()  # noqa: SLF001
    assert url.drivername == "mysql+aiomysql"
    assert url.query.get("charset") == "utf8mb4"


def test_mssql_url_query_defaults() -> None:
    adapter = MSSQLAdapter(_config(EngineType.MSSQL))
    query = adapter._url_query()  # noqa: SLF001
    # Driver is auto-detected from the host (e.g. Driver 17 or 18 for SQL Server).
    assert "SQL Server" in query["driver"]
    assert query["Encrypt"] == "no"  # ssl_mode None → no encryption by default
    assert query["TrustServerCertificate"] == "yes"


def test_mssql_custom_odbc_driver_and_tls() -> None:
    cfg = _config(EngineType.MSSQL, ssl_mode="verify-full", options={"odbc_driver": "ODBC Driver 17 for SQL Server"})
    query = MSSQLAdapter(cfg)._url_query()  # noqa: SLF001
    assert query["driver"] == "ODBC Driver 17 for SQL Server"
    assert query["Encrypt"] == "yes"
    assert query["TrustServerCertificate"] == "no"


def test_mssql_driver_autodetect(monkeypatch) -> None:
    # With no per-connection override, the adapter picks the newest installed driver.
    import app.db.adapters.mssql as mssql_mod

    mssql_mod.detect_odbc_driver.cache_clear()
    monkeypatch.setattr(
        mssql_mod, "detect_odbc_driver", lambda: "ODBC Driver 18 for SQL Server"
    )
    query = MSSQLAdapter(_config(EngineType.MSSQL))._url_query()  # noqa: SLF001
    assert query["driver"] == "ODBC Driver 18 for SQL Server"


# --- SSL context ------------------------------------------------------------------------


@pytest.mark.parametrize("mode", [None, "", "disable", "allow", "prefer"])
def test_ssl_disabled_modes_return_none(mode: str | None) -> None:
    assert build_ssl_context(mode) is None


def test_ssl_require_does_not_verify() -> None:
    import ssl

    ctx = build_ssl_context("require")
    assert ctx is not None
    assert ctx.verify_mode == ssl.CERT_NONE
    assert ctx.check_hostname is False


def test_ssl_verify_full_verifies_hostname() -> None:
    import ssl

    ctx = build_ssl_context("verify-full")
    assert ctx is not None
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


# --- connect-args -----------------------------------------------------------------------


def test_postgres_connect_args_set_app_name_and_timeout() -> None:
    args = PostgreSQLAdapter(_config(EngineType.POSTGRESQL, connect_timeout=7))._connect_args()  # noqa: SLF001
    assert args["timeout"] == 7
    assert args["server_settings"]["application_name"] == "db-admin-platform"


def test_postgres_connect_args_include_ssl_when_required() -> None:
    args = PostgreSQLAdapter(_config(EngineType.POSTGRESQL, ssl_mode="require"))._connect_args()  # noqa: SLF001
    assert "ssl" in args


def test_redact_strips_password() -> None:
    adapter = PostgreSQLAdapter(_config(EngineType.POSTGRESQL, password="topsecret"))
    assert adapter._redact("error near topsecret here") == "error near *** here"  # noqa: SLF001


# --- system-schema detection (non-admins must never see these) ---------------------------


def test_postgres_system_schema_detection() -> None:
    pg = PostgreSQLAdapter(_config(EngineType.POSTGRESQL))
    assert pg.is_system_schema("pg_catalog")
    assert pg.is_system_schema("pg_toast")
    assert pg.is_system_schema("information_schema")
    assert not pg.is_system_schema("public")
    assert not pg.is_system_schema("reporting")
    assert not pg.is_system_schema(None)


def test_mysql_system_schema_detection() -> None:
    my = MySQLAdapter(_config(EngineType.MYSQL))
    for s in ("information_schema", "mysql", "performance_schema", "sys"):
        assert my.is_system_schema(s)
    assert not my.is_system_schema("appdb")


def test_mssql_system_schema_detection() -> None:
    ms = MSSQLAdapter(_config(EngineType.MSSQL))
    assert ms.is_system_schema("sys")
    assert ms.is_system_schema("INFORMATION_SCHEMA")  # case-insensitive
    assert ms.is_system_schema("db_owner")
    assert not ms.is_system_schema("dbo")
    assert not ms.is_system_schema("sales")


# --- create_database name validation (defence-in-depth) ----------------------------------


async def test_create_database_rejects_invalid_names() -> None:
    from app.core.exceptions import ValidationError

    pg = PostgreSQLAdapter(_config(EngineType.POSTGRESQL))
    for bad in ("1bad", "has space", "drop;table", "weird*name", "", "x" * 64):
        with pytest.raises(ValidationError):
            await pg.create_database(bad)


# --- non-transactional statement routing (CREATE DATABASE etc. need AUTOCOMMIT) ----------


def test_requires_autocommit_detection() -> None:
    from app.db.adapters.sqlalchemy_adapter import _requires_autocommit  # noqa: PLC0415

    must = [
        "CREATE DATABASE lait", "create database lait;", "  CREATE   DATABASE x",
        "DROP DATABASE x", "ALTER DATABASE x SET y = 1", "VACUUM ANALYZE",
        "CREATE INDEX CONCURRENTLY ix ON t(c)", "DROP INDEX CONCURRENTLY ix",
    ]
    must_not = [
        "SELECT * FROM tutor", "CREATE TABLE t(id int)", "CREATE INDEX ix ON t(c)",
        "DROP TABLE t", "ALTER TABLE t ADD c int", "INSERT INTO t VALUES (1)",
    ]
    assert all(_requires_autocommit(s) for s in must)
    assert not any(_requires_autocommit(s) for s in must_not)
