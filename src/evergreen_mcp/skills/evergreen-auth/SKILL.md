---
description: Install and configure the Evergreen MCP server — handles auth prerequisites and common failure fixes.
---

# Evergreen MCP Setup & Auth

## Prerequisite (must do first)

The MCP server reads credentials from the Evergreen CLI. Run this once on your host:

```bash
evergreen login
```

This creates `~/.evergreen.yml` and `~/.kanopy/token-oidclogin.json` — the server reads these automatically.

If CLI not installed: `brew install mongodb/brew/evergreen` or [GitHub releases](https://github.com/evergreen-ci/evergreen/releases).

## Install MCP server

**uvx (simplest)**:
```json
{
  "command": "uvx",
  "args": ["--from=git+https://github.com/evergreen-ci/evergreen-mcp-server", "evergreen-mcp-server"]
}
```

**Docker** (mounts credentials as read-only):
```json
{
  "command": "docker",
  "args": [
    "run", "--rm", "-i",
    "-v", "${HOME}/.kanopy/token-oidclogin.json:/home/evergreen/.kanopy/token-oidclogin.json:ro",
    "-v", "${HOME}/.evergreen.yml:/home/evergreen/.evergreen.yml:ro",
    "ghcr.io/evergreen-ci/evergreen-mcp-server:latest"
  ]
}
```

## Auth not working?

The fix is almost always just re-running `evergreen login` to refresh expired tokens.

Other causes:

| Problem | Fix |
|---|---|
| `evergreen login` needed | Run `evergreen login` |
| Docker can't read files | Permissions need `600` or `644` |
| VS Code can't find files | Use `${userHome}` not `${HOME}` |
| Docker test | `docker run --rm -it -v ~/.kanopy/token-oidclogin.json:... -v ~/.evergreen.yml:... ghcr.io/evergreen-ci/evergreen-mcp-server:latest --help` |
| uvx can't find creds | Files at `~/.evergreen.yml` and `~/.kanopy/token-oidclogin.json`? Run `evergreen login` |
| Docker API key mode | Both `EVERGREEN_USER` and `EVERGREEN_API_KEY` env vars required |
| Per-request mode | `EVERGREEN_AUTH_MODE=per_request` — gateway must inject `bearer_token` per tool call |
| Intermittent 401 | Multiple sessions sharing token file. Use API key auth instead |