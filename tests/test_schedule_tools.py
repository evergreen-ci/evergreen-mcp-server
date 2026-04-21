"""Tests for the schedule_tools.schedule_unscheduled_tasks helper."""

import unittest
from unittest.mock import AsyncMock

from evergreen_mcp.schedule_tools import schedule_unscheduled_tasks


def _make_scheduled_task(
    task_id: str,
    *,
    display_name: str = "compile",
    build_variant: str = "ubuntu",
    status: str = "will-run",
    execution: int = 0,
    activated: bool = True,
) -> dict:
    return {
        "id": task_id,
        "displayName": display_name,
        "buildVariant": build_variant,
        "status": status,
        "execution": execution,
        "activated": activated,
    }


class TestScheduleUnscheduledTasks(unittest.IsolatedAsyncioTestCase):
    async def test_happy_path(self):
        """Two task IDs in, two scheduled — full response shape."""
        mock_client = AsyncMock()
        mock_client.schedule_tasks.return_value = [
            _make_scheduled_task("t1", display_name="compile"),
            _make_scheduled_task("t2", display_name="lint"),
        ]

        result = await schedule_unscheduled_tasks(
            mock_client, version_id="v1", task_ids=["t1", "t2"]
        )

        mock_client.schedule_tasks.assert_awaited_once_with("v1", ["t1", "t2"])
        self.assertEqual(result["version_id"], "v1")
        self.assertEqual(result["requested_task_ids"], ["t1", "t2"])
        self.assertEqual(result["scheduled_count"], 2)
        self.assertEqual(result["missing_task_ids"], [])
        self.assertNotIn("message", result)

        scheduled = result["scheduled_tasks"]
        self.assertEqual(len(scheduled), 2)
        self.assertEqual(scheduled[0]["task_id"], "t1")
        self.assertEqual(scheduled[0]["display_name"], "compile")
        self.assertEqual(scheduled[0]["build_variant"], "ubuntu")
        self.assertEqual(scheduled[0]["status"], "will-run")
        self.assertEqual(scheduled[0]["execution"], 0)
        self.assertTrue(scheduled[0]["activated"])

    async def test_partial_response_lists_missing(self):
        """3 IDs requested, 2 returned -> missing_task_ids includes the third."""
        mock_client = AsyncMock()
        mock_client.schedule_tasks.return_value = [
            _make_scheduled_task("t1"),
            _make_scheduled_task("t3"),
        ]

        result = await schedule_unscheduled_tasks(
            mock_client, version_id="v1", task_ids=["t1", "t2", "t3"]
        )

        self.assertEqual(result["scheduled_count"], 2)
        self.assertEqual(result["missing_task_ids"], ["t2"])
        self.assertIn("message", result)
        self.assertIn("TASKS:EDIT", result["message"])

    async def test_empty_task_ids_validation_error(self):
        """Empty task_ids -> error, no client call."""
        mock_client = AsyncMock()

        result = await schedule_unscheduled_tasks(
            mock_client, version_id="v1", task_ids=[]
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("task_ids", result["error"])
        mock_client.schedule_tasks.assert_not_awaited()

    async def test_empty_version_id_validation_error(self):
        """Empty version_id -> error, no client call."""
        mock_client = AsyncMock()

        result = await schedule_unscheduled_tasks(
            mock_client, version_id="", task_ids=["t1"]
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("version_id", result["error"])
        mock_client.schedule_tasks.assert_not_awaited()

    async def test_blank_only_task_ids_validation_error(self):
        """task_ids of all empty strings -> validation error after dedupe."""
        mock_client = AsyncMock()

        result = await schedule_unscheduled_tasks(
            mock_client, version_id="v1", task_ids=["", ""]
        )

        self.assertEqual(result["status"], "error")
        mock_client.schedule_tasks.assert_not_awaited()

    async def test_client_exception_returned_as_error(self):
        """Client raises -> error shape with exception text preserved."""
        mock_client = AsyncMock()
        mock_client.schedule_tasks.side_effect = RuntimeError("boom")

        result = await schedule_unscheduled_tasks(
            mock_client, version_id="v1", task_ids=["t1"]
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["version_id"], "v1")
        self.assertEqual(result["requested_task_ids"], ["t1"])
        self.assertIn("RuntimeError", result["error"])
        self.assertIn("boom", result["error"])

    async def test_duplicate_task_ids_deduped(self):
        """Duplicates in input are collapsed before the mutation call."""
        mock_client = AsyncMock()
        mock_client.schedule_tasks.return_value = [_make_scheduled_task("t1")]

        result = await schedule_unscheduled_tasks(
            mock_client, version_id="v1", task_ids=["t1", "t1", "t1"]
        )

        mock_client.schedule_tasks.assert_awaited_once_with("v1", ["t1"])
        self.assertEqual(result["requested_task_ids"], ["t1"])
        self.assertEqual(result["scheduled_count"], 1)
        self.assertEqual(result["missing_task_ids"], [])


if __name__ == "__main__":
    unittest.main()
