# DB Admin Platform

A production-grade, enterprise multi-database administration platform (SSMS / DataGrip /
pgAdmin-class) — connect to PostgreSQL, MySQL and SQL Server; run SQL through a web editor
with streaming results; browse schemas; manage users/roles; and keep an immutable audit log.

- **Backend:** FastAPI (async), SQLAlchemy 2.0, JWT + RBAC, AES-256-GCM credential encryption,
  per-session connection orchestration, multi-engine adapter layer, query engine with the SQL
  safety layer, schema introspection, append-only audit log.
- **Frontend:** Vanilla ES6 + Web Components (no framework, no build step) — Monaco SQL editor,
  AG Grid data tables, schema tree, admin panel.
- **Deploy:** Docker + docker-compose (control-plane Postgres + backend + nginx frontend).

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the locked system design,
[`backend/README.md`](backend/README.md) and [`frontend/README.md`](frontend/README.md) for
component details.

## Quick start (Docker)

```bash
cp .env.example .env
# Fill in the secrets — generate them with:
python -c "import secrets; print(secrets.token_urlsafe(48))"                  # SECURITY_JWT_SECRET
python -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())" # SECURITY_MASTER_ENCRYPTION_KEY
# Set BOOTSTRAP_ADMIN_EMAIL / BOOTSTRAP_ADMIN_PASSWORD for the first admin.

docker compose up --build
```

Then open **http://localhost:8080** and sign in with the bootstrap admin.

The backend container automatically:
1. waits for the control-plane database,
2. applies Alembic migrations (`alembic upgrade head`),
3. creates the bootstrap admin (idempotent),
4. starts the API server.

| Service | Port | Notes |
|---|---|---|
| frontend (nginx) | 8080 | Serves the SPA, proxies `/api` + WebSocket to the backend |
| backend (uvicorn) | 8000 | FastAPI; `GET /health/ready` for orchestration |
| postgres | — | Control-plane DB (internal network only) |

## Local development

- **Backend:** see [`backend/README.md`](backend/README.md) — `pip install -e ".[dev]"`, run
  `uvicorn app.main:app --reload`, tests with `pytest`.
- **Frontend:** see [`frontend/README.md`](frontend/README.md) — serve statically
  (`python -m http.server --directory frontend`), tests with `node --test`.

## Operational notes

- **Single backend worker.** Live target-database sessions are held in process memory by the
  Connection Orchestrator, so the backend runs one uvicorn worker. Horizontal scaling would
  require externalizing session state (documented constraint, not a bug).
- **Secrets.** `SECURITY_MASTER_ENCRYPTION_KEY` (KEK) wraps per-connection data keys. Losing it
  makes stored target-DB credentials unrecoverable; rotate via the pluggable `KeyManager`.
- **Audit immutability** is enforced both in the app (no update/delete paths) and at the
  database level (PostgreSQL rules block `UPDATE`/`DELETE` on `audit_logs`).
- **Offline-capable frontend.** Monaco and AG Grid are vendored into the nginx image — no CDN
  access is required at runtime.

## Testing summary

- Backend: 119 tests (`pytest`), incl. live integration against PostgreSQL 16 / MySQL 8.
- Frontend: 39 unit tests (`node --test`), plus browser-verified UI for every view.
