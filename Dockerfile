FROM python:3.13-alpine

ARG VERSION=0.1.0

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

# Set entry point
ENTRYPOINT ["evergreen-mcp-server"]

# Labels
LABEL maintainer="MongoDB"
LABEL description="Evergreen MCP Server - A server for interacting with the Evergreen API"
LABEL version=${VERSION}
