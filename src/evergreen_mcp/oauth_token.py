"""OAuth token provider that delegates to the Evergreen CLI.

When ~/.evergreen.yml contains an `oauth` section, token acquisition is handled
entirely by the `evergreen client get-oauth-token` command. This module caches
the resulting JWT in memory until 60 seconds before its `exp` claim, then
re-shells on the next request.
"""

import asyncio
import logging
import time
from typing import Callable, Optional

import jwt as pyjwt

logger = logging.getLogger(__name__)

_cached_token: Optional[str] = None
_token_exp: float = 0.0
_refresh_lock: Optional[asyncio.Lock] = None
_EXPIRY_BUFFER = 60  # seconds


def _get_lock() -> asyncio.Lock:
    global _refresh_lock
    if _refresh_lock is None:
        _refresh_lock = asyncio.Lock()
    return _refresh_lock


def _token_is_valid() -> bool:
    return bool(_cached_token) and time.time() < _token_exp - _EXPIRY_BUFFER


async def get_oauth_token() -> str:
    """Return the current valid token, re-shelling if needed."""
    if _token_is_valid():
        return _cached_token  # type: ignore[return-value]
    await ensure_oauth_token(on_refresh=lambda _: None)
    return _cached_token  # type: ignore[return-value]


async def ensure_oauth_token(on_refresh: Callable[[str], None]) -> None:
    """Ensure the cached OAuth token is valid, re-shelling if needed.

    on_refresh is called with the new token only when a re-shell actually
    occurs (cache hit → no call). Use it to update transport headers or
    any other state that depends on the current token value.
    """
    global _cached_token, _token_exp

    if _token_is_valid():
        return

    async with _get_lock():
        # Re-check after acquiring lock — another coroutine may have already refreshed.
        if _token_is_valid():
            return

        proc = await asyncio.create_subprocess_exec(
            "evergreen",
            "client",
            "get-oauth-token",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"evergreen client get-oauth-token failed (exit {proc.returncode}): "
                f"{stderr.decode().strip()}"
            )

        token = stdout.decode().strip()
        claims = pyjwt.decode(
            token, options={"verify_signature": False, "verify_exp": False}
        )
        exp = claims.get("exp")
        if not exp:
            raise RuntimeError(
                "OAuth token from 'evergreen client get-oauth-token' is missing the "
                "'exp' claim; cannot cache token. Check that the CLI returns a valid JWT."
            )
        _token_exp = float(exp)
        _cached_token = token
        logger.debug("OAuth token refreshed, expires at %s", _token_exp)
        on_refresh(token)
