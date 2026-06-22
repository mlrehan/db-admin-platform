"""Phase 1 foundation smoke tests.

These exercise the wiring that everything else depends on — config validation, the app
factory, exception envelope, and health endpoints — without requiring a live database.
"""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from app.core.config import Environment, Settings
from app.core.exceptions import AppError, NotFoundError
from app.main import create_app


def test_settings_defaults_local() -> None:
    settings = Settings()
    assert settings.app.environment == Environment.LOCAL
    assert settings.api.prefix.startswith("/api")


def test_cors_origins_parsed_from_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_CORS_ORIGINS", "http://a.test, http://b.test")
    from app.core.config import APISettings

    api = APISettings()
    assert api.cors_origins == ["http://a.test", "http://b.test"]


def test_master_key_rejects_wrong_length(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = base64.b64encode(b"too-short").decode()
    monkeypatch.setenv("SECURITY_MASTER_ENCRYPTION_KEY", bad)
    from app.core.config import SecuritySettings

    with pytest.raises(ValueError, match="32 bytes"):
        SecuritySettings()


def test_production_requires_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENVIRONMENT", "production")
    monkeypatch.setenv("SECURITY_JWT_SECRET", "")
    with pytest.raises(ValueError, match="Missing required secrets"):
        Settings()


def test_app_error_payload() -> None:
    err = NotFoundError("nope", details={"id": 1})
    assert isinstance(err, AppError)
    payload = err.to_payload()
    assert payload["code"] == "NOT_FOUND"
    assert payload["details"] == {"id": 1}


def test_liveness_endpoint() -> None:
    # TestClient runs lifespan; init_engine works without a reachable DB (ping is lazy).
    with TestClient(create_app()) as client:
        resp = client.get("/health/live")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "version" in body
        assert resp.headers.get("X-Request-ID")
