"""
REST API client for the Evergreen API.

This module provides a REST client for interacting with the Evergreen CI/CD platform.
It handles authentication, connection management and query execution.
"""

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Dict, Optional
from evergreen_mcp.utils import scan_log_for_errors
import aiohttp

if TYPE_CHECKING:
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
        elif self.user and self.api_key:
            logger.debug("Using API key for authenticating HTTP requests")
            headers["Api-User"] = self.user
            headers["Api-Key"] = self.api_key
        else:
            raise Exception("No authentication method provided")
        print(headers)
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
        print("Attempting token refresh...")
        logger.info("Attempting token refresh...")
        try:
            token_data = await self._auth_manager.refresh_token()
            if token_data:
                self.bearer_token = token_data["access_token"]
                self.headers = self._get_headers()
                await self._close_session()  # Force new session with new headers
                print("Token refreshed successfully")
                logger.info("Token refreshed successfully")
                return True
        except Exception as e:
            logger.error("Token refresh failed: %s", e)
        return False

    async def _request(self, method: str, url: str, _retry: bool = True, **kwargs) -> Any:
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
                if response.status == 401 and _retry and await self._try_refresh_token():
                    return await self._request(method, url, _retry=False, **kwargs)
                print("Response status: ", response.status)
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "")
                if "application/json" in content_type:
                    return {
                        "status": "success",
                        "data": await response.json()
                    }
                else:
                    return {
                        "status": "success",
                        "data": await response.text()
                    }
        except aiohttp.ClientResponseError as e:
            if e.status == 401 and _retry and await self._try_refresh_token():
                return await self._request(method, url, _retry=False, **kwargs)
            raise
            

    async def get_task_logs(self, task_id: str, execution_retries: int) -> str:
        """
        Get the logs for a task.
        """
        endpoint = f"tasks/{task_id}/build/TaskLogs?type=task_log&execution={execution_retries}"
        response = await self._request("GET", endpoint)
        if response.get("status") == "success":
            return response.get("data")
        else:
            return None

    async def get_task_test_results(
        self,
        task_id: str,
        execution_retries: int,
        job_name: str,
        tail_limit: int = 1000,
    ) -> Optional[str]:
        """
        Get the test results for a task and scan for errors.
        Returns:
        - summary string (with top hits + example lines), or
        - raw log if no suspicious lines found (you can change this behavior).
        """
        endpoint = (
            f"tasks/{task_id}/build/TestLogs/{job_name}%2Fglobal.log"
            f"?execution={execution_retries}&tail_limit={tail_limit}"
        )
        response = await self._request("GET", endpoint)

        if response.get("status") != "success":
            return None

        log_text = response.get("data") or ""
        logger.info("Test results bytes: %s", len(log_text))

        scan = scan_log_for_errors(
            log_text,
            max_examples_per_term=2,
            include_matched_excerpt=False,  # set True if you want the matched lines returned too
        )

        # If nothing matched, just return the tail you fetched (or return a "clean" message)
        if scan.matched_lines == 0:
            return log_text

        # Build a compact summary
        lines = []
        lines.append(
            f"Log scan: {scan.matched_lines}/{scan.total_lines} lines matched error patterns."
        )
        lines.append("")
        lines.append("Top hits:")
        for term, count in scan.top_terms:
            lines.append(f"  - {term}: {count}")

        lines.append("")
        lines.append("Examples (first few per term):")
        for term, _count in scan.top_terms:
            exs = scan.examples_by_term.get(term, [])
            if not exs:
                continue
            lines.append(f"\n[{term}]")
            for ex in exs:
                # Keep lines from exploding in size
                if len(ex) > 500:
                    ex = ex[:500] + "…"
                lines.append(f"  {ex}")

        summary = "\n".join(lines)

        # Log the summary too (optional)
        logger.warning("Detected error patterns in task log:\n%s", summary)

        return summary

