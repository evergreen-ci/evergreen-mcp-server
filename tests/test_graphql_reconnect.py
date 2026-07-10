"""Tests for concurrency-safe reconnect in EvergreenGraphQLClient._execute_query."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from gql.transport.exceptions import TransportError, TransportServerError

import evergreen_mcp.evergreen_graphql_client as gqlc
from evergreen_mcp.evergreen_graphql_client import EvergreenGraphQLClient


def _unauthorized() -> TransportServerError:
    return TransportServerError("401 Unauthorized", code=401)


def _make_session(execute_side_effect) -> MagicMock:
    """Build a fake gql session with a mocked execute and header dict."""
    session = MagicMock()
    session.transport.session.headers = {}
    session.execute = AsyncMock(side_effect=execute_side_effect)
    return session


def _make_client(initial_session, sessions_after_connect=()):
    """Construct a client wired with mocked connect/close.

    connect() hands out the next session from sessions_after_connect and sets
    the event (mimicking the real connect); close() drops the session and clears
    the event. Both are AsyncMocks so call counts can be asserted.
    """
    client = EvergreenGraphQLClient(token_getter=AsyncMock(return_value="tok"))
    client._session = initial_session
    client._connected_event.set()

    session_iter = iter(sessions_after_connect)

    def fake_connect(force_refresh=False):
        client._session = next(session_iter)
        client._connected_event.set()

    def fake_close():
        client._session = None
        client._connected_event.clear()

    client.connect = AsyncMock(side_effect=fake_connect)
    client.close = AsyncMock(side_effect=fake_close)
    return client


async def test_normal_path_is_concurrent_and_never_reconnects():
    """Concurrent queries on a healthy connection all succeed with no teardown."""
    in_flight = 0
    max_in_flight = 0

    async def execute(query, variable_values=None):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0)  # yield so callers overlap
        in_flight -= 1
        return {"ok": True}

    client = _make_client(_make_session(execute))

    results = await asyncio.gather(
        *(client._execute_query("query {x}") for _ in range(5))
    )

    assert all(r == {"ok": True} for r in results)
    assert max_in_flight > 1  # genuinely concurrent, not serialized
    client.close.assert_not_called()
    client.connect.assert_not_called()
    assert client._generation == 0
    assert client._token_getter.await_count >= 5  # header refreshed per query


async def test_concurrent_401s_reconnect_exactly_once():
    """N concurrent 401s trigger a single-flight teardown/reconnect."""

    async def always_401(query, variable_values=None):
        raise _unauthorized()

    async def ok(query, variable_values=None):
        return {"ok": True}

    initial = _make_session(always_401)
    healthy = _make_session(ok)
    client = _make_client(initial, sessions_after_connect=[healthy])

    results = await asyncio.gather(
        *(client._execute_query("query {x}") for _ in range(6))
    )

    assert all(r == {"ok": True} for r in results)
    assert client.close.await_count == 1
    assert client.connect.await_count == 1
    assert client._generation == 1


async def test_stale_token_recovers_after_second_reconnect():
    """A refreshed token that is immediately stale triggers another reconnect."""

    async def always_401(query, variable_values=None):
        raise _unauthorized()

    async def ok(query, variable_values=None):
        return {"ok": True}

    initial = _make_session(always_401)
    still_401 = _make_session(always_401)  # first reconnect lands on a stale token
    healthy = _make_session(ok)  # second reconnect recovers
    client = _make_client(initial, sessions_after_connect=[still_401, healthy])

    result = await client._execute_query("query {x}")

    assert result == {"ok": True}
    assert client.connect.await_count == 2
    assert client._generation == 2


async def test_repeated_401_is_bounded():
    """Persistent 401s stop after _MAX_RECONNECT_ATTEMPTS instead of looping forever."""

    async def always_401(query, variable_values=None):
        raise _unauthorized()

    initial = _make_session(always_401)
    replacements = [
        _make_session(always_401) for _ in range(gqlc._MAX_RECONNECT_ATTEMPTS)
    ]
    client = _make_client(initial, sessions_after_connect=replacements)

    with pytest.raises(Exception, match="after repeated token refresh"):
        await client._execute_query("query {x}")

    assert client.connect.await_count == gqlc._MAX_RECONNECT_ATTEMPTS
    assert client._generation == gqlc._MAX_RECONNECT_ATTEMPTS


async def test_bystander_retries_when_reconnected_underneath():
    """A generic failure during a concurrent reconnect is retried, not surfaced."""
    calls = {"n": 0}

    async def execute(query, variable_values=None):
        calls["n"] += 1
        if calls["n"] == 1:
            # Simulate another task having reconnected while we were in flight.
            client._generation += 1
            raise TransportError("connection reset by peer")
        return {"ok": True}

    client = _make_client(_make_session(execute))

    result = await client._execute_query("query {x}")

    assert result == {"ok": True}
    client.connect.assert_not_called()  # we did not start our own reconnect


async def test_reconnect_failure_surfaces_and_does_not_deadlock():
    """If connect() fails during reconnect, the error surfaces and the gate reopens."""

    async def always_401(query, variable_values=None):
        raise _unauthorized()

    client = _make_client(_make_session(always_401))
    client.connect = AsyncMock(side_effect=RuntimeError("boom"))

    with pytest.raises(RuntimeError, match="boom"):
        await client._execute_query("query {x}")

    # Event must be set again so parked waiters are not stuck forever.
    assert client._connected_event.is_set()
    assert client._generation == 0  # generation not bumped on failure
