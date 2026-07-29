"""Failed jobs tools for Evergreen MCP server

This module provides the core logic for fetching user patches and failed jobs
from Evergreen.
It uses a patch-based approach focused on the authenticated user's recent patches.
"""

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from .evergreen_rest_client import EvergreenRestClient

logger = logging.getLogger(__name__)

# Constants for test status values
FAILED_TEST_STATUSES = ["fail", "failed"]


async def fetch_user_recent_patches(
    client,
    user_id: str,
    page_size: int = 10,
    page: int = 0,
    project_id: str = None,
) -> Dict[str, Any]:
    """Fetch recent patches for the authenticated user with pagination

    Args:
        client: Evergreen GraphQL client
        user_id: User identifier (typically email)
        page_size: Number of patches per page (default: 10, max: 50)
        page: Page number, 0-indexed (default: 0)
        project_id: Optional project identifier to filter patches

    Returns:
        Dictionary containing user's recent patches with pagination info
    """
    logger.info(
        "Fetching patches for user %s (page %s, page_size %s)",
        user_id,
        page,
        page_size,
    )
    if project_id:
        logger.info("Project filter: %s", project_id)

    # Get user's recent patches for this page
    patches = await client.get_user_recent_patches(user_id, page_size, page)

    # Process and format patches
    processed_patches = []
    for patch in patches:
        patch_project_identifier = (patch.get("projectMetadata") or {}).get(
            "identifier"
        )
        if project_id and patch_project_identifier != project_id:
            continue
        patch_info = {
            "patch_id": patch.get("id"),
            "patch_number": patch.get("patchNumber"),
            "githash": patch.get("githash"),
            "description": patch.get("description"),
            "author": patch.get("author"),
            "author_display_name": patch.get("authorDisplayName"),
            "status": patch.get("status"),
            "create_time": patch.get("createTime"),
            "project_identifier": patch_project_identifier,
            "has_version": patch.get("versionFull") is not None,
            "version_status": (
                patch.get("versionFull", {}).get("status")
                if patch.get("versionFull")
                else None
            ),
        }
        processed_patches.append(patch_info)

    logger.info("Successfully processed %s patches", len(processed_patches))

    # Determine if there are more pages
    # If we got a full page of results, there's likely more
    has_more = len(patches) == page_size

    return {
        "user_id": user_id,
        "project_id": project_id,
        "patches": processed_patches,
        "count": len(processed_patches),
        "page": page,
        "page_size": page_size,
        "has_more": has_more,
        "next_page": page + 1 if has_more else None,
    }


# Mirrors evergreen.TaskFailureStatuses (globals.go:422-433) plus "aborted"
# (globals.go:99). These are DISPLAY statuses: the GraphQL `statuses` filter
# matches DisplayStatusKey (model/task/db.go:2501-2507), not the raw stored
# status. The previous list omitted test-timed-out, setup-failed,
# system-unresponsive and system-timed-out, so status_filter="failed" silently
# hid setup and timeout failures.
FAILED_STATUSES = [
    "failed",
    "task-timed-out",
    "system-failed",
    "test-timed-out",
    "setup-failed",
    "system-unresponsive",
    "system-timed-out",
    "aborted",
    # A failed task with an annotation. Excluding it meant status_filter="failed"
    # silently hid every triaged failure; see status_summary_tools.FAILURE_STATUSES.
    "known-issue",
]

# Callers asked for up to 5,000 tasks in the sampled transcripts. A mainline
# sys-perf version has thousands of tasks and each shaped task is ~700 bytes, so
# an uncapped request is a multi-megabyte response nobody reads. Use
# get_status_summary_evergreen when the question is "how is it going" — its size
# is independent of task count.
MAX_TASK_RESULTS = 500


def _clamp_max_results(max_results: int) -> tuple:
    """Clamp max_results, returning (value, warning-or-None)."""
    if max_results > MAX_TASK_RESULTS:
        return MAX_TASK_RESULTS, (
            f"max_results was clamped from {max_results} to {MAX_TASK_RESULTS}. "
            "For a whole-version overview use get_status_summary_evergreen, "
            "whose response size does not grow with task count."
        )
    return max_results, None


STATUS_FILTER_MAP = {
    "failed": FAILED_STATUSES,
    # The display status for a passing task is "success" (globals.go:66);
    # "succeeded" matches nothing.
    "succeeded": ["success"],
    "all": [],
}


def _shape_failure_details(details: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Shape task details, returning None when nothing actually failed.

    Evergreen populates this struct with empty strings on a successful task
    ({"description": "", "timedOut": false, "timeoutType": "", ...}), so a naive
    truthiness check on the struct emits padding for every passing task. Only
    the keys that carry signal are included.
    """
    if not details:
        return None
    shaped = {}
    if details.get("description"):
        shaped["description"] = details["description"]
    if details.get("timedOut"):
        shaped["timed_out"] = True
        if details.get("timeoutType"):
            shaped["timeout_type"] = details["timeoutType"]
    # description is often the failing command verbatim; don't say it twice.
    failing_command = details.get("failingCommand")
    if failing_command and failing_command != details.get("description"):
        shaped["failing_command"] = failing_command
    return shaped or None


async def fetch_patch_jobs(
    client,
    patch_id: str,
    max_results: int = 50,
    project_id: str = None,
    status_filter: str = "failed",
    project_id_inferred: bool = False,
) -> Dict[str, Any]:
    """Fetch jobs for a specific patch, optionally filtered by status.

    Args:
        client: Evergreen GraphQL client
        patch_id: Patch identifier
        max_results: Maximum number of tasks to return
        project_id: Optional project identifier to validate patch ownership
        status_filter: One of "failed", "succeeded", or "all"
        project_id_inferred: True when project_id came from server-side
            inference rather than the caller. A mismatch against an inferred
            value is the server's own guess being wrong, so it is reported
            alongside the data instead of discarding a successful fetch.

    Returns:
        Dictionary containing patch info and tasks data
    """
    logger.info("Fetching %s jobs for patch %s", status_filter, patch_id)
    if project_id:
        logger.info("Project context: %s", project_id)

    max_results, clamp_warning = _clamp_max_results(max_results)
    statuses = STATUS_FILTER_MAP.get(status_filter, FAILED_STATUSES)
    patch = await client.get_patch_tasks(patch_id, statuses, limit=max_results)

    patch_project_identifier = (patch.get("projectMetadata") or {}).get("identifier")
    project_mismatch = None
    if project_id and patch_project_identifier != project_id:
        if not project_id_inferred:
            raise ValueError(
                f"Patch {patch_id} belongs to project "
                f"{patch_project_identifier!r}, not the requested {project_id!r}."
            )
        # The project_id was auto-detected, not asked for. The patch fetch
        # already succeeded, so returning the data with a note beats throwing
        # it away and making the caller pay for another round-trip.
        project_mismatch = (
            f"Auto-detected project was {project_id!r} but this patch belongs "
            f"to {patch_project_identifier!r}; using the patch's own project."
        )

    # Extract patch information
    patch_info = {
        "patch_id": patch.get("id"),
        "patch_number": patch.get("patchNumber"),
        "githash": patch.get("githash"),
        "description": patch.get("description"),
        "author": patch.get("author"),
        "author_display_name": patch.get("authorDisplayName"),
        "status": patch.get("status"),
        "create_time": patch.get("createTime"),
        "project_identifier": patch_project_identifier,
    }

    # Extract version and tasks information
    version = patch.get("versionFull", {})
    version_info = (
        {
            "version_id": version.get("id"),
            "revision": version.get("revision"),
            "author": version.get("author"),
            "create_time": version.get("createTime"),
            "status": version.get("status"),
        }
        if version
        else None
    )

    tasks_data = version.get("tasks", {}) if version else {}
    tasks = tasks_data.get("data", [])
    total_count = tasks_data.get("count", 0)

    processed_tasks = []
    build_variants = set()
    has_timeouts = False

    for task in tasks[:max_results]:
        # Extract key information
        task_info = {
            "task_id": task.get("id"),
            "task_name": task.get("displayName"),
            "build_variant": task.get("buildVariant"),
            "status": task.get("displayStatus") or task.get("status"),
            "execution": task.get("execution", 0),
            "finish_time": task.get("finishTime"),
            "duration_ms": task.get("timeTaken"),
        }

        # Host metadata only for failures, where "was it the host/AMI?" is a
        # real question. On passing tasks it was 535k of 565k sampled bytes and
        # near-constant per variant. Use get_task_log_summary for a single
        # task's host details regardless of status.
        if task_info["status"] in FAILED_STATUSES:
            task_info.update(
                {
                    "ami": task.get("ami"),
                    "host_id": task.get("hostId"),
                    "distro_id": task.get("distroId"),
                    "image_id": task.get("imageId"),
                }
            )

        # Failure details only when they say something. Evergreen returns the
        # struct populated with empty strings for a task that passed, which was
        # ~90 characters of nothing on 4,326 of 4,577 sampled tasks.
        details = task.get("details") or {}
        failure_details = _shape_failure_details(details)
        if failure_details:
            task_info["failure_details"] = failure_details
        if details.get("timedOut"):
            has_timeouts = True

        # Test counts only when the task actually produced test results. The
        # all-zero placeholder was emitted for 2,511 of 4,577 sampled tasks and
        # said nothing that the key's absence does not already say.
        if task.get("hasTestResults"):
            task_info["test_info"] = {
                "failed_test_count": task.get("failedTestCount", 0),
                "total_test_count": task.get("totalTestCount", 0),
            }

        processed_tasks.append(task_info)
        build_variants.add(task.get("buildVariant"))

    summary = {
        "total_tasks": total_count,
        "returned_tasks": len(processed_tasks),
        "build_variants": sorted(list(build_variants)),
        "has_timeouts": has_timeouts,
        "status_filter": status_filter,
    }

    logger.info(
        "Successfully processed %s tasks (%s) for patch %s",
        len(processed_tasks),
        status_filter,
        patch_id,
    )

    result = {
        "patch_info": patch_info,
        "version_info": version_info,
        "tasks": processed_tasks,
        "summary": summary,
        "project_id": patch_project_identifier or project_id,
    }
    if project_mismatch:
        result["project_mismatch"] = project_mismatch
    if clamp_warning:
        result["warning"] = clamp_warning
    return result


async def fetch_version_jobs(
    client,
    version_id: str,
    max_results: int = 50,
    status_filter: str = "all",
) -> Dict[str, Any]:
    """Fetch jobs for a specific version by version_id, optionally filtered by status.

    Unlike fetch_patch_jobs (which queries via the patch graph), this queries
    the version directly — making it the right tool for trigger_request and
    other non-patch versions that are invisible to the waterfall tools.

    Args:
        client: Evergreen GraphQL client
        version_id: Version identifier (e.g. from a Spruce URL)
        max_results: Maximum number of tasks to return
        status_filter: One of "failed", "succeeded", or "all"

    Returns:
        Dictionary containing version info and tasks data
    """
    logger.info("Fetching %s jobs for version %s", status_filter, version_id)

    max_results, clamp_warning = _clamp_max_results(max_results)
    statuses = STATUS_FILTER_MAP.get(status_filter, [])
    version = await client.get_version_tasks(version_id, statuses, limit=max_results)

    version_info = {
        "version_id": version.get("id"),
        "revision": version.get("revision"),
        "author": version.get("author"),
        "create_time": version.get("createTime"),
        "status": version.get("status"),
    }

    tasks_data = version.get("tasks", {})
    tasks = tasks_data.get("data", [])
    total_count = tasks_data.get("count", 0)

    processed_tasks = []
    build_variants = set()
    has_timeouts = False

    for task in tasks[:max_results]:
        task_info = {
            "task_id": task.get("id"),
            "task_name": task.get("displayName"),
            "build_variant": task.get("buildVariant"),
            "status": task.get("displayStatus") or task.get("status"),
            "execution": task.get("execution", 0),
            "finish_time": task.get("finishTime"),
            "duration_ms": task.get("timeTaken"),
        }

        # See fetch_patch_jobs: host metadata only where it answers something.
        if task_info["status"] in FAILED_STATUSES:
            task_info.update(
                {
                    "ami": task.get("ami"),
                    "host_id": task.get("hostId"),
                    "distro_id": task.get("distroId"),
                    "image_id": task.get("imageId"),
                }
            )

        details = task.get("details") or {}
        failure_details = _shape_failure_details(details)
        if failure_details:
            task_info["failure_details"] = failure_details
        if details.get("timedOut"):
            has_timeouts = True

        if task.get("hasTestResults"):
            task_info["test_info"] = {
                "failed_test_count": task.get("failedTestCount", 0),
                "total_test_count": task.get("totalTestCount", 0),
            }

        processed_tasks.append(task_info)
        build_variants.add(task.get("buildVariant"))

    summary = {
        "total_tasks": total_count,
        "returned_tasks": len(processed_tasks),
        "build_variants": sorted(list(build_variants)),
        "has_timeouts": has_timeouts,
        "status_filter": status_filter,
    }

    logger.info(
        "Successfully processed %s tasks (%s) for version %s",
        len(processed_tasks),
        status_filter,
        version_id,
    )

    result = {
        "version_info": version_info,
        "tasks": processed_tasks,
        "summary": summary,
    }
    if clamp_warning:
        result["warning"] = clamp_warning
    return result


async def fetch_task_logs(client, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch detailed logs for a specific task

    Args:
        client: EvergreenGraphQLClient instance
        arguments: Tool arguments containing task_id, execution, max_lines,
                   filter_errors

    Returns:
        Dictionary containing task logs,
        or an error response if the task is not found/invalid
    """
    # Extract and validate arguments
    task_id = arguments.get("task_id")
    if not task_id:
        raise ValueError("task_id parameter is required")

    execution = arguments.get("execution", 0)
    max_lines = arguments.get("max_lines", 1000)
    filter_errors = arguments.get("filter_errors", True)

    # Fetch task logs
    task_data = await client.get_task_logs(task_id, execution)

    # Process logs
    raw_logs = task_data.get("taskLogs", {}).get("taskLogs", [])
    processed_logs = process_logs(raw_logs, max_lines, filter_errors)

    logger.info(
        "Successfully fetched %s log entries for task %s",
        len(processed_logs),
        task_id,
    )

    return {
        "task_id": task_id,
        "execution": execution,
        "task_name": task_data.get("displayName"),
        "log_type": "task",
        "total_lines": len(processed_logs),
        "logs": processed_logs,
        "truncated": len(processed_logs) >= max_lines,
        # Host metadata
        "ami": task_data.get("ami"),
        "host_id": task_data.get("hostId"),
        "distro_id": task_data.get("distroId"),
        "image_id": task_data.get("imageId"),
    }


async def fetch_task_test_results(client, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch detailed test results for a specific task

    Args:
        client: EvergreenGraphQLClient instance
        arguments: Tool arguments containing task_id, execution, failed_only, limit

    Returns:
        Dictionary containing detailed test results,
        or an error response if the task is not found/invalid
    """
    # Extract and validate arguments
    task_id = arguments.get("task_id")
    if not task_id:
        raise ValueError("task_id parameter is required")

    execution = arguments.get("execution", 0)
    failed_only = arguments.get("failed_only", True)
    limit = arguments.get("limit", 100)

    # Fetch task test results
    task_data = await client.get_task_test_results(
        task_id, execution, failed_only, limit
    )

    # Extract task information
    task_info = {
        "task_id": task_data.get("id"),
        "task_name": task_data.get("displayName"),
        "build_variant": task_data.get("buildVariant"),
        "status": task_data.get("displayStatus") or task_data.get("status"),
        "execution": task_data.get("execution"),
        "has_test_results": task_data.get("hasTestResults", False),
        "failed_test_count": task_data.get("failedTestCount", 0),
        "total_test_count": task_data.get("totalTestCount", 0),
        # Host metadata
        "ami": task_data.get("ami"),
        "host_id": task_data.get("hostId"),
        "distro_id": task_data.get("distroId"),
        "image_id": task_data.get("imageId"),
    }

    # Process test results
    test_results_data = task_data.get("tests", {})
    test_results = test_results_data.get("testResults", [])

    processed_tests = []
    failed_tests = 0

    for test in test_results:
        test_result_info = {
            "test_file": test.get("testFile"),
            "status": test.get("status"),
            "duration": test.get("duration"),
            "start_time": test.get("startTime"),
            "end_time": test.get("endTime"),
            "exit_code": test.get("exitCode"),
        }

        # One log URL, not three. Evergreen returns url / urlParsley / urlRaw:
        # three renderings of the same log, each embedding the ~160-character
        # task id. Across the sampled corpus those three fields were 54% of
        # this tool's entire payload. Parsley is the viewer to hand a human,
        # and get_test_results_detailed fetches the content when needed.
        logs = test.get("logs") or {}
        log_url = logs.get("urlParsley") or logs.get("url") or logs.get("urlRaw")
        if log_url:
            test_result_info["log_url"] = log_url
        if logs.get("lineNum"):
            test_result_info["log_line"] = logs.get("lineNum")

        # Count failed tests
        if test.get("status", "").lower() in FAILED_TEST_STATUSES:
            failed_tests += 1

        processed_tests.append(test_result_info)

    # Create summary
    summary = {
        "total_test_results": test_results_data.get("totalTestCount", 0),
        "filtered_test_count": test_results_data.get("filteredTestCount", 0),
        "returned_tests": len(processed_tests),
        "failed_tests_in_results": failed_tests,
        "filter_applied": "failed tests only" if failed_only else "all tests",
    }

    logger.info(
        "Successfully processed %s test results for task %s",
        len(processed_tests),
        task_id,
    )

    return {
        "task_info": task_info,
        "test_results": processed_tests,
        "summary": summary,
    }


def process_logs(
    raw_logs: List[Dict[str, Any]], max_lines: int, filter_errors: bool
) -> List[Dict[str, Any]]:
    """Process and filter task log data based on parameters

    Args:
        raw_logs: Raw log entries from GraphQL
        max_lines: Maximum number of log lines to return
        filter_errors: Whether to filter for error/failure messages only

    Returns:
        Processed and filtered log entries
    """
    # Filter for errors if requested
    if filter_errors:
        filtered_logs = []
        for log in raw_logs:
            severity = log.get("severity", "").lower()
            message = log.get("message", "").lower()

            # Include if severity indicates error or message contains error/fail
            # keywords
            if (
                severity in ["error", "fatal"]
                or "error" in message
                or "fail" in message
                or "exception" in message
            ):
                filtered_logs.append(log)

        raw_logs = filtered_logs

    # Sort by timestamp and limit
    try:
        sorted_logs = sorted(raw_logs, key=lambda x: x.get("timestamp", ""))
    except (TypeError, ValueError):
        # If timestamp sorting fails, use original order
        sorted_logs = raw_logs

    return sorted_logs[:max_lines]


async def fetch_inferred_project_ids(
    client, user_id: str, max_patches: int = 50
) -> Dict[str, Any]:
    """Fetch unique project identifiers from user's patches

    Args:
        client: Evergreen GraphQL client
        user_id: User identifier (typically email)
        max_patches: Maximum number of patches to scan (default: 50)

    Returns:
        Dictionary containing unique project identifiers with patch counts,
        or an error response if fetching fails
    """
    logger.info(
        "Fetching inferred project IDs for user %s (max %s patches)",
        user_id,
        max_patches,
    )

    # Fetch patches to infer project identifiers
    patches = await client.get_inferred_project_ids(user_id, limit=max_patches, page=0)

    # Extract unique project identifiers and count patches per project
    project_counts: Dict[str, int] = {}
    latest_patch_times: Dict[str, str] = {}

    for patch in patches:
        project_id = (patch.get("projectMetadata") or {}).get("identifier")
        create_time = patch.get("createTime")

        if project_id:
            # Count patches per project
            project_counts[project_id] = project_counts.get(project_id, 0) + 1

            # Track latest patch time per project
            if (
                project_id not in latest_patch_times
                or create_time > latest_patch_times[project_id]
            ):
                latest_patch_times[project_id] = create_time

    # Build result list with project info
    project_list = []
    for project_id, count in project_counts.items():
        project_list.append(
            {
                "project_identifier": project_id,
                "patch_count": count,
                "latest_patch_time": latest_patch_times.get(project_id),
            }
        )

    # Sort by patch count (descending) then by latest patch time (descending)
    project_list.sort(
        key=lambda x: (-x["patch_count"], x["latest_patch_time"] or ""),
        reverse=False,
    )

    logger.info(
        "Successfully inferred %s unique project IDs from %s patches",
        len(project_list),
        len(patches),
    )

    return {
        "user_id": user_id,
        "projects": project_list,
        "total_projects": len(project_list),
        "patches_scanned": len(patches),
        "max_patches": max_patches,
    }


class ProjectInferenceResult:
    """Result of project ID inference with confidence information."""

    def __init__(
        self,
        project_id: str | None,
        confidence: str,
        available_projects: List[Dict[str, Any]],
        message: str,
        source: str,
    ):
        self.project_id = project_id
        self.confidence = confidence  # "high", "medium", "low", "none"
        self.available_projects = available_projects
        self.message = message
        self.source = (
            source  # "single_project", "workspace_match", "user_selection_required"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "confidence": self.confidence,
            "available_projects": self.available_projects,
            "message": self.message,
            "source": self.source,
        }


async def infer_project_id_from_context(
    client,
    user_id: str,
    max_patches: int = 50,
) -> ProjectInferenceResult:
    """Intelligently infer project ID from user's patches.

    1. If only one project in patches, use it (highest confidence)
    2. If multiple projects, use the one with the most recent patch (medium confidence)
       and list others as alternatives.

    Args:
        client: Evergreen GraphQL client
        user_id: User identifier (typically email)
        max_patches: Maximum number of patches to scan (default: 50)

    Returns:
        ProjectInferenceResult with project_id, confidence, and available projects
    """
    result = await fetch_inferred_project_ids(client, user_id, max_patches)
    available_projects = result["projects"]

    project_ids = [p["project_identifier"] for p in available_projects]

    if not project_ids:
        logger.warning("No project IDs found in user's recent patches")
        return ProjectInferenceResult(
            project_id=None,
            confidence="none",
            available_projects=[],
            message="No projects found in your recent patches. Please specify a project_id.",
            source="user_selection_required",
        )

    if len(project_ids) == 1:
        logger.info("Only one project found in patches, using: %s", project_ids[0])
        return ProjectInferenceResult(
            project_id=project_ids[0],
            confidence="high",
            available_projects=available_projects,
            message=f"Using project '{project_ids[0]}' (only project found in your recent patches)",
            source="single_project",
        )

    # Sort by latest_patch_time descending, then patch_count descending
    sorted_projects = sorted(
        available_projects,
        key=lambda x: (x.get("latest_patch_time") or "", x.get("patch_count", 0)),
        reverse=True,
    )

    most_recent = sorted_projects[0]
    project_id = most_recent["project_identifier"]

    # Format message listing other projects
    other_projects = [p["project_identifier"] for p in sorted_projects[1:]]
    others_msg = ", ".join(other_projects[:3])  # List up to 3 others
    if len(other_projects) > 3:
        others_msg += f", and {len(other_projects) - 3} more"

    logger.info(
        "Using most recent project '%s' (from %s) as default",
        project_id,
        most_recent.get("latest_patch_time"),
    )

    return ProjectInferenceResult(
        project_id=project_id,
        confidence="low",
        available_projects=available_projects,
        message=(f"""
                You are an ai assistant working with the user to help diagnose the recent patches. 
                The patches are coming from the project_id {project_id}. 
                However you should also verify with the user if that is the correct project_id,
                as we have other project_ids that are also valid such as {others_msg}.
                If this is incorrect, please specify project_id explicitly.
            """),
        source="most_recent_fallback",
    )


async def fetch_evergreen_task_logs(
    client: "EvergreenRestClient",
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    """Fetch task logs via the REST API.

    Args:
        client: EvergreenRestClient instance
        arguments: Tool arguments containing task_id, execution_retries

    Returns:
        Dictionary containing task logs
    """
    logger.info("fetch_evergreen_task_logs called with arguments: %s", arguments)
    task_id = arguments.get("task_id")
    execution_retries = arguments.get("execution_retries", 0)
    max_chars = arguments.get("max_chars", 0)

    logger.info("Calling client.get_task_logs for task %s", task_id)
    response = await client.get_task_logs(
        task_id, execution_retries, max_chars=max_chars
    )
    logger.info("client.get_task_logs returned type: %s", type(response))

    return {"logs": response}


async def fetch_evergreen_task_test_results(
    client: "EvergreenRestClient",
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    """Fetch raw test log content via the REST API.

    Args:
        client: EvergreenRestClient instance
        arguments: Tool arguments containing task_id, execution_retries, test_name, tail_limit

    Returns:
        Dictionary containing raw test log content
    """
    logger.info(
        "fetch_evergreen_task_test_results called with arguments: %s", arguments
    )
    task_id = arguments.get("task_id")
    execution_retries = arguments.get("execution_retries", 0)
    test_name = arguments.get("test_name")
    tail_limit = arguments.get("tail_limit", 100000)
    logger.info("Fetching test results with tail_limit: %s", tail_limit)
    response = await client.get_task_test_results(
        task_id, execution_retries, test_name, tail_limit=tail_limit
    )
    return {"logs": response}
