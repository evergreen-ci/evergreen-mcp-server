FROM python:3.13-alpine
ARG VERSION=0.1.0
RUN addgroup -S evergreen && adduser -S evergreen -G evergreen
COPY . /app
WORKDIR /app
RUN pip install --no-cache-dir -e .
USER evergreen
ENTRYPOINT ["evergreen-mcp-server"]
LABEL maintainer="MongoDB"
LABEL description="Evergreen MCP Server - A server for interacting with the Evergreen API"
LABEL version=${VERSION}
