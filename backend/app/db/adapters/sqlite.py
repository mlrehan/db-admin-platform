"""SQLite adapter (aiosqlite driver).

SQLite is file-based, so a "connection" points at a database *file* — and, for safety, only at
a file inside the configured ``CONN_SQLITE_DATA_DIR`` directory. Names are validated (no path
separators, no traversal) and the resolved path must stay inside that directory, so a user can
never make the server open or create an arbitrary file. Files are opened with SQLite's URI
``mode=rw`` so connecting to a missing name fails instead of silently creating a file; new
databases are created explicitly through :meth:`create_database`.

A connection with no database selected is the "server level": it lists every database file in
the data directory and lets the user switch between them, mirroring the other engines.

Every connection enables ``PRAGMA foreign_keys`` (off by default in SQLite), WAL journaling
(readers don't block writers — important with many concurrent sessions) and a busy timeout.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, ClassVar

import aiosqlite
from sqlalchemy import event, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.exceptions import ConflictError, ConnectionFailedError, ValidationError
from app.db.adapters.metadata import DatabaseInfo, RoutineInfo
from app.db.adapters.sqlalchemy_adapter import SQLAlchemyAdapter

_ALLOWED_SUFFIXES = (".db", ".sqlite", ".sqlite3")
# A bare file name: letters/digits/_ - . only (no separators, no leading dot).
_FILE_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,120}$")
_BACKEND_ROOT = Path(__file__).resolve().parents[3]


def sqlite_data_dir() -> Path:
    """The directory SQLite database files must live in (created on demand)."""
    from app.core.config import get_settings  # local: settings are loaded lazily

    raw = Path(get_settings().connections.sqlite_data_dir)
    directory = raw if raw.is_absolute() else _BACKEND_ROOT / raw
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def resolve_sqlite_file(name: str) -> Path:
    """Validate a database name and resolve it to a file inside the data directory.

    Raises :class:`ValidationError` for anything that isn't a plain file name with an allowed
    extension (``.db`` is appended when there's none) or that would escape the directory."""
    candidate = (name or "").strip()
    if not _FILE_NAME_RE.match(candidate) or ".." in candidate:
        raise ValidationError(
            "Invalid SQLite database name. Use a plain file name such as 'sales.db' "
            "(letters, digits, '_', '-', '.'; no folders)."
        )
    if not candidate.lower().endswith(_ALLOWED_SUFFIXES):
        candidate += ".db"
    base = sqlite_data_dir()
    path = (base / candidate).resolve()
    if path.parent != base:  # defence in depth against traversal / symlink tricks
        raise ValidationError("SQLite databases must live in the configured data directory.")
    return path


def _on_connect(dbapi_conn: Any, _record: Any) -> None:
    cursor = dbapi_conn.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA busy_timeout = 5000")
        cursor.execute("PRAGMA journal_mode = WAL")
    finally:
        cursor.close()


class SQLiteAdapter(SQLAlchemyAdapter):
    dialect = "sqlite"
    driver = "aiosqlite"
    server_version_sql = "SELECT sqlite_version()"
    system_database = None
    hidden_databases = frozenset()
    system_schemas = frozenset({"temp"})
    script_mode = "sequential"
    routine_definition_sql = None  # SQLite has no stored procedures / functions
    # IntelliSense catalog via the pragma_table_info table-valued function (SQLite >= 3.16).
    catalog_columns_sql = (
        "SELECT 'main', m.name, CASE m.type WHEN 'view' THEN 'VIEW' ELSE 'BASE TABLE' END, "
        "p.name, p.type, NULL, NULL, NULL, CASE p.\"notnull\" WHEN 1 THEN 'NO' ELSE 'YES' END "
        "FROM sqlite_master m JOIN pragma_table_info(m.name) p "
        "WHERE m.type IN ('table','view') AND m.name NOT LIKE 'sqlite~_%' ESCAPE '~' "
        "ORDER BY m.name, p.cid"
    )
    catalog_routines_sql = None
    catalog_routine_params_sql = None

    # --- files / engines -----------------------------------------------------------------

    def _resolve_db(self, database: str | None) -> str | None:
        # No "system database": None means "no file selected" (server level).
        return database or self._config.database or None

    def _file_for(self, database: str | None) -> Path | None:
        name = self._resolve_db(database)
        return resolve_sqlite_file(name) if name else None

    def _create_engine(self, database: str | None) -> AsyncEngine:
        path = self._file_for(database)
        if path is None:
            # Server level with no file selected: a private in-memory catalog connection.
            engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        else:
            cfg = self._config
            url = URL.create(
                "sqlite+aiosqlite",
                database=path.as_uri(),
                query={"mode": "rw", "uri": "true"},  # never create a file implicitly
            )
            engine = create_async_engine(
                url,
                pool_size=cfg.pool_min_size,
                max_overflow=max(cfg.pool_max_size - cfg.pool_min_size, 0),
                pool_timeout=cfg.connect_timeout,
                pool_pre_ping=True,
                connect_args={"timeout": cfg.connect_timeout},
            )
        event.listen(engine.sync_engine, "connect", _on_connect)
        return engine

    async def connect(self) -> None:
        if self._config.database:
            path = self._file_for(None)
            if path is None or not path.exists():
                raise ConnectionFailedError(
                    f"SQLite database '{self._config.database}' does not exist in the data "
                    "directory. Create it first or choose an existing file.",
                    details=self._config.redacted(),
                )
        await super().connect()

    # --- databases = files in the data directory ------------------------------------------

    async def list_databases(self) -> list[DatabaseInfo]:
        active = (self._file_for(self._active_db).name if self._resolve_db(self._active_db) else None)
        files = sorted(
            p.name for p in sqlite_data_dir().iterdir()
            if p.is_file() and p.suffix.lower() in _ALLOWED_SUFFIXES
        )
        return [DatabaseInfo(name=f, is_active=(f == active)) for f in files]

    async def use_database(self, database: str | None) -> None:
        if database:
            path = resolve_sqlite_file(database)
            if not path.exists():
                raise ConnectionFailedError(
                    f"Cannot access database '{database}'.", details={"database": database}
                )
            database = path.name  # canonical file name (extension included)
        await super().use_database(database)

    async def create_database(self, name: str) -> str:
        path = resolve_sqlite_file(name)
        if path.exists():
            raise ConflictError(f"SQLite database '{path.name}' already exists.")
        conn = await aiosqlite.connect(str(path))
        try:
            await conn.execute("PRAGMA journal_mode = WAL")
            await conn.commit()
        finally:
            await conn.close()
        return path.name

    # --- introspection ---------------------------------------------------------------------

    async def list_routines(self, schema: str | None = None) -> list[RoutineInfo]:
        return []  # SQLite has no stored procedures or functions

    _DEFINITION_TYPES: ClassVar[dict[str, str]] = {
        "table": "table", "base table": "table", "view": "view",
        "index": "index", "trigger": "trigger",
    }

    async def get_object_definition(
        self, object_type: str, name: str, schema: str | None = None
    ) -> str | None:
        """SQLite stores every object's original DDL in ``sqlite_master.sql`` — return it
        verbatim (exact, including constraints and comments)."""
        kind = self._DEFINITION_TYPES.get((object_type or "").strip().lower())
        if kind is None:
            return None
        master = "sqlite_temp_master" if (schema or "").lower() == "temp" else "sqlite_master"
        async with self.acquire() as conn:
            row = (
                await conn.execute(
                    text(f"SELECT sql FROM {master} WHERE type = :t AND name = :n"),
                    {"t": kind, "n": name},
                )
            ).first()
        if not row or not row[0]:
            return None
        ddl = str(row[0]).rstrip()
        return ddl if ddl.endswith(";") else ddl + ";"

    # --- cancellation -------------------------------------------------------------------

    async def cancel_running(self) -> None:
        """Interrupt the statement running on the script connection (sqlite3's thread-safe
        ``interrupt``; aiosqlite calls it directly rather than queueing it)."""
        conn = self._script_conn
        if conn is None or conn.closed:
            return
        try:
            raw = await conn.get_raw_connection()
            interrupt = getattr(raw.driver_connection, "interrupt", None)
            if interrupt is not None:
                await interrupt()
        except Exception:  # noqa: BLE001 - the caller releases the connection anyway
            pass
