#!/usr/bin/env bash
# Backend container entrypoint:
#   1. Wait for the control-plane database to accept connections.
#   2. Apply Alembic migrations (idempotent — safe to run on every start).
#   3. Optionally bootstrap the first admin user (idempotent).
#   4. Exec the server command (CMD).
set -euo pipefail

echo "[entrypoint] waiting for control-plane database ${CONTROL_DB_HOST:-postgres}:${CONTROL_DB_PORT:-5432}…"
python - <<'PY'
import asyncio, os, sys, time
import asyncpg

async def wait():
    host = os.environ.get("CONTROL_DB_HOST", "postgres")
    port = int(os.environ.get("CONTROL_DB_PORT", "5432"))
    user = os.environ.get("CONTROL_DB_USER", "dbadmin")
    pwd = os.environ.get("CONTROL_DB_PASSWORD", "")
    db = os.environ.get("CONTROL_DB_NAME", "db_admin_platform")
    deadline = time.time() + 60
    while True:
        try:
            conn = await asyncpg.connect(host=host, port=port, user=user, password=pwd, database=db)
            await conn.close()
            return
        except Exception as exc:  # noqa: BLE001
            if time.time() > deadline:
                print(f"[entrypoint] database not reachable: {exc}", file=sys.stderr)
                sys.exit(1)
            await asyncio.sleep(1.5)

asyncio.run(wait())
PY
echo "[entrypoint] database is up."

echo "[entrypoint] applying migrations…"
alembic upgrade head

if [[ -n "${BOOTSTRAP_ADMIN_EMAIL:-}" && -n "${BOOTSTRAP_ADMIN_PASSWORD:-}" ]]; then
  echo "[entrypoint] ensuring bootstrap admin ${BOOTSTRAP_ADMIN_EMAIL}…"
  python -m app.cli create-admin \
    --email "${BOOTSTRAP_ADMIN_EMAIL}" \
    --password "${BOOTSTRAP_ADMIN_PASSWORD}" || \
    echo "[entrypoint] admin already exists or creation skipped."
fi

echo "[entrypoint] starting: $*"
exec "$@"
