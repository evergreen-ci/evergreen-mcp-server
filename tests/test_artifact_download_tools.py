"""
Unit tests for artifact download tools.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from evergreen_mcp import artifact_download_tools
from evergreen_mcp.artifact_download_tools import (
    _safe_join,
    download_task_artifacts,
    fetch_task_artifacts,
)
from evergreen_mcp.evergreen_rest_client import EvergreenRestClient
from evergreen_mcp.models import Artifact, TaskResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client() -> EvergreenRestClient:
    return EvergreenRestClient(bearer_token="tok")


def _make_artifact(**overrides) -> Artifact:
    defaults = dict(
        name="binary",
        url="https://s3.example.com/binary.tar.gz",
        visibility="signed",
        ignore_for_fetch=False,
        content_type="application/x-gzip",
    )
    defaults.update(overrides)
    return Artifact(**defaults)


def _make_task(artifacts=None) -> TaskResponse:
    if artifacts is None:
        artifacts = [_make_artifact()]
    return TaskResponse(
        task_id="task-abc",
        execution=0,
        display_name="compile",
        status="failed",
        activated=True,
        build_id="build-1",
        build_variant="enterprise-rhel-80-64-bit",
        version_id="version-xyz",
        artifacts=artifacts,
    )


# ---------------------------------------------------------------------------
# _safe_join
# ---------------------------------------------------------------------------


class TestSafeJoin(unittest.TestCase):
    def test_normal_path_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            result = _safe_join(base, "subdir", "file.txt")
            assert str(result).startswith(str(base))

    def test_traversal_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            with pytest.raises(ValueError, match="Path traversal detected"):
                _safe_join(base, "..", "outside.txt")

    def test_deep_traversal_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            with pytest.raises(ValueError, match="Path traversal detected"):
                _safe_join(base, "a", "b", "../../..", "etc", "passwd")

    def test_prefix_collision_raises(self):
        """A sibling directory whose name starts with the base name must be rejected.

        E.g. base=/tmp/foo must NOT accept /tmp/foobar/baz.
        """
        with tempfile.TemporaryDirectory() as tmp:
            # Create /tmp/<rand>/foo  (the intended base)
            base = Path(tmp, "foo").resolve()
            base.mkdir()
            # Create /tmp/<rand>/foobar  (the sibling with matching prefix)
            sibling = Path(tmp, "foobar").resolve()
            sibling.mkdir()
            with pytest.raises(ValueError, match="Path traversal detected"):
                # "../foobar/evil.txt" resolves to the sibling, which starts
                # with the base string but is NOT inside it.
                _safe_join(base, "..", "foobar", "evil.txt")


# ---------------------------------------------------------------------------
# download_task_artifacts
# ---------------------------------------------------------------------------


class TestDownloadTaskArtifacts(unittest.IsolatedAsyncioTestCase):
    async def test_downloads_artifact_to_expected_path(self):
        client = _make_client()
        task = _make_task()
        client.get_task_details = AsyncMock(return_value=task)

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(
                artifact_download_tools,
                "_stream_to_file",
                new=AsyncMock(),
            ) as mock_stream:
                result = await download_task_artifacts(client, "task-abc", work_dir=tmp)

        assert "binary" in result
        mock_stream.assert_called_once()
        _, called_url, _ = mock_stream.call_args[0]
        assert called_url == "https://s3.example.com/binary.tar.gz"

    async def test_artifact_filter_narrows_download(self):
        client = _make_client()
        task = _make_task(
            artifacts=[
                _make_artifact(
                    name="binary", url="https://s3.example.com/binary.tar.gz"
                ),
                _make_artifact(
                    name="logs",
                    url="https://s3.example.com/logs.html",
                    content_type="text/html",
                ),
            ]
        )
        client.get_task_details = AsyncMock(return_value=task)

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(
                artifact_download_tools,
                "_stream_to_file",
                new=AsyncMock(),
            ) as mock_stream:
                result = await download_task_artifacts(
                    client, "task-abc", artifact_filter="logs", work_dir=tmp
                )

        assert "logs" in result
        assert "binary" not in result
        assert mock_stream.call_count == 1
        _, called_url, _ = mock_stream.call_args[0]
        assert called_url == "https://s3.example.com/logs.html"

    async def test_no_artifacts_returns_empty_dict(self):
        client = _make_client()
        task = _make_task(artifacts=None)
        client.get_task_details = AsyncMock(return_value=task)

        result = await download_task_artifacts(client, "task-abc")
        assert result == {}

    async def test_filter_with_no_matches_raises_value_error(self):
        client = _make_client()
        task = _make_task()
        client.get_task_details = AsyncMock(return_value=task)

        with pytest.raises(ValueError, match="No artifacts match filter"):
            await download_task_artifacts(
                client, "task-abc", artifact_filter="nonexistent"
            )

    async def test_ignore_for_fetch_skips_artifact(self):
        client = _make_client()
        task = _make_task(artifacts=[_make_artifact(ignore_for_fetch=True)])
        client.get_task_details = AsyncMock(return_value=task)

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(
                artifact_download_tools,
                "_stream_to_file",
                new=AsyncMock(),
            ) as mock_stream:
                result = await download_task_artifacts(client, "task-abc", work_dir=tmp)

        assert result == {}
        mock_stream.assert_not_called()

    async def test_download_failure_is_skipped_and_logged(self):
        """A per-artifact HTTP error should be swallowed; other artifacts continue."""
        client = _make_client()
        task = _make_task(
            artifacts=[
                _make_artifact(name="bad", url="https://s3.example.com/bad.tar.gz"),
                _make_artifact(
                    name="good",
                    url="https://s3.example.com/good.tar.gz",
                ),
            ]
        )
        client.get_task_details = AsyncMock(return_value=task)

        async def fake_stream(http_client, url, dest):
            if "bad" in url:
                raise httpx.HTTPError("connection refused")
            dest.write_bytes(b"data")

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(
                artifact_download_tools,
                "_stream_to_file",
                new=fake_stream,
            ):
                result = await download_task_artifacts(client, "task-abc", work_dir=tmp)

        assert "good" in result
        assert "bad" not in result

    async def test_path_traversal_in_version_id_raises(self):
        client = _make_client()
        task = TaskResponse(
            task_id="task-abc",
            execution=0,
            display_name="compile",
            status="failed",
            activated=True,
            build_id="build-1",
            build_variant="enterprise-rhel-80-64-bit",
            version_id="../../evil",
            artifacts=[_make_artifact()],
        )
        client.get_task_details = AsyncMock(return_value=task)

        with tempfile.TemporaryDirectory() as tmp:
            with pytest.raises(ValueError, match="Path traversal detected"):
                await download_task_artifacts(client, "task-abc", work_dir=tmp)

    async def test_url_with_no_path_uses_artifact_name(self):
        """When the URL has no filename component the artifact name is used."""
        client = _make_client()
        task = _make_task(
            artifacts=[
                _make_artifact(
                    url="https://s3.example.com/",
                    name="my artifact",
                    content_type="application/x-gzip",
                )
            ]
        )
        client.get_task_details = AsyncMock(return_value=task)

        captured: dict = {}

        async def fake_stream(http_client, url, dest):
            captured["dest"] = dest
            dest.write_bytes(b"data")

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(
                artifact_download_tools,
                "_stream_to_file",
                new=fake_stream,
            ):
                result = await download_task_artifacts(client, "task-abc", work_dir=tmp)

        assert "my artifact" in result
        assert captured["dest"].name == "my_artifact.tgz"

    async def test_filename_collision_disambiguates(self):
        """Two artifacts resolving to the same filename get distinct paths."""
        client = _make_client()
        task = _make_task(
            artifacts=[
                _make_artifact(
                    name="first",
                    url="https://s3.example.com/path-a/results.tar.gz",
                ),
                _make_artifact(
                    name="second",
                    url="https://s3.example.com/path-b/results.tar.gz",
                ),
            ]
        )
        client.get_task_details = AsyncMock(return_value=task)

        captured_paths: list = []

        async def fake_stream(http_client, url, dest):
            captured_paths.append(dest)
            dest.write_bytes(b"data")

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(
                artifact_download_tools,
                "_stream_to_file",
                new=fake_stream,
            ):
                result = await download_task_artifacts(client, "task-abc", work_dir=tmp)

        assert len(result) == 2
        assert len(captured_paths) == 2
        # The two files should have distinct names on disk.
        assert captured_paths[0].name != captured_paths[1].name
        # The second should have a _1 suffix.
        assert captured_paths[0].name == "results.tar.gz"
        assert captured_paths[1].name == "results_1.tar.gz"


# ---------------------------------------------------------------------------
# fetch_task_artifacts (wrapper)
# ---------------------------------------------------------------------------


class TestFetchTaskArtifacts(unittest.IsolatedAsyncioTestCase):
    async def test_returns_structured_result_on_success(self):
        client = _make_client()
        task = _make_task()
        client.get_task_details = AsyncMock(return_value=task)

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(
                artifact_download_tools,
                "_stream_to_file",
                new=AsyncMock(),
            ):
                result = await fetch_task_artifacts(
                    client,
                    task_id="task-abc",
                    work_dir=tmp,
                )

        assert result["task_id"] == "task-abc"
        assert "downloaded_artifacts" in result
        assert "artifact_count" in result
        assert "error" not in result

    async def test_returns_error_dict_on_value_error(self):
        client = _make_client()
        task = _make_task()
        client.get_task_details = AsyncMock(return_value=task)

        result = await fetch_task_artifacts(
            client,
            task_id="task-abc",
            artifact_filter="nonexistent",
        )

        assert "error" in result
        assert "nonexistent" in result["error"]
        assert result["task_id"] == "task-abc"
        assert result["artifact_filter"] == "nonexistent"

    async def test_returns_error_dict_on_runtime_error(self):
        """RuntimeError from get_task_details should be caught and surfaced."""
        client = _make_client()
        client.get_task_details = AsyncMock(
            side_effect=RuntimeError("Failed to fetch task details for 'bad-id'")
        )

        result = await fetch_task_artifacts(
            client,
            task_id="bad-id",
        )

        assert "error" in result
        assert "RuntimeError" in result["error"]
        assert "bad-id" in result["error"]
        assert result["task_id"] == "bad-id"
