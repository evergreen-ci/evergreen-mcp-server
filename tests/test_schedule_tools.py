"""Tests for the schedule_tools.schedule_unscheduled_tasks helper."""

import unittest
from unittest.mock import AsyncMock

from evergreen_mcp.schedule_tools import (
    _merge_variants_tasks,
    configure_patch,
    schedule_unscheduled_tasks,
)


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


class TestMergeVariantsTasks(unittest.TestCase):
    def test_adds_new_variant(self):
        existing = [{"name": "v-a", "tasks": ["t1"]}]
        additions = [{"variant": "v-b", "tasks": ["t2"]}]
        merged = _merge_variants_tasks(existing, additions)
        self.assertEqual(
            merged,
            [
                {"variant": "v-a", "tasks": ["t1"], "displayTasks": []},
                {"variant": "v-b", "tasks": ["t2"], "displayTasks": []},
            ],
        )

    def test_merges_tasks_into_existing_variant_without_dupes(self):
        existing = [{"name": "v-a", "tasks": ["t1", "t2"]}]
        additions = [{"variant": "v-a", "tasks": ["t2", "t3"]}]
        merged = _merge_variants_tasks(existing, additions)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["variant"], "v-a")
        self.assertEqual(merged[0]["tasks"], ["t1", "t2", "t3"])

    def test_empty_existing(self):
        merged = _merge_variants_tasks([], [{"variant": "v-b", "tasks": ["t2"]}])
        self.assertEqual(
            merged, [{"variant": "v-b", "tasks": ["t2"], "displayTasks": []}]
        )


class TestConfigurePatch(unittest.IsolatedAsyncioTestCase):
    async def test_happy_path_sends_union(self):
        mock_client = AsyncMock()
        mock_client.get_patch_configure.return_value = {
            "id": "p1",
            "description": "orig",
            "variantsTasks": [{"name": "v-a", "tasks": ["t1"]}],
            "parameters": [{"key": "k", "value": "v"}],
        }
        mock_client.schedule_patch.return_value = {
            "id": "p1",
            "status": "created",
            "activated": True,
            "variantsTasks": [
                {"name": "v-a", "tasks": ["t1"]},
                {"name": "v-b", "tasks": ["t2"]},
            ],
        }

        result = await configure_patch(
            mock_client,
            patch_id="p1",
            add_variants_tasks=[{"variant": "v-b", "tasks": ["t2"]}],
        )

        # The configure payload must carry the full union + preserved metadata.
        _, sent_configure = mock_client.schedule_patch.await_args.args
        self.assertEqual(sent_configure["description"], "orig")
        self.assertEqual(sent_configure["parameters"], [{"key": "k", "value": "v"}])
        self.assertEqual(
            sent_configure["variantsTasks"],
            [
                {"variant": "v-a", "tasks": ["t1"], "displayTasks": []},
                {"variant": "v-b", "tasks": ["t2"], "displayTasks": []},
            ],
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["patch_status"], "created")
        self.assertTrue(result["activated"])
        self.assertEqual(
            result["variants_tasks"],
            [
                {"variant": "v-a", "tasks": ["t1"]},
                {"variant": "v-b", "tasks": ["t2"]},
            ],
        )

    async def test_description_override(self):
        mock_client = AsyncMock()
        mock_client.get_patch_configure.return_value = {
            "description": "orig",
            "variantsTasks": [],
        }
        mock_client.schedule_patch.return_value = {"id": "p1", "variantsTasks": []}

        await configure_patch(
            mock_client,
            patch_id="p1",
            add_variants_tasks=[{"variant": "v-b", "tasks": ["t2"]}],
            description="new desc",
        )
        _, sent_configure = mock_client.schedule_patch.await_args.args
        self.assertEqual(sent_configure["description"], "new desc")
        self.assertNotIn("parameters", sent_configure)

    async def test_missing_patch_id(self):
        mock_client = AsyncMock()
        result = await configure_patch(
            mock_client, patch_id="", add_variants_tasks=[{"variant": "v", "tasks": ["t"]}]
        )
        self.assertEqual(result["status"], "error")
        mock_client.get_patch_configure.assert_not_awaited()

    async def test_empty_additions(self):
        mock_client = AsyncMock()
        result = await configure_patch(
            mock_client, patch_id="p1", add_variants_tasks=[]
        )
        self.assertEqual(result["status"], "error")
        mock_client.get_patch_configure.assert_not_awaited()

    async def test_addition_missing_tasks(self):
        mock_client = AsyncMock()
        result = await configure_patch(
            mock_client, patch_id="p1", add_variants_tasks=[{"variant": "v", "tasks": []}]
        )
        self.assertEqual(result["status"], "error")
        mock_client.get_patch_configure.assert_not_awaited()

    async def test_read_failure_returns_error(self):
        mock_client = AsyncMock()
        mock_client.get_patch_configure.side_effect = RuntimeError("boom")
        result = await configure_patch(
            mock_client,
            patch_id="p1",
            add_variants_tasks=[{"variant": "v", "tasks": ["t"]}],
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("boom", result["error"])
        mock_client.schedule_patch.assert_not_awaited()

    async def test_mutation_failure_returns_error(self):
        mock_client = AsyncMock()
        mock_client.get_patch_configure.return_value = {
            "description": "orig",
            "variantsTasks": [],
        }
        mock_client.schedule_patch.side_effect = RuntimeError("kaboom")
        result = await configure_patch(
            mock_client,
            patch_id="p1",
            add_variants_tasks=[{"variant": "v", "tasks": ["t"]}],
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("kaboom", result["error"])
        self.assertEqual(result["requested_additions"], [{"variant": "v", "tasks": ["t"]}])


if __name__ == "__main__":
    unittest.main()
