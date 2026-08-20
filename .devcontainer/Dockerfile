FROM mcr.microsoft.com/devcontainers/python:3.13-bookworm

# --------------------------------------------------
# System dependencies
# --------------------------------------------------

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        wget \
        jq \
        unzip \
        ca-certificates \
        build-essential \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# --------------------------------------------------
# uv
# --------------------------------------------------

COPY --from=ghcr.io/astral-sh/uv:0.11.32 \
    /uv \
    /uvx \
    /usr/local/bin/

# --------------------------------------------------
# just
# --------------------------------------------------

COPY --from=ghcr.io/casey/just:1.58.0 \
    /just \
    /usr/local/bin/just

# --------------------------------------------------
# Environment
# --------------------------------------------------

ENV UV_LINK_MODE=copy \
    UV_CACHE_DIR=/home/vscode/.cache/uv \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /workspaces/finflow_v2