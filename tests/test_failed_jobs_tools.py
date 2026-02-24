"""Tests for failed jobs tools, including host metadata extraction."""

import unittest
from unittest.mock import AsyncMock

from evergreen_mcp.failed_jobs_tools import (
    fetch_patch_failed_jobs,
    fetch_task_logs,
    fetch_task_test_results,
)


class TestFetchPatchFailedJobs(unittest.IsolatedAsyncioTestCase):
    """Test fetching failed jobs from patches."""

    async def test_host_metadata_included_in_task_info(self):
        """Test that host metadata fields are extracted from task data."""
        mock_client = AsyncMock()
        mock_client.get_patch_failed_tasks.return_value = {
            "id": "patch123",
            "patchNumber": 1,
            "githash": "abc123",
            "description": "Test patch",
            "author": "test@example.com",
            "authorDisplayName": "Test User",
            "status": "failed",
            "createTime": "2025-01-01T12:00:00Z",
            "projectIdentifier": "test-project",
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

        result = await fetch_patch_failed_jobs(mock_client, "patch123")

        # Verify the result structure
        self.assertIn("failed_tasks", result)
        self.assertEqual(len(result["failed_tasks"]), 1)

        task = result["failed_tasks"][0]

        # Verify host metadata fields are present
        self.assertEqual(task["ami"], "ami-0522af671d366e600")
        self.assertEqual(task["host_id"], "i-0abc123def456789")
        self.assertEqual(task["distro_id"], "amazon2-cloud-large")
        self.assertEqual(task["image_id"], "ami-0522af671d366e600")

    async def test_host_metadata_handles_missing_values(self):
        """Test that missing host metadata fields are handled gracefully."""
        mock_client = AsyncMock()
        mock_client.get_patch_failed_tasks.return_value = {
            "id": "patch123",
            "patchNumber": 1,
            "githash": "abc123",
            "description": "Test patch",
            "author": "test@example.com",
            "authorDisplayName": "Test User",
            "status": "failed",
            "createTime": "2025-01-01T12:00:00Z",
            "projectIdentifier": "test-project",
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

        result = await fetch_patch_failed_jobs(mock_client, "patch123")

        task = result["failed_tasks"][0]

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
