"""JWT access/refresh token issuance and verification.

Tokens are signed with the configured HMAC secret. Both token types carry:

* ``sub``  — user id
* ``role`` — the user's role at issue time
* ``type`` — ``access`` or ``refresh``
* ``tv``   — the user's ``token_version``; bumping it server-side invalidates every
             previously-issued token for that user (logout-everywhere / password change)
* ``jti``  — unique token id
* ``iat`` / ``exp`` / ``iss`` — standard temporal + issuer claims

Verification is strict: signature, expiry and issuer are all enforced. Any problem raises
:class:`app.core.exceptions.AuthenticationError` with a safe message.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

import jwt

from app.core.config import SecuritySettings
from app.core.exceptions import AuthenticationError


class TokenType(str, Enum):
    ACCESS = "access"
    REFRESH = "refresh"


@dataclass(frozen=True)
class TokenClaims:
    subject: str
    role: str
    token_type: TokenType
    token_version: int
    jti: str
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class IssuedToken:
    token: str
    expires_in: int  # seconds until expiry


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _issue(
    settings: SecuritySettings,
    *,
    subject: str,
    role: str,
    token_version: int,
    token_type: TokenType,
    ttl_seconds: int,
) -> IssuedToken:
    issued_at = _now()
    expires_at = issued_at + timedelta(seconds=ttl_seconds)
    payload = {
        "sub": subject,
        "role": role,
        "type": token_type.value,
        "tv": token_version,
        "jti": uuid.uuid4().hex,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "iss": settings.jwt_issuer,
    }
    token = jwt.encode(
        payload,
        settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    return IssuedToken(token=token, expires_in=ttl_seconds)


def create_access_token(
    settings: SecuritySettings, *, subject: str, role: str, token_version: int
) -> IssuedToken:
    return _issue(
        settings,
        subject=subject,
        role=role,
        token_version=token_version,
        token_type=TokenType.ACCESS,
        ttl_seconds=settings.access_token_ttl_seconds,
    )


def create_refresh_token(
    settings: SecuritySettings, *, subject: str, role: str, token_version: int
) -> IssuedToken:
    return _issue(
        settings,
        subject=subject,
        role=role,
        token_version=token_version,
        token_type=TokenType.REFRESH,
        ttl_seconds=settings.refresh_token_ttl_seconds,
    )


def decode_token(
    settings: SecuritySettings, token: str, *, expected_type: TokenType
) -> TokenClaims:
    """Verify ``token`` and return its claims, or raise :class:`AuthenticationError`."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            options={"require": ["sub", "exp", "iat", "iss", "type"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Token has expired.", code="TOKEN_EXPIRED") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Invalid authentication token.") from exc

    if payload.get("type") != expected_type.value:
        raise AuthenticationError(
            f"Expected a {expected_type.value} token.", code="TOKEN_WRONG_TYPE"
        )

    try:
        return TokenClaims(
            subject=str(payload["sub"]),
            role=str(payload.get("role", "")),
            token_type=TokenType(payload["type"]),
            token_version=int(payload.get("tv", 0)),
            jti=str(payload.get("jti", "")),
            issued_at=datetime.fromtimestamp(payload["iat"], tz=timezone.utc),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=timezone.utc),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise AuthenticationError("Malformed token claims.") from exc
