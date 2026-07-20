"""Shared single-flight reconnect/retry logic for the Evergreen clients.

Both the GraphQL and REST clients are long-lived singletons shared across
concurrent MCP tool calls and authenticate with the same short-lived OAuth
token. When a token expires, many in-flight calls can fail with a 401 at once.
This mixin coordinates them so that:

- normal calls run lock-free, gated only on an ``asyncio.Event`` that is set
  while the connection is healthy (so they are not serialized);
- a 401 triggers exactly one teardown/rebuild ("single-flight"), guarded by a
  lock and a generation counter (double-checked locking);
- other callers wait for that rebuild and then retry, rather than each starting
  their own; and
- a caller whose retry after the rebuild still fails with a 401 surfaces the
  error rather than reconnecting again; the total number of tries per call is
  bounded by ``_MAX_RECONNECT_ATTEMPTS``.

Clients mix this in and supply three small hooks: :meth:`_is_auth_error`,
:meth:`_reestablish_connection`, and a per-attempt callable passed to
:meth:`_run_with_reconnect`.
"""

import asyncio
import logging
from typing import Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

# Maximum number of attempts (including the initial try) a call makes before
# giving up. With the default of 2 this is one initial try plus a single retry
# after one reconnect; a token that is still stale after that reconnect surfaces
# the error rather than thrashing.
_MAX_RECONNECT_ATTEMPTS = 2

T = TypeVar("T")


class ReconnectMixin:
    """Mixin providing a concurrency-safe, single-flight reconnect loop.

    Subclasses must call :meth:`_init_reconnect_state` from ``__init__`` and may
    override :meth:`_is_auth_error` and :meth:`_reestablish_connection`.
    """

    def _init_reconnect_state(self, *, start_ready: bool) -> None:
        """Initialize the reconnect primitives.

        Args:
            start_ready: Whether the connection is usable immediately. Clients
                with no explicit ``connect()`` (e.g. a lazily created session)
                pass ``True``; clients that set up the connection in a separate
                ``connect()`` pass ``False`` and set the event there.
        """
        # Held only while tearing down and rebuilding the connection, never
        # while executing a request.
        self._reconnect_lock = asyncio.Lock()
        # Set when the connection is healthy; cleared during a reconnect so
        # callers park until it is back up. Set() returning immediately from
        # wait() is what keeps normal calls from being serialized.
        self._connected_event = asyncio.Event()
        # Incremented on each successful reconnect. Lets a caller detect that
        # another task already reconnected (single-flight) and that its own
        # observed connection is stale.
        self._generation = 0
        if start_ready:
            self._connected_event.set()

    @staticmethod
    def _is_auth_error(error: Exception) -> bool:
        """Return True if the error indicates an expired/invalid token (401).

        Overridden per client to inspect its transport's error type.
        """
        return False

    async def _reestablish_connection(self) -> None:
        """Tear down and rebuild the underlying connection with a fresh token.

        Overridden per client. Called at most once per generation, under the
        reconnect lock, with the connection gated off.
        """
        raise NotImplementedError

    async def _reconnect(self, observed_generation: int) -> None:
        """Rebuild the connection at most once per generation (single-flight).

        Only the first caller to observe a given generation performs the
        rebuild; concurrent callers that observed the same generation return
        immediately and let their caller retry against the new connection.

        Args:
            observed_generation: The generation the caller used when its request
                failed. If the current generation has already advanced past it,
                someone else has reconnected and this call is a no-op.
        """
        async with self._reconnect_lock:
            if self._generation != observed_generation:
                # A reconnect already happened since the caller's request;
                # nothing to do. The caller loops and retries on the new
                # connection.
                return

            # Gate off new/retrying calls while we rebuild the connection.
            self._connected_event.clear()
            try:
                await self._reestablish_connection()
                self._generation += 1
            finally:
                # Set unconditionally so a failed rebuild can never deadlock
                # waiters. On failure the generation is not bumped and the
                # connection is unusable, so retries surface the error instead.
                self._connected_event.set()

    async def _run_with_reconnect(self, attempt: Callable[[int], Awaitable[T]]) -> T:
        """Run ``attempt`` in the bounded, gated single-flight retry loop.

        Args:
            attempt: Async callable taking the observed generation and producing
                the result. It should perform one execution against the current
                connection and let auth errors propagate for classification.

        Returns:
            The result of the first successful ``attempt``.
        """
        for attempt_index in range(_MAX_RECONNECT_ATTEMPTS):
            is_last_attempt = attempt_index == _MAX_RECONNECT_ATTEMPTS - 1
            # Park until the connection is healthy. When it already is, this
            # returns immediately without holding any lock, so concurrent calls
            # are not serialized. During a reconnect the event is cleared and
            # callers wait here instead of firing at a torn-down connection or
            # each starting their own reconnect.
            await self._connected_event.wait()
            gen = self._generation
            try:
                return await attempt(gen)
            except Exception as e:
                if self._generation != gen or not self._connected_event.is_set():
                    # Another task is reconnecting (event cleared) or already
                    # reconnected (generation advanced) while we were in flight;
                    # our failure is likely collateral from that teardown. Loop
                    # to park on the event and retry on the fresh connection.
                    logger.debug("Connection changing during request; retrying")
                    continue
                if self._is_auth_error(e) and not is_last_attempt:
                    # Reconnect and retry, but never on the final attempt: a
                    # reconnect there would burn a rebuild with no try left to
                    # use it, so surface the error instead.
                    logger.info("Got auth error, reconnecting with fresh token")
                    await self._reconnect(gen)
                    continue
                raise

        raise Exception("Request failed after repeated reconnect attempts")
