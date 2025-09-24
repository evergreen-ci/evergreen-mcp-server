"""MCP server for Evergreen"""

from asyncio import run
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator, Sequence
import os.path
import sys
import logging
import argparse
import json

import mcp.server.stdio
import mcp.types as types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.models import InitializationOptions
import yaml

try:
    from .evergreen_graphql_client import EvergreenGraphQLClient
    from .mcp_tools import get_tool_definitions, TOOL_HANDLERS
except ImportError:
    # For standalone usage
    from evergreen_graphql_client import EvergreenGraphQLClient
    from mcp_tools import get_tool_definitions, TOOL_HANDLERS

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global configuration
USER_ID = None
DEFAULT_PROJECT_ID = None


@asynccontextmanager
async def _server_lifespan(_) -> AsyncIterator[dict]:
    """Server lifespan manager - handles GraphQL client lifecycle"""
    global USER_ID

    with open(os.path.expanduser("~/.evergreen.yml"), mode="rb") as f:
        evergreen_config = yaml.safe_load(f)

    client = EvergreenGraphQLClient(
        user=evergreen_config["user"],
        api_key=evergreen_config["api_key"]
    )

    # Store user ID for patch queries
    USER_ID = evergreen_config["user"]

    async with client:
        logger.info("Evergreen GraphQL client initialized")
        if DEFAULT_PROJECT_ID:
            logger.info(f"Default project ID configured: {DEFAULT_PROJECT_ID}")
        yield {"evergreen_client": client}

server: Server = Server("evergreen-mcp-server", lifespan=_server_lifespan)

@server.list_resources()
async def _handle_project_resources() -> Sequence[types.Resource]:
    """Handle project resources using GraphQL client"""
    client = server.request_context.lifespan_context["evergreen_client"]

    try:
        projects = await client.get_projects()
        logger.info(f"Retrieved {len(projects)} projects for resource listing")

        return list(map(lambda project: types.Resource(
            uri=f"evergreen://project/{project['id']}",
            name=project['displayName'],
            mimeType="application/json",
        ), projects))
    except Exception as e:
        logger.error(f"Failed to retrieve projects: {e}")
        # Return empty list on error to prevent server crash
        return []


@server.list_tools()
async def _handle_list_tools() -> Sequence[types.Tool]:
    """List available MCP tools"""
    tools = get_tool_definitions()
    logger.info(f"📋 Listing {len(tools)} available tools:")
    for tool in tools:
        logger.info(f"   - {tool.name}: {tool.description}")
    return tools


@server.call_tool()
async def _handle_call_tool(name: str, arguments: dict) -> Sequence[types.TextContent]:
    """Handle MCP tool calls by delegating to appropriate handlers"""
    logger.info(f"🔧 Tool call received: {name}")
    logger.info(f"   Arguments: {json.dumps(arguments, indent=2)}")

    client = server.request_context.lifespan_context["evergreen_client"]

    # Get the handler for this tool
    handler = TOOL_HANDLERS.get(name)
    if not handler:
        logger.error(f"❌ Unknown tool requested: {name}")
        logger.info(f"   Available tools: {list(TOOL_HANDLERS.keys())}")
        error_response = {
            "error": f"Unknown tool: {name}",
            "available_tools": list(TOOL_HANDLERS.keys())
        }
        return [types.TextContent(
            type="text",
            text=json.dumps(error_response, indent=2)
        )]

    # Call the appropriate handler
    try:
        logger.debug(f"Executing tool: {name}")
        if name == "list_user_recent_patches":
            result = await handler(arguments, client, USER_ID)
        else:
            result = await handler(arguments, client)
        logger.debug(f"Tool {name} completed successfully")
        return result
    except Exception as e:
        logger.error(f"Tool handler failed for {name}: {e}")
        import traceback
        logger.debug(f"Full traceback: {traceback.format_exc()}")
        error_response = {
            "error": f"Tool execution failed: {str(e)}",
            "tool": name
            # Removed arguments to avoid logging potentially sensitive data
        }
        return [types.TextContent(
            type="text",
            text=json.dumps(error_response, indent=2)
        )]


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
        help="Default Evergreen project identifier"
    )

    args = parser.parse_args()

    # Set global project ID if provided
    if args.project_id:
        DEFAULT_PROJECT_ID = args.project_id
        logger.info(f"Using default project ID: {DEFAULT_PROJECT_ID}")

    logger.info("Initializing MCP server...")
    try:
        sys.exit(run(_main()))
    except Exception as e:
        logger.error(f"Server failed to start: {e}")
        sys.exit(1)
