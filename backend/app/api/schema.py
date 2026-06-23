"""Schema Explorer endpoints.

Walk the target database's structure over a caller-owned live session:
``schemas → tables → table detail (columns/indexes/foreign keys) → routines``. Requires
``schema:read`` and session ownership.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import (
    get_access_service,
    get_metadata_service,
    get_orchestrator,
    get_query_engine,
)
from app.auth.dependencies import CurrentUser, has_permission, require_permissions
from app.auth.roles import Permission
from app.core.exceptions import AuthorizationError, NotFoundError
from app.services.access_control import AccessControlService
from app.services.audit_sink import QueryAuditEvent
from app.services.query_engine import QueryEngine
from app.db.adapters.metadata import (
    DatabaseInfo,
    RoutineInfo,
    SchemaInfo,
    TableDetail,
    TableInfo,
)
from app.schemas.metadata import (
    ColumnOut,
    CreateDatabaseRequest,
    DatabaseOut,
    ForeignKeyOut,
    IndexOut,
    RoutineOut,
    SchemaOut,
    SwitchDatabaseRequest,
    TableDetailOut,
    TableOut,
)
from app.services.metadata_service import MetadataService
from app.services.orchestrator import ConnectionOrchestrator

router = APIRouter(tags=["schema"])

_can_read = Depends(require_permissions(Permission.SCHEMA_READ))


def _table_out(t: TableInfo) -> TableOut:
    return TableOut(name=t.name, schema_name=t.schema, kind=t.kind, comment=t.comment)


def _detail_out(d: TableDetail) -> TableDetailOut:
    return TableDetailOut(
        table=_table_out(d.table),
        columns=[
            ColumnOut(
                name=c.name, data_type=c.data_type, nullable=c.nullable, default=c.default,
                primary_key=c.primary_key, autoincrement=c.autoincrement, comment=c.comment,
            )
            for c in d.columns
        ],
        primary_key=d.primary_key,
        indexes=[
            IndexOut(name=i.name, columns=i.columns, unique=i.unique, primary=i.primary)
            for i in d.indexes
        ],
        foreign_keys=[
            ForeignKeyOut(
                name=fk.name, columns=fk.columns, referred_schema=fk.referred_schema,
                referred_table=fk.referred_table, referred_columns=fk.referred_columns,
            )
            for fk in d.foreign_keys
        ],
    )


async def _resolve_session(
    orchestrator: ConnectionOrchestrator, session_id: uuid.UUID, user_id: uuid.UUID
):
    return await orchestrator.get_session(session_id, user_id=user_id)


@router.get(
    "/sessions/{session_id}/databases",
    response_model=list[DatabaseOut],
    dependencies=[_can_read],
)
async def list_databases(
    session_id: uuid.UUID,
    user: CurrentUser,
    orchestrator: Annotated[ConnectionOrchestrator, Depends(get_orchestrator)],
    metadata: Annotated[MetadataService, Depends(get_metadata_service)],
    access: Annotated[AccessControlService, Depends(get_access_service)],
) -> list[DatabaseOut]:
    session = await _resolve_session(orchestrator, session_id, user.id)
    policy = await access.policy_for(user, session.connection_id)
    databases: list[DatabaseInfo] = await metadata.list_databases(session)
    return [
        DatabaseOut(name=d.name, is_active=d.is_active)
        for d in databases
        if policy.database_allowed(d.name)
    ]


@router.post(
    "/sessions/{session_id}/database",
    response_model=DatabaseOut,
    dependencies=[_can_read],
)
async def switch_database(
    session_id: uuid.UUID,
    payload: SwitchDatabaseRequest,
    user: CurrentUser,
    orchestrator: Annotated[ConnectionOrchestrator, Depends(get_orchestrator)],
    metadata: Annotated[MetadataService, Depends(get_metadata_service)],
    access: Annotated[AccessControlService, Depends(get_access_service)],
) -> DatabaseOut:
    session = await _resolve_session(orchestrator, session_id, user.id)
    policy = await access.policy_for(user, session.connection_id)
    if payload.database and not policy.database_allowed(payload.database):
        raise AuthorizationError(
            f"Access denied to database '{payload.database}'.", code="ACCESS_DENIED"
        )
    await metadata.use_database(session, payload.database)
    return DatabaseOut(name=session.adapter.active_database or "", is_active=True)


@router.post(
    "/sessions/{session_id}/databases",
    response_model=DatabaseOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_can_read],
)
async def create_database(
    session_id: uuid.UUID,
    payload: CreateDatabaseRequest,
    user: CurrentUser,
    orchestrator: Annotated[ConnectionOrchestrator, Depends(get_orchestrator)],
    metadata: Annotated[MetadataService, Depends(get_metadata_service)],
    access: Annotated[AccessControlService, Depends(get_access_service)],
    engine: Annotated[QueryEngine, Depends(get_query_engine)],
) -> DatabaseOut:
    """Create a new database on the connection's server.

    Admins always may; a non-admin may only when an admin granted them broad CREATE rights on
    the connection (default-deny). The action is audited like any other DDL.
    """
    session = await _resolve_session(orchestrator, session_id, user.id)
    policy = await access.policy_for(user, session.connection_id)
    if not policy.can_create_database():
        raise AuthorizationError(
            "You have not been granted permission to create databases on this connection.",
            code="ACCESS_DENIED",
        )
    started = QueryAuditEvent.now()
    try:
        created = await metadata.create_database(session, payload.name)
    except Exception as exc:
        await engine.audit_action(
            user=user, session=session, statement=f"CREATE DATABASE {payload.name}",
            success=False, started=started, error=str(exc),
            error_code=getattr(exc, "code", "DATABASE_CREATE_FAILED"),
        )
        raise
    await engine.audit_action(
        user=user, session=session, statement=f"CREATE DATABASE {created}",
        success=True, started=started,
    )
    return DatabaseOut(name=created, is_active=False)


@router.get(
    "/sessions/{session_id}/schemas",
    response_model=list[SchemaOut],
    dependencies=[_can_read],
)
async def list_schemas(
    session_id: uuid.UUID,
    user: CurrentUser,
    orchestrator: Annotated[ConnectionOrchestrator, Depends(get_orchestrator)],
    metadata: Annotated[MetadataService, Depends(get_metadata_service)],
) -> list[SchemaOut]:
    session = await _resolve_session(orchestrator, session_id, user.id)
    schemas: list[SchemaInfo] = await metadata.list_schemas(session)
    # Non-admins never see engine-internal/system schemas (pg_catalog, sys, information_schema,
    # mysql, …). Admins keep full visibility so they can administer everything.
    is_admin = has_permission(user, Permission.USER_MANAGE)
    is_system = getattr(session.adapter, "is_system_schema", lambda _n: False)
    return [
        SchemaOut(name=s.name, is_default=s.is_default)
        for s in schemas
        if is_admin or not is_system(s.name)
    ]


@router.get(
    "/sessions/{session_id}/tables",
    response_model=list[TableOut],
    dependencies=[_can_read],
)
async def list_tables(
    session_id: uuid.UUID,
    user: CurrentUser,
    orchestrator: Annotated[ConnectionOrchestrator, Depends(get_orchestrator)],
    metadata: Annotated[MetadataService, Depends(get_metadata_service)],
    access: Annotated[AccessControlService, Depends(get_access_service)],
    schema: Annotated[str | None, Query()] = None,
) -> list[TableOut]:
    session = await _resolve_session(orchestrator, session_id, user.id)
    policy = await access.policy_for(user, session.connection_id)
    db = session.adapter.active_database
    tables = await metadata.list_tables(session, schema)
    return [_table_out(t) for t in tables if policy.table_visible(db, t.schema, t.name)]


@router.get(
    "/sessions/{session_id}/tables/{table}",
    response_model=TableDetailOut,
    dependencies=[_can_read],
)
async def describe_table(
    session_id: uuid.UUID,
    table: str,
    user: CurrentUser,
    orchestrator: Annotated[ConnectionOrchestrator, Depends(get_orchestrator)],
    metadata: Annotated[MetadataService, Depends(get_metadata_service)],
    access: Annotated[AccessControlService, Depends(get_access_service)],
    schema: Annotated[str | None, Query()] = None,
) -> TableDetailOut:
    session = await _resolve_session(orchestrator, session_id, user.id)
    policy = await access.policy_for(user, session.connection_id)
    if not policy.table_visible(session.adapter.active_database, schema, table):
        raise NotFoundError("Table not found.")
    detail = await metadata.describe_table(session, table, schema)
    return _detail_out(detail)


@router.get(
    "/sessions/{session_id}/routines",
    response_model=list[RoutineOut],
    dependencies=[_can_read],
)
async def list_routines(
    session_id: uuid.UUID,
    user: CurrentUser,
    orchestrator: Annotated[ConnectionOrchestrator, Depends(get_orchestrator)],
    metadata: Annotated[MetadataService, Depends(get_metadata_service)],
    schema: Annotated[str | None, Query()] = None,
) -> list[RoutineOut]:
    session = await _resolve_session(orchestrator, session_id, user.id)
    routines: list[RoutineInfo] = await metadata.list_routines(session, schema)
    return [RoutineOut(name=r.name, kind=r.kind, return_type=r.return_type) for r in routines]
