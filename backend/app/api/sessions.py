"""Live database-session endpoints (Connection Orchestrator surface).

Opening a session creates an isolated, per-user adapter against a saved connection. Sessions
are owner-scoped: a user can only see and close their own. Requires ``connection:use``.

Actual statement execution over a session is the Query Engine (Phase 5); these endpoints
manage the session lifecycle. Opening a session for an engine whose adapter is not yet
registered (pre-Phase 4) returns a typed ``UNSUPPORTED_ENGINE`` error.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import get_access_service, get_connection_service, get_orchestrator
from app.auth.dependencies import CurrentUser, has_permission, require_permissions
from app.auth.roles import Permission
from app.core.exceptions import NotFoundError
from app.schemas.session import OpenSessionRequest, SessionRead
from app.services.access_control import AccessControlService
from app.services.connection_service import ConnectionService
from app.services.orchestrator import ConnectionOrchestrator, LiveSession

router = APIRouter(tags=["sessions"])

_can_use = Depends(require_permissions(Permission.CONNECTION_USE))


def _to_read(session: LiveSession, *, can_create_database: bool = False) -> SessionRead:
    return SessionRead(
        id=session.id,
        connection_id=session.connection_id,
        engine=session.adapter.engine,
        created_at=session.created_at,
        last_used_at=session.last_used_at,
        idle_seconds=round(session.idle_seconds(), 2),
        connected=session.adapter.is_connected,
        active_database=session.adapter.active_database,
        can_create_database=can_create_database,
    )


async def _can_create_db(access: AccessControlService, user: CurrentUser, connection_id) -> bool:
    policy = await access.policy_for(user, connection_id)
    return policy.can_create_database()


@router.post(
    "",
    response_model=SessionRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_can_use],
)
async def open_session(
    payload: OpenSessionRequest,
    user: CurrentUser,
    service: Annotated[ConnectionService, Depends(get_connection_service)],
    orchestrator: Annotated[ConnectionOrchestrator, Depends(get_orchestrator)],
    access: Annotated[AccessControlService, Depends(get_access_service)],
) -> SessionRead:
    # A user may open a session on a connection they OWN, or (admin) any connection, or one
    # that has been SHARED with them via an access grant. Their access grants then govern what
    # they can do (databases / tables / operations).
    conn = await service.get(payload.connection_id)
    is_admin = has_permission(user, Permission.USER_MANAGE)
    if conn is None or (
        conn.owner_id != user.id
        and not is_admin
        and not await access.can_access_connection(user, conn.id)
    ):
        raise NotFoundError("Connection not found.")
    config = service.resolve_config(conn)
    session = await orchestrator.open_session(
        user_id=user.id, connection_id=conn.id, config=config
    )
    return _to_read(session, can_create_database=await _can_create_db(access, user, conn.id))


@router.get("", response_model=list[SessionRead], dependencies=[_can_use])
async def list_sessions(
    user: CurrentUser,
    orchestrator: Annotated[ConnectionOrchestrator, Depends(get_orchestrator)],
    access: Annotated[AccessControlService, Depends(get_access_service)],
) -> list[SessionRead]:
    sessions = await orchestrator.list_sessions(user_id=user.id)
    # Cache per-connection capability so N sessions on one connection cost one policy lookup.
    cache: dict[uuid.UUID, bool] = {}
    out: list[SessionRead] = []
    for s in sessions:
        if s.connection_id not in cache:
            cache[s.connection_id] = await _can_create_db(access, user, s.connection_id)
        out.append(_to_read(s, can_create_database=cache[s.connection_id]))
    return out


@router.get("/{session_id}", response_model=SessionRead, dependencies=[_can_use])
async def get_session_info(
    session_id: uuid.UUID,
    user: CurrentUser,
    orchestrator: Annotated[ConnectionOrchestrator, Depends(get_orchestrator)],
    access: Annotated[AccessControlService, Depends(get_access_service)],
) -> SessionRead:
    session = await orchestrator.get_session(session_id, user_id=user.id)
    return _to_read(
        session, can_create_database=await _can_create_db(access, user, session.connection_id)
    )


@router.delete(
    "/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
    dependencies=[_can_use],
)
async def close_session(
    session_id: uuid.UUID,
    user: CurrentUser,
    orchestrator: Annotated[ConnectionOrchestrator, Depends(get_orchestrator)],
) -> None:
    await orchestrator.close_session(session_id, user_id=user.id)
