"""MCP tool definitions and handlers for Evergreen server

This module contains all MCP tool definitions, schemas, and handler functions
to keep the main server.py file clean and focused on server lifecycle management.
"""

import json
import logging
from collections.abc import Sequence
from typing import Any, Dict

import mcp.types as types

from .failed_jobs_tools import (
    fetch_patch_failed_jobs,
    fetch_task_logs,
    fetch_user_recent_patches,
)

logger = logging.getLogger(__name__)


def get_tool_definitions() -> Sequence[types.Tool]:
    """Get all MCP tool definitions."""
    return [
        types.Tool(
            name="list_user_recent_patches",
            description="Retrieve the authenticated user's recent Evergreen patches/commits with their CI/CD status. Use this to see your recent code changes, check patch status (success/failed/running), and identify patches that need attention. Returns patch IDs needed for other tools.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Number of recent patches to return. Use smaller numbers (3-5) for quick overview, larger (10-20) for comprehensive analysis. Maximum 50.",
                        "default": 10,
                        "minimum": 1,
                        "maximum": 50,
                    }
                },
                "required": [],
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="get_patch_failed_jobs",
            description="Analyze failed CI/CD jobs for a specific patch to understand why builds are failing. Shows detailed failure information including failed tasks, build variants, timeout issues, and log links. Essential for debugging patch failures.",
            inputSchema={
                "type": "object",
                "properties": {
                    "patch_id": {
                        "type": "string",
                        "description": "Patch identifier obtained from list_user_recent_patches. This is the 'patch_id' field from the patches array.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of failed tasks to analyze. Use 10-20 for focused analysis, 50+ for comprehensive failure review.",
                        "default": 50,
                        "minimum": 1,
                        "maximum": 100,
                    },
                },
                "required": ["patch_id"],
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="get_task_logs",
            description="Extract detailed logs from a specific failed Evergreen task to identify root cause of failures. Filters for error messages by default to focus on relevant failure information. Use task_id from get_patch_failed_jobs results.",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "Task identifier from get_patch_failed_jobs response. Found in the 'task_id' field of failed_tasks array.",
                    },
                    "execution": {
                        "type": "integer",
                        "description": "Task execution number if task was retried. Usually 0 for first execution, 1+ for retries.",
                        "default": 0,
                        "minimum": 0,
                    },
                    "max_lines": {
                        "type": "integer",
                        "description": "Maximum log lines to return. Use 100-500 for quick error analysis, 1000+ for comprehensive debugging.",
                        "default": 1000,
                        "minimum": 10,
                        "maximum": 5000,
                    },
                    "filter_errors": {
                        "type": "boolean",
                        "description": "Whether to show only error/failure messages (recommended) or all log output. Set to false only when you need complete context.",
                        "default": True,
                    },
                },
                "required": ["task_id"],
                "additionalProperties": False,
            },
        ),
    ]


async def handle_list_user_recent_patches(
    arguments: Dict[str, Any], client, user_id: str
) -> Sequence[types.TextContent]:
    """Handle list_user_recent_patches tool call"""
    try:
        limit = arguments.get("limit", 10)
        result = await fetch_user_recent_patches(client, user_id, limit)
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]
    except Exception as e:
        logger.error("Failed to fetch user patches: %s", e)
        error_response = {
            "error": str(e),
            "tool": "list_user_recent_patches",
            "arguments": arguments,
        }
        return [
            types.TextContent(type="text", text=json.dumps(error_response, indent=2))
        ]


async def handle_get_patch_failed_jobs(
    arguments: Dict[str, Any], client
) -> Sequence[types.TextContent]:
    """Handle get_patch_failed_jobs tool call"""
    try:
        patch_id = arguments.get("patch_id")
        if not patch_id:
            raise ValueError("patch_id parameter is required")

        max_results = arguments.get("max_results", 50)
        result = await fetch_patch_failed_jobs(client, patch_id, max_results)
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]
    except Exception as e:
        logger.error("Failed to fetch patch failed jobs: %s", e)
        error_response = {
            "error": str(e),
            "tool": "get_patch_failed_jobs",
            "arguments": arguments,
        }
        return [
            types.TextContent(type="text", text=json.dumps(error_response, indent=2))
        ]


async def handle_get_task_logs(
    arguments: Dict[str, Any], client
) -> Sequence[types.TextContent]:
    """Handle get_task_logs tool call"""
    try:
        result = await fetch_task_logs(client, arguments)
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]
    except Exception as e:
        logger.error("Failed to fetch task logs: %s", e)
        error_response = {
            "error": str(e),
            "tool": "get_task_logs",
            "arguments": arguments,
        }
        return [
            types.TextContent(type="text", text=json.dumps(error_response, indent=2))
        ]


# Tool handler registry for easy lookup
TOOL_HANDLERS = {
    "list_user_recent_patches": handle_list_user_recent_patches,
    "get_patch_failed_jobs": handle_get_patch_failed_jobs,
    "get_task_logs": handle_get_task_logs,
}
