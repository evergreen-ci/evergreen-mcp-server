"""FastMCP server for Evergreen

This module provides the main MCP server using FastMCP framework.
It handles server lifecycle, configuration, and tool registration.
"""

import argparse
import logging
import os
import os.path
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

import yaml
from fastmcp import Context, FastMCP

# Add src directory to path for imports when running directly
_src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

from evergreen_mcp.evergreen_graphql_client import EvergreenGraphQLClient

__version__ = "0.4.0"

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class EvergreenContext:
    """Context object holding the Evergreen client and configuration."""

    client: EvergreenGraphQLClient
    user_id: str
    default_project_id: str | None = None


def detect_project_from_workspace(
    config_data: dict, workspace_dir: str = None
) -> str | None:
    """Detect project ID from workspace directory using Evergreen config

    Args:
        config_data: Parsed ~/.evergreen.yml configuration
        workspace_dir: Workspace directory path (optional)

    Returns:
        Detected project ID or None if no match found
    """
    if not workspace_dir:
        workspace_dir = os.getenv("WORKSPACE_PATH") or os.getenv("PWD") or os.getcwd()

    if not workspace_dir:
        logger.debug("No workspace directory available for project detection")
        return None

    workspace_dir = os.path.abspath(os.path.expanduser(workspace_dir))

    projects_for_directory = config_data.get("projects_for_directory", {})

    if not projects_for_directory:
        logger.debug("No projects_for_directory section in config")
        return None

    logger.debug("Checking workspace: %s", workspace_dir)
    logger.debug("Available project mappings: %s", projects_for_directory)

    if workspace_dir in projects_for_directory:
        project_id = projects_for_directory[workspace_dir]
        logger.info("Exact match found: %s -> %s", workspace_dir, project_id)
        return project_id

    best_match = None
    best_match_len = 0

    for config_path, project_id in projects_for_directory.items():
        config_path = os.path.abspath(os.path.expanduser(config_path))

        try:
            common = os.path.commonpath([workspace_dir, config_path])
            if common == config_path and len(config_path) > best_match_len:
                best_match = project_id
                best_match_len = len(config_path)
        except ValueError:
            continue

    if best_match:
        logger.info("Parent directory match found: %s", best_match)
        return best_match

    logger.debug("No project match found for workspace: %s", workspace_dir)
    return None


def load_evergreen_config() -> tuple[dict, str | None]:
    """Load Evergreen configuration from environment or config file.

    Returns:
        Tuple of (config dict, default project ID)
    """
    # Check for environment variables first (Docker setup)
    evergreen_user = os.getenv("EVERGREEN_USER")
    evergreen_api_key = os.getenv("EVERGREEN_API_KEY")
    evergreen_project = os.getenv("EVERGREEN_PROJECT")
    workspace_dir = os.getenv("WORKSPACE_PATH")

    if evergreen_user and evergreen_api_key:
        # Use environment variables (Docker setup)
        logger.info("Using environment variables for Evergreen configuration")
        evergreen_config = {
            "user": evergreen_user,
            "api_key": evergreen_api_key,
        }
    else:
        # Fall back to config file (local setup)
        logger.info("Using ~/.evergreen.yml for Evergreen configuration")
        with open(os.path.expanduser("~/.evergreen.yml"), mode="rb") as f:
            evergreen_config = yaml.safe_load(f)

    # Determine default project ID
    default_project_id = None

    # Try auto-detection from workspace
    detected_project = detect_project_from_workspace(evergreen_config, workspace_dir)
    if detected_project:
        default_project_id = detected_project
        logger.info("Auto-detected project ID from workspace: %s", default_project_id)

    # Fall back to EVERGREEN_PROJECT environment variable
    if not default_project_id and evergreen_project:
        default_project_id = evergreen_project
        logger.info("Using project ID from environment: %s", default_project_id)

    return evergreen_config, default_project_id


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[EvergreenContext]:
    """Server lifespan manager - handles GraphQL client lifecycle.

    This context manager initializes the Evergreen GraphQL client on startup
    and ensures proper cleanup on shutdown.
    """
    evergreen_config, default_project_id = load_evergreen_config()

    client = EvergreenGraphQLClient(
        user=evergreen_config["user"], api_key=evergreen_config["api_key"]
    )

    async with client:
        logger.info("Evergreen GraphQL client initialized")
        if default_project_id:
            logger.info("Default project ID configured: %s", default_project_id)
        else:
            logger.info(
                "No default project ID configured - "
                "tools will require explicit project_id parameter"
            )

        yield EvergreenContext(
            client=client,
            user_id=evergreen_config["user"],
            default_project_id=default_project_id,
        )

    logger.info("Evergreen GraphQL client closed")


# Create the FastMCP server instance
mcp = FastMCP(
    "Evergreen MCP Server",
    version=__version__,
    lifespan=lifespan,
)


# Import and register tools after mcp is created
from evergreen_fast_mcp.tools import register_tools  # noqa: E402

register_tools(mcp)


# Register resources
@mcp.resource("evergreen://projects")
async def list_projects_resource(ctx: Context) -> str:
    """List all Evergreen projects as a resource."""
    import json

    evg_ctx = ctx.request_context.lifespan_context
    try:
        projects = await evg_ctx.client.get_projects()
        return json.dumps(
            [
                {
                    "id": p.get("id"),
                    "identifier": p.get("identifier"),
                    "displayName": p.get("displayName"),
                    "enabled": p.get("enabled"),
                    "owner": p.get("owner"),
                    "repo": p.get("repo"),
                }
                for p in projects
            ],
            indent=2,
        )
    except Exception as e:
        logger.error("Failed to fetch projects resource: %s", e)
        return json.dumps({"error": str(e)})


def main() -> None:
    """Main entry point for the FastMCP server."""
    logger.info("Starting Evergreen FastMCP Server v%s...", __version__)

    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Evergreen FastMCP Server")
    parser.add_argument(
        "--project-id",
        type=str,
        help="Default Evergreen project identifier (optional)",
    )
    parser.add_argument(
        "--workspace-dir",
        type=str,
        help="Workspace directory for auto-detecting project ID (optional)",
    )

    args = parser.parse_args()

    # Set workspace directory as environment variable if provided
    if args.workspace_dir:
        os.environ["WORKSPACE_PATH"] = args.workspace_dir
        logger.info("Using workspace directory: %s", args.workspace_dir)

    # Set project ID as environment variable if provided (takes precedence)
    if args.project_id:
        os.environ["EVERGREEN_PROJECT"] = args.project_id
        logger.info("Using explicit project ID: %s", args.project_id)

    logger.info("Starting FastMCP server...")
    mcp.run()


if __name__ == "__main__":
    main()

