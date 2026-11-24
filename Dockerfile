# Stage 1: Build static SPA bundle on native build host (fast multi-arch)
FROM --platform=$BUILDPLATFORM node:22-alpine AS web-builder
WORKDIR /build/web
COPY web/package.json web/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# Stage 2: Minimal Python runtime serving FastAPI + SPA
FROM python:3.12-slim AS runtime
WORKDIR /app

LABEL org.opencontainers.image.source="https://github.com/KoalaPro/OpenTradeLedger" \
      org.opencontainers.image.title="OpenTradeLedger" \
      org.opencontainers.image.description="Local-first, strictly read-only multi-exchange trade ledger" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OTL_HOST=0.0.0.0 \
    OTL_PORT=8012 \
    OTL_DATA_DIR=/app/data \
    OTL_DEMO_MODE=false

RUN pip install --no-cache-dir \
    "fastapi>=0.115.0" \
    "uvicorn>=0.30.0" \
    "httpx>=0.27.0" \
    "cryptography>=42.0.0" \
    "pydantic>=2.7.0" && mkdir -p /app/data

COPY pyproject.toml README.md ./
COPY backend/ ./backend/
COPY scripts/ ./scripts/
COPY --from=web-builder /build/web/dist ./web/dist

RUN pip install --no-cache-dir --no-deps .

EXPOSE 8012

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os, urllib.request; p = os.environ.get('OTL_PORT', '8012'); urllib.request.urlopen(f'http://127.0.0.1:{p}/api/v1/health', timeout=3)" || exit 1

CMD ["python", "scripts/run_server.py"]
