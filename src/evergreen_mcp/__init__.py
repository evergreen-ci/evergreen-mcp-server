"""Evergreen FastMCP Server package

This is the upgraded version of the Evergreen MCP Server using FastMCP.
"""

__version__ = "0.4.0"

import logging
import os
import sys

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

SENTRY_DSN = os.getenv(
    "SENTRY_DSN",
    "https://14073ac4115b2196bafcca18270a3a12@o4504991346720768.ingest.us.sentry.io/4510699515478016",
)


if os.getenv("SENTRY_ENABLED", "false").lower() == "true":
    sentry_sdk.init(
        dsn=os.getenv("SENTRY_DSN", SENTRY_DSN),
        traces_sample_rate=1.0,
        send_default_pii=True,
        shutdown_timeout=10,  # Give more time to flush events before process exit
        integrations=[
            LoggingIntegration(level=logging.INFO, event_level=logging.CRITICAL),
        ],
    )
    sys.stderr.write("Sentry MCP observability enabled")
