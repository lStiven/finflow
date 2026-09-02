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
# Chromium's shared libraries, for the headless browser
# --------------------------------------------------
#
# There is no browser in this container's editor, so a screen that has only
# been type-checked has never been *seen*. `just shot` drives a headless
# Chromium over the running app; these are what that binary links against.
#
# Baked here rather than installed on first use because they need root and
# because a layer is cached, where a postCreate step pays for itself on every
# rebuild. The **browser itself** is not here: it is downloaded after
# `npm ci`, where its version can be matched to the `playwright` package that
# will drive it. A binary pinned in this file would drift from that package
# the first time it was upgraded, and Playwright refuses to run a mismatch.
#
# This is `playwright install-deps chromium` for Playwright 1.62 on Debian 12,
# captured from what it actually installed rather than guessed. If a later
# version wants more, Chromium fails to launch naming the missing library and
#   sudo $(command -v node) frontend/node_modules/playwright/cli.js install-deps chromium
# installs it — move whatever that adds into this list.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fonts-freefont-ttf \
        fonts-liberation \
        fonts-noto-color-emoji \
        fonts-unifont \
        libasound2 \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libatspi2.0-0 \
        libcairo2 \
        libcups2 \
        libdbus-1-3 \
        libdrm2 \
        libfontconfig1 \
        libfreetype6 \
        libgbm1 \
        libglib2.0-0 \
        libnspr4 \
        libnss3 \
        libpango-1.0-0 \
        libx11-6 \
        libxcb1 \
        libxcomposite1 \
        libxdamage1 \
        libxext6 \
        libxfixes3 \
        libxkbcommon0 \
        libxrandr2 \
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