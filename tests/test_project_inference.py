"""Tests for intelligent project ID inference logic."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from evergreen_mcp.failed_jobs_tools import (
    ProjectInferenceResult,
    fetch_inferred_project_ids,
    infer_project_id_from_context,
)


class TestFetchInferredProjectIds(unittest.IsolatedAsyncioTestCase):
    """Test fetching project IDs from GraphQL."""

    async def test_fetch_projects(self):
        """Test parsing patches into unique projects."""
        mock_client = AsyncMock()
        mock_client.get_inferred_project_ids.return_value = [
            # Project A: 2 patches, latest today
            {
                "projectIdentifier": "project-a",
                "createTime": "2025-01-02T12:00:00Z",
            },
            {
                "projectIdentifier": "project-a",
                "createTime": "2025-01-01T12:00:00Z",
            },
            # Project B: 1 patch, older
            {
                "projectIdentifier": "project-b",
                "createTime": "2024-12-31T12:00:00Z",
            },
        ]

        result = await fetch_inferred_project_ids(mock_client, "user@example.com")

        self.assertEqual(result["total_projects"], 2)
        projects = result["projects"]

        # Should be sorted by patch count (desc) then time
        self.assertEqual(projects[0]["project_identifier"], "project-a")
        self.assertEqual(projects[0]["patch_count"], 2)
        self.assertEqual(projects[0]["latest_patch_time"], "2025-01-02T12:00:00Z")

        self.assertEqual(projects[1]["project_identifier"], "project-b")
        self.assertEqual(projects[1]["patch_count"], 1)


class TestInferProjectIdFromContext(unittest.IsolatedAsyncioTestCase):
    """Test the inference logic."""

    @patch("evergreen_mcp.failed_jobs_tools.fetch_inferred_project_ids")
    async def test_single_project(self, mock_fetch):
        """Test auto-selecting single project."""
        mock_fetch.return_value = {
            "projects": [
                {
                    "project_identifier": "mms",
                    "patch_count": 5,
                    "latest_patch_time": "2025-01-01",
                }
            ]
        }

        client = AsyncMock()
        result = await infer_project_id_from_context(client, "user")

        self.assertEqual(result.project_id, "mms")
        self.assertEqual(result.confidence, "high")
        self.assertEqual(result.source, "single_project")

    @patch("evergreen_mcp.failed_jobs_tools.fetch_inferred_project_ids")
    async def test_multiple_projects_most_recent(self, mock_fetch):
        """Test selecting most recent project from multiple."""
        mock_fetch.return_value = {
            "projects": [
                # Old but active project
                {
                    "project_identifier": "old-active",
                    "patch_count": 10,
                    "latest_patch_time": "2024-01-01",
                },
                # Recent project (should be picked even with fewer patches)
                {
                    "project_identifier": "new-hotness",
                    "patch_count": 2,
                    "latest_patch_time": "2025-01-01",
                },
            ]
        }

        client = AsyncMock()
        result = await infer_project_id_from_context(client, "user")

        self.assertEqual(result.project_id, "new-hotness")
        self.assertEqual(result.confidence, "medium")
        self.assertEqual(result.source, "most_recent_fallback")

        # Check for key parts of the message
        self.assertIn(
            "The patches are coming from the project_id new-hotness", result.message
        )
        self.assertIn("valid such as old-active", result.message)

    @patch("evergreen_mcp.failed_jobs_tools.fetch_inferred_project_ids")
    async def test_no_projects(self, mock_fetch):
        """Test when no projects found."""
        mock_fetch.return_value = {"projects": []}

        client = AsyncMock()
        result = await infer_project_id_from_context(client, "user")

        self.assertIsNone(result.project_id)
        self.assertEqual(result.confidence, "none")
        self.assertEqual(result.source, "user_selection_required")


if __name__ == "__main__":
    unittest.main()
