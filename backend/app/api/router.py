"""Aggregate API router.

Each phase mounts its feature routers here. Health is intentionally mounted at the app root
(not under the versioned API prefix) so orchestrators can probe it without version coupling;
that wiring lives in :func:`app.main.create_app`. This module aggregates the *versioned*
feature routers (auth, connections, query, metadata, admin) as later phases add them.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api import (
    access,
    audit,
    auth,
    connections,
    query,
    schema,
    sessions,
    users,
    ws_query,
)

api_router = APIRouter()

# Phase 2: authentication and user administration.
api_router.include_router(auth.router, prefix="/auth")
api_router.include_router(users.router, prefix="/users")

# Phase 3: saved connections and live database sessions.
api_router.include_router(connections.router, prefix="/connections")
api_router.include_router(sessions.router, prefix="/sessions")

# Phase 5: query execution (buffered HTTP) and streaming (WebSocket).
api_router.include_router(query.router)
api_router.include_router(ws_query.router)

# Phase 6: schema introspection and immutable audit log.
api_router.include_router(schema.router)
api_router.include_router(audit.router, prefix="/audit")

# Upgrade 2: granular access control (database/table/operation grants).
api_router.include_router(access.router, prefix="/access")
