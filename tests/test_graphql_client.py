"""Thin integration test wiring EvergreenGraphQLClient onto ReconnectMixin.

The reconnect concurrency machinery itself is covered in test_reconnect.py;
this asserts the GraphQL client's hooks connect to it correctly — a 401 through
the public _execute_query triggers exactly one close() + connect(force_refresh).
"""

from unittest.mock import AsyncMock, MagicMock

from gql.transport.exceptions import TransportServerError

from evergreen_mcp.evergreen_graphql_client import EvergreenGraphQLClient


def _make_session(execute_side_effect) -> MagicMock:
    session = MagicMock()
    session.transport.session.headers = {}
    session.execute = AsyncMock(side_effect=execute_side_effect)
    return session


async def test_401_triggers_single_reconnect_then_succeeds():
    """A 401 tears down once and retries on a freshly connected session."""

    async def always_401(query, variable_values=None):
        raise TransportServerError("401 Unauthorized", code=401)

    async def ok(query, variable_values=None):
        return {"ok": True}

    client = EvergreenGraphQLClient(token_getter=AsyncMock(return_value="tok"))
    client._session = _make_session(always_401)
    client._connected_event.set()

    healthy = _make_session(ok)

    def fake_connect(force_refresh=False):
        assert force_refresh is True
        client._session = healthy
        client._connected_event.set()

    def fake_close():
        client._session = None
        client._connected_event.clear()

    client.connect = AsyncMock(side_effect=fake_connect)
    client.close = AsyncMock(side_effect=fake_close)

    result = await client._execute_query("query {x}")

    assert result == {"ok": True}
    assert client.close.await_count == 1
    assert client.connect.await_count == 1
    assert client._generation == 1
