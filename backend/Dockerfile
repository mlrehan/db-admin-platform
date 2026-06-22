# Backend image: FastAPI + async drivers for PostgreSQL/MySQL/MSSQL.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System dependencies:
#  - build-essential / unixodbc-dev: build pyodbc (the MSSQL adapter's driver)
#  - msodbcsql18: Microsoft ODBC driver for SQL Server (from the Microsoft apt repo)
#  - curl/gnupg: fetch the Microsoft signing key + repo
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl gnupg ca-certificates build-essential unixodbc-dev \
    && curl -sSL https://packages.microsoft.com/keys/microsoft.asc \
        | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/microsoft-prod.gpg] \
        https://packages.microsoft.com/debian/12/prod bookworm main" \
        > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && apt-get purge -y --auto-remove curl gnupg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first for better layer caching.
COPY pyproject.toml ./
RUN pip install . && pip install "alembic>=1.13"

COPY . .

# Run as a non-root user; ensure the entrypoint is executable regardless of host FS bits.
RUN chmod +x docker-entrypoint.sh \
    && useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

ENV APP_ENVIRONMENT=production \
    SERVER_HOST=0.0.0.0 \
    SERVER_PORT=8000 \
    LOG_FORMAT=json

EXPOSE 8000
ENTRYPOINT ["./docker-entrypoint.sh"]
# Single worker: live DB sessions live in-process (the orchestrator). Horizontal scaling
# would require externalizing session state; documented in the deployment notes.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", \
     "--no-access-log", "--proxy-headers"]
