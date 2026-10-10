# Running & Deploying the DB Admin Platform (SQL Studio)

This guide covers all three supported workflows with step-by-step commands:

1. **[Production Deployment on Ubuntu Linux (`docker-compose.yml`)](#1-production-deployment-on-ubuntu-linux-docker-compose)** — recommended for going live on an Ubuntu server.
2. **[Development on Windows 11 with Docker (`docker-compose.dev.yml`)](#2-development-on-windows-11-with-docker-docker-composedevyml)** — containerized dev stack with live code reload.
3. **[Native Development on Windows 11 without Docker (VS Code)](#3-native-development-on-windows-11-without-docker)** — run Python FastAPI + static frontend directly.

---

## Port & Route Overview (Zero Default-Port Conflicts)

To prevent conflicts with other applications that commonly bind default ports (`8000`, `8080`, `5432`, `3306`, `1433`, `6379`, `3000`), this project uses a dedicated high-port block configured in `.env`:

| Service | Host Port (`.env` Key) | Container Port | Purpose |
| :--- | :--- | :--- | :--- |
| **Frontend (Nginx SPA + Reverse Proxy)** | **`18480`** (`FRONTEND_PORT`) | `80` | Main web entry point (`http://SERVER_IP:18480`). Serves the SPA & reverse-proxies `/api` + WebSockets to `backend` |
| **Backend (FastAPI API Server)** | **`18400`** (`BACKEND_PORT`) | `8000` | FastAPI control plane (`/health/ready`, and `/docs` in development mode) |
| **Control-Plane PostgreSQL 16** | **`15432`** (`CONTROL_DB_HOST_PORT`) | `5432` | Platform state DB (published on `15432` in dev; internal-only in production) |
| **Target MySQL 8.0** | **`13306`** (`MYSQL_HOST_PORT`) | `3306` | Bundled MySQL 8.0 target instance |
| **Target SQL Server 2022** | **`15333`** (`MSSQL_HOST_PORT`) | `1433` | Bundled Microsoft SQL Server 2022 Developer target instance |

### Application Entry Routes
- **`/` (`#/`) — SQL Studio Landing Page (Public):** Displays two environments:
  1. **SQL Playground (`#/playground`):** Instant, no-login SQLite 3 IDE running 100% in the browser via locally vendored WebAssembly (`sql.js`) with preloaded `course`, `student`, and `admission` tables.
  2. **Enterprise SQL Workspace (`#/login` → `#/workspace`):** Authenticated multi-DBMS workspace (`PostgreSQL`, `MySQL`, `Microsoft SQL Server`) with Schema Explorer, Data Viewer, ER Diagrams, RBAC Grants, and immutable Audit Logs.

---

## 1. Production Deployment on Ubuntu Linux (Docker Compose)

In production, [docker-compose.yml](file:///d:/LAIT/db-admin-platform/docker-compose.yml) runs the entire stack in containers, vendors all frontend libraries (including Monaco Editor, AG Grid, and SQLite WebAssembly) inside the `frontend` image so no external CDN is required, applies Alembic database migrations automatically, and creates the initial Admin user on startup.

### Step 1.1 — Install Docker Engine & Docker Compose Plugin on Ubuntu
Run these commands on your Ubuntu server:
```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER
```
*(Log out and log back in once so your user can run `docker` without `sudo`.)*

### Step 1.2 — Clone or Copy the Project to the Server
```bash
git clone <your-repo-url> db-admin-platform
cd db-admin-platform
```

### Step 1.3 — Configure the Production `.env` File
Copy the template and edit `.env`:
```bash
cp .env.example .env
nano .env
```
Generate strong cryptographic secrets on the Ubuntu server:
```bash
openssl rand -base64 48   # Copy output into SECURITY_JWT_SECRET
openssl rand -base64 32   # Copy output into SECURITY_MASTER_ENCRYPTION_KEY (exact 32-byte AES-256 key)
openssl rand -base64 24   # Copy output into CONTROL_DB_PASSWORD
```
Fill in `.env`:
```ini
# --- Control-plane database ---
CONTROL_DB_USER=postgres
CONTROL_DB_PASSWORD=<paste-strong-db-password>
CONTROL_DB_NAME=db_admin_platform

# --- Security (REQUIRED in production) ---
SECURITY_JWT_SECRET=<paste-openssl-rand-base64-48>
SECURITY_MASTER_ENCRYPTION_KEY=<paste-openssl-rand-base64-32>

# --- First admin user (created automatically on first start) ---
BOOTSTRAP_ADMIN_EMAIL=admin@yourcompany.com
BOOTSTRAP_ADMIN_PASSWORD=<strong-password-at-least-12-characters>

# --- Target DB container passwords (if using bundled MSSQL / MySQL) ---
MSSQL_SA_PASSWORD=<strong-mssql-sa-password>
MYSQL_ROOT_PASSWORD=<strong-mysql-root-password>
MYSQL_DATABASE=app_db
MYSQL_USER=app_user
MYSQL_PASSWORD=<strong-mysql-app-password>

# --- Dedicated non-default host ports ---
FRONTEND_PORT=18480
BACKEND_PORT=18400
CONTROL_DB_HOST_PORT=15432
MYSQL_HOST_PORT=13306
MSSQL_HOST_PORT=15333

# --- Public origin(s) allowed by CORS (comma-separated, no trailing slash) ---
API_CORS_ORIGINS=https://sql.yourcompany.com,http://YOUR_SERVER_IP:18480
LOG_LEVEL=INFO
```
> **Important:** Back up `SECURITY_MASTER_ENCRYPTION_KEY` in a secure password manager/vault. It encrypts all saved target-database passwords in PostgreSQL; if lost, stored connection passwords cannot be decrypted.

### Step 1.4 — Build and Start the Production Stack
```bash
docker compose up -d --build
```
On first boot:
1. `postgres` initializes `db_admin_platform` and passes its `pg_isready` healthcheck.
2. `backend` waits for `postgres`, runs `alembic upgrade head` (applying all 8 schema migrations), creates the `BOOTSTRAP_ADMIN_EMAIL` admin account, and starts `uvicorn`.
3. `frontend` waits for `backend` to report `healthy`, then starts `nginx` on host port `18480`.

### Step 1.5 — Verify the Deployment
Check container health:
```bash
docker compose ps
```
All core services (`postgres`, `backend`, `frontend`) should show **`Up ... (healthy)`**.

Verify the readiness probe:
```bash
curl http://localhost:18400/health/ready
# Expected output: {"status":"ok","checks":{"control_plane_db":"ok"}}
```
Now open **`http://YOUR_SERVER_IP:18480`** in your browser:
- You will see the **SQL Studio** landing page.
- Click **SQL Playground** to verify the in-browser SQLite environment.
- Click **Enterprise SQL Workspace** and sign in with `BOOTSTRAP_ADMIN_EMAIL` and `BOOTSTRAP_ADMIN_PASSWORD`.

### Step 1.6 — Put Behind a Domain & HTTPS (Recommended for Live Production)
To serve the application at `https://sql.yourcompany.com` on standard HTTPS port `443` while keeping the container on port `18480`:

#### Option A: Using Caddy (Automatic Let's Encrypt SSL)
```bash
sudo apt-get install -y caddy
sudo tee /etc/caddy/Caddyfile >/dev/null <<'EOF'
sql.yourcompany.com {
    reverse_proxy 127.0.0.1:18480
}
EOF
sudo systemctl reload caddy
```

#### Option B: Using Host Nginx + Certbot
If your Ubuntu server already runs host Nginx for other apps, add a site config in `/etc/nginx/sites-available/db-admin-platform`:
```nginx
server {
    listen 80;
    server_name sql.yourcompany.com;

    location / {
        proxy_pass http://127.0.0.1:18480;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
```
Enable the site and issue a Let's Encrypt certificate:
```bash
sudo ln -s /etc/nginx/sites-available/db-admin-platform /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d sql.yourcompany.com
```

### Step 1.7 — Connecting to Target Databases from the Enterprise Workspace
Because `backend` runs inside a Docker container, use the right `Host` value when registering a connection under **Connections → + New connection**:

| Target Database Location | `Host` Value to Enter | `Port` Value |
| :--- | :--- | :--- |
| **Remote database server** (Cloud RDS, Azure SQL, another VPS) | The server's IP or hostname (e.g. `209.126.6.131`) | Server's port (`5432`, `3306`, `1433`, etc.) |
| **Bundled MySQL container** in `docker-compose.yml` | `mysql` | `3306` |
| **Bundled MSSQL container** in `docker-compose.yml` | `mssql` | `1433` |
| **Database installed directly on the same Ubuntu host** | `host.docker.internal` *(not `localhost`)* | Host DB port |

### Step 1.8 — Day-2 Production Operations (Updates, Backups, Logs)
- **View live logs:**
  ```bash
  docker compose logs -f backend frontend
  ```
- **Deploy code updates (zero data loss; migrations run automatically):**
  ```bash
  git pull
  docker compose up -d --build
  ```
- **Back up the control-plane PostgreSQL database:**
  ```bash
  docker compose exec -T postgres pg_dump -U postgres db_admin_platform > backup_$(date +%F).sql
  ```
- **Restore from backup:**
  ```bash
  cat backup_YYYY-MM-DD.sql | docker compose exec -T postgres psql -U postgres -d db_admin_platform
  ```
- **Reset or create an Admin user via CLI:**
  ```bash
  docker compose exec backend python -m app.cli list-users
  docker compose exec backend python -m app.cli reset-password --email admin@yourcompany.com --password "<NewStrongPassword123!>"
  ```

---

## 2. Development on Windows 11 with Docker (`docker-compose.dev.yml`)

[docker-compose.dev.yml](file:///d:/LAIT/db-admin-platform/docker-compose.dev.yml) is optimized for Windows 11 + Docker Desktop:
- Uses the same non-default host ports (`18480` frontend, `18400` backend, `15432` PostgreSQL).
- Enables `APP_ENVIRONMENT=development` and `APP_DEBUG=true` so Swagger UI (`http://localhost:18400/docs`) is enabled.
- Bind-mounts `./backend/app` and `./frontend/*` into the containers so code edits on Windows 11 take effect immediately without rebuilding images.
- Maps `host.docker.internal` so you can connect to PostgreSQL/MySQL/MSSQL instances installed natively on Windows 11.

### Start the Core Development Stack (PostgreSQL + Backend + Frontend)
```powershell
docker compose -f docker-compose.dev.yml up -d --build
```
- **Web App (Landing Page, SQLite Playground, Enterprise Workspace):** `http://localhost:18480/`
- **Backend Swagger API Docs:** `http://localhost:18400/docs`
- **Backend Readiness Probe:** `http://localhost:18400/health/ready`
- **Control-Plane PostgreSQL (for pgAdmin / DBeaver on Windows):** `localhost:15432`

### Start with Containerized MSSQL & MySQL Target Databases (Optional)
```powershell
docker compose -f docker-compose.dev.yml --profile dbs up -d --build
```

### Stop the Development Stack
```powershell
docker compose -f docker-compose.dev.yml down
```

---

## 3. Native Development on Windows 11 without Docker

If you prefer running Python and the static frontend directly in PowerShell / VS Code without Docker:

### Step 3.1 — Backend (`http://localhost:18400`)
Ensure a local PostgreSQL server has a database named `db_admin_platform` and matches `backend/.env`:
```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

alembic upgrade head
python -m app.cli create-admin --email admin@lait.org.co.uk --password administrator-LAIT_460349
uvicorn app.main:app --reload --port 18400
```

### Step 3.2 — Frontend (`http://localhost:18480`)
`frontend/config.js` is pre-configured to point to `http://localhost:18400/api/v1`:
```powershell
python frontend\devserver.py 18480
```
Open **`http://localhost:18480`** in your browser.

### Step 3.3 — Automated Test Suites
```powershell
# Backend test suite (233 tests)
cd backend; .\.venv\Scripts\Activate.ps1; pytest -q

# Frontend test suite (55 tests, including router and SQLite playground tests)
cd ..\frontend; npm test
```
