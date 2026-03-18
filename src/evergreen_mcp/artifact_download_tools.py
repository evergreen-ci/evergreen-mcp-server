"""Artifact download tools for Evergreen MCP server

This module provides functions for downloading task artifacts from Evergreen.
"""

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Optional
from urllib.parse import urlparse

import httpx

if TYPE_CHECKING:
    from .evergreen_rest_client import EvergreenRestClient

logger = logging.getLogger(__name__)


def _safe_join(base: Path, *parts: str) -> Path:
    """Join path parts under *base*, raising ValueError on traversal attempts.

    Args:
        base: The resolved base directory that all results must stay within.
        *parts: Path components to append.

    Returns:
        The resolved joined path.

    Raises:
        ValueError: If the resolved path would escape *base*.
    """
    joined = base.joinpath(*parts).resolve()
    if not joined.is_relative_to(base):
        raise ValueError(
            f"Path traversal detected: resolved path '{joined}' "
            f"is outside base directory '{base}'"
        )
    return joined


async def download_task_artifacts(
    rest_client: "EvergreenRestClient",
    task_id: str,
    artifact_filter: Optional[str] = None,
    work_dir: str = "WORK",
) -> Dict[str, Path]:
    """Download artifacts for a task.

    Args:
        rest_client: An EvergreenRestClient instance.
        task_id: The task identifier.
        artifact_filter: If provided, only artifacts whose name contains this
            string (case-insensitive) are downloaded.
        work_dir: Base directory; artifacts land under
            ``<work_dir>/<version_id>/task-<display_name>-<execution>/``.

    Returns:
        Dict mapping artifact name to the downloaded file Path.

    Raises:
        ValueError: If no artifacts match the filter, or if a path traversal
            is detected in API-supplied values.
    """
    logger.info("Downloading artifacts for task: %s", task_id)

    task = await rest_client.get_task_details(task_id)

    if not task.artifacts:
        logger.warning("No artifacts found for task: %s", task_id)
        return {}

    artifacts_to_download = task.artifacts
    if artifact_filter:
        artifacts_to_download = [
            a for a in task.artifacts if artifact_filter.lower() in a.name.lower()
        ]
        logger.info(
            "Filtered to %d artifacts containing '%s'",
            len(artifacts_to_download),
            artifact_filter,
        )

    if not artifacts_to_download:
        artifact_names = [a.name for a in task.artifacts]
        error_msg = f"No artifacts match filter '{artifact_filter}'.\n\n"
        error_msg += f"Available artifacts ({len(artifact_names)} total):\n"
        for name in artifact_names:
            error_msg += f"  - {name}\n"
        raise ValueError(error_msg)

    # Validate and create the artifacts directory.  version_id and display_name
    # come from the Evergreen API, so we resolve and check for traversal.
    base_dir = Path(work_dir).resolve()
    artifacts_dir = _safe_join(
        base_dir,
        task.version_id,
        f"task-{task.display_name}-{task.execution}",
    )
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Artifacts directory: %s", artifacts_dir)

    downloaded: Dict[str, Path] = {}
    seen_filenames: set[str] = set()

    # This tool does not support auth at this time
    async with httpx.AsyncClient(timeout=60) as http_client:
        for artifact in artifacts_to_download:
            if artifact.ignore_for_fetch:
                logger.info("Skipping artifact '%s' (marked to ignore)", artifact.name)
                continue

            logger.info("Downloading artifact: %s", artifact.name)
            parsed = urlparse(artifact.url)
            file_name = Path(parsed.path).name
            if not file_name:
                file_name = artifact.name.replace(" ", "_").replace("/", "_")
                if artifact.content_type == "application/x-gzip":
                    file_name += ".tgz"
                elif artifact.content_type == "text/html":
                    file_name += ".html"

            # Disambiguate filename collisions by appending a counter.
            if file_name in seen_filenames:
                p = Path(file_name)
                # Use suffixes to handle compound extensions like .tar.gz
                all_suffixes = "".join(p.suffixes)
                stem = p.name.removesuffix(all_suffixes) if all_suffixes else p.name
                counter = 1
                while f"{stem}_{counter}{all_suffixes}" in seen_filenames:
                    counter += 1
                file_name = f"{stem}_{counter}{all_suffixes}"
            seen_filenames.add(file_name)

            # Validate the constructed file path stays within artifacts_dir.
            try:
                file_path = _safe_join(artifacts_dir, file_name)
            except ValueError as e:
                logger.error(
                    "Skipping artifact '%s': unsafe filename '%s': %s",
                    artifact.name,
                    file_name,
                    e,
                )
                continue

            try:
                await _stream_to_file(http_client, artifact.url, file_path)
                downloaded[artifact.name] = file_path
                logger.info("Downloaded: %s", file_path)
            except (httpx.HTTPError, IOError) as e:
                logger.error("Failed to download artifact '%s': %s", artifact.name, e)

    logger.info("Downloaded %d artifacts to: %s", len(downloaded), artifacts_dir)
    return downloaded


async def fetch_task_artifacts(
    client: "EvergreenRestClient",
    task_id: str,
    artifact_filter: Optional[str] = None,
    work_dir: str = "WORK",
) -> dict:
    """Download artifacts and return a JSON-serialisable result dict.

    Thin wrapper around download_task_artifacts for use by the MCP tool layer.
    Catches all exceptions (e.g. filter matched nothing, API errors, validation
    failures) and surfaces them as an ``error`` key rather than propagating.

    Args:
        client: EvergreenRestClient instance.
        task_id: The task identifier.
        artifact_filter: If provided, only artifacts whose name contains this
            string (case-insensitive) are downloaded.
        work_dir: Base directory for downloaded artifacts.

    Returns:
        A dict with keys:
        - ``task_id``, ``artifact_filter``, ``work_dir``: echoed for context.
        - ``downloaded_artifacts``: mapping of artifact name -> file path string.
        - ``artifact_count``: number of artifacts downloaded.
        - ``error``: present only on failure; other keys are absent.
    """
    try:
        result = await download_task_artifacts(
            client,
            task_id=task_id,
            artifact_filter=artifact_filter,
            work_dir=work_dir,
        )
    except Exception as exc:
        return {
            "task_id": task_id,
            "artifact_filter": artifact_filter,
            "work_dir": work_dir,
            "error": f"{type(exc).__name__}: {exc}",
        }

    return {
        "task_id": task_id,
        "artifact_filter": artifact_filter,
        "work_dir": work_dir,
        "downloaded_artifacts": {name: str(path) for name, path in result.items()},
        "artifact_count": len(result),
    }


async def _stream_to_file(client: httpx.AsyncClient, url: str, dest: Path) -> None:
    """Stream the content of *url* directly to *dest* without buffering in RAM.

    Args:
        client: An active httpx.AsyncClient to reuse for the request.
        url: The URL to download.
        dest: Filesystem path to write to (overwritten if it already exists).

    Raises:
        httpx.HTTPStatusError: If the server returns a non-2xx response.
        IOError: If writing to *dest* fails.
    """
    async with client.stream("GET", url) as response:
        response.raise_for_status()
        with open(dest, "wb") as f:
            async for chunk in response.aiter_bytes(chunk_size=65536):
                await asyncio.to_thread(f.write, chunk)
