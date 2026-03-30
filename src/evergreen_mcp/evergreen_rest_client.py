"""
REST API client for the Evergreen API.

This module provides a REST client for interacting with the Evergreen CI/CD platform.
It handles authentication, connection management and query execution.
"""

import logging
from typing import Any, Dict, Optional

import aiohttp

from evergreen_mcp.models import TaskResponse
from evergreen_mcp.utils import scan_log_for_errors

from .oidc_auth import OIDCAuthManager

# from . import __version__
__version__ = "0.1.0"

logger = logging.getLogger(__name__)


class EvergreenRestClient:
    """
    REST API client for the Evergreen API.

    This class provides a REST client for interacting with the Evergreen CI/CD platform.
    It handles authentication, connection management and query execution.
    """

    def __init__(
        self,
        user: Optional[str] = None,
        base_url: str = "https://evergreen.corp.mongodb.com/rest/v2/",
        api_key: Optional[str] = None,
        bearer_token: Optional[str] = None,
        auth_manager: Optional["OIDCAuthManager"] = None,
    ):
        """
        Initialize the EvergreenRestClient.

        Args:
            user: Evergreen username (for API key auth)
            api_key: The API key to use for authentication.
            bearer_token: OAuth/OIDC bearer token (for token auth)
            base_url: The base URL of the Evergreen API.
            auth_manager: OIDCAuthManager instance for automatic token refresh
        """

        self.user = user
        self.base_url = base_url
        self.api_key = api_key
        self.bearer_token = bearer_token
        self._auth_manager = auth_manager

        if not bearer_token and not (user and api_key) and not auth_manager:
            raise ValueError(
                "Either bearer_token, (user and api_key), or auth_manager must be provided"
            )

        # If auth_manager provided, use its token
        if auth_manager and not bearer_token:
            self.bearer_token = auth_manager.access_token

        self.headers = self._get_headers()
        self.session = None  # Created lazily in _request

    def _get_headers(self) -> Dict[str, str]:
        """
        Get the headers for the API request.
        """
        headers = {
            "User-Agent": f"evergreen-mcp/{__version__}",
            "Accept": "application/json",
        }
        if self.bearer_token:
            logger.debug("Using Bearer token for authenticating HTTP requests")
            headers["Authorization"] = f"Bearer {self.bearer_token}"
            # Also set the Kanopy internal header for mesh-to-mesh communication
            headers["x-kanopy-internal-authorization"] = f"Bearer {self.bearer_token}"
        elif self.user and self.api_key:
            logger.debug("Using API key for authenticating HTTP requests")
            headers["Api-User"] = self.user
            headers["Api-Key"] = self.api_key
        else:
            raise Exception("No authentication method provided")
        return headers

    def _get_session(self) -> aiohttp.ClientSession:
        """
        Get the session for the API request.
        """
        if self.session is None:
            self.session = aiohttp.ClientSession(headers=self.headers)
        return self.session

    async def _close_session(self):
        """
        Close the session for the API request.
        """
        if self.session is not None:
            await self.session.close()
            self.session = None

    async def _try_refresh_token(self) -> bool:
        """Attempt to refresh the bearer token and recreate session."""
        if not self._auth_manager or not self.bearer_token:
            return False
        logger.info("Attempting token refresh...")
        try:
            token_data = await self._auth_manager.refresh_token()
            if token_data:
                self.bearer_token = token_data["access_token"]
                self.headers = self._get_headers()
                await self._close_session()  # Force new session with new headers
                logger.info("Token refreshed successfully")
                return True
        except Exception as e:
            logger.error("Token refresh failed: %s", e)
        return False

    async def _request(
        self, method: str, url: str, _retry: bool = True, **kwargs
    ) -> Any:
        """
        Make a request to the API.
        """
        session = self._get_session()
        if url.startswith("http"):
            full_url = url
        else:
            full_url = self.base_url + url

        try:
            async with session.request(method, full_url, **kwargs) as response:
                # Handle 401 - try token refresh
                if (
                    response.status == 401
                    and _retry
                    and await self._try_refresh_token()
                ):
                    return await self._request(method, url, _retry=False, **kwargs)
                logger.debug("Response status: %s", response.status)
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "")
                if "application/json" in content_type:
                    return {"status": "success", "data": await response.json()}
                else:
                    return {"status": "success", "data": await response.text()}
        except aiohttp.ClientResponseError as e:
            if e.status == 401 and _retry and await self._try_refresh_token():
                return await self._request(method, url, _retry=False, **kwargs)
            raise

    async def get_task_logs(
        self, task_id: str, execution_retries: int
    ) -> Optional[str]:
        """
        Get the logs for a task.

        Args:
            task_id: The task identifier.
            execution_retries: The execution number (0 for first run, 1+ for retries).

        Returns:
            The raw log text, or None if the request failed.
        """
        endpoint = f"tasks/{task_id}/build/TaskLogs?type=task_log&execution={execution_retries}"
        response = await self._request("GET", endpoint)
        if response.get("status") != "success":
            return None

        log_text = response.get("data") or ""
        logger.info("Task log bytes: %s", len(log_text))

        # Scan for error patterns and return a structured summary when matches
        # are found; otherwise fall back to the raw text.
        scan = scan_log_for_errors(log_text)
        if scan.matched_lines == 0:
            return log_text

        parts: list[str] = []
        parts.append(f"Log scan: {scan.matched_lines}/{scan.total_lines} lines matched")
        parts.append("")

        if scan.top_terms:
            parts.append("Top error terms:")
            for term, count in scan.top_terms:
                parts.append(f"  {term}: {count}")
            parts.append("")

        if scan.examples_by_term:
            parts.append("Example lines per term:")
            for term, examples in list(scan.examples_by_term.items())[:10]:
                parts.append(f"  [{term}]")
                for ex in examples:
                    parts.append(f"    {ex}")
            parts.append("")

        if scan.matched_excerpt:
            parts.append("Matched excerpt (last 50 matched lines):")
            parts.append(scan.matched_excerpt)

        return "\n".join(parts)

    async def get_task_test_results(
        self,
        task_id: str,
        execution_retries: int,
        test_name: str,
        tail_limit: int = 100000,
    ) -> Optional[str]:
        """
        Get raw test log content for a task.

        Fetches the test log from the REST API (stored in S3, not accessible
        via GraphQL). Returns the raw log text for downstream processing.

        Args:
            task_id: The task identifier.
            execution_retries: The execution number (0 for first run, 1+ for retries).
            test_name: The test name used to locate logs in S3 (e.g., Job0, Job1).
            tail_limit: Number of lines to return from the end of the log.

        Returns:
            The raw test log text, or None if the request failed.
        """
        endpoint = (
            f"tasks/{task_id}/build/TestLogs/{test_name}%2Fglobal.log"
            f"?execution={execution_retries}&tail_limit={tail_limit}"
        )
        response = await self._request("GET", endpoint)

        if response.get("status") != "success":
            return None

        log_text = response.get("data") or ""
        logger.info("Test results bytes: %s", len(log_text))

        # Scan for error patterns and return a structured summary when matches
        # are found; otherwise fall back to the raw text.
        scan = scan_log_for_errors(log_text)
        if scan.matched_lines == 0:
            return log_text

        parts: list[str] = []
        parts.append(f"Log scan: {scan.matched_lines}/{scan.total_lines} lines matched")
        parts.append("")

        if scan.top_terms:
            parts.append("Top error terms:")
            for term, count in scan.top_terms:
                parts.append(f"  {term}: {count}")
            parts.append("")

        if scan.examples_by_term:
            parts.append("Example lines per term:")
            for term, examples in list(scan.examples_by_term.items())[:10]:
                parts.append(f"  [{term}]")
                for ex in examples:
                    parts.append(f"    {ex}")
            parts.append("")

        if scan.matched_excerpt:
            parts.append("Matched excerpt (last 50 matched lines):")
            parts.append(scan.matched_excerpt)

        return "\n".join(parts)

    async def get_task_details(
        self, task_id: str, fetch_all_executions: bool = False
    ) -> TaskResponse:
        """Fetch detailed information about a specific task.

        Args:
            task_id: The task identifier.
            fetch_all_executions: Whether to include all historical executions.

        Returns:
            TaskResponse for the requested task.

        Raises:
            RuntimeError: If the API request fails or returns an unexpected
                response shape.
            ValidationError: If the API payload cannot be parsed into a
                TaskResponse.
        """
        params = ""
        if fetch_all_executions:
            params = "?fetch_all_executions=true"
        endpoint = f"tasks/{task_id}{params}"
        response = await self._request(
            "GET", endpoint, timeout=aiohttp.ClientTimeout(total=30)
        )

        if response.get("status") != "success":
            raise RuntimeError(
                f"Failed to fetch task details for '{task_id}': "
                f"status={response.get('status')!r}"
            )

        data = response.get("data")
        if data is None:
            raise RuntimeError(f"No data returned for task '{task_id}'")

        return TaskResponse.model_validate(data)
