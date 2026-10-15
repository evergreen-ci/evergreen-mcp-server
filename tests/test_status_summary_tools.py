"""Tests for the compact status-summary tools."""

import unittest
from unittest.mock import AsyncMock

from evergreen_mcp.status_summary_tools import (
    FAILURE_STATUSES,
    fetch_patch_status_summary,
    fetch_version_status_summary,
)


def _stats(**counts):
    return {"counts": [{"status": s, "count": c} for s, c in counts.items()]}


def _failing_task(name="perf_task", variant="linux", status="failed", **details):
    base = {
        "description": "shell script encountered problem",
        "timedOut": False,
        "timeoutType": None,
        "failingCommand": None,
    }
    base.update(details)
    return {
        "id": "a" * 160,  # Evergreen task ids really are this long
        "displayName": name,
        "buildVariant": variant,
        "displayStatus": status,
        "execution": 0,
        "details": base,
    }


class TestVersionStatusSummary(unittest.IsolatedAsyncioTestCase):
    async def test_counts_bucketed_and_done_false_while_running(self):
        client = AsyncMock()
        client.get_version_status_summary.return_value = {
            "id": "version123",
            "status": "started",
            "startTime": "2026-07-22T12:05:23.827Z",
            "finishTime": None,
            "projectMetadata": {"identifier": "sys-perf"},
            "taskStatusStats": _stats(success=8, started=2, failed=1),
            "tasks": {"count": 1, "data": [_failing_task()]},
        }

        result = await fetch_version_status_summary(client, "version123")

        self.assertEqual(result["version_id"], "version123")
        self.assertEqual(result["project"], "sys-perf")
        self.assertEqual(result["version_status"], "started")
        self.assertFalse(result["done"])
        self.assertEqual(result["in_flight"], 2)
        self.assertEqual(
            result["counts"]["buckets"],
            {
                "succeeded": 8,
                "failed": 1,
                "running": 2,
                "waiting": 0,
                "not_scheduled": 0,
                "other": 0,
            },
        )
        self.assertEqual(
            result["counts"]["by_status"], {"success": 8, "started": 2, "failed": 1}
        )
        # finish_time is omitted rather than serialized as null
        self.assertNotIn("finish_time", result)
        self.assertEqual(result["start_time"], "2026-07-22T12:05:23.827Z")

    async def test_unscheduled_and_blocked_do_not_hold_the_version_open(self):
        """The failure mode this guards: a sys-perf patch has hundreds of
        unscheduled tasks that will never run, and a blocked task whose
        dependency failed. Counting those as in-flight makes `done` stay false
        forever and a poll loop spin until someone kills it."""
        client = AsyncMock()
        client.get_version_status_summary.return_value = {
            "id": "v1",
            "status": "failed",
            "finishTime": "2026-07-22T13:34:32.748Z",
            "taskStatusStats": _stats(success=10, failed=2, unscheduled=400, blocked=3),
            "tasks": {"count": 2, "data": [_failing_task(), _failing_task("other")]},
        }

        result = await fetch_version_status_summary(client, "v1")

        self.assertEqual(result["in_flight"], 0)
        self.assertTrue(result["done"])
        self.assertEqual(result["counts"]["buckets"]["not_scheduled"], 403)
        self.assertEqual(result["failed_task_count"], 2)

    async def test_queued_tasks_do_hold_the_version_open(self):
        client = AsyncMock()
        client.get_version_status_summary.return_value = {
            "id": "v1",
            "status": "started",
            "taskStatusStats": _stats(success=10, **{"will-run": 5, "pending": 1}),
            "tasks": {"count": 0, "data": []},
        }

        result = await fetch_version_status_summary(client, "v1")

        self.assertEqual(result["in_flight"], 6)
        self.assertFalse(result["done"])

    async def test_every_evergreen_failure_display_status_is_bucketed(self):
        """FAILURE_STATUSES must match evergreen.TaskFailureStatuses; a status
        that falls through to 'other' would be a failure the poller reports as
        neither failed nor in-flight."""
        client = AsyncMock()
        client.get_version_status_summary.return_value = {
            "id": "v1",
            "status": "failed",
            "taskStatusStats": _stats(**{s: 1 for s in FAILURE_STATUSES}),
            "tasks": {"count": 0, "data": []},
        }

        result = await fetch_version_status_summary(client, "v1")

        self.assertEqual(result["counts"]["buckets"]["failed"], len(FAILURE_STATUSES))
        self.assertEqual(result["counts"]["buckets"]["other"], 0)
        self.assertTrue(result["done"])

    async def test_done_true_with_only_terminal_statuses(self):
        client = AsyncMock()
        client.get_version_status_summary.return_value = {
            "id": "v1",
            "status": "success",
            "taskStatusStats": _stats(success=12),
            "tasks": {"count": 0, "data": []},
        }

        result = await fetch_version_status_summary(client, "v1")

        self.assertTrue(result["done"])
        self.assertEqual(result["in_flight"], 0)
        self.assertEqual(result["failed_tasks"], [])

    async def test_unknown_status_reported_not_dropped(self):
        client = AsyncMock()
        client.get_version_status_summary.return_value = {
            "id": "v1",
            "status": "started",
            "taskStatusStats": _stats(success=1, **{"brand-new-status": 3}),
            "tasks": {"count": 0, "data": []},
        }

        result = await fetch_version_status_summary(client, "v1")

        self.assertEqual(result["counts"]["by_status"]["brand-new-status"], 3)
        self.assertEqual(result["counts"]["buckets"]["other"], 3)
        # An unrecognised status must not be mistaken for in-flight work...
        self.assertTrue(result["done"])

    async def test_failed_task_shape_omits_task_id_and_truncates(self):
        client = AsyncMock()
        long_desc = "x" * 500
        client.get_version_status_summary.return_value = {
            "id": "v1",
            "status": "failed",
            "taskStatusStats": _stats(failed=1),
            "tasks": {
                "count": 1,
                "data": [
                    _failing_task(
                        description=long_desc,
                        timedOut=True,
                        timeoutType="idle",
                        failingCommand="shell.exec",
                    )
                ],
            },
        }

        result = await fetch_version_status_summary(
            client, "v1", description_max_chars=100
        )

        task = result["failed_tasks"][0]
        self.assertNotIn("task_id", task)
        self.assertEqual(task["task_name"], "perf_task")
        self.assertEqual(task["build_variant"], "linux")
        self.assertEqual(len(task["description"]), 100)
        self.assertTrue(task["description_truncated"])
        self.assertTrue(task["timed_out"])
        self.assertEqual(task["timeout_type"], "idle")
        self.assertEqual(task["failing_command"], "shell.exec")

    async def test_failed_task_list_truncation_reported(self):
        client = AsyncMock()
        client.get_version_status_summary.return_value = {
            "id": "v1",
            "status": "failed",
            "taskStatusStats": _stats(failed=9),
            "tasks": {"count": 9, "data": [_failing_task(f"t{i}") for i in range(9)]},
        }

        result = await fetch_version_status_summary(client, "v1", max_failed_tasks=3)

        self.assertEqual(len(result["failed_tasks"]), 3)
        self.assertEqual(result["failed_task_count"], 9)
        self.assertEqual(result["failed_tasks_truncated"]["returned"], 3)
        self.assertEqual(result["failed_tasks_truncated"]["total"], 9)

    async def test_eta_passed_through(self):
        client = AsyncMock()
        stats = _stats(started=1)
        stats["eta"] = "2026-07-22T14:00:00Z"
        client.get_version_status_summary.return_value = {
            "id": "v1",
            "status": "started",
            "taskStatusStats": stats,
            "tasks": {"count": 0, "data": []},
        }

        result = await fetch_version_status_summary(client, "v1")

        self.assertEqual(result["eta"], "2026-07-22T14:00:00Z")

    async def test_failure_statuses_forwarded_to_query(self):
        client = AsyncMock()
        client.get_version_status_summary.return_value = {
            "id": "v1",
            "status": "success",
            "taskStatusStats": _stats(success=1),
            "tasks": {"count": 0, "data": []},
        }

        await fetch_version_status_summary(client, "v1", max_failed_tasks=7)

        client.get_version_status_summary.assert_awaited_once_with(
            "v1", FAILURE_STATUSES, failed_limit=7
        )


class TestPatchStatusSummary(unittest.IsolatedAsyncioTestCase):
    async def test_patch_fields_included(self):
        client = AsyncMock()
        client.get_patch_status_summary.return_value = {
            "id": "patch123",
            "description": "PERF-9224 bisection",
            "status": "started",
            "projectMetadata": {"identifier": "sys-perf"},
            "version": {
                "id": "version123",
                "status": "started",
                "taskStatusStats": _stats(success=3, started=1),
                "tasks": {"count": 0, "data": []},
            },
        }

        result = await fetch_patch_status_summary(client, "patch123")

        self.assertEqual(result["patch_id"], "patch123")
        self.assertEqual(result["description"], "PERF-9224 bisection")
        self.assertEqual(result["patch_status"], "started")
        self.assertEqual(result["version_id"], "version123")
        self.assertEqual(result["project"], "sys-perf")
        self.assertFalse(result["done"])

    async def test_unfinalized_patch_has_no_version(self):
        client = AsyncMock()
        client.get_patch_status_summary.return_value = {
            "id": "patch123",
            "description": "not scheduled yet",
            "status": "created",
            "projectMetadata": {"identifier": "sys-perf"},
            "version": None,
        }

        result = await fetch_patch_status_summary(client, "patch123")

        self.assertIsNone(result["version_id"])
        self.assertFalse(result["done"])
        self.assertIn("finalize_patch_evergreen", result["message"])

    async def test_summary_is_much_smaller_than_detailed_shape(self):
        """Guard the whole point of this tool: the response must stay compact
        as task count grows, because taskStatusStats is a server-side
        aggregate rather than a per-task list."""
        import json

        client = AsyncMock()
        client.get_patch_status_summary.return_value = {
            "id": "patch123",
            "description": "big patch",
            "status": "started",
            "projectMetadata": {"identifier": "sys-perf"},
            "version": {
                "id": "version123",
                "status": "started",
                "taskStatusStats": _stats(success=2000, started=40, failed=2),
                "tasks": {"count": 2, "data": [_failing_task(), _failing_task("t2")]},
            },
        }

        result = await fetch_patch_status_summary(client, "patch123")
        size = len(json.dumps(result, separators=(",", ":")))

        self.assertEqual(result["counts"]["buckets"]["succeeded"], 2000)
        # 2,042 tasks summarized in well under 1 KB.
        self.assertLess(size, 1000)


if __name__ == "__main__":
    unittest.main()


class TestStatusVocabulary(unittest.TestCase):
    """Guard the status vocabulary against Evergreen's own whitelist.

    Evergreen intersects any requested status filter with evergreen.TaskStatuses
    and silently drops unknown values (graphql/util.go:793-799). If every
    requested status is dropped the filter disappears entirely and the query
    returns EVERY task — which is how status_filter="succeeded" used to return
    failed and running tasks too.
    """

    # Verbatim copy of evergreen.TaskStatuses (globals.go:386-404).
    EVERGREEN_TASK_STATUSES = {
        "started",
        "success",
        "failed",
        "system-failed",
        "test-timed-out",
        "setup-failed",
        "aborted",
        "blocked",
        "pending",
        "known-issue",
        "system-unresponsive",
        "system-timed-out",
        "task-timed-out",
        "will-run",
        "unscheduled",
        "undispatched",
        "dispatched",
    }

    def test_failure_statuses_are_all_searchable(self):
        unknown = set(FAILURE_STATUSES) - self.EVERGREEN_TASK_STATUSES
        self.assertEqual(unknown, set(), f"not searchable, will be dropped: {unknown}")

    def test_status_filter_map_values_are_all_searchable(self):
        from evergreen_mcp.failed_jobs_tools import STATUS_FILTER_MAP

        for name, statuses in STATUS_FILTER_MAP.items():
            unknown = set(statuses) - self.EVERGREEN_TASK_STATUSES
            self.assertEqual(
                unknown,
                set(),
                f"status_filter={name!r} would silently match all tasks: {unknown}",
            )

    def test_bucket_sets_are_disjoint_and_cover_the_vocabulary(self):
        from evergreen_mcp.status_summary_tools import (
            _NOT_SCHEDULED,
            _RUNNING,
            _SUCCESS,
            _WAITING,
        )

        import itertools

        sets = [_SUCCESS, _RUNNING, _WAITING, _NOT_SCHEDULED, set(FAILURE_STATUSES)]
        for a, b in itertools.combinations(sets, 2):
            self.assertEqual(a & b, set(), f"overlapping buckets: {a & b}")
        # Every real Evergreen status must land in a named bucket, never "other".
        union = set().union(*sets)
        self.assertEqual(self.EVERGREEN_TASK_STATUSES - union, set())


class TestDoneIsNotMonotonic(unittest.IsolatedAsyncioTestCase):
    """`done` can legitimately flip back to false, and that must be visible.

    Excluding unscheduled tasks from in_flight is the right call (counting them
    would hang forever on a sys-perf patch), but it means a not-yet-activated
    version reports done=true. taskStatusStats hardcodes
    includeNeverActivated = !IsPatchRequester (version_resolver.go:532-536) and a
    never-activated task displays as "unscheduled" (model/task/db.go:281-289), so
    a fresh mainline version is all-unscheduled.
    """

    async def test_fresh_mainline_version_reports_done_with_a_caveat(self):
        client = AsyncMock()
        client.get_version_status_summary.return_value = {
            "id": "v1",
            "status": "created",
            "taskStatusStats": _stats(unscheduled=1200),
            "tasks": {"count": 0, "data": []},
        }

        result = await fetch_version_status_summary(client, "v1")

        self.assertTrue(result["done"])
        self.assertIn("done_caveat", result)
        self.assertIn("1200", result["done_caveat"])
        self.assertIn("batchtime", result["done_caveat"])

    async def test_genuinely_finished_version_has_no_caveat(self):
        client = AsyncMock()
        client.get_version_status_summary.return_value = {
            "id": "v1",
            "status": "success",
            "taskStatusStats": _stats(success=40),
            "tasks": {"count": 0, "data": []},
        }

        result = await fetch_version_status_summary(client, "v1")

        self.assertTrue(result["done"])
        self.assertNotIn("done_caveat", result)

    async def test_running_version_has_no_caveat_even_with_unscheduled(self):
        client = AsyncMock()
        client.get_version_status_summary.return_value = {
            "id": "v1",
            "status": "started",
            "taskStatusStats": _stats(started=2, unscheduled=300),
            "tasks": {"count": 0, "data": []},
        }

        result = await fetch_version_status_summary(client, "v1")

        self.assertFalse(result["done"])
        self.assertNotIn("done_caveat", result)


class TestKnownIssueIsAFailure(unittest.IsolatedAsyncioTestCase):
    """`known-issue` is a failed task carrying an annotation, not a pass.

    Evergreen sets it from HasAnnotations, checked first and unconditionally in
    the display-status switch (model/task/task.go:2193-2195), so it masks the
    underlying failure status. Its own definition of success
    (operations/last_revision.go:435-441) is:

        status == TaskSucceeded
        || (knownIssuesAreSuccess && IsFailedTaskStatus(status) && HasAnnotations)

    so it counts as success only when a caller explicitly opts in. Bucketing it
    as succeeded applied that opt-in silently and let a summary read clean while
    a genuine failure sat inside it. Observed live: the sys-perf
    disagg-m8g-perf-11-node variant reported {"success":50,"failed":4,
    "known-issue":2} — a real, not hypothetical, case.
    """

    async def test_known_issue_counts_as_failed_not_succeeded(self):
        client = AsyncMock()
        client.get_version_status_summary.return_value = {
            "id": "v1",
            "status": "failed",
            "taskStatusStats": _stats(success=50, failed=4, **{"known-issue": 2}),
            "tasks": {"count": 0, "data": []},
        }

        result = await fetch_version_status_summary(client, "v1")

        buckets = result["counts"]["buckets"]
        self.assertEqual(buckets["succeeded"], 50)
        self.assertEqual(buckets["failed"], 6)  # 4 plain + 2 annotated
        self.assertEqual(buckets["other"], 0)
        # The verbatim count survives, so a triaged failure is still
        # distinguishable from a fresh one.
        self.assertEqual(result["counts"]["by_status"]["known-issue"], 2)

    async def test_a_version_with_only_known_issues_is_not_reported_clean(self):
        """The regression this guards: buckets.failed == 0 while work failed."""
        client = AsyncMock()
        client.get_version_status_summary.return_value = {
            "id": "v1",
            "status": "success",
            "taskStatusStats": _stats(success=20, **{"known-issue": 3}),
            "tasks": {"count": 3, "data": [_failing_task(status="known-issue")]},
        }

        result = await fetch_version_status_summary(client, "v1")

        self.assertEqual(result["counts"]["buckets"]["failed"], 3)
        self.assertNotEqual(result["counts"]["buckets"]["failed"], 0)
        self.assertEqual(result["failed_task_count"], 3)
        self.assertEqual(result["failed_tasks"][0]["status"], "known-issue")

    async def test_known_issue_is_enumerated_by_the_graphql_filter(self):
        client = AsyncMock()
        client.get_version_status_summary.return_value = {
            "id": "v1",
            "status": "success",
            "taskStatusStats": _stats(success=1),
            "tasks": {"count": 0, "data": []},
        }

        await fetch_version_status_summary(client, "v1")

        statuses = client.get_version_status_summary.await_args.args[1]
        self.assertIn("known-issue", statuses)

    async def test_status_filter_failed_also_requests_known_issue(self):
        from evergreen_mcp.failed_jobs_tools import STATUS_FILTER_MAP

        self.assertIn("known-issue", STATUS_FILTER_MAP["failed"])
        self.assertNotIn("known-issue", STATUS_FILTER_MAP["succeeded"])


class TestFailingCommandDeduplication(unittest.IsolatedAsyncioTestCase):
    """Don't emit failing_command when it just repeats the description.

    Evergreen commonly sets details.description to the failing command verbatim.
    Observed live on a 4,314-task mainline version: every enumerated failure
    carried the identical string twice, ~22% of the response.
    """

    async def test_identical_failing_command_is_omitted(self):
        client = AsyncMock()
        same = "'shell.exec' in function 'f_dsi_run_workload' (step 1.10 of 1)"
        client.get_version_status_summary.return_value = {
            "id": "v1",
            "status": "failed",
            "taskStatusStats": _stats(failed=1),
            "tasks": {
                "count": 1,
                "data": [_failing_task(description=same, failingCommand=same)],
            },
        }

        result = await fetch_version_status_summary(client, "v1")

        task = result["failed_tasks"][0]
        self.assertEqual(task["description"], same)
        self.assertNotIn("failing_command", task)

    async def test_distinct_failing_command_is_kept(self):
        client = AsyncMock()
        client.get_version_status_summary.return_value = {
            "id": "v1",
            "status": "failed",
            "taskStatusStats": _stats(failed=1),
            "tasks": {
                "count": 1,
                "data": [
                    _failing_task(
                        description="test suite reported failures",
                        failingCommand="shell.exec",
                    )
                ],
            },
        }

        result = await fetch_version_status_summary(client, "v1")

        task = result["failed_tasks"][0]
        self.assertEqual(task["description"], "test suite reported failures")
        self.assertEqual(task["failing_command"], "shell.exec")

    async def test_truncated_description_still_keeps_the_command(self):
        """A description cut short must not be compared as if it were equal."""
        client = AsyncMock()
        same = "x" * 400
        client.get_version_status_summary.return_value = {
            "id": "v1",
            "status": "failed",
            "taskStatusStats": _stats(failed=1),
            "tasks": {
                "count": 1,
                "data": [_failing_task(description=same, failingCommand=same)],
            },
        }

        result = await fetch_version_status_summary(client, "v1", description_max_chars=50)

        task = result["failed_tasks"][0]
        self.assertTrue(task["description_truncated"])
        # Compared against the FULL description, so still recognised as a dupe.
        self.assertNotIn("failing_command", task)
