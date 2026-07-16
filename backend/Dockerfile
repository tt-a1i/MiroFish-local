FROM python:3.11-slim

ARG UV_HTTP_TIMEOUT=300

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_HTTP_TIMEOUT=${UV_HTTP_TIMEOUT} \
    UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    PATH="/root/.local/bin:${PATH}"

WORKDIR /app/backend

RUN sed -i "s@http://deb.debian.org@https://mirrors.aliyun.com@g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ca-certificates \
    wget \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ uv

COPY backend /app/backend

RUN uv sync --extra graphiti
RUN uv venv .venv-simulation --python 3.11
RUN uv pip install --python .venv-simulation/bin/python \
    camel-oasis==0.2.5 \
    camel-ai==0.2.78 \
    openai \
    python-dotenv
COPY .env.example /app/.env.example

RUN chmod +x /app/backend/docker/entrypoint.sh

EXPOSE 5001

ENTRYPOINT ["/app/backend/docker/entrypoint.sh"]
