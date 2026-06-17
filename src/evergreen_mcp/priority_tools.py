"""Priority-setting tools for the Evergreen MCP server."""

import logging
from typing import Any, Dict, Optional

from .schedule_tools import _format_exception

logger = logging.getLogger(__name__)

# Non-admin users cannot set priority above this value.
MAX_TASK_PRIORITY = 100
# Setting priority to -1 disables and deactivates the task.
DISABLED_TASK_PRIORITY = -1


def _spruce_task_url(task_id: str) -> Optional[str]:
    if not task_id:
        return None
    return f"https://spruce.corp.mongodb.com/task/{task_id}"


def _spruce_version_url(version_id: str) -> Optional[str]:
    if not version_id:
        return None
    return f"https://spruce.corp.mongodb.com/version/{version_id}/tasks"


async def set_task_priority(
    api_client, *, task_id: str, priority: int
) -> Dict[str, Any]:
    """Set the priority for a single Evergreen task.

    Args:
        api_client: An EvergreenRestClient.
        task_id: Task identifier.
        priority: New priority value. Non-admins are limited to <= 100.
            Use -1 to disable and deactivate the task.
    """
    if not task_id:
        return {"status": "error", "error": "task_id is required."}
    try:
        data = await api_client.set_task_priority(task_id, priority)
    except Exception as e:
        logger.warning("set_task_priority failed for task %s", task_id)
        return {
            "status": "error",
            "task_id": task_id,
            "priority": priority,
            "error": _format_exception(e),
        }
    result: Dict[str, Any] = {
        "status": "ok",
        "task_id": task_id,
        "priority": priority,
        "url": _spruce_task_url(task_id),
    }
    # Surface key fields from the API response when present.
    if isinstance(data, dict):
        for key in ("display_name", "build_variant", "status", "activated"):
            if key in data:
                result[key] = data[key]
    return result


async def set_patch_priority(
    api_client, *, patch_id: str, priority: int
) -> Dict[str, Any]:
    """Set the priority for all tasks in an Evergreen patch/version.

    Args:
        api_client: An EvergreenRestClient.
        patch_id: Patch (version) identifier.
        priority: New priority value. Non-admins are limited to <= 100.
            Use -1 to disable and deactivate all tasks in the patch.
    """
    if not patch_id:
        return {"status": "error", "error": "patch_id is required."}
    try:
        await api_client.set_patch_priority(patch_id, priority)
    except Exception as e:
        logger.warning("set_patch_priority failed for patch %s", patch_id)
        return {
            "status": "error",
            "patch_id": patch_id,
            "priority": priority,
            "error": _format_exception(e),
        }
    return {
        "status": "ok",
        "patch_id": patch_id,
        "priority": priority,
        "url": _spruce_version_url(patch_id),
    }
