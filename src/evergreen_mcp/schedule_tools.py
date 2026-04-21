"""Task-scheduling tools for the Evergreen MCP server.

Wraps the Evergreen `scheduleTasks(versionId, taskIds)` GraphQL mutation —
the write counterpart to the read-only waterfall tools. Used to flip
previously-unscheduled tasks (status="unscheduled" in the waterfall response)
into the run queue without leaving the LLM session.
"""

import logging
from typing import Any, Dict, List


logger = logging.getLogger(__name__)


def _format_exception(e: BaseException) -> str:
    """Render an exception as a human-readable string, even when str(e) is empty.

    Mirrors the helper in waterfall_tools.py: gql transport exceptions stash
    details in `.errors` and have an empty __str__, so we fall back to repr()
    and those attrs to preserve diagnostic info.
    """
    parts: List[str] = []
    msg = str(e)
    if msg:
        parts.append(msg)
    errs = getattr(e, "errors", None)
    if errs:
        parts.append(f"graphql_errors={errs}")
    if not parts:
        parts.append(repr(e))
    return f"{type(e).__name__}: {' | '.join(parts)}"


def _shape_task(task: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "task_id": task.get("id"),
        "display_name": task.get("displayName"),
        "build_variant": task.get("buildVariant"),
        "status": task.get("status"),
        "execution": task.get("execution") or 0,
        "activated": bool(task.get("activated")),
    }


async def schedule_unscheduled_tasks(
    client,
    *,
    version_id: str,
    task_ids: List[str],
) -> Dict[str, Any]:
    """Schedule previously-unscheduled tasks on an Evergreen version.

    Args:
        client: An EvergreenGraphQLClient.
        version_id: Version (or patch version) identifier owning the tasks.
        task_ids: Task identifiers to schedule.

    Returns:
        Dict with the requested IDs, the resulting task entities, and any
        IDs that Evergreen silently dropped (already finished, wrong version,
        missing TASKS:EDIT permission).
    """
    if not version_id:
        return {"status": "error", "error": "version_id is required."}
    if not task_ids:
        return {
            "status": "error",
            "version_id": version_id,
            "error": "task_ids must be a non-empty list.",
        }

    # Dedupe while preserving order so the response mirrors the user's intent.
    seen: set = set()
    deduped: List[str] = []
    for tid in task_ids:
        if tid and tid not in seen:
            seen.add(tid)
            deduped.append(tid)

    if not deduped:
        return {
            "status": "error",
            "version_id": version_id,
            "error": "task_ids must contain at least one non-empty identifier.",
        }

    try:
        scheduled = await client.schedule_tasks(version_id, deduped)
    except Exception as e:
        logger.warning("scheduleTasks failed for version %s", version_id)
        return {
            "status": "error",
            "version_id": version_id,
            "requested_task_ids": deduped,
            "error": _format_exception(e),
        }

    shaped = [_shape_task(t) for t in scheduled]
    returned_ids = {t["task_id"] for t in shaped if t.get("task_id")}
    missing = [tid for tid in deduped if tid not in returned_ids]

    response: Dict[str, Any] = {
        "version_id": version_id,
        "requested_task_ids": deduped,
        "scheduled_count": len(shaped),
        "scheduled_tasks": shaped,
        "missing_task_ids": missing,
    }
    if missing:
        response["message"] = (
            f"{len(missing)} task ID(s) were not scheduled. They may already be "
            "finished/running, may belong to a different version, or the caller "
            "may lack TASKS:EDIT permission on the project."
        )
    return response
