"""Shared API dependencies for Phase 3 (connections & live sessions)."""

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


def get_connection_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ConnectionService:
    return ConnectionService(
        session, get_credential_cipher(settings), settings.connections
    )


# HTTPConnection is the common base of Request (HTTP) and WebSocket, so these dependencies
# resolve correctly for both REST and WebSocket routes.
def get_orchestrator(conn: HTTPConnection) -> ConnectionOrchestrator:
    orchestrator = getattr(conn.app.state, "orchestrator", None)
    if orchestrator is None:  # pragma: no cover - misconfiguration guard
        raise ServiceUnavailableError("Connection orchestrator is not initialized.")
    return orchestrator


def get_query_engine(conn: HTTPConnection) -> QueryEngine:
    engine = getattr(conn.app.state, "query_engine", None)
    if engine is None:  # pragma: no cover - misconfiguration guard
        raise ServiceUnavailableError("Query engine is not initialized.")
    return engine


def get_metadata_service() -> MetadataService:
    return MetadataService()


def get_audit_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AuditService:
    return AuditService(session)


def get_access_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AccessControlService:
    return AccessControlService(session)
