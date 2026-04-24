"""Tests for the restart_tools.restart_task / restart_version helpers."""

import unittest
from unittest.mock import AsyncMock

from evergreen_mcp.restart_tools import restart_task, restart_version


def _make_task(
    task_id: str,
    *,
    display_name: str = "compile",
    build_variant: str = "ubuntu",
    status: str = "will-run",
    execution: int = 1,
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


def _make_version(
    version_id: str,
    *,
    status: str = "started",
    activated: bool = True,
) -> dict:
    return {"id": version_id, "status": status, "activated": activated}


class TestRestartTask(unittest.IsolatedAsyncioTestCase):
    async def test_happy_path(self):
        mock_client = AsyncMock()
        mock_client.restart_task.return_value = _make_task("t1", execution=2)

        result = await restart_task(mock_client, task_id="t1")

        mock_client.restart_task.assert_awaited_once_with("t1", False)
        self.assertEqual(result["task_id"], "t1")
        self.assertFalse(result["failed_only"])
        self.assertEqual(result["task"]["task_id"], "t1")
        self.assertEqual(result["task"]["execution"], 2)
        self.assertEqual(result["task"]["display_name"], "compile")
        self.assertTrue(result["task"]["activated"])
        self.assertNotIn("status", result)

    async def test_failed_only_passthrough(self):
        mock_client = AsyncMock()
        mock_client.restart_task.return_value = _make_task("t1")

        result = await restart_task(
            mock_client, task_id="t1", failed_only=True
        )

        mock_client.restart_task.assert_awaited_once_with("t1", True)
        self.assertTrue(result["failed_only"])

    async def test_empty_task_id_validation_error(self):
        mock_client = AsyncMock()

        result = await restart_task(mock_client, task_id="")

        self.assertEqual(result["status"], "error")
        self.assertIn("task_id", result["error"])
        mock_client.restart_task.assert_not_awaited()

    async def test_empty_response_is_error(self):
        """Evergreen returned nothing -> surface a TASKS:EDIT hint."""
        mock_client = AsyncMock()
        mock_client.restart_task.return_value = {}

        result = await restart_task(mock_client, task_id="t1")

        self.assertEqual(result["status"], "error")
        self.assertIn("TASKS:EDIT", result["error"])

    async def test_client_exception_returned_as_error(self):
        mock_client = AsyncMock()
        mock_client.restart_task.side_effect = RuntimeError("boom")

        result = await restart_task(
            mock_client, task_id="t1", failed_only=True
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["task_id"], "t1")
        self.assertTrue(result["failed_only"])
        self.assertIn("RuntimeError", result["error"])
        self.assertIn("boom", result["error"])


class TestRestartVersion(unittest.IsolatedAsyncioTestCase):
    async def test_happy_path_all_completed(self):
        """No task_ids -> restart_versions called with empty list."""
        mock_client = AsyncMock()
        mock_client.restart_versions.return_value = [_make_version("v1")]

        result = await restart_version(mock_client, version_id="v1")

        mock_client.restart_versions.assert_awaited_once_with("v1", False, [])
        self.assertEqual(result["version_id"], "v1")
        self.assertFalse(result["abort"])
        self.assertEqual(result["requested_task_ids"], [])
        self.assertTrue(result["restarted_all_completed"])
        self.assertEqual(len(result["restarted_versions"]), 1)
        self.assertEqual(result["restarted_versions"][0]["version_id"], "v1")
        self.assertEqual(result["restarted_versions"][0]["status"], "started")

    async def test_happy_path_with_task_subset_and_abort(self):
        mock_client = AsyncMock()
        mock_client.restart_versions.return_value = [_make_version("v1")]

        result = await restart_version(
            mock_client,
            version_id="v1",
            task_ids=["t1", "t2"],
            abort=True,
        )

        mock_client.restart_versions.assert_awaited_once_with(
            "v1", True, ["t1", "t2"]
        )
        self.assertTrue(result["abort"])
        self.assertEqual(result["requested_task_ids"], ["t1", "t2"])
        self.assertFalse(result["restarted_all_completed"])

    async def test_duplicate_task_ids_deduped(self):
        mock_client = AsyncMock()
        mock_client.restart_versions.return_value = [_make_version("v1")]

        result = await restart_version(
            mock_client,
            version_id="v1",
            task_ids=["t1", "t1", "", "t2", "t1"],
        )

        mock_client.restart_versions.assert_awaited_once_with(
            "v1", False, ["t1", "t2"]
        )
        self.assertEqual(result["requested_task_ids"], ["t1", "t2"])

    async def test_empty_version_id_validation_error(self):
        mock_client = AsyncMock()

        result = await restart_version(mock_client, version_id="")

        self.assertEqual(result["status"], "error")
        self.assertIn("version_id", result["error"])
        mock_client.restart_versions.assert_not_awaited()

    async def test_client_exception_returned_as_error(self):
        mock_client = AsyncMock()
        mock_client.restart_versions.side_effect = RuntimeError("boom")

        result = await restart_version(
            mock_client, version_id="v1", abort=True, task_ids=["t1"]
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["version_id"], "v1")
        self.assertTrue(result["abort"])
        self.assertEqual(result["requested_task_ids"], ["t1"])
        self.assertIn("RuntimeError", result["error"])
        self.assertIn("boom", result["error"])


if __name__ == "__main__":
    unittest.main()
