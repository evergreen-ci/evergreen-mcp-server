"""Patch-creation and patch-lifecycle tools for the Evergreen MCP server.

Wraps the legacy `PUT /api/patches/`, `POST /api/patches/{id}` (finalize/cancel),
`GET /rest/v2/patches/{id}`, and module endpoints used by `evergreen patch`.
The diff itself is captured by the caller (e.g., Claude Code via Bash) and
passed in as a string; the server is responsible only for the API call.
"""

import base64
import logging
from typing import Any, Dict, List, Optional

from .schedule_tools import _format_exception

logger = logging.getLogger(__name__)


def _shape_patch(patch: Dict[str, Any]) -> Dict[str, Any]:
    """Shape a raw patch dict for return to the model.

    The legacy create endpoint returns the patch object under a "patch" key;
    its keys differ from the v2 representation. We accept either by reading
    the most useful fields if present.
    """
    return {
        "patch_id": patch.get("Id") or patch.get("id") or patch.get("patch_id"),
        "description": patch.get("Description") or patch.get("description"),
        "project": patch.get("Project") or patch.get("project_identifier"),
        "branch": patch.get("Branch") or patch.get("branch"),
        "status": patch.get("Status") or patch.get("status"),
        "activated": bool(
            patch.get("Activated")
            if patch.get("Activated") is not None
            else patch.get("activated", False)
        ),
        "version": patch.get("Version") or patch.get("version"),
        "github_patch_data": patch.get("GithubPatchData")
        or patch.get("github_patch_data"),
    }


def _spruce_patch_url(patch_id: str) -> Optional[str]:
    if not patch_id:
        return None
    return f"https://spruce.mongodb.com/version/{patch_id}"


def _validate_selection(
    *,
    variants: Optional[List[str]],
    tasks: Optional[List[str]],
    regex_variants: Optional[List[str]],
    regex_tasks: Optional[List[str]],
    alias: Optional[str],
    repeat_patch_id: Optional[str],
    repeat_definition: bool,
    repeat_failed: bool,
) -> Optional[str]:
    """Return an error string if no valid selection mode is supplied, else None.

    Mirrors the CLI: a patch must specify at least one of variants+tasks,
    regex variants+tasks, alias, or a repeat option. Multiple selection
    modes can coexist server-side, so we only require *something*.
    """
    has_explicit = bool(variants) and bool(tasks)
    has_regex = bool(regex_variants) and bool(regex_tasks)
    has_alias = bool(alias)
    has_repeat = bool(repeat_patch_id) or repeat_definition or repeat_failed
    if not (has_explicit or has_regex or has_alias or has_repeat):
        return (
            "No tasks selected. Provide one of: variants+tasks, "
            "regex_variants+regex_tasks, alias, or a repeat option "
            "(repeat_patch_id, repeat_definition, repeat_failed)."
        )
    return None


def _build_create_payload(
    *,
    project_id: str,
    diff: str,
    base_revision: str,
    description: Optional[str],
    variants: Optional[List[str]],
    tasks: Optional[List[str]],
    regex_variants: Optional[List[str]],
    regex_tasks: Optional[List[str]],
    alias: Optional[str],
    parameters: Optional[List[Dict[str, str]]],
    trigger_aliases: Optional[List[str]],
    test_selection_include_variants: Optional[List[str]],
    test_selection_include_tasks: Optional[List[str]],
    test_selection_exclude_variants: Optional[List[str]],
    test_selection_exclude_tasks: Optional[List[str]],
    git_metadata: Optional[Dict[str, str]],
    finalize: bool,
    repeat_patch_id: Optional[str],
    repeat_definition: bool,
    repeat_failed: bool,
) -> Dict[str, Any]:
    """Build the JSON payload accepted by `PUT /api/patches/`.

    Field names match the Go struct in evergreen/operations/http.go::PutPatch.
    Diffs are sent as base64-encoded bytes (Go marshals []byte as base64,
    which the server expects).
    """
    git_metadata = git_metadata or {}
    return {
        "desc": description or "",
        "project": project_id,
        "path": "",
        "patch_bytes": base64.b64encode(diff.encode("utf-8")).decode("ascii"),
        "githash": base_revision,
        "alias": alias or "",
        "buildvariants_new": variants or [],
        "tasks": tasks or [],
        "regex_buildvariants": regex_variants or [],
        "regex_tasks": regex_tasks or [],
        "regex_test_selection_buildvariants": test_selection_include_variants or [],
        "regex_test_selection_excluded_buildvariants": (
            test_selection_exclude_variants or []
        ),
        "regex_test_selection_tasks": test_selection_include_tasks or [],
        "regex_test_selection_excluded_tasks": test_selection_exclude_tasks or [],
        "finalize": bool(finalize),
        "trigger_aliases": trigger_aliases or [],
        "parameters": [
            {"key": p.get("key", ""), "value": p.get("value", "")}
            for p in (parameters or [])
        ],
        "git_metadata": {
            "username": git_metadata.get("author", ""),
            "email": git_metadata.get("email", ""),
            "git_version": git_metadata.get("git_version", ""),
        },
        "reuse_definition": bool(repeat_definition),
        "repeat_failed": bool(repeat_failed),
        "repeat_patch_id": repeat_patch_id or "",
        "github_author": "",
        "patch_author": "",
        "local_module_includes": [],
    }


async def create_patch(
    api_client,
    *,
    project_id: str,
    diff: str,
    base_revision: str,
    description: Optional[str] = None,
    variants: Optional[List[str]] = None,
    tasks: Optional[List[str]] = None,
    regex_variants: Optional[List[str]] = None,
    regex_tasks: Optional[List[str]] = None,
    alias: Optional[str] = None,
    parameters: Optional[List[Dict[str, str]]] = None,
    trigger_aliases: Optional[List[str]] = None,
    test_selection_include_variants: Optional[List[str]] = None,
    test_selection_include_tasks: Optional[List[str]] = None,
    test_selection_exclude_variants: Optional[List[str]] = None,
    test_selection_exclude_tasks: Optional[List[str]] = None,
    git_metadata: Optional[Dict[str, str]] = None,
    finalize: bool = False,
    repeat_patch_id: Optional[str] = None,
    repeat_definition: bool = False,
    repeat_failed: bool = False,
) -> Dict[str, Any]:
    """Submit a new patch to Evergreen."""
    if not project_id:
        return {"status": "error", "error": "project_id is required."}
    if not diff:
        return {
            "status": "error",
            "project_id": project_id,
            "error": (
                "diff is required. Capture it locally with `git diff --binary` "
                "against the merge-base."
            ),
        }
    if not base_revision:
        return {
            "status": "error",
            "project_id": project_id,
            "error": "base_revision is required (commit SHA the diff is based on).",
        }

    selection_error = _validate_selection(
        variants=variants,
        tasks=tasks,
        regex_variants=regex_variants,
        regex_tasks=regex_tasks,
        alias=alias,
        repeat_patch_id=repeat_patch_id,
        repeat_definition=repeat_definition,
        repeat_failed=repeat_failed,
    )
    if selection_error:
        return {
            "status": "error",
            "project_id": project_id,
            "error": selection_error,
        }

    payload = _build_create_payload(
        project_id=project_id,
        diff=diff,
        base_revision=base_revision,
        description=description,
        variants=variants,
        tasks=tasks,
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
        finalize=finalize,
        repeat_patch_id=repeat_patch_id,
        repeat_definition=repeat_definition,
        repeat_failed=repeat_failed,
    )

    try:
        response = await api_client.create_patch(payload)
    except Exception as e:
        logger.warning("create_patch failed for project %s", project_id)
        return {
            "status": "error",
            "project_id": project_id,
            "error": _format_exception(e),
        }

    raw = response.get("patch") or response
    shaped = _shape_patch(raw if isinstance(raw, dict) else {})
    patch_id = shaped.get("patch_id")
    return {
        "status": "ok",
        "patch_id": patch_id,
        "url": _spruce_patch_url(patch_id),
        "finalized": bool(finalize),
        "patch": shaped,
    }


async def finalize_patch(api_client, *, patch_id: str) -> Dict[str, Any]:
    """Activate an unfinalized patch so its tasks get scheduled."""
    if not patch_id:
        return {"status": "error", "error": "patch_id is required."}
    try:
        await api_client.modify_patch(patch_id, "finalize")
    except Exception as e:
        logger.warning("finalize_patch failed for %s", patch_id)
        return {
            "status": "error",
            "patch_id": patch_id,
            "error": _format_exception(e),
        }
    return {
        "status": "ok",
        "patch_id": patch_id,
        "action": "finalize",
        "url": _spruce_patch_url(patch_id),
    }


async def cancel_patch(api_client, *, patch_id: str) -> Dict[str, Any]:
    """Cancel an unfinalized patch."""
    if not patch_id:
        return {"status": "error", "error": "patch_id is required."}
    try:
        await api_client.modify_patch(patch_id, "cancel")
    except Exception as e:
        logger.warning("cancel_patch failed for %s", patch_id)
        return {
            "status": "error",
            "patch_id": patch_id,
            "error": _format_exception(e),
        }
    return {
        "status": "ok",
        "patch_id": patch_id,
        "action": "cancel",
    }


async def get_patch(api_client, *, patch_id: str) -> Dict[str, Any]:
    """Fetch full details for a single patch."""
    if not patch_id:
        return {"status": "error", "error": "patch_id is required."}
    try:
        data = await api_client.get_patch(patch_id)
    except Exception as e:
        logger.warning("get_patch failed for %s", patch_id)
        return {
            "status": "error",
            "patch_id": patch_id,
            "error": _format_exception(e),
        }
    return {
        "status": "ok",
        "patch_id": patch_id,
        "patch": data,
        "url": _spruce_patch_url(patch_id),
    }


async def set_patch_module(
    api_client,
    *,
    patch_id: str,
    module_name: str,
    diff: str,
    base_revision: str,
    message: str = "",
) -> Dict[str, Any]:
    """Attach a module diff to an existing patch."""
    if not patch_id:
        return {"status": "error", "error": "patch_id is required."}
    if not module_name:
        return {
            "status": "error",
            "patch_id": patch_id,
            "error": "module_name is required.",
        }
    if not diff:
        return {"status": "error", "patch_id": patch_id, "error": "diff is required."}
    if not base_revision:
        return {
            "status": "error",
            "patch_id": patch_id,
            "error": "base_revision is required.",
        }
    try:
        await api_client.set_patch_module(
            patch_id=patch_id,
            module=module_name,
            diff=diff,
            base_revision=base_revision,
            message=message,
        )
    except Exception as e:
        logger.warning(
            "set_patch_module failed for patch=%s module=%s", patch_id, module_name
        )
        return {
            "status": "error",
            "patch_id": patch_id,
            "module_name": module_name,
            "error": _format_exception(e),
        }
    return {"status": "ok", "patch_id": patch_id, "module_name": module_name}


async def remove_patch_module(
    api_client, *, patch_id: str, module_name: str
) -> Dict[str, Any]:
    """Detach a module from a patch."""
    if not patch_id:
        return {"status": "error", "error": "patch_id is required."}
    if not module_name:
        return {
            "status": "error",
            "patch_id": patch_id,
            "error": "module_name is required.",
        }
    try:
        await api_client.remove_patch_module(patch_id, module_name)
    except Exception as e:
        logger.warning(
            "remove_patch_module failed for patch=%s module=%s", patch_id, module_name
        )
        return {
            "status": "error",
            "patch_id": patch_id,
            "module_name": module_name,
            "error": _format_exception(e),
        }
    return {"status": "ok", "patch_id": patch_id, "module_name": module_name}


async def list_project_patch_aliases(
    api_client, *, project_id: str, include_project_config: bool = False
) -> Dict[str, Any]:
    """List the patch aliases defined for a project."""
    if not project_id:
        return {"status": "error", "error": "project_id is required."}
    try:
        aliases = await api_client.list_project_patch_aliases(
            project_id, include_project_config=include_project_config
        )
    except Exception as e:
        logger.warning("list_project_patch_aliases failed for %s", project_id)
        return {
            "status": "error",
            "project_id": project_id,
            "error": _format_exception(e),
        }
    shaped = [
        {
            "alias": a.get("alias") or a.get("Alias"),
            "description": a.get("description") or a.get("Description"),
            "variant": a.get("variant") or a.get("Variant"),
            "task": a.get("task") or a.get("Task"),
            "variant_tags": a.get("variant_tags") or a.get("VariantTags") or [],
            "task_tags": a.get("task_tags") or a.get("TaskTags") or [],
        }
        for a in aliases
    ]
    return {
        "project_id": project_id,
        "aliases": shaped,
        "count": len(shaped),
    }


async def list_project_tasks(graphql_client, *, project_id: str) -> Dict[str, Any]:
    """List unique task names seen on the latest waterfall version.

    Mirrors the strategy used by `fetch_project_build_variants` — issues a
    waterfall query at limit=1 and pulls out task names. This avoids
    requiring a separate REST endpoint and gives the agent a useful answer
    for "which tasks can I select for a patch in project X?".
    """
    if not project_id:
        return {"status": "error", "error": "project_id is required."}

    # Local import to avoid a circular dep between this module and waterfall_tools.
    from .waterfall_tools import _build_options

    options = _build_options(
        project_id=project_id,
        limit=1,
        max_order=None,
        min_order=None,
        revision=None,
        date=None,
        variants=None,
        tasks=None,
        statuses=None,
        requesters=None,
        omit_inactive_builds=False,
        task_case_sensitive=None,
        variant_case_sensitive=None,
    )
    try:
        raw = await graphql_client.get_waterfall(options)
    except Exception as e:
        logger.warning("list_project_tasks fetch failed for %s", project_id)
        return {
            "status": "error",
            "project_id": project_id,
            "error": _format_exception(e),
        }

    seen: Dict[str, str] = {}
    for version in raw.get("flattenedVersions") or []:
        for build in version.get("waterfallBuilds") or []:
            for task in build.get("tasks") or []:
                display_name = task.get("displayName") or task.get("display_name")
                if not display_name or display_name in seen:
                    continue
                seen[display_name] = task.get("buildVariant") or build.get(
                    "buildVariant"
                )

    tasks_out = [
        {"display_name": name, "build_variant": seen[name]}
        for name in sorted(seen.keys())
    ]
    return {
        "project_id": project_id,
        "tasks": tasks_out,
        "count": len(tasks_out),
    }
