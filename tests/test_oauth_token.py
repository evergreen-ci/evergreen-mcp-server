"""Tests for oauth_token module."""

import base64
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import evergreen_mcp.oauth_token as oauth_module
from evergreen_mcp.oauth_token import get_oauth_token


def _make_jwt(exp: float) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = (
        base64.urlsafe_b64encode(json.dumps({"sub": "user", "exp": int(exp)}).encode())
        .decode()
        .rstrip("=")
    )
    return f"{header}.{payload}.sig"


@pytest.fixture(autouse=True)
def reset_cache():
    """Reset module-level cache and lock before each test."""
    oauth_module._cached_token = None
    oauth_module._token_exp = 0.0
    oauth_module._refresh_lock = None
    yield
    oauth_module._cached_token = None
    oauth_module._token_exp = 0.0
    oauth_module._refresh_lock = None


async def _fake_subprocess(token: str):
    """Helper that patches create_subprocess_exec to return the given token."""
    proc = MagicMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(token.encode(), b""))
    return proc


@pytest.mark.asyncio
async def test_shells_out_on_first_call():
    token = _make_jwt(time.time() + 3600)
    with patch(
        "asyncio.create_subprocess_exec", return_value=await _fake_subprocess(token)
    ):
        result = await get_oauth_token()
    assert result == token


@pytest.mark.asyncio
async def test_cache_hit_skips_subprocess():
    token = _make_jwt(time.time() + 3600)
    with patch(
        "asyncio.create_subprocess_exec", return_value=await _fake_subprocess(token)
    ) as mock_exec:
        await get_oauth_token()
        await get_oauth_token()
        # subprocess called only once
        assert mock_exec.call_count == 1


@pytest.mark.asyncio
async def test_expired_cache_re_shells():
    old_token = _make_jwt(time.time() - 1)  # already expired
    new_token = _make_jwt(time.time() + 3600)

    # Seed the cache with an expired token
    oauth_module._cached_token = old_token
    oauth_module._token_exp = time.time() - 1  # expired

    with patch(
        "asyncio.create_subprocess_exec", return_value=await _fake_subprocess(new_token)
    ):
        result = await get_oauth_token()
    assert result == new_token


@pytest.mark.asyncio
async def test_near_expiry_re_shells():
    """Tokens expiring within the 60s buffer should trigger a re-fetch."""
    stale_token = _make_jwt(time.time() + 30)  # within 60s buffer

    oauth_module._cached_token = stale_token
    oauth_module._token_exp = time.time() + 30

    new_token = _make_jwt(time.time() + 3600)
    with patch(
        "asyncio.create_subprocess_exec", return_value=await _fake_subprocess(new_token)
    ):
        result = await get_oauth_token()
    assert result == new_token


@pytest.mark.asyncio
async def test_missing_exp_raises():
    """Token without an exp claim should raise RuntimeError rather than silently re-shelling."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = (
        base64.urlsafe_b64encode(json.dumps({"sub": "user"}).encode())
        .decode()
        .rstrip("=")
    )
    token_no_exp = f"{header}.{payload}.sig"
    with patch(
        "asyncio.create_subprocess_exec",
        return_value=await _fake_subprocess(token_no_exp),
    ):
        with pytest.raises(RuntimeError, match="Failed to decode OAuth token"):
            await get_oauth_token()


@pytest.mark.asyncio
async def test_subprocess_failure_raises():
    proc = MagicMock()
    proc.returncode = 1
    proc.communicate = AsyncMock(return_value=(b"", b"auth error"))
    with patch("asyncio.create_subprocess_exec", return_value=proc):
        with pytest.raises(
            RuntimeError, match="evergreen client get-oauth-token failed"
        ):
            await get_oauth_token()
