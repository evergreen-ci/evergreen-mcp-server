"""Task-scheduling tools for the Evergreen MCP server.

Wraps the Evergreen `scheduleTasks(versionId, taskIds)` GraphQL mutation —
the write counterpart to the read-only waterfall tools. Used to flip
previously-unscheduled tasks (status="unscheduled" in the waterfall response)
into the run queue without leaving the LLM session.
"""

import logging
from typing import Any, Dict, List, Optional


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


def _merge_variants_tasks(
    existing: List[Dict[str, Any]],
    additions: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Union existing variant/task selections with the requested additions.

    Evergreen's `schedulePatch` mutation does a raw `$set` on the patch's
    `variantsTasks` metadata (replace, not merge) — while task/build creation
    itself is additive. So to avoid clobbering the patch's recorded selection
    (Spruce checkbox state, check-run counts, `patch.variantsTasks`), we must
    resend the full union of already-selected + newly-added pairs.

    `existing` items use the GraphQL output shape ({"name": variant, "tasks":
    [...], "displayTasks": [...]}); `additions` use the tool-input shape
    ({"variant": ..., "tasks": [...]}). Both are normalised to the mutation
    input shape ({"variant", "tasks", "displayTasks"}).
    """
    # variant -> {"tasks": ordered-unique list, "displayTasks": [...]}
    merged: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []

    def _add(variant: str, tasks: List[str], display_tasks: List[Dict[str, Any]]):
        if not variant:
            return
        if variant not in merged:
            merged[variant] = {"tasks": [], "displayTasks": display_tasks or []}
            order.append(variant)
        bucket = merged[variant]
        for t in tasks or []:
            if t and t not in bucket["tasks"]:
                bucket["tasks"].append(t)
        # Preserve any display tasks we learned about (existing selection).
        if display_tasks:
            bucket["displayTasks"] = display_tasks

    for vt in existing or []:
        _add(
            vt.get("name") or vt.get("variant") or "",
            vt.get("tasks") or [],
            _shape_display_tasks(vt.get("displayTasks")),
        )
    for vt in additions or []:
        _add(
            vt.get("variant") or vt.get("name") or "",
            vt.get("tasks") or [],
            _shape_display_tasks(vt.get("displayTasks")),
        )

    return [
        {
            "variant": v,
            "tasks": merged[v]["tasks"],
            "displayTasks": merged[v]["displayTasks"],
        }
        for v in order
    ]


def _shape_display_tasks(
    display_tasks: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Normalise display-task entries to the `DisplayTask` mutation input shape.

    The mutation's `DisplayTask` input has fields `Name` and `ExecTasks`. The
    read side returns them under `name`/`execTasks`, so accept either casing.
    """
    out: List[Dict[str, Any]] = []
    for dt in display_tasks or []:
        name = dt.get("Name") or dt.get("name") or ""
        exec_tasks = dt.get("ExecTasks") or dt.get("execTasks") or []
        if name:
            out.append({"Name": name, "ExecTasks": list(exec_tasks)})
    return out


async def configure_patch(
    client,
    *,
    patch_id: str,
    add_variants_tasks: List[Dict[str, Any]],
    description: Optional[str] = None,
) -> Dict[str, Any]:
    """Add build-variant/task pairs to an existing (even finalized) patch.

    Wraps Evergreen's `schedulePatch(patchId, configure)` mutation — the same
    surface Spruce's "Configure Patch" flow uses. On an already-finalized
    patch this creates the new variant's build and tasks (reusing existing
    compile artifacts) without removing what's already scheduled.

    Because the mutation replaces the patch's `variantsTasks` metadata rather
    than merging it, this reads the patch's current selection first and submits
    the union of existing + requested pairs.

    Args:
        client: An EvergreenGraphQLClient.
        patch_id: The patch (version) to reconfigure. Requires PATCHES:EDIT.
        add_variants_tasks: List of {"variant": str, "tasks": [str, ...]} to add.
        description: Optional new description. If omitted, the patch's current
            description is preserved (the mutation requires a non-null value).

    Returns:
        Dict with the resulting patch status and its full variant/task selection.
    """
    if not patch_id:
        return {"status": "error", "error": "patch_id is required."}
    if not add_variants_tasks:
        return {
            "status": "error",
            "patch_id": patch_id,
            "error": "add_variants_tasks must be a non-empty list of "
            '{"variant": ..., "tasks": [...]} entries.',
        }
    for vt in add_variants_tasks:
        if not vt.get("variant") or not vt.get("tasks"):
            return {
                "status": "error",
                "patch_id": patch_id,
                "error": "Each add_variants_tasks entry needs a non-empty "
                "'variant' and a non-empty 'tasks' list.",
            }

    try:
        current = await client.get_patch_configure(patch_id)
    except Exception as e:
        logger.warning("get_patch_configure failed for %s", patch_id)
        return {
            "status": "error",
            "patch_id": patch_id,
            "error": _format_exception(e),
        }

    existing_vts = current.get("variantsTasks") or []
    merged = _merge_variants_tasks(existing_vts, add_variants_tasks)

    configure = {
        "description": description
        if description is not None
        else (current.get("description") or ""),
        "variantsTasks": merged,
    }
    # Preserve existing parameters so the mutation doesn't drop them.
    params = current.get("parameters") or []
    if params:
        configure["parameters"] = [
            {"key": p.get("key", ""), "value": p.get("value", "")} for p in params
        ]

    try:
        result = await client.schedule_patch(patch_id, configure)
    except Exception as e:
        logger.warning("schedulePatch failed for %s", patch_id)
        return {
            "status": "error",
            "patch_id": patch_id,
            "requested_additions": add_variants_tasks,
            "error": _format_exception(e),
        }

    result_vts = [
        {
            "variant": vt.get("name") or vt.get("variant"),
            "tasks": vt.get("tasks") or [],
        }
        for vt in (result.get("variantsTasks") or [])
    ]
    return {
        "status": "ok",
        "patch_id": patch_id,
        "patch_status": result.get("status"),
        "activated": bool(result.get("activated")),
        "requested_additions": add_variants_tasks,
        "variants_tasks": result_vts,
        "url": f"https://spruce.mongodb.com/version/{patch_id}",
    }
