"""FastMCP server for Evergreen

This module provides the main MCP server using FastMCP framework.
It handles server lifecycle, configuration, and tool registration.
"""

import argparse
import asyncio
import json
import logging
import os
import os.path
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

import yaml
from fastmcp import Context, FastMCP
from fastmcp.server.providers.skills import SkillsDirectoryProvider

from evergreen_mcp import __version__
from evergreen_mcp.evergreen_graphql_client import EvergreenGraphQLClient
from evergreen_mcp.evergreen_rest_client import EvergreenRestClient
from evergreen_mcp.mcp_tools import register_tools
from evergreen_mcp.oidc_auth import OIDCAuthenticationError, OIDCAuthManager

# Set up logging — stderr at INFO, per-PID debug file for auth diagnosis
logging.basicConfig(level=logging.INFO)
_log_file = Path.home() / "logs" / f"evergreen-mcp-{os.getpid()}.log"
_log_file.parent.mkdir(exist_ok=True)
_fh = logging.FileHandler(_log_file)
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(logging.Formatter("%(asctime)s [%(process)d] %(name)s %(levelname)s %(message)s"))
logging.getLogger("evergreen_mcp").addHandler(_fh)
logging.getLogger("evergreen_mcp").setLevel(logging.DEBUG)
logger = logging.getLogger(__name__)
logger.info("Debug log: %s", _log_file)


@dataclass
class EvergreenContext:
    """Context object holding the Evergreen client and configuration."""

    client: EvergreenGraphQLClient
    api_client: EvergreenRestClient
    user_id: str
    default_project_id: str | None = None
    workspace_dir: str | None = None
    projects_for_directory: dict = field(default_factory=dict)


def detect_project_from_workspace(
    config_data: dict, workspace_dir: str = None
) -> str | None:
    """Detect project ID from workspace directory using Evergreen config

    Args:
        config_data: Parsed ~/.evergreen.yml configuration
        workspace_dir: Workspace directory path (optional)

    Returns:
        Detected project ID or None if no match found
    """
    if not workspace_dir:
        workspace_dir = os.getenv("WORKSPACE_PATH") or os.getenv("PWD") or os.getcwd()

    if not workspace_dir:
        logger.debug("No workspace directory available for project detection")
        return None

    workspace_dir = os.path.abspath(os.path.expanduser(workspace_dir))

    projects_for_directory = config_data.get("projects_for_directory", {})

    if not projects_for_directory:
        logger.debug("No projects_for_directory section in config")
        return None

    logger.debug("Checking workspace: %s", workspace_dir)
    logger.debug("Available project mappings: %s", projects_for_directory)

    if workspace_dir in projects_for_directory:
        project_id = projects_for_directory[workspace_dir]
        logger.info("Exact match found: %s -> %s", workspace_dir, project_id)
        return project_id

    best_match = None
    best_match_len = 0

    for config_path, project_id in projects_for_directory.items():
        config_path = os.path.abspath(os.path.expanduser(config_path))

        try:
            common = os.path.commonpath([workspace_dir, config_path])
            if common == config_path and len(config_path) > best_match_len:
                best_match = project_id
                best_match_len = len(config_path)
        except ValueError:
            continue

    if best_match:
        logger.info("Parent directory match found: %s", best_match)
        return best_match

    logger.debug("No project match found for workspace: %s", workspace_dir)
    return None


async def load_evergreen_config() -> tuple[dict, str | None, OIDCAuthManager | None]:
    """Load Evergreen configuration from environment or config file.

    Returns:
        Tuple of (config dict, default project ID, auth_manager if using OIDC)
    """
    # Check for environment variables first (Docker setup)
    evergreen_user = os.getenv("EVERGREEN_USER")
    evergreen_api_key = os.getenv("EVERGREEN_API_KEY")
    evergreen_project = os.getenv("EVERGREEN_PROJECT")
    workspace_dir = os.getenv("WORKSPACE_PATH")

    auth_manager = None
    evergreen_config = {}

    # Load projects_for_directory from ~/.evergreen.yml for auto-detection
    # This is needed regardless of auth method
    projects_for_directory = {}
    evergreen_yml_path = os.path.expanduser("~/.evergreen.yml")
    try:
        with open(evergreen_yml_path) as f:
            full_config = yaml.safe_load(f) or {}
            projects_for_directory = full_config.get("projects_for_directory", {})
    except Exception:
        pass  # Config file may not exist or be readable

    if evergreen_user and evergreen_api_key:
        # Use environment variables (Docker setup)
        logger.info("Using environment variables for Evergreen configuration")
        evergreen_config = {
            "user": evergreen_user,
            "api_key": evergreen_api_key,
            "auth_method": "api_key",
            "projects_for_directory": projects_for_directory,
        }
    elif os.getenv("EVERGREEN_AUTH_MODE") == "per_request":
        # No default credentials — per-request api_user/api_key are required
        # on every tool call. Used by MCP gateways that inject per-user
        # credentials from a credential store.
        logger.info("Using per-request authentication (no default credentials)")
        evergreen_config = {
            "user": "",
            "auth_method": "per_request",
            "projects_for_directory": projects_for_directory,
        }
    else:
        # OIDC Authentication (always - no API key fallback)
        logger.info("Using OIDC authentication...")
        auth_manager = OIDCAuthManager()

        # Use ensure_authenticated() which handles the full flow:
        # token file check → refresh → device flow
        if not await auth_manager.ensure_authenticated():
            raise OIDCAuthenticationError("OIDC authentication failed")

        logger.info("Authenticated as: %s", auth_manager.user_id)

        evergreen_config = {
            "user": auth_manager.user_id,
            "bearer_token": auth_manager.access_token,
            "auth_method": "oidc",
            "projects_for_directory": projects_for_directory,
        }

    # Determine default project ID
    default_project_id = None

    # Try auto-detection from workspace
    detected_project = detect_project_from_workspace(evergreen_config, workspace_dir)
    if detected_project:
        default_project_id = detected_project
        logger.info("Auto-detected project ID from workspace: %s", default_project_id)

    # Fall back to EVERGREEN_PROJECT environment variable
    if not default_project_id and evergreen_project:
        default_project_id = evergreen_project
        logger.info("Using project ID from environment: %s", default_project_id)

    return evergreen_config, default_project_id, auth_manager


_PROACTIVE_REFRESH_BEFORE_S = 0  # fire only after AT expires; devprod refreshes first (at 30s)
_CONSECUTIVE_FAILURE_LIMIT = 3
_FILE_CHECK_INTERVAL_S = 60  # how often to poll the shared file while sleeping


async def _proactive_refresh_loop(auth_manager: "OIDCAuthManager") -> None:
    """Background task: keep the in-memory access token up-to-date.

    With _PROACTIVE_REFRESH_BEFORE_S=0 this loop sleeps for the full AT
    lifetime and wakes up only after the AT expires. By that point
    devprod-mcp-proxy has already refreshed (it fires at 30 s before expiry)
    and written a fresh token to the shared file. refresh_token(0) reads the
    file under the lock, finds a valid token, and short-circuits — no HTTP
    call, no RT consumed, no race with devprod.

    While sleeping, the loop polls the shared file every _FILE_CHECK_INTERVAL_S
    seconds. If devprod writes a newer token mid-sleep, we pick it up and
    extend the sleep — preventing the expiry-boundary race entirely.

    If the file also has an expired token (devprod not running), refresh_token
    makes a real HTTP call. After _CONSECUTIVE_FAILURE_LIMIT consecutive
    failures it falls back to ensure_authenticated() which triggers device flow.
    """
    consecutive_failures = 0
    while True:
        try:
            if auth_manager.access_token:
                is_valid, remaining = auth_manager._check_token_expiry(
                    {"access_token": auth_manager.access_token}
                )
                if remaining > _PROACTIVE_REFRESH_BEFORE_S:
                    sleep_for = max(remaining - _PROACTIVE_REFRESH_BEFORE_S, 30)
                    logger.debug(
                        "Proactive refresh: %ds remaining, sleeping %ds",
                        remaining, sleep_for,
                    )
                    # Sleep in chunks, picking up fresher tokens written by
                    # devprod or other processes between cycles.
                    while sleep_for > 0:
                        chunk = min(_FILE_CHECK_INTERVAL_S, sleep_for)
                        await asyncio.sleep(chunk)
                        sleep_for -= chunk
                        token_data = auth_manager._read_token_file()
                        if token_data:
                            _, new_remaining = auth_manager._check_token_expiry(token_data)
                            if new_remaining > remaining:
                                auth_manager._access_token = token_data["access_token"]
                                new_sleep = max(
                                    new_remaining - _PROACTIVE_REFRESH_BEFORE_S, 30
                                )
                                logger.debug(
                                    "File watcher: newer token (%ds remaining),"
                                    " extending sleep to %ds",
                                    new_remaining, new_sleep,
                                )
                                sleep_for = new_sleep
                                remaining = new_remaining
                    continue

            logger.info("Proactive refresh: AT expired — reading from shared file")
            result = await auth_manager.refresh_token(min_remaining_s=_PROACTIVE_REFRESH_BEFORE_S)
            if result:
                consecutive_failures = 0
                _, new_remaining = auth_manager._check_token_expiry(
                    {"access_token": auth_manager.access_token}
                )
                sleep_for = max(new_remaining - _PROACTIVE_REFRESH_BEFORE_S, 30)
                logger.debug("Proactive refresh: %ds remaining, sleeping %ds", new_remaining, sleep_for)
                await asyncio.sleep(sleep_for)
            else:
                consecutive_failures += 1
                if consecutive_failures >= _CONSECUTIVE_FAILURE_LIMIT:
                    logger.warning(
                        "Proactive refresh: %d consecutive failures — triggering re-authentication",
                        consecutive_failures,
                    )
                    auth_manager._access_token = None  # bypass ensure_authenticated fast path
                    try:
                        await auth_manager.ensure_authenticated()
                        consecutive_failures = 0
                        logger.info("Proactive refresh: re-authentication succeeded")
                    except Exception as e:
                        logger.error(
                            "Proactive refresh: re-authentication failed: %s — retrying in 60s", e
                        )
                else:
                    logger.warning("Proactive refresh: failed — retrying in 60s")
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            logger.info("Proactive refresh task shutting down")
            return
        except Exception as e:
            logger.error("Proactive refresh error: %s — retrying in 60s", e)
            await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[EvergreenContext]:
    """Server lifespan manager - handles GraphQL client lifecycle.

    This context manager initializes the Evergreen GraphQL client on startup
    and ensures proper cleanup on shutdown.
    """
    evergreen_config, default_project_id, auth_manager = await load_evergreen_config()

    # Configurable endpoint URLs — per auth method, with defaults
    oidc_rest_url = os.getenv(
        "EVERGREEN_OIDC_REST_URL",
        "https://evergreen.corp.mongodb.com/rest/v2/",
    )
    oidc_graphql_url = os.getenv(
        "EVERGREEN_OIDC_GRAPHQL_URL",
        "https://evergreen.corp.mongodb.com/graphql/query",
    )
    api_key_rest_url = os.getenv(
        "EVERGREEN_API_KEY_REST_URL",
        "https://evergreen.mongodb.com/rest/v2/",
    )
    api_key_graphql_url = os.getenv(
        "EVERGREEN_API_KEY_GRAPHQL_URL",
        "https://evergreen.mongodb.com/graphql/query",
    )

    # Get workspace directory for intelligent project detection
    workspace_dir = os.getenv("WORKSPACE_PATH") or os.getenv("PWD") or os.getcwd()

    # Extract projects_for_directory config for runtime auto-detection
    projects_for_directory = evergreen_config.get("projects_for_directory", {})

    # Create client based on authentication method
    auth_method = evergreen_config.get("auth_method", "oidc")

    if auth_method == "oidc":
        logger.info("Initializing GraphQL client with OIDC Bearer token")
        # Use corp endpoint for OIDC authentication
        # Pass auth_manager to enable automatic token refresh
        client = EvergreenGraphQLClient(
            bearer_token=evergreen_config["bearer_token"],
            endpoint=oidc_graphql_url,
            auth_manager=auth_manager,
        )
        api_client = EvergreenRestClient(
            bearer_token=evergreen_config["bearer_token"],
            base_url=oidc_rest_url,
            auth_manager=auth_manager,
        )
    elif auth_method == "per_request":
        logger.info(
            "No default clients — per-request credentials required on every tool call"
        )
        client = None
        api_client = None
    else:
        logger.info("Initializing GraphQL client with API key")
        # These fields were validated during config loading
        client = EvergreenGraphQLClient(
            user=evergreen_config["user"],
            api_key=evergreen_config["api_key"],
            endpoint=api_key_graphql_url,
        )
        api_client = EvergreenRestClient(
            user=evergreen_config["user"],
            api_key=evergreen_config["api_key"],
            base_url=api_key_rest_url,
        )

    # Start proactive token refresh background task for OIDC auth
    refresh_task = None
    if auth_manager is not None:
        refresh_task = asyncio.create_task(_proactive_refresh_loop(auth_manager))
        logger.info("Proactive token refresh task started")

    # In per_request mode, there are no default clients to manage.
    if client is None:
        logger.info("Per-request auth mode — no default clients to initialize")
        try:
            yield EvergreenContext(
                api_client=None,
                client=None,
                user_id="",
                default_project_id=default_project_id,
                workspace_dir=workspace_dir,
                projects_for_directory=projects_for_directory,
            )
        finally:
            if refresh_task:
                refresh_task.cancel()
    else:
        async with client:
            logger.info("Evergreen GraphQL client initialized")
            logger.info("Authentication method: %s", auth_method)
            logger.info("Current workspace directory: %s", workspace_dir)
            if projects_for_directory:
                logger.info(
                    "Loaded %d project directory mappings from config",
                    len(projects_for_directory),
                )
            if default_project_id:
                logger.info("Default project ID configured: %s", default_project_id)
            else:
                logger.info(
                    "No default project ID configured - "
                    "tools will use intelligent auto-detection"
                )

            try:
                yield EvergreenContext(
                    api_client=api_client,
                    client=client,
                    user_id=evergreen_config["user"],
                    default_project_id=default_project_id,
                    workspace_dir=workspace_dir,
                    projects_for_directory=projects_for_directory,
                )
            finally:
                if refresh_task:
                    refresh_task.cancel()
                await api_client._close_session()
                logger.info("Evergreen REST client session closed")

        logger.info("Evergreen GraphQL client closed")


# Create the FastMCP server instance
mcp = FastMCP(
    "Evergreen MCP Server",
    version=__version__,
    lifespan=lifespan,
    instructions="""\
# Evergreen MCP Server — AI Agent Instructions

You are connected to the Evergreen CI/CD MCP server for MongoDB. This server \
provides tools to inspect patches, diagnose CI failures, and retrieve logs.

## On Startup
1. Call `get_inferred_project_ids_evergreen` to discover the user's active projects.
2. If multiple projects are returned, ask the user which one to use.
3. Use the chosen `project_id` in all subsequent tool calls.

## Available Skills (MCP Resources)
This server exposes detailed skill guides as `skill://` resources. \
Read these for in-depth tool documentation, debugging workflows, and Evergreen \
domain knowledge:
- `skill://tool-guide/SKILL.md` — Complete tool reference with parameters, \
return shapes, and decision tree for which tool to use when.
- `skill://debugging-workflow/SKILL.md` — Step-by-step workflows for \
diagnosing CI failures from patch to root cause.
- `skill://evergreen-concepts/SKILL.md` — Evergreen domain knowledge: \
hierarchy, statuses, terminology, and how to interpret results.

## Quick Tool Selection
- **"Check my CI"** → `list_user_recent_patches_evergreen`
- **"Why is it failing?"** → `get_patch_failed_jobs_evergreen` (needs patch_id)
- **"Which tests failed?"** → `get_task_test_results_evergreen` (needs task_id)
- **"Show me the logs"** → `get_task_logs_evergreen` (needs task_id)
- **"What projects do I have?"** → `get_inferred_project_ids_evergreen`

## Key Rule
Always progress from broad to narrow: Project → Patch → Task → Tests/Logs. \
Do not skip steps.
""",
)

# Register skills as MCP resources using the SkillsDirectoryProvider.
# Each subdirectory under skills/ with a SKILL.md becomes a discoverable
# skill resource at skill://<name>/SKILL.md.
_skills_dir = Path(__file__).parent / "skills"
mcp.add_provider(
    SkillsDirectoryProvider(roots=_skills_dir, supporting_files="resources")
)

register_tools(mcp)


# Register resources
@mcp.resource("evergreen://projects")
async def list_projects_resource(ctx: Context) -> str:
    """List all Evergreen projects as a resource."""
    evg_ctx = ctx.request_context.lifespan_context
    projects = await evg_ctx.client.get_projects()
    return json.dumps(
        [
            {
                "id": p.get("id"),
                "identifier": p.get("identifier"),
                "displayName": p.get("displayName"),
                "enabled": p.get("enabled"),
                "owner": p.get("owner"),
                "repo": p.get("repo"),
            }
            for p in projects
        ],
        indent=2,
    )


@mcp.prompt(
    name="intelligent-project-detection",
    description="How to use intelligent project ID auto-detection in Evergreen tools",
)
async def intelligent_project_detection_prompt() -> str:
    """Prompt explaining intelligent project ID auto-detection."""
    return """# Intelligent Project ID Auto-Detection

The Evergreen MCP tools automatically detect the correct project ID by analyzing \
the user's recent patch history.

## How It Works (when project_id is NOT specified):

1. **Single project** - If user only has patches in one project, use it \
automatically (High confidence).
2. **Multiple projects** - Selects the project with the **most recent activity** \
(Medium confidence).
   - Returns patches for that project.
   - Includes a list of other available projects in the response message.

## AI Agent Instructions:

**Step 1: Call the tool without project_id**
```
list_user_recent_patches_evergreen(limit=10)
```

**Step 2: Check the response**
- If patches are returned: **Success!**
- If the response mentions "Other active projects", inform the user:
  > "Showing patches for 'mms' (most recent). Also found activity in: \
mongodb-mongo-master, server."

**Step 3: If User Asks for Another Project**
- Call the tool again with the specific `project_id`:
```
list_user_recent_patches_evergreen(project_id="mongodb-mongo-master", limit=10)
```

## Tools with Auto-Detection:
- `list_user_recent_patches_evergreen`
- `get_patch_failed_jobs_evergreen`
"""


@mcp.prompt(
    name="debug-failed-patch",
    description=(
        "Step-by-step guide to investigate a failing CI patch. "
        "Use when the user says 'why is my patch failing?' or 'debug my CI'."
    ),
)
async def debug_failed_patch_prompt() -> str:
    """Prompt providing a step-by-step debugging workflow."""
    return """\
# Debug a Failed Patch — Step-by-Step

Follow these steps in order to investigate a CI failure:

## Step 1: Identify the project
```
get_inferred_project_ids_evergreen(max_patches=50)
```
If multiple projects, ask the user which one.

## Step 2: Find the failing patch
```
list_user_recent_patches_evergreen(project_id="<project>", limit=5)
```
Look for patches with status "failed". Note the patch_id.

## Step 3: Get failed tasks
```
get_patch_failed_jobs_evergreen(patch_id="<patch_id>")
```
Classify each failed task:
- failed_test_count > 0 → test failure → go to Step 4a
- timed_out == true → timeout → go to Step 4b
- No tests, no timeout → setup/infra → go to Step 4b

## Step 4a: Get test failures
```
get_task_test_results_evergreen(task_id="<task_id>", failed_only=True)
```
Report the failing test names and look for patterns.

## Step 4b: Get error logs
```
get_task_logs_evergreen(task_id="<task_id>", filter_errors=True)
```
Find the root cause error message.
If too few results, retry with filter_errors=False.

## Step 5: Synthesize
Present a clear diagnosis: what failed, why, and what the user should do.
"""


@mcp.prompt(
    name="check-ci-status",
    description=(
        "Quick CI health check. Use when the user asks "
        "'how is my CI?' or 'check my builds'."
    ),
)
async def check_ci_status_prompt() -> str:
    """Prompt for a quick CI status overview."""
    return """\
# Quick CI Status Check

## Step 1: Discover projects
```
get_inferred_project_ids_evergreen()
```

## Step 2: Get recent patches
```
list_user_recent_patches_evergreen(project_id="<project>", limit=5)
```

## Step 3: Summarize
Report a concise summary like:
> "Your last 5 patches: 3 succeeded, 1 failed (SERVER-12345), 1 running."

Only drill deeper if the user asks or if there are failures worth flagging.
"""


def main() -> None:
    """Main entry point for the FastMCP server."""
    logger.info("Starting Evergreen FastMCP Server v%s...", __version__)

    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Evergreen FastMCP Server")
    parser.add_argument(
        "--project-id",
        type=str,
        help="Default Evergreen project identifier (optional)",
    )
    parser.add_argument(
        "--workspace-dir",
        type=str,
        help="Workspace directory for auto-detecting project ID (optional)",
    )

    args = parser.parse_args()

    # Set workspace directory as environment variable if provided
    if args.workspace_dir:
        os.environ["WORKSPACE_PATH"] = args.workspace_dir
        logger.info("Using workspace directory: %s", args.workspace_dir)

    # Set project ID as environment variable if provided (takes precedence)
    if args.project_id:
        os.environ["EVERGREEN_PROJECT"] = args.project_id
        logger.info("Using explicit project ID: %s", args.project_id)

    logger.info("Starting FastMCP server...")
    mcp.run()


if __name__ == "__main__":
    main()
