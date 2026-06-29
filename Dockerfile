FROM python:3.13-alpine

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ARG VERSION=0.4.2

# Create non-root user
RUN addgroup -S evergreen && adduser -S evergreen -G evergreen

# Set working directory
WORKDIR /app

# Copy project files
COPY . /app

# Install the package using the lockfile, without dev dependencies
RUN uv sync --frozen --no-dev

# Switch to non-root user
USER evergreen

# Set default token file path for Docker
# This overrides the path in ~/.evergreen.yml which has host paths
ENV EVERGREEN_TOKEN_FILE=/home/evergreen/.kanopy/token-oidclogin.json

# Run the app via uv to ensure it uses the synced virtualenv
ENTRYPOINT ["uv", "run", "evergreen-mcp-server"]

# Labels
LABEL maintainer="MongoDB"
LABEL description="Evergreen MCP Server - A server for interacting with the Evergreen API"
LABEL version=${VERSION}
LABEL org.opencontainers.image.source="https://github.com/evergreen-ci/evergreen-mcp-server"
