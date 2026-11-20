# syntax=docker/dockerfile:1.7
# ---- 1. build the SPA
FROM node:22.23.5-bookworm-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- 2. python deps
FROM python:3.13.18-slim-bookworm AS deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY pyproject.toml ./
COPY backend/app/__init__.py backend/app/__init__.py
RUN python -m venv /venv && /venv/bin/pip install --upgrade pip==25.3 && /venv/bin/pip install . && /venv/bin/pip uninstall -y earnings-intel

# ---- 3. runtime
FROM python:3.13.18-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH="/venv/bin:$PATH" PYTHONPATH=/app/backend ENV=production LOG_JSON=1
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=deps /venv /venv
COPY backend/ backend/
COPY --from=web /web/dist frontend/dist
RUN mkdir -p var && chown -R app /app
USER app
EXPOSE 8000
# Heroku sets $PORT; migrations run at startup (idempotent, Alembic).
CMD ["sh", "-c", "uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*' --timeout-keep-alive 75"]
