"""Tests for per-request credential overrides in mcp_tools._get_clients."""

import base64
import json
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from evergreen_mcp.mcp_tools import _get_clients, _user_from_jwt


@dataclass
class FakeEvergreenContext:
    """Minimal EvergreenContext for testing."""

    client: object = None
    api_client: object = None
    user_id: str = ""
    default_project_id: str = None
    workspace_dir: str = None
    projects_for_directory: dict = field(default_factory=dict)


def _make_jwt(claims: dict) -> str:
    """Build a fake unsigned JWT for testing."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload = (
        base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    )
    return f"{header}.{payload}."


@pytest.mark.asyncio
async def test_get_clients_falls_back_to_lifespan_context():
    """When no per-request credentials are provided, use lifespan clients."""
    mock_client = MagicMock()
    mock_api_client = MagicMock()
    ctx = FakeEvergreenContext(
        client=mock_client,
        api_client=mock_api_client,
        user_id="default.user",
    )

    async with _get_clients(ctx) as (client, api_client, user_id):
        assert client is mock_client
        assert api_client is mock_api_client
        assert user_id == "default.user"


@pytest.mark.asyncio
async def test_get_clients_creates_per_request_clients_with_bearer_token():
    """When bearer_token is provided, create new clients with bearer auth."""
    ctx = FakeEvergreenContext(
        client=MagicMock(),
        api_client=MagicMock(),
        user_id="default.user",
    )
    token = _make_jwt({"email": "april.white@mongodb.com"})

    with (
        patch("evergreen_mcp.mcp_tools.EvergreenGraphQLClient") as mock_gql_cls,
        patch("evergreen_mcp.mcp_tools.EvergreenRestClient") as mock_rest_cls,
    ):
        mock_gql = AsyncMock()
        mock_gql.__aenter__ = AsyncMock(return_value=mock_gql)
        mock_gql.__aexit__ = AsyncMock(return_value=False)
        mock_gql_cls.return_value = mock_gql

        mock_rest = MagicMock()
        mock_rest._close_session = AsyncMock()
        mock_rest_cls.return_value = mock_rest

        async with _get_clients(ctx, bearer_token=token) as (
            client,
            api_client,
            user_id,
        ):
            assert client is mock_gql
            assert api_client is mock_rest
            assert user_id == "april.white"
            mock_gql_cls.assert_called_once_with(bearer_token=token, endpoint=None)
            mock_rest_cls.assert_called_once_with(bearer_token=token)

        mock_rest._close_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_clients_raises_when_no_credentials():
    """When lifespan clients are None and no per-request creds, raise."""
    ctx = FakeEvergreenContext(client=None, api_client=None, user_id="")

    with pytest.raises(ValueError, match="No Evergreen credentials"):
        async with _get_clients(ctx) as _:
            pass


def test_user_from_jwt_extracts_email_local_part():
    token = _make_jwt({"email": "april.white@mongodb.com"})
    assert _user_from_jwt(token) == "april.white"


def test_user_from_jwt_falls_back_to_preferred_username():
    token = _make_jwt({"preferred_username": "april.white"})
    assert _user_from_jwt(token) == "april.white"


def test_user_from_jwt_falls_back_to_sub():
    token = _make_jwt({"sub": "some-subject-id"})
    assert _user_from_jwt(token) == "some-subject-id"


def test_user_from_jwt_returns_empty_on_garbage():
    assert _user_from_jwt("not-a-jwt") == ""


# ---------------------------------------------------------------------------
# bearer_token parameter shape
#
# EVERGREEN_AUTH_MODE=per_request leaves the lifespan context without clients
# (server.py:145-152 — "used by MCP gateways that inject per-user credentials
# from a credential store"), so _get_clients RAISES unless a bearer_token
# arrives as a tool argument. That is why the parameter was narrowed
# (str | None = None -> str = "") rather than removed from the tool schema:
# removing it would save ~945 tokens per request but break that deployment.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_string_default_falls_back_to_lifespan_clients():
    """The new "" default must behave exactly as the old None default did."""
    mock_client = MagicMock()
    mock_api_client = MagicMock()
    ctx = FakeEvergreenContext(
        client=mock_client, api_client=mock_api_client, user_id="me"
    )

    async with _get_clients(ctx, bearer_token="") as (client, api_client, user_id):
        assert client is mock_client
        assert api_client is mock_api_client
        assert user_id == "me"


@pytest.mark.asyncio
async def test_per_request_mode_without_a_token_raises_clearly():
    """A gateway context before credential injection: no clients, no token."""
    ctx = FakeEvergreenContext(client=None, api_client=None, user_id="")

    with pytest.raises(ValueError, match="No Evergreen credentials available"):
        async with _get_clients(ctx, bearer_token=""):
            pass


@pytest.mark.asyncio
async def test_gateway_token_still_builds_per_request_clients():
    """The capability the narrowing preserves: a real token routes per-request."""
    import evergreen_mcp.mcp_tools as mt

    ctx = FakeEvergreenContext(client=None, api_client=None, user_id="")
    built = {}

    class FakeGQL:
        def __init__(self, **kw):
            built["gql_kwargs"] = kw

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class FakeREST:
        def __init__(self, **kw):
            built["rest_kwargs"] = kw

        async def _close_session(self):
            built["closed"] = True

    token = _make_jwt({"email": "marcin.borowiec@mongodb.com"})
    with patch.object(mt, "EvergreenGraphQLClient", FakeGQL), patch.object(
        mt, "EvergreenRestClient", FakeREST
    ):
        async with mt._get_clients(ctx, bearer_token=token) as (gql, rest, user_id):
            assert isinstance(gql, FakeGQL)
            assert isinstance(rest, FakeREST)
            assert user_id == "marcin.borowiec"

    assert built["gql_kwargs"]["bearer_token"] == token
    assert built["rest_kwargs"]["bearer_token"] == token
    assert built["closed"] is True
