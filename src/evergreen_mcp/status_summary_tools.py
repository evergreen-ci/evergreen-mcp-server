"""Compact status-summary tool for polling a patch or version.

This is the `_summary` counterpart to get_patch_failed_jobs_evergreen and
get_version_tasks_evergreen, which return one fully-shaped object per task
(task_id, host metadata, test counts, timing). Those are the right shape when
you are diagnosing a specific failure, but the overwhelmingly common use is
polling: "is it done yet, and if something broke, what broke?"

Measured on the local transcript corpus (2,083 Evergreen MCP calls), the
detailed shape is ~49x larger than the answer to that question, and every byte
is re-transmitted on each later turn of the conversation. So this module
returns an explicit, deliberately narrow schema:

    status / done / counts / failed_tasks[{task_name, build_variant, ...}]

Per-task status counts come from the GraphQL `taskStatusStats` aggregate, so
the response stays the same size whether the version has 10 tasks or 4,000.
Only failing tasks are enumerated.
"""

import logging
from typing import Any, Dict, List, Optional

from evergreen_mcp.utils import patch_version

logger = logging.getLogger(__name__)

# All status values below are DISPLAY statuses, not the raw stored status.
# Both the `statuses` task filter and taskStatusStats key off DisplayStatusKey
# (evergreen model/task/db.go:2501-2507 and :2114), whereas the raw `status`
# field only ever holds success/failed/started/undispatched
# (evergreen globals.go:50-71). Mixing the two silently loses every
# system/setup/timeout failure mode.

# Mirrors evergreen.TaskFailureStatuses (globals.go:422-433) — every way a
# completed task can fail — plus two display statuses that also mean "did not
# pass": "aborted" (globals.go:99) and "known-issue".
#
# "known-issue" is a FAILED task that carries an annotation. Evergreen sets it
# from HasAnnotations, checked first and unconditionally in the display-status
# switch (model/task/task.go:2193-2195), which is why it hides the underlying
# failure status. Evergreen's own definition is explicit that this is a failure:
# isSuccessfulTask (operations/last_revision.go:435-441) is
#     status == TaskSucceeded || (knownIssuesAreSuccess && IsFailedTaskStatus(status) && HasAnnotations)
# i.e. it counts as success ONLY when the caller opts in via
# knownIssuesAreSuccess. Bucketing it as succeeded would apply that opt-in
# silently and let a summary read clean while a real failure sits in it.
FAILURE_STATUSES = [
    "failed",
    "task-timed-out",
    "system-failed",
    "test-timed-out",
    "setup-failed",
    "system-unresponsive",
    "system-timed-out",
    "aborted",
    "known-issue",
]

# Buckets used to roll the taskStatusStats counts into something a poller can
# branch on. Any status not listed is still reported verbatim under
# counts.by_status and rolled into the "other" bucket, so a newly added
# Evergreen status can never be silently dropped.

# Only a genuine pass. See FAILURE_STATUSES for why "known-issue" is not here.
_SUCCESS = {"success"}

# evergreen.TaskInProgressStatuses (globals.go:1162) — picked up by an agent.
_RUNNING = {"started", "dispatched"}

# Activated and expected to run, so these legitimately hold the version open.
_WAITING = {"will-run", "pending", "undispatched"}

# Terminal-for-polling: these tasks are NOT going to produce a result without
# someone intervening, so they must not count towards in_flight. Treating them
# as pending is how a poll loop ends up waiting forever on a patch that will
# never progress: "unscheduled" means not activated (globals.go:78),
# "blocked" means a dependency will not satisfy (globals.go:104), and
# "inactive" is the deprecated legacy equivalent (globals.go:84).
_NOT_SCHEDULED = {"unscheduled", "blocked", "inactive"}

DEFAULT_MAX_FAILED_TASKS = 50
DEFAULT_DESCRIPTION_MAX_CHARS = 300


def _bucket(status: str) -> str:
    if status in _SUCCESS:
        return "succeeded"
    if status in _RUNNING:
        return "running"
    if status in _WAITING:
        return "waiting"
    if status in _NOT_SCHEDULED:
        return "not_scheduled"
    if status in FAILURE_STATUSES:
        return "failed"
    return "other"


def _shape_counts(stats: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Roll taskStatusStats.counts into buckets plus verbatim per-status counts."""
    raw = (stats or {}).get("counts") or []
    counts: Dict[str, int] = {}
    buckets = {
        "succeeded": 0,
        "failed": 0,
        "running": 0,
        "waiting": 0,
        "not_scheduled": 0,
        "other": 0,
    }
    for entry in raw:
        status = entry.get("status")
        count = entry.get("count", 0)
        if not status:
            continue
        counts[status] = counts.get(status, 0) + count
        buckets[_bucket(status)] += count
    return {"by_status": counts, "buckets": buckets}


def _shape_failed_tasks(
    tasks: List[Dict[str, Any]],
    max_failed_tasks: int,
    description_max_chars: int,
) -> List[Dict[str, Any]]:
    """Shape failing tasks down to identity + why it failed.

    Deliberately omits task_id (Evergreen task ids average ~160 characters and
    accounted for 13.6% of detailed-response bytes). Re-derive a task_id with
    get_patch_failed_jobs_evergreen / get_version_tasks_evergreen when you
    actually need to drill into logs.
    """
    shaped = []
    for task in tasks[:max_failed_tasks]:
        details = task.get("details") or {}
        description = details.get("description") or ""
        truncated = len(description) > description_max_chars
        entry = {
            "task_name": task.get("displayName"),
            "build_variant": task.get("buildVariant"),
            "status": task.get("displayStatus") or task.get("status"),
            "description": description[:description_max_chars],
        }
        if truncated:
            entry["description_truncated"] = True
        if details.get("timedOut"):
            entry["timed_out"] = True
            if details.get("timeoutType"):
                entry["timeout_type"] = details.get("timeoutType")
        # Evergreen frequently sets details.description to the failing command
        # verbatim, so emitting both repeats the same string. Observed live: on a
        # 4,314-task mainline version every enumerated failure duplicated it,
        # ~22% of the response.
        failing_command = details.get("failingCommand")
        if failing_command and failing_command != description:
            entry["failing_command"] = failing_command
        if task.get("execution"):
            entry["execution"] = task.get("execution")
        shaped.append(entry)
    return shaped


def _build_summary(
    *,
    version: Dict[str, Any],
    counts: Dict[str, Any],
    failed_tasks_data: Dict[str, Any],
    max_failed_tasks: int,
    description_max_chars: int,
    patch: Optional[Dict[str, Any]] = None,
    project_identifier: Optional[str] = None,
) -> Dict[str, Any]:
    stats = version.get("taskStatusStats") or {}
    buckets = counts["buckets"]
    in_flight = buckets["running"] + buckets["waiting"]

    failed_total = (failed_tasks_data or {}).get("count", 0)
    failed = _shape_failed_tasks(
        (failed_tasks_data or {}).get("data") or [],
        max_failed_tasks,
        description_max_chars,
    )

    summary: Dict[str, Any] = {
        "version_id": version.get("id"),
        "project": project_identifier,
        "version_status": version.get("status"),
        # An explicit done indicator, so a poller never has to infer
        # completion from a status string it may not recognise. Only running
        # and genuinely-queued tasks count towards in_flight; see
        # _NOT_SCHEDULED for why unscheduled/blocked deliberately do not.
        #
        # done is NOT monotonic. Because unscheduled tasks are excluded from
        # in_flight, a version whose tasks have not been activated yet reports
        # done=True and can later go back to False:
        #   - Mainline versions: taskStatusStats takes no arguments and the
        #     resolver hardcodes includeNeverActivated = !IsPatchRequester
        #     (version_resolver.go:532-536), so a mainline version always
        #     includes never-activated tasks. A never-activated task displays
        #     as "unscheduled" (model/task/db.go:281-289), so a fresh mainline
        #     version with batchtime variants is all-unscheduled => done=True,
        #     until the scheduler activates it.
        #   - After an abort (display status "aborted" => failed bucket) a
        #     restart resets tasks to "will-run" => done goes False again.
        #   - configure_patch / schedule_tasks add work to a finished version.
        # done_caveat below makes this visible instead of silent.
        "done": in_flight == 0,
        "in_flight": in_flight,
        "counts": counts,
        "failed_task_count": failed_total,
        "failed_tasks": failed,
    }
    if patch is not None:
        summary["patch_id"] = patch.get("id")
        summary["description"] = patch.get("description")
        summary["patch_status"] = patch.get("status")
    if version.get("startTime"):
        summary["start_time"] = version.get("startTime")
    if version.get("finishTime"):
        summary["finish_time"] = version.get("finishTime")
    if stats.get("eta"):
        # Evergreen's own estimate of when the remaining tasks finish — use it
        # to choose when to poll again rather than polling on a fixed timer.
        summary["eta"] = stats.get("eta")
    # Turn the non-monotonic-done trap into information the caller can act on:
    # "done, but N tasks are merely unscheduled and may still be activated".
    not_scheduled = buckets.get("not_scheduled", 0)
    if summary["done"] and not_scheduled:
        summary["done_caveat"] = (
            f"{not_scheduled} task(s) are unscheduled/blocked and are not "
            "counted as in-flight. done can return to false if they are "
            "activated later (batchtime on mainline versions, a restart, or "
            "configure_patch). Re-check before treating this as final."
        )

    if failed_total > len(failed):
        summary["failed_tasks_truncated"] = {
            "returned": len(failed),
            "total": failed_total,
            "hint": "raise max_failed_tasks to see the rest",
        }
    return summary


async def fetch_patch_status_summary(
    client,
    patch_id: str,
    max_failed_tasks: int = DEFAULT_MAX_FAILED_TASKS,
    description_max_chars: int = DEFAULT_DESCRIPTION_MAX_CHARS,
) -> Dict[str, Any]:
    """Fetch a compact status summary for a patch.

    Args:
        client: Evergreen GraphQL client
        patch_id: Patch identifier
        max_failed_tasks: Maximum number of failing tasks to enumerate
        description_max_chars: Truncate each failure description to this length

    Returns:
        Compact status summary dictionary
    """
    logger.info("Fetching status summary for patch %s", patch_id)
    patch = await client.get_patch_status_summary(
        patch_id, FAILURE_STATUSES, failed_limit=max_failed_tasks
    )

    version = patch_version(patch)
    if not version:
        # Unfinalized patch: no version exists yet, so there is nothing to poll.
        return {
            "patch_id": patch.get("id"),
            "description": patch.get("description"),
            "patch_status": patch.get("status"),
            "project": (patch.get("projectMetadata") or {}).get("identifier"),
            "version_id": None,
            "done": False,
            "in_flight": 0,
            "counts": {"by_status": {}, "buckets": {}},
            "failed_task_count": 0,
            "failed_tasks": [],
            "message": (
                "Patch has no version yet — it is not finalized/scheduled. "
                "Call finalize_patch_evergreen to schedule it."
            ),
        }

    return _build_summary(
        version=version,
        counts=_shape_counts(version.get("taskStatusStats")),
        failed_tasks_data=version.get("tasks") or {},
        max_failed_tasks=max_failed_tasks,
        description_max_chars=description_max_chars,
        patch=patch,
        project_identifier=(patch.get("projectMetadata") or {}).get("identifier"),
    )


async def fetch_version_status_summary(
    client,
    version_id: str,
    max_failed_tasks: int = DEFAULT_MAX_FAILED_TASKS,
    description_max_chars: int = DEFAULT_DESCRIPTION_MAX_CHARS,
) -> Dict[str, Any]:
    """Fetch a compact status summary for a version.

    Args:
        client: Evergreen GraphQL client
        version_id: Version identifier (e.g. from a Spruce URL)
        max_failed_tasks: Maximum number of failing tasks to enumerate
        description_max_chars: Truncate each failure description to this length

    Returns:
        Compact status summary dictionary
    """
    logger.info("Fetching status summary for version %s", version_id)
    version = await client.get_version_status_summary(
        version_id, FAILURE_STATUSES, failed_limit=max_failed_tasks
    )

    return _build_summary(
        version=version,
        counts=_shape_counts(version.get("taskStatusStats")),
        failed_tasks_data=version.get("tasks") or {},
        max_failed_tasks=max_failed_tasks,
        description_max_chars=description_max_chars,
        project_identifier=(version.get("projectMetadata") or {}).get("identifier"),
    )
