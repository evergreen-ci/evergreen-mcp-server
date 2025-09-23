"""Failed jobs tools for Evergreen MCP server

This module provides the core logic for fetching user patches and failed jobs from Evergreen.
It uses a patch-based approach focused on the authenticated user's recent patches.
"""

from typing import Dict, List, Any, Optional
import logging
import json

logger = logging.getLogger(__name__)


async def fetch_user_recent_patches(
    client,
    user_id: str,
    limit: int = 10
) -> Dict[str, Any]:
    """Fetch recent patches for the authenticated user

    Args:
        client: Evergreen GraphQL client
        user_id: User identifier (typically email)
        limit: Number of patches to return (default: 10, max: 50)

    Returns:
        Dictionary containing user's recent patches
    """
    try:
        logger.info(f"Fetching {limit} recent patches for user {user_id}")

        # Get user's recent patches
        patches = await client.get_user_recent_patches(user_id, limit)

        # Process and format patches
        processed_patches = []
        for patch in patches:
            patch_info = {
                "patch_id": patch.get('id'),
                "patch_number": patch.get('patchNumber'),
                "githash": patch.get('githash'),
                "description": patch.get('description'),
                "author": patch.get('author'),
                "author_display_name": patch.get('authorDisplayName'),
                "status": patch.get('status'),
                "create_time": patch.get('createTime'),
                "project_identifier": patch.get('projectIdentifier'),
                "has_version": patch.get('versionFull') is not None,
                "version_status": patch.get('versionFull', {}).get('status') if patch.get('versionFull') else None
            }
            processed_patches.append(patch_info)

        logger.info(f"Successfully processed {len(processed_patches)} patches")

        return {
            "user_id": user_id,
            "patches": processed_patches,
            "total_patches": len(processed_patches)
        }

    except Exception as e:
        logger.error(f"Error fetching user patches: {e}")
        return {
            "error": str(e),
            "user_id": user_id,
            "patches": [],
            "total_patches": 0
        }


async def fetch_patch_failed_jobs(
    client,
    patch_id: str,
    max_results: int = 50
) -> Dict[str, Any]:
    """Fetch failed jobs for a specific patch

    Args:
        client: Evergreen GraphQL client
        patch_id: Patch identifier
        max_results: Maximum number of failed tasks to return

    Returns:
        Dictionary containing patch info and failed jobs data
    """
    try:
        logger.info(f"Fetching failed jobs for patch {patch_id}")

        # Get patch with failed tasks
        patch = await client.get_patch_failed_tasks(patch_id)

        # Extract patch information
        patch_info = {
            "patch_id": patch.get('id'),
            "patch_number": patch.get('patchNumber'),
            "githash": patch.get('githash'),
            "description": patch.get('description'),
            "author": patch.get('author'),
            "author_display_name": patch.get('authorDisplayName'),
            "status": patch.get('status'),
            "create_time": patch.get('createTime'),
            "project_identifier": patch.get('projectIdentifier')
        }

        # Extract version and tasks information
        version = patch.get('versionFull', {})
        version_info = {
            "version_id": version.get('id'),
            "revision": version.get('revision'),
            "author": version.get('author'),
            "create_time": version.get('createTime'),
            "status": version.get('status')
        } if version else None

        # Process failed tasks
        tasks_data = version.get('tasks', {}) if version else {}
        failed_tasks = tasks_data.get('data', [])
        total_count = tasks_data.get('count', 0)

        processed_tasks = []
        build_variants = set()
        has_timeouts = False

        for task in failed_tasks[:max_results]:  # Limit results
            # Extract key information
            task_info = {
                "task_id": task.get('id'),
                "task_name": task.get('displayName'),
                "build_variant": task.get('buildVariant'),
                "status": task.get('status'),
                "execution": task.get('execution', 0),
                "finish_time": task.get('finishTime'),
                "duration_ms": task.get('timeTaken'),
            }

            # Add failure details if available
            details = task.get('details', {})
            if details:
                task_info["failure_details"] = {
                    "description": details.get('description'),
                    "timed_out": details.get('timedOut', False),
                    "timeout_type": details.get('timeoutType'),
                    "failing_command": details.get('failingCommand')
                }

                if details.get('timedOut'):
                    has_timeouts = True

            # Add log links
            logs = task.get('logs', {})
            if logs:
                task_info["logs"] = {
                    "task_log": logs.get('taskLogLink'),
                    "agent_log": logs.get('agentLogLink'),
                    "system_log": logs.get('systemLogLink'),
                    "all_logs": logs.get('allLogLink')
                }

            processed_tasks.append(task_info)
            build_variants.add(task.get('buildVariant'))

        # Create summary
        summary = {
            "total_failed_tasks": total_count,
            "returned_tasks": len(processed_tasks),
            "failed_build_variants": sorted(list(build_variants)),
            "has_timeouts": has_timeouts
        }

        logger.info(f"Successfully processed {len(processed_tasks)} failed tasks for patch {patch_id}")

        return {
            "patch_info": patch_info,
            "version_info": version_info,
            "failed_tasks": processed_tasks,
            "summary": summary
        }

    except Exception as e:
        logger.error(f"Error fetching failed jobs for patch {patch_id}: {e}")
        return {
            "error": str(e),
            "patch_id": patch_id,
            "patch_info": None,
            "version_info": None,
            "failed_tasks": [],
            "summary": {
                "total_failed_tasks": 0,
                "error": str(e)
            }
        }


async def fetch_task_logs(client, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch detailed logs for a specific task
    
    Args:
        client: EvergreenGraphQLClient instance
        arguments: Tool arguments containing task_id, execution, max_lines, filter_errors
        
    Returns:
        Dictionary containing task logs
    """
    try:
        # Extract and validate arguments
        task_id = arguments.get('task_id')
        if not task_id:
            raise ValueError("task_id parameter is required")
        
        execution = arguments.get('execution', 0)
        max_lines = arguments.get('max_lines', 1000)
        filter_errors = arguments.get('filter_errors', True)
        
        # Fetch task logs
        task_data = await client.get_task_logs(task_id, execution)
        
        # Process logs
        raw_logs = task_data.get('taskLogs', {}).get('taskLogs', [])
        processed_logs = process_logs(raw_logs, max_lines, filter_errors)
        
        result = {
            'task_id': task_id,
            'execution': execution,
            'task_name': task_data.get('displayName'),
            'log_type': 'task',
            'total_lines': len(processed_logs),
            'logs': processed_logs,
            'truncated': len(processed_logs) >= max_lines
        }
        
        logger.info(f"Successfully fetched {len(processed_logs)} log entries for task {task_id}")
        return result
        
    except Exception as e:
        logger.error(f"Failed to fetch task logs: {e}")
        raise


def process_logs(raw_logs: List[Dict[str, Any]], max_lines: int, filter_errors: bool) -> List[Dict[str, Any]]:
    """Process and filter task log data based on parameters
    
    Args:
        raw_logs: Raw log entries from GraphQL
        max_lines: Maximum number of log lines to return
        filter_errors: Whether to filter for error/failure messages only
        
    Returns:
        Processed and filtered log entries
    """
    # Filter for errors if requested
    if filter_errors:
        filtered_logs = []
        for log in raw_logs:
            severity = log.get('severity', '').lower()
            message = log.get('message', '').lower()
            
            # Include if severity indicates error or message contains error/fail keywords
            if (severity in ['error', 'fatal'] or 
                'error' in message or 
                'fail' in message or
                'exception' in message):
                filtered_logs.append(log)
        
        raw_logs = filtered_logs

    # Sort by timestamp and limit
    try:
        sorted_logs = sorted(raw_logs, key=lambda x: x.get('timestamp', ''))
    except (TypeError, ValueError):
        # If timestamp sorting fails, use original order
        sorted_logs = raw_logs
    
    return sorted_logs[:max_lines]


def format_error_response(error_message: str, suggestions: List[str] = None) -> Dict[str, Any]:
    """Format a standardized error response
    
    Args:
        error_message: Main error message
        suggestions: Optional list of suggestions for the user
        
    Returns:
        Formatted error response dictionary
    """
    response = {"error": error_message}
    if suggestions:
        response["suggestions"] = suggestions
    return response
