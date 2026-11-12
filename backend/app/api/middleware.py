"""HTTP middleware.

:class:`RequestContextMiddleware` assigns each request a correlation id (honouring an
inbound ``X-Request-ID`` from a trusted proxy, otherwise generating one), binds it to the
logging context, records wall-clock duration, and emits a structured access log line. The id
is echoed back in the ``X-Request-ID`` response header.
"""

from __future__ import annotations

import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.context import client_ip_ctx, client_private_ip_ctx, request_id_ctx
from app.core.logging import get_logger

logger = get_logger("app.access")

_REQUEST_ID_HEADER = "X-Request-ID"
_PRIVATE_IP_HEADER = "X-Private-IP"


def _peer_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _client_ip(request: Request) -> str | None:
    """Best-effort PUBLIC client IP. Honours the first hop in ``X-Forwarded-For`` (set by a
    trusted reverse proxy such as the bundled nginx), else falls back to the socket peer."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return _peer_ip(request)


def _private_ip(request: Request) -> str | None:
    """Best-effort PRIVATE (LAN) client IP. The browser reports its local address in the
    ``X-Private-IP`` header (obtained via WebRTC); if absent we fall back to the direct socket
    peer, which is the client's LAN address on a direct connection or the proxy hop otherwise."""
    reported = (request.headers.get(_PRIVATE_IP_HEADER) or "").strip()
    # Guard against a spoofed/oversized header; a real IP is short.
    if reported and len(reported) <= 64:
        return reported
    return _peer_ip(request)


class RequestContextMiddleware:
    """Pure ASGI middleware (not ``BaseHTTPMiddleware``).

    ``BaseHTTPMiddleware`` runs every request through an extra anyio task group and memory
    stream — measurable CPU on each request, which caps throughput of the single-worker API
    under many concurrent users. This implementation does the same work (request id, client IP
    context, access log, X-Request-ID response header) directly on the ASGI messages."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":  # websockets / lifespan pass straight through
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        request_id = request.headers.get(_REQUEST_ID_HEADER) or uuid.uuid4().hex
        token = request_id_ctx.set(request_id)
        ip_token = client_ip_ctx.set(_client_ip(request))
        priv_token = client_private_ip_ctx.set(_private_ip(request))
        start = time.perf_counter()
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = MutableHeaders(scope=message)
                headers[_REQUEST_ID_HEADER] = request_id
                # Standard Server-Timing header: time spent inside the app until the response
                # was ready (browser dev-tools show it; lets clients separate server time from
                # network / queueing when diagnosing latency).
                headers["Server-Timing"] = f"app;dur={(time.perf_counter() - start) * 1000:.1f}"
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            logger.exception(
                "Request failed",
                extra={
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                    "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                },
            )
            raise
        else:
            logger.info(
                "Request completed",
                extra={
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                    "status_code": status_code,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                },
            )
        finally:
            request_id_ctx.reset(token)
            client_ip_ctx.reset(ip_token)
            client_private_ip_ctx.reset(priv_token)
