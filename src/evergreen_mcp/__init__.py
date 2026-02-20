"""Evergreen FastMCP Server package

This is the upgraded version of the Evergreen MCP Server using FastMCP.
"""

__version__ = "0.4.0"
USER_AGENT = f"evergreen-mcp-server/{__version__}"

import os
import sys

import sentry_sdk
from sentry_sdk.integrations.mcp import MCPIntegration

from evergreen_mcp.utils import load_evergreen_config

SENTRY_DSN = os.getenv(
    "SENTRY_DSN",
    "https://14073ac4115b2196bafcca18270a3a12@o4504991346720768.ingest.us.sentry.io/4510699515478016",
)

FASTMCP_WRAPPER_EXCEPTIONS = {"ToolError", "ResourceError", "PromptError"}


def before_send(event, hint):
    """Filter duplicate exceptions from FastMCP's error handling.

    FastMCP logs exceptions with logger.exception() before raising wrapper
    exceptions (ToolError, ResourceError, PromptError). This causes Sentry
    to capture both the original exception and the wrapper, creating duplicates.

    We drop the wrapper exceptions since the original exception provides
    more useful debugging context with the actual failure location.
    """
    if "exc_info" in hint:
        exc_type, exc_value, _ = hint["exc_info"]
        if exc_type is not None:
            # Check if this is a FastMCP wrapper exception
            if exc_type.__name__ in FASTMCP_WRAPPER_EXCEPTIONS:
                # Drop this event - the original exception was already captured
                return None
    return event


if os.getenv("SENTRY_ENABLED", "false").lower() == "true":
    sentry_sdk.init(
        dsn=os.getenv("SENTRY_DSN", SENTRY_DSN),
        traces_sample_rate=1.0,
        send_default_pii=True,
        integrations=[MCPIntegration()],
        before_send=before_send,
    )
    sys.stderr.write("Sentry MCP observability enabled")

    # Set user context for Sentry - let ConfigParseError propagate
    # since a malformed config means the service can't work anyway
    config = load_evergreen_config()
    user_id = config.get("user")
    USER_AGENT += f"/user_id={user_id}"
    if user_id:
        sentry_sdk.set_user({"id": user_id, "username": user_id})
