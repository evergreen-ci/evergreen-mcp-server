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

# OIDC Authentication (recommended):
#   Mount token files to standard locations inside container:
#     -v ~/.kanopy:/home/evergreen/.kanopy:ro
#     -v ~/.evergreen.yml:/home/evergreen/.evergreen.yml:ro
# 
# API Key Authentication (legacy):
#   Pass credentials via environment variables:
#     -e EVERGREEN_USER=your_username
#     -e EVERGREEN_API_KEY=your_api_key
#
# Optional configuration:
#   -e EVERGREEN_PROJECT=mongodb-mongo-master  # Default project identifier
#   -e WORKSPACE_PATH=/workspace               # Workspace path for project detection
#
# Example usage:
#   docker run --rm -i \
#     -v ~/.kanopy:/home/evergreen/.kanopy:ro \
#     -v ~/.evergreen.yml:/home/evergreen/.evergreen.yml:ro \
#     ghcr.io/evergreen-ci/evergreen-mcp-server:latest

# Set default token file path for Docker
# This overrides the path in ~/.evergreen.yml which has host paths
ENV EVERGREEN_TOKEN_FILE=/home/evergreen/.kanopy/token-oidclogin.json

# Set entry point
ENTRYPOINT ["evergreen-mcp-server"]

# Labels
LABEL maintainer="MongoDB"
LABEL description="Evergreen MCP Server - A server for interacting with the Evergreen API"
LABEL version=${VERSION}
LABEL org.opencontainers.image.source="https://github.com/evergreen-ci/evergreen-mcp-server"
