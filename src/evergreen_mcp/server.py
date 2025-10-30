"""MCP server for Evergreen"""

import argparse
import json
import logging
import os
import os.path
import sys
from asyncio import run
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import mcp.server.stdio
import mcp.types as types
import yaml
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.models import InitializationOptions

from .evergreen_graphql_client import EvergreenGraphQLClient
from .mcp_tools import TOOL_HANDLERS, get_tool_definitions

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global configuration
USER_ID = None
DEFAULT_PROJECT_ID = None


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


@asynccontextmanager
async def _server_lifespan(_) -> AsyncIterator[dict]:
    """Server lifespan manager - handles GraphQL client lifecycle"""
    global USER_ID, DEFAULT_PROJECT_ID

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

        # Set default project ID from environment if provided and not set
        if evergreen_project and not DEFAULT_PROJECT_ID:
            DEFAULT_PROJECT_ID = evergreen_project
            logger.info("Using project ID from environment: %s", DEFAULT_PROJECT_ID)
    else:
        # Fall back to config file (local setup)
        logger.info("Using ~/.evergreen.yml for Evergreen configuration")
        with open(os.path.expanduser("~/.evergreen.yml"), mode="rb") as f:
            evergreen_config = yaml.safe_load(f)

    if not DEFAULT_PROJECT_ID:
        detected_project = detect_project_from_workspace(
            evergreen_config, workspace_dir
        )
        if detected_project:
            DEFAULT_PROJECT_ID = detected_project
            logger.info(
                "Auto-detected project ID from workspace: %s", DEFAULT_PROJECT_ID
            )

    client = EvergreenGraphQLClient(
        user=evergreen_config["user"], api_key=evergreen_config["api_key"]
    )

    # Store user ID for patch queries
    USER_ID = evergreen_config["user"]

    async with client:
        logger.info("Evergreen GraphQL client initialized")
        if DEFAULT_PROJECT_ID:
            logger.info("Default project ID configured: %s", DEFAULT_PROJECT_ID)
        else:
            logger.info(
                "No default project ID configured - tools will require explicit project_id parameter"
            )
        yield {"evergreen_client": client}


server: Server = Server("evergreen-mcp-server", lifespan=_server_lifespan)


@server.list_resources()
async def _handle_project_resources() -> Sequence[types.Resource]:
    """Handle project resources using GraphQL client"""
    client = server.request_context.lifespan_context["evergreen_client"]

    try:
        projects = await client.get_projects()
        logger.info("Retrieved %s projects for resource listing", len(projects))

        return list(
            map(
                lambda project: types.Resource(
                    uri=f"evergreen://project/{project['id']}",
                    name=project["displayName"],
                    mimeType="application/json",
                ),
                projects,
            )
        )
    except Exception:
        logger.error("Failed to retrieve projects", exc_info=True)
        # Return empty list on error to prevent server crash
        return []


@server.list_tools()
async def _handle_list_tools() -> Sequence[types.Tool]:
    """List available MCP tools"""
    tools = get_tool_definitions()
    logger.info("Listing %s available tools:", len(tools))
    for tool in tools:
        logger.info("   - %s: %s", tool.name, tool.description)
    return tools


@server.call_tool()
async def _handle_call_tool(name: str, arguments: dict) -> Sequence[types.TextContent]:
    """Handle MCP tool calls by delegating to appropriate handlers"""
    logger.info("Tool call received: %s", name)
    logger.info("   Arguments: %s", json.dumps(arguments, indent=2))

    client = server.request_context.lifespan_context["evergreen_client"]

    # Get the handler for this tool
    handler = TOOL_HANDLERS.get(name)
    if not handler:
        logger.error("Unknown tool requested: %s", name)
        logger.info("   Available tools: %s", list(TOOL_HANDLERS.keys()))
        error_response = {
            "error": f"Unknown tool: {name}",
            "available_tools": list(TOOL_HANDLERS.keys()),
        }
        return [
            types.TextContent(type="text", text=json.dumps(error_response, indent=2))
        ]

    # Call the appropriate handler
    try:
        logger.debug("Executing tool: %s", name)
        if name == "list_user_recent_patches_evergreen":
            result = await handler(arguments, client, USER_ID)
        else:
            result = await handler(arguments, client)
        logger.debug("Tool %s completed successfully", name)
        return result
    except Exception as e:
        logger.error("Tool handler failed for %s", name, exc_info=True)
        error_response = {
            "error": f"Tool execution failed: {str(e)}",
            "tool": name,
            # Removed arguments to avoid logging potentially sensitive data
        }
        return [
            types.TextContent(type="text", text=json.dumps(error_response, indent=2))
        ]


async def _main() -> int:
    logger.info("Setting up MCP stdio server...")
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        logger.info("MCP server running and ready for connections")
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="evergreen-mcp-server",
                server_version="0.1.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )
    logger.info("MCP server shutting down")
    return 0


def main() -> None:
    """Main entry point for the MCP server"""
    global DEFAULT_PROJECT_ID

    logger.info("Starting Evergreen MCP Server...")

    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Evergreen MCP Server")
    parser.add_argument(
        "--project-id",
        type=str,
        help="Default Evergreen project identifier (optional, can be auto-detected from workspace)",
    )
    parser.add_argument(
        "--workspace-dir",
        type=str,
        help="Workspace directory for auto-detecting project ID (optional, defaults to current directory)",
    )

    args = parser.parse_args()

    # Set workspace directory as environment variable if provided
    if args.workspace_dir:
        os.environ["WORKSPACE_PATH"] = args.workspace_dir
        logger.info("Using workspace directory: %s", args.workspace_dir)

    # Set global project ID if provided (takes precedence over auto-detection)
    if args.project_id:
        DEFAULT_PROJECT_ID = args.project_id
        logger.info("Using explicit project ID: %s", DEFAULT_PROJECT_ID)

    logger.info("Initializing MCP server...")
    try:
        sys.exit(run(_main()))
    except Exception:
        logger.error("Server failed to start", exc_info=True)
        sys.exit(1)
