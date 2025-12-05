#!/usr/bin/env python3
"""
Unit tests for OIDC authentication module

These tests validate the OIDCAuthManager class including:
- JWT token validation and verification
- Token expiry checking
- Token refresh logic
- Device flow authentication
- Error handling and edge cases
"""

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, mock_open, patch

import jwt
import pytest
from aiohttp import ClientError, ClientTimeout
from jwt import PyJWKClient

from evergreen_mcp.oidc_auth import (
    DEX_CLIENT_ID,
    DEX_ISSUER,
    EVERGREEN_CONFIG_FILE,
    HTTP_TIMEOUT_DEVICE_POLL,
    HTTP_TIMEOUT_METADATA,
    HTTP_TIMEOUT_TOKEN,
    KANOPY_TOKEN_FILE,
    REQUIRED_SCOPES,
    OIDCAuthManager,
)


@pytest.fixture
def auth_manager():
    """Create a fresh OIDCAuthManager instance for each test."""
    return OIDCAuthManager()


@pytest.fixture
def mock_jwks_client():
    """Create a mock PyJWKClient."""
    client = Mock(spec=PyJWKClient)
    signing_key = Mock()
    signing_key.key = "test_key"
    client.get_signing_key_from_jwt.return_value = signing_key
    return client


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
        "iss": DEX_ISSUER,
        "aud": DEX_CLIENT_ID,
    }


@pytest.fixture
def expired_jwt_claims(valid_jwt_claims):
    """Generate expired JWT claims for testing."""
    claims = valid_jwt_claims.copy()
    claims["exp"] = int(time.time()) - 3600  # Expired 1 hour ago
    return claims


class TestOIDCAuthManagerInit:
    """Test OIDCAuthManager initialization."""

    def test_init_defaults(self, auth_manager):
        """Test that manager initializes with correct defaults."""
        assert auth_manager.issuer == DEX_ISSUER
        assert auth_manager.client_id == DEX_CLIENT_ID
        assert auth_manager.device_auth_endpoint is None
        assert auth_manager.token_endpoint is None
        assert auth_manager._access_token is None
        assert auth_manager._refresh_token is None
        assert auth_manager._user_info == {}
        assert auth_manager._jwks_client is None
        assert isinstance(auth_manager._refresh_lock, asyncio.Lock)
        assert isinstance(auth_manager._auth_lock, asyncio.Lock)


class TestInitializeEndpoints:
    """Test OIDC endpoint initialization."""

    @pytest.mark.asyncio
    async def test_initialize_endpoints_success(self, auth_manager):
        """Test successful endpoint initialization."""
        mock_metadata = {
            "device_authorization_endpoint": "https://dex.example.com/device",
            "token_endpoint": "https://dex.example.com/token",
            "jwks_uri": "https://dex.example.com/keys",
        }

        with patch("aiohttp.ClientSession") as mock_session:
            mock_response = AsyncMock()
            mock_response.raise_for_status = Mock()
            mock_response.json = AsyncMock(return_value=mock_metadata)

            # Create proper context manager mock
            mock_get_context = AsyncMock()
            mock_get_context.__aenter__ = AsyncMock(return_value=mock_response)
            mock_get_context.__aexit__ = AsyncMock(return_value=None)

            mock_session_instance = AsyncMock()
            mock_session_instance.get = Mock(return_value=mock_get_context)
            mock_session_instance.__aenter__.return_value = mock_session_instance
            mock_session_instance.__aexit__.return_value = None
            mock_session.return_value = mock_session_instance

            with patch("evergreen_mcp.oidc_auth.PyJWKClient") as mock_jwks:
                await auth_manager.initialize_endpoints()

                assert (
                    auth_manager.device_auth_endpoint
                    == "https://dex.example.com/device"
                )
                assert auth_manager.token_endpoint == "https://dex.example.com/token"
                assert auth_manager._jwks_uri == "https://dex.example.com/keys"
                mock_jwks.assert_called_once()

    @pytest.mark.asyncio
    async def test_initialize_endpoints_timeout(self, auth_manager):
        """Test endpoint initialization with timeout."""
        with patch("aiohttp.ClientSession") as mock_session:
            # Create context manager mock that raises on __aenter__
            mock_get_context = AsyncMock()
            mock_get_context.__aenter__ = AsyncMock(side_effect=asyncio.TimeoutError())
            mock_get_context.__aexit__ = AsyncMock(return_value=None)

            mock_session_instance = AsyncMock()
            mock_session_instance.get = Mock(return_value=mock_get_context)
            mock_session_instance.__aenter__.return_value = mock_session_instance
            mock_session_instance.__aexit__.return_value = None
            mock_session.return_value = mock_session_instance

            with pytest.raises(asyncio.TimeoutError):
                await auth_manager.initialize_endpoints()

    @pytest.mark.asyncio
    async def test_initialize_endpoints_network_error(self, auth_manager):
        """Test endpoint initialization with network error."""
        with patch("aiohttp.ClientSession") as mock_session:
            # Create context manager mock that raises on __aenter__
            mock_get_context = AsyncMock()
            mock_get_context.__aenter__ = AsyncMock(
                side_effect=ClientError("Network error")
            )
            mock_get_context.__aexit__ = AsyncMock(return_value=None)

            mock_session_instance = AsyncMock()
            mock_session_instance.get = Mock(return_value=mock_get_context)
            mock_session_instance.__aenter__.return_value = mock_session_instance
            mock_session_instance.__aexit__.return_value = None
            mock_session.return_value = mock_session_instance

            with pytest.raises(ClientError):
                await auth_manager.initialize_endpoints()


class TestTokenVerificationAndDecoding:
    """Test JWT token verification and decoding."""

    def test_verify_and_decode_token_success(self, auth_manager, valid_jwt_claims):
        """Test successful token verification."""
        test_token = "test.jwt.token"
        auth_manager._jwks_client = Mock(spec=PyJWKClient)

        signing_key = Mock()
        signing_key.key = "test_key"
        auth_manager._jwks_client.get_signing_key_from_jwt.return_value = signing_key

        with patch("jwt.decode", return_value=valid_jwt_claims):
            claims = auth_manager._verify_and_decode_token(test_token, verify=True)

            assert claims == valid_jwt_claims
            auth_manager._jwks_client.get_signing_key_from_jwt.assert_called_once_with(
                test_token
            )

    def test_verify_and_decode_token_expired(self, auth_manager, expired_jwt_claims):
        """Test token verification with expired token."""
        test_token = "expired.jwt.token"
        auth_manager._jwks_client = Mock(spec=PyJWKClient)

        signing_key = Mock()
        signing_key.key = "test_key"
        auth_manager._jwks_client.get_signing_key_from_jwt.return_value = signing_key

        with patch(
            "jwt.decode", side_effect=jwt.ExpiredSignatureError("Token expired")
        ):
            claims = auth_manager._verify_and_decode_token(test_token, verify=True)
            assert claims is None

    def test_verify_and_decode_token_invalid(self, auth_manager):
        """Test token verification with invalid token."""
        test_token = "invalid.jwt.token"
        auth_manager._jwks_client = Mock(spec=PyJWKClient)

        signing_key = Mock()
        signing_key.key = "test_key"
        auth_manager._jwks_client.get_signing_key_from_jwt.return_value = signing_key

        with patch("jwt.decode", side_effect=jwt.InvalidTokenError("Invalid token")):
            claims = auth_manager._verify_and_decode_token(test_token, verify=True)
            assert claims is None

    def test_verify_and_decode_token_no_jwks_client(self, auth_manager):
        """Test token verification without JWKS client initialized."""
        test_token = "test.jwt.token"
        auth_manager._jwks_client = None

        claims = auth_manager._verify_and_decode_token(test_token, verify=True)
        assert claims is None

    def test_verify_and_decode_token_no_verification(
        self, auth_manager, valid_jwt_claims
    ):
        """Test token decoding without verification (insecure mode)."""
        test_token = "test.jwt.token"

        with patch("jwt.decode", return_value=valid_jwt_claims):
            claims = auth_manager._verify_and_decode_token(test_token, verify=False)
            assert claims == valid_jwt_claims


class TestTokenExpiry:
    """Test token expiry checking."""

    def test_check_token_expiry_valid(self, auth_manager, valid_jwt_claims):
        """Test checking expiry of a valid token."""
        test_token = "valid.jwt.token"
        auth_manager._jwks_client = Mock()

        with patch.object(
            auth_manager, "_verify_and_decode_token", return_value=valid_jwt_claims
        ):
            is_valid, remaining = auth_manager._check_token_expiry(test_token)

            assert is_valid is True
            assert remaining > 0
            # Should have buffer of 60 seconds
            assert remaining < (valid_jwt_claims["exp"] - time.time())

    def test_check_token_expiry_expired(self, auth_manager, expired_jwt_claims):
        """Test checking expiry of an expired token."""
        test_token = "expired.jwt.token"
        auth_manager._jwks_client = Mock()

        with patch.object(
            auth_manager, "_verify_and_decode_token", return_value=expired_jwt_claims
        ):
            is_valid, remaining = auth_manager._check_token_expiry(test_token)

            assert is_valid is False
            assert remaining < 0

    def test_check_token_expiry_invalid_token(self, auth_manager):
        """Test checking expiry of an invalid token."""
        test_token = "invalid.jwt.token"

        with patch.object(auth_manager, "_verify_and_decode_token", return_value=None):
            is_valid, remaining = auth_manager._check_token_expiry(test_token)

            assert is_valid is False
            assert remaining == 0


class TestUserInfoExtraction:
    """Test user info extraction from JWT tokens."""

    def test_extract_user_info_success(self, auth_manager, valid_jwt_claims):
        """Test successful user info extraction."""
        test_token = "valid.jwt.token"

        with patch.object(
            auth_manager, "_verify_and_decode_token", return_value=valid_jwt_claims
        ):
            user_info = auth_manager._extract_user_info(test_token)

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
        test_token = "minimal.jwt.token"

        with patch.object(
            auth_manager, "_verify_and_decode_token", return_value=minimal_claims
        ):
            user_info = auth_manager._extract_user_info(test_token)

            assert user_info["username"] == "user-123"  # Falls back to sub
            assert user_info["email"] is None
            assert user_info["name"] is None
            assert user_info["groups"] == []

    def test_extract_user_info_invalid_token(self, auth_manager):
        """Test user info extraction from invalid token."""
        test_token = "invalid.jwt.token"

        with patch.object(auth_manager, "_verify_and_decode_token", return_value=None):
            user_info = auth_manager._extract_user_info(test_token)
            assert user_info == {}


class TestScopeValidation:
    """Test OAuth scope validation."""

    def test_validate_token_scopes_with_all_scopes(self, auth_manager):
        """Test scope validation when all required scopes are present."""
        claims = {
            "scope": " ".join(REQUIRED_SCOPES),
            "exp": int(time.time()) + 3600,
        }
        test_token = "valid.jwt.token"

        with patch.object(
            auth_manager, "_verify_and_decode_token", return_value=claims
        ):
            result = auth_manager._validate_token_scopes(test_token)
            assert result is True

    def test_validate_token_scopes_with_missing_scopes(self, auth_manager):
        """Test scope validation when some scopes are missing."""
        claims = {
            "scope": "openid profile",  # Missing email, offline_access, groups
            "exp": int(time.time()) + 3600,
        }
        test_token = "limited.jwt.token"

        with patch.object(
            auth_manager, "_verify_and_decode_token", return_value=claims
        ):
            # Should return True (advisory only)
            result = auth_manager._validate_token_scopes(test_token)
            assert result is True

    def test_validate_token_scopes_no_scope_claim(self, auth_manager):
        """Test scope validation when token has no scope claim."""
        claims = {
            "sub": "user-123",
            "exp": int(time.time()) + 3600,
        }
        test_token = "no-scope.jwt.token"

        with patch.object(
            auth_manager, "_verify_and_decode_token", return_value=claims
        ):
            result = auth_manager._validate_token_scopes(test_token)
            assert result is True  # Should pass (normal for access tokens)

    def test_validate_token_scopes_as_list(self, auth_manager):
        """Test scope validation when scopes are provided as a list."""
        claims = {
            "scope": list(REQUIRED_SCOPES),
            "exp": int(time.time()) + 3600,
        }
        test_token = "list-scope.jwt.token"

        with patch.object(
            auth_manager, "_verify_and_decode_token", return_value=claims
        ):
            result = auth_manager._validate_token_scopes(test_token)
            assert result is True


class TestKanopyTokenCheck:
    """Test Kanopy token file checking."""

    def test_check_kanopy_token_success(
        self, auth_manager, valid_jwt_claims, mock_jwks_client
    ):
        """Test successful Kanopy token check."""
        auth_manager._jwks_client = mock_jwks_client

        token_data = {
            "access_token": "valid.access.token",
            "refresh_token": "valid.refresh.token",
        }

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data=json.dumps(token_data))),
            patch.object(
                auth_manager, "_check_token_expiry", return_value=(True, 3600)
            ),
            patch.object(auth_manager, "_validate_token_scopes", return_value=True),
            patch.object(
                auth_manager,
                "_extract_user_info",
                return_value={"email": "test@test.com"},
            ),
        ):
            token = auth_manager.check_kanopy_token()

            assert token == "valid.access.token"
            assert auth_manager._access_token == "valid.access.token"
            assert auth_manager._refresh_token == "valid.refresh.token"

    def test_check_kanopy_token_file_not_found(self, auth_manager):
        """Test Kanopy token check when file doesn't exist."""
        with patch("pathlib.Path.exists", return_value=False):
            token = auth_manager.check_kanopy_token()
            assert token is None

    def test_check_kanopy_token_expired(
        self, auth_manager, expired_jwt_claims, mock_jwks_client
    ):
        """Test Kanopy token check with expired token."""
        auth_manager._jwks_client = mock_jwks_client

        token_data = {
            "access_token": "expired.access.token",
            "refresh_token": "valid.refresh.token",
        }

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data=json.dumps(token_data))),
            patch.object(
                auth_manager, "_check_token_expiry", return_value=(False, -3600)
            ),
        ):
            token = auth_manager.check_kanopy_token()
            assert token is None
            assert auth_manager._refresh_token == "valid.refresh.token"

    def test_check_kanopy_token_invalid_json(self, auth_manager):
        """Test Kanopy token check with invalid JSON."""
        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data="invalid json{")),
        ):
            token = auth_manager.check_kanopy_token()
            assert token is None


class TestEvergreenTokenCheck:
    """Test Evergreen config token checking."""

    def test_check_evergreen_token_success(
        self, auth_manager, valid_jwt_claims, mock_jwks_client
    ):
        """Test successful Evergreen token check."""
        auth_manager._jwks_client = mock_jwks_client

        config_data = {
            "oauth": {"token_file_path": "/tmp/evergreen-token.json"},
            "user": "testuser",
        }

        token_data = {
            "access_token": "valid.access.token",
            "refresh_token": "valid.refresh.token",
        }

        # Mock Path.exists to return True for both config and token file
        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data=json.dumps(token_data))),
            patch.object(
                auth_manager, "_check_token_expiry", return_value=(True, 3600)
            ),
            patch.object(auth_manager, "_validate_token_scopes", return_value=True),
            patch.object(
                auth_manager,
                "_extract_user_info",
                return_value={"email": "test@test.com"},
            ),
            patch("yaml.safe_load", return_value=config_data),
        ):
            token = auth_manager.check_evergreen_token()

            assert token == "valid.access.token"
            assert auth_manager._access_token == "valid.access.token"
            assert auth_manager._refresh_token == "valid.refresh.token"

    def test_check_evergreen_token_config_not_found(self, auth_manager):
        """Test Evergreen token check when config file doesn't exist."""
        with patch("pathlib.Path.exists", return_value=False):
            token = auth_manager.check_evergreen_token()
            assert token is None

    def test_check_evergreen_token_no_oauth_section(self, auth_manager):
        """Test Evergreen token check when config has no oauth section."""
        config_data = {"user": "testuser", "api_key": "testkey"}

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data=json.dumps(config_data))),
            patch("yaml.safe_load", return_value=config_data),
        ):
            token = auth_manager.check_evergreen_token()
            assert token is None


class TestTokenRefresh:
    """Test token refresh functionality."""

    @pytest.mark.asyncio
    async def test_refresh_token_success(self, auth_manager, valid_jwt_claims):
        """Test successful token refresh."""
        auth_manager._refresh_token = "valid.refresh.token"
        auth_manager.token_endpoint = "https://dex.example.com/token"
        auth_manager._jwks_client = Mock()

        new_token_data = {
            "access_token": "new.access.token",
            "refresh_token": "new.refresh.token",
        }

        with patch("aiohttp.ClientSession") as mock_session:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value=new_token_data)

            # Create proper context manager mock
            mock_post_context = AsyncMock()
            mock_post_context.__aenter__ = AsyncMock(return_value=mock_response)
            mock_post_context.__aexit__ = AsyncMock(return_value=None)

            mock_session_instance = AsyncMock()
            mock_session_instance.post = Mock(return_value=mock_post_context)
            mock_session_instance.__aenter__.return_value = mock_session_instance
            mock_session_instance.__aexit__.return_value = None
            mock_session.return_value = mock_session_instance

            with (
                patch.object(auth_manager, "_validate_token_scopes", return_value=True),
                patch.object(
                    auth_manager,
                    "_extract_user_info",
                    return_value={"email": "test@test.com"},
                ),
                patch.object(auth_manager, "_save_token"),
            ):
                token = await auth_manager.refresh_token()

                assert token == "new.access.token"
                assert auth_manager._access_token == "new.access.token"
                assert auth_manager._refresh_token == "new.refresh.token"

    @pytest.mark.asyncio
    async def test_refresh_token_no_refresh_token(self, auth_manager):
        """Test token refresh without refresh token."""
        auth_manager._refresh_token = None

        token = await auth_manager.refresh_token()
        assert token is None

    @pytest.mark.asyncio
    async def test_refresh_token_already_refreshed(self, auth_manager):
        """Test that concurrent refresh is prevented."""
        auth_manager._refresh_token = "valid.refresh.token"
        auth_manager._access_token = "current.access.token"
        auth_manager.token_endpoint = "https://dex.example.com/token"

        with patch.object(
            auth_manager, "_check_token_expiry", return_value=(True, 3600)
        ):
            token = await auth_manager.refresh_token()

            # Should return existing token without making network call
            assert token == "current.access.token"

    @pytest.mark.asyncio
    async def test_refresh_token_server_error(self, auth_manager):
        """Test token refresh with server error."""
        auth_manager._refresh_token = "valid.refresh.token"
        auth_manager.token_endpoint = "https://dex.example.com/token"

        with patch("aiohttp.ClientSession") as mock_session:
            mock_response = AsyncMock()
            mock_response.status = 400
            mock_response.text = AsyncMock(return_value="Invalid refresh token")

            mock_context = AsyncMock()
            mock_context.__aenter__.return_value = mock_response
            mock_context.__aexit__.return_value = None

            mock_session_instance = AsyncMock()
            mock_session_instance.post.return_value = mock_context
            mock_session_instance.__aenter__.return_value = mock_session_instance
            mock_session_instance.__aexit__.return_value = None
            mock_session.return_value = mock_session_instance

            token = await auth_manager.refresh_token()
            assert token is None


class TestSaveToken:
    """Test token file saving."""

    def test_save_token_success(self, auth_manager):
        """Test successful token save."""
        token_data = {
            "access_token": "test.access.token",
            "refresh_token": "test.refresh.token",
        }

        with (
            patch("pathlib.Path.mkdir"),
            patch("builtins.open", mock_open()) as m,
            patch("os.fsync"),
            patch("pathlib.Path.replace"),
            patch("pathlib.Path.with_suffix") as mock_suffix,
        ):
            temp_path = Mock(spec=Path)
            temp_path.exists.return_value = False
            mock_suffix.return_value = temp_path

            auth_manager._save_token(token_data)

            # Verify file was written
            m.assert_called()

    def test_save_token_cleanup_on_error(self, auth_manager):
        """Test that temp file is cleaned up on error."""
        token_data = {"access_token": "test.token"}

        with (
            patch("pathlib.Path.mkdir"),
            patch("builtins.open", side_effect=OSError("Write error")),
            patch("pathlib.Path.with_suffix") as mock_suffix,
        ):
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
    async def test_device_flow_auth_success(self, auth_manager):
        """Test successful device flow authentication."""
        auth_manager.device_auth_endpoint = "https://dex.example.com/device"
        auth_manager.token_endpoint = "https://dex.example.com/token"

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

        with (
            patch("aiohttp.ClientSession") as mock_session,
            patch("webbrowser.open"),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            # Mock device code request
            device_mock = AsyncMock()
            device_mock.raise_for_status = Mock()
            device_mock.json = AsyncMock(return_value=device_response)

            # Mock token polling (succeed on first poll for speed)
            token_mock = AsyncMock()
            token_mock.status = 200
            token_mock.json = AsyncMock(return_value=token_response)

            # Create proper context managers
            device_context = AsyncMock()
            device_context.__aenter__ = AsyncMock(return_value=device_mock)
            device_context.__aexit__ = AsyncMock(return_value=None)

            token_context = AsyncMock()
            token_context.__aenter__ = AsyncMock(return_value=token_mock)
            token_context.__aexit__ = AsyncMock(return_value=None)

            mock_session_instance = AsyncMock()
            mock_session_instance.post = Mock(
                side_effect=[device_context, token_context]
            )
            mock_session_instance.__aenter__.return_value = mock_session_instance
            mock_session_instance.__aexit__.return_value = None
            mock_session.return_value = mock_session_instance

            with (
                patch.object(auth_manager, "_validate_token_scopes", return_value=True),
                patch.object(
                    auth_manager,
                    "_extract_user_info",
                    return_value={"email": "test@test.com"},
                ),
                patch.object(auth_manager, "_save_token"),
            ):
                token = await auth_manager.device_flow_auth()

                assert token == "new.access.token"
                assert auth_manager._access_token == "new.access.token"
                assert auth_manager._refresh_token == "new.refresh.token"

    @pytest.mark.asyncio
    async def test_device_flow_auth_pending(self, auth_manager):
        """Test device flow with authorization pending."""
        auth_manager.device_auth_endpoint = "https://dex.example.com/device"
        auth_manager.token_endpoint = "https://dex.example.com/token"

        device_response = {
            "device_code": "device123",
            "user_code": "USER123",
            "verification_uri": "https://dex.example.com/verify",
            "interval": 1,
        }

        with (
            patch("aiohttp.ClientSession") as mock_session,
            patch("webbrowser.open"),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            device_mock = AsyncMock()
            device_mock.raise_for_status = Mock()
            device_mock.json = AsyncMock(return_value=device_response)

            # First poll: pending, second poll: success
            pending_mock = AsyncMock()
            pending_mock.status = 400
            pending_mock.json = AsyncMock(
                return_value={"error": "authorization_pending"}
            )

            success_mock = AsyncMock()
            success_mock.status = 200
            success_mock.json = AsyncMock(
                return_value={
                    "access_token": "new.token",
                    "refresh_token": "refresh.token",
                }
            )

            device_context = AsyncMock()
            device_context.__aenter__ = AsyncMock(return_value=device_mock)
            device_context.__aexit__ = AsyncMock(return_value=None)

            pending_context = AsyncMock()
            pending_context.__aenter__ = AsyncMock(return_value=pending_mock)
            pending_context.__aexit__ = AsyncMock(return_value=None)

            success_context = AsyncMock()
            success_context.__aenter__ = AsyncMock(return_value=success_mock)
            success_context.__aexit__ = AsyncMock(return_value=None)

            mock_session_instance = AsyncMock()
            mock_session_instance.post = Mock(
                side_effect=[
                    device_context,
                    pending_context,
                    success_context,
                ]
            )
            mock_session_instance.__aenter__.return_value = mock_session_instance
            mock_session_instance.__aexit__.return_value = None
            mock_session.return_value = mock_session_instance

            with (
                patch.object(auth_manager, "_validate_token_scopes", return_value=True),
                patch.object(auth_manager, "_extract_user_info", return_value={}),
                patch.object(auth_manager, "_save_token"),
            ):
                token = await auth_manager.device_flow_auth()
                assert token == "new.token"

    @pytest.mark.asyncio
    async def test_device_flow_auth_expired(self, auth_manager):
        """Test device flow with expired device code."""
        auth_manager.device_auth_endpoint = "https://dex.example.com/device"
        auth_manager.token_endpoint = "https://dex.example.com/token"

        device_response = {
            "device_code": "device123",
            "user_code": "USER123",
            "verification_uri": "https://dex.example.com/verify",
            "interval": 1,
        }

        with (
            patch("aiohttp.ClientSession") as mock_session,
            patch("webbrowser.open"),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            device_mock = AsyncMock()
            device_mock.raise_for_status = Mock()
            device_mock.json = AsyncMock(return_value=device_response)

            expired_mock = AsyncMock()
            expired_mock.status = 400
            expired_mock.json = AsyncMock(return_value={"error": "expired_token"})

            mock_context_device = AsyncMock()
            mock_context_device.__aenter__.return_value = device_mock
            mock_context_device.__aexit__.return_value = None

            mock_context_expired = AsyncMock()
            mock_context_expired.__aenter__.return_value = expired_mock
            mock_context_expired.__aexit__.return_value = None

            mock_session_instance = AsyncMock()
            mock_session_instance.post.side_effect = [
                mock_context_device,
                mock_context_expired,
            ]
            mock_session_instance.__aenter__.return_value = mock_session_instance
            mock_session_instance.__aexit__.return_value = None
            mock_session.return_value = mock_session_instance

            token = await auth_manager.device_flow_auth()
            assert token is None


class TestEnsureAuthenticated:
    """Test the main authentication flow."""

    @pytest.mark.asyncio
    async def test_ensure_authenticated_with_kanopy_token(
        self, auth_manager, mock_jwks_client
    ):
        """Test authentication using Kanopy token."""
        auth_manager._jwks_client = None

        with (
            patch.object(
                auth_manager, "initialize_endpoints", new_callable=AsyncMock
            ) as mock_init,
            patch.object(
                auth_manager, "check_kanopy_token", return_value="valid.token"
            ),
        ):
            result = await auth_manager.ensure_authenticated()

            assert result is True
            mock_init.assert_called_once()

    @pytest.mark.asyncio
    async def test_ensure_authenticated_with_refresh(self, auth_manager):
        """Test authentication using token refresh."""
        auth_manager._jwks_client = Mock()
        auth_manager._refresh_token = "valid.refresh.token"

        with (
            patch.object(auth_manager, "check_kanopy_token", return_value=None),
            patch.object(auth_manager, "check_evergreen_token", return_value=None),
            patch.object(
                auth_manager, "refresh_token", new_callable=AsyncMock
            ) as mock_refresh,
        ):
            mock_refresh.return_value = "new.token"

            result = await auth_manager.ensure_authenticated()

            assert result is True
            mock_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_ensure_authenticated_with_device_flow(self, auth_manager):
        """Test authentication using device flow."""
        auth_manager._jwks_client = None

        with (
            patch.object(auth_manager, "initialize_endpoints", new_callable=AsyncMock),
            patch.object(auth_manager, "check_kanopy_token", return_value=None),
            patch.object(auth_manager, "check_evergreen_token", return_value=None),
            patch.object(
                auth_manager, "device_flow_auth", new_callable=AsyncMock
            ) as mock_device,
        ):
            mock_device.return_value = "new.token"

            result = await auth_manager.ensure_authenticated()

            assert result is True
            mock_device.assert_called_once()

    @pytest.mark.asyncio
    async def test_ensure_authenticated_already_authenticated(self, auth_manager):
        """Test that concurrent requests don't re-authenticate."""
        auth_manager._jwks_client = Mock()
        auth_manager._access_token = "existing.token"

        with (
            patch.object(
                auth_manager, "_check_token_expiry", return_value=(True, 3600)
            ),
            patch.object(auth_manager, "check_kanopy_token") as mock_kanopy,
        ):
            result = await auth_manager.ensure_authenticated()

            assert result is True
            # Should not check Kanopy if already authenticated
            mock_kanopy.assert_not_called()

    @pytest.mark.asyncio
    async def test_ensure_authenticated_all_methods_fail(self, auth_manager):
        """Test authentication when all methods fail."""
        auth_manager._jwks_client = None

        with (
            patch.object(auth_manager, "initialize_endpoints", new_callable=AsyncMock),
            patch.object(auth_manager, "check_kanopy_token", return_value=None),
            patch.object(auth_manager, "check_evergreen_token", return_value=None),
            patch.object(
                auth_manager, "device_flow_auth", new_callable=AsyncMock
            ) as mock_device,
        ):
            mock_device.return_value = None

            result = await auth_manager.ensure_authenticated()

            assert result is False


class TestIsAuthenticated:
    """Test non-blocking authentication status check."""

    def test_is_authenticated_true(self, auth_manager):
        """Test is_authenticated returns True for valid token."""
        auth_manager._access_token = "valid.token"

        with patch.object(
            auth_manager, "_check_token_expiry", return_value=(True, 3600)
        ):
            assert auth_manager.is_authenticated() is True

    def test_is_authenticated_false_no_token(self, auth_manager):
        """Test is_authenticated returns False when no token."""
        auth_manager._access_token = None
        assert auth_manager.is_authenticated() is False

    def test_is_authenticated_false_expired(self, auth_manager):
        """Test is_authenticated returns False for expired token."""
        auth_manager._access_token = "expired.token"

        with patch.object(
            auth_manager, "_check_token_expiry", return_value=(False, -3600)
        ):
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

    def test_user_id_property_no_email(self, auth_manager):
        """Test user_id property with no email."""
        auth_manager._user_info = {}

        assert auth_manager.user_id is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
