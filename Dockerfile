# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

# No bytecode pollution, unbuffered logs, no pip cache layer bloat.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install deps first for layer caching. Copy only metadata + sources needed
# to resolve the package, then install.
COPY pyproject.toml README.md ./
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./

RUN pip install .

# Run as an unprivileged, single-purpose user. No secrets are baked in — they
# arrive at runtime via env_file (see docker-compose.yml).
RUN useradd --create-home --uid 10001 app \
    && chown -R app:app /app
USER app

# Default: the Arq worker (collectors via cron + scoring + notify).
CMD ["arq", "buyingsignal.worker.WorkerSettings"]
