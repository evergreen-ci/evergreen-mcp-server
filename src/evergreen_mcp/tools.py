"""FastMCP tool definitions for Evergreen server

This module contains all MCP tool definitions using FastMCP decorators.
Tools are registered with the FastMCP server instance.
"""

import json
import logging
from typing import Annotated

from fastmcp import Context, FastMCP

from .failed_jobs_tools import (
    fetch_patch_failed_jobs,
    fetch_task_logs,
    fetch_task_test_results,
    fetch_user_recent_patches,
)

logger = logging.getLogger(__name__)


def register_tools(mcp: FastMCP) -> None:
    """Register all tools with the FastMCP server."""

    @mcp.tool(
        description=(
            "Retrieve the authenticated user's recent Evergreen patches/commits "
            "with their CI/CD status. Use this to see your recent code changes, "
            "check patch status (success/failed/running), and identify patches "
            "that need attention. Returns patch IDs needed for other tools. "
            "\n\nBEST PRACTICE: If project_id is not provided, first call "
            "list_user_projects_evergreen to discover available projects, then "
            "correlate the project identifier to the current working directory "
            "(e.g., 'mongodb-mongo-master' for mongo repo) and use that as the "
            "project_id parameter."
        )
    )
    async def list_user_recent_patches_evergreen(
        ctx: Context,
        project_id: Annotated[
            str | None,
            "Evergreen project identifier (e.g., 'mongodb-mongo-master') to filter "
            "patches. If not known, call list_user_projects_evergreen first to "
            "discover available projects, then match the project to the current "
            "directory context.",
        ] = None,
        limit: Annotated[
            int,
            "Number of recent patches to return. Use smaller numbers (3-5) for "
            "quick overview, larger (10-20) for comprehensive analysis. Maximum 50.",
        ] = 10,
    ) -> str:
        """List the user's recent patches from Evergreen."""
        try:
            # Get context from lifespan
            evg_ctx = ctx.request_context.lifespan_context

            # Use default project ID if not provided
            effective_project_id = project_id or evg_ctx.default_project_id

            if effective_project_id:
                logger.info("Using project ID: %s", effective_project_id)

            result = await fetch_user_recent_patches(
                evg_ctx.client,
                evg_ctx.user_id,
                limit,
                project_id=effective_project_id,
            )
            return json.dumps(result, indent=2)
        except Exception as e:
            logger.error("Failed to fetch user patches: %s", e)
            error_response = {
                "error": str(e),
                "tool": "list_user_recent_patches_evergreen",
            }
            return json.dumps(error_response, indent=2)

    @mcp.tool(
        description=(
            "Analyze failed CI/CD jobs for a specific patch to understand why "
            "builds are failing. Shows detailed failure information including "
            "failed tasks, build variants, timeout issues, log links, and test "
            "failure counts. Essential for debugging patch failures."
            "\n\nBEST PRACTICE: If project_id is not provided, first call "
            "list_user_projects_evergreen to discover available projects, then "
            "read the output and correlate the project identifier to the current "
            "working directory to determine the correct project_id parameter."
        )
    )
    async def get_patch_failed_jobs_evergreen(
        ctx: Context,
        patch_id: Annotated[
            str,
            "Patch identifier obtained from list_user_recent_patches. This is the "
            "'patch_id' field from the patches array.",
        ],
        project_id: Annotated[
            str | None,
            "Evergreen project identifier for the patch. If not known, call "
            "list_user_projects_evergreen first to discover available projects.",
        ] = None,
        max_results: Annotated[
            int,
            "Maximum number of failed tasks to analyze. Use 10-20 for focused "
            "analysis, 50+ for comprehensive failure review.",
        ] = 50,
    ) -> str:
        """Get failed jobs for a specific patch."""
        try:
            evg_ctx = ctx.request_context.lifespan_context

            # Use default project ID if not provided
            effective_project_id = project_id or evg_ctx.default_project_id

            result = await fetch_patch_failed_jobs(
                evg_ctx.client, patch_id, max_results, project_id=effective_project_id
            )
            return json.dumps(result, indent=2)
        except Exception as e:
            logger.error("Failed to fetch patch failed jobs: %s", e)
            error_response = {
                "error": str(e),
                "tool": "get_patch_failed_jobs_evergreen",
            }
            return json.dumps(error_response, indent=2)

    @mcp.tool(
        description=(
            "Extract detailed logs from a specific failed Evergreen task to "
            "identify root cause of failures. Filters for error messages by "
            "default to focus on relevant failure information. Use task_id "
            "from get_patch_failed_jobs results."
        )
    )
    async def get_task_logs_evergreen(
        ctx: Context,
        task_id: Annotated[
            str,
            "Task identifier from get_patch_failed_jobs response. Found in the "
            "'task_id' field of failed_tasks array.",
        ],
        execution: Annotated[
            int,
            "Task execution number if task was retried. Usually 0 for first "
            "execution, 1+ for retries.",
        ] = 0,
        max_lines: Annotated[
            int,
            "Maximum log lines to return. Use 100-500 for quick error analysis, "
            "1000+ for comprehensive debugging.",
        ] = 1000,
        filter_errors: Annotated[
            bool,
            "Whether to show only error/failure messages (recommended) or all "
            "log output. Set to false only when you need complete context.",
        ] = True,
    ) -> str:
        """Get detailed logs for a specific task."""
        try:
            evg_ctx = ctx.request_context.lifespan_context

            arguments = {
                "task_id": task_id,
                "execution": execution,
                "max_lines": max_lines,
                "filter_errors": filter_errors,
            }

            result = await fetch_task_logs(evg_ctx.client, arguments)
            return json.dumps(result, indent=2)
        except Exception as e:
            logger.error("Failed to fetch task logs: %s", e)
            error_response = {
                "error": str(e),
                "tool": "get_task_logs_evergreen",
            }
            return json.dumps(error_response, indent=2)

    @mcp.tool(
        description=(
            "Fetch detailed test results for a specific Evergreen task, "
            "including individual unit test failures. Use this when a task "
            "shows failed_test_count > 0 to get specific test failure "
            "details. Essential for debugging unit test failures."
        )
    )
    async def get_task_test_results_evergreen(
        ctx: Context,
        task_id: Annotated[
            str,
            "Task identifier from get_patch_failed_jobs response. Found in the "
            "'task_id' field of failed_tasks array.",
        ],
        execution: Annotated[
            int,
            "Task execution number if task was retried. Usually 0 for first "
            "execution, 1+ for retries.",
        ] = 0,
        failed_only: Annotated[
            bool,
            "Whether to fetch only failed tests (recommended) or all test results. "
            "Set to false to see all tests including passing ones.",
        ] = True,
        limit: Annotated[
            int,
            "Maximum number of test results to return. Use 50-100 for focused "
            "analysis, 200+ for comprehensive review.",
        ] = 100,
    ) -> str:
        """Get detailed test results for a specific task."""
        try:
            evg_ctx = ctx.request_context.lifespan_context

            arguments = {
                "task_id": task_id,
                "execution": execution,
                "failed_only": failed_only,
                "limit": limit,
            }

            result = await fetch_task_test_results(evg_ctx.client, arguments)
            return json.dumps(result, indent=2)
        except Exception as e:
            logger.error("Failed to fetch task test results: %s", e)
            error_response = {
                "error": str(e),
                "tool": "get_task_test_results_evergreen",
            }
            return json.dumps(error_response, indent=2)

    @mcp.tool(
        description=(
            "List all Evergreen projects accessible to the authenticated user. "
            "Returns project details including identifiers, display names, owners, "
            "repositories, and enabled status. Use this to discover available "
            "projects before querying patches or tasks."
        )
    )
    async def list_user_projects_evergreen(ctx: Context) -> str:
        """List all projects accessible to the user."""
        try:
            evg_ctx = ctx.request_context.lifespan_context

            projects = await evg_ctx.client.get_projects()
            logger.info("Retrieved %s projects", len(projects))

            result = {
                "projects": [
                    {
                        "identifier": p.get("identifier"),
                        "display_name": p.get("displayName"),
                        "owner": p.get("owner"),
                        "repo": p.get("repo"),
                        "branch": p.get("branch"),
                        "enabled": p.get("enabled"),
                    }
                    for p in projects
                ],
                "total_count": len(projects),
            }
            return json.dumps(result, indent=2)
        except Exception as e:
            logger.error("Failed to fetch projects: %s", e)
            error_response = {
                "error": str(e),
                "tool": "list_user_projects_evergreen",
            }
            return json.dumps(error_response, indent=2)

    logger.info("Registered %d tools with FastMCP server", 5)
