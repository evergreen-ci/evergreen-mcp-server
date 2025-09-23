"""MCP tool definitions and handlers for Evergreen server

This module contains all MCP tool definitions, schemas, and handler functions
to keep the main server.py file clean and focused on server lifecycle management.
"""

import json
import logging
from collections.abc import Sequence
from typing import Dict, Any

import mcp.types as types

try:
    from .failed_jobs_tools import fetch_user_recent_patches, fetch_patch_failed_jobs, fetch_task_logs
except ImportError:
    # For standalone usage
    from failed_jobs_tools import fetch_user_recent_patches, fetch_patch_failed_jobs, fetch_task_logs

logger = logging.getLogger(__name__)


def get_tool_definitions() -> Sequence[types.Tool]:
    """Get all MCP tool definitions"""
    return [
        types.Tool(
            name="list_user_recent_patches",
            description="List recent patches for the authenticated user",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Number of patches to return (default: 10, max: 50)",
                        "default": 10,
                        "minimum": 1,
                        "maximum": 50
                    }
                },
                "required": []
            }
        ),
        types.Tool(
            name="get_patch_failed_jobs",
            description="Get failed jobs for a specific patch",
            inputSchema={
                "type": "object",
                "properties": {
                    "patch_id": {
                        "type": "string",
                        "description": "Patch identifier from list_user_recent_patches (required)"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of failed tasks to return (default: 50)",
                        "default": 50
                    }
                },
                "required": ["patch_id"]
            }
        ),
        types.Tool(
            name="get_task_logs",
            description="Get detailed logs for a specific Evergreen task",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "Task identifier (required)"
                    },
                    "execution": {
                        "type": "integer",
                        "description": "Task execution number (default: 0)",
                        "default": 0
                    },
                    "max_lines": {
                        "type": "integer",
                        "description": "Maximum number of log lines to return (default: 1000)",
                        "default": 1000
                    },
                    "filter_errors": {
                        "type": "boolean",
                        "description": "Whether to filter for error messages only (default: True)",
                        "default": True
                    }
                },
                "required": ["task_id"]
            }
        )
    ]


async def handle_list_user_recent_patches(
    arguments: Dict[str, Any], 
    client, 
    user_id: str
) -> Sequence[types.TextContent]:
    """Handle list_user_recent_patches tool call"""
    try:
        limit = arguments.get('limit', 10)
        result = await fetch_user_recent_patches(client, user_id, limit)
        return [types.TextContent(
            type="text",
            text=json.dumps(result, indent=2)
        )]
    except Exception as e:
        logger.error(f"Failed to fetch user patches: {e}")
        error_response = {
            "error": str(e),
            "tool": "list_user_recent_patches",
            "arguments": arguments
        }
        return [types.TextContent(
            type="text",
            text=json.dumps(error_response, indent=2)
        )]


async def handle_get_patch_failed_jobs(
    arguments: Dict[str, Any], 
    client
) -> Sequence[types.TextContent]:
    """Handle get_patch_failed_jobs tool call"""
    try:
        patch_id = arguments.get('patch_id')
        if not patch_id:
            raise ValueError("patch_id parameter is required")
        
        max_results = arguments.get('max_results', 50)
        result = await fetch_patch_failed_jobs(client, patch_id, max_results)
        return [types.TextContent(
            type="text",
            text=json.dumps(result, indent=2)
        )]
    except Exception as e:
        logger.error(f"Failed to fetch patch failed jobs: {e}")
        error_response = {
            "error": str(e),
            "tool": "get_patch_failed_jobs",
            "arguments": arguments
        }
        return [types.TextContent(
            type="text",
            text=json.dumps(error_response, indent=2)
        )]


async def handle_get_task_logs(
    arguments: Dict[str, Any], 
    client
) -> Sequence[types.TextContent]:
    """Handle get_task_logs tool call"""
    try:
        result = await fetch_task_logs(client, arguments)
        return [types.TextContent(
            type="text",
            text=json.dumps(result, indent=2)
        )]
    except Exception as e:
        logger.error(f"Failed to fetch task logs: {e}")
        error_response = {
            "error": str(e),
            "tool": "get_task_logs",
            "arguments": arguments
        }
        return [types.TextContent(
            type="text",
            text=json.dumps(error_response, indent=2)
        )]


# Tool handler registry for easy lookup
TOOL_HANDLERS = {
    "list_user_recent_patches": handle_list_user_recent_patches,
    "get_patch_failed_jobs": handle_get_patch_failed_jobs,
    "get_task_logs": handle_get_task_logs,
}
