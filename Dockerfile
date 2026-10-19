FROM python:3.13-alpine

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

RUN addgroup -S evergreen && adduser -S evergreen -G evergreen

WORKDIR /app

# Install dependencies before copying source for better layer caching.
# The cache mount keeps uv's download/build cache across builds, so even when a
# version bump changes pyproject.toml/uv.lock and invalidates this layer, deps
# are served from cache instead of re-downloaded (UV_LINK_MODE=copy above is set
# for exactly this mount).
COPY pyproject.toml uv.lock /app/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Copy source and install the local package
COPY src /app/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

USER evergreen

# Set default token file path for Docker
# This overrides the path in ~/.evergreen.yml which has host paths
ENV EVERGREEN_TOKEN_FILE=/home/evergreen/.kanopy/token-oidclogin.json

ENTRYPOINT ["/app/.venv/bin/evergreen-mcp-server"]

LABEL maintainer="MongoDB"
LABEL description="Evergreen MCP Server - A server for interacting with the Evergreen API"
LABEL org.opencontainers.image.source="https://github.com/evergreen-ci/evergreen-mcp-server"
