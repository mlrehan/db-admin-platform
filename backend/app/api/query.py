"""Query execution endpoints (buffered HTTP path).

Runs a statement against a caller-owned live session. The SQL safety layer enforces
role-based restrictions before execution; results are row-capped and JSON-safe. Streaming is
available over WebSocket (see :mod:`app.api.ws_query`).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import get_access_service, get_orchestrator, get_query_engine
from app.auth.dependencies import CurrentUser, require_permissions
from app.auth.roles import Permission
from app.schemas.query import (
    QueryRequest,
    QueryResultOut,
    RunningQueryOut,
    ScriptRequest,
    ScriptResultOut,
    StatementResultOut,
)
from app.services.access_control import AccessControlService
from app.services.orchestrator import ConnectionOrchestrator
from app.services.query_engine import ExecuteResult, QueryEngine, ScriptResult

router = APIRouter(tags=["query"])

_can_use = Depends(require_permissions(Permission.CONNECTION_USE))


def _to_out(result: ExecuteResult) -> QueryResultOut:
    return QueryResultOut(
        query_id=result.query_id,
        columns=[{"name": c.name, "type": c.type_name} for c in result.columns],
        rows=result.rows,
        row_count=result.row_count,
        rows_affected=result.rows_affected,
        execution_ms=result.execution_ms,
        truncated=result.truncated,
        returns_rows=result.returns_rows,
        category=result.category,
        destructive=result.destructive,
    )


@router.post(
    "/sessions/{session_id}/query",
    response_model=QueryResultOut,
    dependencies=[_can_use],
)
async def execute_query(
    session_id: uuid.UUID,
    payload: QueryRequest,
    user: CurrentUser,
    orchestrator: Annotated[ConnectionOrchestrator, Depends(get_orchestrator)],
    engine: Annotated[QueryEngine, Depends(get_query_engine)],
    access: Annotated[AccessControlService, Depends(get_access_service)],
) -> QueryResultOut:
    session = await orchestrator.get_session(session_id, user_id=user.id)
    policy = await access.policy_for(user, session.connection_id)
    result = await engine.execute(
        user=user,
        session=session,
        sql=payload.sql,
        parameters=payload.params,
        max_rows=payload.max_rows,
        policy=policy,
    )
    return _to_out(result)


def _script_out(result: ScriptResult) -> ScriptResultOut:
    return ScriptResultOut(
        success=result.success,
        statements=[
            StatementResultOut(
                sql=s.sql,
                success=s.success,
                returns_rows=s.returns_rows,
                columns=[{"name": c.name, "type": c.type_name} for c in s.columns],
                rows=s.rows,
                row_count=s.row_count,
                rows_affected=s.rows_affected,
                execution_ms=s.execution_ms,
                truncated=s.truncated,
                category=s.category,
                destructive=s.destructive,
                error_code=s.error_code,
                error=s.error,
            )
            for s in result.statements
        ],
    )


@router.post(
    "/sessions/{session_id}/script",
    response_model=ScriptResultOut,
    dependencies=[_can_use],
)
async def execute_script(
    session_id: uuid.UUID,
    payload: ScriptRequest,
    user: CurrentUser,
    orchestrator: Annotated[ConnectionOrchestrator, Depends(get_orchestrator)],
    engine: Annotated[QueryEngine, Depends(get_query_engine)],
    access: Annotated[AccessControlService, Depends(get_access_service)],
) -> ScriptResultOut:
    session = await orchestrator.get_session(session_id, user_id=user.id)
    policy = await access.policy_for(user, session.connection_id)
    result = await engine.execute_script(
        user=user,
        session=session,
        sql=payload.sql,
        parameters=payload.params,
        max_rows=payload.max_rows,
        policy=policy,
    )
    return _script_out(result)


@router.get(
    "/queries/running",
    response_model=list[RunningQueryOut],
    dependencies=[_can_use],
)
async def list_running_queries(
    user: CurrentUser,
    engine: Annotated[QueryEngine, Depends(get_query_engine)],
) -> list[RunningQueryOut]:
    running = await engine.list_running(user_id=user.id)
    return [
        RunningQueryOut(
            query_id=q.id,
            session_id=q.session_id,
            category=q.category,
            started_at=q.started_at.isoformat(),
        )
        for q in running
    ]


@router.post(
    "/queries/{query_id}/cancel",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
    dependencies=[_can_use],
)
async def cancel_query(
    query_id: uuid.UUID,
    user: CurrentUser,
    engine: Annotated[QueryEngine, Depends(get_query_engine)],
) -> None:
    await engine.cancel(query_id, user_id=user.id)
