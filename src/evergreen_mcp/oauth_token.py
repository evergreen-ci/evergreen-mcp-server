"""OAuth token provider that delegates to the Evergreen CLI.

When ~/.evergreen.yml contains an `oauth` section, token acquisition is handled
entirely by the `evergreen client get-oauth-token` command. This module caches
the resulting JWT in memory until 60 seconds before its `exp` claim, then
re-shells on the next request.
"""

import asyncio
import logging
import time
from typing import Optional

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


async def get_oauth_token(force_refresh: bool = False) -> str:
    """Return the current valid token, re-shelling if needed."""
    if _token_is_valid() and not force_refresh:
        return _cached_token  # type: ignore[return-value]
    await ensure_oauth_token(force_refresh=force_refresh)
    return _cached_token  # type: ignore[return-value]


async def ensure_oauth_token(force_refresh: bool = False) -> None:
    """Ensure the cached OAuth token is valid, re-shelling if needed."""
    global _cached_token, _token_exp

    if _token_is_valid() and not force_refresh:
        return

    async with _get_lock():
        # Re-check after acquiring lock — another coroutine may have already refreshed.
        if _token_is_valid() and not force_refresh:
            return

        try:
            proc = await asyncio.create_subprocess_exec(
                "evergreen",
                "client",
                "get-oauth-token",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
        except Exception as e:
            raise RuntimeError(
                f"Failed to run 'evergreen client get-oauth-token': {e}. Is it installed and working?"
            ) from e

        if proc.returncode != 0:
            raise RuntimeError(
                f"evergreen client get-oauth-token failed (exit {proc.returncode}): "
                f"{stderr.decode().strip()}"
            )

        try:
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
        except Exception as e:
            raise RuntimeError(
                f"Failed to decode OAuth token from 'evergreen client get-oauth-token': {e}. Check the output yourself and/or report to #ask-devprod"
            ) from e
