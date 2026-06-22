"""Connection service — persistence + encryption for saved connections.

Owns all access to the ``connections`` table. Passwords are encrypted on the way in (via the
envelope :class:`~app.security.encryption.CredentialCipher`) and only ever decrypted here, to
build the short-lived :class:`~app.db.adapters.base.ConnectionConfig` the orchestrator needs.
The decrypted password never leaves the server and never appears in a response schema.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import ConnectionSettings
from app.core.exceptions import ConflictError, NotFoundError
from app.db.adapters.base import ConnectionConfig
from app.db.engines import EngineType
from app.models.connection import Connection
from app.schemas.connection import ConnectionCreate, ConnectionUpdate
from app.security.encryption import CredentialCipher


class ConnectionService:
    def __init__(
        self,
        session: AsyncSession,
        cipher: CredentialCipher,
        conn_settings: ConnectionSettings | None = None,
    ) -> None:
        self._session = session
        self._cipher = cipher
        self._conn_settings = conn_settings or ConnectionSettings()

    # --- reads ---------------------------------------------------------------------------

    async def get(self, connection_id: uuid.UUID) -> Connection | None:
        return await self._session.get(Connection, connection_id)

    async def get_owned(
        self, connection_id: uuid.UUID, owner_id: uuid.UUID, *, allow_any: bool = False
    ) -> Connection:
        """Fetch a connection, enforcing ownership unless ``allow_any`` (admin)."""
        conn = await self.get(connection_id)
        if conn is None or (not allow_any and conn.owner_id != owner_id):
            # Same 404 whether missing or not-owned: don't leak existence to non-owners.
            raise NotFoundError("Connection not found.")
        return conn

    async def list_for_owner(
        self, owner_id: uuid.UUID, *, limit: int = 100, offset: int = 0
    ) -> list[Connection]:
        result = await self._session.execute(
            select(Connection)
            .where(Connection.owner_id == owner_id)
            .order_by(Connection.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def get_by_ids(self, ids: set[uuid.UUID]) -> list[Connection]:
        if not ids:
            return []
        result = await self._session.execute(
            select(Connection).where(Connection.id.in_(ids))
        )
        return list(result.scalars().all())

    async def list_all(self, *, limit: int = 100, offset: int = 0) -> list[Connection]:
        result = await self._session.execute(
            select(Connection)
            .order_by(Connection.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    # --- writes --------------------------------------------------------------------------

    async def create(self, *, owner_id: uuid.UUID, data: ConnectionCreate) -> Connection:
        if await self._name_taken(owner_id, data.name):
            raise ConflictError("A connection with this name already exists.")
        conn = Connection(
            owner_id=owner_id,
            name=data.name,
            engine=data.engine,
            host=data.host,
            port=data.port if data.port is not None else data.engine.default_port,
            database=data.database,
            username=data.username,
            encrypted_credentials=self._cipher.encrypt(data.password.get_secret_value()),
            ssl_mode=data.ssl_mode,
            options=data.options,
        )
        self._session.add(conn)
        await self._session.flush()
        return conn

    async def update(self, conn: Connection, data: ConnectionUpdate) -> Connection:
        if data.name is not None and data.name != conn.name:
            if await self._name_taken(conn.owner_id, data.name):
                raise ConflictError("A connection with this name already exists.")
            conn.name = data.name
        for attr in ("host", "port", "database", "username", "ssl_mode", "options", "is_active"):
            value = getattr(data, attr)
            if value is not None:
                setattr(conn, attr, value)
        if data.password is not None:
            conn.encrypted_credentials = self._cipher.encrypt(data.password.get_secret_value())
        await self._session.flush()
        return conn

    async def delete(self, conn: Connection) -> None:
        await self._session.delete(conn)
        await self._session.flush()

    # --- resolution for the orchestrator -------------------------------------------------

    def resolve_config(self, conn: Connection) -> ConnectionConfig:
        """Decrypt and assemble the runtime config for an adapter. Server-side only.

        Pool sizing and connect timeout are taken from the orchestrator settings so each live
        session gets a correctly-sized private pool.
        """
        s = self._conn_settings
        return ConnectionConfig(
            engine=EngineType(conn.engine),
            host=conn.host,
            port=conn.port,
            database=conn.database,
            username=conn.username,
            password=self._cipher.decrypt(conn.encrypted_credentials),
            options=dict(conn.options or {}),
            ssl_mode=conn.ssl_mode,
            connect_timeout=s.connect_timeout_seconds,
            pool_min_size=s.session_pool_min_size,
            pool_max_size=s.session_pool_max_size,
        )

    # --- helpers -------------------------------------------------------------------------

    async def _name_taken(self, owner_id: uuid.UUID, name: str) -> bool:
        result = await self._session.execute(
            select(Connection.id).where(
                Connection.owner_id == owner_id, Connection.name == name
            )
        )
        return result.first() is not None
