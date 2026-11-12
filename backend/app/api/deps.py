"""Shared API dependencies for Phase 3 (connections & live sessions).

These providers are ``async def`` on purpose even though they never await: FastAPI runs a
plain ``def`` dependency in a worker thread (anyio pool, 40 tokens by default), so ~20
trivial providers per request became 20 thread hops — measurable queueing with 100
concurrent users. They do no blocking I/O, so running them inline on the loop is free.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import HTTPConnection

from app.core.config import Settings, get_settings
from app.core.exceptions import ServiceUnavailableError
from app.db.session import get_session
from app.security.encryption import get_credential_cipher
from app.services.access_control import AccessControlService
from app.services.audit_service import AuditService
from app.services.connection_service import ConnectionService
from app.services.metadata_service import MetadataService
from app.services.orchestrator import ConnectionOrchestrator
from app.services.query_engine import QueryEngine


async def get_connection_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ConnectionService:
    return ConnectionService(
        session, get_credential_cipher(settings), settings.connections
    )


# HTTPConnection is the common base of Request (HTTP) and WebSocket, so these dependencies
# resolve correctly for both REST and WebSocket routes.
async def get_orchestrator(conn: HTTPConnection) -> ConnectionOrchestrator:
    orchestrator = getattr(conn.app.state, "orchestrator", None)
    if orchestrator is None:  # pragma: no cover - misconfiguration guard
        raise ServiceUnavailableError("Connection orchestrator is not initialized.")
    return orchestrator


async def get_query_engine(conn: HTTPConnection) -> QueryEngine:
    engine = getattr(conn.app.state, "query_engine", None)
    if engine is None:  # pragma: no cover - misconfiguration guard
        raise ServiceUnavailableError("Query engine is not initialized.")
    return engine


async def get_metadata_service() -> MetadataService:
    return MetadataService()


async def get_audit_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AuditService:
    return AuditService(session)


async def get_access_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AccessControlService:
    return AccessControlService(session)
