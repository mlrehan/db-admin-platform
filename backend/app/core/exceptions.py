"""Application exception hierarchy.

All deliberately-raised, client-facing errors derive from :class:`AppError`. The API layer
(see ``app.api``) maps these to JSON responses with a stable ``code`` so the frontend can
branch on machine-readable error codes rather than HTTP status alone.

Unexpected exceptions (anything *not* an ``AppError``) are treated as 500s and never leak
internal detail to the client.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for all expected, client-facing application errors.

    Attributes
    ----------
    status_code:
        HTTP status to return.
    code:
        Stable, machine-readable error code (SCREAMING_SNAKE_CASE).
    message:
        Human-readable, safe-to-display message.
    details:
        Optional structured context (must not contain secrets).
    """

    status_code: int = 500
    code: str = "INTERNAL_ERROR"
    message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.status_code = status_code or self.status_code
        self.details = details or {}
        super().__init__(self.message)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload


# --- 4xx ---------------------------------------------------------------------------------


class ValidationError(AppError):
    status_code = 422
    code = "VALIDATION_ERROR"
    message = "The request was invalid."


class AuthenticationError(AppError):
    status_code = 401
    code = "AUTHENTICATION_ERROR"
    message = "Authentication failed."


class AuthorizationError(AppError):
    status_code = 403
    code = "AUTHORIZATION_ERROR"
    message = "You are not authorized to perform this action."


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"
    message = "The requested resource was not found."


class ConflictError(AppError):
    status_code = 409
    code = "CONFLICT"
    message = "The request conflicts with the current state."


class RateLimitError(AppError):
    status_code = 429
    code = "RATE_LIMITED"
    message = "Too many requests."


# --- 5xx / infrastructure ----------------------------------------------------------------


class ConfigurationError(AppError):
    status_code = 500
    code = "CONFIGURATION_ERROR"
    message = "The server is misconfigured."


class EncryptionError(AppError):
    status_code = 500
    code = "ENCRYPTION_ERROR"
    message = "A cryptographic operation failed."


class UnsupportedEngineError(AppError):
    status_code = 422
    code = "UNSUPPORTED_ENGINE"
    message = "The requested database engine is not supported."


class SessionLimitError(AppError):
    status_code = 409
    code = "SESSION_LIMIT_REACHED"
    message = "The maximum number of concurrent database sessions has been reached."


class ConnectionFailedError(AppError):
    status_code = 502
    code = "CONNECTION_FAILED"
    message = "Failed to connect to the target database."


class QueryExecutionError(AppError):
    status_code = 400
    code = "QUERY_EXECUTION_ERROR"
    message = "The query failed to execute."


class QueryCancelledError(AppError):
    status_code = 409
    code = "QUERY_CANCELLED"
    message = "The query was cancelled."


class QueryTimeoutError(AppError):
    status_code = 504
    code = "QUERY_TIMEOUT"
    message = "The query exceeded the execution time limit."


class DatabaseError(AppError):
    status_code = 500
    code = "DATABASE_ERROR"
    message = "A database error occurred."


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "SERVICE_UNAVAILABLE"
    message = "The service is temporarily unavailable."
