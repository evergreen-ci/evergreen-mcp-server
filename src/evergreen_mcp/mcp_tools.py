"""FastMCP tool definitions for Evergreen server

This module contains all MCP tool definitions using FastMCP decorators.
Tools are registered with the FastMCP server instance.
"""

import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Annotated, Any, AsyncIterator, Dict, Optional, Tuple

from fastmcp import Context, FastMCP

from .artifact_download_tools import fetch_task_artifacts
from .evergreen_graphql_client import EvergreenGraphQLClient
from .evergreen_rest_client import EvergreenRestClient
from .failed_jobs_tools import (
    ProjectInferenceResult,
    fetch_evergreen_task_logs,
    fetch_evergreen_task_test_results,
    fetch_inferred_project_ids,
    fetch_patch_failed_jobs,
    fetch_task_logs,
    fetch_task_test_results,
    fetch_user_recent_patches,
    infer_project_id_from_context,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _get_clients(
    evg_ctx: Any,
    bearer_token: Optional[str] = None,
) -> AsyncIterator[Tuple[Any, Any, str]]:
    """Get GraphQL and REST clients, using per-request credentials if provided.

    When a bearer token is provided, temporary clients are created for this
    request only. Otherwise falls back to the lifespan context clients.

    Yields (graphql_client, rest_client, user_id).
    """
    if bearer_token:
        # Extract user ID from JWT for tools that need it (e.g. list patches).
        user_id = _user_from_jwt(bearer_token)
        # Use mesh-internal endpoints if configured via env vars.
        evg_uri = os.environ.get("EVERGREEN_URI", "")
        gql_endpoint = f"{evg_uri}/graphql/query" if evg_uri else None
        rest_base_url = f"{evg_uri}/rest/v2/" if evg_uri else None
        client = EvergreenGraphQLClient(
            bearer_token=bearer_token, endpoint=gql_endpoint
        )
        api_client = (
            EvergreenRestClient(bearer_token=bearer_token, base_url=rest_base_url)
            if rest_base_url
            else EvergreenRestClient(bearer_token=bearer_token)
        )
        async with client:
            try:
                yield client, api_client, user_id
            finally:
                await api_client._close_session()
    elif evg_ctx.client is not None:
        yield evg_ctx.client, evg_ctx.api_client, evg_ctx.user_id
    else:
        raise ValueError(
            "No Evergreen credentials available. Either configure default credentials "
            "(EVERGREEN_USER/EVERGREEN_API_KEY) or provide a bearer_token parameter."
        )


def _user_from_jwt(token: str) -> str:
    """Extract the username from a JWT bearer token without signature verification."""
    import base64

    try:
        payload = token.split(".")[1]
        # Fix padding
        payload += "=" * (4 - len(payload) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(payload))
        email = decoded.get("email", "")
        if "@" in email:
            return email.split("@")[0]
        return decoded.get("preferred_username") or decoded.get("sub") or ""
    except Exception:
        return ""


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
    async def list_user_recent_patches_evergreen(
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
        bearer_token: Annotated[
            str | None,
            "Override with a bearer token for this request. If not provided, uses the server's default credentials.",
        ] = None,
    ) -> str:
        """List the user's recent patches from Evergreen."""
        evg_ctx = ctx.request_context.lifespan_context

        async with _get_clients(evg_ctx, bearer_token=bearer_token) as (
            client,
            api_client,
            user_id,
        ):
            # Intelligent project ID resolution
            effective_project_id = project_id
            inference_result: Optional[ProjectInferenceResult] = None

            # If no explicit project ID, attempt intelligent inference
            if not effective_project_id:
                logger.info(
                    "No project_id specified, attempting intelligent auto-detection..."
                )
                inference_result = await infer_project_id_from_context(
                    client,
                    user_id,
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
                client,
                user_id,
                limit,
                project_id=effective_project_id,
            )

            # Include low-confidence warning if applicable
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
    async def get_patch_failed_jobs_evergreen(
        ctx: Context,
        patch_id: Annotated[
            str,
            "Patch identifier obtained from list_user_recent_patches. This is the "
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
        bearer_token: Annotated[
            str | None,
            "Override with a bearer token for this request. If not provided, uses the server's default credentials.",
        ] = None,
    ) -> str:
        """Get failed jobs for a specific patch."""
        evg_ctx = ctx.request_context.lifespan_context

        async with _get_clients(evg_ctx, bearer_token=bearer_token) as (
            client,
            api_client,
            user_id,
        ):
            # Intelligent project ID resolution
            effective_project_id = project_id
            inference_result: Optional[ProjectInferenceResult] = None

            # If no explicit project ID, attempt intelligent inference
            if not effective_project_id:
                logger.info(
                    "No project_id specified, attempting intelligent auto-detection..."
                )
                inference_result = await infer_project_id_from_context(
                    client,
                    user_id,
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
                client, patch_id, max_results, project_id=effective_project_id
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
            "Get a truncated view of task logs via GraphQL. Returns log metadata "
            "and filtered error/failure messages, but only captures a limited "
            "portion of the full log (mostly test log ingestion messages). "
            "For complete raw task logs including timeout output, process dumps, "
            "and full execution logs, use get_task_log_detailed instead. "
            "Use task_id from get_patch_failed_jobs results."
        )
    )
    async def get_task_log_summary(
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
        bearer_token: Annotated[
            str | None,
            "Override with a bearer token for this request. If not provided, uses the server's default credentials.",
        ] = None,
    ) -> str:
        """Get detailed logs for a specific task."""
        evg_ctx = ctx.request_context.lifespan_context

        arguments = {
            "task_id": task_id,
            "execution": execution,
            "max_lines": max_lines,
            "filter_errors": filter_errors,
        }

        async with _get_clients(evg_ctx, bearer_token=bearer_token) as (
            client,
            api_client,
            user_id,
        ):
            result = await fetch_task_logs(client, arguments)
        return json.dumps(result, indent=2)

    @mcp.tool(
        description=(
            "Get test result metadata via GraphQL. Returns test names, pass/fail "
            "statuses, durations, and Parsley log viewer URLs — but not the actual "
            "error messages from test output. For the raw test log content with "
            "error pattern analysis, use get_test_results_detailed instead. "
            "Use task_id from get_patch_failed_jobs results."
        )
    )
    async def get_test_results_summary(
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
        bearer_token: Annotated[
            str | None,
            "Override with a bearer token for this request. If not provided, uses the server's default credentials.",
        ] = None,
    ) -> str:
        """Get detailed test results for a specific task."""
        evg_ctx = ctx.request_context.lifespan_context

        arguments = {
            "task_id": task_id,
            "execution": execution,
            "failed_only": failed_only,
            "limit": limit,
        }

        async with _get_clients(evg_ctx, bearer_token=bearer_token) as (
            client,
            api_client,
            user_id,
        ):
            result = await fetch_task_test_results(client, arguments)
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
        bearer_token: Annotated[
            str | None,
            "Override with a bearer token for this request. If not provided, uses the server's default credentials.",
        ] = None,
    ) -> str:
        """Get unique project identifiers from user's recent patches."""
        evg_ctx = ctx.request_context.lifespan_context

        async with _get_clients(evg_ctx, bearer_token=bearer_token) as (
            client,
            api_client,
            user_id,
        ):
            result = await fetch_inferred_project_ids(client, user_id, max_patches)
        return json.dumps(result, indent=2)

    @mcp.tool(
        description=(
            "Get the complete raw task logs via REST API. Returns the full "
            "untruncated task execution log including timeout handler output, "
            "process dumps, and stdout/stderr — content that the GraphQL "
            "get_task_log_summary tool cannot access. Automatically scans for "
            "error patterns and returns a structured summary with top error "
            "terms and example lines when errors are found. Best for debugging "
            "non-test failures (setup errors, timeouts, compilation failures). "
            "Use task_id from get_patch_failed_jobs results."
        )
    )
    async def get_task_log_detailed(
        ctx: Context,
        task_id: Annotated[
            str,
            "Task identifier from get_patch_failed_jobs response. Found in the "
            "'task_id' field of failed_tasks array.",
        ],
        execution_retries: Annotated[
            int,
            "Task execution number if task was retried. Usually 0 for first "
            "execution, 1+ for retries.",
        ] = 0,
        bearer_token: Annotated[
            str | None,
            "Override with a bearer token for this request. If not provided, uses the server's default credentials.",
        ] = None,
    ) -> str:
        evg_ctx = ctx.request_context.lifespan_context
        arguments = {
            "task_id": task_id,
            "execution_retries": execution_retries,
        }

        async with _get_clients(evg_ctx, bearer_token=bearer_token) as (
            client,
            api_client,
            user_id,
        ):
            result = await fetch_evergreen_task_logs(api_client, arguments)
        return json.dumps(result, indent=2)

    @mcp.tool(
        description=(
            "Get raw test log content via REST API. "
            "Fetches actual test output (stored in S3, not accessible via GraphQL). "
            "Automatically scans for error patterns and returns a structured "
            "summary with top error terms and example lines when errors are found. "
            "Use this to understand WHY a test failed, not just that it failed. "
            "Requires task_id and test_name from get_patch_failed_jobs results."
        )
    )
    async def get_test_results_detailed(
        ctx: Context,
        test_name: Annotated[
            str,
            "The test name used to locate its log in S3. For resmoke tests "
            "this is typically Job0, Job1, etc. For other test runners it may "
            "be the full test identifier. Used to construct the S3 log path: "
            "TestLogs/{test_name}/global.log.",
        ],
        task_id: Annotated[
            str,
            "Task identifier from get_patch_failed_jobs response. Found in the "
            "'task_id' field of failed_tasks array.",
        ],
        execution_retries: Annotated[
            int,
            "Task execution number if task was retried. Usually 0 for first "
            "execution, 1+ for retries.",
        ] = 0,
        tail_limit: Annotated[
            int,
            "The number of lines to return from the end of the test results. "
            "Defaults to 100000 for comprehensive review.",
        ] = 100000,
        bearer_token: Annotated[
            str | None,
            "Override with a bearer token for this request. If not provided, uses the server's default credentials.",
        ] = None,
    ) -> str:
        evg_ctx = ctx.request_context.lifespan_context
        arguments = {
            "task_id": task_id,
            "execution_retries": execution_retries,
            "test_name": test_name,
            "tail_limit": tail_limit,
        }
        async with _get_clients(evg_ctx, bearer_token=bearer_token) as (
            client,
            api_client,
            user_id,
        ):
            result = await fetch_evergreen_task_test_results(api_client, arguments)
        return json.dumps(result, indent=2)

    @mcp.tool(
        description=(
            "Download artifacts from a specific Evergreen task. Use this to retrieve "
            "build outputs, test results, logs, or other files generated by a task. "
            "Artifacts are downloaded to a local directory structure organized by version."
        )
    )
    async def download_task_artifacts_evergreen(
        ctx: Context,
        task_id: Annotated[
            str,
            "The ID of the task to download artifacts for. Required.",
        ],
        artifact_filter: Annotated[
            str | None,
            "Optional filter to download only artifacts containing this string (case-insensitive). If not provided, all artifacts are downloaded.",
        ] = None,
        work_dir: Annotated[
            str,
            "The base directory to create artifact folders in. Defaults to 'WORK'.",
        ] = "WORK",
        bearer_token: Annotated[
            str | None,
            "Override with a bearer token for this request. If not provided, uses the server's default credentials.",
        ] = None,
    ) -> str:
        """Download artifacts for a given Evergreen task and return paths."""
        evg_ctx = ctx.request_context.lifespan_context
        async with _get_clients(evg_ctx, bearer_token=bearer_token) as (
            client,
            api_client,
            user_id,
        ):
            result = await fetch_task_artifacts(
                api_client,
                task_id=task_id,
                artifact_filter=artifact_filter,
                work_dir=work_dir,
            )
        return json.dumps(result, indent=2)

    logger.info("Registered %d tools with FastMCP server", 8)
