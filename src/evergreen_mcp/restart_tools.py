"""Task- and version-restart tools for the Evergreen MCP server.

Wraps the Evergreen `restartTask` and `restartVersions` GraphQL mutations so
a failed task or whole patch can be restarted from an LLM session without
leaving for the Spruce UI.
"""

import logging
from typing import Any, Dict, List, Optional

from .schedule_tools import _format_exception, _shape_task


logger = logging.getLogger(__name__)


def _shape_version(version: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "version_id": version.get("id"),
        "status": version.get("status"),
        "activated": bool(version.get("activated")),
    }


async def restart_task(
    client,
    *,
    task_id: str,
    failed_only: bool = False,
) -> Dict[str, Any]:
    """Restart a finished Evergreen task.

    Args:
        client: An EvergreenGraphQLClient.
        task_id: Task identifier to restart.
        failed_only: For display tasks, restart only failed execution tasks.
            Ignored for non-display tasks.

    Returns:
        Dict with the restarted task's shaped fields, or an error shape.
    """
    if not task_id:
        return {"status": "error", "error": "task_id is required."}

    try:
        restarted = await client.restart_task(task_id, failed_only)
    except Exception as e:
        logger.warning("restartTask failed for task %s", task_id)
        return {
            "status": "error",
            "task_id": task_id,
            "failed_only": failed_only,
            "error": _format_exception(e),
        }

    if not restarted:
        return {
            "status": "error",
            "task_id": task_id,
            "failed_only": failed_only,
            "error": (
                "Evergreen returned no task. The task may not exist, may not "
                "be finished yet, or the caller may lack TASKS:EDIT permission."
            ),
        }

    return {
        "task_id": task_id,
        "failed_only": failed_only,
        "task": _shape_task(restarted),
    }


async def restart_version(
    client,
    *,
    version_id: str,
    task_ids: Optional[List[str]] = None,
    abort: bool = False,
) -> Dict[str, Any]:
    """Restart tasks on an Evergreen version (patch).

    Args:
        client: An EvergreenGraphQLClient.
        version_id: Version (or patch version) identifier to restart.
        task_ids: Specific task IDs to restart. None or empty means "restart
            all completed tasks on the version".
        abort: If True, abort in-progress tasks before restarting.

    Returns:
        Dict with the restarted version(s) and echo of the request, or an
        error shape.
    """
    if not version_id:
        return {"status": "error", "error": "version_id is required."}

    deduped: List[str] = []
    if task_ids:
        seen: set = set()
        for tid in task_ids:
            if tid and tid not in seen:
                seen.add(tid)
                deduped.append(tid)

    try:
        versions = await client.restart_versions(version_id, abort, deduped)
    except Exception as e:
        logger.warning("restartVersions failed for version %s", version_id)
        return {
            "status": "error",
            "version_id": version_id,
            "abort": abort,
            "requested_task_ids": deduped,
            "error": _format_exception(e),
        }

    return {
        "version_id": version_id,
        "abort": abort,
        "requested_task_ids": deduped,
        "restarted_all_completed": not deduped,
        "restarted_versions": [_shape_version(v) for v in versions],
    }
