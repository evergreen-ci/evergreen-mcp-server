"""Tests for the waterfall view tools."""

import unittest
from unittest.mock import AsyncMock

from evergreen_mcp.waterfall_tools import (
    MAX_COMMITS_RANGE,
    MAX_LIMIT_DETAILED,
    MAX_LIMIT_SUMMARY,
    WATERFALL_VARIANT_CAP,
    fetch_mainline_commits_between,
    fetch_project_build_variants,
    fetch_waterfall_detailed,
    fetch_waterfall_summary,
)


def _make_task(name: str, status: str, task_id: str | None = None) -> dict:
    return {
        "id": task_id or f"task-{name}",
        "displayName": name,
        "displayStatusCache": status,
        "execution": 0,
    }


def _make_build(variant: str, version_id: str, statuses: list[str]) -> dict:
    return {
        "id": f"build-{variant}-{version_id}",
        "activated": True,
        "buildVariant": variant,
        "displayName": variant.replace("-", " ").title(),
        "version": version_id,
        "tasks": [_make_task(f"{variant}-{i}", s) for i, s in enumerate(statuses)],
    }


def _make_version(
    version_id: str, order: int, builds: list[dict], activated: bool = True
) -> dict:
    return {
        "id": version_id,
        "revision": f"rev{version_id}",
        "author": "alice",
        "message": f"msg-{version_id}",
        "createTime": "2026-04-17T10:00:00Z",
        "order": order,
        "activated": activated,
        "requester": "gitter_request",
        "status": "failed",
        "waterfallBuilds": builds,
    }


def _make_pagination(active_ids: list[str], **overrides) -> dict:
    base = {
        "activeVersionIds": active_ids,
        "hasNextPage": True,
        "hasPrevPage": False,
        "mostRecentVersionOrder": 100,
        "nextPageOrder": 90,
        "prevPageOrder": 110,
    }
    base.update(overrides)
    return base


class TestFetchWaterfallSummary(unittest.IsolatedAsyncioTestCase):
    async def test_summary_basic_grid(self):
        """Two versions × two variants produces correct status counts."""
        mock_client = AsyncMock()
        mock_client.get_waterfall.return_value = {
            "flattenedVersions": [
                _make_version(
                    "v1",
                    100,
                    [
                        _make_build("ubuntu", "v1", ["success", "success", "failed"]),
                        _make_build("rhel", "v1", ["success"]),
                    ],
                ),
                _make_version(
                    "v2",
                    99,
                    [
                        _make_build("ubuntu", "v2", ["failed", "failed"]),
                        _make_build("rhel", "v2", ["success", "success"]),
                    ],
                ),
            ],
            "pagination": _make_pagination(["v1", "v2"]),
        }

        result = await fetch_waterfall_summary(mock_client, project_id="proj", limit=10)

        self.assertEqual(result["project_id"], "proj")
        self.assertEqual(len(result["versions"]), 2)
        self.assertEqual(len(result["variants"]), 2)

        ubuntu_row = next(
            r for r in result["variants"] if r["build_variant"] == "ubuntu"
        )
        self.assertEqual(
            ubuntu_row["cells"]["v1"]["status_counts"], {"success": 2, "failed": 1}
        )
        self.assertEqual(ubuntu_row["cells"]["v1"]["total"], 3)
        self.assertEqual(ubuntu_row["cells"]["v2"]["status_counts"], {"failed": 2})

        self.assertEqual(result["pagination"]["next_page_order"], 90)
        self.assertEqual(result["pagination"]["prev_page_order"], 110)
        self.assertTrue(result["pagination"]["has_next_page"])
        self.assertFalse(result["truncation"]["variants_truncated"])

    async def test_inactive_version_dropped(self):
        """Versions not in activeVersionIds are dropped entirely."""
        mock_client = AsyncMock()
        mock_client.get_waterfall.return_value = {
            "flattenedVersions": [
                _make_version("v1", 100, [_make_build("ubuntu", "v1", ["success"])]),
                _make_version("v_inactive", 99, [], activated=False),
            ],
            "pagination": _make_pagination(["v1"]),
        }

        result = await fetch_waterfall_summary(mock_client, project_id="proj")

        version_ids = [v["version_id"] for v in result["versions"]]
        self.assertEqual(version_ids, ["v1"])

        ubuntu_row = result["variants"][0]
        self.assertIn("v1", ubuntu_row["cells"])
        self.assertNotIn("v_inactive", ubuntu_row["cells"])

    async def test_inactive_build_omitted(self):
        """Cells with build.activated=false are dropped when omit_inactive_builds=True."""
        mock_client = AsyncMock()
        active_build = _make_build("ubuntu", "v1", ["success"])
        inactive_build = _make_build("rhel", "v1", ["unscheduled"])
        inactive_build["activated"] = False
        mock_client.get_waterfall.return_value = {
            "flattenedVersions": [_make_version("v1", 100, [active_build, inactive_build])],
            "pagination": _make_pagination(["v1"]),
        }

        result = await fetch_waterfall_summary(
            mock_client, project_id="proj", omit_inactive_builds=True
        )

        variant_names = [r["build_variant"] for r in result["variants"]]
        self.assertEqual(variant_names, ["ubuntu"])

    async def test_statuses_filter_summary(self):
        """statuses filter restricts status_counts and drops empty cells."""
        mock_client = AsyncMock()
        mock_client.get_waterfall.return_value = {
            "flattenedVersions": [
                _make_version(
                    "v1",
                    100,
                    [
                        _make_build("ubuntu", "v1", ["success", "failed", "success"]),
                        _make_build("rhel", "v1", ["success", "success"]),
                    ],
                ),
            ],
            "pagination": _make_pagination(["v1"]),
        }

        result = await fetch_waterfall_summary(
            mock_client, project_id="proj", statuses=["failed"]
        )

        # rhel had no failed tasks → variant row dropped entirely
        names = [r["build_variant"] for r in result["variants"]]
        self.assertEqual(names, ["ubuntu"])
        cell = result["variants"][0]["cells"]["v1"]
        self.assertEqual(cell["status_counts"], {"failed": 1})
        self.assertEqual(cell["total"], 1)
        self.assertEqual(cell["total_unfiltered"], 3)

    async def test_variants_filter_applied_client_side(self):
        """Variant rows not matching the variants filter are dropped."""
        mock_client = AsyncMock()
        mock_client.get_waterfall.return_value = {
            "flattenedVersions": [
                _make_version(
                    "v1",
                    100,
                    [
                        _make_build("ubuntu-2204", "v1", ["success"]),
                        _make_build("ubuntu-2004", "v1", ["success"]),
                        _make_build("rhel-8", "v1", ["success"]),
                    ],
                ),
            ],
            "pagination": _make_pagination(["v1"]),
        }

        result = await fetch_waterfall_summary(
            mock_client, project_id="proj", variants=["ubuntu.*"]
        )

        names = sorted(r["build_variant"] for r in result["variants"])
        self.assertEqual(names, ["ubuntu-2004", "ubuntu-2204"])

    async def test_variant_truncation(self):
        """More variants than the cap sets variants_truncated=True."""
        mock_client = AsyncMock()
        many_variants = [
            _make_build(f"variant-{i:03d}", "v1", ["success"])
            for i in range(WATERFALL_VARIANT_CAP + 20)
        ]
        mock_client.get_waterfall.return_value = {
            "flattenedVersions": [_make_version("v1", 100, many_variants)],
            "pagination": _make_pagination(["v1"]),
        }

        result = await fetch_waterfall_summary(mock_client, project_id="proj")

        self.assertEqual(len(result["variants"]), WATERFALL_VARIANT_CAP)
        self.assertTrue(result["truncation"]["variants_truncated"])
        self.assertEqual(result["truncation"]["variant_cap"], WATERFALL_VARIANT_CAP)

    async def test_date_normalization(self):
        """YYYY-MM-DD becomes RFC3339 end-of-day in the GraphQL options."""
        mock_client = AsyncMock()
        mock_client.get_waterfall.return_value = {
            "flattenedVersions": [],
            "pagination": _make_pagination([]),
        }

        await fetch_waterfall_summary(mock_client, project_id="proj", date="2026-04-17")

        sent_options = mock_client.get_waterfall.await_args.args[0]
        self.assertEqual(sent_options["date"], "2026-04-17T23:59:59Z")
        self.assertEqual(sent_options["projectIdentifier"], "proj")

    async def test_revision_short_rejected(self):
        """A short revision is rejected without calling GraphQL."""
        mock_client = AsyncMock()

        result = await fetch_waterfall_summary(
            mock_client, project_id="proj", revision="abc"
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("revision", result["error"])
        mock_client.get_waterfall.assert_not_awaited()

    async def test_both_orders_rejected(self):
        """Setting both max_order and min_order is rejected."""
        mock_client = AsyncMock()

        result = await fetch_waterfall_summary(
            mock_client, project_id="proj", max_order=100, min_order=200
        )

        self.assertEqual(result["status"], "error")
        mock_client.get_waterfall.assert_not_awaited()

    async def test_limit_clamping_summary(self):
        """A huge limit is clamped to MAX_LIMIT_SUMMARY and emits a warning."""
        mock_client = AsyncMock()
        mock_client.get_waterfall.return_value = {
            "flattenedVersions": [],
            "pagination": _make_pagination([]),
        }

        result = await fetch_waterfall_summary(
            mock_client, project_id="proj", limit=500
        )

        sent_options = mock_client.get_waterfall.await_args.args[0]
        self.assertEqual(sent_options["limit"], MAX_LIMIT_SUMMARY)
        self.assertIn("warnings", result)
        self.assertTrue(any("clamped" in w for w in result["warnings"]))

    async def test_project_not_found(self):
        """A GraphQL exception becomes a structured error response."""
        mock_client = AsyncMock()
        mock_client.get_waterfall.side_effect = Exception("project not found")

        result = await fetch_waterfall_summary(mock_client, project_id="missing")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["project_id"], "missing")
        self.assertIn("project not found", result["error"])

    async def test_empty_waterfall(self):
        """Empty results carry an explicit message."""
        mock_client = AsyncMock()
        mock_client.get_waterfall.return_value = {
            "flattenedVersions": [],
            "pagination": _make_pagination([]),
        }

        result = await fetch_waterfall_summary(mock_client, project_id="proj")

        self.assertEqual(result["versions"], [])
        self.assertEqual(result["variants"], [])
        self.assertIn("message", result)


class TestFetchWaterfallDetailed(unittest.IsolatedAsyncioTestCase):
    async def test_detailed_returns_task_ids(self):
        """Detailed cells expose task_id, status, and execution."""
        mock_client = AsyncMock()
        builds = [_make_build("ubuntu", "v1", ["failed", "success"])]
        mock_client.get_waterfall.return_value = {
            "flattenedVersions": [_make_version("v1", 100, builds)],
            "pagination": _make_pagination(["v1"]),
        }

        result = await fetch_waterfall_detailed(mock_client, project_id="proj")

        cell = result["variants"][0]["cells"]["v1"]
        self.assertEqual(len(cell["tasks"]), 2)
        first = cell["tasks"][0]
        self.assertIn("task_id", first)
        self.assertIn("display_name", first)
        self.assertEqual(first["status"], "failed")
        self.assertEqual(first["execution"], 0)

    async def test_limit_clamping_detailed(self):
        mock_client = AsyncMock()
        mock_client.get_waterfall.return_value = {
            "flattenedVersions": [],
            "pagination": _make_pagination([]),
        }

        await fetch_waterfall_detailed(mock_client, project_id="proj", limit=500)

        sent_options = mock_client.get_waterfall.await_args.args[0]
        self.assertEqual(sent_options["limit"], MAX_LIMIT_DETAILED)

    async def test_statuses_and_tasks_filter_detailed(self):
        """statuses + tasks filters drop non-matching tasks; empty cells/rows pruned."""
        mock_client = AsyncMock()
        builds = [
            _make_build("ubuntu", "v1", ["success", "failed", "success"]),
            _make_build("rhel", "v1", ["success"]),
        ]
        # Rename one task for filter testing
        builds[0]["tasks"][1]["displayName"] = "compile_x"
        mock_client.get_waterfall.return_value = {
            "flattenedVersions": [_make_version("v1", 100, builds)],
            "pagination": _make_pagination(["v1"]),
        }

        result = await fetch_waterfall_detailed(
            mock_client,
            project_id="proj",
            statuses=["failed"],
            tasks=["compile.*"],
        )

        names = [r["build_variant"] for r in result["variants"]]
        self.assertEqual(names, ["ubuntu"])
        tasks = result["variants"][0]["cells"]["v1"]["tasks"]
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["status"], "failed")
        self.assertEqual(tasks[0]["display_name"], "compile_x")

    async def test_case_sensitivity_flags_propagate(self):
        mock_client = AsyncMock()
        mock_client.get_waterfall.return_value = {
            "flattenedVersions": [],
            "pagination": _make_pagination([]),
        }

        await fetch_waterfall_detailed(
            mock_client,
            project_id="proj",
            tasks=["compile"],
            task_case_sensitive=False,
            variant_case_sensitive=False,
        )

        sent_options = mock_client.get_waterfall.await_args.args[0]
        self.assertEqual(sent_options["taskCaseSensitive"], False)
        self.assertEqual(sent_options["variantCaseSensitive"], False)
        self.assertEqual(sent_options["tasks"], ["compile"])


class TestFetchProjectBuildVariants(unittest.IsolatedAsyncioTestCase):
    async def test_dedupes_and_sorts(self):
        """Variants are deduplicated and returned alphabetically."""
        mock_client = AsyncMock()
        mock_client.get_waterfall.return_value = {
            "flattenedVersions": [
                _make_version(
                    "v1",
                    100,
                    [
                        _make_build("ubuntu", "v1", ["success"]),
                        _make_build("rhel", "v1", ["success"]),
                        _make_build("ubuntu", "v1", ["success"]),
                    ],
                ),
            ],
            "pagination": _make_pagination(["v1"]),
        }

        result = await fetch_project_build_variants(mock_client, project_id="proj")

        names = [v["build_variant"] for v in result["build_variants"]]
        self.assertEqual(names, ["rhel", "ubuntu"])
        self.assertEqual(result["count"], 2)

    async def test_requires_project_id(self):
        mock_client = AsyncMock()
        result = await fetch_project_build_variants(mock_client, project_id="")
        self.assertEqual(result["status"], "error")
        mock_client.get_waterfall.assert_not_awaited()


def _make_commit_version(
    version_id: str,
    order: int,
    *,
    activated: bool = True,
    requester: str = "gitter_request",
) -> dict:
    """Lean version dict for the mainline-commits fetcher (no waterfallBuilds)."""
    return {
        "id": version_id,
        "revision": f"rev{version_id}",
        "author": "alice",
        "message": f"msg-{version_id}",
        "createTime": "2026-04-17T10:00:00Z",
        "order": order,
        "activated": activated,
        "requester": requester,
    }


class TestFetchMainlineCommitsBetween(unittest.IsolatedAsyncioTestCase):
    async def test_happy_path_inclusive_range(self):
        """All commits in the [lo, hi] window are returned, newest-first."""
        mock_client = AsyncMock()
        mock_client.get_mainline_commits.return_value = {
            "flattenedVersions": [
                _make_commit_version(f"v{o}", o) for o in (74964, 74963, 74962, 74961, 74960, 74959)
            ],
            "pagination": {},
        }

        result = await fetch_mainline_commits_between(
            mock_client, project_id="sys-perf", start_order=74959, end_order=74964
        )

        self.assertEqual(result["project_id"], "sys-perf")
        self.assertEqual(result["start_order"], 74959)
        self.assertEqual(result["end_order"], 74964)
        self.assertEqual(result["count"], 6)
        orders = [c["order"] for c in result["commits"]]
        self.assertEqual(orders, [74964, 74963, 74962, 74961, 74960, 74959])
        first = result["commits"][0]
        for key in ("order", "version_id", "revision", "message", "author", "create_time"):
            self.assertIn(key, first)
        self.assertEqual(first["revision"], "revv74964")

    async def test_reversed_bounds_normalize(self):
        """start_order > end_order still produces the same ordered window."""
        mock_client = AsyncMock()
        mock_client.get_mainline_commits.return_value = {
            "flattenedVersions": [_make_commit_version(f"v{o}", o) for o in (102, 101, 100)],
            "pagination": {},
        }

        result = await fetch_mainline_commits_between(
            mock_client, project_id="proj", start_order=102, end_order=100
        )

        self.assertEqual(result["start_order"], 100)
        self.assertEqual(result["end_order"], 102)
        self.assertEqual([c["order"] for c in result["commits"]], [102, 101, 100])

    async def test_filters_to_range(self):
        """Versions outside [lo, hi] in the GraphQL feed are filtered out."""
        mock_client = AsyncMock()
        mock_client.get_mainline_commits.return_value = {
            "flattenedVersions": [_make_commit_version(f"v{o}", o) for o in (105, 103, 102, 101, 99)],
            "pagination": {},
        }

        result = await fetch_mainline_commits_between(
            mock_client, project_id="proj", start_order=101, end_order=103
        )

        self.assertEqual([c["order"] for c in result["commits"]], [103, 102, 101])

    async def test_missing_project_id(self):
        mock_client = AsyncMock()
        result = await fetch_mainline_commits_between(
            mock_client, project_id="", start_order=1, end_order=10
        )
        self.assertEqual(result["status"], "error")
        mock_client.get_mainline_commits.assert_not_awaited()

    async def test_upstream_error(self):
        mock_client = AsyncMock()
        mock_client.get_mainline_commits.side_effect = Exception("graphql exploded")
        result = await fetch_mainline_commits_between(
            mock_client, project_id="proj", start_order=1, end_order=10
        )
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["project_id"], "proj")
        self.assertIn("graphql exploded", result["error"])

    async def test_oversized_range_warning(self):
        """A span > MAX_COMMITS_RANGE clamps and emits a truncation warning."""
        mock_client = AsyncMock()
        mock_client.get_mainline_commits.return_value = {
            "flattenedVersions": [],
            "pagination": {},
        }

        result = await fetch_mainline_commits_between(
            mock_client,
            project_id="proj",
            start_order=1,
            end_order=MAX_COMMITS_RANGE + 50,
        )

        sent_options = mock_client.get_mainline_commits.await_args.args[0]
        self.assertEqual(sent_options["limit"], MAX_COMMITS_RANGE)
        self.assertIn("warnings", result)
        self.assertTrue(any("exceeds max" in w for w in result["warnings"]))

    async def test_requesters_propagation(self):
        """When requesters is provided it lands in options; when None it's omitted."""
        mock_client = AsyncMock()
        mock_client.get_mainline_commits.return_value = {
            "flattenedVersions": [],
            "pagination": {},
        }

        await fetch_mainline_commits_between(
            mock_client, project_id="proj", start_order=1, end_order=5
        )
        opts_default = mock_client.get_mainline_commits.await_args.args[0]
        self.assertNotIn("requesters", opts_default)

        await fetch_mainline_commits_between(
            mock_client,
            project_id="proj",
            start_order=1,
            end_order=5,
            requesters=["gitter_request", "trigger_request"],
        )
        opts_with = mock_client.get_mainline_commits.await_args.args[0]
        self.assertEqual(opts_with["requesters"], ["gitter_request", "trigger_request"])

    async def test_max_order_uses_hi_plus_one(self):
        """maxOrder is set to hi+1 so inclusive-or-exclusive schema both work."""
        mock_client = AsyncMock()
        mock_client.get_mainline_commits.return_value = {
            "flattenedVersions": [],
            "pagination": {},
        }

        await fetch_mainline_commits_between(
            mock_client, project_id="proj", start_order=100, end_order=110
        )

        opts = mock_client.get_mainline_commits.await_args.args[0]
        self.assertEqual(opts["maxOrder"], 111)
        self.assertEqual(opts["limit"], 11)
        self.assertEqual(opts["projectIdentifier"], "proj")

    async def test_empty_range_message(self):
        """Empty result carries an explanatory message."""
        mock_client = AsyncMock()
        mock_client.get_mainline_commits.return_value = {
            "flattenedVersions": [],
            "pagination": {},
        }

        result = await fetch_mainline_commits_between(
            mock_client, project_id="proj", start_order=1, end_order=5
        )

        self.assertEqual(result["count"], 0)
        self.assertEqual(result["commits"], [])
        self.assertIn("message", result)


if __name__ == "__main__":
    unittest.main()
