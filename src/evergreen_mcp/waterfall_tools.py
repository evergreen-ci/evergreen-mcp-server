"""Waterfall view tools for the Evergreen MCP server.

Wraps the Evergreen `waterfall(options: WaterfallOptions!)` GraphQL query and
shapes the response for LLM consumption. Three callables back the three
registered MCP tools:

  * fetch_waterfall_summary  -> per-cell status counts (compact)
  * fetch_waterfall_detailed -> per-cell task arrays (heavier)
  * fetch_project_build_variants -> unique (build_variant, display_name) list
"""

import logging
import re
from typing import Any, Dict, List, Optional


def _format_exception(e: BaseException) -> str:
    """Render an exception as a human-readable string, even when str(e) is empty.

    Some gql transport exceptions stash details in `.errors` and have an empty
    __str__; falling back to repr() and these attrs preserves diagnostic info.
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

logger = logging.getLogger(__name__)

WATERFALL_VARIANT_CAP = 60
MAX_LIMIT_SUMMARY = 30
MAX_LIMIT_DETAILED = 15
MIN_REVISION_LENGTH = 7
MAX_COMMITS_RANGE = 200


def _normalize_date(date_str: Optional[str]) -> Optional[str]:
    """Convert a YYYY-MM-DD date to end-of-day RFC3339 UTC, or pass through.

    The GraphQL `Time` scalar accepts RFC3339; the user-facing tool takes the
    simpler date form. Already-formatted values are returned unchanged.
    """
    if not date_str:
        return None
    if "T" in date_str:
        return date_str
    return f"{date_str}T23:59:59Z"


def _validate_inputs(
    project_id: Optional[str],
    revision: Optional[str],
    max_order: Optional[int],
    min_order: Optional[int],
) -> Optional[Dict[str, Any]]:
    """Return an error dict if inputs are invalid, else None."""
    if not project_id:
        return {"status": "error", "error": "project_id is required."}
    if revision is not None and len(revision) < MIN_REVISION_LENGTH:
        return {
            "status": "error",
            "error": (
                f"revision must be at least {MIN_REVISION_LENGTH} characters "
                "(git short SHA)."
            ),
        }
    if max_order is not None and min_order is not None:
        return {
            "status": "error",
            "error": "Pass only one of max_order or min_order, not both.",
        }
    return None


def _build_options(
    *,
    project_id: str,
    limit: int,
    max_order: Optional[int],
    min_order: Optional[int],
    revision: Optional[str],
    date: Optional[str],
    variants: Optional[List[str]],
    tasks: Optional[List[str]],
    statuses: Optional[List[str]],
    requesters: Optional[List[str]],
    omit_inactive_builds: bool,
    task_case_sensitive: Optional[bool],
    variant_case_sensitive: Optional[bool],
) -> Dict[str, Any]:
    """Assemble the WaterfallOptions GraphQL input dict, omitting null fields."""
    options: Dict[str, Any] = {
        "projectIdentifier": project_id,
        "limit": limit,
        "omitInactiveBuilds": omit_inactive_builds,
    }
    if max_order is not None:
        options["maxOrder"] = max_order
    if min_order is not None:
        options["minOrder"] = min_order
    if revision:
        options["revision"] = revision
    if date:
        options["date"] = date
    if variants:
        options["variants"] = variants
    if tasks:
        options["tasks"] = tasks
    if statuses:
        options["statuses"] = statuses
    if requesters:
        options["requesters"] = requesters
    if task_case_sensitive is not None:
        options["taskCaseSensitive"] = task_case_sensitive
    if variant_case_sensitive is not None:
        options["variantCaseSensitive"] = variant_case_sensitive
    return options


def _shape_versions(
    flattened_versions: List[Dict[str, Any]],
    active_version_ids: List[str],
) -> List[Dict[str, Any]]:
    """Project active versions into the response shape.

    Inactive versions (commits with no build activity in the requested window)
    are dropped: they carry no per-cell data and only inflate the response.
    """
    active_set = set(active_version_ids or [])
    out: List[Dict[str, Any]] = []
    for v in flattened_versions or []:
        version_id = v.get("id") or ""
        if version_id not in active_set:
            continue
        out.append(
            {
                "order": v.get("order") or 0,
                "version_id": version_id,
                "revision": v.get("revision"),
                "message": v.get("message"),
                "author": v.get("author"),
                "create_time": v.get("createTime"),
                "requester": v.get("requester"),
                "status": v.get("status"),
                "activated": bool(v.get("activated")),
            }
        )
    return out


def _summary_cell(
    build: Dict[str, Any], status_filter: Optional[set] = None
) -> Dict[str, Any]:
    counts: Dict[str, int] = {}
    total = 0
    for task in build.get("tasks") or []:
        status = task.get("displayStatusCache") or "unknown"
        total += 1
        if status_filter is not None and status not in status_filter:
            continue
        counts[status] = counts.get(status, 0) + 1
    cell: Dict[str, Any] = {
        "build_id": build.get("id"),
        "activated": bool(build.get("activated")),
        "status_counts": counts,
        "total": sum(counts.values()),
    }
    if status_filter is not None:
        cell["total_unfiltered"] = total
    return cell


def _detailed_cell(
    build: Dict[str, Any],
    status_filter: Optional[set] = None,
    task_patterns: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    tasks_out: List[Dict[str, Any]] = []
    for task in build.get("tasks") or []:
        status = task.get("displayStatusCache") or "unknown"
        if status_filter is not None and status not in status_filter:
            continue
        name = task.get("displayName") or ""
        if task_patterns is not None and not any(
            p.search(name) for p in task_patterns
        ):
            continue
        tasks_out.append(
            {
                "task_id": task.get("id"),
                "display_name": name,
                "status": status,
                "execution": task.get("execution") or 0,
            }
        )
    return {
        "build_id": build.get("id"),
        "activated": bool(build.get("activated")),
        "tasks": tasks_out,
    }


def _compile_variant_filters(
    variants: Optional[List[str]], case_sensitive: bool
) -> Optional[List[Any]]:
    """Compile a list of variant filter strings as regex patterns.

    Mirrors Evergreen's behavior where each entry is treated as a regex.
    Invalid regexes are silently dropped.
    """
    if not variants:
        return None
    flags = 0 if case_sensitive else re.IGNORECASE
    compiled: List[Any] = []
    for raw in variants:
        try:
            compiled.append(re.compile(raw, flags))
        except re.error:
            logger.warning("Skipping invalid variant filter regex: %s", raw)
    return compiled or None


def _variant_matches(variant: str, patterns: Optional[List[Any]]) -> bool:
    if patterns is None:
        return True
    return any(p.search(variant) for p in patterns)


def _shape_variants_grid(
    flattened_versions: List[Dict[str, Any]],
    active_version_ids: List[str],
    *,
    detailed: bool,
    variant_cap: int,
    variant_patterns: Optional[List[Any]] = None,
    omit_inactive_builds: bool = True,
    status_filter: Optional[set] = None,
    task_patterns: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """Pivot version->builds into variant rows with version-keyed cells.

    Variants are emitted in first-seen order across versions and capped.
    Applies the `variants` filter and `omit_inactive_builds` client-side
    because the Evergreen API treats `variants` as a version-selector — it
    still returns every variant's builds for matching versions.
    """
    active_set = set(active_version_ids or [])
    rows: Dict[str, Dict[str, Any]] = {}
    matching_variant_names: set = set()
    for version in flattened_versions or []:
        version_id = version.get("id")
        if not version_id or version_id not in active_set:
            continue
        for build in version.get("waterfallBuilds") or []:
            variant = build.get("buildVariant")
            if not variant:
                continue
            if not _variant_matches(variant, variant_patterns):
                continue
            if omit_inactive_builds and not build.get("activated"):
                continue
            matching_variant_names.add(variant)
            row = rows.get(variant)
            if row is None:
                if len(rows) >= variant_cap:
                    continue
                row = {
                    "build_variant": variant,
                    "display_name": build.get("displayName"),
                    "cells": {},
                }
                rows[variant] = row
            if detailed:
                cell = _detailed_cell(
                    build, status_filter=status_filter, task_patterns=task_patterns
                )
                if status_filter is not None and not cell["tasks"]:
                    continue
            else:
                cell = _summary_cell(build, status_filter=status_filter)
                if status_filter is not None and cell["total"] == 0:
                    continue
            row["cells"][version_id] = cell

    truncated = len(matching_variant_names) > variant_cap
    non_empty_rows = [r for r in rows.values() if r["cells"]]
    return {
        "variants": non_empty_rows,
        "truncation": {
            "variants_truncated": truncated,
            "variant_cap": variant_cap,
        },
    }


def _shape_pagination(pagination: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "has_next_page": bool(pagination.get("hasNextPage")),
        "next_page_order": pagination.get("nextPageOrder") or 0,
        "has_prev_page": bool(pagination.get("hasPrevPage")),
        "prev_page_order": pagination.get("prevPageOrder") or 0,
        "most_recent_version_order": pagination.get("mostRecentVersionOrder") or 0,
        "how_to_paginate": (
            "Pass next_page_order as max_order for older versions; "
            "prev_page_order as min_order for newer."
        ),
    }


def _build_response(
    *,
    project_id: str,
    raw: Dict[str, Any],
    detailed: bool,
    variant_cap: int = WATERFALL_VARIANT_CAP,
    variant_patterns: Optional[List[Any]] = None,
    omit_inactive_builds: bool = True,
    status_filter: Optional[set] = None,
    task_patterns: Optional[List[Any]] = None,
    warnings: Optional[List[str]] = None,
) -> Dict[str, Any]:
    flattened = raw.get("flattenedVersions") or []
    pagination = raw.get("pagination") or {}
    active_ids = pagination.get("activeVersionIds") or []
    versions = _shape_versions(flattened, active_ids)
    grid = _shape_variants_grid(
        flattened,
        active_ids,
        detailed=detailed,
        variant_cap=variant_cap,
        variant_patterns=variant_patterns,
        omit_inactive_builds=omit_inactive_builds,
        status_filter=status_filter,
        task_patterns=task_patterns,
    )

    response: Dict[str, Any] = {
        "project_id": project_id,
        "versions": versions,
        "variants": grid["variants"],
        "pagination": _shape_pagination(pagination),
        "truncation": grid["truncation"],
    }
    if not versions:
        response["message"] = "No versions found for the selected filters."
    if warnings:
        response["warnings"] = warnings
    return response


async def fetch_waterfall_summary(
    client,
    *,
    project_id: str,
    limit: int = 10,
    max_order: Optional[int] = None,
    min_order: Optional[int] = None,
    revision: Optional[str] = None,
    date: Optional[str] = None,
    variants: Optional[List[str]] = None,
    statuses: Optional[List[str]] = None,
    requesters: Optional[List[str]] = None,
    omit_inactive_builds: bool = True,
) -> Dict[str, Any]:
    """Fetch the waterfall and return summary cells (status counts per cell)."""
    err = _validate_inputs(project_id, revision, max_order, min_order)
    if err:
        err["project_id"] = project_id
        return err

    clamped = max(1, min(limit, MAX_LIMIT_SUMMARY))
    options = _build_options(
        project_id=project_id,
        limit=clamped,
        max_order=max_order,
        min_order=min_order,
        revision=revision,
        date=_normalize_date(date),
        variants=variants,
        tasks=None,
        statuses=statuses,
        requesters=requesters,
        omit_inactive_builds=omit_inactive_builds,
        task_case_sensitive=None,
        variant_case_sensitive=None,
    )
    try:
        raw = await client.get_waterfall(options)
    except Exception as e:
        logger.warning("Waterfall summary fetch failed for %s", project_id)
        return {
            "status": "error",
            "project_id": project_id,
            "error": _format_exception(e),
        }

    warnings: List[str] = []
    if clamped != limit:
        warnings.append(
            f"limit was clamped from {limit} to {clamped} (max {MAX_LIMIT_SUMMARY})."
        )
    return _build_response(
        project_id=project_id,
        raw=raw,
        detailed=False,
        variant_patterns=_compile_variant_filters(variants, case_sensitive=True),
        omit_inactive_builds=omit_inactive_builds,
        status_filter=set(statuses) if statuses else None,
        warnings=warnings or None,
    )


async def fetch_waterfall_detailed(
    client,
    *,
    project_id: str,
    limit: int = 5,
    max_order: Optional[int] = None,
    min_order: Optional[int] = None,
    revision: Optional[str] = None,
    date: Optional[str] = None,
    variants: Optional[List[str]] = None,
    tasks: Optional[List[str]] = None,
    statuses: Optional[List[str]] = None,
    requesters: Optional[List[str]] = None,
    omit_inactive_builds: bool = True,
    task_case_sensitive: bool = True,
    variant_case_sensitive: bool = True,
) -> Dict[str, Any]:
    """Fetch the waterfall and return per-task detail cells."""
    err = _validate_inputs(project_id, revision, max_order, min_order)
    if err:
        err["project_id"] = project_id
        return err

    clamped = max(1, min(limit, MAX_LIMIT_DETAILED))
    options = _build_options(
        project_id=project_id,
        limit=clamped,
        max_order=max_order,
        min_order=min_order,
        revision=revision,
        date=_normalize_date(date),
        variants=variants,
        tasks=tasks,
        statuses=statuses,
        requesters=requesters,
        omit_inactive_builds=omit_inactive_builds,
        task_case_sensitive=task_case_sensitive,
        variant_case_sensitive=variant_case_sensitive,
    )
    try:
        raw = await client.get_waterfall(options)
    except Exception as e:
        logger.warning("Waterfall detailed fetch failed for %s", project_id)
        return {
            "status": "error",
            "project_id": project_id,
            "error": _format_exception(e),
        }

    warnings: List[str] = []
    if clamped != limit:
        warnings.append(
            f"limit was clamped from {limit} to {clamped} (max {MAX_LIMIT_DETAILED})."
        )
    return _build_response(
        project_id=project_id,
        raw=raw,
        detailed=True,
        variant_patterns=_compile_variant_filters(
            variants, case_sensitive=variant_case_sensitive
        ),
        omit_inactive_builds=omit_inactive_builds,
        status_filter=set(statuses) if statuses else None,
        task_patterns=_compile_variant_filters(
            tasks, case_sensitive=task_case_sensitive
        ),
        warnings=warnings or None,
    )


async def fetch_mainline_commits_between(
    client,
    *,
    project_id: str,
    start_order: int,
    end_order: int,
    requesters: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """List mainline commits between two version order numbers, inclusive.

    Built for change-point / regression analysis: returns *every* version in
    the order window, including ones the variant of interest skipped. Does
    not filter by `activeVersionIds` — the caller wants to see the commits
    a perf variant didn't run on.
    """
    if not project_id:
        return {"status": "error", "error": "project_id is required."}
    if start_order is None or end_order is None:
        return {
            "status": "error",
            "project_id": project_id,
            "error": "start_order and end_order are required.",
        }
    lo, hi = sorted((int(start_order), int(end_order)))
    span = hi - lo + 1
    clamped = min(span, MAX_COMMITS_RANGE)

    options: Dict[str, Any] = {
        "projectIdentifier": project_id,
        "limit": clamped,
        # +1 is defensive: the WaterfallOptions schema isn't checked in here,
        # so we don't know if maxOrder is inclusive or exclusive. The
        # client-side [lo, hi] filter below makes either behavior correct.
        "maxOrder": hi + 1,
    }
    if requesters:
        options["requesters"] = requesters

    try:
        raw = await client.get_mainline_commits(options)
    except Exception as e:
        logger.warning("Mainline commits fetch failed for %s", project_id)
        return {
            "status": "error",
            "project_id": project_id,
            "error": _format_exception(e),
        }

    commits: List[Dict[str, Any]] = []
    for v in raw.get("flattenedVersions") or []:
        order = v.get("order") or 0
        if order < lo or order > hi:
            continue
        commits.append(
            {
                "order": order,
                "version_id": v.get("id") or "",
                "revision": v.get("revision"),
                "message": v.get("message"),
                "author": v.get("author"),
                "create_time": v.get("createTime"),
                "requester": v.get("requester"),
                "activated": bool(v.get("activated")),
            }
        )
    commits.sort(key=lambda c: c["order"], reverse=True)

    warnings: List[str] = []
    if span > MAX_COMMITS_RANGE:
        warnings.append(
            f"Range of {span} commits exceeds max {MAX_COMMITS_RANGE}; "
            f"returned the most-recent {clamped}. Narrow the range or "
            "issue follow-up calls with adjusted bounds."
        )

    response: Dict[str, Any] = {
        "project_id": project_id,
        "start_order": lo,
        "end_order": hi,
        "count": len(commits),
        "commits": commits,
    }
    if not commits:
        response["message"] = "No mainline commits found in the requested order range."
    if warnings:
        response["warnings"] = warnings
    return response


async def fetch_project_build_variants(
    client,
    *,
    project_id: str,
) -> Dict[str, Any]:
    """List unique (build_variant, display_name) pairs for a project.

    Backed by a single waterfall query at limit=1 — no extra GraphQL surface.
    """
    if not project_id:
        return {"status": "error", "error": "project_id is required."}

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
        raw = await client.get_waterfall(options)
    except Exception as e:
        logger.warning("Build variants fetch failed for %s", project_id)
        return {
            "status": "error",
            "project_id": project_id,
            "error": _format_exception(e),
        }

    seen: Dict[str, str] = {}
    for version in raw.get("flattenedVersions") or []:
        for build in version.get("waterfallBuilds") or []:
            variant = build.get("buildVariant")
            if not variant or variant in seen:
                continue
            seen[variant] = build.get("displayName") or variant

    build_variants = [
        {"build_variant": variant, "display_name": seen[variant]}
        for variant in sorted(seen.keys())
    ]
    return {
        "project_id": project_id,
        "build_variants": build_variants,
        "count": len(build_variants),
    }
