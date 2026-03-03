#!/usr/bin/env python3
"""
Concurrency and atomicity tests for OIDC authentication module.

Tests validate:
- Cross-process coordination (re-check-after-lock pattern)
- Disk-only refresh token (no stale in-memory refresh tokens)
- Atomic file writes with random temp file names
- State/file consistency ordering
- TOCTOU fix in _read_token_file
"""

import asyncio
import base64
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, mock_open, patch

import pytest

from evergreen_mcp.oidc_auth import OIDCAuthManager


def create_mock_jwt(claims: dict) -> str:
    """Create a mock JWT token from claims."""
    header = (
        base64.urlsafe_b64encode(b'{"alg":"RS256","typ":"JWT"}').decode().rstrip("=")
    )
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    signature = base64.urlsafe_b64encode(b"mock_signature").decode().rstrip("=")
    return f"{header}.{payload}.{signature}"


@pytest.fixture
def valid_jwt_claims():
    """Generate valid JWT claims for testing."""
    return {
        "sub": "test-user-id",
        "email": "test@mongodb.com",
        "preferred_username": "test",
        "name": "Test User",
        "groups": ["team1", "team2"],
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
    }


@pytest.fixture
def expired_jwt_claims():
    """Generate expired JWT claims for testing."""
    return {
        "sub": "test-user-id",
        "email": "test@mongodb.com",
        "preferred_username": "test",
        "exp": int(time.time()) - 3600,
        "iat": int(time.time()) - 7200,
    }


@pytest.fixture
def auth_manager():
    """Create a fresh OIDCAuthManager instance for each test."""
    mock_config = {
        "oauth": {
            "issuer": "https://dex.example.com",
            "client_id": "test-client-id",
            "token_file_path": "/tmp/test-token.json",
        }
    }
    with patch("builtins.open", mock_open(read_data=json.dumps(mock_config))):
        with patch.object(Path, "exists", return_value=True):
            with patch("yaml.safe_load", return_value=mock_config):
                return OIDCAuthManager()


class TestCrossProcessCoordination:
    """Test ensure_authenticated re-checks token file after acquiring lock."""

    @pytest.mark.asyncio
    async def test_recheck_after_lock_finds_valid_token(
        self, auth_manager, valid_jwt_claims
    ):
        """After acquiring lock, if token file is valid, skip authentication."""
        token = create_mock_jwt(valid_jwt_claims)
        token_data = {"access_token": token, "refresh_token": "refresh"}

        with patch.object(auth_manager, "_read_token_file", return_value=token_data):
            with patch("evergreen_mcp.oidc_auth.AsyncFileLock") as mock_afl:
                async_cm = AsyncMock()
                async_cm.__aenter__ = AsyncMock(return_value=None)
                async_cm.__aexit__ = AsyncMock(return_value=False)
                mock_afl.return_value = async_cm

                result = await auth_manager.ensure_authenticated()

                assert result is True
                assert auth_manager._access_token == token

    @pytest.mark.asyncio
    async def test_recheck_after_lock_still_no_token_proceeds(
        self, auth_manager, valid_jwt_claims
    ):
        """If re-check still finds no token, proceeds to full authentication."""
        with patch.object(auth_manager, "_read_token_file", return_value=None):
            with patch.object(
                auth_manager, "_do_authentication", new_callable=AsyncMock
            ) as mock_auth:
                mock_auth.return_value = True
                with patch("evergreen_mcp.oidc_auth.AsyncFileLock") as mock_afl:
                    async_cm = AsyncMock()
                    async_cm.__aenter__ = AsyncMock(return_value=None)
                    async_cm.__aexit__ = AsyncMock(return_value=False)
                    mock_afl.return_value = async_cm

                    result = await auth_manager.ensure_authenticated()

                    assert result is True
                    mock_auth.assert_called_once()


class TestDiskOnlyRefreshToken:
    """Test that refresh tokens are always read from disk, never cached in memory."""

    @pytest.mark.asyncio
    async def test_refresh_reads_from_disk_not_memory(
        self, auth_manager, expired_jwt_claims, valid_jwt_claims
    ):
        """refresh_token() reads the refresh token from disk under the lock."""
        expired_token = create_mock_jwt(expired_jwt_claims)
        valid_token = create_mock_jwt(valid_jwt_claims)

        disk_data = {
            "access_token": expired_token,
            "refresh_token": "fresh-from-disk",
        }

        refreshed_data = {
            "access_token": valid_token,
            "refresh_token": "new.refresh",
        }

        auth_manager._metadata = {"token_endpoint": "https://dex.example.com/token"}
        auth_manager._client = Mock()

        with patch.object(auth_manager, "_read_token_file", return_value=disk_data):
            with patch.object(
                auth_manager, "_do_refresh_token", new_callable=AsyncMock
            ) as mock_refresh:
                mock_refresh.return_value = refreshed_data
                with patch("evergreen_mcp.oidc_auth.AsyncFileLock") as mock_afl:
                    async_cm = AsyncMock()
                    async_cm.__aenter__ = AsyncMock(return_value=None)
                    async_cm.__aexit__ = AsyncMock(return_value=False)
                    mock_afl.return_value = async_cm

                    result = await auth_manager.refresh_token()

                    assert result is not None
                    # Verify the refresh token passed to _do_refresh_token
                    # came from disk, not from any in-memory field
                    mock_refresh.assert_called_once_with("fresh-from-disk")

    @pytest.mark.asyncio
    async def test_no_refresh_token_attribute_on_manager(self, auth_manager):
        """OIDCAuthManager should not have a _refresh_token attribute."""
        assert not hasattr(auth_manager, "_refresh_token")

    @pytest.mark.asyncio
    async def test_concurrent_refresh_serialized_by_file_lock(
        self, auth_manager, expired_jwt_claims, valid_jwt_claims
    ):
        """Multiple concurrent refresh calls are serialized by the file lock."""
        expired_token = create_mock_jwt(expired_jwt_claims)
        valid_token = create_mock_jwt(valid_jwt_claims)

        call_count = 0

        # First call: disk has expired token, refresh succeeds
        # Second call: disk has valid token (written by first call), returns early
        def read_token_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "access_token": expired_token,
                    "refresh_token": "disk.refresh",
                }
            else:
                # After first refresh, disk has valid token
                return {
                    "access_token": valid_token,
                    "refresh_token": "new.refresh",
                }

        refreshed_data = {
            "access_token": valid_token,
            "refresh_token": "new.refresh",
        }

        auth_manager._metadata = {"token_endpoint": "https://dex.example.com/token"}
        auth_manager._client = Mock()

        with patch.object(
            auth_manager, "_read_token_file", side_effect=read_token_side_effect
        ):
            with patch.object(
                auth_manager, "_do_refresh_token", new_callable=AsyncMock
            ) as mock_refresh:
                mock_refresh.return_value = refreshed_data
                with patch("evergreen_mcp.oidc_auth.AsyncFileLock") as mock_afl:
                    async_cm = AsyncMock()
                    async_cm.__aenter__ = AsyncMock(return_value=None)
                    async_cm.__aexit__ = AsyncMock(return_value=False)
                    mock_afl.return_value = async_cm

                    # Two sequential calls (file lock makes them serial)
                    r1 = await auth_manager.refresh_token()
                    r2 = await auth_manager.refresh_token()

                    assert r1 is not None
                    assert r2 is not None
                    # Only first call should have done HTTP refresh
                    mock_refresh.assert_called_once()


class TestAtomicFileWrites:
    """Test that _save_token uses random temp file names."""

    def test_uses_named_temporary_file(self, auth_manager):
        """Verify NamedTemporaryFile is used with correct parameters."""
        token_data = {"access_token": "test.token"}

        mock_tmp = MagicMock()
        mock_tmp.name = "/tmp/.tmp_token_random123.json"

        with patch.object(Path, "mkdir"):
            with patch(
                "tempfile.NamedTemporaryFile", return_value=mock_tmp
            ) as mock_ntf:
                with patch("os.fsync"):
                    with patch.object(Path, "replace"):
                        auth_manager._save_token(token_data)

                        mock_ntf.assert_called_once()
                        call_kwargs = mock_ntf.call_args
                        # Verify temp file is in same directory as token file
                        assert call_kwargs.kwargs["dir"] == str(
                            auth_manager.token_file.parent
                        )
                        assert call_kwargs.kwargs["prefix"] == ".tmp_token_"
                        assert call_kwargs.kwargs["suffix"] == ".json"
                        assert call_kwargs.kwargs["delete"] is False

    def test_temp_file_in_same_directory_as_token(self, auth_manager):
        """Verify temp file is created in the same directory as the token file."""
        token_data = {"access_token": "test.token"}

        captured_dir = None

        def capture_ntf(**kwargs):
            nonlocal captured_dir
            captured_dir = kwargs.get("dir")
            mock = MagicMock()
            mock.name = f"{captured_dir}/.tmp_token_abc.json"
            return mock

        with patch.object(Path, "mkdir"):
            with patch("tempfile.NamedTemporaryFile", side_effect=capture_ntf):
                with patch("os.fsync"):
                    with patch.object(Path, "replace"):
                        auth_manager._save_token(token_data)

        assert captured_dir == str(auth_manager.token_file.parent)


class TestStateFileConsistency:
    """Test that _save_token is called before in-memory state is updated."""

    @pytest.mark.asyncio
    async def test_refresh_saves_before_memory_update(
        self, auth_manager, valid_jwt_claims
    ):
        """In _do_refresh_token, _save_token is called before updating memory."""
        token = create_mock_jwt(valid_jwt_claims)
        auth_manager._metadata = {"token_endpoint": "https://dex.example.com/token"}
        auth_manager._client = Mock()

        new_token_data = {
            "access_token": token,
            "refresh_token": "new.refresh.token",
        }

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = new_token_data

        state_at_save_time = {}

        def capture_state_at_save(token_data):
            # Record what the in-memory state was when _save_token was called
            state_at_save_time["access_token"] = auth_manager._access_token

        with patch("evergreen_mcp.oidc_auth.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            with patch.object(
                auth_manager, "_save_token", side_effect=capture_state_at_save
            ):
                result = await auth_manager._do_refresh_token("old.refresh.token")

        # At the time _save_token was called, in-memory state should still
        # have the OLD values (save happens before memory update)
        assert state_at_save_time["access_token"] is None  # Was not yet set

        # After the method returns, memory should be updated
        assert auth_manager._access_token == token

    @pytest.mark.asyncio
    async def test_poll_device_flow_saves_before_memory_update(
        self, auth_manager, valid_jwt_claims
    ):
        """In poll_device_flow, _save_token is called before updating memory."""
        token = create_mock_jwt(valid_jwt_claims)
        auth_manager._metadata = {"token_endpoint": "https://dex.example.com/token"}
        auth_manager._client = Mock()

        token_response = {
            "access_token": token,
            "refresh_token": "new.refresh.token",
        }

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = token_response

        state_at_save_time = {}

        def capture_state_at_save(token_data):
            state_at_save_time["access_token"] = auth_manager._access_token

        with patch("evergreen_mcp.oidc_auth.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            with patch.object(
                auth_manager, "_save_token", side_effect=capture_state_at_save
            ):
                result = await auth_manager.poll_device_flow("device123")

        # At save time, in-memory state should still have old values
        assert state_at_save_time["access_token"] is None

        # After method returns, memory is updated
        assert auth_manager._access_token == token


class TestTOCTOUFix:
    """Test that _read_token_file handles FileNotFoundError without exists()."""

    def test_handles_file_not_found(self, auth_manager):
        """_read_token_file catches FileNotFoundError from open()."""
        with patch("builtins.open", side_effect=FileNotFoundError("No such file")):
            result = auth_manager._read_token_file()
            assert result is None

    def test_does_not_call_exists(self, auth_manager, valid_jwt_claims):
        """_read_token_file does not call self.token_file.exists()."""
        token = create_mock_jwt(valid_jwt_claims)
        token_data = {"access_token": token, "refresh_token": "refresh"}

        with patch("builtins.open", mock_open(read_data=json.dumps(token_data))):
            with patch.object(Path, "exists") as mock_exists:
                auth_manager._read_token_file()
                # exists() should never be called on the token file path
                mock_exists.assert_not_called()

    def test_handles_other_exceptions(self, auth_manager):
        """_read_token_file catches generic exceptions from open()."""
        with patch("builtins.open", side_effect=PermissionError("Permission denied")):
            result = auth_manager._read_token_file()
            assert result is None

    def test_handles_json_decode_error(self, auth_manager):
        """_read_token_file catches JSON decode errors."""
        with patch("builtins.open", mock_open(read_data="not json{")):
            result = auth_manager._read_token_file()
            assert result is None
