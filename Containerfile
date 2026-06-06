# syntax=docker/dockerfile:1
# ── Stage 1: Build dependencies ──
FROM docker.io/library/python:3.11.12-slim-bookworm AS builder

RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
  && mkdir -p /etc/apt/keyrings \
  && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
  && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main" > /etc/apt/sources.list.d/nodesource.list \
  && apt-get update \
  && apt-get install -y --no-install-recommends nodejs \
  && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

WORKDIR /build

# --- Node deps (layer cache: package manifests rarely change) ---
COPY package.json package-lock.json ./
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN --mount=type=cache,target=/root/.npm \
    npm ci && npm ci --prefix frontend

# --- Python main venv: graphiti + flask (layer cache) ---
COPY backend/pyproject.toml backend/uv.lock ./backend/
RUN --mount=type=cache,target=/root/.cache/uv \
    cd backend && uv sync --extra graphiti --frozen --no-dev

# --- Python simulation venv: oasis (isolated, conflicts with graphiti) ---
RUN cd backend \
    && uv venv .venv-simulation --python 3.11 --seed \
    && uv pip install --python .venv-simulation/bin/python --no-cache-dir \
         camel-oasis==0.2.5 camel-ai==0.2.78 openai python-dotenv

# ── Stage 2: Runtime ──
FROM docker.io/library/python:3.11.12-slim-bookworm

RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates curl gnupg wget \
  && mkdir -p /etc/apt/keyrings \
  && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
  && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main" > /etc/apt/sources.list.d/nodesource.list \
  && apt-get update \
  && apt-get install -y --no-install-recommends nodejs \
  && rm -rf /var/lib/apt/lists/* \
  && groupadd -r mirofish -g 1000 \
  && useradd -r -u 1000 -g mirofish -m -d /home/mirofish -s /bin/bash mirofish

# Copy uv (needed by npm run backend -> uv run)
COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

WORKDIR /app

# Copy built deps from builder (most stable layers first)
COPY --from=builder /build/node_modules ./node_modules
COPY --from=builder /build/frontend/node_modules ./frontend/node_modules
COPY --from=builder /build/backend/.venv ./backend/.venv
COPY --from=builder /build/backend/.venv-simulation ./backend/.venv-simulation

# App source (changes most frequently - last COPY, with ownership)
COPY --chown=mirofish:mirofish . .

USER mirofish

# Python optimizations: no bytecode, unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 3000 5001

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD wget --no-verbose --tries=1 --spider http://localhost:5001/ || exit 1

CMD ["npm", "run", "dev"]
