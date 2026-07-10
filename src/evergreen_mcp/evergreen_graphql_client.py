"""GraphQL client for Evergreen API

This module provides a GraphQL client for interacting with the Evergreen CI/CD platform.
It handles authentication, connection management, and query execution.
"""

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from gql import Client, gql
from gql.transport.aiohttp import AIOHTTPTransport
from gql.transport.exceptions import TransportError

from . import USER_AGENT
from .evergreen_queries import (
    GET_INFERRED_PROJECT_IDS,
    GET_PATCH_FAILED_TASKS,
    GET_PROJECT,
    GET_PROJECT_SETTINGS,
    GET_PROJECTS,
    GET_TASK_LOGS,
    GET_TASK_TEST_RESULTS,
    GET_USER_RECENT_PATCHES,
    GET_VERSION_WITH_FAILED_TASKS,
)

# Constants for test status values
FAILED_TEST_STATUSES = ["fail", "failed"]

# Maximum number of times _execute_query will reconnect and retry on a 401
# before giving up. Bounds thrashing when a fresh token is immediately stale.
_MAX_RECONNECT_ATTEMPTS = 2

logger = logging.getLogger(__name__)


class EvergreenGraphQLClient:
    """GraphQL client for Evergreen API

    This client provides async methods for querying the Evergreen GraphQL API.
    It handles authentication via API keys or Bearer tokens and manages the connection lifecycle.
    """

    def __init__(
        self,
        user: str = None,
        api_key: str = None,
        bearer_token: str = None,
        endpoint: str = None,
        token_getter: Optional[Callable[[bool], Awaitable[str]]] = None,
    ):
        """Initialize the GraphQL client

        Args:
            user: Evergreen username (for API key auth)
            api_key: Evergreen API key (for API key auth)
            bearer_token: Static OAuth/OIDC bearer token (for token auth)
            endpoint: GraphQL endpoint URL (defaults to Evergreen's main instance)
            token_getter: Async callable that returns a fresh bearer token on each connect.
        """
        self.user = user
        self.api_key = api_key
        self.bearer_token = bearer_token
        self.endpoint = endpoint or "https://evergreen.mongodb.com/graphql/query"
        self._client = None
        self._session = None
        # Held only while tearing down and rebuilding the connection, never
        # while executing a query.
        self._reconnect_lock = asyncio.Lock()
        # Set when the connection is healthy; cleared during a reconnect so
        # callers park until the connection is back up. Set() when healthy
        # returns immediately from wait(), so normal queries are not serialized.
        self._connected_event = asyncio.Event()
        # Incremented on each successful reconnect. Lets a caller detect that
        # another task already reconnected (single-flight) and that its own
        # observed connection is stale.
        self._generation = 0
        self._token_getter = token_getter

        # Validate that we have some form of authentication
        if not token_getter and not bearer_token and not (user and api_key):
            raise ValueError(
                "Either token_getter, bearer_token, or both user and api_key must be provided"
            )

    async def connect(self, force_refresh: bool = False):
        """Initialize GraphQL client connection"""
        if self._token_getter:
            token = await self._token_getter(force_refresh)
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            }
            logger.debug("Using Bearer token authentication")
        elif self.bearer_token:
            headers = {
                "Authorization": f"Bearer {self.bearer_token}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            }
            logger.debug("Using Bearer token authentication")
        else:
            headers = {
                "Api-User": self.user,
                "Api-Key": self.api_key,
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            }
            logger.debug("Using API key authentication")

        logger.debug("Connecting to GraphQL endpoint: %s", self.endpoint)

        transport = AIOHTTPTransport(url=self.endpoint, headers=headers)
        self._client = Client(transport=transport)
        self._session = await self._client.connect_async(reconnecting=True)
        self._connected_event.set()

        logger.info("GraphQL client connected successfully")

    async def close(self):
        """Close client connections"""
        if self._session:
            try:
                await self._session.close()
                logger.debug("GraphQL session closed")
            except Exception:
                logger.warning("Error closing GraphQL session", exc_info=True)

        self._session = None
        self._client = None
        self._connected_event.clear()

    async def _execute_query(
        self, query_string: str, variables: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Execute a GraphQL query with error handling.

        Args:
            query_string: GraphQL query string
            variables: Query variables

        Returns:
            Query result data

        Raises:
            Exception: If query execution fails
        """
        query = gql(query_string)

        for _ in range(_MAX_RECONNECT_ATTEMPTS):
            # Park until the connection is healthy. When it already is, this
            # returns immediately without holding any lock, so concurrent
            # queries are not serialized. During a reconnect the event is
            # cleared and callers wait here instead of firing at a torn-down
            # session or each starting their own reconnect.
            await self._connected_event.wait()

            # Capture the connection we are about to use. gen lets us detect a
            # reconnect that happens underneath us; using the local session
            # avoids touching a reference another task may swap mid-flight.
            gen = self._generation
            session = self._session
            if session is None:
                raise RuntimeError("Client not connected. Call connect() first.")

            if self._token_getter and session.transport.session is not None:
                # Proactively refresh the auth header from the (cached) token so
                # ordinary token rotation never even reaches a 401. The heavy
                # teardown in _reconnect() is reserved for genuine auth failures.
                session.transport.session.headers.update(
                    {"Authorization": f"Bearer {await self._token_getter()}"}
                )

            try:
                result = await session.execute(query, variable_values=variables)
                logger.debug(
                    "Query executed successfully: %s chars returned", len(str(result))
                )
                return result
            except TransportError as e:
                if self._generation != gen or not self._connected_event.is_set():
                    # Another task is reconnecting (event cleared) or already
                    # reconnected (generation advanced) while we were in flight;
                    # our failure is likely collateral from that teardown. Loop
                    # to park on the event and retry on the fresh connection
                    # instead of surfacing the error.
                    logger.debug("Connection changing during query; retrying")
                    continue
                if self._is_auth_error(e) and self._token_getter:
                    logger.info(
                        "Got 401 on GraphQL query, reconnecting with fresh token"
                    )
                    await self._reconnect(gen)
                    continue
                logger.warning("GraphQL transport error")
                raise Exception("Failed to execute GraphQL query") from e
            except Exception:
                logger.warning("GraphQL query execution error")
                raise

        raise Exception("Failed to execute GraphQL query after repeated token refresh")

    @staticmethod
    def _is_auth_error(error: Exception) -> bool:
        """Return True if the transport error looks like an auth failure (401)."""
        error_str = str(error).lower()
        return "401" in error_str or "unauthorized" in error_str

    async def _reconnect(self, observed_generation: int):
        """Tear down and rebuild the connection, at most once per generation.

        Only the first caller to observe a given generation performs the
        teardown/connect (single-flight); concurrent callers that observed the
        same generation return immediately and let their caller retry against
        the new connection.

        Args:
            observed_generation: The generation the caller used when its query
                failed. If the current generation has already advanced past it,
                someone else has reconnected and this call is a no-op.
        """
        async with self._reconnect_lock:
            if self._generation != observed_generation:
                # A reconnect already happened since the caller's query; nothing
                # to do. The caller will loop and retry on the new connection.
                return

            # Gate off new/retrying queries while we rebuild the connection.
            self._connected_event.clear()
            try:
                await self.close()
                await self.connect(force_refresh=True)
                self._generation += 1
            finally:
                # close()/connect() manage the event too, but set it here
                # unconditionally so a failed connect() can never deadlock
                # waiters. On failure the session is None and they raise instead.
                self._connected_event.set()

    async def get_projects(self) -> List[Dict[str, Any]]:
        """Get all projects from Evergreen

        Returns:
            List of project dictionaries with flattened structure
        """
        result = await self._execute_query(GET_PROJECTS)

        # Flatten grouped projects into simple list
        projects = []
        for group in result.get("projects", []):
            projects.extend(group.get("projects", []))

        logger.info("Retrieved %s projects", len(projects))
        return projects

    async def get_project(self, project_id: str) -> Dict[str, Any]:
        """Get specific project by ID

        Args:
            project_id: Project identifier

        Returns:
            Project details dictionary
        """
        variables = {"projectId": project_id}
        result = await self._execute_query(GET_PROJECT, variables)

        project = result.get("project")
        if not project:
            raise Exception(f"Project not found: {project_id}")

        logger.info(
            "Retrieved project details for: %s", project.get("displayName", project_id)
        )
        return project

    async def get_project_settings(self, project_id: str) -> Dict[str, Any]:
        """Get project settings and configuration

        Args:
            project_id: Project identifier

        Returns:
            Project settings dictionary
        """
        variables = {"projectId": project_id}
        result = await self._execute_query(GET_PROJECT_SETTINGS, variables)

        settings = result.get("projectSettings")
        if not settings:
            raise Exception(f"Project settings not found: {project_id}")

        logger.info("Retrieved project settings for: %s", project_id)
        return settings

    async def get_user_recent_patches(
        self, user_id: str, limit: int = 10, page: int = 0
    ) -> List[Dict[str, Any]]:
        """Get recent patches for the authenticated user with pagination

        Args:
            user_id: User identifier (typically email)
            limit: Number of patches per page (default: 10, max: 50)
            page: Page number (0-indexed, default: 0)

        Returns:
            List of patch dictionaries for the requested page
        """
        variables = {
            "userId": user_id,
            "limit": min(limit, 50),  # Cap at 50 for performance
            "page": page,
        }

        result = await self._execute_query(GET_USER_RECENT_PATCHES, variables)
        patches = result.get("user", {}).get("patches", {}).get("patches", [])

        logger.info(
            "Retrieved %s patches for user %s (page %s)", len(patches), user_id, page
        )
        return patches

    async def get_patch_failed_tasks(self, patch_id: str) -> Dict[str, Any]:
        """Get failed tasks for a specific patch

        Args:
            patch_id: Patch identifier

        Returns:
            Patch with failed tasks dictionary
        """
        variables = {"patchId": patch_id}
        result = await self._execute_query(GET_PATCH_FAILED_TASKS, variables)
        patch = result.get("patch")

        if not patch:
            raise Exception(f"Patch not found: {patch_id}")

        # Count failed tasks
        version = patch.get("versionFull", {})
        failed_count = version.get("tasks", {}).get("count", 0)

        logger.info("Retrieved patch %s with %s failed tasks", patch_id, failed_count)
        return patch

    async def get_version_with_failed_tasks(self, version_id: str) -> Dict[str, Any]:
        """Get version with failed tasks only

        Args:
            version_id: Version identifier

        Returns:
            Version with failed tasks dictionary
        """
        variables = {"versionId": version_id}
        result = await self._execute_query(GET_VERSION_WITH_FAILED_TASKS, variables)

        version = result.get("version")
        if not version:
            raise Exception(f"Version not found: {version_id}")

        failed_count = version.get("tasks", {}).get("count", 0)
        logger.info(
            "Retrieved version %s with %s failed tasks", version_id, failed_count
        )
        return version

    async def get_task_logs(self, task_id: str, execution: int = 0) -> Dict[str, Any]:
        """Get detailed logs for a specific task

        Args:
            task_id: Task identifier
            execution: Task execution number (default: 0)

        Returns:
            Task logs dictionary
        """
        variables = {"taskId": task_id, "execution": execution}
        result = await self._execute_query(GET_TASK_LOGS, variables)

        task = result.get("task")
        if not task:
            raise Exception(f"Task not found: {task_id}")

        logs_count = len(task.get("taskLogs", {}).get("taskLogs", []))
        logger.info("Retrieved %s log entries for task %s", logs_count, task_id)
        return task

    async def get_task_test_results(
        self,
        task_id: str,
        execution: int = 0,
        failed_only: bool = True,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """Get detailed test results for a specific task

        Args:
            task_id: Task identifier
            execution: Task execution number (default: 0)
            failed_only: Whether to fetch only failed tests (default: True)
            limit: Maximum number of test results to return (default: 100)

        Returns:
            Task test results dictionary
        """
        # Build test filter options
        test_filter_options = {"limit": limit, "page": 0}

        if failed_only:
            test_filter_options["statuses"] = FAILED_TEST_STATUSES

        variables = {
            "taskId": task_id,
            "execution": execution,
            "testFilterOptions": test_filter_options,
        }

        result = await self._execute_query(GET_TASK_TEST_RESULTS, variables)

        task = result.get("task")
        if not task:
            raise Exception(f"Task not found: {task_id}")

        test_results = task.get("tests", {})
        test_count = test_results.get("filteredTestCount", 0)
        logger.info("Retrieved %s test results for task %s", test_count, task_id)
        return task

    async def get_inferred_project_ids(
        self, user_id: str, limit: int = 50, page: int = 0
    ) -> List[Dict[str, Any]]:
        """Get project identifiers inferred from user's recent patches

        Args:
            user_id: User identifier (typically email)
            limit: Maximum number of patches to scan (default: 50)
            page: Page number (0-indexed, default: 0)

        Returns:
            List of patch dictionaries with project identifiers
        """
        variables = {
            "userId": user_id,
            "limit": min(limit, 50),
            "page": page,
        }

        result = await self._execute_query(GET_INFERRED_PROJECT_IDS, variables)
        patches = result.get("user", {}).get("patches", {}).get("patches", [])

        logger.info(
            "Retrieved %s patches for project inference for user %s",
            len(patches),
            user_id,
        )
        return patches

    async def __aenter__(self):
        """Async context manager entry"""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        _ = exc_type, exc_val, exc_tb  # Unused but required by protocol
        await self.close()
        return None
