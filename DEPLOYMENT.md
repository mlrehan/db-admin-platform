# Running & Deploying the DB Admin Platform

Two supported workflows:

- **A. Local development** on Windows 11 (run backend + frontend directly, no Docker)
- **B. Production** on Ubuntu Linux (Docker Compose — the whole stack in containers)

---

## A. Development — Windows 11 (VS Code)

### A0. Prerequisites
- **Python 3.12+** (3.13/3.14 also work locally)
- **PostgreSQL** running locally (this is the platform's *own* "control‑plane" database — separate from any database you connect to and administer)
- **VS Code** + the **Python** extension
- (optional) **Node.js** — only needed to run the frontend unit tests
- (optional) the **Microsoft ODBC Driver 18** — only if you want to connect to **SQL Server** targets from your dev machine (PostgreSQL & MySQL targets need nothing extra)

### A1. Create the control‑plane database (one time)
PostgreSQL does not auto‑create it:
```powershell
psql -U postgres -c "CREATE DATABASE db_admin_platform;"
```
Make sure `backend/.env` matches your local PostgreSQL (`CONTROL_DB_USER`, `CONTROL_DB_PASSWORD`, `CONTROL_DB_PORT`, `CONTROL_DB_NAME`). A dev `backend/.env` already exists with `APP_ENVIRONMENT=local` and pre‑generated dev secrets.

### A2. Backend (FastAPI on :8000)
In a VS Code PowerShell terminal:
```powershell
cd backend
python -m venv .venv                 # first time only
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"              # first time only (installs all deps + test tools)

alembic upgrade head                 # create / update tables
python -m app.cli create-admin --email admin@lait.org.uk   # set a 12+ char password
uvicorn app.main:app --reload --port 8000
```
Check: open **http://localhost:8000/docs** and **http://localhost:8000/health/ready** (should report `control_plane_db: ok`).

> Prefer the debugger? Press **F5 → "Backend: FastAPI (uvicorn)"** (configured in `.vscode/launch.json`). Run the migration + create‑admin once first.

### A3. Frontend (static, on :8080)
The frontend is plain ES modules — **no build step**. Serve it with the included no‑cache dev server (this avoids the browser caching old modules between edits):
```powershell
python frontend\devserver.py 8080
```
Open **http://localhost:8080** and sign in with the admin you created.

- `frontend/config.js` already points the API at `http://localhost:8000/api/v1` for dev.
- `backend/.env` → `API_CORS_ORIGINS` must include your frontend origin (`http://localhost:8080`). Adjust if you use a different port.
- Alternative: the VS Code **Live Server** extension (configured to serve `/frontend` on 8080 in `.vscode/settings.json`) — but it caches modules, so the dev server above is recommended.

### A4. Run the tests (optional)
```powershell
# backend (uses in-memory SQLite — no PostgreSQL needed)
cd backend; .\.venv\Scripts\Activate.ps1; pytest -q

# frontend (needs Node)
cd ..\frontend; npm test          # node --test
```

### Dev notes
- **Two terminals**: one for the backend (A2), one for the frontend (A3).
- **MSSQL** target support needs `aioodbc`/`pyodbc` + the MS ODBC driver; PostgreSQL/MySQL work without it.
- Editing frontend files: reload the browser. If you don't see changes, you're hitting the browser cache — the no‑cache dev server (A3) prevents this.

---

## B. Production — Ubuntu Linux (Docker Compose)

The whole system (PostgreSQL + FastAPI backend + nginx‑served frontend) runs in containers. `docker compose up --build` builds everything, runs database migrations, and bootstraps the first admin automatically.

### B1. Install Docker Engine + Compose plugin
```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER     # log out/in so your user can run docker without sudo
```

### B2. Get the code onto the server
```bash
git clone <your-repo-url> db-admin-platform   # or: scp -r ./db-admin-platform user@server:~/
cd db-admin-platform
```

### B3. Create the production `.env` (secrets)
```bash
cp .env.example .env
nano .env
```
Generate strong values and paste them in:
```bash
openssl rand -base64 48     # → SECURITY_JWT_SECRET
openssl rand -base64 32     # → SECURITY_MASTER_ENCRYPTION_KEY  (decodes to 32 bytes = AES-256)
openssl rand -base64 24     # → CONTROL_DB_PASSWORD
```
Set in `.env`:
```ini
CONTROL_DB_USER=dbadmin
CONTROL_DB_PASSWORD=<from openssl>
CONTROL_DB_NAME=db_admin_platform
SECURITY_JWT_SECRET=<from openssl>
SECURITY_MASTER_ENCRYPTION_KEY=<from openssl>
BOOTSTRAP_ADMIN_EMAIL=admin@yourcompany.com
BOOTSTRAP_ADMIN_PASSWORD=<a strong 12+ char password>
API_CORS_ORIGINS=https://db.yourcompany.com   # your public URL (or http://SERVER_IP:8080 for a quick test)
FRONTEND_PORT=8080
```
> ⚠️ The `SECURITY_MASTER_ENCRYPTION_KEY` encrypts all stored target‑DB credentials — back it up securely; losing it makes saved credentials unrecoverable.

### B4. Build & start
```bash
docker compose up -d --build
```
On start the backend automatically: waits for PostgreSQL → runs `alembic upgrade head` → creates the bootstrap admin (idempotent) → starts the API.

### B5. Verify
```bash
docker compose ps                 # postgres + backend should be "healthy"
docker compose logs -f backend    # watch migrations + "Application started"
curl -s http://localhost:8000/health/ready   # {"status":"ok","checks":{"control_plane_db":"ok"}}
```
Open **http://SERVER_IP:8080** and sign in with the bootstrap admin.

| Service | Port | Notes |
|---|---|---|
| frontend (nginx) | `8080` → 80 | Serves the SPA, proxies `/api` + WebSocket to the backend |
| backend (uvicorn) | `8000` | Internal/diagnostic; can be unexposed in prod (see B7) |
| postgres | — | Internal network only (never published) |

### B5a. Connecting to the databases you want to administer

This is the part people get wrong, so read carefully. **The backend runs inside a container**,
so when you create a connection, `host` is resolved *from the container's point of view*.

| The target database is… | Use this `host` |
|---|---|
| A **remote** server (another machine, cloud, RDS, etc.) | its hostname or IP, e.g. `db.example.com` or `10.0.0.5` |
| Installed **on the same Ubuntu server** as Docker | **`host.docker.internal`** — **NOT `localhost`** |
| Another **Docker container** on this host | that container's service/network name |

> ⚠️ `localhost` / `127.0.0.1` inside a connection refers to the **backend container itself**,
> not your server. The compose file already maps `host.docker.internal` to the host gateway so
> the second case works on Linux.

If your PostgreSQL / MySQL is installed directly on the host, you must also let it accept
connections from the Docker network (by default they only listen on `127.0.0.1`):

- **PostgreSQL** — in `postgresql.conf` set `listen_addresses = '*'` (or the docker bridge IP),
  and in `pg_hba.conf` allow the docker subnet, e.g.
  `host all all 172.16.0.0/12 scram-sha-256`. Then `sudo systemctl restart postgresql`.
- **MySQL** — set `bind-address = 0.0.0.0` in `mysqld.cnf`, create/grant the user for the docker
  subnet (e.g. `'app'@'172.%'`), and restart MySQL.
- Open the DB port to the docker bridge in your firewall if `ufw` is active, e.g.
  `sudo ufw allow from 172.16.0.0/12 to any port 5432`.

### B5b. Connecting to a remote Microsoft SQL Server

Supported out of the box — the backend image ships the **Microsoft ODBC Driver 18** plus
`aioodbc`/`pyodbc`, and the adapter auto-selects the newest installed driver. Just make sure:

- the SQL Server is reachable from the host (port **1433** open to outbound), and
- you set the connection's **SSL mode** to match the server:
  - blank / `disable` → unencrypted (Driver 18 sends `Encrypt=no`),
  - `require` → encrypted, trusting the server certificate (typical for internal servers),
  - `verify-full` → encrypted **and** validates the certificate (needs a trusted CA).

If a SQL Server connection fails with a TLS/certificate error, switch the SSL mode to `require`.

### B6. Put it behind HTTPS (recommended)
Terminate TLS with a host reverse proxy. **Caddy** (automatic Let's Encrypt certs) is simplest:
```bash
sudo apt-get install -y caddy
sudo tee /etc/caddy/Caddyfile >/dev/null <<'EOF'
db.yourcompany.com {
    reverse_proxy localhost:8080
}
EOF
sudo systemctl restart caddy
```
Point your domain's DNS at the server, then browse **https://db.yourcompany.com**. (nginx + certbot works too.)

### B7. Harden
- **Firewall**: expose only 80/443 publicly.
  ```bash
  sudo ufw allow 22,80,443/tcp && sudo ufw enable
  ```
- **Don't publish the backend** publicly — in `docker-compose.yml` remove the `backend` `ports:` mapping (the frontend reaches it over the internal Docker network).
- **Change the bootstrap admin password** after first login (top‑right key icon → Change password).
- The audit log is immutable (DB‑level rules); keep it that way.

### B8. Day‑2 operations
**Logs**
```bash
docker compose logs -f backend frontend
```
**Update to a new version** (migrations run automatically on restart):
```bash
git pull
docker compose up -d --build
```
**Back up the database** (data lives in the `pgdata` volume):
```bash
docker compose exec -T postgres pg_dump -U dbadmin db_admin_platform > backup_$(date +%F).sql
# restore:  cat backup.sql | docker compose exec -T postgres psql -U dbadmin -d db_admin_platform
```
**Stop / start**
```bash
docker compose down        # stop (keeps the pgdata volume)
docker compose up -d        # start again
# docker compose down -v   # DANGER: also deletes the database volume
```

### Scaling note
The backend runs a **single worker** by design — live target‑DB sessions are held in process memory by the Connection Orchestrator. Running multiple backend replicas would split that state; horizontal scaling would first require externalizing session state.
