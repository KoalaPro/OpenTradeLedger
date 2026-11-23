# Stage 1: Build static SPA bundle
FROM node:22-alpine AS web-builder
WORKDIR /build/web
COPY web/package.json web/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# Stage 2: Minimal Python runtime serving FastAPI + SPA
FROM python:3.12-slim AS runtime
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OTL_HOST=0.0.0.0 \
    OTL_PORT=8012 \
    OTL_DATA_DIR=/app/data \
    OTL_DEMO_MODE=false

COPY pyproject.toml README.md ./
COPY backend/ ./backend/
COPY scripts/ ./scripts/
COPY --from=web-builder /build/web/dist ./web/dist

RUN pip install --no-cache-dir . && mkdir -p /app/data

EXPOSE 8012

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8012/api/v1/health', timeout=3)" || exit 1

CMD ["python", "scripts/run_server.py"]
