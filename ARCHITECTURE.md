# DB Admin Platform — Architecture (LOCKED)

> This document defines the system architecture. Per project rules, **it MUST NOT change
> once defined** unless explicitly instructed. All modules must respect it.

## 1. Topology

```
Frontend (Web Components, vanilla ES6)
        │  HTTPS / WSS
        ▼
API Gateway (FastAPI routers + middleware)
        │
        ▼
Service Layer (Auth, Connection Orchestrator, Query Engine, Metadata, Audit)
        │
        ▼
DB Adapter Layer (Adapter Pattern: PostgreSQL / MySQL / MSSQL)
        │
        ▼
Target DBMS (user-registered databases)
```

The platform keeps **two distinct data planes**:

- **Control plane** — the platform's own state (users, roles, saved connections with
  encrypted credentials, audit logs). Stored in a dedicated **PostgreSQL** database,
  accessed via async SQLAlchemy 2.0 + asyncpg, migrated with Alembic.
- **Data plane** — the *target* databases users connect to and administer. Reached only
  through the DB Adapter Layer. Control-plane DB and data-plane DBs never share sessions.

## 2. Locked technology decisions

| Concern | Decision |
|---|---|
| Backend framework | FastAPI (async-first) |
| Control-plane DB | PostgreSQL (async SQLAlchemy 2.0, asyncpg, Alembic) |
| Config | pydantic-settings v2, env-driven, fail-fast validation |
| Credential encryption | AES-256-GCM **envelope encryption**: env-provided master key (KEK) wraps per-connection data keys (DEK). `KeyManager` interface is pluggable so AWS KMS / Vault can replace the env provider with no architecture change. |
| AuthN | JWT (access + refresh), Argon2 password hashing |
| AuthZ | RBAC — roles: Admin, DBA, Developer, Viewer |
| Target DBs | PostgreSQL, MySQL, MSSQL via Adapter Pattern |
| Query results | Async execution + streaming over WebSocket, cancellation support |
| Audit | Append-only, immutable log records |
| Frontend | Vanilla JS ES6 modules + Web Components + Monaco + AG Grid + WebSocket client. **No React / No Next.js.** |
| Deploy | Docker + docker-compose (backend, frontend, control-plane Postgres) |

## 3. Backend package layout

```
backend/app/
  main.py            App factory + lifespan + middleware wiring
  core/              config, logging, exceptions, constants (cross-cutting, no business logic)
  db/                control-plane engine, session factory, declarative Base
  api/               FastAPI routers (the API Gateway surface)
  services/          business logic (orchestrator, query engine, metadata, audit)
  db/adapters/       DB Adapter Layer (per-engine implementations)
  auth/              JWT, RBAC, password hashing
  security/          encryption / key management / SQL safety
  models/            SQLAlchemy ORM models (control plane)
  schemas/           Pydantic request/response models
  utils/             small stateless helpers
```

## 4. Build phases (dependency order)

1. **Backend foundation** — app bootstrap, config, DB session system  ← *current*
2. Authentication — JWT, RBAC, user model + persistence
3. Connection Orchestrator — connection lifecycle, per-session pooling, isolation
4. DB Adapter Layer — full abstraction, multi-DB support
5. Query Engine — async execution, streaming, cancellation
6. Metadata + Audit — schema introspection, immutable audit log
7. Frontend core — app shell, routing, API client, WebSocket layer
8. UI modules — SQL editor, schema explorer, table viewer, admin panel
9. Deployment — Dockerfiles, docker-compose, env configs

Each phase is completed and stopped at before the next begins.
