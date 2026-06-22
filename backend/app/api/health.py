"""Health and readiness endpoints.

* ``/health/live``  — liveness: the process is up. Never touches the database.
* ``/health/ready`` — readiness: dependencies (control-plane DB) are reachable. Returns 503
  when not ready so orchestrators can gate traffic.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from app.core.config import get_settings
from app.db import session as db_session

router = APIRouter(tags=["health"])


class LivenessResponse(BaseModel):
    status: str
    service: str
    version: str
    environment: str


class ReadinessResponse(BaseModel):
    status: str
    checks: dict[str, str]


@router.get("/live", response_model=LivenessResponse)
async def liveness() -> LivenessResponse:
    settings = get_settings()
    return LivenessResponse(
        status="ok",
        service=settings.app.name,
        version=settings.app.version,
        environment=settings.app.environment.value,
    )


@router.get("/ready", response_model=ReadinessResponse)
async def readiness(response: Response) -> ReadinessResponse:
    db_ok = await db_session.ping()
    checks = {"control_plane_db": "ok" if db_ok else "unavailable"}
    overall_ok = all(value == "ok" for value in checks.values())
    if not overall_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(status="ok" if overall_ok else "degraded", checks=checks)
