"""HTTP middleware.

:class:`RequestContextMiddleware` assigns each request a correlation id (honouring an
inbound ``X-Request-ID`` from a trusted proxy, otherwise generating one), binds it to the
logging context, records wall-clock duration, and emits a structured access log line. The id
is echoed back in the ``X-Request-ID`` response header.
"""

from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.context import client_ip_ctx, request_id_ctx
from app.core.logging import get_logger

logger = get_logger("app.access")

_REQUEST_ID_HEADER = "X-Request-ID"


def _client_ip(request: Request) -> str | None:
    """Best-effort client IP. Honours the first hop in ``X-Forwarded-For`` (set by a trusted
    reverse proxy such as the bundled nginx), else falls back to the socket peer."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.client.host if request.client else None


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = request.headers.get(_REQUEST_ID_HEADER) or uuid.uuid4().hex
        token = request_id_ctx.set(request_id)
        ip_token = client_ip_ctx.set(_client_ip(request))
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.exception(
                "Request failed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round(duration_ms, 2),
                },
            )
            raise
        else:
            duration_ms = (time.perf_counter() - start) * 1000
            response.headers[_REQUEST_ID_HEADER] = request_id
            logger.info(
                "Request completed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": round(duration_ms, 2),
                },
            )
            return response
        finally:
            request_id_ctx.reset(token)
            client_ip_ctx.reset(ip_token)
