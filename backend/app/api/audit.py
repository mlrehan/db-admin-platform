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
from app.auth.dependencies import CurrentUser, has_permission
from app.auth.roles import Permission
from app.core.exceptions import NotFoundError
from app.models.audit import AuditLog
from app.schemas.audit import AuditLogOut
from app.services.audit_service import AuditService

router = APIRouter(tags=["audit"])


def _can_view_all(user: CurrentUser) -> bool:
    """Whether the caller may see *everyone's* audit records (admins / auditors)."""
    return has_permission(user, Permission.AUDIT_READ)


@router.get("/logs", response_model=list[AuditLogOut])
async def list_audit_logs(
    user: CurrentUser,
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
    """List audit records. Any authenticated user may read **their own** activity; only
    auditors/admins (``audit:read``) may read across users.

    The own-only restriction is enforced here, server-side: a non-privileged caller's
    ``user_id`` filter is *forced* to their own id, so tampering with the query string cannot
    widen access."""
    effective_user_id = user_id if _can_view_all(user) else user.id
    return await service.search(
        user_id=effective_user_id,
        connection_id=connection_id,
        category=category,
        success=success,
        destructive=destructive,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )


@router.get("/logs/{audit_id}", response_model=AuditLogOut)
async def get_audit_log(
    audit_id: uuid.UUID,
    user: CurrentUser,
    service: Annotated[AuditService, Depends(get_audit_service)],
) -> AuditLog:
    record = await service.get(audit_id)
    # Hide existence of records the caller isn't allowed to see (404, not 403 — no info leak).
    if record is None or (not _can_view_all(user) and record.user_id != user.id):
        raise NotFoundError("Audit record not found.")
    return record
