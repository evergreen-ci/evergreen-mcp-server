"""FastMCP tool definitions for Evergreen server

This module contains all MCP tool definitions using FastMCP decorators.
Tools are registered with the FastMCP server instance.
"""

import json
import logging
from typing import Annotated, Any, Dict, Optional

from fastmcp import Context, FastMCP

from .failed_jobs_tools import (
    ProjectInferenceResult,
    fetch_inferred_project_ids,
    fetch_patch_failed_jobs,
    fetch_task_logs,
    fetch_task_test_results,
    fetch_user_recent_patches,
    infer_project_id_from_context,
    fetch_evergreen_task_logs,
    fetch_evergreen_task_test_results
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
            "If project_id is not specified, will automatically detect it from "
            "your workspace directory and recent patch activity."
            "This tool may return a list of available project_ids if it cannot determine the project_id automatically."
            "You should ask the user which project they want to use, then call this tool again with the project_id parameter set to their choice."
        )
    )
    async def list_recent_patches(
        ctx: Context,
        project_id: Annotated[
            str,
            "Evergreen project identifier (e.g., 'mongodb-mongo-master', 'mms') to "
            "filter patches. If not provided, will auto-detect from recent activity.",
        ],
        limit: Annotated[
            int,
            "Number of recent patches to return. Use smaller numbers (3-5) for "
            "quick overview, larger (10-20) for comprehensive analysis. Maximum 50.",
        ] = 10,
    ) -> str:
        """List the user's recent patches from Evergreen."""
        evg_ctx = ctx.request_context.lifespan_context

        # Intelligent project ID resolution
        effective_project_id = project_id
        inference_result: Optional[ProjectInferenceResult] = None

        # If no explicit project ID, attempt intelligent inference
        if not effective_project_id:
            logger.info(
                "No project_id specified, attempting intelligent auto-detection..."
            )
            inference_result = await infer_project_id_from_context(
                evg_ctx.client,
                evg_ctx.user_id,
            )

            if inference_result.project_id:
                effective_project_id = inference_result.project_id
                logger.info(
                    "Auto-detected project ID: %s (confidence: %s, source: %s)",
                    effective_project_id,
                    inference_result.confidence,
                    inference_result.source,
                )
            else:
                # User selection required - return ONLY the project list, no patches
                logger.warning(
                    "Could not auto-detect project ID, requesting user selection"
                )
                return json.dumps(
                    {
                        "status": "user_selection_required",
                        "message": inference_result.message,
                        "available_projects": [
                            {
                                "project_identifier": p["project_identifier"],
                                "patch_count": p["patch_count"],
                                "latest_patch_time": p["latest_patch_time"],
                            }
                            for p in inference_result.available_projects
                        ],
                        "action_required": (
                            "ASK THE USER which project they want to use, then call "
                            "this tool again with the project_id parameter set to their choice."
                        ),
                    },
                    indent=2,
                )

        if effective_project_id:
            logger.info("Using project ID: %s", effective_project_id)

        result = await fetch_user_recent_patches(
            evg_ctx.client,
            evg_ctx.user_id,
            limit,
            project_id=effective_project_id,
        )

        # Include low-confidence warning if applicable
        # We put the message at the top level for better visibility to the AI
        if inference_result and inference_result.confidence == "low":
            final_response = {
                "emit_message": inference_result.message,
                "project_detection": {
                    "status": "low_confidence",
                    "detected_project": effective_project_id,
                    "available_projects": [
                        p["project_identifier"]
                        for p in inference_result.available_projects
                    ],
                },
            }
            final_response.update(result)
            return json.dumps(final_response, indent=2)

        return json.dumps(result, indent=2)

    @mcp.tool(
        description=(
            "Analyze failed CI/CD jobs for a specific patch to understand why "
            "builds are failing. Shows detailed failure information including "
            "failed tasks, build variants, timeout issues, log links, and test "
            "failure counts. Essential for debugging patch failures. "
            "If project_id is not specified, will automatically detect it from "
            "your workspace directory and recent patch activity."
            "This tool may return a list of available project_ids if it cannot determine the project_id automatically."
            "You should ask the user which project they want to use, then call this tool again with the project_id parameter set to their choice."
        )
    )
    async def get_patch_failures(
        ctx: Context,
        patch_id: Annotated[
            str,
            "Patch identifier obtained from list_recent_patches. This is the "
            "'patch_id' field from the patches array.",
        ],
        project_id: Annotated[
            str | None,
            "Evergreen project identifier for the patch. If not provided, will auto-detect.",
        ] = None,
        max_results: Annotated[
            int,
            "Maximum number of failed tasks to analyze. Use 10-20 for focused "
            "analysis, 50+ for comprehensive failure review.",
        ] = 50,
    ) -> str:
        """Get failed jobs for a specific patch."""
        evg_ctx = ctx.request_context.lifespan_context

        # Intelligent project ID resolution
        effective_project_id = project_id
        inference_result: Optional[ProjectInferenceResult] = None

        # If no explicit project ID, attempt intelligent inference
        if not effective_project_id:
            logger.info(
                "No project_id specified, attempting intelligent auto-detection..."
            )
            inference_result = await infer_project_id_from_context(
                evg_ctx.client,
                evg_ctx.user_id,
            )

            if inference_result.project_id:
                effective_project_id = inference_result.project_id
                logger.info(
                    "Auto-detected project ID: %s (confidence: %s)",
                    effective_project_id,
                    inference_result.confidence,
                )
            else:
                # User selection required - return available projects
                return json.dumps(
                    {
                        "status": "user_selection_required",
                        "message": inference_result.message,
                        "available_projects": [
                            {
                                "project_identifier": p["project_identifier"],
                                "patch_count": p["patch_count"],
                                "latest_patch_time": p["latest_patch_time"],
                            }
                            for p in inference_result.available_projects
                        ],
                        "action_required": (
                            "ASK THE USER which project they want to use, then call "
                            "this tool again with the project_id parameter set to their choice."
                        ),
                    },
                    indent=2,
                )

        result = await fetch_patch_failed_jobs(
            evg_ctx.client, patch_id, max_results, project_id=effective_project_id
        )

        # Include low-confidence warning if applicable
        if inference_result and inference_result.confidence == "low":
            final_response = {
                "emit_message": inference_result.message,
                "project_detection": {
                    "status": "low_confidence",
                    "detected_project": effective_project_id,
                },
            }
            final_response.update(result)
            return json.dumps(final_response, indent=2)

        return json.dumps(result, indent=2)

    @mcp.tool(
        description=(
            "Get a quick summary of task logs via GraphQL. Returns basic log info "
            "and filtered error messages. For full detailed logs with error pattern "
            "analysis, use get_task_log_detailed instead. Use task_id from "
            "get_patch_failures results."
        )
    )
    async def get_task_log_summary(
        ctx: Context,
        task_id: Annotated[
            str,
            "Task identifier from get_patch_failures response. Found in the "
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
        evg_ctx = ctx.request_context.lifespan_context

        arguments = {
            "task_id": task_id,
            "execution": execution,
            "max_lines": max_lines,
            "filter_errors": filter_errors,
        }

        result = await fetch_task_logs(evg_ctx.client, arguments)
        return json.dumps(result, indent=2)

    @mcp.tool(
        description=(
            "Get a quick summary of test results via GraphQL. Returns basic test "
            "info including pass/fail status and test names. For full detailed "
            "logs with actual error messages, use get_test_results_detailed instead. "
            "Use task_id from get_patch_failures results."
        )
    )
    async def get_test_results_summary(
        ctx: Context,
        task_id: Annotated[
            str,
            "Task identifier from get_patch_failures response. Found in the "
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
        evg_ctx = ctx.request_context.lifespan_context

        arguments = {
            "task_id": task_id,
            "execution": execution,
            "failed_only": failed_only,
            "limit": limit,
        }

        result = await fetch_task_test_results(evg_ctx.client, arguments)
        return json.dumps(result, indent=2)

    @mcp.tool(
        description=(
            "Get a list of unique project identifiers inferred from the user's "
            "recent patches. This helps discover which Evergreen projects the user "
            "has been working on, sorted by activity (patch count and recency). "
            "Useful for understanding project context and filtering other queries."
        )
    )
    async def get_inferred_project_ids_evergreen(
        ctx: Context,
        max_patches: Annotated[
            int,
            "Maximum number of recent patches to scan for project identifiers. "
            "Use 20-50 for quick discovery, up to 50 for comprehensive analysis. "
            "Default is 50.",
        ] = 50,
    ) -> str:
        """Get unique project identifiers from user's recent patches."""
        evg_ctx = ctx.request_context.lifespan_context

        result = await fetch_inferred_project_ids(
            evg_ctx.client, evg_ctx.user_id, max_patches
        )
        return json.dumps(result, indent=2)


    @mcp.tool(
        description=(
            "Get full detailed task logs via REST API with error pattern analysis. "
            "Scans logs for error patterns (panic, fatal, exception, etc.) and "
            "provides categorized error counts with examples. Best for debugging "
            "root cause of failures. Use task_id from get_patch_failures results."
        )
    )
    async def get_task_log_detailed(
        ctx: Context,
        task_id: Annotated[
            str,
            "Task identifier from get_patch_failures response. Found in the "
            "'task_id' field of failed_tasks array.",
        ],
        execution_retries: Annotated[
            int,
            "Task execution number if task was retried. Usually 0 for first "
            "execution, 1+ for retries.",
        ] = 0,
    ) -> str:
        evg_ctx = ctx.request_context.lifespan_context
        arguments = {
            "task_id": task_id,
            "execution_retries": execution_retries
        }

        result = await fetch_evergreen_task_logs(evg_ctx.api_client, arguments)
        return json.dumps(result, indent=2)

    @mcp.tool(
        description=(
            "Get full detailed test results via REST API with actual error messages. "
            "Fetches raw test logs and scans for error patterns to identify root "
            "cause of test failures. Best for debugging specific test failures. "
            "Use task_id from get_patch_failures results."
        )
    )
    async def get_test_results_detailed(
        ctx: Context,
        task_id: Annotated[
            str,
            "Task identifier from get_patch_failures response. Found in the "
            "'task_id' field of failed_tasks array.",
        ],
        execution_retries: Annotated[
            int,
            "Task execution number if task was retried. Usually 0 for first "
        ],
        job_name: Annotated[
            str,
            "The name of the job to get the test results for. This is the "
            "'job_name' field of the failed_tasks array. i.e. Job0, Job1, etc.",
        ],
        tail_limit: Annotated[
            int,
            "The number of lines to return from the end of the test results. "
            "Use 100-500 for quick error analysis, 1000+ for comprehensive review.",
        ] = 1000,
    ) -> str:
        evg_ctx = ctx.request_context.lifespan_context
        arguments = {
            "task_id": task_id,
            "execution_retries": execution_retries,
            "job_name": job_name,
            "tail_limit": tail_limit
        }
        result = await fetch_evergreen_task_test_results(evg_ctx.api_client, arguments)
        return json.dumps(result, indent=2)

    logger.info("Registered %d tools with FastMCP server", 7)
    
