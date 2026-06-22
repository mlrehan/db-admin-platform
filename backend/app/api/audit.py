"""Audit log query endpoints (read-only).

Requires the ``audit:read`` permission (Admin and DBA). The audit log is immutable, so there
are deliberately no create/update/delete endpoints — records are written only internally by
the Query Engine's audit sink.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_audit_service
from app.auth.dependencies import require_permissions
from app.auth.roles import Permission
from app.core.exceptions import NotFoundError
from app.models.audit import AuditLog
from app.schemas.audit import AuditLogOut
from app.services.audit_service import AuditService

router = APIRouter(tags=["audit"])

_can_read_audit = Depends(require_permissions(Permission.AUDIT_READ))


@router.get("/logs", response_model=list[AuditLogOut], dependencies=[_can_read_audit])
async def list_audit_logs(
    service: Annotated[AuditService, Depends(get_audit_service)],
    user_id: Annotated[uuid.UUID | None, Query()] = None,
    connection_id: Annotated[uuid.UUID | None, Query()] = None,
    category: Annotated[str | None, Query()] = None,
    success: Annotated[bool | None, Query()] = None,
    destructive: Annotated[bool | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[AuditLog]:
    return await service.search(
        user_id=user_id,
        connection_id=connection_id,
        category=category,
        success=success,
        destructive=destructive,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )


@router.get("/logs/{audit_id}", response_model=AuditLogOut, dependencies=[_can_read_audit])
async def get_audit_log(
    audit_id: uuid.UUID,
    service: Annotated[AuditService, Depends(get_audit_service)],
) -> AuditLog:
    record = await service.get(audit_id)
    if record is None:
        raise NotFoundError("Audit record not found.")
    return record
