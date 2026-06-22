"""Metadata service — schema introspection over a live session.

A thin orchestration layer over the session's adapter. It exists so the API layer depends on
a service (not directly on adapters), keeping a single place to later add cross-cutting
concerns such as caching or result shaping without touching endpoints.
"""

from __future__ import annotations

from app.db.adapters.metadata import (
    DatabaseInfo,
    RoutineInfo,
    SchemaInfo,
    TableDetail,
    TableInfo,
)
from app.services.orchestrator import LiveSession


class MetadataService:
    async def list_databases(self, session: LiveSession) -> list[DatabaseInfo]:
        session.touch()
        return await session.adapter.list_databases()

    async def use_database(self, session: LiveSession, database: str | None) -> None:
        session.touch()
        await session.adapter.use_database(database)

    async def list_schemas(self, session: LiveSession) -> list[SchemaInfo]:
        session.touch()
        return await session.adapter.list_schemas()

    async def list_tables(
        self, session: LiveSession, schema: str | None = None
    ) -> list[TableInfo]:
        session.touch()
        return await session.adapter.list_tables(schema)

    async def describe_table(
        self, session: LiveSession, table: str, schema: str | None = None
    ) -> TableDetail:
        session.touch()
        return await session.adapter.describe_table(table, schema)

    async def list_routines(
        self, session: LiveSession, schema: str | None = None
    ) -> list[RoutineInfo]:
        session.touch()
        return await session.adapter.list_routines(schema)
