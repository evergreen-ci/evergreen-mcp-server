"""Unit tests for the shared single-flight reconnect logic in ReconnectMixin.

These exercise the concurrency machinery directly against a tiny fake subclass,
independent of either concrete client, so the subtle generation/event/lock code
is covered in exactly one place.
"""

import asyncio

import pytest

from evergreen_mcp.reconnect import _MAX_RECONNECT_ATTEMPTS, ReconnectMixin


class _AuthError(Exception):
    """Stand-in for a transport's 401/unauthorized error."""


class FakeClient(ReconnectMixin):
    """Minimal ReconnectMixin subclass driven entirely by injected callables.

    Args:
        execute: async callable(generation) run as the per-attempt body.
        start_ready: whether the connection is usable immediately.
    """

    def __init__(self, execute, *, start_ready=True):
        self._execute = execute
        self.reconnect_calls = 0
        self._init_reconnect_state(start_ready=start_ready)

    def _is_auth_error(self, error: Exception) -> bool:
        return isinstance(error, _AuthError)

    async def _reestablish_connection(self) -> None:
        self.reconnect_calls += 1

    async def run(self):
        return await self._run_with_reconnect(self._execute)


async def test_normal_path_is_concurrent_and_never_reconnects():
    """Concurrent calls on a healthy connection all succeed without teardown."""
    in_flight = 0
    max_in_flight = 0

    async def execute(_generation):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0)  # yield so callers overlap
        in_flight -= 1
        return "ok"

    client = FakeClient(execute)

    results = await asyncio.gather(*(client.run() for _ in range(5)))

    assert results == ["ok"] * 5
    assert max_in_flight > 1  # genuinely concurrent, not serialized
    assert client.reconnect_calls == 0
    assert client._generation == 0


async def test_concurrent_auth_errors_reconnect_exactly_once():
    """N concurrent auth errors trigger a single-flight reconnect."""
    state = {"healthy": False}

    async def execute(_generation):
        if not state["healthy"]:
            raise _AuthError("401")
        return "ok"

    client = FakeClient(execute)

    async def reestablish():
        state["healthy"] = True
        client.reconnect_calls += 1

    client._reestablish_connection = reestablish

    results = await asyncio.gather(*(client.run() for _ in range(6)))

    assert results == ["ok"] * 6
    assert client.reconnect_calls == 1
    assert client._generation == 1


async def test_recovers_after_single_reconnect():
    """A 401 on the initial try recovers on the retry after one reconnect."""
    calls = {"n": 0}

    async def execute(_generation):
        calls["n"] += 1
        # Fail on the initial generation; succeed once the single reconnect has
        # bumped the generation.
        if client._generation < 1:
            raise _AuthError("401")
        return "ok"

    client = FakeClient(execute)

    result = await client.run()

    assert result == "ok"
    assert client.reconnect_calls == 1
    assert client._generation == 1


async def test_repeated_auth_error_is_bounded():
    """Persistent auth errors stop after _MAX_RECONNECT_ATTEMPTS, not forever."""

    async def execute(_generation):
        raise _AuthError("401")

    client = FakeClient(execute)

    # The auth error on the final attempt surfaces directly rather than being
    # wrapped, since we do not reconnect again after it.
    with pytest.raises(_AuthError, match="401"):
        await client.run()

    # We reconnect between attempts but never after the final attempt, so the
    # number of reconnects is one fewer than the number of tries.
    assert client.reconnect_calls == _MAX_RECONNECT_ATTEMPTS - 1
    assert client._generation == _MAX_RECONNECT_ATTEMPTS - 1


async def test_bystander_retries_when_reconnected_underneath():
    """A generic failure during a concurrent reconnect is retried, not surfaced."""
    calls = {"n": 0}

    async def execute(_generation):
        calls["n"] += 1
        if calls["n"] == 1:
            # Simulate another task having reconnected while we were in flight.
            client._generation += 1
            raise RuntimeError("connection reset by peer")
        return "ok"

    client = FakeClient(execute)

    result = await client.run()

    assert result == "ok"
    assert client.reconnect_calls == 0  # we did not start our own reconnect


async def test_reconnect_failure_surfaces_and_does_not_deadlock():
    """If the rebuild fails, the error surfaces and the gate reopens."""

    async def execute(_generation):
        raise _AuthError("401")

    client = FakeClient(execute)

    async def boom():
        raise RuntimeError("boom")

    client._reestablish_connection = boom

    with pytest.raises(RuntimeError, match="boom"):
        await client.run()

    # Event must be set again so parked waiters are not stuck forever.
    assert client._connected_event.is_set()
    assert client._generation == 0  # generation not bumped on failure


async def test_non_auth_error_surfaces_immediately():
    """A non-auth error on a healthy connection is raised without reconnecting."""

    async def execute(_generation):
        raise ValueError("nope")

    client = FakeClient(execute)

    with pytest.raises(ValueError, match="nope"):
        await client.run()

    assert client.reconnect_calls == 0
    assert client._generation == 0
