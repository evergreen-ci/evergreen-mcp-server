"""GraphQL client for Evergreen API

This module provides a GraphQL client for interacting with the Evergreen CI/CD platform.
It handles authentication, connection management, and query execution.
"""

from typing import List, Dict, Any, Optional
import logging
import aiohttp
from gql import Client, gql
from gql.transport.aiohttp import AIOHTTPTransport
from gql.transport.exceptions import TransportError

try:
    from .evergreen_queries import (
        GET_PROJECTS, GET_PROJECT, GET_PROJECT_SETTINGS,
        GET_USER_RECENT_PATCHES, GET_PATCH_FAILED_TASKS, GET_TASK_LOGS
    )
except ImportError:
    # For standalone usage
    from evergreen_queries import (
        GET_PROJECTS, GET_PROJECT, GET_PROJECT_SETTINGS,
        GET_USER_RECENT_PATCHES, GET_PATCH_FAILED_TASKS, GET_TASK_LOGS
    )

logger = logging.getLogger(__name__)


class EvergreenGraphQLClient:
    """GraphQL client for Evergreen API
    
    This client provides async methods for querying the Evergreen GraphQL API.
    It handles authentication via API keys and manages the connection lifecycle.
    """
    
    def __init__(self, user: str, api_key: str, endpoint: str = None):
        """Initialize the GraphQL client
        
        Args:
            user: Evergreen username
            api_key: Evergreen API key
            endpoint: GraphQL endpoint URL (defaults to Evergreen's main instance)
        """
        self.user = user
        self.api_key = api_key
        self.endpoint = endpoint or "https://evergreen.mongodb.com/graphql/query"
        self._client = None
        
    async def connect(self):
        """Initialize GraphQL client connection"""
        headers = {
            "Api-User": self.user,
            "Api-Key": self.api_key,
            "Content-Type": "application/json",
        }

        logger.debug(f"Connecting to GraphQL endpoint: {self.endpoint}")

        # Create transport with headers directly
        transport = AIOHTTPTransport(url=self.endpoint, headers=headers)
        self._client = Client(transport=transport)

        logger.info("GraphQL client connected successfully")
        
    async def close(self):
        """Close client connections"""
        if self._client:
            try:
                # Close the transport if it has a close method
                if hasattr(self._client.transport, 'close'):
                    await self._client.transport.close()
                logger.debug("GraphQL client closed")
            except Exception as e:
                logger.warning(f"Error closing GraphQL client: {e}")
            
    async def _execute_query(self, query_string: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute a GraphQL query with error handling
        
        Args:
            query_string: GraphQL query string
            variables: Query variables
            
        Returns:
            Query result data
            
        Raises:
            Exception: If query execution fails
        """
        if not self._client:
            raise RuntimeError("Client not connected. Call connect() first.")
            
        try:
            query = gql(query_string)
            result = await self._client.execute_async(query, variable_values=variables)
            logger.debug(f"Query executed successfully: {len(str(result))} chars returned")
            return result
        except TransportError as e:
            logger.error(f"GraphQL transport error: {e}")
            raise Exception(f"Failed to execute GraphQL query: {e}")
        except Exception as e:
            logger.error(f"GraphQL query execution error: {e}")
            raise
            
    async def get_projects(self) -> List[Dict[str, Any]]:
        """Get all projects from Evergreen
        
        Returns:
            List of project dictionaries with flattened structure
        """
        result = await self._execute_query(GET_PROJECTS)
        
        # Flatten grouped projects into simple list
        projects = []
        for group in result.get('projects', []):
            projects.extend(group.get('projects', []))
            
        logger.info(f"Retrieved {len(projects)} projects")
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
        
        project = result.get('project')
        if not project:
            raise Exception(f"Project not found: {project_id}")
            
        logger.info(f"Retrieved project details for: {project.get('displayName', project_id)}")
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
        
        settings = result.get('projectSettings')
        if not settings:
            raise Exception(f"Project settings not found: {project_id}")
            
        logger.info(f"Retrieved project settings for: {project_id}")
        return settings
    

    async def get_user_recent_patches(self, user_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent patches for the authenticated user

        Args:
            user_id: User identifier (typically email)
            limit: Number of patches to return (default: 10, max: 50)

        Returns:
            List of patch dictionaries
        """
        variables = {"userId": user_id, "limit": min(limit, 50)}  # Cap at 50 for performance

        try:
            result = await self._execute_query(GET_USER_RECENT_PATCHES, variables)
            patches = result.get('user', {}).get('patches', {}).get('patches', [])

            logger.info(f"Retrieved {len(patches)} recent patches for user {user_id}")
            return patches

        except Exception as e:
            logger.error(f"Error fetching recent patches for user {user_id}: {e}")
            raise

    async def get_patch_failed_tasks(self, patch_id: str) -> Dict[str, Any]:
        """Get failed tasks for a specific patch

        Args:
            patch_id: Patch identifier

        Returns:
            Patch with failed tasks dictionary
        """
        variables = {"patchId": patch_id}

        try:
            result = await self._execute_query(GET_PATCH_FAILED_TASKS, variables)
            patch = result.get('patch')

            if not patch:
                raise Exception(f"Patch not found: {patch_id}")

            # Count failed tasks
            version = patch.get('versionFull', {})
            failed_count = version.get('tasks', {}).get('count', 0)

            logger.info(f"Retrieved patch {patch_id} with {failed_count} failed tasks")
            return patch

        except Exception as e:
            logger.error(f"Error fetching failed tasks for patch {patch_id}: {e}")
            raise

    async def get_version_with_failed_tasks(self, version_id: str) -> Dict[str, Any]:
        """Get version with failed tasks only

        Args:
            version_id: Version identifier

        Returns:
            Version with failed tasks dictionary
        """
        variables = {"versionId": version_id}
        result = await self._execute_query(GET_VERSION_WITH_FAILED_TASKS, variables)

        version = result.get('version')
        if not version:
            raise Exception(f"Version not found: {version_id}")

        failed_count = version.get('tasks', {}).get('count', 0)
        logger.info(f"Retrieved version {version_id} with {failed_count} failed tasks")
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

        task = result.get('task')
        if not task:
            raise Exception(f"Task not found: {task_id}")

        logs_count = len(task.get('taskLogs', {}).get('taskLogs', []))
        logger.info(f"Retrieved {logs_count} log entries for task {task_id}")
        return task

    async def __aenter__(self):
        """Async context manager entry"""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.close()
