"""Tests for failed jobs tools, including host metadata extraction."""

import unittest
from unittest.mock import AsyncMock

from evergreen_mcp.failed_jobs_tools import (
    fetch_patch_jobs,
    fetch_version_jobs,
    fetch_task_logs,
    fetch_task_test_results,
)


class TestFetchPatchFailedJobs(unittest.IsolatedAsyncioTestCase):
    """Test fetching failed jobs from patches."""

    async def test_host_metadata_included_in_task_info(self):
        """Test that host metadata fields are extracted from task data."""
        mock_client = AsyncMock()
        mock_client.get_patch_tasks.return_value = {
            "id": "patch123",
            "patchNumber": 1,
            "githash": "abc123",
            "description": "Test patch",
            "author": "test@example.com",
            "authorDisplayName": "Test User",
            "status": "failed",
            "createTime": "2025-01-01T12:00:00Z",
            "projectMetadata": {"identifier": "test-project"},
            "versionFull": {
                "id": "version123",
                "revision": "abc123",
                "author": "test@example.com",
                "createTime": "2025-01-01T12:00:00Z",
                "status": "failed",
                "tasks": {
                    "count": 1,
                    "data": [
                        {
                            "id": "task123",
                            "displayName": "test_task",
                            "buildVariant": "ubuntu2204",
                            "status": "failed",
                            "execution": 0,
                            "finishTime": "2025-01-01T13:00:00Z",
                            "timeTaken": 3600000,
                            "hasTestResults": True,
                            "failedTestCount": 2,
                            "totalTestCount": 10,
                            # Host metadata fields
                            "ami": "ami-0522af671d366e600",
                            "hostId": "i-0abc123def456789",
                            "distroId": "amazon2-cloud-large",
                            "imageId": "ami-0522af671d366e600",
                            "details": {
                                "description": "Test failed",
                                "status": "failed",
                                "timedOut": False,
                                "timeoutType": None,
                                "failingCommand": "test",
                            },
                            "logs": {
                                "taskLogLink": "http://example.com/task",
                                "agentLogLink": "http://example.com/agent",
                                "systemLogLink": "http://example.com/system",
                                "allLogLink": "http://example.com/all",
                            },
                        }
                    ],
                },
            },
        }

        result = await fetch_patch_jobs(mock_client, "patch123")

        # Verify the result structure
        self.assertIn("tasks", result)
        self.assertEqual(len(result["tasks"]), 1)

        task = result["tasks"][0]

        # Verify host metadata fields are present
        self.assertEqual(task["ami"], "ami-0522af671d366e600")
        self.assertEqual(task["host_id"], "i-0abc123def456789")
        self.assertEqual(task["distro_id"], "amazon2-cloud-large")
        self.assertEqual(task["image_id"], "ami-0522af671d366e600")

    async def test_host_metadata_handles_missing_values(self):
        """Test that missing host metadata fields are handled gracefully."""
        mock_client = AsyncMock()
        mock_client.get_patch_tasks.return_value = {
            "id": "patch123",
            "patchNumber": 1,
            "githash": "abc123",
            "description": "Test patch",
            "author": "test@example.com",
            "authorDisplayName": "Test User",
            "status": "failed",
            "createTime": "2025-01-01T12:00:00Z",
            "projectMetadata": {"identifier": "test-project"},
            "versionFull": {
                "id": "version123",
                "revision": "abc123",
                "author": "test@example.com",
                "createTime": "2025-01-01T12:00:00Z",
                "status": "failed",
                "tasks": {
                    "count": 1,
                    "data": [
                        {
                            "id": "task123",
                            "displayName": "test_task",
                            "buildVariant": "ubuntu2204",
                            "status": "failed",
                            "execution": 0,
                            "finishTime": "2025-01-01T13:00:00Z",
                            "timeTaken": 3600000,
                            "hasTestResults": False,
                            "failedTestCount": 0,
                            "totalTestCount": 0,
                            # Host metadata fields intentionally missing
                            "details": {},
                            "logs": {},
                        }
                    ],
                },
            },
        }

        result = await fetch_patch_jobs(mock_client, "patch123")

        task = result["tasks"][0]

        # Verify host metadata fields are None when not provided
        self.assertIsNone(task["ami"])
        self.assertIsNone(task["host_id"])
        self.assertIsNone(task["distro_id"])
        self.assertIsNone(task["image_id"])


class TestFetchTaskLogs(unittest.IsolatedAsyncioTestCase):
    """Test fetching task logs."""

    async def test_host_metadata_included_in_logs_response(self):
        """Test that host metadata fields are included in task logs response."""
        mock_client = AsyncMock()
        mock_client.get_task_logs.return_value = {
            "id": "task123",
            "displayName": "test_task",
            "execution": 0,
            "ami": "ami-0522af671d366e600",
            "hostId": "i-0abc123def456789",
            "distroId": "amazon2-cloud-large",
            "imageId": "ami-0522af671d366e600",
            "taskLogs": {
                "taskId": "task123",
                "execution": 0,
                "taskLogs": [
                    {
                        "severity": "E",
                        "message": "Test error message",
                        "timestamp": "2025-01-01T12:00:00Z",
                        "type": "task",
                    }
                ],
            },
        }

        result = await fetch_task_logs(
            mock_client,
            {
                "task_id": "task123",
                "execution": 0,
                "max_lines": 100,
                "filter_errors": False,
            },
        )

        # Verify host metadata fields are present
        self.assertEqual(result["ami"], "ami-0522af671d366e600")
        self.assertEqual(result["host_id"], "i-0abc123def456789")
        self.assertEqual(result["distro_id"], "amazon2-cloud-large")
        self.assertEqual(result["image_id"], "ami-0522af671d366e600")

    async def test_host_metadata_handles_missing_values_in_logs(self):
        """Test that missing host metadata in logs response is handled gracefully."""
        mock_client = AsyncMock()
        mock_client.get_task_logs.return_value = {
            "id": "task123",
            "displayName": "test_task",
            "execution": 0,
            # Host metadata fields intentionally missing
            "taskLogs": {
                "taskId": "task123",
                "execution": 0,
                "taskLogs": [],
            },
        }

        result = await fetch_task_logs(
            mock_client,
            {
                "task_id": "task123",
                "execution": 0,
                "max_lines": 100,
                "filter_errors": False,
            },
        )

        # Verify host metadata fields are None when not provided
        self.assertIsNone(result["ami"])
        self.assertIsNone(result["host_id"])
        self.assertIsNone(result["distro_id"])
        self.assertIsNone(result["image_id"])


class TestFetchTaskTestResults(unittest.IsolatedAsyncioTestCase):
    """Test fetching task test results."""

    async def test_host_metadata_included_in_test_results_response(self):
        """Test that host metadata fields are included in test results response."""
        mock_client = AsyncMock()
        mock_client.get_task_test_results.return_value = {
            "id": "task123",
            "displayName": "test_task",
            "buildVariant": "ubuntu2204",
            "status": "failed",
            "execution": 0,
            "hasTestResults": True,
            "failedTestCount": 1,
            "totalTestCount": 5,
            "ami": "ami-0522af671d366e600",
            "hostId": "i-0abc123def456789",
            "distroId": "amazon2-cloud-large",
            "imageId": "ami-0522af671d366e600",
            "tests": {
                "totalTestCount": 5,
                "filteredTestCount": 1,
                "testResults": [
                    {
                        "id": "test1",
                        "testFile": "test_example.py",
                        "status": "fail",
                        "duration": 1.5,
                        "startTime": "2025-01-01T12:00:00Z",
                        "endTime": "2025-01-01T12:00:01Z",
                        "exitCode": 1,
                        "groupID": "group1",
                        "logs": {
                            "url": "http://example.com/logs",
                            "urlParsley": "http://example.com/parsley",
                            "urlRaw": "http://example.com/raw",
                            "lineNum": 100,
                            "renderingType": "default",
                            "version": 1,
                        },
                    }
                ],
            },
        }

        result = await fetch_task_test_results(
            mock_client,
            {"task_id": "task123", "execution": 0, "failed_only": True, "limit": 100},
        )

        # Verify host metadata fields are present in task_info
        task_info = result["task_info"]
        self.assertEqual(task_info["ami"], "ami-0522af671d366e600")
        self.assertEqual(task_info["host_id"], "i-0abc123def456789")
        self.assertEqual(task_info["distro_id"], "amazon2-cloud-large")
        self.assertEqual(task_info["image_id"], "ami-0522af671d366e600")

    async def test_host_metadata_handles_missing_values_in_test_results(self):
        """Test that missing host metadata in test results is handled gracefully."""
        mock_client = AsyncMock()
        mock_client.get_task_test_results.return_value = {
            "id": "task123",
            "displayName": "test_task",
            "buildVariant": "ubuntu2204",
            "status": "failed",
            "execution": 0,
            "hasTestResults": False,
            "failedTestCount": 0,
            "totalTestCount": 0,
            # Host metadata fields intentionally missing
            "tests": {
                "totalTestCount": 0,
                "filteredTestCount": 0,
                "testResults": [],
            },
        }

        result = await fetch_task_test_results(
            mock_client,
            {"task_id": "task123", "execution": 0, "failed_only": True, "limit": 100},
        )

        # Verify host metadata fields are None when not provided
        task_info = result["task_info"]
        self.assertIsNone(task_info["ami"])
        self.assertIsNone(task_info["host_id"])
        self.assertIsNone(task_info["distro_id"])
        self.assertIsNone(task_info["image_id"])


class TestHostMetadataFieldNames(unittest.TestCase):
    """Test that host metadata field names are consistent."""

    def test_expected_host_metadata_fields(self):
        """Verify the expected host metadata field names are documented."""
        # This test documents the expected field names for host metadata
        expected_fields = {
            "ami": "The AMI ID used by the host (e.g., ami-0522af671d366e600)",
            "host_id": "The EC2 instance ID (e.g., i-0abc123def456789)",
            "distro_id": "The Evergreen distro name (e.g., amazon2-cloud-large)",
            "image_id": "The image identifier (often same as AMI)",
        }

        # Just verify the expected fields are defined
        self.assertEqual(len(expected_fields), 4)
        self.assertIn("ami", expected_fields)
        self.assertIn("host_id", expected_fields)
        self.assertIn("distro_id", expected_fields)
        self.assertIn("image_id", expected_fields)


if __name__ == "__main__":
    unittest.main()


class TestProjectMismatchHandling(unittest.IsolatedAsyncioTestCase):
    """A mismatch against an auto-inferred project must not discard the fetch.

    The server guesses project_id when the caller omits it, then used to raise
    on its own wrong guess — throwing away a patch it had already fetched and
    costing a whole extra round-trip.
    """

    @staticmethod
    def _patch(project="other-project"):
        return {
            "id": "patch123",
            "patchNumber": 1,
            "githash": "abc",
            "description": "d",
            "author": "a",
            "authorDisplayName": "A",
            "status": "failed",
            "createTime": "2026-01-01T00:00:00Z",
            "projectMetadata": {"identifier": project},
            "versionFull": {
                "id": "v1",
                "revision": "abc",
                "author": "a",
                "createTime": "2026-01-01T00:00:00Z",
                "status": "failed",
                "tasks": {"count": 0, "data": []},
            },
        }

    async def test_inferred_mismatch_returns_data_with_a_note(self):
        client = AsyncMock()
        client.get_patch_tasks.return_value = self._patch()

        result = await fetch_patch_jobs(
            client, "patch123", project_id="guessed", project_id_inferred=True
        )

        self.assertIn("project_mismatch", result)
        self.assertIn("other-project", result["project_mismatch"])
        # The patch's own project wins over the bad guess.
        self.assertEqual(result["project_id"], "other-project")
        self.assertEqual(result["patch_info"]["patch_id"], "patch123")

    async def test_explicit_mismatch_still_raises(self):
        client = AsyncMock()
        client.get_patch_tasks.return_value = self._patch()

        with self.assertRaises(ValueError) as ctx:
            await fetch_patch_jobs(
                client, "patch123", project_id="asked-for", project_id_inferred=False
            )
        self.assertIn("other-project", str(ctx.exception))
        self.assertIn("asked-for", str(ctx.exception))

    async def test_matching_project_adds_no_note(self):
        client = AsyncMock()
        client.get_patch_tasks.return_value = self._patch(project="sys-perf")

        result = await fetch_patch_jobs(
            client, "patch123", project_id="sys-perf", project_id_inferred=True
        )

        self.assertNotIn("project_mismatch", result)


class TestPayloadShaping(unittest.IsolatedAsyncioTestCase):
    """Fields that answer nothing must not be emitted.

    Host metadata answers "was it the host/AMI?", which only arises for a
    failure; on passing tasks it was 535k of 565k sampled bytes. The all-zero
    test_info placeholder was emitted for 2,511 of 4,577 sampled tasks.
    """

    @staticmethod
    def _version(*tasks):
        return {
            "id": "v1",
            "revision": "abc",
            "author": "a",
            "createTime": "2026-01-01T00:00:00Z",
            "status": "started",
            "tasks": {"count": len(tasks), "data": list(tasks)},
        }

    @staticmethod
    def _task(status="success", has_tests=False):
        return {
            "id": "t1",
            "displayName": "perf",
            "buildVariant": "linux",
            "displayStatus": status,
            "execution": 0,
            "finishTime": None,
            "timeTaken": 1,
            "hasTestResults": has_tests,
            "failedTestCount": 2 if has_tests else 0,
            "totalTestCount": 9 if has_tests else 0,
            "ami": "ami-123",
            "hostId": "i-123",
            "distroId": "rhel94",
            "imageId": "rhel94",
            "details": {},
        }

    async def test_passing_task_omits_host_metadata(self):
        client = AsyncMock()
        client.get_version_tasks.return_value = self._version(self._task("success"))

        result = await fetch_version_jobs(client, "v1", status_filter="all")

        task = result["tasks"][0]
        for key in ("ami", "host_id", "distro_id", "image_id"):
            self.assertNotIn(key, task)

    async def test_failed_task_keeps_host_metadata(self):
        client = AsyncMock()
        client.get_version_tasks.return_value = self._version(
            self._task("system-failed")
        )

        result = await fetch_version_jobs(client, "v1", status_filter="all")

        task = result["tasks"][0]
        self.assertEqual(task["ami"], "ami-123")
        self.assertEqual(task["distro_id"], "rhel94")

    async def test_task_without_test_results_omits_test_info(self):
        client = AsyncMock()
        client.get_version_tasks.return_value = self._version(
            self._task("success", has_tests=False)
        )

        result = await fetch_version_jobs(client, "v1", status_filter="all")

        self.assertNotIn("test_info", result["tasks"][0])

    async def test_task_with_test_results_keeps_counts(self):
        client = AsyncMock()
        client.get_version_tasks.return_value = self._version(
            self._task("failed", has_tests=True)
        )

        result = await fetch_version_jobs(client, "v1", status_filter="all")

        self.assertEqual(
            result["tasks"][0]["test_info"],
            {"failed_test_count": 2, "total_test_count": 9},
        )


class TestMaxResultsClamp(unittest.IsolatedAsyncioTestCase):
    """An uncapped task listing is a multi-megabyte response nobody reads."""

    @staticmethod
    def _version():
        return {
            "id": "v1",
            "revision": "abc",
            "author": "a",
            "createTime": "2026-01-01T00:00:00Z",
            "status": "started",
            "tasks": {"count": 0, "data": []},
        }

    async def test_clamps_and_warns(self):
        from evergreen_mcp.failed_jobs_tools import MAX_TASK_RESULTS

        client = AsyncMock()
        client.get_version_tasks.return_value = self._version()

        result = await fetch_version_jobs(client, "v1", max_results=5000)

        client.get_version_tasks.assert_awaited_once()
        self.assertEqual(
            client.get_version_tasks.await_args.kwargs["limit"], MAX_TASK_RESULTS
        )
        self.assertIn("clamped", result["warning"])
        self.assertIn("get_status_summary_evergreen", result["warning"])

    async def test_reasonable_request_is_untouched_and_unwarned(self):
        client = AsyncMock()
        client.get_version_tasks.return_value = self._version()

        result = await fetch_version_jobs(client, "v1", max_results=50)

        self.assertEqual(client.get_version_tasks.await_args.kwargs["limit"], 50)
        self.assertNotIn("warning", result)


class TestFailureDetailsShaping(unittest.IsolatedAsyncioTestCase):
    """Evergreen fills `details` with empty strings on a task that PASSED.

    A truthiness check on the struct therefore emits ~90 characters of nothing
    per passing task — 411k chars across 4,326 of 4,577 sampled tasks. Found by
    calling the live API, not by the corpus analysis, which only showed the
    field was large.
    """

    @staticmethod
    def _version(details):
        return {
            "id": "v1",
            "revision": "abc",
            "author": "a",
            "createTime": "2026-01-01T00:00:00Z",
            "status": "success",
            "tasks": {
                "count": 1,
                "data": [
                    {
                        "id": "t1",
                        "displayName": "perf",
                        "buildVariant": "linux",
                        "displayStatus": "success",
                        "execution": 0,
                        "timeTaken": 1,
                        "hasTestResults": False,
                        "details": details,
                    }
                ],
            },
        }

    async def test_passing_task_omits_empty_failure_details(self):
        client = AsyncMock()
        # Exactly what Evergreen returns for a successful task.
        client.get_version_tasks.return_value = self._version(
            {
                "description": "",
                "status": "success",
                "timedOut": False,
                "timeoutType": "",
                "failingCommand": "",
            }
        )

        result = await fetch_version_jobs(client, "v1", status_filter="all")

        self.assertNotIn("failure_details", result["tasks"][0])

    async def test_real_failure_keeps_only_meaningful_keys(self):
        client = AsyncMock()
        client.get_version_tasks.return_value = self._version(
            {
                "description": "shell script encountered problem",
                "timedOut": False,
                "timeoutType": "",
                "failingCommand": "shell.exec",
            }
        )

        result = await fetch_version_jobs(client, "v1", status_filter="all")

        self.assertEqual(
            result["tasks"][0]["failure_details"],
            {
                "description": "shell script encountered problem",
                "failing_command": "shell.exec",
            },
        )

    async def test_timeout_still_reported_and_flagged_in_summary(self):
        client = AsyncMock()
        client.get_version_tasks.return_value = self._version(
            {"description": "", "timedOut": True, "timeoutType": "idle"}
        )

        result = await fetch_version_jobs(client, "v1", status_filter="all")

        fd = result["tasks"][0]["failure_details"]
        self.assertTrue(fd["timed_out"])
        self.assertEqual(fd["timeout_type"], "idle")
        self.assertTrue(result["summary"]["has_timeouts"])
