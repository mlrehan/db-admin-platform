# Backend — DB Admin Platform

FastAPI (async-first) control plane for the multi-database administration platform.
See [`../ARCHITECTURE.md`](../ARCHITECTURE.md) for the locked system design.

## Layout

| Path | Responsibility |
|---|---|
| `app/core/` | Config, logging, exceptions, request context (cross-cutting) |
| `app/db/` | Control-plane async engine, session lifecycle, declarative base |
| `app/api/` | API gateway: routers, middleware, exception handlers |
| `app/services/` | Business logic (orchestrator, query engine, metadata, audit) — Phase 3+ |
| `app/auth/`, `app/security/` | JWT/RBAC and encryption — Phase 2 |
| `app/models/`, `app/schemas/` | ORM models / Pydantic DTOs — Phase 2+ |
| `alembic/` | Async migrations against the control-plane DB |

## Local development

```bash
python -m venv .venv
.venv\Scripts\activate            # PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"

copy .env.example .env            # then fill secrets (see comments in the file)
```

Generate the required secrets:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"                 # SECURITY_JWT_SECRET
python -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())"  # SECURITY_MASTER_ENCRYPTION_KEY
```

Run:

```bash
python -m app.main          # or: uvicorn app.main:app --reload
pytest -q                   # foundation smoke tests
```

## Health probes

| Endpoint | Meaning |
|---|---|
| `GET /health/live` | Liveness — process is up (no DB access). |
| `GET /health/ready` | Readiness — control-plane DB reachable; `503` when not. |

API docs at `/docs` (disabled automatically in `production`).

## Migrations (Phase 2+, once models exist)

```bash
alembic revision --autogenerate -m "message"
alembic upgrade head
```

The DB URL is injected from app settings in `alembic/env.py` — no credentials in the repo.
