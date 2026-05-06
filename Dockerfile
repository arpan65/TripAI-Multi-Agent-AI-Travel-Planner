# ── Stage 1: build frontend ───────────────────────────────────────────────────
FROM node:20-alpine AS frontend-builder
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ .
# No VITE_API_URL — frontend uses relative URLs (same origin as FastAPI)
RUN npm run build

# ── Stage 2: Python backend ───────────────────────────────────────────────────
FROM python:3.11-slim

# Node.js 20 via NodeSource (needed for playwright-mcp)
RUN apt-get update && apt-get install -y --no-install-recommends curl gnupg && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

# uv (Python package manager + uvx)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# Install playwright-mcp globally so shutil.which("playwright-mcp") finds it.
# Browser is stored in a Docker volume mounted at /ms-playwright so it persists
# across image updates without bloating the image (~2 GB saved).
RUN npm install -g @playwright/mcp@0.0.73

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# Python deps — separate layer so code changes don't bust the dep cache
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Chromium system libs (same set works for chrome-for-testing)
RUN uv run playwright install-deps chromium

COPY app/ ./app/
COPY playwright-mcp.config.json ./

# Frontend static files built in Stage 1
COPY --from=frontend-builder /app/dist ./frontend_dist

# Entrypoint installs chrome-for-testing into the volume on first run if missing,
# then starts uvicorn.
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/docker-entrypoint.sh"]
