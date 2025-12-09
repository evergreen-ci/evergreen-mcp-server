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
            config = yaml.safe_load(f)
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

    Thread Safety:
    - Uses asyncio locks to prevent race conditions during token refresh
    - Ensures only one refresh/authentication happens at a time
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
        self._refresh_lock: asyncio.Lock = asyncio.Lock()
        self._auth_lock: asyncio.Lock = asyncio.Lock()

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
        Check if token is expired.

        Args:
            token_data: Token data dict with 'access_token' and optionally 'expires_at'

        Returns:
            Tuple of (is_valid, seconds_remaining)
        """
        expires_at = token_data.get("expires_at", 0)
        if expires_at:
            remaining = expires_at - time.time()
            return remaining > 60, int(remaining)  # 1 min buffer

        # If no expiry info, try to decode the JWT token
        access_token = token_data.get("access_token")
        if not access_token:
            return False, 0

        try:
            # Use PyJWT to decode without signature verification
            claims = pyjwt.decode(
                access_token,
                options={"verify_signature": False, "verify_exp": False},
            )
            exp = claims.get("exp", 0)
            if exp:
                remaining = exp - time.time()
                return remaining > 60, int(remaining)
        except Exception:
            pass

        # If we can't decode, assume it's valid and let the API reject it
        logger.warning("Could not determine token expiry, assuming valid")
        return True, 3600

    def _extract_user_id(self, access_token: str) -> str:
        """Extract user identifier from JWT token for logging.

        Raises:
            Exception: If token cannot be decoded
        """
        try:
            claims = pyjwt.decode(
                access_token,
                options={"verify_signature": False, "verify_exp": False},
            )
            email = claims.get("email")
            if email and "@" in email:
                return email.split("@")[0]
            return claims.get("preferred_username") or claims.get("sub") or "unknown"
        except Exception:
            logger.error("Failed to extract user ID from token")
            raise

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

        async with self._refresh_lock:
            if self._access_token:
                token_data = {
                    "access_token": self._access_token,
                    "refresh_token": self._refresh_token,
                }
                is_valid, remaining = self._check_token_expiry(token_data)
                if is_valid:
                    logger.debug(
                        "Token already refreshed by another request (%d min remaining)",
                        remaining // 60,
                    )
                    return token_data

            logger.info("Attempting token refresh...")
            try:
                await self._get_client()

                # Refresh the token manually using httpx

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
                        token_data = response.json()

                        # Update internal state
                        self._access_token = token_data["access_token"]
                        self._refresh_token = token_data.get(
                            "refresh_token", self._refresh_token
                        )
                        self._user_id = self._extract_user_id(self._access_token)

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

    async def device_flow_auth(self) -> Optional[dict]:
        """Perform device authorization flow manually using httpx."""
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

                # Step 2: Poll for token
                token_endpoint = self._metadata["token_endpoint"]

                while True:
                    await asyncio.sleep(interval)

                    try:
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
                            token_data = response.json()

                            # Update internal state
                            self._access_token = token_data["access_token"]
                            self._refresh_token = token_data.get("refresh_token")
                            self._user_id = self._extract_user_id(self._access_token)

                            # Save token to file
                            self._save_token(token_data)
                            logger.info("Authentication successful!")
                            return token_data
                        else:
                            # Parse error response - handle both 400 and 401
                            # Some OAuth servers return 401 for authorization_pending
                            try:
                                error_data = response.json()
                                error = error_data.get("error", "unknown_error")
                                error_description = error_data.get(
                                    "error_description", ""
                                )
                            except Exception:
                                # Response might not be JSON
                                logger.warning(
                                    "Token poll returned %d: %s",
                                    response.status_code,
                                    response.text[:200] if response.text else "empty",
                                )
                                error = "unknown_error"
                                error_description = response.text

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
                                logger.error("Authentication request expired")
                                return None
                            elif response.status_code == 401:
                                # 401 during polling often means still waiting
                                # for user to complete authentication
                                logger.debug(
                                    "Got 401, treating as authorization pending..."
                                )
                                continue
                            else:
                                logger.error(
                                    "Authentication failed: status=%d, error=%s, desc=%s",
                                    response.status_code,
                                    error,
                                    error_description,
                                )
                                return None

                    except Exception as e:
                        logger.error("Token polling error: %s", e)
                        return None

        except Exception as e:
            logger.error("Device flow authentication error: %s", e)
            return None

    async def ensure_authenticated(self) -> bool:
        """
        Main authentication flow with concurrency protection.

        Steps:
        1. Initialize OAuth2 client
        2. Check kanopy token
        3. Check evergreen token
        4. Try refresh if expired
        5. Do device flow if needed
        """
        async with self._auth_lock:
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

            # Check configured token file (from oauth.token_file_path or default kanopy location)
            logger.info("Checking for existing token...")
            token_data = self.check_token_file()
            if token_data:
                self._access_token = token_data["access_token"]
                self._refresh_token = token_data.get("refresh_token")
                self._user_id = self._extract_user_id(self._access_token)
                return True

            # Try refresh if we have a refresh token
            if self._refresh_token:
                logger.info("Attempting token refresh...")
                token_data = await self.refresh_token()
                if token_data:
                    self._access_token = token_data["access_token"]
                    self._refresh_token = token_data.get(
                        "refresh_token", self._refresh_token
                    )
                    self._user_id = self._extract_user_id(self._access_token)
                    return True

            # Need to authenticate
            logger.warning("No valid token found - authentication required")
            token_data = await self.device_flow_auth()
            if token_data:
                self._access_token = token_data["access_token"]
                self._refresh_token = token_data.get("refresh_token")
                self._user_id = self._extract_user_id(self._access_token)
                return True

            return False

    def set_token_from_data(self, token_data: dict) -> None:
        """Set internal token state from token data dict.

        This is the preferred way to update the auth manager's token state
        from external code, rather than accessing private attributes directly.

        Args:
            token_data: Dict containing 'access_token' and optionally 'refresh_token'
        """
        self._access_token = token_data["access_token"]
        self._refresh_token = token_data.get("refresh_token")
        self._user_id = self._extract_user_id(self._access_token)

    def is_authenticated(self) -> bool:
        """Check if currently authenticated with a valid token."""
        if not self._access_token:
            return False

        token_data = {
            "access_token": self._access_token,
            "refresh_token": self._refresh_token,
        }
        is_valid, _ = self._check_token_expiry(token_data)
        return is_valid

    @property
    def access_token(self) -> Optional[str]:
        """Get current access token."""
        return self._access_token

    @property
    def user_id(self) -> Optional[str]:
        """Get user ID (username) for logging."""
        return self._user_id
