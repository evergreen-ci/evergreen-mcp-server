"""
REST API client for the Evergreen API.

This module provides a REST client for interacting with the Evergreen CI/CD platform.
It handles authentication, connection management and query execution.
"""

import logging
from typing import Any, Callable, Dict, Optional

import aiohttp

from evergreen_mcp.models import TaskResponse
from evergreen_mcp.reconnect import ReconnectMixin
from evergreen_mcp.utils import scan_log_for_errors

# from . import __version__
__version__ = "0.1.0"

logger = logging.getLogger(__name__)


class EvergreenRestClient(ReconnectMixin):
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
        token_getter: Optional[Callable] = None,
    ):
        """
        Initialize the EvergreenRestClient.

        Args:
            user: Evergreen username (for API key auth)
            api_key: The API key to use for authentication.
            bearer_token: Static OAuth/OIDC bearer token (for token auth)
            base_url: The base URL of the Evergreen API.
            token_getter: Async callable that returns a fresh bearer token on each call.
                          Takes precedence over bearer_token and handles expiry automatically.
        """

        self.user = user
        self.base_url = base_url
        self.api_key = api_key
        self.bearer_token = bearer_token
        self._token_getter = token_getter

        if not token_getter and not bearer_token and not (user and api_key):
            raise ValueError(
                "Either token_getter, bearer_token, or (user and api_key) must be provided"
            )

        self.session = None  # Created lazily in _request
        # No explicit connect(): the session is created lazily, so the client
        # is usable immediately.
        self._init_reconnect_state(start_ready=True)

    async def _get_auth_headers(self) -> Dict[str, str]:
        """Build auth headers, calling token_getter if set."""
        headers: Dict[str, str] = {
            "User-Agent": f"evergreen-mcp/{__version__}",
            "Accept": "application/json",
        }
        if self._token_getter:
            logger.debug("Using Bearer token for authenticating HTTP requests")
            headers["Authorization"] = f"Bearer {await self._token_getter()}"
        elif self.bearer_token:
            logger.debug("Using Bearer token for authenticating HTTP requests")
            headers["Authorization"] = f"Bearer {self.bearer_token}"
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
            self.session = aiohttp.ClientSession()
        return self.session

    async def _close_session(self):
        """
        Close the session for the API request.
        """
        if self.session is not None:
            await self.session.close()
            self.session = None

    async def _request(self, method: str, url: str, **kwargs) -> Any:
        """
        Make a request to the API.

        On a 401 the token is refreshed and the request is retried, coordinated
        across concurrent callers by ReconnectMixin (single-flight).
        """
        if url.startswith("http"):
            full_url = url
        else:
            full_url = self.base_url + url

        async def attempt(_generation: int) -> Any:
            # Rebuild headers each attempt so a token refreshed by a reconnect
            # is picked up on retry.
            headers = await self._get_auth_headers()
            session = self._get_session()
            async with session.request(
                method, full_url, headers=headers, **kwargs
            ) as response:
                logger.debug("Response status: %s", response.status)
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "")
                if "application/json" in content_type:
                    return {"status": "success", "data": await response.json()}
                return {"status": "success", "data": await response.text()}

        return await self._run_with_reconnect(attempt)

    def _is_auth_error(self, error: Exception) -> bool:
        """Return True for a 401 response when we can refresh the token."""
        return (
            self._token_getter is not None
            and isinstance(error, aiohttp.ClientResponseError)
            and error.status == 401
        )

    async def _reestablish_connection(self) -> None:
        """Drop the session and force a fresh token for the next request."""
        await self._close_session()
        if self._token_getter:
            # Prime the shared token cache; the next _get_auth_headers() call
            # (on retry) picks up the refreshed token.
            await self._token_getter(force_refresh=True)

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
