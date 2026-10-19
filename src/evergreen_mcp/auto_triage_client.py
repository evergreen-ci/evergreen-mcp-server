"""Client for the auto-triage service.

auto-triage (https://auto-triage.devprod-evergreen.prod.corp.mongodb.com) runs a
richer log-analysis pipeline than the built-in `scan_log_for_errors` summarizer:
it discovers error templates, builds a causation graph, and produces a root-cause
triage with suggested ARR regexes.

The MCP forwards its own OIDC bearer token so auto-triage can fetch the logs from
Evergreen on the user's behalf (mesh-to-mesh, corp endpoint) — the same token the
MCP already uses to talk to Evergreen. Callers treat this as best-effort: on any
failure they fall back to the rudimentary REST-scan path.
"""

import logging
import os
from typing import Any, Dict, Optional

import aiohttp

logger = logging.getLogger(__name__)

__version__ = "0.1.0"

DEFAULT_AUTO_TRIAGE_URL = "https://auto-triage.devprod-evergreen.prod.corp.mongodb.com"

# analyze routes are mounted under this prefix (see auto_triage.main).
_API_PREFIX = "/api/v1"

# Analysis runs the full pipeline (parsing + embeddings + LLM triage), so it can
# take a while for large logs. Keep the ceiling generous but bounded.
DEFAULT_TIMEOUT_SECONDS = 300


class AutoTriageError(Exception):
    """Raised when the auto-triage service cannot produce a result."""


def auto_triage_base_url() -> str:
    """Resolve the auto-triage base URL from the environment (with a prod default)."""
    return os.environ.get("AUTO_TRIAGE_URL", DEFAULT_AUTO_TRIAGE_URL).rstrip("/")


def auto_triage_enabled() -> bool:
    """Whether auto-triage routing is enabled (default on; opt out via env)."""
    return os.environ.get("AUTO_TRIAGE_ENABLED", "1").lower() not in (
        "0",
        "false",
        "no",
    )


async def _post(path: str, bearer_token: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """POST to an auto-triage endpoint, forwarding the OIDC bearer token."""
    url = f"{auto_triage_base_url()}{_API_PREFIX}{path}"
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "User-Agent": f"evergreen-mcp/{__version__}",
        "Accept": "application/json",
    }
    timeout = aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT_SECONDS)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise AutoTriageError(
                        f"auto-triage {path} returned {resp.status}: {body[:500]}"
                    )
                return await resp.json()
    except AutoTriageError:
        raise
    except Exception as e:
        raise AutoTriageError(f"auto-triage request to {url} failed: {e}") from e


async def analyze_task_log(
    task_id: str,
    execution: int,
    bearer_token: Optional[str],
    log_type: str = "task_log",
) -> Dict[str, Any]:
    """Run auto-triage against a single task-level log stream.

    Raises AutoTriageError if triage is unavailable (no bearer token, service
    down, non-200, etc.) so the caller can fall back to the rudimentary scan.
    """
    if not bearer_token:
        raise AutoTriageError("no OIDC bearer token available to call auto-triage")
    return await _post(
        f"/analyze/task/{task_id}/log/{log_type}",
        bearer_token,
        {"execution": execution},
    )


async def analyze_task_test(
    task_id: str,
    execution: int,
    test_name: str,
    bearer_token: Optional[str],
) -> Dict[str, Any]:
    """Run auto-triage against a single failed test's log.

    Raises AutoTriageError if triage is unavailable so the caller can fall back.
    """
    if not bearer_token:
        raise AutoTriageError("no OIDC bearer token available to call auto-triage")
    return await _post(
        f"/analyze/task/{task_id}/test/{test_name}",
        bearer_token,
        {"execution": execution},
    )
