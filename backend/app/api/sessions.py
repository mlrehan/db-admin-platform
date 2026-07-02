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
from app.auth.dependencies import (
    CurrentUser,
    get_user_service,
    has_permission,
    require_permissions,
)
from app.auth.roles import Permission
from app.core.exceptions import NotFoundError
from app.schemas.session import (
    AdminSessionRead,
    CloseAllSessionsRequest,
    CloseSessionsResult,
    OpenSessionRequest,
    SessionRead,
)
from app.services.access_control import AccessControlService
from app.services.connection_service import ConnectionService
from app.services.orchestrator import ConnectionOrchestrator, LiveSession
from app.services.user_service import UserService

router = APIRouter(tags=["sessions"])

_can_use = Depends(require_permissions(Permission.CONNECTION_USE))
_admin_only = Depends(require_permissions(Permission.USER_MANAGE))

# A session with no activity beyond this many seconds is flagged "idle" in the admin view.
_IDLE_THRESHOLD_SECONDS = 300


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
    # If a non-admin is granted exactly one specific database on a server-level connection,
    # point the new session at it immediately. Otherwise their first query would run against
    # the server's default database (active_database=None) and be denied by the access policy,
    # even though they can see the granted database in the picker.
    if not is_admin and session.adapter.active_database is None:
        granted = await access.granted_databases(user, conn.id)
        if len(granted) == 1:
            try:
                await session.adapter.use_database(next(iter(granted)))
            except Exception:  # noqa: BLE001 - best-effort; session still opens
                pass
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


# --- admin oversight of all users' sessions ----------------------------------------------


@router.get("/admin/all", response_model=list[AdminSessionRead], dependencies=[_admin_only])
async def list_all_sessions(
    orchestrator: Annotated[ConnectionOrchestrator, Depends(get_orchestrator)],
    connections: Annotated[ConnectionService, Depends(get_connection_service)],
    users: Annotated[UserService, Depends(get_user_service)],
) -> list[AdminSessionRead]:
    """Every live session across all users (admin oversight): server name, engine, database,
    idle status. Enriched with the owning user's email and the connection's name."""
    sessions = await orchestrator.list_all_sessions()
    # Resolve owner emails and connection names once per distinct id.
    email_cache: dict[uuid.UUID, str | None] = {}
    name_cache: dict[uuid.UUID, str | None] = {}
    out: list[AdminSessionRead] = []
    for s in sessions:
        if s.user_id not in email_cache:
            u = await users.get_by_id(s.user_id)
            email_cache[s.user_id] = getattr(u, "email", None)
        if s.connection_id not in name_cache:
            c = await connections.get(s.connection_id)
            name_cache[s.connection_id] = getattr(c, "name", None)
        idle = s.idle_seconds()
        out.append(
            AdminSessionRead(
                id=s.id,
                user_id=s.user_id,
                user_email=email_cache[s.user_id],
                connection_id=s.connection_id,
                connection_name=name_cache[s.connection_id],
                engine=s.adapter.engine,
                database=s.adapter.active_database,
                created_at=s.created_at,
                last_used_at=s.last_used_at,
                idle_seconds=round(idle, 2),
                idle=idle > _IDLE_THRESHOLD_SECONDS,
                connected=s.adapter.is_connected,
            )
        )
    return out


@router.post("/admin/close-all", response_model=CloseSessionsResult, dependencies=[_admin_only])
async def close_all_sessions(
    payload: CloseAllSessionsRequest,
    user: CurrentUser,
    orchestrator: Annotated[ConnectionOrchestrator, Depends(get_orchestrator)],
) -> CloseSessionsResult:
    """Disconnect every live session. By default the admin's own sessions are kept open so they
    aren't logged out of the tool they're administering."""
    exclude: set[uuid.UUID] = set()
    if payload.exclude_self:
        own = await orchestrator.list_sessions(user_id=user.id)
        exclude = {s.id for s in own}
    closed = await orchestrator.close_all_sessions(exclude=exclude)
    return CloseSessionsResult(closed=closed)


@router.delete(
    "/admin/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
    dependencies=[_admin_only],
)
async def admin_close_session(
    session_id: uuid.UUID,
    orchestrator: Annotated[ConnectionOrchestrator, Depends(get_orchestrator)],
) -> None:
    """Disconnect any user's session by id (admin)."""
    await orchestrator.close_any_session(session_id)


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
