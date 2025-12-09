#!/usr/bin/env python3
"""
Unit tests for OIDC authentication module

These tests validate the OIDCAuthManager class including:
- Token expiry checking
- User info extraction from JWT
- Token refresh logic
- Device flow authentication
- Token file handling
"""

import asyncio
import base64
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, mock_open, patch

import pytest

from evergreen_mcp.oidc_auth import (
    DEFAULT_KANOPY_TOKEN_FILE,
    EVERGREEN_CONFIG_FILE,
    HTTP_TIMEOUT,
    OIDCAuthenticationError,
    OIDCAuthManager,
    _load_oauth_config_from_evergreen_yml,
)


@pytest.fixture
def auth_manager():
    """Create a fresh OIDCAuthManager instance for each test."""
    with patch.object(Path, "exists", return_value=False):
        return OIDCAuthManager()


@pytest.fixture
def auth_manager_with_config():
    """Create OIDCAuthManager with mocked config."""
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


@pytest.fixture
def valid_jwt_claims():
    """Generate valid JWT claims for testing."""
    return {
        "sub": "test-user-id",
        "email": "test@mongodb.com",
        "preferred_username": "test",
        "name": "Test User",
        "groups": ["team1", "team2"],
        "exp": int(time.time()) + 3600,  # Expires in 1 hour
        "iat": int(time.time()),
    }


@pytest.fixture
def expired_jwt_claims(valid_jwt_claims):
    """Generate expired JWT claims for testing."""
    claims = valid_jwt_claims.copy()
    claims["exp"] = int(time.time()) - 3600  # Expired 1 hour ago
    return claims


def create_mock_jwt(claims: dict) -> str:
    """Create a mock JWT token from claims."""
    header = (
        base64.urlsafe_b64encode(b'{"alg":"RS256","typ":"JWT"}').decode().rstrip("=")
    )
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    signature = base64.urlsafe_b64encode(b"mock_signature").decode().rstrip("=")
    return f"{header}.{payload}.{signature}"


class TestLoadOAuthConfig:
    """Test loading OAuth config from evergreen.yml."""

    def test_load_config_file_not_exists(self):
        """Test loading config when file doesn't exist."""
        with patch.object(Path, "exists", return_value=False):
            config = _load_oauth_config_from_evergreen_yml()
            assert config == {}

    def test_load_config_success(self):
        """Test successful config loading."""
        mock_config = {
            "oauth": {
                "issuer": "https://dex.example.com",
                "client_id": "test-client",
            }
        }
        with patch.object(Path, "exists", return_value=True):
            with patch("builtins.open", mock_open(read_data="")):
                with patch("yaml.safe_load", return_value=mock_config):
                    config = _load_oauth_config_from_evergreen_yml()
                    assert config["issuer"] == "https://dex.example.com"
                    assert config["client_id"] == "test-client"

    def test_load_config_no_oauth_section(self):
        """Test loading config without oauth section."""
        mock_config = {"user": "testuser", "api_key": "testkey"}
        with patch.object(Path, "exists", return_value=True):
            with patch("builtins.open", mock_open(read_data="")):
                with patch("yaml.safe_load", return_value=mock_config):
                    config = _load_oauth_config_from_evergreen_yml()
                    assert config == {}

    def test_load_config_error(self):
        """Test loading config with error."""
        with patch.object(Path, "exists", return_value=True):
            with patch("builtins.open", side_effect=Exception("Read error")):
                config = _load_oauth_config_from_evergreen_yml()
                assert config == {}


class TestOIDCAuthManagerInit:
    """Test OIDCAuthManager initialization."""

    def test_init_defaults(self, auth_manager):
        """Test that manager initializes with None defaults when no config."""
        assert auth_manager.issuer is None
        assert auth_manager.client_id is None
        assert auth_manager.kanopy_token_file == DEFAULT_KANOPY_TOKEN_FILE
        assert auth_manager._access_token is None
        assert auth_manager._refresh_token is None
        assert auth_manager._user_info == {}
        assert auth_manager._client is None
        assert auth_manager._metadata is None
        assert isinstance(auth_manager._refresh_lock, asyncio.Lock)
        assert isinstance(auth_manager._auth_lock, asyncio.Lock)

    def test_init_with_config(self, auth_manager_with_config):
        """Test initialization with config from evergreen.yml."""
        assert auth_manager_with_config.issuer == "https://dex.example.com"
        assert auth_manager_with_config.client_id == "test-client-id"
        assert auth_manager_with_config.kanopy_token_file == Path(
            "/tmp/test-token.json"
        )


class TestGetClient:
    """Test OAuth2 client initialization."""

    @pytest.mark.asyncio
    async def test_get_client_success(self, auth_manager_with_config):
        """Test successful client initialization."""
        mock_metadata = {
            "device_authorization_endpoint": "https://dex.example.com/device",
            "token_endpoint": "https://dex.example.com/token",
            "jwks_uri": "https://dex.example.com/keys",
        }

        mock_response = Mock()
        mock_response.json.return_value = mock_metadata
        mock_response.raise_for_status = Mock()

        with patch("evergreen_mcp.oidc_auth.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            client = await auth_manager_with_config._get_client()

            assert client is not None
            assert auth_manager_with_config._metadata == mock_metadata

    @pytest.mark.asyncio
    async def test_get_client_cached(self, auth_manager_with_config):
        """Test that client is cached after first initialization."""
        mock_client = Mock()
        auth_manager_with_config._client = mock_client
        auth_manager_with_config._metadata = {
            "token_endpoint": "https://example.com/token"
        }

        result = await auth_manager_with_config._get_client()

        assert result is mock_client

    @pytest.mark.asyncio
    async def test_get_client_network_error(self, auth_manager_with_config):
        """Test client initialization with network error."""
        with patch("evergreen_mcp.oidc_auth.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=Exception("Network error"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            with pytest.raises(Exception, match="Network error"):
                await auth_manager_with_config._get_client()


class TestTokenExpiry:
    """Test token expiry checking."""

    def test_check_token_expiry_valid_with_expires_at(self, auth_manager):
        """Test checking expiry with expires_at field."""
        token_data = {
            "access_token": "test.token",
            "expires_at": time.time() + 3600,  # 1 hour from now
        }
        is_valid, remaining = auth_manager._check_token_expiry(token_data)
        assert is_valid is True
        assert remaining > 3500  # Should be close to 3600

    def test_check_token_expiry_expired_with_expires_at(self, auth_manager):
        """Test checking expiry with expired expires_at field."""
        token_data = {
            "access_token": "test.token",
            "expires_at": time.time() - 3600,  # 1 hour ago
        }
        is_valid, remaining = auth_manager._check_token_expiry(token_data)
        assert is_valid is False
        assert remaining < 0

    def test_check_token_expiry_valid_from_jwt(self, auth_manager, valid_jwt_claims):
        """Test checking expiry from JWT token."""
        token = create_mock_jwt(valid_jwt_claims)
        token_data = {"access_token": token}

        is_valid, remaining = auth_manager._check_token_expiry(token_data)
        assert is_valid is True
        assert remaining > 0

    def test_check_token_expiry_expired_from_jwt(
        self, auth_manager, expired_jwt_claims
    ):
        """Test checking expiry from expired JWT token."""
        token = create_mock_jwt(expired_jwt_claims)
        token_data = {"access_token": token}

        is_valid, remaining = auth_manager._check_token_expiry(token_data)
        assert is_valid is False
        assert remaining < 0

    def test_check_token_expiry_no_token(self, auth_manager):
        """Test checking expiry with no token."""
        token_data = {}
        is_valid, remaining = auth_manager._check_token_expiry(token_data)
        assert is_valid is False
        assert remaining == 0

    def test_check_token_expiry_invalid_jwt(self, auth_manager):
        """Test checking expiry with invalid JWT format."""
        token_data = {"access_token": "not.a.valid.jwt.token"}
        # Should assume valid if can't decode
        is_valid, remaining = auth_manager._check_token_expiry(token_data)
        assert is_valid is True


class TestUserInfoExtraction:
    """Test user info extraction from JWT tokens."""

    def test_extract_user_info_success(self, auth_manager, valid_jwt_claims):
        """Test successful user info extraction."""
        token = create_mock_jwt(valid_jwt_claims)
        user_info = auth_manager._extract_user_info(token)

        assert user_info["username"] == "test"
        assert user_info["email"] == "test@mongodb.com"
        assert user_info["name"] == "Test User"
        assert user_info["groups"] == ["team1", "team2"]
        assert user_info["exp"] == valid_jwt_claims["exp"]

    def test_extract_user_info_minimal_claims(self, auth_manager):
        """Test user info extraction with minimal claims."""
        minimal_claims = {
            "sub": "user-123",
            "exp": int(time.time()) + 3600,
        }
        token = create_mock_jwt(minimal_claims)
        user_info = auth_manager._extract_user_info(token)

        assert user_info["username"] == "user-123"  # Falls back to sub
        assert user_info["email"] is None
        assert user_info["name"] is None
        assert user_info["groups"] == []

    def test_extract_user_info_invalid_token(self, auth_manager):
        """Test user info extraction from invalid token."""
        user_info = auth_manager._extract_user_info("invalid.token")
        assert user_info == {}


class TestKanopyTokenCheck:
    """Test Kanopy token file checking."""

    def test_check_kanopy_token_success(self, auth_manager, valid_jwt_claims):
        """Test successful Kanopy token check."""
        token = create_mock_jwt(valid_jwt_claims)
        token_data = {
            "access_token": token,
            "refresh_token": "valid.refresh.token",
        }

        with patch.object(Path, "exists", return_value=True):
            with patch("builtins.open", mock_open(read_data=json.dumps(token_data))):
                result = auth_manager.check_kanopy_token()

                assert result is not None
                assert result["access_token"] == token
                assert result["refresh_token"] == "valid.refresh.token"

    def test_check_kanopy_token_file_not_found(self, auth_manager):
        """Test Kanopy token check when file doesn't exist."""
        with patch.object(Path, "exists", return_value=False):
            result = auth_manager.check_kanopy_token()
            assert result is None

    def test_check_kanopy_token_expired(self, auth_manager, expired_jwt_claims):
        """Test Kanopy token check with expired token."""
        token = create_mock_jwt(expired_jwt_claims)
        token_data = {
            "access_token": token,
            "refresh_token": "valid.refresh.token",
        }

        with patch.object(Path, "exists", return_value=True):
            with patch("builtins.open", mock_open(read_data=json.dumps(token_data))):
                result = auth_manager.check_kanopy_token()
                assert result is None

    def test_check_kanopy_token_invalid_json(self, auth_manager):
        """Test Kanopy token check with invalid JSON."""
        with patch.object(Path, "exists", return_value=True):
            with patch("builtins.open", mock_open(read_data="invalid json{")):
                result = auth_manager.check_kanopy_token()
                assert result is None

    def test_check_kanopy_token_fallback_to_default(self, auth_manager):
        """Test fallback to default token location."""
        # Configure a custom path
        auth_manager.kanopy_token_file = Path("/custom/path/token.json")

        token_data = {
            "access_token": create_mock_jwt(
                {"sub": "test", "exp": int(time.time()) + 3600}
            ),
        }

        def exists_side_effect(self):
            # Custom path doesn't exist, default does
            if str(self) == "/custom/path/token.json":
                return False
            return True

        with patch.object(Path, "exists", exists_side_effect):
            with patch("builtins.open", mock_open(read_data=json.dumps(token_data))):
                result = auth_manager.check_kanopy_token()
                assert result is not None


class TestTokenRefresh:
    """Test token refresh functionality."""

    @pytest.mark.asyncio
    async def test_refresh_token_success(self, auth_manager_with_config):
        """Test successful token refresh."""
        auth_manager_with_config._refresh_token = "valid.refresh.token"
        auth_manager_with_config._metadata = {
            "token_endpoint": "https://dex.example.com/token"
        }
        auth_manager_with_config._client = Mock()  # Mark as initialized

        new_token_data = {
            "access_token": "new.access.token",
            "refresh_token": "new.refresh.token",
        }

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = new_token_data

        with patch("evergreen_mcp.oidc_auth.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            with patch.object(auth_manager_with_config, "_save_token"):
                result = await auth_manager_with_config.refresh_token()

                assert result is not None
                assert result["access_token"] == "new.access.token"
                assert auth_manager_with_config._access_token == "new.access.token"
                assert auth_manager_with_config._refresh_token == "new.refresh.token"

    @pytest.mark.asyncio
    async def test_refresh_token_no_refresh_token(self, auth_manager):
        """Test token refresh without refresh token."""
        auth_manager._refresh_token = None

        result = await auth_manager.refresh_token()
        assert result is None

    @pytest.mark.asyncio
    async def test_refresh_token_already_valid(
        self, auth_manager_with_config, valid_jwt_claims
    ):
        """Test that refresh is skipped if token is already valid."""
        token = create_mock_jwt(valid_jwt_claims)
        auth_manager_with_config._refresh_token = "valid.refresh.token"
        auth_manager_with_config._access_token = token
        auth_manager_with_config._metadata = {
            "token_endpoint": "https://dex.example.com/token"
        }

        result = await auth_manager_with_config.refresh_token()

        # Should return existing token data without making network call
        assert result is not None
        assert result["access_token"] == token

    @pytest.mark.asyncio
    async def test_refresh_token_server_error(self, auth_manager_with_config):
        """Test token refresh with server error."""
        auth_manager_with_config._refresh_token = "valid.refresh.token"
        auth_manager_with_config._access_token = None
        auth_manager_with_config._metadata = {
            "token_endpoint": "https://dex.example.com/token"
        }
        auth_manager_with_config._client = Mock()  # Mark as initialized

        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.text = "Invalid refresh token"

        with patch("evergreen_mcp.oidc_auth.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            result = await auth_manager_with_config.refresh_token()
            assert result is None


class TestSaveToken:
    """Test token file saving."""

    def test_save_token_success(self, auth_manager):
        """Test successful token save."""
        token_data = {
            "access_token": "test.access.token",
            "refresh_token": "test.refresh.token",
        }

        with patch.object(Path, "mkdir"):
            with patch("builtins.open", mock_open()) as m:
                with patch("os.fsync"):
                    with patch.object(Path, "replace"):
                        with patch.object(Path, "with_suffix") as mock_suffix:
                            temp_path = Mock(spec=Path)
                            temp_path.exists.return_value = False
                            mock_suffix.return_value = temp_path

                            auth_manager._save_token(token_data)

                            # Verify file was written
                            m.assert_called()

    def test_save_token_cleanup_on_error(self, auth_manager):
        """Test that temp file is cleaned up on error."""
        token_data = {"access_token": "test.token"}

        with patch.object(Path, "mkdir"):
            with patch("builtins.open", side_effect=OSError("Write error")):
                with patch.object(Path, "with_suffix") as mock_suffix:
                    temp_path = Mock(spec=Path)
                    temp_path.exists.return_value = True
                    temp_path.unlink = Mock()
                    mock_suffix.return_value = temp_path

                    with pytest.raises(OSError):
                        auth_manager._save_token(token_data)

                    # Verify temp file cleanup was attempted
                    temp_path.unlink.assert_called_once()


class TestDeviceFlowAuth:
    """Test device authorization flow."""

    @pytest.mark.asyncio
    async def test_device_flow_auth_success(self, auth_manager_with_config):
        """Test successful device flow authentication."""
        auth_manager_with_config._metadata = {
            "device_authorization_endpoint": "https://dex.example.com/device",
            "token_endpoint": "https://dex.example.com/token",
        }
        auth_manager_with_config._client = Mock()

        device_response = {
            "device_code": "device123",
            "user_code": "USER123",
            "verification_uri": "https://dex.example.com/verify",
            "interval": 1,
        }

        token_response = {
            "access_token": "new.access.token",
            "refresh_token": "new.refresh.token",
        }

        device_mock = Mock()
        device_mock.json.return_value = device_response
        device_mock.raise_for_status = Mock()

        token_mock = Mock()
        token_mock.status_code = 200
        token_mock.json.return_value = token_response

        with patch("evergreen_mcp.oidc_auth.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=[device_mock, token_mock])
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            with patch("webbrowser.open"):
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    with patch.object(auth_manager_with_config, "_save_token"):
                        result = await auth_manager_with_config.device_flow_auth()

                        assert result is not None
                        assert result["access_token"] == "new.access.token"
                        assert (
                            auth_manager_with_config._access_token == "new.access.token"
                        )
                        assert (
                            auth_manager_with_config._refresh_token
                            == "new.refresh.token"
                        )

    @pytest.mark.asyncio
    async def test_device_flow_auth_pending_then_success(
        self, auth_manager_with_config
    ):
        """Test device flow with authorization pending."""
        auth_manager_with_config._metadata = {
            "device_authorization_endpoint": "https://dex.example.com/device",
            "token_endpoint": "https://dex.example.com/token",
        }
        auth_manager_with_config._client = Mock()

        device_response = {
            "device_code": "device123",
            "user_code": "USER123",
            "verification_uri": "https://dex.example.com/verify",
            "interval": 1,
        }

        device_mock = Mock()
        device_mock.json.return_value = device_response
        device_mock.raise_for_status = Mock()

        # First poll: pending
        pending_mock = Mock()
        pending_mock.status_code = 400
        pending_mock.json.return_value = {"error": "authorization_pending"}

        # Second poll: success
        success_mock = Mock()
        success_mock.status_code = 200
        success_mock.json.return_value = {
            "access_token": "new.token",
            "refresh_token": "refresh.token",
        }

        with patch("evergreen_mcp.oidc_auth.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(
                side_effect=[device_mock, pending_mock, success_mock]
            )
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            with patch("webbrowser.open"):
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    with patch.object(auth_manager_with_config, "_save_token"):
                        result = await auth_manager_with_config.device_flow_auth()
                        assert result is not None
                        assert result["access_token"] == "new.token"

    @pytest.mark.asyncio
    async def test_device_flow_auth_expired(self, auth_manager_with_config):
        """Test device flow with expired device code."""
        auth_manager_with_config._metadata = {
            "device_authorization_endpoint": "https://dex.example.com/device",
            "token_endpoint": "https://dex.example.com/token",
        }
        auth_manager_with_config._client = Mock()

        device_response = {
            "device_code": "device123",
            "user_code": "USER123",
            "verification_uri": "https://dex.example.com/verify",
            "interval": 1,
        }

        device_mock = Mock()
        device_mock.json.return_value = device_response
        device_mock.raise_for_status = Mock()

        expired_mock = Mock()
        expired_mock.status_code = 400
        expired_mock.json.return_value = {"error": "expired_token"}

        with patch("evergreen_mcp.oidc_auth.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=[device_mock, expired_mock])
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            with patch("webbrowser.open"):
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    result = await auth_manager_with_config.device_flow_auth()
                    assert result is None


class TestEnsureAuthenticated:
    """Test the main authentication flow."""

    @pytest.mark.asyncio
    async def test_ensure_authenticated_with_existing_valid_token(
        self, auth_manager_with_config, valid_jwt_claims
    ):
        """Test that already authenticated state is recognized."""
        token = create_mock_jwt(valid_jwt_claims)
        auth_manager_with_config._access_token = token
        auth_manager_with_config._refresh_token = "refresh.token"

        result = await auth_manager_with_config.ensure_authenticated()

        assert result is True

    @pytest.mark.asyncio
    async def test_ensure_authenticated_with_kanopy_token(
        self, auth_manager_with_config, valid_jwt_claims
    ):
        """Test authentication using Kanopy token."""
        token = create_mock_jwt(valid_jwt_claims)
        token_data = {"access_token": token, "refresh_token": "refresh"}

        with patch.object(
            auth_manager_with_config, "_get_client", new_callable=AsyncMock
        ):
            with patch.object(
                auth_manager_with_config, "check_kanopy_token", return_value=token_data
            ):
                result = await auth_manager_with_config.ensure_authenticated()

                assert result is True
                assert auth_manager_with_config._access_token == token

    @pytest.mark.asyncio
    async def test_ensure_authenticated_with_refresh(
        self, auth_manager_with_config, valid_jwt_claims
    ):
        """Test authentication using token refresh."""
        token = create_mock_jwt(valid_jwt_claims)
        token_data = {"access_token": token, "refresh_token": "new.refresh"}

        auth_manager_with_config._refresh_token = "old.refresh.token"

        with patch.object(
            auth_manager_with_config, "_get_client", new_callable=AsyncMock
        ):
            with patch.object(
                auth_manager_with_config, "check_kanopy_token", return_value=None
            ):
                with patch.object(
                    auth_manager_with_config, "refresh_token", new_callable=AsyncMock
                ) as mock_refresh:
                    mock_refresh.return_value = token_data

                    result = await auth_manager_with_config.ensure_authenticated()

                    assert result is True
                    mock_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_ensure_authenticated_with_device_flow(
        self, auth_manager_with_config, valid_jwt_claims
    ):
        """Test authentication using device flow."""
        token = create_mock_jwt(valid_jwt_claims)
        token_data = {"access_token": token, "refresh_token": "refresh"}

        with patch.object(
            auth_manager_with_config, "_get_client", new_callable=AsyncMock
        ):
            with patch.object(
                auth_manager_with_config, "check_kanopy_token", return_value=None
            ):
                with patch.object(
                    auth_manager_with_config, "device_flow_auth", new_callable=AsyncMock
                ) as mock_device:
                    mock_device.return_value = token_data

                    result = await auth_manager_with_config.ensure_authenticated()

                    assert result is True
                    mock_device.assert_called_once()

    @pytest.mark.asyncio
    async def test_ensure_authenticated_all_methods_fail(
        self, auth_manager_with_config
    ):
        """Test authentication when all methods fail."""
        with patch.object(
            auth_manager_with_config, "_get_client", new_callable=AsyncMock
        ):
            with patch.object(
                auth_manager_with_config, "check_kanopy_token", return_value=None
            ):
                with patch.object(
                    auth_manager_with_config, "device_flow_auth", new_callable=AsyncMock
                ) as mock_device:
                    mock_device.return_value = None

                    result = await auth_manager_with_config.ensure_authenticated()

                    assert result is False


class TestIsAuthenticated:
    """Test non-blocking authentication status check."""

    def test_is_authenticated_true(self, auth_manager, valid_jwt_claims):
        """Test is_authenticated returns True for valid token."""
        token = create_mock_jwt(valid_jwt_claims)
        auth_manager._access_token = token

        assert auth_manager.is_authenticated() is True

    def test_is_authenticated_false_no_token(self, auth_manager):
        """Test is_authenticated returns False when no token."""
        auth_manager._access_token = None
        assert auth_manager.is_authenticated() is False

    def test_is_authenticated_false_expired(self, auth_manager, expired_jwt_claims):
        """Test is_authenticated returns False for expired token."""
        token = create_mock_jwt(expired_jwt_claims)
        auth_manager._access_token = token

        assert auth_manager.is_authenticated() is False


class TestProperties:
    """Test property accessors."""

    def test_user_info_property(self, auth_manager):
        """Test user_info property."""
        test_info = {"username": "test", "email": "test@example.com"}
        auth_manager._user_info = test_info

        assert auth_manager.user_info == test_info

    def test_access_token_property(self, auth_manager):
        """Test access_token property."""
        test_token = "test.access.token"
        auth_manager._access_token = test_token

        assert auth_manager.access_token == test_token

    def test_user_id_property_from_email(self, auth_manager):
        """Test user_id property extraction from email."""
        auth_manager._user_info = {"email": "testuser@mongodb.com"}

        assert auth_manager.user_id == "testuser"

    def test_user_id_property_from_username(self, auth_manager):
        """Test user_id property fallback to username."""
        auth_manager._user_info = {"username": "testuser"}

        assert auth_manager.user_id == "testuser"

    def test_user_id_property_no_info(self, auth_manager):
        """Test user_id property with no user info."""
        auth_manager._user_info = {}

        assert auth_manager.user_id is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
