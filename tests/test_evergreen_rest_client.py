"""
Unit tests for EvergreenRestClient.

Tests cover:
- Initialization and auth validation
- Header construction for bearer token and API key auth
- Lazy session creation and cleanup
- Token refresh flow
- Request routing, 401 retry logic, and response handling
- REST endpoint methods: get_task_logs, get_task_test_results,
  get_task_details, download_task_artifacts
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from evergreen_mcp.evergreen_rest_client import EvergreenRestClient

# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestInit(unittest.TestCase):
    """Test EvergreenRestClient.__init__ validation."""

    def test_init_with_bearer_token(self):
        client = EvergreenRestClient(bearer_token="tok-123")
        assert client.bearer_token == "tok-123"
        assert client.session is None

    def test_init_with_api_key(self):
        client = EvergreenRestClient(user="admin", api_key="key-456")
        assert client.user == "admin"
        assert client.api_key == "key-456"

    def test_init_with_auth_manager(self):
        mgr = MagicMock()
        mgr.access_token = "mgr-tok"
        client = EvergreenRestClient(auth_manager=mgr)
        assert client.bearer_token == "mgr-tok"
        assert client._auth_manager is mgr

    def test_init_auth_manager_does_not_override_explicit_bearer(self):
        mgr = MagicMock()
        mgr.access_token = "mgr-tok"
        client = EvergreenRestClient(bearer_token="explicit", auth_manager=mgr)
        assert client.bearer_token == "explicit"

    def test_init_no_auth_raises(self):
        with pytest.raises(ValueError, match="Either bearer_token"):
            EvergreenRestClient()

    def test_init_user_without_api_key_raises(self):
        with pytest.raises(ValueError):
            EvergreenRestClient(user="admin")

    def test_custom_base_url(self):
        client = EvergreenRestClient(
            bearer_token="tok", base_url="https://custom.api/v1/"
        )
        assert client.base_url == "https://custom.api/v1/"


# ---------------------------------------------------------------------------
# Headers
# ---------------------------------------------------------------------------


class TestGetHeaders(unittest.TestCase):
    """Test _get_headers returns correct auth headers."""

    def test_bearer_token_headers(self):
        client = EvergreenRestClient(bearer_token="tok-123")
        headers = client._get_headers()
        assert headers["Authorization"] == "Bearer tok-123"
        assert "Api-User" not in headers
        assert "Api-Key" not in headers

    def test_api_key_headers(self):
        client = EvergreenRestClient(user="admin", api_key="key-456")
        headers = client._get_headers()
        assert headers["Api-User"] == "admin"
        assert headers["Api-Key"] == "key-456"
        assert "Authorization" not in headers

    def test_user_agent_header(self):
        client = EvergreenRestClient(bearer_token="tok")
        headers = client._get_headers()
        assert headers["User-Agent"].startswith("evergreen-mcp/")

    def test_accept_json_header(self):
        client = EvergreenRestClient(bearer_token="tok")
        headers = client._get_headers()
        assert headers["Accept"] == "application/json"


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------


class TestSessionManagement(unittest.IsolatedAsyncioTestCase):
    """Test lazy session creation and cleanup."""

    async def test_get_session_creates_session(self):
        client = EvergreenRestClient(bearer_token="tok")
        assert client.session is None
        session = client._get_session()
        assert session is not None
        assert isinstance(session, aiohttp.ClientSession)
        await client._close_session()

    async def test_get_session_returns_same_session(self):
        client = EvergreenRestClient(bearer_token="tok")
        s1 = client._get_session()
        s2 = client._get_session()
        assert s1 is s2
        await client._close_session()

    async def test_close_session(self):
        client = EvergreenRestClient(bearer_token="tok")
        client.session = AsyncMock()
        await client._close_session()
        client.session is None

    async def test_close_session_noop_when_none(self):
        client = EvergreenRestClient(bearer_token="tok")
        await client._close_session()  # should not raise


# ---------------------------------------------------------------------------
# Token refresh
# ---------------------------------------------------------------------------


class TestTryRefreshToken(unittest.IsolatedAsyncioTestCase):
    """Test _try_refresh_token behavior."""

    async def test_refresh_succeeds(self):
        mgr = AsyncMock()
        mgr.access_token = "old-tok"
        mgr.refresh_token.return_value = {"access_token": "new-tok"}

        client = EvergreenRestClient(bearer_token="old-tok", auth_manager=mgr)
        client.session = AsyncMock()

        result = await client._try_refresh_token()
        assert result is True
        assert client.bearer_token == "new-tok"
        assert "Bearer new-tok" in client.headers["Authorization"]

    async def test_refresh_returns_false_without_auth_manager(self):
        client = EvergreenRestClient(bearer_token="tok")
        result = await client._try_refresh_token()
        assert result is False

    async def test_refresh_returns_false_on_exception(self):
        mgr = AsyncMock()
        mgr.access_token = "tok"
        mgr.refresh_token.side_effect = Exception("network error")

        client = EvergreenRestClient(bearer_token="tok", auth_manager=mgr)
        result = await client._try_refresh_token()
        assert result is False

    async def test_refresh_returns_false_when_no_token_data(self):
        mgr = AsyncMock()
        mgr.access_token = "tok"
        mgr.refresh_token.return_value = None

        client = EvergreenRestClient(bearer_token="tok", auth_manager=mgr)
        result = await client._try_refresh_token()
        assert result is False


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class TestRequest(unittest.IsolatedAsyncioTestCase):
    """Test _request method: URL routing, response handling, 401 retry."""

    def _make_client(self):
        return EvergreenRestClient(
            bearer_token="tok",
            base_url="https://api.example.com/v2/",
        )

    def _mock_response(
        self, status=200, json_data=None, text_data="", content_type="application/json"
    ):
        resp = AsyncMock()
        resp.status = status
        resp.headers = {"Content-Type": content_type}
        resp.json = AsyncMock(return_value=json_data)
        resp.text = AsyncMock(return_value=text_data)
        resp.raise_for_status = MagicMock()
        return resp

    async def test_request_relative_url(self):
        client = self._make_client()
        resp = self._mock_response(json_data={"key": "val"})

        mock_session = MagicMock()
        mock_session.request = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=resp),
                __aexit__=AsyncMock(return_value=False),
            )
        )
        client.session = mock_session

        result = await client._request("GET", "tasks/123")
        mock_session.request.assert_called_once_with(
            "GET", "https://api.example.com/v2/tasks/123"
        )
        assert result == {"status": "success", "data": {"key": "val"}}

    async def test_request_absolute_url(self):
        client = self._make_client()
        resp = self._mock_response(json_data={"key": "val"})

        mock_session = MagicMock()
        mock_session.request = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=resp),
                __aexit__=AsyncMock(return_value=False),
            )
        )
        client.session = mock_session

        await client._request("GET", "https://other.api/endpoint")
        mock_session.request.assert_called_once_with(
            "GET", "https://other.api/endpoint"
        )

    async def test_request_text_response(self):
        client = self._make_client()
        resp = self._mock_response(
            text_data="plain text log",
            content_type="text/plain",
        )

        mock_session = MagicMock()
        mock_session.request = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=resp),
                __aexit__=AsyncMock(return_value=False),
            )
        )
        client.session = mock_session

        result = await client._request("GET", "logs/abc")
        assert result == {"status": "success", "data": "plain text log"}

    async def test_request_401_triggers_retry(self):
        client = self._make_client()

        # First call returns 401, second call succeeds
        resp_401 = self._mock_response(status=401)
        resp_401.raise_for_status = MagicMock(
            side_effect=aiohttp.ClientResponseError(
                request_info=MagicMock(),
                history=(),
                status=401,
            )
        )
        resp_ok = self._mock_response(json_data={"retried": True})

        call_count = 0
        original_request = client._request

        with patch.object(
            client, "_try_refresh_token", new_callable=AsyncMock, return_value=True
        ):
            # Mock session to return 401 then 200
            mock_ctx_401 = AsyncMock(
                __aenter__=AsyncMock(return_value=resp_401),
                __aexit__=AsyncMock(return_value=False),
            )
            mock_ctx_ok = AsyncMock(
                __aenter__=AsyncMock(return_value=resp_ok),
                __aexit__=AsyncMock(return_value=False),
            )

            mock_session = MagicMock()
            mock_session.request = MagicMock(side_effect=[mock_ctx_401, mock_ctx_ok])
            client.session = mock_session

            result = await client._request("GET", "tasks/1")
            assert result == {"status": "success", "data": {"retried": True}}

    async def test_request_401_no_infinite_retry(self):
        """Second 401 should raise, not retry again."""
        client = self._make_client()

        resp_401 = self._mock_response(status=401)
        resp_401.raise_for_status = MagicMock(
            side_effect=aiohttp.ClientResponseError(
                request_info=MagicMock(),
                history=(),
                status=401,
            )
        )

        mock_ctx = AsyncMock(
            __aenter__=AsyncMock(return_value=resp_401),
            __aexit__=AsyncMock(return_value=False),
        )
        mock_session = MagicMock()
        mock_session.request = MagicMock(return_value=mock_ctx)
        client.session = mock_session

        with patch.object(
            client, "_try_refresh_token", new_callable=AsyncMock, return_value=True
        ):
            with pytest.raises(aiohttp.ClientResponseError):
                await client._request("GET", "tasks/1", _retry=False)


# ---------------------------------------------------------------------------
# Endpoint methods
# ---------------------------------------------------------------------------


class TestGetTaskLogs(unittest.IsolatedAsyncioTestCase):
    """Test get_task_logs endpoint method."""

    async def test_get_task_logs_success(self):
        client = EvergreenRestClient(bearer_token="tok")
        client._request = AsyncMock(
            return_value={"status": "success", "data": "log line 1\nlog line 2"}
        )

        result = await client.get_task_logs("task-abc", 0)
        assert result == "log line 1\nlog line 2"
        client._request.assert_called_once_with(
            "GET", "tasks/task-abc/build/TaskLogs?type=task_log&execution=0"
        )

    async def test_get_task_logs_with_retries(self):
        client = EvergreenRestClient(bearer_token="tok")
        client._request = AsyncMock(
            return_value={"status": "success", "data": "retry logs"}
        )

        result = await client.get_task_logs("task-abc", 2)
        assert result == "retry logs"
        client._request.assert_called_once_with(
            "GET", "tasks/task-abc/build/TaskLogs?type=task_log&execution=2"
        )

    async def test_get_task_logs_failure(self):
        client = EvergreenRestClient(bearer_token="tok")
        client._request = AsyncMock(return_value={"status": "error", "data": None})

        result = await client.get_task_logs("task-abc", 0)
        assert result is None


class TestGetTaskTestResults(unittest.IsolatedAsyncioTestCase):
    """Test get_task_test_results endpoint method."""

    async def test_get_test_results_success(self):
        client = EvergreenRestClient(bearer_token="tok")
        client._request = AsyncMock(
            return_value={
                "status": "success",
                "data": "FAIL: TestSomething\npanic: oops",
            }
        )

        result = await client.get_task_test_results("task-abc", 0, "Job0")
        # Log contains error keywords, so the scanner returns a structured summary
        assert "Log scan: 2/2 lines matched" in result
        assert "fail: 1" in result
        assert "panic: 1" in result
        client._request.assert_called_once_with(
            "GET",
            "tasks/task-abc/build/TestLogs/Job0%2Fglobal.log"
            "?execution=0&tail_limit=100000",
        )

    async def test_get_test_results_custom_tail_limit(self):
        client = EvergreenRestClient(bearer_token="tok")
        client._request = AsyncMock(
            return_value={"status": "success", "data": "some logs"}
        )

        result = await client.get_task_test_results(
            "task-abc", 1, "Job1", tail_limit=500
        )
        assert result == "some logs"
        client._request.assert_called_once_with(
            "GET",
            "tasks/task-abc/build/TestLogs/Job1%2Fglobal.log"
            "?execution=1&tail_limit=500",
        )

    async def test_get_test_results_failure(self):
        client = EvergreenRestClient(bearer_token="tok")
        client._request = AsyncMock(return_value={"status": "error", "data": None})

        result = await client.get_task_test_results("task-abc", 0, "Job0")
        assert result is None

    async def test_get_test_results_empty_data(self):
        client = EvergreenRestClient(bearer_token="tok")
        client._request = AsyncMock(return_value={"status": "success", "data": ""})

        result = await client.get_task_test_results("task-abc", 0, "Job0")
        assert result == ""


# ---------------------------------------------------------------------------
# Wrapper functions in failed_jobs_tools
# ---------------------------------------------------------------------------


from evergreen_mcp.failed_jobs_tools import (
    fetch_evergreen_task_logs,
    fetch_evergreen_task_test_results,
)


class TestFetchEvergreenTaskLogs(unittest.IsolatedAsyncioTestCase):
    """Test fetch_evergreen_task_logs wrapper."""

    async def test_delegates_to_client(self):
        mock_client = AsyncMock()
        mock_client.get_task_logs.return_value = "raw log output"

        result = await fetch_evergreen_task_logs(
            mock_client, {"task_id": "t1", "execution_retries": 1}
        )
        assert result == {"logs": "raw log output"}
        mock_client.get_task_logs.assert_called_once_with("t1", 1)

    async def test_defaults_execution_retries(self):
        mock_client = AsyncMock()
        mock_client.get_task_logs.return_value = "logs"

        await fetch_evergreen_task_logs(mock_client, {"task_id": "t1"})
        mock_client.get_task_logs.assert_called_once_with("t1", 0)


class TestFetchEvergreenTaskTestResults(unittest.IsolatedAsyncioTestCase):
    """Test fetch_evergreen_task_test_results wrapper."""

    async def test_delegates_to_client(self):
        mock_client = AsyncMock()
        mock_client.get_task_test_results.return_value = "test output"

        result = await fetch_evergreen_task_test_results(
            mock_client,
            {
                "task_id": "t1",
                "execution_retries": 0,
                "test_name": "Job0",
                "tail_limit": 500,
            },
        )
        assert result == {"logs": "test output"}
        mock_client.get_task_test_results.assert_called_once_with(
            "t1", 0, "Job0", tail_limit=500
        )

    async def test_defaults(self):
        mock_client = AsyncMock()
        mock_client.get_task_test_results.return_value = "output"

        await fetch_evergreen_task_test_results(
            mock_client, {"task_id": "t1", "test_name": "Job0"}
        )
        mock_client.get_task_test_results.assert_called_once_with(
            "t1", 0, "Job0", tail_limit=100000
        )


# ---------------------------------------------------------------------------
# get_task_details
# ---------------------------------------------------------------------------

_TASK_RESPONSE_DATA = {
    "task_id": "task-abc",
    "execution": 0,
    "display_name": "compile",
    "status": "failed",
    "activated": True,
    "build_id": "build-1",
    "build_variant": "enterprise-rhel-80-64-bit",
    "version_id": "version-xyz",
    "artifacts": [
        {
            "name": "binary",
            "url": "https://s3.example.com/binary.tar.gz",
            "visibility": "signed",
            "ignore_for_fetch": False,
            "content_type": "application/x-gzip",
        }
    ],
}


class TestGetTaskDetails(unittest.IsolatedAsyncioTestCase):
    """Test get_task_details endpoint method."""

    async def test_returns_task_response_on_success(self):
        client = EvergreenRestClient(bearer_token="tok")
        client._request = AsyncMock(
            return_value={"status": "success", "data": _TASK_RESPONSE_DATA}
        )

        result = await client.get_task_details("task-abc")

        assert result.task_id == "task-abc"
        assert result.display_name == "compile"
        assert result.version_id == "version-xyz"
        assert len(result.artifacts) == 1
        assert result.artifacts[0].name == "binary"

    async def test_includes_fetch_all_executions_param(self):
        client = EvergreenRestClient(bearer_token="tok")
        client._request = AsyncMock(
            return_value={"status": "success", "data": _TASK_RESPONSE_DATA}
        )

        await client.get_task_details("task-abc", fetch_all_executions=True)

        call_url = client._request.call_args[0][1]
        assert "fetch_all_executions=true" in call_url

    async def test_raises_runtime_error_on_failed_status(self):
        client = EvergreenRestClient(bearer_token="tok")
        client._request = AsyncMock(return_value={"status": "error", "data": None})

        with pytest.raises(RuntimeError, match="Failed to fetch task details"):
            await client.get_task_details("task-abc")

    async def test_raises_runtime_error_when_data_is_none(self):
        client = EvergreenRestClient(bearer_token="tok")
        client._request = AsyncMock(return_value={"status": "success", "data": None})

        with pytest.raises(RuntimeError, match="No data returned"):
            await client.get_task_details("task-abc")

    async def test_raises_validation_error_on_bad_schema(self):
        from pydantic import ValidationError

        client = EvergreenRestClient(bearer_token="tok")
        # Missing required fields (task_id, display_name, etc.)
        client._request = AsyncMock(
            return_value={"status": "success", "data": {"unexpected": "shape"}}
        )

        with pytest.raises(ValidationError):
            await client.get_task_details("task-abc")
