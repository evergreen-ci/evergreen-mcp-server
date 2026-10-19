"""FastMCP tool definitions for Evergreen server

This module contains all MCP tool definitions using FastMCP decorators.
Tools are registered with the FastMCP server instance.
"""

import json
import logging
import os
import subprocess
from contextlib import asynccontextmanager
from typing import Annotated, Any, AsyncIterator, Dict, Optional, Tuple

from fastmcp import Context, FastMCP

from .artifact_download_tools import fetch_task_artifacts
from .auto_triage_client import (
    AutoTriageError,
    analyze_task_log,
    analyze_task_test,
    auto_triage_enabled,
)
from .evergreen_graphql_client import EvergreenGraphQLClient
from .evergreen_rest_client import EvergreenRestClient
from .failed_jobs_tools import (
    ProjectInferenceResult,
    fetch_evergreen_task_logs,
    fetch_evergreen_task_test_results,
    fetch_inferred_project_ids,
    fetch_patch_jobs,
    fetch_version_jobs,
    fetch_task_logs,
    fetch_task_test_results,
    fetch_user_recent_patches,
    infer_project_id_from_context,
)
from .patch_creation_tools import (
    cancel_patch,
    create_patch,
    finalize_patch,
    get_patch,
    list_project_patch_aliases,
    list_project_tasks,
    remove_patch_module,
    set_patch_module,
)
from .priority_tools import set_patch_priority, set_task_priority
from .restart_tools import restart_task, restart_version
from .schedule_tools import configure_patch, schedule_unscheduled_tasks
from .status_summary_tools import (
    DEFAULT_DESCRIPTION_MAX_CHARS,
    DEFAULT_MAX_FAILED_TASKS,
    fetch_patch_status_summary,
    fetch_version_status_summary,
)
from .waterfall_tools import (
    fetch_mainline_commits_between,
    fetch_project_build_variants,
    fetch_waterfall_detailed,
    fetch_waterfall_summary,
)

logger = logging.getLogger(__name__)

# Tool responses are consumed by a model, not read by a human. Pretty-printing
# with indent=2 accounted for 18.6% of all Evergreen MCP response bytes measured
# across the local transcript corpus (25% for the task-list tools), and every
# byte is re-sent on each subsequent turn of the conversation.
_COMPACT_JSON = (",", ":")


def _as_list(value: Any) -> Optional[list]:
    """Accept a bare string where a list of filters is expected.

    Callers naturally pass a single filter as a string (`variants="^(disagg-...)"`);
    a strict `list[str]` annotation rejects that with a pydantic validation
    error, costing a whole round-trip to learn nothing. This was the single most
    repeated tool-call failure in the sampled transcripts.
    """
    if value is None:
        return None
    if isinstance(value, str):
        # Deliberately NOT split on commas. These params accept regex on the
        # waterfall tools, and a regex can legitimately contain a comma
        # ("^(a|b){1,2}$"), so splitting would corrupt valid input. A caller who
        # crams "compile,unit_tests" into one string gets one unmatched name;
        # create_patch already detects the resulting empty variants_tasks and
        # warns not to finalize (patch_creation_tools.py:311-341).
        return [value]
    return list(value)


@asynccontextmanager
async def _get_clients(
    evg_ctx: Any,
    bearer_token: Optional[str] = None,
) -> AsyncIterator[Tuple[Any, Any, str]]:
    """Get GraphQL and REST clients, using per-request credentials if provided.

    When a bearer token is provided, temporary clients are created for this
    request only. Otherwise falls back to the lifespan context clients.

    Do NOT remove or hide `bearer_token` from the tool schemas to save tokens.
    It is ~735 tokens of schema re-sent on every request and was passed 0 times
    in 2,083 sampled local calls, which makes it look dead — but under
    EVERGREEN_AUTH_MODE=per_request (server.py, "used by MCP gateways that
    inject per-user credentials from a credential store") the lifespan context
    has no clients at all, and this function raises unless the token arrives as
    a tool argument. Hiding it would break that deployment outright. The type
    was narrowed (str | None = None -> str = "") to drop the anyOf union from
    the schema, which is the safe part of that saving.

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


async def _resolve_project_id(
    client: Any, user_id: str, project_id: Optional[str]
) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Resolve a project_id, falling back to context inference.

    Returns (effective_project_id, user_selection_payload). When
    user_selection_payload is non-None, the caller should return it directly
    to the user so they can pick a project.
    """
    if project_id:
        return project_id, None

    inference: ProjectInferenceResult = await infer_project_id_from_context(
        client, user_id
    )
    if inference.project_id:
        return inference.project_id, None

    return None, {
        "status": "user_selection_required",
        "message": inference.message,
        "available_projects": [
            {
                "project_identifier": p["project_identifier"],
                "patch_count": p["patch_count"],
                "latest_patch_time": p["latest_patch_time"],
            }
            for p in inference.available_projects
        ],
        "action_required": (
            "ASK THE USER which project they want to use, then call this tool "
            "again with the project_id parameter set to their choice."
        ),
    }


def _git_diff_from_repo(repo_path: str) -> tuple[str, str]:
    """Run git diff against mainline and return (diff, base_revision).

    Upstream detection order: origin/master → origin/main → @{u}.
    Feature branches often track themselves, so prefer the explicit mainline
    refs rather than the branch's tracking remote.
    """
    expanded = os.path.expanduser(repo_path)
    upstream = None
    for candidate in ("origin/master", "origin/main"):
        r = subprocess.run(
            ["git", "rev-parse", "--verify", candidate],
            cwd=expanded,
            capture_output=True,
        )
        if r.returncode == 0:
            upstream = candidate
            break
    if upstream is None:
        upstream = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "@{u}"],
            cwd=expanded,
            text=True,
            stderr=subprocess.PIPE,
        ).strip()
    base_revision = subprocess.check_output(
        ["git", "merge-base", upstream, "HEAD"],
        cwd=expanded,
        text=True,
        stderr=subprocess.PIPE,
    ).strip()
    diff = subprocess.check_output(
        ["git", "diff", "--binary", f"{upstream}...HEAD"],
        cwd=expanded,
        text=True,
        stderr=subprocess.PIPE,
    )
    return diff, base_revision


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
            str | None,
            "Evergreen project identifier (e.g., 'mongodb-mongo-master', 'mms') to "
            "filter patches. If not provided, will auto-detect from recent activity.",
        ] = None,
        limit: Annotated[
            int,
            "Number of recent patches to return. Use smaller numbers (3-5) for "
            "quick overview, larger (10-20) for comprehensive analysis. Maximum 50.",
        ] = 10,
        bearer_token: Annotated[
            str,
            "Per-request auth override; normally omit.",
        ] = "",
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
                        separators=_COMPACT_JSON,
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
                return json.dumps(final_response, separators=_COMPACT_JSON)

            return json.dumps(result, separators=_COMPACT_JSON)

    @mcp.tool(
        description=(
            "Get CI/CD jobs for a specific patch, with full per-task detail. "
            "By default returns only failed jobs "
            "to help debug patch failures; use status_filter='all' to see every task or "
            "'succeeded' to see only passing tasks. Shows task status, build variants, "
            "timeout info, task_id, host metadata, and test counts. "
            "If you are only checking whether the patch has finished and what "
            "failed, use get_status_summary_evergreen instead — it is ~20x "
            "smaller. Come here when you need a task_id to drill into. "
            "If project_id is not specified, will automatically detect it from "
            "your workspace directory and recent patch activity. "
            "This tool may return a list of available project_ids if it cannot determine the project_id automatically. "
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
            "Maximum number of tasks to return. Use 10-20 for focused "
            "analysis, 50+ for comprehensive review.",
        ] = 50,
        status_filter: Annotated[
            str,
            "Which tasks to return: 'failed' (default) for failed/timed-out tasks only, "
            "'succeeded' for passing tasks only, 'all' for every task regardless of status.",
        ] = "failed",
        bearer_token: Annotated[
            str,
            "Per-request auth override; normally omit.",
        ] = "",
    ) -> str:
        """Get jobs for a specific patch, filtered by status."""
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
                        separators=_COMPACT_JSON,
                    )

            result = await fetch_patch_jobs(
                client,
                patch_id,
                max_results,
                project_id=effective_project_id,
                status_filter=status_filter,
                project_id_inferred=project_id is None,
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
                return json.dumps(final_response, separators=_COMPACT_JSON)

            return json.dumps(result, separators=_COMPACT_JSON)

    @mcp.tool(
        description=(
            "PREFERRED TOOL FOR POLLING whether a patch or version has finished. "
            "Returns a compact fixed schema: version_status, an explicit 'done' "
            "flag, 'in_flight' count, per-status task counts, Evergreen's own "
            "'eta', and one entry per FAILING task ({task_name, build_variant, "
            "status, description}) — including 'known-issue' tasks, which are "
            "annotated failures and count as failed, not succeeded. "
            "Roughly 20-50x smaller than "
            "get_patch_failed_jobs_evergreen / get_version_tasks_evergreen, and "
            "the size does not grow with task count, so use this by default and "
            "reach for the detailed tools only once you know which task to "
            "investigate (they return task_id, host metadata and test counts). "
            "Pass exactly one of patch_id or version_id. "
            "'done' is not monotonic: unscheduled tasks are not counted as "
            "in-flight, so a version whose tasks are not activated yet "
            "(batchtime on mainline, or after an abort/restart or "
            "configure_patch) can report done=true and later go back to "
            "false. A 'done_caveat' field appears when that is possible. "
            "When polling several patches, issue the calls IN PARALLEL in a "
            "single turn rather than one per turn — that is where most of the "
            "cost of status polling actually lives. Prefer polling from a "
            "subagent when you have many checks to make."
        )
    )
    async def get_status_summary_evergreen(
        ctx: Context,
        patch_id: Annotated[
            str | None,
            "Patch identifier to summarize. Mutually exclusive with version_id.",
        ] = None,
        version_id: Annotated[
            str | None,
            "Version identifier to summarize (extract from a Spruce URL: "
            "spruce.corp.mongodb.com/version/<version_id>/tasks). Use this for "
            "trigger_request and other non-patch versions. Mutually exclusive "
            "with patch_id.",
        ] = None,
        max_failed_tasks: Annotated[
            int,
            "Maximum number of failing tasks to enumerate. The total count is "
            "always reported even when the list is truncated.",
        ] = DEFAULT_MAX_FAILED_TASKS,
        description_max_chars: Annotated[
            int,
            "Truncate each failure description to this many characters. Raise it "
            "if a description is cut off mid-message.",
        ] = DEFAULT_DESCRIPTION_MAX_CHARS,
        bearer_token: Annotated[
            str,
            "Per-request auth override; normally omit.",
        ] = "",
    ) -> str:
        """Get a compact status summary for a patch or version."""
        if bool(patch_id) == bool(version_id):
            return json.dumps(
                {
                    "status": "error",
                    "error": (
                        "Pass exactly one of patch_id or version_id "
                        f"(got patch_id={patch_id!r}, version_id={version_id!r})."
                    ),
                },
                separators=_COMPACT_JSON,
            )

        evg_ctx = ctx.request_context.lifespan_context
        async with _get_clients(evg_ctx, bearer_token=bearer_token) as (
            client,
            _api_client,
            _user_id,
        ):
            if patch_id:
                result = await fetch_patch_status_summary(
                    client,
                    patch_id,
                    max_failed_tasks=max_failed_tasks,
                    description_max_chars=description_max_chars,
                )
            else:
                result = await fetch_version_status_summary(
                    client,
                    version_id,
                    max_failed_tasks=max_failed_tasks,
                    description_max_chars=description_max_chars,
                )
        return json.dumps(result, separators=_COMPACT_JSON)

    @mcp.tool(
        description=(
            "Get CI/CD tasks for a specific Evergreen version by version_id, "
            "with full per-task detail (task_id, host metadata, timing, test "
            "counts). This is the heaviest read tool in this server: prefer "
            "get_status_summary_evergreen when you only need to know whether "
            "the version finished and what failed, and come here once you need "
            "a task_id to drill into. "
            "Unlike get_patch_failed_jobs_evergreen (which queries via the patch graph), "
            "this queries the version directly — making it the right tool for "
            "trigger_request versions and other non-patch versions that are "
            "invisible to the waterfall tools. By default returns all tasks "
            "(status_filter='all'); use 'failed' or 'succeeded' to filter. "
            "The version_id can be extracted from any Spruce URL: "
            "spruce.corp.mongodb.com/version/<version_id>/tasks."
        )
    )
    async def get_version_tasks_evergreen(
        ctx: Context,
        version_id: Annotated[
            str,
            "Version identifier. Extract from a Spruce URL: "
            "spruce.corp.mongodb.com/version/<version_id>/tasks",
        ],
        max_results: Annotated[
            int,
            "Maximum number of tasks to fetch and return, capped at 500. Use "
            "10-20 for focused analysis, 50-200 for comprehensive review. A "
            "mainline version can hold thousands of tasks, so pair a large "
            "value with status_filter='failed' rather than trying to list them "
            "all; for a whole-version overview use get_status_summary_evergreen, "
            "whose size does not grow with task count.",
        ] = 50,
        status_filter: Annotated[
            str,
            "Which tasks to return: 'all' (default) for every task, "
            "'failed' for failed/timed-out tasks only, "
            "'succeeded' for passing tasks only.",
        ] = "all",
        bearer_token: Annotated[
            str,
            "Per-request auth override; normally omit.",
        ] = "",
    ) -> str:
        """Get tasks for a specific version by version_id."""
        evg_ctx = ctx.request_context.lifespan_context

        async with _get_clients(evg_ctx, bearer_token=bearer_token) as (
            client,
            _api_client,
            _user_id,
        ):
            result = await fetch_version_jobs(
                client,
                version_id,
                max_results,
                status_filter=status_filter,
            )
            return json.dumps(result, separators=_COMPACT_JSON)

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
            str,
            "Per-request auth override; normally omit.",
        ] = "",
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
        return json.dumps(result, separators=_COMPACT_JSON)

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
            str,
            "Per-request auth override; normally omit.",
        ] = "",
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
        return json.dumps(result, separators=_COMPACT_JSON)

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
            str,
            "Per-request auth override; normally omit.",
        ] = "",
    ) -> str:
        """Get unique project identifiers from user's recent patches."""
        evg_ctx = ctx.request_context.lifespan_context

        async with _get_clients(evg_ctx, bearer_token=bearer_token) as (
            client,
            api_client,
            user_id,
        ):
            result = await fetch_inferred_project_ids(client, user_id, max_patches)
        return json.dumps(result, separators=_COMPACT_JSON)

    @mcp.tool(
        description=(
            "Get the recent event log for an Evergreen distro, newest first. "
            "Returns the full change history (each event's before/after distro "
            "snapshot) so you can spot any environmental change: AMI (Amazon "
            "Machine Image) rotations, toolchain updates that happen without an "
            "image rebuild, and other distro-setting changes. Also includes a "
            "derived 'ami_changes' list for the common 'did the base AMI rotate?' "
            "question (before -> after AMI IDs, timestamp, who changed it). A "
            "recent distro change is a common cause of sudden task regressions "
            "that have no corresponding project code change, so use this when a "
            "task started failing with environmental symptoms. Get the distro_id "
            "from a task's 'distro_id' field in get_patch_failed_jobs results. "
            "Requires DistroSettingsView permission on the distro; if the caller "
            "lacks it the response includes a permission error and no event data."
        )
    )
    async def get_distro_ami_changes_evergreen(
        ctx: Context,
        distro_id: Annotated[
            str,
            "Distro identifier to inspect, e.g. 'ubuntu2204-large'. Found in the "
            "'distro_id' field of a task from get_patch_failed_jobs.",
        ],
        limit: Annotated[
            int,
            "Maximum number of recent distro events to scan for AMI changes. "
            "Default is 20.",
        ] = 20,
        bearer_token: Annotated[
            str | None,
            "Override with a bearer token for this request. If not provided, uses the server's default credentials.",
        ] = None,
    ) -> str:
        """Get the recent event log (incl. AMI changes) for a distro."""
        evg_ctx = ctx.request_context.lifespan_context

        async with _get_clients(evg_ctx, bearer_token=bearer_token) as (
            client,
            api_client,
            user_id,
        ):
            try:
                result = await client.get_distro_events(distro_id, limit)
            except Exception as e:
                # The distroEvents directive returns a GraphQL error (not an empty
                # result) when the caller lacks DistroSettingsView. Surface it
                # instead of failing, so callers can note "AMI check skipped".
                message = str(e)
                logger.info(
                    "Could not fetch distro events for %s: %s", distro_id, message
                )
                return json.dumps(
                    {
                        "distro_id": distro_id,
                        "status": "error",
                        "message": message,
                        "hint": (
                            "This may be a permissions issue - fetching distro events "
                            "requires DistroSettingsView on the distro."
                        ),
                    },
                    separators=_COMPACT_JSON,
                )
        return json.dumps(result, separators=_COMPACT_JSON)

    @mcp.tool(
        description=(
            "Triage a task's logs to find the root cause of a failure. Routes the "
            "task log through the auto-triage service, which runs a multi-stage "
            "pipeline (error-template discovery, causation graph, and LLM triage) "
            "and returns a structured root-cause analysis with a triage summary, "
            "failure classification, and suggested ARR regex. If auto-triage is "
            "unavailable it transparently falls back to fetching the complete raw "
            "task log via REST and scanning it for error patterns. Best for "
            "debugging non-test failures (setup errors, timeouts, compilation "
            "failures). Use task_id from get_patch_failed_jobs results."
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
        max_chars: Annotated[
            int,
            "Cap the returned log text at this many characters, keeping the "
            "TAIL (failures surface at the end). Defaults to 20000. Raise it "
            "only after seeing a truncation notice and needing earlier output.",
        ] = 0,
        bearer_token: Annotated[
            str,
            "Per-request auth override; normally omit.",
        ] = "",
    ) -> str:
        evg_ctx = ctx.request_context.lifespan_context
        arguments = {
            "task_id": task_id,
            "execution_retries": execution_retries,
            "max_chars": max_chars,
        }

        async with _get_clients(evg_ctx, bearer_token=bearer_token) as (
            client,
            api_client,
            user_id,
        ):
            # Forward the OIDC bearer token so auto-triage can fetch the logs from
            # Evergreen on the user's behalf. Fall back to the raw REST scan on any
            # auto-triage failure so this tool always returns something usable.
            token = bearer_token or getattr(api_client, "bearer_token", None)
            if auto_triage_enabled():
                try:
                    triage = await analyze_task_log(
                        task_id, execution_retries, token, log_type="task_log"
                    )
                    return json.dumps(
                        {"source": "auto-triage", "task_id": task_id, "triage": triage},
                        separators=_COMPACT_JSON,
                    )
                except AutoTriageError as e:
                    logger.info(
                        "auto-triage unavailable for task %s, falling back to "
                        "REST log scan: %s",
                        task_id,
                        e,
                    )

            result = await fetch_evergreen_task_logs(api_client, arguments)
            result["source"] = "rest-scan"
        return json.dumps(result, separators=_COMPACT_JSON)

    @mcp.tool(
        description=(
            "Triage a failed test's log to find the root cause. Routes the test "
            "log through the auto-triage service, which runs a multi-stage pipeline "
            "(error-template discovery, causation graph, and LLM triage) and returns "
            "a structured root-cause analysis with a triage summary, failure "
            "classification, and suggested ARR regex. If auto-triage is unavailable "
            "it transparently falls back to fetching the raw test log via REST "
            "(stored in S3, not accessible via GraphQL) and scanning it for error "
            "patterns. Use this to understand WHY a test failed, not just that it "
            "failed. Requires task_id and test_name from get_patch_failed_jobs results."
        )
    )
    async def get_test_results_detailed(
        ctx: Context,
        test_name: Annotated[
            str,
            "REQUIRED. Use the 'test_file' value from a "
            "get_test_results_summary response verbatim (e.g. "
            "'resource_sanity_checks', 'load') — that is the name this log is "
            "stored under. For resmoke tests it is typically Job0, Job1, etc. "
            "Call get_test_results_summary first if you do not have it; there "
            "is no default. Used to construct the S3 log path: "
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
            str,
            "Per-request auth override; normally omit.",
        ] = "",
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
            # Forward the OIDC bearer token so auto-triage can fetch the test log
            # from Evergreen on the user's behalf. Fall back to the raw REST scan
            # on any auto-triage failure.
            token = bearer_token or getattr(api_client, "bearer_token", None)
            if auto_triage_enabled():
                try:
                    triage = await analyze_task_test(
                        task_id, execution_retries, test_name, token
                    )
                    return json.dumps(
                        {
                            "source": "auto-triage",
                            "task_id": task_id,
                            "test_name": test_name,
                            "triage": triage,
                        },
                        separators=_COMPACT_JSON,
                    )
                except AutoTriageError as e:
                    logger.info(
                        "auto-triage unavailable for test %s in task %s, falling "
                        "back to REST log scan: %s",
                        test_name,
                        task_id,
                        e,
                    )

            result = await fetch_evergreen_task_test_results(api_client, arguments)
            result["source"] = "rest-scan"
        return json.dumps(result, separators=_COMPACT_JSON)

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
            str,
            "Per-request auth override; normally omit.",
        ] = "",
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
        return json.dumps(result, separators=_COMPACT_JSON)

    @mcp.tool(
        description=(
            "Get a compact view of the Evergreen project waterfall: a grid of "
            "build variants (rows) by recent versions/patches (columns), where "
            "each cell shows a count of task statuses (e.g., {success: 42, "
            "failed: 3}). Use this to answer 'is the project healthy?', 'which "
            "variants have been failing on recent commits?', or 'show me the "
            "waterfall around date/revision X'. Returns version metadata and "
            "pagination cursors. For per-task detail, follow up with "
            "get_waterfall_detailed_evergreen filtered to a narrower slice. "
            "If project_id is not provided, will auto-detect; may return a "
            "user_selection_required response listing available projects."
        )
    )
    async def get_waterfall_summary_evergreen(
        ctx: Context,
        project_id: Annotated[
            str | None,
            "Evergreen project identifier. If omitted, auto-detected from the "
            "user's recent patch activity.",
        ] = None,
        limit: Annotated[
            int,
            "Number of versions (columns) to fetch. Clamped to 1–30. Default 10.",
        ] = 10,
        max_order: Annotated[
            int | None,
            "Pagination cursor: pass pagination.next_page_order from a prior "
            "response to fetch older versions.",
        ] = None,
        min_order: Annotated[
            int | None,
            "Pagination cursor: pass pagination.prev_page_order to fetch newer "
            "versions. Mutually exclusive with max_order.",
        ] = None,
        revision: Annotated[
            str | None,
            "Center the waterfall around this git revision (>=7 chars).",
        ] = None,
        date: Annotated[
            str | None,
            "Show versions on/before this date (YYYY-MM-DD).",
        ] = None,
        variants: Annotated[
            list[str] | str | None,
            "Filter by build variant (regex or exact). Use "
            "list_project_build_variants_evergreen to discover valid values.",
        ] = None,
        statuses: Annotated[
            list[str] | str | None,
            "Filter task status counts (e.g., ['failed', 'system-failed']).",
        ] = None,
        requesters: Annotated[
            list[str] | str | None,
            "Filter by requester type. Server defaults to system requesters "
            "(commit/branch).",
        ] = None,
        omit_inactive_builds: Annotated[
            bool,
            "Skip inactive builds in the grid. Defaults to True (less noise).",
        ] = True,
        bearer_token: Annotated[
            str,
            "Per-request auth override; normally omit.",
        ] = "",
    ) -> str:
        """Compact project waterfall: per-cell task status counts."""
        evg_ctx = ctx.request_context.lifespan_context
        async with _get_clients(evg_ctx, bearer_token=bearer_token) as (
            client,
            api_client,
            user_id,
        ):
            effective_id, selection = await _resolve_project_id(
                client, user_id, project_id
            )
            if selection is not None:
                return json.dumps(selection, separators=_COMPACT_JSON)

            result = await fetch_waterfall_summary(
                client,
                project_id=effective_id,
                limit=limit,
                max_order=max_order,
                min_order=min_order,
                revision=revision,
                date=date,
                variants=_as_list(variants),
                statuses=_as_list(statuses),
                requesters=_as_list(requesters),
                omit_inactive_builds=omit_inactive_builds,
            )
        return json.dumps(result, separators=_COMPACT_JSON)

    @mcp.tool(
        description=(
            "Get the Evergreen project waterfall with per-task detail. Each "
            "cell carries a list of tasks with id, display_name, status, and "
            "execution. Returned task_id values plug directly into "
            "get_task_log_summary, get_task_log_detailed, "
            "get_test_results_summary, and get_test_results_detailed. "
            "More expensive than the summary tool — narrow the slice via "
            "variants, tasks, or statuses filters. If project_id is not "
            "provided, will auto-detect."
        )
    )
    async def get_waterfall_detailed_evergreen(
        ctx: Context,
        project_id: Annotated[
            str | None,
            "Evergreen project identifier. If omitted, auto-detected.",
        ] = None,
        limit: Annotated[
            int,
            "Number of versions (columns). Clamped to 1–15. Default 5.",
        ] = 5,
        max_order: Annotated[
            int | None,
            "Pagination cursor: next_page_order from a prior response.",
        ] = None,
        min_order: Annotated[
            int | None,
            "Pagination cursor: prev_page_order from a prior response.",
        ] = None,
        revision: Annotated[
            str | None,
            "Center on this git revision (>=7 chars).",
        ] = None,
        date: Annotated[
            str | None,
            "Show versions on/before this date (YYYY-MM-DD).",
        ] = None,
        variants: Annotated[
            list[str] | str | None,
            "Filter by build variant (regex or exact).",
        ] = None,
        tasks: Annotated[
            list[str] | str | None,
            "Filter by task display name (regex or exact).",
        ] = None,
        statuses: Annotated[
            list[str] | str | None,
            "Filter by task status (e.g., ['failed', 'system-failed']).",
        ] = None,
        requesters: Annotated[
            list[str] | str | None,
            "Filter by requester type. Server defaults to system requesters.",
        ] = None,
        omit_inactive_builds: Annotated[
            bool,
            "Skip inactive builds. Defaults to True.",
        ] = True,
        task_case_sensitive: Annotated[
            bool,
            "Case sensitivity for the tasks filter. Defaults to True (faster).",
        ] = True,
        variant_case_sensitive: Annotated[
            bool,
            "Case sensitivity for the variants filter. Defaults to True.",
        ] = True,
        bearer_token: Annotated[
            str,
            "Per-request auth override; normally omit.",
        ] = "",
    ) -> str:
        """Detailed project waterfall: per-cell task arrays with task_ids."""
        evg_ctx = ctx.request_context.lifespan_context
        async with _get_clients(evg_ctx, bearer_token=bearer_token) as (
            client,
            api_client,
            user_id,
        ):
            effective_id, selection = await _resolve_project_id(
                client, user_id, project_id
            )
            if selection is not None:
                return json.dumps(selection, separators=_COMPACT_JSON)

            result = await fetch_waterfall_detailed(
                client,
                project_id=effective_id,
                limit=limit,
                max_order=max_order,
                min_order=min_order,
                revision=revision,
                date=date,
                variants=_as_list(variants),
                tasks=_as_list(tasks),
                statuses=_as_list(statuses),
                requesters=_as_list(requesters),
                omit_inactive_builds=omit_inactive_builds,
                task_case_sensitive=task_case_sensitive,
                variant_case_sensitive=variant_case_sensitive,
            )
        return json.dumps(result, separators=_COMPACT_JSON)

    @mcp.tool(
        description=(
            "List the unique build variants for an Evergreen project. Use "
            "this to discover valid variant names before filtering "
            "get_waterfall_summary_evergreen or get_waterfall_detailed_evergreen "
            "via the variants parameter. Returns build_variant identifiers and "
            "their human display names."
        )
    )
    async def list_project_build_variants_evergreen(
        ctx: Context,
        project_id: Annotated[
            str | None,
            "Evergreen project identifier. If omitted, auto-detected.",
        ] = None,
        bearer_token: Annotated[
            str,
            "Per-request auth override; normally omit.",
        ] = "",
    ) -> str:
        """List unique build variants for a project."""
        evg_ctx = ctx.request_context.lifespan_context
        async with _get_clients(evg_ctx, bearer_token=bearer_token) as (
            client,
            api_client,
            user_id,
        ):
            effective_id, selection = await _resolve_project_id(
                client, user_id, project_id
            )
            if selection is not None:
                return json.dumps(selection, separators=_COMPACT_JSON)

            result = await fetch_project_build_variants(client, project_id=effective_id)
        return json.dumps(result, separators=_COMPACT_JSON)

    @mcp.tool(
        description=(
            "List mainline (master) commits on an Evergreen project between "
            "two version order numbers, inclusive. Use this for change-point "
            "/ regression analysis: when a perf variant skipped commits "
            "between two runs, this enumerates the candidate culprits. "
            "Returns {order, version_id, revision, message, author, "
            "create_time, requester, activated} for each commit, "
            "newest-first. Defaults to the server's mainline requesters "
            "(commit/branch); override via the requesters parameter. "
            "Order numbers can be obtained from get_waterfall_summary_evergreen."
        )
    )
    async def get_mainline_commits_between_evergreen(
        ctx: Context,
        start_order: Annotated[
            int,
            "One end of the order range (inclusive). May be the lower or "
            "upper bound — the tool normalizes.",
        ],
        end_order: Annotated[
            int,
            "The other end of the order range (inclusive).",
        ],
        project_id: Annotated[
            str | None,
            "Evergreen project identifier. If omitted, auto-detected.",
        ] = None,
        requesters: Annotated[
            list[str] | str | None,
            "Filter by requester type. If omitted, the server's mainline "
            "default applies (commit/branch).",
        ] = None,
        bearer_token: Annotated[
            str,
            "Per-request auth override; normally omit.",
        ] = "",
    ) -> str:
        """List mainline commits between two version order numbers."""
        evg_ctx = ctx.request_context.lifespan_context
        async with _get_clients(evg_ctx, bearer_token=bearer_token) as (
            client,
            api_client,
            user_id,
        ):
            effective_id, selection = await _resolve_project_id(
                client, user_id, project_id
            )
            if selection is not None:
                return json.dumps(selection, separators=_COMPACT_JSON)

            result = await fetch_mainline_commits_between(
                client,
                project_id=effective_id,
                start_order=start_order,
                end_order=end_order,
                requesters=_as_list(requesters),
            )
        return json.dumps(result, separators=_COMPACT_JSON)

    @mcp.tool(
        description=(
            "Schedule (activate) previously-unscheduled tasks on an Evergreen "
            "version so they will run. This is a write/mutation: it changes "
            "shared CI state. Use it when a task on version X / variant Y did "
            "not run because it wasn't scheduled. Requires TASKS:EDIT "
            "permission on the project. "
            "Workflow: first call get_waterfall_detailed_evergreen with "
            "statuses=['unscheduled'] to discover the task_id values for the "
            "version of interest, then pass version_id and those task_ids "
            "here. Returns the tasks that were actually scheduled plus a "
            "missing_task_ids list for any IDs Evergreen silently dropped "
            "(already finished, wrong version, or insufficient permission)."
        )
    )
    async def schedule_tasks_evergreen(
        ctx: Context,
        version_id: Annotated[
            str,
            "Version (or patch version) identifier owning the tasks. Found in "
            "the 'version_id' field of get_waterfall_detailed_evergreen "
            "responses, or the 'version.id' field on a patch.",
        ],
        task_ids: Annotated[
            list[str],
            "Task identifiers to schedule. Obtain from "
            "get_waterfall_detailed_evergreen (the 'task_id' field of each "
            "task in a cell). Must be tasks belonging to the given version_id.",
        ],
        bearer_token: Annotated[
            str,
            "Per-request auth override; normally omit.",
        ] = "",
    ) -> str:
        """Schedule previously-unscheduled tasks on a version."""
        evg_ctx = ctx.request_context.lifespan_context
        async with _get_clients(evg_ctx, bearer_token=bearer_token) as (
            client,
            api_client,
            user_id,
        ):
            result = await schedule_unscheduled_tasks(
                client,
                version_id=version_id,
                task_ids=task_ids,
            )
        return json.dumps(result, separators=_COMPACT_JSON)

    @mcp.tool(
        description=(
            "Add build-variant/task pairs to an EXISTING Evergreen patch — the "
            "MCP equivalent of Spruce's 'Configure Patch' / 'Add tasks to a "
            "patch' flow. This is a write/mutation: it changes shared CI state. "
            "Requires PATCHES:EDIT permission on the project. "
            "Use this when a patch was already created (even finalized) and you "
            "want to run an ADDITIONAL variant/task on it while REUSING its "
            "existing compile artifacts, instead of creating a brand-new patch "
            "that recompiles. The variant/task pair must exist in the patch's "
            "resolved project config. "
            "Unlike schedule_tasks_evergreen (which only activates tasks that "
            "already exist on the version in an unscheduled state), this creates "
            "the variant's build and tasks if they weren't in the patch yet. "
            "Already-scheduled tasks are preserved (the tool resends the full "
            "union of existing + new selections). Any required compile "
            "dependency the patch didn't already build will run; the rest are "
            "reused. "
            "Note: display-task selections cannot be read back from Evergreen, "
            "so if the patch had display tasks selected they are not preserved "
            "by this call. "
            "Returns the patch's resulting full variant/task selection."
        )
    )
    async def configure_patch_evergreen(
        ctx: Context,
        patch_id: Annotated[
            str,
            "Patch identifier to reconfigure (as returned by "
            "create_patch_evergreen or list_user_recent_patches_evergreen).",
        ],
        add_variants_tasks: Annotated[
            list[dict],
            "Variant/task pairs to add, as a list of "
            '{"variant": <build-variant name>, "tasks": [<task name>, ...]} '
            "dicts. Example: "
            '[{"variant": "disagg-m8g-perf-11-node.arm.aws", '
            '"tasks": ["data_handle_locust"]}]. '
            "Each variant/task pair must exist in the patch's resolved project "
            "config, else Evergreen silently drops it.",
        ],
        description: Annotated[
            str | None,
            "Optional new patch description. If omitted, the patch's current "
            "description is preserved.",
        ] = None,
        bearer_token: Annotated[
            str,
            "Per-request auth override; normally omit.",
        ] = "",
    ) -> str:
        """Add variant/task pairs to an existing patch (reusing compiles)."""
        evg_ctx = ctx.request_context.lifespan_context
        async with _get_clients(evg_ctx, bearer_token=bearer_token) as (
            client,
            api_client,
            user_id,
        ):
            result = await configure_patch(
                client,
                patch_id=patch_id,
                add_variants_tasks=add_variants_tasks,
                description=description,
            )
        return json.dumps(result, separators=_COMPACT_JSON)

    @mcp.tool(
        description=(
            "Restart a finished Evergreen task, producing a new execution. "
            "This is a write/mutation: it changes shared CI state. Requires "
            "TASKS:EDIT permission on the project. Use it to re-run a task "
            "that failed flakily or to reproduce a failure. "
            "Obtain task_id from get_patch_failed_jobs_evergreen, "
            "get_waterfall_detailed_evergreen, or the Spruce task URL "
            "(https://spruce.corp.mongodb.com/task/<task_id>/logs). "
            "failed_only only affects display tasks — when true, only "
            "restarts their failed execution tasks; ignored otherwise. "
            "Returns the restarted task with its new execution number."
        )
    )
    async def restart_task_evergreen(
        ctx: Context,
        task_id: Annotated[
            str,
            "Task identifier to restart. Taken from a Spruce task URL "
            "(/task/<task_id>/logs) or from the 'task_id' field of "
            "get_patch_failed_jobs_evergreen / "
            "get_waterfall_detailed_evergreen responses.",
        ],
        failed_only: Annotated[
            bool,
            "For display tasks, restart only the failed execution tasks "
            "instead of all of them. Has no effect on non-display tasks. "
            "Defaults to false.",
        ] = False,
        bearer_token: Annotated[
            str,
            "Per-request auth override; normally omit.",
        ] = "",
    ) -> str:
        """Restart a finished task, producing a new execution."""
        evg_ctx = ctx.request_context.lifespan_context
        async with _get_clients(evg_ctx, bearer_token=bearer_token) as (
            client,
            api_client,
            user_id,
        ):
            result = await restart_task(
                client,
                task_id=task_id,
                failed_only=failed_only,
            )
        return json.dumps(result, separators=_COMPACT_JSON)

    @mcp.tool(
        description=(
            "Restart tasks on an Evergreen version (patch). This is a "
            "write/mutation: it changes shared CI state. Requires TASKS:EDIT "
            "permission on the project. "
            "When task_ids is omitted or empty, restarts all completed tasks "
            "on the version (mirrors Spruce's 'Restart' button on a patch). "
            "Pass specific task_ids to restart only a subset. "
            "Set abort=true to also abort any in-progress tasks and queue "
            "them for reset before restarting completed ones. "
            "Obtain version_id from list_user_recent_patches_evergreen "
            "('version_id' field) or the 'version.id' on a patch. "
            "Returns the restarted version entities."
        )
    )
    async def restart_version_evergreen(
        ctx: Context,
        version_id: Annotated[
            str,
            "Version (or patch version) identifier to restart. Found in the "
            "'version_id' field of list_user_recent_patches_evergreen or "
            "'version.id' on a patch.",
        ],
        task_ids: Annotated[
            list[str] | None,
            "Specific task identifiers to restart. Omit or pass an empty "
            "list to restart all completed tasks on the version.",
        ] = None,
        abort: Annotated[
            bool,
            "If true, abort any in-progress tasks and queue them for reset "
            "before restarting completed tasks. Defaults to false.",
        ] = False,
        bearer_token: Annotated[
            str,
            "Per-request auth override; normally omit.",
        ] = "",
    ) -> str:
        """Restart tasks on a version (full patch or a subset)."""
        evg_ctx = ctx.request_context.lifespan_context
        async with _get_clients(evg_ctx, bearer_token=bearer_token) as (
            client,
            api_client,
            user_id,
        ):
            result = await restart_version(
                client,
                version_id=version_id,
                task_ids=task_ids,
                abort=abort,
            )
        return json.dumps(result, separators=_COMPACT_JSON)

    @mcp.tool(
        description=(
            "Submit a new Evergreen patch. "
            "Pass `server_repo_path` (path to the server's local git checkout) "
            "and optionally `module_repos` (list of {module, repo_path} dicts "
            "for each module with local changes). The server generates all diffs "
            "automatically — no need to run git commands. "
            "After creating the patch the server calls set_patch_module for "
            "each entry in module_repos, then finalizes if finalize=true. "
            "At least one task-selection mode is required: variants+tasks, "
            "regex_variants+regex_tasks, alias, or a repeat option "
            "(repeat_patch_id, repeat_definition, repeat_failed). "
            "For projects that generate their task list from a module-included "
            "file (e.g. sys-perf with DSI), pass `local_module_include_paths` "
            "so the patched config is resolved at create time. "
            "Returns the new patch_id and a Spruce URL."
        )
    )
    async def create_patch_evergreen(
        ctx: Context,
        server_repo_path: Annotated[
            str,
            "Absolute (or ~-expanded) path to the server's local git repo. "
            "The server runs `git diff --binary <upstream>...HEAD` and "
            "`git merge-base` automatically. Upstream is inferred as "
            "origin/master > origin/main > @{u}. For module-only patches "
            "(no server changes), still pass this path — the diff will be "
            "empty but the base_revision is still required.",
        ],
        module_repos: Annotated[
            list[dict] | None,
            "List of {module, repo_path} dicts, one per module to attach. "
            "The server runs `git diff --binary <upstream>...HEAD` inside each "
            "repo and calls set_patch_module automatically. Example: "
            '[{"module": "dsi", "repo_path": "~/dsi/my-branch"}]. '
            "If the repo has no local commits ahead of upstream (e.g. freshly "
            "synced ~/dsi/master), the diff is empty and the module is pinned to "
            "that exact upstream commit, unmodified — a valid, common case (no "
            "throwaway commit needed). Such modules are listed under "
            "`modules_pinned_no_diff` in the response so the pin is visible. "
            "Without this, the module falls back to whatever revision the "
            "project config tracks, which can be arbitrarily stale. "
            "After all modules are set, the patch is finalized if finalize=true.",
        ] = None,
        project_id: Annotated[
            str | None,
            "Evergreen project identifier. If omitted, auto-detected from " "context.",
        ] = None,
        description: Annotated[
            str | None,
            "Patch description shown in the Spruce UI.",
        ] = None,
        variants: Annotated[
            list[str] | str | None,
            "Build variants to run. Evergreen schedules the cross-product of "
            "variants × tasks — every listed task on every listed variant. "
            "Task+variant pairs that don't exist in the resolved project config "
            "are silently dropped (no error). Check variants_tasks in the "
            "response to confirm what was actually registered. "
            "Use ['all'] for every variant. Pair with `tasks`.",
        ] = None,
        tasks: Annotated[
            list[str] | str | None,
            "Task names to run on the selected variants. Scheduled as a "
            "cross-product with `variants`; pairs absent from the project "
            "config are silently dropped. Use ['all'] for every task. "
            "Pair with `variants`.",
        ] = None,
        regex_variants: Annotated[
            list[str] | None,
            "Regex patterns matching variants to run. Pair with regex_tasks.",
        ] = None,
        regex_tasks: Annotated[
            list[str] | None,
            "Regex patterns matching tasks to run. Pair with regex_variants.",
        ] = None,
        alias: Annotated[
            str | None,
            "Patch alias name (server-defined task selector). Discover with "
            "list_project_patch_aliases_evergreen.",
        ] = None,
        repeat_patch_id: Annotated[
            str | None,
            "If set, copy the variant/task definition from this prior patch.",
        ] = None,
        repeat_definition: Annotated[
            bool,
            "If true, reuse the most recent patch's variant/task definition.",
        ] = False,
        repeat_failed: Annotated[
            bool,
            "If true, re-run only the failed tasks from the most recent "
            "patch. Useful for the debug-and-retry loop.",
        ] = False,
        parameters: Annotated[
            list[dict] | None,
            "Patch parameters as a list of {key, value} dicts.",
        ] = None,
        trigger_aliases: Annotated[
            list[str] | None,
            "Cross-project trigger alias names.",
        ] = None,
        test_selection_include_variants: Annotated[
            list[str] | None,
            "Regex patterns — enable test selection on matching variants.",
        ] = None,
        test_selection_include_tasks: Annotated[
            list[str] | None,
            "Regex patterns — enable test selection on matching tasks.",
        ] = None,
        test_selection_exclude_variants: Annotated[
            list[str] | None,
            "Regex patterns — disable test selection on matching variants.",
        ] = None,
        test_selection_exclude_tasks: Annotated[
            list[str] | None,
            "Regex patterns — disable test selection on matching tasks.",
        ] = None,
        git_metadata: Annotated[
            dict | None,
            "Optional git context: {author, email, git_version}.",
        ] = None,
        finalize: Annotated[
            bool,
            "If true, immediately schedule the patch's tasks. Defaults to "
            "false (matches CLI); call finalize_patch_evergreen later.",
        ] = False,
        local_module_includes: Annotated[
            list[dict] | None,
            "MCP equivalent of the CLI's --include-modules. Provide a list of "
            "{module, filename, file_content} entries, one per file referenced "
            "by the project YAML's `include:` directive. Required for projects "
            "where the task list is generated from a module-included file "
            "(e.g. sys-perf reads task definitions from dsi/). Pass the FULL "
            "RAW FILE CONTENT of each YAML file in `file_content` — NOT a git "
            "diff. Evergreen uses these to resolve the project config at "
            "patch-creation time. Without this, the patched task list is "
            "resolved against the unpatched module files and selected tasks may "
            "not exist. For large files prefer local_module_include_paths.",
        ] = None,
        local_module_include_paths: Annotated[
            list[dict] | None,
            "File-path variant of local_module_includes for large files. "
            "Provide a list of {module, filename, path} entries where `path` "
            "is an absolute path on the local filesystem. The server reads "
            "each file's FULL RAW CONTENT (not a diff) and bundles it exactly "
            "as local_module_includes would. Use this instead of "
            "local_module_includes when the file content is too large to pass "
            "inline (e.g. dsi/variants.yml is ~140 KB). "
            "Entries from both parameters are merged before sending.",
        ] = None,
        bearer_token: Annotated[
            str,
            "Per-request auth override; normally omit.",
        ] = "",
    ) -> str:
        """Submit a new patch."""
        import os as _os

        # --- generate diff from server repo path ---
        try:
            effective_diff, effective_base_revision = _git_diff_from_repo(
                server_repo_path
            )
        except subprocess.CalledProcessError as exc:
            return json.dumps(
                {
                    "status": "error",
                    "error": f"git command failed in {server_repo_path}: {exc.stderr.strip() if exc.stderr else str(exc)}",
                },
                separators=_COMPACT_JSON,
            )

        # --- expand local_module_include_paths ---
        combined_includes = list(local_module_includes or [])
        for entry in local_module_include_paths or []:
            path = entry.get("path") or ""
            if not path:
                return json.dumps(
                    {"status": "error", "error": f"local_module_include_paths entry missing 'path': {entry}"},
                    separators=_COMPACT_JSON,
                )
            try:
                with open(_os.path.expanduser(path), "r", encoding="utf-8") as fh:
                    content = fh.read()
            except OSError as exc:
                return json.dumps(
                    {"status": "error", "error": f"Cannot read {path}: {exc}"},
                    separators=_COMPACT_JSON,
                )
            combined_includes.append(
                {
                    "module": entry.get("module") or "",
                    "filename": entry.get("filename") or entry.get("file_name") or "",
                    "file_content": content,
                }
            )

        # When module_repos are provided we finalize manually after setting
        # modules, so don't let create_patch trigger finalize itself.
        create_finalize = finalize and not module_repos

        evg_ctx = ctx.request_context.lifespan_context
        async with _get_clients(evg_ctx, bearer_token=bearer_token) as (
            client,
            api_client,
            user_id,
        ):
            effective_id, selection = await _resolve_project_id(
                client, user_id, project_id
            )
            if selection is not None:
                return json.dumps(selection, separators=_COMPACT_JSON)
            assert effective_id is not None
            result = await create_patch(
                api_client,
                project_id=effective_id,
                diff=effective_diff,
                base_revision=effective_base_revision,
                description=description,
                variants=_as_list(variants),
                tasks=_as_list(tasks),
                regex_variants=regex_variants,
                regex_tasks=regex_tasks,
                alias=alias,
                parameters=parameters,
                trigger_aliases=trigger_aliases,
                test_selection_include_variants=test_selection_include_variants,
                test_selection_include_tasks=test_selection_include_tasks,
                test_selection_exclude_variants=test_selection_exclude_variants,
                test_selection_exclude_tasks=test_selection_exclude_tasks,
                git_metadata=git_metadata,
                finalize=create_finalize,
                repeat_patch_id=repeat_patch_id,
                repeat_definition=repeat_definition,
                repeat_failed=repeat_failed,
                local_module_includes=combined_includes or None,
            )

            if result.get("status") != "ok":
                return json.dumps(result, separators=_COMPACT_JSON)

            patch_id = result.get("patch_id")

            # --- attach module diffs ---
            module_results = []
            for mod in module_repos or []:
                mod_name = mod.get("module") or ""
                mod_path = mod.get("repo_path") or ""
                if not mod_name or not mod_path:
                    module_results.append(
                        {"module": mod_name, "status": "error", "error": "module and repo_path are required in module_repos entries"}
                    )
                    continue
                try:
                    mod_diff, mod_base = _git_diff_from_repo(mod_path)
                except subprocess.CalledProcessError as exc:
                    module_results.append(
                        {
                            "module": mod_name,
                            "status": "error",
                            "error": f"git command failed in {mod_path}: {exc.stderr.strip() if exc.stderr else str(exc)}",
                        }
                    )
                    continue
                mod_result = await set_patch_module(
                    api_client,
                    patch_id=patch_id,
                    module_name=mod_name,
                    diff=mod_diff,
                    base_revision=mod_base,
                    message=description or "",
                )
                module_results.append({"module": mod_name, **mod_result})

            if module_results:
                result["module_results"] = module_results
                any_mod_error = any(m.get("status") == "error" for m in module_results)
                if any_mod_error:
                    result["warning"] = (
                        "One or more module diffs failed. "
                        "The patch was created but may be incomplete. "
                        "Check module_results for details. "
                        "The patch was NOT finalized."
                    )
                # Surface empty-diff pins at the top level so a "pinned to exact
                # upstream commit" module (worktree with no local commits) is
                # visible here rather than only discovered from a runtime task
                # failure. This is expected/valid, not an error.
                pinned = [
                    m.get("module")
                    for m in module_results
                    if m.get("status") == "ok" and m.get("diff_empty")
                ]
                if pinned:
                    result["modules_pinned_no_diff"] = pinned

            # --- finalize if requested (and modules were set) ---
            if finalize and module_repos and not any(
                m.get("status") == "error" for m in module_results
            ):
                fin_result = await finalize_patch(api_client, patch_id=patch_id)
                result["finalized"] = fin_result.get("status") == "ok"
                if fin_result.get("status") != "ok":
                    result["finalize_error"] = fin_result.get("error")

        return json.dumps(result, separators=_COMPACT_JSON)

    @mcp.tool(
        description=(
            "Finalize an unfinalized Evergreen patch — activates it so its "
            "selected tasks get scheduled. Use this after "
            "create_patch_evergreen when finalize=false was passed (the "
            "default). For already-finalized patches, use "
            "restart_version_evergreen instead."
        )
    )
    async def finalize_patch_evergreen(
        ctx: Context,
        patch_id: Annotated[
            str,
            "Patch identifier returned by create_patch_evergreen.",
        ],
        bearer_token: Annotated[
            str,
            "Per-request auth override; normally omit.",
        ] = "",
    ) -> str:
        """Activate an unfinalized patch."""
        evg_ctx = ctx.request_context.lifespan_context
        async with _get_clients(evg_ctx, bearer_token=bearer_token) as (
            client,
            api_client,
            user_id,
        ):
            result = await finalize_patch(api_client, patch_id=patch_id)
        return json.dumps(result, separators=_COMPACT_JSON)

    @mcp.tool(
        description=(
            "Cancel an unfinalized Evergreen patch. The patch will not run "
            "any tasks. Use this to abandon a patch that was created but "
            "should not proceed."
        )
    )
    async def cancel_patch_evergreen(
        ctx: Context,
        patch_id: Annotated[
            str,
            "Patch identifier to cancel.",
        ],
        bearer_token: Annotated[
            str,
            "Per-request auth override; normally omit.",
        ] = "",
    ) -> str:
        """Cancel an unfinalized patch."""
        evg_ctx = ctx.request_context.lifespan_context
        async with _get_clients(evg_ctx, bearer_token=bearer_token) as (
            client,
            api_client,
            user_id,
        ):
            result = await cancel_patch(api_client, patch_id=patch_id)
        return json.dumps(result, separators=_COMPACT_JSON)

    @mcp.tool(
        description=(
            "Fetch details for a single Evergreen patch by its ID. "
            "By default returns a slim summary (status, project, description, "
            "variants_tasks, times, URL) — safe to call in any context. "
            "Pass full=true only when you need every raw field from the v2 "
            "APIPatch payload; the response can be very large. "
            "Use list_user_recent_patches_evergreen to find patch IDs, or "
            "get_patch_failed_jobs_evergreen for a failure-focused view."
        )
    )
    async def get_patch_evergreen(
        ctx: Context,
        patch_id: Annotated[
            str,
            "Patch identifier.",
        ],
        full: Annotated[
            bool,
            "Return the complete raw v2 APIPatch payload. "
            "Default false (slim summary). Only set true when you need "
            "a field not present in the slim view.",
        ] = False,
        bearer_token: Annotated[
            str,
            "Per-request auth override; normally omit.",
        ] = "",
    ) -> str:
        """Fetch a single patch by id."""
        evg_ctx = ctx.request_context.lifespan_context
        async with _get_clients(evg_ctx, bearer_token=bearer_token) as (
            client,
            api_client,
            user_id,
        ):
            result = await get_patch(api_client, patch_id=patch_id)

        if not full and result.get("status") == "ok":
            raw = result.get("patch") or {}
            result = {
                "status": "ok",
                "patch_id": result.get("patch_id"),
                "url": result.get("url"),
                "project": raw.get("project_identifier") or raw.get("project_id"),
                "description": raw.get("description"),
                "patch_status": raw.get("status"),
                "activated": raw.get("activated"),
                "create_time": raw.get("create_time"),
                "finish_time": raw.get("finish_time"),
                "variants_tasks": raw.get("variants_tasks") or [],
            }

        return json.dumps(result, separators=_COMPACT_JSON)

    @mcp.tool(
        description=(
            "Attach (or replace) a module diff on an existing patch. Modules "
            "are auxiliary repositories defined by the project; submitting "
            "code changes that span multiple modules requires one call per "
            "module. The server generates the diff automatically from the "
            "module's local git repo."
        )
    )
    async def set_patch_module_evergreen(
        ctx: Context,
        patch_id: Annotated[str, "Patch identifier."],
        module_name: Annotated[
            str,
            "Module name as defined in the project's evergreen.yml modules section.",
        ],
        repo_path: Annotated[
            str,
            "Absolute path to the module's local git repo. The server runs "
            "`git diff <upstream>...HEAD` and `git merge-base` automatically. "
            "Upstream is inferred as origin/master > origin/main > @{u}.",
        ],
        message: Annotated[
            str,
            "Optional commit message / note attached to the module diff.",
        ] = "",
        bearer_token: Annotated[
            str,
            "Per-request auth override; normally omit.",
        ] = "",
    ) -> str:
        """Set a module diff on a patch."""
        try:
            diff, base_revision = _git_diff_from_repo(repo_path)
        except subprocess.CalledProcessError as exc:
            return json.dumps(
                {
                    "status": "error",
                    "patch_id": patch_id,
                    "error": f"git command failed in {repo_path}: {exc.stderr.strip() if exc.stderr else str(exc)}",
                },
                separators=_COMPACT_JSON,
            )
        evg_ctx = ctx.request_context.lifespan_context
        async with _get_clients(evg_ctx, bearer_token=bearer_token) as (
            client,
            api_client,
            user_id,
        ):
            result = await set_patch_module(
                api_client,
                patch_id=patch_id,
                module_name=module_name,
                diff=diff,
                base_revision=base_revision,
                message=message,
            )
        return json.dumps(result, separators=_COMPACT_JSON)

    @mcp.tool(
        description=(
            "Detach a module from an existing patch. Use this to remove a "
            "previously-set module diff before finalizing."
        )
    )
    async def remove_patch_module_evergreen(
        ctx: Context,
        patch_id: Annotated[str, "Patch identifier."],
        module_name: Annotated[str, "Module name to remove."],
        bearer_token: Annotated[
            str,
            "Per-request auth override; normally omit.",
        ] = "",
    ) -> str:
        """Remove a module from a patch."""
        evg_ctx = ctx.request_context.lifespan_context
        async with _get_clients(evg_ctx, bearer_token=bearer_token) as (
            client,
            api_client,
            user_id,
        ):
            result = await remove_patch_module(
                api_client, patch_id=patch_id, module_name=module_name
            )
        return json.dumps(result, separators=_COMPACT_JSON)

    @mcp.tool(
        description=(
            "List unique task names that ran on the latest waterfall version "
            "of an Evergreen project, with the build variant each was seen "
            "on. Use this to discover valid task names to pass to "
            "create_patch_evergreen's `tasks` parameter. Pairs with "
            "list_project_build_variants_evergreen."
        )
    )
    async def list_project_tasks_evergreen(
        ctx: Context,
        project_id: Annotated[
            str | None,
            "Evergreen project identifier. If omitted, auto-detected.",
        ] = None,
        bearer_token: Annotated[
            str,
            "Per-request auth override; normally omit.",
        ] = "",
    ) -> str:
        """List task names for a project."""
        evg_ctx = ctx.request_context.lifespan_context
        async with _get_clients(evg_ctx, bearer_token=bearer_token) as (
            client,
            api_client,
            user_id,
        ):
            effective_id, selection = await _resolve_project_id(
                client, user_id, project_id
            )
            if selection is not None:
                return json.dumps(selection, separators=_COMPACT_JSON)
            assert effective_id is not None
            result = await list_project_tasks(client, project_id=effective_id)
        return json.dumps(result, separators=_COMPACT_JSON)

    @mcp.tool(
        description=(
            "List patch aliases defined for an Evergreen project. Aliases "
            "are server-defined task selectors that can be used as the "
            "`alias` parameter of create_patch_evergreen. Returns each "
            "alias's name, description, and the variant/task patterns it "
            "expands to."
        )
    )
    async def list_project_patch_aliases_evergreen(
        ctx: Context,
        project_id: Annotated[
            str | None,
            "Evergreen project identifier. If omitted, auto-detected.",
        ] = None,
        include_project_config: Annotated[
            bool,
            "If true, also return aliases declared inline in the project's "
            "evergreen.yml. Defaults to false (only admin-defined aliases).",
        ] = False,
        bearer_token: Annotated[
            str,
            "Per-request auth override; normally omit.",
        ] = "",
    ) -> str:
        """List patch aliases for a project."""
        evg_ctx = ctx.request_context.lifespan_context
        async with _get_clients(evg_ctx, bearer_token=bearer_token) as (
            client,
            api_client,
            user_id,
        ):
            effective_id, selection = await _resolve_project_id(
                client, user_id, project_id
            )
            if selection is not None:
                return json.dumps(selection, separators=_COMPACT_JSON)
            assert effective_id is not None
            result = await list_project_patch_aliases(
                api_client,
                project_id=effective_id,
                include_project_config=include_project_config,
            )
        return json.dumps(result, separators=_COMPACT_JSON)

    @mcp.tool(
        description=(
            "Set the priority for a single Evergreen task. Higher priority "
            "tasks are scheduled before lower priority ones. Non-admin users "
            "can set priority up to 100 (MaxTaskPriority); values above 100 "
            "require project admin permission. Setting priority to -1 "
            "(DisabledTaskPriority) deactivates the task. "
            "Obtain task_id from get_patch_failed_jobs_evergreen, "
            "get_version_tasks_evergreen, or a Spruce task URL "
            "(https://spruce.corp.mongodb.com/task/<task_id>). "
            "To boost all tasks in a patch at once, use "
            "set_patch_priority_evergreen instead."
        )
    )
    async def set_task_priority_evergreen(
        ctx: Context,
        task_id: Annotated[
            str,
            "Task identifier to reprioritize.",
        ],
        priority: Annotated[
            int,
            "New priority value. Use -1 to disable the task. "
            "Non-admin users are limited to values <= 100.",
        ],
        bearer_token: Annotated[
            str,
            "Per-request auth override; normally omit.",
        ] = "",
    ) -> str:
        """Set priority for a single Evergreen task."""
        evg_ctx = ctx.request_context.lifespan_context
        async with _get_clients(evg_ctx, bearer_token=bearer_token) as (
            client,
            api_client,
            user_id,
        ):
            result = await set_task_priority(
                api_client, task_id=task_id, priority=priority
            )
        return json.dumps(result, separators=_COMPACT_JSON)

    @mcp.tool(
        description=(
            "Set the priority for all tasks in an Evergreen patch or version. "
            "Higher priority tasks are scheduled before lower priority ones. "
            "Non-admin users can set priority up to 100 (MaxTaskPriority); "
            "values above 100 require project admin permission. Setting "
            "priority to -1 deactivates all tasks in the patch. "
            "Obtain patch_id from list_user_recent_patches_evergreen or a "
            "Spruce version URL "
            "(https://spruce.corp.mongodb.com/version/<patch_id>/tasks). "
            "To change priority for a single task only, use "
            "set_task_priority_evergreen instead."
        )
    )
    async def set_patch_priority_evergreen(
        ctx: Context,
        patch_id: Annotated[
            str,
            "Patch (version) identifier. All tasks in this patch will have "
            "their priority updated.",
        ],
        priority: Annotated[
            int,
            "New priority value. Use -1 to disable all tasks. "
            "Non-admin users are limited to values <= 100.",
        ],
        bearer_token: Annotated[
            str,
            "Per-request auth override; normally omit.",
        ] = "",
    ) -> str:
        """Set priority for all tasks in an Evergreen patch."""
        evg_ctx = ctx.request_context.lifespan_context
        async with _get_clients(evg_ctx, bearer_token=bearer_token) as (
            client,
            api_client,
            user_id,
        ):
            result = await set_patch_priority(
                api_client, patch_id=patch_id, priority=priority
            )
        return json.dumps(result, separators=_COMPACT_JSON)

    logger.info("Registered %d tools with FastMCP server", 25)
