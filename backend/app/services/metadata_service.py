"""Metadata service — schema introspection over a live session.

A thin orchestration layer over the session's adapter, and the single place where metadata is
**cached**: every read goes through the shared :data:`~app.services.metadata_cache.metadata_cache`
keyed by ``(connection, database)``, so concurrent users of the same connection share one
catalog round trip instead of each re-querying the target database. Writes that change the
structure (create database, DDL scripts) invalidate the connection's entries.
"""

from __future__ import annotations

from app.db.adapters.metadata import (
    Catalog,
    DatabaseInfo,
    RelationshipGraph,
    RoutineInfo,
    SchemaInfo,
    TableDetail,
    TableInfo,
)
from app.services.metadata_cache import MetadataCache, metadata_cache, scope_key, server_identity
from app.services.orchestrator import LiveSession


def _norm(value: str | None) -> str:
    return (value or "").lower()


class MetadataService:
    def __init__(self, cache: MetadataCache | None = None) -> None:
        self._cache = cache or metadata_cache

    def _scope(self, session: LiveSession) -> tuple:
        self._link(session)
        return scope_key(session.connection_id, session.adapter.active_database)

    def _link(self, session: LiveSession) -> None:
        # Remember which server this connection targets so DDL through ANOTHER connection to
        # the same server also refreshes this connection's cached metadata.
        self._cache.link(session.connection_id, server_identity(session.adapter.config))

    def invalidate(self, session: LiveSession) -> None:
        """Forget everything cached for the session's connection (all databases) and for any
        other connection to the same server."""
        self._cache.invalidate(session.connection_id, server=server_identity(session.adapter.config))

    async def list_databases(self, session: LiveSession) -> list[DatabaseInfo]:
        session.touch()
        # The database list is per connection (not per database); the active flag is applied
        # per session afterwards since sessions may target different databases.
        self._link(session)
        cached = await self._cache.get_or_load(
            (session.connection_id, "*", "databases"),
            session.adapter.list_databases,
        )
        active = _norm(session.adapter.active_database)
        return [DatabaseInfo(name=d.name, is_active=(_norm(d.name) == active)) for d in cached]

    async def use_database(self, session: LiveSession, database: str | None) -> None:
        session.touch()
        await session.adapter.use_database(database)

    async def create_database(self, session: LiveSession, name: str) -> str:
        session.touch()
        create = getattr(session.adapter, "create_database", None)
        if create is None:  # pragma: no cover - all real adapters support it
            raise NotImplementedError("This engine does not support creating databases.")
        created = await create(name)
        self.invalidate(session)  # the database list changed
        return created

    async def list_schemas(self, session: LiveSession) -> list[SchemaInfo]:
        session.touch()
        return await self._cache.get_or_load(
            self._scope(session) + ("schemas",), session.adapter.list_schemas
        )

    async def list_tables(
        self, session: LiveSession, schema: str | None = None
    ) -> list[TableInfo]:
        session.touch()
        return await self._cache.get_or_load(
            self._scope(session) + ("tables", _norm(schema)),
            lambda: session.adapter.list_tables(schema),
        )

    async def describe_table(
        self, session: LiveSession, table: str, schema: str | None = None
    ) -> TableDetail:
        session.touch()
        return await self._cache.get_or_load(
            self._scope(session) + ("table", _norm(schema), _norm(table)),
            lambda: session.adapter.describe_table(table, schema),
        )

    async def list_routines(
        self, session: LiveSession, schema: str | None = None
    ) -> list[RoutineInfo]:
        session.touch()
        return await self._cache.get_or_load(
            self._scope(session) + ("routines", _norm(schema)),
            lambda: session.adapter.list_routines(schema),
        )

    async def get_relationships(self, session: LiveSession) -> RelationshipGraph:
        """Every FK of the active database with cardinality + junction tables (diagram)."""
        session.touch()
        getter = getattr(session.adapter, "get_relationships", None)
        if getter is None:  # pragma: no cover
            return RelationshipGraph(relationships=(), junctions=())
        return await self._cache.get_or_load(self._scope(session) + ("relationships",), getter)

    async def get_catalog(self, session: LiveSession) -> Catalog:
        """The bulk IntelliSense catalog (all tables/views + columns + routines)."""
        session.touch()
        getter = getattr(session.adapter, "get_catalog", None)
        if getter is None:  # pragma: no cover - every real adapter implements it
            return Catalog(default_schema=None, objects=(), routines=())
        return await self._cache.get_or_load(self._scope(session) + ("catalog",), getter)
