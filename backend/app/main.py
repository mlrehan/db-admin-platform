"""FastAPI application factory and process entrypoint.

``create_app`` wires the whole HTTP surface: logging, lifespan (engine init/dispose),
middleware, exception handlers, health probes, and the versioned API router. Business
endpoints are added by later phases through :data:`app.api.router.api_router`.

Run locally with::

    python -m app.main
    # or
    uvicorn app.main:app --reload
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health
from app.api.errors import register_exception_handlers
from app.api.middleware import RequestContextMiddleware
from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.db import session as db_session
from app.db.adapters.builtin import register_builtin_adapters
from app.services.audit_service import DatabaseAuditSink
from app.services.orchestrator import ConnectionOrchestrator
from app.services.query_engine import QueryEngine
from app.services.sql_guard import SqlGuard

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize and tear down process-wide resources."""
    settings: Settings = app.state.settings
    db_session.init_engine(settings.control_db)

    if await db_session.ping():
        logger.info("Startup: control-plane database reachable")
    else:
        # Don't crash on a transient DB hiccup at boot; readiness probe will report it and
        # `pool_pre_ping` recovers connections. A hard dependency check belongs in the
        # orchestrator's readiness gate, not in process startup.
        logger.warning("Startup: control-plane database NOT reachable yet")

    # Register the built-in DB adapters (PostgreSQL/MySQL/MSSQL) into the registry.
    register_builtin_adapters()

    # Connection Orchestrator: one shared, concurrency-safe instance per process.
    orchestrator = ConnectionOrchestrator(settings.connections)
    await orchestrator.start()
    app.state.orchestrator = orchestrator

    # Query Engine with the SQL safety layer and the durable, append-only audit sink.
    # The sink writes in its own session against the control-plane database.
    audit_sink = DatabaseAuditSink(db_session.get_sessionmaker())
    app.state.query_engine = QueryEngine(SqlGuard(), audit_sink, settings.query)

    logger.info(
        "Application started",
        extra={"environment": settings.app.environment.value, "version": settings.app.version},
    )
    try:
        yield
    finally:
        await orchestrator.stop()
        await db_session.dispose_engine()
        logger.info("Application shutdown complete")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.logging)

    app = FastAPI(
        title=settings.app.name,
        version=settings.app.version,
        debug=settings.app.debug,
        lifespan=lifespan,
        docs_url="/docs" if not settings.app.is_production else None,
        redoc_url=None,
        openapi_url="/openapi.json" if not settings.app.is_production else None,
    )
    # Expose settings on app.state so lifespan/handlers read a single resolved instance.
    app.state.settings = settings

    # Middleware (executed bottom-up; context middleware must wrap everything).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.api.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)

    # Health at root (version-independent); business endpoints under the versioned prefix.
    app.include_router(health.router, prefix="/health")
    app.include_router(api_router, prefix=settings.api.prefix)

    return app


app = create_app()


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.server.host,
        port=settings.server.port,
        forwarded_allow_ips=settings.server.forwarded_allow_ips,
        log_config=None,  # we configure logging ourselves
        reload=not settings.app.is_production,
    )


if __name__ == "__main__":
    main()
