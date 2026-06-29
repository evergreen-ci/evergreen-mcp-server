"""
Unit tests for EvergreenRestClient.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from evergreen_mcp.evergreen_rest_client import EvergreenRestClient


class TestInit(unittest.TestCase):
    def test_init_with_bearer_token(self):
        client = EvergreenRestClient(bearer_token="tok-123")
        assert client.bearer_token == "tok-123"
        assert client.session is None

    def test_init_with_api_key(self):
        client = EvergreenRestClient(user="admin", api_key="key-456")
        assert client.user == "admin"
        assert client.api_key == "key-456"

    def test_init_with_token_getter(self):
        getter = AsyncMock(return_value="tok")
        client = EvergreenRestClient(token_getter=getter)
        assert client._token_getter is getter

    def test_init_no_auth_raises(self):
        with pytest.raises(ValueError, match="Either token_getter"):
            EvergreenRestClient()

    def test_init_user_without_api_key_raises(self):
        with pytest.raises(ValueError):
            EvergreenRestClient(user="admin")

    def test_custom_base_url(self):
        client = EvergreenRestClient(
            bearer_token="tok", base_url="https://custom.api/v1/"
        )
        assert client.base_url == "https://custom.api/v1/"


class TestGetAuthHeaders(unittest.IsolatedAsyncioTestCase):
    async def test_bearer_token_headers(self):
        client = EvergreenRestClient(bearer_token="tok-123")
        headers = await client._get_auth_headers()
        assert headers["Authorization"] == "Bearer tok-123"
        assert "Api-User" not in headers

    async def test_api_key_headers(self):
        client = EvergreenRestClient(user="admin", api_key="key-456")
        headers = await client._get_auth_headers()
        assert headers["Api-User"] == "admin"
        assert headers["Api-Key"] == "key-456"
        assert "Authorization" not in headers

    async def test_token_getter_headers(self):
        getter = AsyncMock(return_value="dynamic-tok")
        client = EvergreenRestClient(token_getter=getter)
        headers = await client._get_auth_headers()
        assert headers["Authorization"] == "Bearer dynamic-tok"

    async def test_user_agent_header(self):
        client = EvergreenRestClient(bearer_token="tok")
        headers = await client._get_auth_headers()
        assert headers["User-Agent"].startswith("evergreen-mcp/")

    async def test_accept_json_header(self):
        client = EvergreenRestClient(bearer_token="tok")
        headers = await client._get_auth_headers()
        assert headers["Accept"] == "application/json"


class TestSessionManagement(unittest.IsolatedAsyncioTestCase):
    async def test_get_session_creates_session(self):
        client = EvergreenRestClient(bearer_token="tok")
        assert client.session is None
        session = client._get_session()
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
        assert client.session is None

    async def test_close_session_noop_when_none(self):
        client = EvergreenRestClient(bearer_token="tok")
        await client._close_session()  # should not raise


class TestRequest(unittest.IsolatedAsyncioTestCase):
    def _make_client(self):
        return EvergreenRestClient(
            bearer_token="tok", base_url="https://api.example.com/v2/"
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
        call_args = mock_session.request.call_args
        assert call_args[0] == ("GET", "https://api.example.com/v2/tasks/123")
        assert "Authorization" in call_args[1]["headers"]
        assert result == {"status": "success", "data": {"key": "val"}}

    async def test_request_absolute_url(self):
        client = self._make_client()
        resp = self._mock_response(json_data={})
        mock_session = MagicMock()
        mock_session.request = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=resp),
                __aexit__=AsyncMock(return_value=False),
            )
        )
        client.session = mock_session
        await client._request("GET", "https://other.api/endpoint")
        call_args = mock_session.request.call_args
        assert call_args[0] == ("GET", "https://other.api/endpoint")

    async def test_request_text_response(self):
        client = self._make_client()
        resp = self._mock_response(
            text_data="plain text log", content_type="text/plain"
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


class TestGetTaskLogs(unittest.IsolatedAsyncioTestCase):
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

    async def test_get_task_logs_failure(self):
        client = EvergreenRestClient(bearer_token="tok")
        client._request = AsyncMock(return_value={"status": "error", "data": None})
        result = await client.get_task_logs("task-abc", 0)
        assert result is None


class TestGetTaskTestResults(unittest.IsolatedAsyncioTestCase):
    async def test_get_test_results_success(self):
        client = EvergreenRestClient(bearer_token="tok")
        client._request = AsyncMock(
            return_value={
                "status": "success",
                "data": "FAIL: TestSomething\npanic: oops",
            }
        )
        result = await client.get_task_test_results("task-abc", 0, "Job0")
        assert "Log scan: 2/2 lines matched" in result
        assert "fail: 1" in result
        assert "panic: 1" in result

    async def test_get_test_results_failure(self):
        client = EvergreenRestClient(bearer_token="tok")
        client._request = AsyncMock(return_value={"status": "error", "data": None})
        result = await client.get_task_test_results("task-abc", 0, "Job0")
        assert result is None


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
    async def test_returns_task_response_on_success(self):
        client = EvergreenRestClient(bearer_token="tok")
        client._request = AsyncMock(
            return_value={"status": "success", "data": _TASK_RESPONSE_DATA}
        )
        result = await client.get_task_details("task-abc")
        assert result.task_id == "task-abc"
        assert result.display_name == "compile"
        assert len(result.artifacts) == 1

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
        client._request = AsyncMock(
            return_value={"status": "success", "data": {"unexpected": "shape"}}
        )
        with pytest.raises(ValidationError):
            await client.get_task_details("task-abc")


from evergreen_mcp.failed_jobs_tools import (
    fetch_evergreen_task_logs,
    fetch_evergreen_task_test_results,
)


class TestFetchEvergreenTaskLogs(unittest.IsolatedAsyncioTestCase):
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
