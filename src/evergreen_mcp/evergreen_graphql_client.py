"""GraphQL client for Evergreen API

This module provides a GraphQL client for interacting with the Evergreen CI/CD platform.
It handles authentication, connection management, and query execution.
"""

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from .oidc_auth import OIDCAuthManager

from gql import Client, gql
from gql.transport.aiohttp import AIOHTTPTransport
from gql.transport.exceptions import TransportError

from . import USER_AGENT
from .evergreen_queries import (
    GET_INFERRED_PROJECT_IDS,
    GET_MAINLINE_COMMITS,
    GET_PATCH_CONFIGURE,
    GET_PATCH_STATUS_SUMMARY,
    GET_PATCH_TASKS,
    GET_PROJECTS,
    GET_TASK_LOGS,
    GET_TASK_TEST_RESULTS,
    GET_USER_RECENT_PATCHES,
    GET_VERSION_STATUS_SUMMARY,
    GET_VERSION_TASKS,
    GET_WATERFALL,
    RESTART_TASK,
    RESTART_VERSIONS,
    SCHEDULE_PATCH,
    SCHEDULE_TASKS,
)
from .utils import patch_version

# Constants for test status values
FAILED_TEST_STATUSES = ["fail", "failed"]

logger = logging.getLogger(__name__)


def _describe_transport_error(e: Exception) -> str:
    """Build an actionable one-line description of a GraphQL transport failure.

    The previous messages ("Failed to execute GraphQL query") discarded every
    detail, including the HTTP status. An expired OIDC token surfaced as that
    opaque string with the real 401 buried in a traceback, which invites blind
    retries of a call that cannot succeed until the caller re-authenticates.
    """
    status = getattr(e, "code", None)
    text = str(e)
    hint = ""
    if status == 401 or "401" in text or "unauthorized" in text.lower():
        hint = (
            " The Evergreen credentials are rejected — the OIDC token has most "
            "likely expired. Re-authenticate (refresh the token file, or run "
            "/mcp to reconnect this server) before retrying; retrying as-is "
            "will fail the same way."
        )
    elif status == 403 or "forbidden" in text.lower():
        hint = (
            " Authenticated but not authorized for this resource — check the "
            "project/patch belongs to you or that you have access."
        )
    elif status in (502, 503, 504) or "gateway" in text.lower():
        hint = " Evergreen appears to be unavailable; this is likely transient."
    status_part = f"HTTP {status}: " if status else ""
    return f"{status_part}{text}{hint}"


class EvergreenGraphQLClient:
    """GraphQL client for Evergreen API

    This client provides async methods for querying the Evergreen GraphQL API.
    It handles authentication via API keys or Bearer tokens and manages the connection lifecycle.

    For OIDC authentication, an auth_manager can be provided to enable automatic
    token refresh when the access token expires.
    """

    def __init__(
        self,
        user: str = None,
        api_key: str = None,
        bearer_token: str = None,
        endpoint: str = None,
        auth_manager: Optional["OIDCAuthManager"] = None,
    ):
        """Initialize the GraphQL client

        Args:
            user: Evergreen username (for API key auth)
            api_key: Evergreen API key (for API key auth)
            bearer_token: OAuth/OIDC bearer token (for token auth)
            endpoint: GraphQL endpoint URL (defaults to Evergreen's main instance)
            auth_manager: OIDCAuthManager instance for automatic token refresh
        """
        self.user = user
        self.api_key = api_key
        self.bearer_token = bearer_token
        self.endpoint = endpoint or "https://evergreen.mongodb.com/graphql/query"
        self._client = None
        self._session = None
        self._auth_manager = auth_manager

        # Validate that we have some form of authentication
        if not bearer_token and not (user and api_key):
            raise ValueError(
                "Either bearer_token or both user and api_key must be provided"
            )

    async def connect(self):
        """Initialize GraphQL client connection"""
        # Determine authentication method
        if self.bearer_token:
            # Use Bearer token authentication
            headers = {
                "Authorization": f"Bearer {self.bearer_token}",
                # Also set the Kanopy internal header for mesh-to-mesh communication
                "x-kanopy-internal-authorization": f"Bearer {self.bearer_token}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            }
            logger.debug("Using Bearer token authentication")
        else:
            # Use API key authentication
            headers = {
                "Api-User": self.user,
                "Api-Key": self.api_key,
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            }
            logger.debug("Using API key authentication")

        logger.debug("Connecting to GraphQL endpoint: %s", self.endpoint)

        # Create transport with headers directly
        transport = AIOHTTPTransport(url=self.endpoint, headers=headers)
        self._client = Client(transport=transport)
        self._session = await self._client.connect_async(reconnecting=True)

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

    async def _execute_query(
        self, query_string: str, variables: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Execute a GraphQL query with error handling and automatic token refresh

        Args:
            query_string: GraphQL query string
            variables: Query variables

        Returns:
            Query result data

        Raises:
            Exception: If query execution fails
        """
        if not self._session:
            raise RuntimeError("Client not connected. Call connect() first.")

        try:
            query = gql(query_string)
            result = await self._session.execute(query, variable_values=variables)
            logger.debug(
                "Query executed successfully: %s chars returned", len(str(result))
            )
            return result
        except TransportError as e:
            # Check if this is a 401 Unauthorized error
            error_str = str(e).lower()
            if "401" in error_str or "unauthorized" in error_str:
                if await self._try_refresh_token():
                    # Token refreshed, retry the query with proper error handling
                    logger.info("Retrying query after token refresh")
                    try:
                        query = gql(query_string)
                        result = await self._session.execute(
                            query, variable_values=variables
                        )
                        logger.debug(
                            "Query executed successfully after refresh: %s chars returned",
                            len(str(result)),
                        )
                        return result
                    except TransportError as retry_e:
                        logger.warning(
                            "GraphQL transport error on retry after token refresh: %s", retry_e, exc_info=True,
                        )
                        raise Exception(
                            "GraphQL query failed after a token refresh: "
                            + _describe_transport_error(retry_e)
                        ) from retry_e
                    except Exception as retry_e:
                        logger.warning(
                            "GraphQL query execution error on retry after token refresh",
                        )
                        raise Exception("Query failed after token refresh") from retry_e
            logger.warning("GraphQL transport error: %s", e, exc_info=True)
            raise Exception(
                "GraphQL query failed: " + _describe_transport_error(e)
            ) from e
        except Exception as e:
            logger.warning("GraphQL query execution error: %s", e, exc_info=True)
            raise

    async def _try_refresh_token(self) -> bool:
        """Attempt to refresh the bearer token and reconnect.

        Returns:
            True if token was refreshed and client reconnected, False otherwise
        """
        if not self._auth_manager or not self.bearer_token:
            logger.debug("No auth manager available for token refresh")
            return False

        logger.info("Access token rejected by server, attempting refresh...")
        try:
            token_data = await self._auth_manager.refresh_token()
            if token_data:
                self.bearer_token = token_data["access_token"]
                await self.close()
                await self.connect()
                logger.info("Token refreshed and client reconnected")
                return True
            else:
                logger.warning("Token refresh failed")
                return False
        except Exception as e:
            logger.warning("Error refreshing token: %s", e)
            return False

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

    async def get_patch_status_summary(
        self, patch_id: str, failed_statuses: List[str], failed_limit: int = 50
    ) -> Dict[str, Any]:
        """Get compact status counts plus failing tasks for a patch.

        Args:
            patch_id: Patch identifier
            failed_statuses: Statuses treated as failures (the only tasks whose
                per-task detail is fetched)
            failed_limit: Maximum number of failing tasks to fetch

        Returns:
            Patch dictionary with version.taskStatusStats and failing tasks
        """
        variables = {
            "patchId": patch_id,
            "failedStatuses": failed_statuses,
            "failedLimit": failed_limit,
        }
        result = await self._execute_query(GET_PATCH_STATUS_SUMMARY, variables)
        patch = result.get("patch")

        if not patch:
            raise Exception(f"Patch not found: {patch_id}")

        return patch

    async def get_version_status_summary(
        self, version_id: str, failed_statuses: List[str], failed_limit: int = 50
    ) -> Dict[str, Any]:
        """Get compact status counts plus failing tasks for a version.

        Args:
            version_id: Version identifier
            failed_statuses: Statuses treated as failures
            failed_limit: Maximum number of failing tasks to fetch

        Returns:
            Version dictionary with taskStatusStats and failing tasks
        """
        variables = {
            "versionId": version_id,
            "failedStatuses": failed_statuses,
            "failedLimit": failed_limit,
        }
        result = await self._execute_query(GET_VERSION_STATUS_SUMMARY, variables)
        version = result.get("version")

        if not version:
            raise Exception(f"Version not found: {version_id}")

        return version

    async def get_patch_tasks(
        self, patch_id: str, statuses: List[str], limit: int = 100
    ) -> Dict[str, Any]:
        """Get tasks for a specific patch, filtered by status.

        Args:
            patch_id: Patch identifier
            statuses: List of statuses to filter by. Empty list returns all tasks.
            limit: Maximum number of tasks to fetch from the API.

        Returns:
            Patch with tasks dictionary
        """
        variables = {"patchId": patch_id, "statuses": statuses, "limit": limit}
        result = await self._execute_query(GET_PATCH_TASKS, variables)
        patch = result.get("patch")

        if not patch:
            raise Exception(f"Patch not found: {patch_id}")

        version = patch_version(patch) or {}
        task_count = version.get("tasks", {}).get("count", 0)
        logger.info("Retrieved patch %s with %s tasks", patch_id, task_count)
        return patch

    async def get_version_tasks(
        self, version_id: str, statuses: List[str], limit: int = 100
    ) -> Dict[str, Any]:
        """Get tasks for a specific version, filtered by status.

        Args:
            version_id: Version identifier
            statuses: List of statuses to filter by. Empty list returns all tasks.
            limit: Maximum number of tasks to fetch from the API.

        Returns:
            Version with tasks dictionary
        """
        variables = {"versionId": version_id, "statuses": statuses, "limit": limit}
        result = await self._execute_query(GET_VERSION_TASKS, variables)

        version = result.get("version")
        if not version:
            raise Exception(f"Version not found: {version_id}")

        task_count = version.get("tasks", {}).get("count", 0)
        logger.info("Retrieved version %s with %s tasks", version_id, task_count)
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

    async def get_waterfall(self, options: Dict[str, Any]) -> Dict[str, Any]:
        """Fetch the project waterfall (build variants × versions grid).

        Args:
            options: WaterfallOptions input as a dict (projectIdentifier required).

        Returns:
            Waterfall payload with versions and pagination.
        """
        variables = {"options": options}
        result = await self._execute_query(GET_WATERFALL, variables)
        waterfall = result.get("waterfall") or {}
        version_count = len(waterfall.get("versions") or [])
        logger.info(
            "Retrieved waterfall for %s: %s versions",
            options.get("projectIdentifier"),
            version_count,
        )
        return waterfall

    async def get_mainline_commits(self, options: Dict[str, Any]) -> Dict[str, Any]:
        """Fetch mainline commits (version metadata only) for an order range.

        Uses a lean selection set on the same `waterfall(options:)` schema as
        get_waterfall, omitting waterfallBuilds so the payload stays small
        for change-point / regression-analysis use cases.

        Args:
            options: WaterfallOptions input as a dict (projectIdentifier required).

        Returns:
            Payload with versions and pagination (no waterfallBuilds).
        """
        variables = {"options": options}
        result = await self._execute_query(GET_MAINLINE_COMMITS, variables)
        waterfall = result.get("waterfall") or {}
        version_count = len(waterfall.get("versions") or [])
        logger.info(
            "Retrieved mainline commits for %s: %s versions",
            options.get("projectIdentifier"),
            version_count,
        )
        return waterfall

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

    async def schedule_tasks(
        self, version_id: str, task_ids: List[str]
    ) -> List[Dict[str, Any]]:
        """Schedule (activate) previously-unscheduled tasks on a version.

        Args:
            version_id: Version (or patch version) identifier owning the tasks.
            task_ids: Task identifiers to schedule. Evergreen requires
                TASKS:EDIT permission on the project.

        Returns:
            List of Task dicts that were scheduled. Evergreen silently drops
            IDs it can't act on, so the result may be shorter than the input.
        """
        variables = {"versionId": version_id, "taskIds": task_ids}
        result = await self._execute_query(SCHEDULE_TASKS, variables)
        scheduled = result.get("scheduleTasks") or []
        logger.info(
            "Scheduled %s/%s tasks on version %s",
            len(scheduled),
            len(task_ids),
            version_id,
        )
        return scheduled

    async def get_patch_configure(self, patch_id: str) -> Dict[str, Any]:
        """Read a patch's current description, parameters, and variant/task pairs.

        Used before schedulePatch to compute the full union of variant/task
        selections (the mutation replaces `variantsTasks` rather than merging).

        Args:
            patch_id: The patch (version) identifier.

        Returns:
            The `patch` dict with `description`, `status`, `activated`,
            `variantsTasks` ([{name, tasks}]), and `parameters` ([{key, value}]).
        """
        variables = {"patchId": patch_id}
        result = await self._execute_query(GET_PATCH_CONFIGURE, variables)
        return result.get("patch") or {}

    async def schedule_patch(
        self, patch_id: str, configure: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Reconfigure an existing patch, adding/scheduling variant/task pairs.

        Wraps the `schedulePatch(patchId, configure)` mutation used by Spruce's
        "Configure Patch" flow. Requires PATCHES:EDIT permission.

        Args:
            patch_id: The patch (version) to reconfigure.
            configure: A `PatchConfigure` input dict (`description`,
                `variantsTasks`, optional `parameters`/`patchTriggerAliases`).

        Returns:
            The resulting Patch dict (`id`, `status`, `activated`,
            `variantsTasks`).
        """
        variables = {"patchId": patch_id, "configure": configure}
        result = await self._execute_query(SCHEDULE_PATCH, variables)
        patch = result.get("schedulePatch") or {}
        logger.info(
            "Reconfigured patch %s with %s variant/task group(s)",
            patch_id,
            len(configure.get("variantsTasks") or []),
        )
        return patch

    async def restart_task(
        self, task_id: str, failed_only: bool
    ) -> Dict[str, Any]:
        """Restart a single finished task, producing a new execution.

        Args:
            task_id: Task identifier to restart.
            failed_only: For display tasks, restart only failed execution
                tasks. No effect on non-display tasks.

        Returns:
            The Task dict returned by Evergreen after the restart.
        """
        variables = {"taskId": task_id, "failedOnly": failed_only}
        result = await self._execute_query(RESTART_TASK, variables)
        task = result.get("restartTask") or {}
        logger.info("Restarted task %s (failed_only=%s)", task_id, failed_only)
        return task

    async def restart_versions(
        self, version_id: str, abort: bool, task_ids: List[str]
    ) -> List[Dict[str, Any]]:
        """Restart tasks on a version.

        Args:
            version_id: Version (or patch version) identifier to restart.
            abort: If True, abort in-progress tasks and queue them for reset
                before restarting completed tasks.
            task_ids: Specific task IDs to restart. An empty list means
                "restart all completed tasks on the version".

        Returns:
            List of Version dicts reflecting post-restart state.
        """
        variables = {
            "versionId": version_id,
            "abort": abort,
            "versionsToRestart": [
                {"versionId": version_id, "taskIds": task_ids}
            ],
        }
        result = await self._execute_query(RESTART_VERSIONS, variables)
        versions = result.get("restartVersions") or []
        logger.info(
            "Restarted version %s (abort=%s, explicit_task_count=%s)",
            version_id,
            abort,
            len(task_ids),
        )
        return versions

    async def __aenter__(self):
        """Async context manager entry"""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        _ = exc_type, exc_val, exc_tb  # Unused but required by protocol
        await self.close()
        return None
