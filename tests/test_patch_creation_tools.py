"""Tests for patch_creation_tools — create/finalize/cancel/get/module/list helpers."""

import base64
import unittest
from unittest.mock import AsyncMock

from evergreen_mcp.patch_creation_tools import (
    cancel_patch,
    create_patch,
    finalize_patch,
    get_patch,
    list_project_patch_aliases,
    list_project_tasks,
    remove_patch_module,
    set_patch_module,
)


class TestCreatePatchValidation(unittest.IsolatedAsyncioTestCase):
    async def test_missing_project_id(self):
        client = AsyncMock()
        result = await create_patch(
            client,
            project_id="",
            diff="diff",
            base_revision="abc",
            variants=["v"],
            tasks=["t"],
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("project_id", result["error"])
        client.create_patch.assert_not_awaited()

    async def test_empty_diff_allowed_for_module_only_patches(self):
        """Empty string diff is valid — module-only patches have no server changes."""
        client = AsyncMock()
        client.create_patch.return_value = {
            "patch": {
                "Id": "p7",
                "variants_tasks": [{"variant": "v", "tasks": ["t"]}],
            }
        }
        result = await create_patch(
            client,
            project_id="p",
            diff="",
            base_revision="abc",
            variants=["v"],
            tasks=["t"],
        )
        self.assertEqual(result["status"], "ok")
        client.create_patch.assert_awaited_once()

    async def test_missing_base_revision(self):
        client = AsyncMock()
        result = await create_patch(
            client,
            project_id="p",
            diff="d",
            base_revision="",
            variants=["v"],
            tasks=["t"],
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("base_revision", result["error"])

    async def test_no_selection_mode_rejected(self):
        client = AsyncMock()
        result = await create_patch(
            client,
            project_id="p",
            diff="d",
            base_revision="abc",
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("No tasks selected", result["error"])
        client.create_patch.assert_not_awaited()

    async def test_alias_only_is_valid_selection(self):
        client = AsyncMock()
        client.create_patch.return_value = {"patch": {"Id": "patch123"}}

        result = await create_patch(
            client,
            project_id="p",
            diff="d",
            base_revision="abc",
            alias="some-alias",
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["patch_id"], "patch123")
        client.create_patch.assert_awaited_once()

    async def test_repeat_failed_only_is_valid_selection(self):
        client = AsyncMock()
        client.create_patch.return_value = {"patch": {"Id": "patch456"}}

        result = await create_patch(
            client,
            project_id="p",
            diff="d",
            base_revision="abc",
            repeat_failed=True,
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["patch_id"], "patch456")

    async def test_only_variants_no_tasks_rejected(self):
        client = AsyncMock()
        result = await create_patch(
            client,
            project_id="p",
            diff="d",
            base_revision="abc",
            variants=["v"],
        )
        self.assertEqual(result["status"], "error")


class TestCreatePatchHappyPath(unittest.IsolatedAsyncioTestCase):
    async def test_payload_shape_and_response(self):
        client = AsyncMock()
        client.create_patch.return_value = {
            "patch": {
                "Id": "p1",
                "Description": "fix",
                "Project": "evergreen",
                "Status": "created",
                "Activated": False,
                "variants_tasks": [
                    {"variant": "ubuntu", "tasks": ["compile", "test"]},
                ],
            }
        }

        result = await create_patch(
            client,
            project_id="evergreen",
            diff="my diff content",
            base_revision="deadbeef",
            description="fix bug",
            variants=["ubuntu", "windows"],
            tasks=["compile", "test"],
            parameters=[{"key": "k", "value": "v"}],
            git_metadata={"author": "alice", "email": "a@b.com"},
            finalize=False,
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["patch_id"], "p1")
        self.assertFalse(result["finalized"])
        self.assertIn("spruce.mongodb.com/version/p1", result["url"])
        self.assertNotIn("warning", result)
        self.assertEqual(result["patch"]["variants_tasks"][0]["variant"], "ubuntu")

        # Inspect the payload that was sent.
        client.create_patch.assert_awaited_once()
        payload = client.create_patch.await_args.args[0]
        self.assertEqual(payload["project"], "evergreen")
        self.assertEqual(payload["githash"], "deadbeef")
        self.assertEqual(payload["desc"], "fix bug")
        self.assertEqual(payload["buildvariants_new"], ["ubuntu", "windows"])
        self.assertEqual(payload["tasks"], ["compile", "test"])
        self.assertEqual(payload["parameters"], [{"key": "k", "value": "v"}])
        self.assertEqual(payload["git_metadata"]["username"], "alice")
        self.assertFalse(payload["finalize"])
        # Diff should be base64-encoded.
        self.assertEqual(
            base64.b64decode(payload["patch_bytes"]).decode("utf-8"),
            "my diff content",
        )

    async def test_finalize_true_propagates(self):
        client = AsyncMock()
        client.create_patch.return_value = {"patch": {"Id": "p2"}}

        result = await create_patch(
            client,
            project_id="p",
            diff="d",
            base_revision="abc",
            alias="a",
            finalize=True,
        )

        self.assertTrue(result["finalized"])
        payload = client.create_patch.await_args.args[0]
        self.assertTrue(payload["finalize"])

    async def test_empty_variants_tasks_triggers_warning(self):
        """When Evergreen returns no variants_tasks for an explicit selection,
        we surface a warning so the agent knows to use local_module_include_paths."""
        client = AsyncMock()
        # Simulate the server accepting the patch but resolving 0 tasks because
        # the requested tasks aren't in the variant's unpatched config.
        client.create_patch.return_value = {
            "patch": {"Id": "p5", "variants_tasks": []}
        }

        result = await create_patch(
            client,
            project_id="sys-perf",
            diff="d",
            base_revision="abc",
            variants=["perf-atlas-gen2.arm.aws"],
            tasks=["find_one_and_update_locust"],
        )

        self.assertEqual(result["status"], "ok")
        self.assertIn("warning", result)
        self.assertIn("local_module_include_paths", result["warning"])
        self.assertIn("Do NOT call finalize", result["warning"])

    async def test_no_warning_when_alias_used(self):
        """Alias-only patches have no explicit task selection — no warning needed."""
        client = AsyncMock()
        client.create_patch.return_value = {"patch": {"Id": "p6", "variants_tasks": []}}

        result = await create_patch(
            client,
            project_id="sys-perf",
            diff="d",
            base_revision="abc",
            alias="some-alias",
        )

        self.assertEqual(result["status"], "ok")
        self.assertNotIn("warning", result)

    async def test_local_module_includes_default_empty(self):
        client = AsyncMock()
        client.create_patch.return_value = {"patch": {"Id": "p3"}}

        await create_patch(
            client,
            project_id="p",
            diff="d",
            base_revision="abc",
            alias="a",
        )

        payload = client.create_patch.await_args.args[0]
        self.assertEqual(payload["local_module_includes"], [])

    async def test_local_module_includes_base64_encoded(self):
        client = AsyncMock()
        client.create_patch.return_value = {"patch": {"Id": "p4"}}

        await create_patch(
            client,
            project_id="sys-perf",
            diff="d",
            base_revision="abc",
            variants=["v"],
            tasks=["t"],
            local_module_includes=[
                {
                    "module": "dsi",
                    "filename": "configurations/some.yml",
                    "file_content": "tasks:\n- name: foo\n",
                },
                # Accept aliased keys.
                {
                    "module": "dsi",
                    "file_name": "other.yml",
                    "content": "x: 1",
                },
            ],
        )

        payload = client.create_patch.await_args.args[0]
        includes = payload["local_module_includes"]
        self.assertEqual(len(includes), 2)
        self.assertEqual(includes[0]["module"], "dsi")
        self.assertEqual(includes[0]["filename"], "configurations/some.yml")
        self.assertEqual(
            base64.b64decode(includes[0]["file_content"]).decode("utf-8"),
            "tasks:\n- name: foo\n",
        )
        self.assertEqual(includes[1]["filename"], "other.yml")
        self.assertEqual(
            base64.b64decode(includes[1]["file_content"]).decode("utf-8"), "x: 1"
        )

    async def test_api_error_path(self):
        client = AsyncMock()
        client.create_patch.side_effect = RuntimeError("server boom")

        result = await create_patch(
            client,
            project_id="p",
            diff="d",
            base_revision="abc",
            alias="a",
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("server boom", result["error"])


class TestFinalizeAndCancelPatch(unittest.IsolatedAsyncioTestCase):
    async def test_finalize_happy_path(self):
        client = AsyncMock()
        result = await finalize_patch(client, patch_id="p1")
        client.modify_patch.assert_awaited_once_with("p1", "finalize")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["action"], "finalize")

    async def test_finalize_missing_id(self):
        client = AsyncMock()
        result = await finalize_patch(client, patch_id="")
        self.assertEqual(result["status"], "error")
        client.modify_patch.assert_not_awaited()

    async def test_cancel_happy_path(self):
        client = AsyncMock()
        result = await cancel_patch(client, patch_id="p1")
        client.modify_patch.assert_awaited_once_with("p1", "cancel")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["action"], "cancel")

    async def test_cancel_api_error(self):
        client = AsyncMock()
        client.modify_patch.side_effect = RuntimeError("nope")
        result = await cancel_patch(client, patch_id="p1")
        self.assertEqual(result["status"], "error")
        self.assertIn("nope", result["error"])


class TestGetPatch(unittest.IsolatedAsyncioTestCase):
    async def test_happy_path(self):
        client = AsyncMock()
        client.get_patch.return_value = {"patch_id": "p1", "status": "started"}
        result = await get_patch(client, patch_id="p1")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["patch"]["status"], "started")

    async def test_missing_id(self):
        client = AsyncMock()
        result = await get_patch(client, patch_id="")
        self.assertEqual(result["status"], "error")
        client.get_patch.assert_not_awaited()


class TestPatchModuleTools(unittest.IsolatedAsyncioTestCase):
    async def test_set_happy_path(self):
        client = AsyncMock()
        result = await set_patch_module(
            client,
            patch_id="p1",
            module_name="mod",
            diff="m diff",
            base_revision="bb",
            message="hi",
        )
        self.assertEqual(result["status"], "ok")
        client.set_patch_module.assert_awaited_once_with(
            patch_id="p1",
            module="mod",
            diff="m diff",
            base_revision="bb",
            message="hi",
        )

    async def test_set_validation_errors(self):
        client = AsyncMock()
        for kwargs in [
            {"patch_id": "", "module_name": "m", "diff": "d", "base_revision": "b"},
            {"patch_id": "p", "module_name": "", "diff": "d", "base_revision": "b"},
            {"patch_id": "p", "module_name": "m", "diff": "", "base_revision": "b"},
            {"patch_id": "p", "module_name": "m", "diff": "d", "base_revision": ""},
        ]:
            result = await set_patch_module(client, **kwargs)
            self.assertEqual(result["status"], "error", kwargs)
        client.set_patch_module.assert_not_awaited()

    async def test_set_local_module_includes_rejects_diff_content(self):
        """Passing a git diff as file_content should raise a clear ValueError."""
        from evergreen_mcp.patch_creation_tools import _encode_local_module_includes

        with self.assertRaises(ValueError) as cm:
            _encode_local_module_includes(
                [
                    {
                        "module": "dsi",
                        "filename": "evergreen/system_perf/master/variants.yml",
                        "file_content": "diff --git a/variants.yml b/variants.yml\n...",
                    }
                ]
            )
        self.assertIn("git diff", str(cm.exception))
        self.assertIn("full raw file content", str(cm.exception))

    async def test_remove_happy_path(self):
        client = AsyncMock()
        result = await remove_patch_module(client, patch_id="p1", module_name="mod")
        self.assertEqual(result["status"], "ok")
        client.remove_patch_module.assert_awaited_once_with("p1", "mod")

    async def test_remove_validation(self):
        client = AsyncMock()
        result = await remove_patch_module(client, patch_id="", module_name="m")
        self.assertEqual(result["status"], "error")
        result = await remove_patch_module(client, patch_id="p", module_name="")
        self.assertEqual(result["status"], "error")


class TestListProjectPatchAliases(unittest.IsolatedAsyncioTestCase):
    async def test_happy_path(self):
        client = AsyncMock()
        client.list_project_patch_aliases.return_value = [
            {"alias": "a1", "description": "d1", "variant": ".*", "task": "compile"}
        ]
        result = await list_project_patch_aliases(client, project_id="p")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["aliases"][0]["alias"], "a1")
        self.assertEqual(result["aliases"][0]["task"], "compile")

    async def test_missing_project(self):
        client = AsyncMock()
        result = await list_project_patch_aliases(client, project_id="")
        self.assertEqual(result["status"], "error")


class TestListProjectTasks(unittest.IsolatedAsyncioTestCase):
    async def test_extracts_unique_task_names_from_waterfall(self):
        client = AsyncMock()
        client.get_waterfall.return_value = {
            "flattenedVersions": [
                {
                    "waterfallBuilds": [
                        {
                            "buildVariant": "ubuntu",
                            "tasks": [
                                {"displayName": "compile"},
                                {"displayName": "test"},
                            ],
                        },
                        {
                            "buildVariant": "windows",
                            "tasks": [
                                {"displayName": "compile"},  # dupe across variants
                                {"displayName": "lint"},
                            ],
                        },
                    ]
                }
            ]
        }

        result = await list_project_tasks(client, project_id="p")

        self.assertEqual(result["count"], 3)
        names = [t["display_name"] for t in result["tasks"]]
        self.assertEqual(names, sorted(names))  # sorted alphabetically
        self.assertEqual(set(names), {"compile", "test", "lint"})

    async def test_empty_waterfall(self):
        client = AsyncMock()
        client.get_waterfall.return_value = {"flattenedVersions": []}
        result = await list_project_tasks(client, project_id="p")
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["tasks"], [])

    async def test_missing_project(self):
        client = AsyncMock()
        result = await list_project_tasks(client, project_id="")
        self.assertEqual(result["status"], "error")
        client.get_waterfall.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
