---
description: Force-refresh Evergreen OIDC tokens via CLI. Run this BEFORE any MCP tool call if you get authentication errors (401, expired token, unauthorized).
---

# Evergreen Auth — Token Refresh

## Core rule

**If any Evergreen MCP tool returns an auth error (401, expired token, unauthorized), do NOT retry the tool. Refresh tokens first.**

The Evergreen MCP server reads credentials from `~/.evergreen.yml` and `~/.kanopy/token-oidclogin.json`. OIDC tokens expire frequently — the MCP server may still be running but its cached token is stale. Refreshing via CLI writes a fresh token to disk that the server picks up on the next request.

## Force-refresh tokens

Run this on the host before attempting MCP work:

```bash
evergreen login
```

This re-authenticates with OIDC and writes fresh tokens to `~/.evergreen.yml` and `~/.kanopy/token-oidclogin.json`.

If the CLI is not installed:
```bash
brew install mongodb/brew/evergreen
```
Alternative: [GitHub releases](https://github.com/evergreen-ci/evergreen/releases).

## When to refresh

| Symptom | Action |
|---|---|
| 401 / Unauthorized from any MCP tool | Run `evergreen login`, then retry |
| "token expired" in error message | Run `evergreen login`, then retry |
| MCP tools return empty/generic errors | Run `evergreen login`, then retry |
| Intermittent auth failures across sessions | Consider API key auth instead (`EVERGREEN_USER` + `EVERGREEN_API_KEY` env vars) |