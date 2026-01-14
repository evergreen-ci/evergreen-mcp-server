FROM python:3.13-alpine

ARG VERSION=0.4.0

# Create non-root user
RUN addgroup -S evergreen && adduser -S evergreen -G evergreen

# Set working directory
WORKDIR /app

# Copy project files
COPY . /app

# Install the package
RUN pip install --no-cache-dir -e .

# Switch to non-root user
USER evergreen

# Set default token file path for Docker
# This overrides the path in ~/.evergreen.yml which has host paths
ENV EVERGREEN_TOKEN_FILE=/home/evergreen/.kanopy/token-oidclogin.json

# Telemetry: Sentry is disabled by default for error monitoring
# Set SENTRY_ENABLED=true to enable telemetry
ENV SENTRY_ENABLED=false

# Set entry point
ENTRYPOINT ["evergreen-mcp-server"]

# Labels
LABEL maintainer="MongoDB"
LABEL description="Evergreen MCP Server - A server for interacting with the Evergreen API"
LABEL version=${VERSION}
LABEL org.opencontainers.image.source="https://github.com/evergreen-ci/evergreen-mcp-server"
