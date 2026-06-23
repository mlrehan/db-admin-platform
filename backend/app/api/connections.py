"""Saved-connection management endpoints.

Read access requires ``connection:use``; create/update/delete require ``connection:manage``.
Connections are owner-scoped — non-admins only see and mutate their own; Admins (who hold
``user:manage``) may act across owners. Credentials are never returned.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import get_access_service, get_connection_service, get_orchestrator
from app.auth.dependencies import CurrentUser, has_permission, require_permissions
from app.auth.roles import Permission
from app.db.adapters.registry import create_adapter
from app.models.connection import Connection
from app.schemas.connection import (
    ConnectionCreate,
    ConnectionDatabasesResponse,
    ConnectionRead,
    ConnectionTablesResponse,
    ConnectionTestResponse,
    ConnectionUpdate,
    GrantTableOut,
)
from app.services.access_control import AccessControlService
from app.services.connection_service import ConnectionService
from app.services.orchestrator import ConnectionOrchestrator

router = APIRouter(tags=["connections"])

_can_use = Depends(require_permissions(Permission.CONNECTION_USE))
_can_manage = Depends(require_permissions(Permission.CONNECTION_MANAGE))


def _is_admin(user: CurrentUser) -> bool:
    return has_permission(user, Permission.USER_MANAGE)


@router.get("", response_model=list[ConnectionRead], dependencies=[_can_use])
async def list_connections(
    user: CurrentUser,
    service: Annotated[ConnectionService, Depends(get_connection_service)],
    access: Annotated[AccessControlService, Depends(get_access_service)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    all_owners: Annotated[bool, Query(description="Admin: list across all owners")] = False,
) -> list[Connection]:
    if all_owners and _is_admin(user):
        return await service.list_all(limit=limit, offset=offset)
    owned = await service.list_for_owner(user.id, limit=limit, offset=offset)
    # Also include connections shared with this user via access grants.
    owned_ids = {c.id for c in owned}
    shared_ids = await access.granted_connection_ids(user)
    shared = await service.get_by_ids(shared_ids - owned_ids)
    return [*owned, *shared]


@router.post(
    "",
    response_model=ConnectionRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_can_manage],
)
async def create_connection(
    payload: ConnectionCreate,
    user: CurrentUser,
    service: Annotated[ConnectionService, Depends(get_connection_service)],
) -> Connection:
    return await service.create(owner_id=user.id, data=payload)


@router.get("/{connection_id}", response_model=ConnectionRead, dependencies=[_can_use])
async def get_connection(
    connection_id: uuid.UUID,
    user: CurrentUser,
    service: Annotated[ConnectionService, Depends(get_connection_service)],
) -> Connection:
    return await service.get_owned(connection_id, user.id, allow_any=_is_admin(user))


@router.patch("/{connection_id}", response_model=ConnectionRead, dependencies=[_can_manage])
async def update_connection(
    connection_id: uuid.UUID,
    payload: ConnectionUpdate,
    user: CurrentUser,
    service: Annotated[ConnectionService, Depends(get_connection_service)],
) -> Connection:
    conn = await service.get_owned(connection_id, user.id, allow_any=_is_admin(user))
    return await service.update(conn, payload)


@router.delete(
    "/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
    dependencies=[_can_manage],
)
async def delete_connection(
    connection_id: uuid.UUID,
    user: CurrentUser,
    service: Annotated[ConnectionService, Depends(get_connection_service)],
) -> None:
    conn = await service.get_owned(connection_id, user.id, allow_any=_is_admin(user))
    await service.delete(conn)


@router.post(
    "/{connection_id}/test",
    response_model=ConnectionTestResponse,
    dependencies=[_can_use],
)
async def test_connection(
    connection_id: uuid.UUID,
    user: CurrentUser,
    service: Annotated[ConnectionService, Depends(get_connection_service)],
    orchestrator: Annotated[ConnectionOrchestrator, Depends(get_orchestrator)],
) -> ConnectionTestResponse:
    """Validate connectivity to the saved connection and report the result.

    ``test_connection`` is self-contained (it manages its own throwaway engine) and never
    raises for connectivity problems — those come back as ``ok=False`` with a redacted
    message. Resolving an engine with no registered adapter raises ``UNSUPPORTED_ENGINE``.
    """
    conn = await service.get_owned(connection_id, user.id, allow_any=_is_admin(user))
    config = service.resolve_config(conn)
    adapter = create_adapter(config)  # raises UNSUPPORTED_ENGINE if unregistered
    result = await adapter.test_connection()
    await adapter.close()
    return ConnectionTestResponse(
        ok=result.ok,
        message=result.message,
        server_version=result.server_version,
        latency_ms=result.latency_ms,
    )


# Cap how many tables we enumerate for the grant picker (keeps the response small on large
# databases; the admin can still type any table name by hand).
_MAX_PICKER_TABLES = 2000


@router.get(
    "/{connection_id}/databases",
    response_model=ConnectionDatabasesResponse,
    dependencies=[_can_use],
)
async def list_connection_databases(
    connection_id: uuid.UUID,
    user: CurrentUser,
    service: Annotated[ConnectionService, Depends(get_connection_service)],
) -> ConnectionDatabasesResponse:
    """List the databases on a saved connection's server, for the access-grant picker.

    Read-only and self-contained (a throwaway adapter, like ``test_connection``). System
    databases are already excluded by the adapter's ``hidden_databases``."""
    conn = await service.get_owned(connection_id, user.id, allow_any=_is_admin(user))
    adapter = create_adapter(service.resolve_config(conn))
    try:
        databases = await adapter.list_databases()
    finally:
        await adapter.close()
    return ConnectionDatabasesResponse(databases=[d.name for d in databases])


@router.get(
    "/{connection_id}/tables",
    response_model=ConnectionTablesResponse,
    dependencies=[_can_use],
)
async def list_connection_tables(
    connection_id: uuid.UUID,
    user: CurrentUser,
    service: Annotated[ConnectionService, Depends(get_connection_service)],
    database: Annotated[str | None, Query(max_length=255)] = None,
) -> ConnectionTablesResponse:
    """List tables (across non-system schemas) in one database, for the access-grant picker."""
    conn = await service.get_owned(connection_id, user.id, allow_any=_is_admin(user))
    adapter = create_adapter(service.resolve_config(conn))
    is_system = getattr(adapter, "is_system_schema", lambda _n: False)
    tables: list[GrantTableOut] = []
    try:
        if database:
            await adapter.use_database(database)
        for schema in await adapter.list_schemas():
            if is_system(schema.name):
                continue
            for t in await adapter.list_tables(schema.name):
                tables.append(GrantTableOut(schema_name=t.schema, name=t.name))
                if len(tables) >= _MAX_PICKER_TABLES:
                    break
            if len(tables) >= _MAX_PICKER_TABLES:
                break
    finally:
        await adapter.close()
    return ConnectionTablesResponse(tables=tables)
