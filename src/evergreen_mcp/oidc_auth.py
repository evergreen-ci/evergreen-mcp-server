"""OIDC/OAuth Device Flow Authentication for Evergreen

This module manages DEX authentication using authlib with:
- Token file path configured in ~/.evergreen.yml (oauth.token_file_path)
- Device authorization flow for new authentication

"""

import asyncio
import json
import logging
import os
import time
import webbrowser
from pathlib import Path
from typing import Optional

import httpx
import jwt as pyjwt
import yaml
from authlib.integrations.httpx_client import AsyncOAuth2Client

logger = logging.getLogger(__name__)


class OIDCAuthenticationError(Exception):
    """Raised when OIDC authentication fails.

    This exception is used to signal authentication failures that should
    be handled by the calling code, such as failed device flow authentication
    or token refresh failures.
    """

    pass


# Evergreen config file location
EVERGREEN_CONFIG_FILE = Path.home() / ".evergreen.yml"

# HTTP timeout configurations (in seconds)
HTTP_TIMEOUT = 30


def _load_oauth_config_from_evergreen_yml() -> dict:
    """Load OAuth configuration from ~/.evergreen.yml.

    Raises:
        OIDCAuthenticationError: If config file is missing or malformed
    """
    if not EVERGREEN_CONFIG_FILE.exists():
        raise OIDCAuthenticationError(
            f"Evergreen config file not found: {EVERGREEN_CONFIG_FILE}\n"
            "Please create ~/.evergreen.yml with oauth configuration."
        )

    try:
        with open(EVERGREEN_CONFIG_FILE) as f:
            config = yaml.safe_load(f) or {}
    except Exception as e:
        raise OIDCAuthenticationError(
            f"Failed to parse {EVERGREEN_CONFIG_FILE}: {e}"
        ) from e

    oauth_config = config.get("oauth")
    if not oauth_config:
        raise OIDCAuthenticationError(
            f"Missing 'oauth' section in {EVERGREEN_CONFIG_FILE}\n"
            "Required fields: issuer, client_id"
        )

    # Validate required fields
    required = ["issuer", "client_id"]
    missing = [f for f in required if not oauth_config.get(f)]
    if missing:
        raise OIDCAuthenticationError(
            f"Missing required oauth fields in {EVERGREEN_CONFIG_FILE}: {missing}"
        )

    return oauth_config


class OIDCAuthManager:
    """
    Manages DEX authentication using authlib.

    This class handles OIDC/OAuth authentication with device flow
    and supports multiple token sources (Kanopy, Evergreen config).
    """

    def __init__(self):
        # Load OAuth config from ~/.evergreen.yml
        oauth_config = _load_oauth_config_from_evergreen_yml()

        # All config must come from evergreen.yml
        self.issuer = oauth_config.get("issuer")
        self.client_id = oauth_config.get("client_id")

        # Token file path: environment variable overrides config
        # This is useful for Docker where the config has host paths
        # but the container has different mount points
        token_file_path = os.getenv("EVERGREEN_TOKEN_FILE") or oauth_config.get(
            "token_file_path"
        )
        self.token_file = Path(token_file_path) if token_file_path else None

        logger.debug(
            "Initialized OIDC auth manager: issuer=%s, client_id=%s, token_file=%s",
            self.issuer,
            self.client_id,
            self.token_file,
        )

        self._client: Optional[AsyncOAuth2Client] = None
        self._metadata: Optional[dict] = None
        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._user_id: Optional[str] = None

    async def _get_client(self) -> AsyncOAuth2Client:
        """Get or create the OAuth2 client with OIDC metadata."""
        if self._client is None:
            logger.info("Initializing OAuth2 client for %s", self.issuer)

            # Fetch OIDC metadata manually
            try:
                async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as http_client:
                    response = await http_client.get(
                        f"{self.issuer}/.well-known/openid-configuration"
                    )
                    response.raise_for_status()
                    self._metadata = response.json()
                logger.info("Fetched OIDC metadata successfully")
            except Exception as e:
                logger.error("Failed to fetch OIDC metadata: %s", e)
                raise

            # Create client with metadata
            self._client = AsyncOAuth2Client(
                client_id=self.client_id,
                token_endpoint=self._metadata["token_endpoint"],
                timeout=HTTP_TIMEOUT,
            )

        return self._client

    def _check_token_expiry(self, token_data: dict) -> tuple[bool, int]:
        """
        Check if token is expired by decoding the JWT.

        Args:
            token_data: Token data dict with 'access_token'

        Returns:
            Tuple of (is_valid, seconds_remaining)
        """
        access_token = token_data.get("access_token")
        if not access_token:
            return False, 0

        try:
            claims = pyjwt.decode(
                access_token,
                options={"verify_signature": False, "verify_exp": False},
            )
            exp = claims.get("exp", 0)
            if exp:
                remaining = exp - time.time()
                return remaining > 60, int(remaining)  # 1 min buffer
            return False, 0
        except Exception as e:
            # Malformed/tampered token - treat as invalid for security
            logger.warning("Could not decode token to check expiry: %s", e)
            return False, 0

    def _extract_user_id(self, access_token: str) -> str:
        """Extract user identifier from JWT token.

        Note: Signature verification is disabled because this is only used for
        extracting the username for display/query purposes. Actual authentication
        is validated by the OIDC provider during token exchange.

        Returns:
            User ID string extracted from token claims.

        Raises:
            OIDCAuthenticationError: If token cannot be decoded (malformed/corrupted).
        """
        try:
            claims = pyjwt.decode(
                access_token,
                options={"verify_signature": False, "verify_exp": False},
            )
            email = claims.get("email")
            if email and "@" in email:
                return email.split("@")[0]
            user_id = claims.get("preferred_username") or claims.get("sub")
            if not user_id:
                raise OIDCAuthenticationError(
                    "Token is missing required identity claims (email, preferred_username, sub). "
                    "Please re-authenticate by removing your token file and restarting."
                )
            return user_id
        except OIDCAuthenticationError:
            raise  # Re-raise our own exceptions
        except Exception as e:
            raise OIDCAuthenticationError(
                f"Token is malformed and cannot be decoded: {e}. "
                "Please re-authenticate by removing your token file and restarting."
            ) from e

    def _normalize_token_data(self, token_data: dict) -> dict:
        """Normalize token data by computing expires_at from expires_in if needed.

        OAuth servers typically return expires_in (seconds until expiry) rather than
        expires_at (absolute timestamp). This method ensures expires_at is always set
        for token file persistence, allowing other tools that read the token file to
        check expiry without decoding the JWT.

        Note: _check_token_expiry() decodes the JWT directly and doesn't use expires_at,
        but this normalization is kept for compatibility with external token consumers.
        """
        if "expires_in" in token_data and "expires_at" not in token_data:
            token_data["expires_at"] = time.time() + token_data["expires_in"]
        return token_data

    def check_token_file(self) -> Optional[dict]:
        """Check configured token file for valid token.

        The token file path must be configured in ~/.evergreen.yml under
        oauth.token_file_path.

        If the access token is expired but a refresh token exists, this method
        will store the refresh token internally so it can be used for refresh.

        Returns:
            Token data dict if valid token found, None otherwise
        """
        if not self.token_file:
            logger.debug("No token file path configured in ~/.evergreen.yml")
            return None

        if not self.token_file.exists():
            logger.debug("Token file not found: %s", self.token_file)
            return None

        logger.info("Found token file: %s", self.token_file)
        try:
            with open(self.token_file) as f:
                token_data = json.load(f)

            if "access_token" in token_data:
                is_valid, remaining = self._check_token_expiry(token_data)
                if is_valid:
                    logger.info("Token valid (%d min remaining)", remaining // 60)
                    return token_data
                else:
                    # Token expired - but store the refresh token so we can try to refresh
                    if token_data.get("refresh_token"):
                        logger.info("Access token expired, but refresh token available")
                        self._refresh_token = token_data["refresh_token"]
                    else:
                        logger.warning("Token expired and no refresh token available")
        except Exception as e:
            logger.error("Error reading token file: %s", e)

        return None

    async def refresh_token(self) -> Optional[dict]:
        """
        Attempt to refresh the token using authlib.

        Returns:
            Token data dict if successful, None otherwise
        """
        if not self._refresh_token:
            logger.warning("No refresh token available")
            return None

        logger.info("Attempting token refresh...")
        try:
            await self._get_client()

            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as http_client:
                response = await http_client.post(
                    self._metadata["token_endpoint"],
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": self._refresh_token,
                        "client_id": self.client_id,
                    },
                )

                if response.status_code == 200:
                    token_data = self._normalize_token_data(response.json())

                    # Validate token BEFORE updating state to ensure atomic updates
                    new_access_token = token_data["access_token"]
                    new_refresh_token = token_data.get(
                        "refresh_token", self._refresh_token
                    )
                    new_user_id = self._extract_user_id(new_access_token)

                    # Token is valid - now update internal state
                    self._access_token = new_access_token
                    self._refresh_token = new_refresh_token
                    self._user_id = new_user_id

                    # Save the new token
                    self._save_token(token_data)
                    logger.info("Token refreshed successfully!")
                    return token_data
                else:
                    logger.error(
                        "Token refresh failed with status %d: %s",
                        response.status_code,
                        response.text,
                    )
                    return None

        except Exception as e:
            logger.error("Token refresh failed: %s", e)
            return None

    def _save_token(self, token_data: dict):
        """Save token to configured token file atomically.

        The token file path must be configured in ~/.evergreen.yml under
        oauth.token_file_path. If not configured, tokens will not be persisted.
        """
        if not self.token_file:
            logger.warning(
                "No token file path configured - token will not be persisted. "
                "Set oauth.token_file_path in ~/.evergreen.yml to enable token caching."
            )
            return

        # Create parent directory if needed
        try:
            self.token_file.parent.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError) as e:
            logger.error(
                "Cannot create token directory %s: %s", self.token_file.parent, e
            )
            return

        # Write to temporary file first
        temp_file = self.token_file.with_suffix(".tmp")
        try:
            with open(temp_file, "w") as f:
                json.dump(token_data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())

            # Atomic rename
            temp_file.replace(self.token_file)
            logger.info("Token saved to %s", self.token_file)
        except Exception as e:
            logger.error("Failed to save token: %s", e)
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except OSError:
                    pass

    async def device_flow_auth(self) -> dict:
        """Perform device authorization flow manually using httpx.

        Returns:
            Token data dict containing access_token and refresh_token

        Raises:
            OIDCAuthenticationError: If authentication fails or times out
        """
        try:
            await self._get_client()

            logger.info("Starting Device Authorization Flow...")

            # Step 1: Request device code

            device_auth_endpoint = self._metadata["device_authorization_endpoint"]

            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as http_client:
                response = await http_client.post(
                    device_auth_endpoint,
                    data={
                        "client_id": self.client_id,
                        "scope": "openid profile email groups offline_access",
                    },
                )
                response.raise_for_status()
                device_data = response.json()

                # Parse device authorization response
                verification_uri = device_data.get(
                    "verification_uri_complete"
                ) or device_data.get("verification_uri")
                user_code = device_data.get("user_code")
                device_code = device_data["device_code"]
                interval = device_data.get("interval", 5)

                # Display auth instructions
                logger.info("=" * 70)
                logger.info(
                    "🔐 AUTHENTICATION REQUIRED - Please complete login in your browser"
                )
                logger.info("=" * 70)
                logger.info("URL: %s", verification_uri)
                if user_code:
                    logger.info("Code: %s", user_code)
                logger.info("=" * 70)

                # Try to open browser
                try:
                    webbrowser.open(verification_uri)
                    logger.info("Browser opened automatically")
                except Exception:
                    logger.info("Please open the URL manually")

                logger.info("Waiting for authentication...")

                # Step 2: Poll for token with maximum timeout
                token_endpoint = self._metadata["token_endpoint"]
                max_wait_time = 300  # 5 minutes
                start_time = time.time()

                while True:
                    if time.time() - start_time > max_wait_time:
                        raise OIDCAuthenticationError(
                            f"Device flow timed out after {max_wait_time} seconds"
                        )

                    await asyncio.sleep(interval)

                    # Poll token endpoint with device code
                    response = await http_client.post(
                        token_endpoint,
                        data={
                            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                            "device_code": device_code,
                            "client_id": self.client_id,
                        },
                    )

                    # Check response
                    if response.status_code == 200:
                        token_data = self._normalize_token_data(response.json())

                        # Validate token BEFORE updating state to ensure atomic updates
                        new_access_token = token_data["access_token"]
                        new_refresh_token = token_data.get("refresh_token")
                        new_user_id = self._extract_user_id(new_access_token)

                        # Token is valid - now update internal state atomically
                        self._access_token = new_access_token
                        self._refresh_token = new_refresh_token
                        self._user_id = new_user_id

                        # Save token to file
                        self._save_token(token_data)
                        logger.info("Authentication successful!")
                        return token_data

                    # Parse error response - handle both 400 and 401
                    # Some OAuth servers return 401 for authorization_pending
                    try:
                        error_data = response.json()
                        error = error_data.get("error", "unknown_error")
                        error_description = error_data.get("error_description", "")
                    except Exception:
                        # Response might not be JSON
                        logger.warning(
                            "Token poll returned %d: %s",
                            response.status_code,
                            response.text[:200] if response.text else "empty",
                        )
                        error = "unknown_error"
                        error_description = response.text or ""

                    logger.debug(
                        "Token poll response: status=%d, error=%s, desc=%s",
                        response.status_code,
                        error,
                        error_description,
                    )

                    if error == "authorization_pending":
                        logger.debug("Authorization pending, polling...")
                        continue
                    elif error == "slow_down":
                        interval += 2
                        logger.debug(
                            "Slowing down polling interval to %d seconds",
                            interval,
                        )
                        continue
                    elif error == "expired_token":
                        raise OIDCAuthenticationError(
                            "Device code expired - please restart authentication"
                        )
                    elif response.status_code == 401:
                        # 401 during polling often means still waiting
                        # for user to complete authentication
                        logger.debug("Got 401, treating as authorization pending...")
                        continue
                    else:
                        raise OIDCAuthenticationError(
                            f"Authentication failed: {error} - {error_description}"
                        )

        except OIDCAuthenticationError:
            raise
        except Exception as e:
            raise OIDCAuthenticationError(
                f"Device flow authentication error: {e}"
            ) from e

    async def ensure_authenticated(self) -> bool:
        """
        Main authentication flow.

        Steps:
        1. Check if already authenticated
        2. Check token file
        3. Try refresh if expired
        4. Do device flow if needed
        """
        logger.info("Checking authentication status...")

        # Check if already authenticated
        if self._access_token:
            token_data = {
                "access_token": self._access_token,
                "refresh_token": self._refresh_token,
            }
            is_valid, remaining = self._check_token_expiry(token_data)
            if is_valid:
                logger.debug(
                    "Already authenticated (%d min remaining)",
                    remaining // 60,
                )
                return True

        # Initialize client
        await self._get_client()

        # Check configured token file
        logger.info("Checking for existing token...")
        token_data = self.check_token_file()
        if token_data:
            self._access_token = token_data["access_token"]
            self._refresh_token = token_data.get("refresh_token")
            try:
                self._user_id = self._extract_user_id(self._access_token)
                return True
            except OIDCAuthenticationError as e:
                # Token file is corrupted - try refresh or device flow
                logger.warning("Token file is malformed: %s. Trying refresh...", e)
                self._access_token = None

        # Try refresh if we have a refresh token
        if self._refresh_token:
            logger.info("Attempting token refresh...")
            token_data = await self.refresh_token()
            if token_data:
                self._access_token = token_data["access_token"]
                self._refresh_token = token_data.get(
                    "refresh_token", self._refresh_token
                )
                try:
                    self._user_id = self._extract_user_id(self._access_token)
                    return True
                except OIDCAuthenticationError as e:
                    # Refresh returned malformed token - try device flow
                    logger.warning(
                        "Refreshed token is malformed: %s. Trying device flow...", e
                    )
                    self._access_token = None

        # Need to authenticate
        logger.warning("No valid token found - authentication required")
        token_data = await self.device_flow_auth()
        if token_data:
            self._access_token = token_data["access_token"]
            self._refresh_token = token_data.get("refresh_token")
            # If device flow returns malformed token, something is seriously wrong
            # with the OIDC provider - let the exception propagate
            self._user_id = self._extract_user_id(self._access_token)
            return True

        return False

    @property
    def access_token(self) -> Optional[str]:
        """Get current access token."""
        return self._access_token

    @property
    def has_refresh_token(self) -> bool:
        """Check if a refresh token is available."""
        return self._refresh_token is not None

    @property
    def user_id(self) -> Optional[str]:
        """Get user ID (username) for logging."""
        return self._user_id
