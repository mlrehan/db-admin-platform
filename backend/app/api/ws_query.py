"""WebSocket query streaming endpoint.

Protocol (JSON messages):

client → server:
    {"action": "execute", "sql": "...", "params": {...}, "batch_size": 500}
    {"action": "cancel"}
    {"action": "ping"}

server → client:
    {"type": "accepted", "query_id": "...", "category": "read"}
    {"type": "columns", "query_id": "...", "columns": [{"name","type"}], "returns_rows": true}
    {"type": "rows", "query_id": "...", "rows": [[...], ...]}
    {"type": "end", "query_id": "...", "row_count": N, "rows_affected": null, ...}
    {"type": "error" | "cancelled" | "pong", ...}

Authentication is via an access token in the ``token`` query parameter (browsers cannot set
WebSocket headers). One query runs at a time per socket; ``cancel`` aborts it.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_orchestrator, get_query_engine
from app.auth.roles import Permission, Role, role_has_permission
from app.auth.tokens import TokenType, decode_token
from app.core.config import Settings, get_settings
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.db.session import get_session
from app.services.access_control import AccessControlService
from app.services.orchestrator import ConnectionOrchestrator
from app.services.query_engine import QueryEngine
from app.services.user_service import UserService

router = APIRouter()
logger = get_logger(__name__)

_WS_AUTH_FAILED = 4401
_WS_FORBIDDEN = 4403
_WS_NOT_FOUND = 4404


async def _authenticate(token: str | None, settings: Settings, session: AsyncSession):
    if not token:
        return None
    try:
        claims = decode_token(settings.security, token, expected_type=TokenType.ACCESS)
        user = await UserService(session).get_by_id(uuid.UUID(claims.subject))
    except (AppError, ValueError):
        return None
    if user is None or not user.is_active or user.token_version != claims.token_version:
        return None
    return user


@router.websocket("/ws/sessions/{session_id}/query")
async def ws_query(
    websocket: WebSocket,
    session_id: uuid.UUID,
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[AsyncSession, Depends(get_session)],
    orchestrator: Annotated[ConnectionOrchestrator, Depends(get_orchestrator)],
    engine: Annotated[QueryEngine, Depends(get_query_engine)],
    token: Annotated[str | None, Query()] = None,
) -> None:
    await websocket.accept()

    user = await _authenticate(token, settings, db)
    if user is None:
        await websocket.send_json({"type": "error", "code": "AUTHENTICATION_ERROR"})
        await websocket.close(code=_WS_AUTH_FAILED)
        return
    if not role_has_permission(Role(user.role), Permission.CONNECTION_USE):
        await websocket.send_json({"type": "error", "code": "AUTHORIZATION_ERROR"})
        await websocket.close(code=_WS_FORBIDDEN)
        return

    try:
        session = await orchestrator.get_session(session_id, user_id=user.id)
    except AppError:
        await websocket.send_json({"type": "error", "code": "NOT_FOUND"})
        await websocket.close(code=_WS_NOT_FOUND)
        return

    # Resolve the caller's access policy once for this socket.
    policy = await AccessControlService(db).policy_for(user, session.connection_id)

    current_task: asyncio.Task[None] | None = None
    current_query_id: dict[str, str | None] = {"id": None}

    async def _run(sql: str, params: dict[str, Any] | None, batch_size: int | None) -> None:
        try:
            async for event in engine.stream(
                user=user, session=session, sql=sql, parameters=params,
                batch_size=batch_size, policy=policy,
            ):
                if event.get("type") == "accepted":
                    current_query_id["id"] = event.get("query_id")
                await websocket.send_json(event)
        except AppError as exc:
            await websocket.send_json(
                {"type": "error", "code": exc.code, "message": exc.message}
            )

    try:
        while True:
            message = await websocket.receive_json()
            action = message.get("action")

            if action == "execute":
                if current_task is not None and not current_task.done():
                    await websocket.send_json(
                        {"type": "error", "code": "BUSY", "message": "A query is already running."}
                    )
                    continue
                sql = message.get("sql")
                if not isinstance(sql, str) or not sql.strip():
                    await websocket.send_json(
                        {"type": "error", "code": "VALIDATION_ERROR", "message": "Missing sql."}
                    )
                    continue
                params = message.get("params") if isinstance(message.get("params"), dict) else None
                batch_size = message.get("batch_size") if isinstance(message.get("batch_size"), int) else None
                current_query_id["id"] = None
                current_task = asyncio.create_task(_run(sql, params, batch_size))

            elif action == "cancel":
                if current_task is not None and not current_task.done():
                    current_task.cancel()
                    try:
                        await current_task
                    except asyncio.CancelledError:
                        pass
                    await websocket.send_json(
                        {"type": "cancelled", "query_id": current_query_id["id"]}
                    )

            elif action == "ping":
                await websocket.send_json({"type": "pong"})

            else:
                await websocket.send_json(
                    {"type": "error", "code": "UNKNOWN_ACTION", "message": str(action)}
                )
    except WebSocketDisconnect:
        pass
    finally:
        if current_task is not None and not current_task.done():
            current_task.cancel()
            try:
                await current_task
            except (asyncio.CancelledError, Exception):
                pass
