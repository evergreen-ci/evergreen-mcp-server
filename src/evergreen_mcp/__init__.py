"""Evergreen FastMCP Server package

This is the upgraded version of the Evergreen MCP Server using FastMCP.
"""

from evergreen_mcp.oidc_auth import OIDCAuthenticationError

__version__ = "0.4.0"

__all__ = ["OIDCAuthenticationError", "__version__"]
