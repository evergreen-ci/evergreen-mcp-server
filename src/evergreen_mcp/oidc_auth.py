"""OIDC/OAuth Device Flow Authentication for Evergreen

This module manages DEX authentication using authlib with multiple token sources:
- ~/.kanopy/token-oidclogin.json (Kanopy token file)
- ~/.evergreen.yml oauth configuration (Evergreen token file)
- Device authorization flow for new authentication
"""

import asyncio
import base64
import json
import logging
import os
import time
import webbrowser
from pathlib import Path
from typing import Optional

import httpx
import yaml
from authlib.integrations.httpx_client import AsyncOAuth2Client
from authlib.jose import jwt
from authlib.jose.errors import DecodeError

logger = logging.getLogger(__name__)


# Default token file locations
DEFAULT_KANOPY_TOKEN_FILE = Path.home() / ".kanopy" / "token-oidclogin.json"
EVERGREEN_CONFIG_FILE = Path.home() / ".evergreen.yml"

# HTTP timeout configurations (in seconds)
HTTP_TIMEOUT = 30


def _load_oauth_config_from_evergreen_yml() -> dict:
    """Load OAuth configuration from ~/.evergreen.yml if available."""
    if not EVERGREEN_CONFIG_FILE.exists():
        return {}

    try:
        with open(EVERGREEN_CONFIG_FILE) as f:
            config = yaml.safe_load(f)
        return config.get("oauth", {})
    except Exception as e:
        logger.debug("Could not load oauth config from ~/.evergreen.yml: %s", e)
        return {}


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
        # Load OAuth config from ~/.evergreen.yml if available
        oauth_config = _load_oauth_config_from_evergreen_yml()

        # Use config from evergreen.yml or fall back to defaults
        self.issuer = oauth_config.get("issuer")
        self.client_id = oauth_config.get("client_id")

        # Token file: prefer oauth.token_file_path, fallback to default kanopy location
        token_file_path = oauth_config.get("token_file_path")
        self.kanopy_token_file = (
            Path(token_file_path) if token_file_path else DEFAULT_KANOPY_TOKEN_FILE
        )

        logger.debug(
            "Initialized OIDC auth manager: issuer=%s, client_id=%s, token_file=%s",
            self.issuer,
            self.client_id,
            self.kanopy_token_file,
        )

        self._client: Optional[AsyncOAuth2Client] = None
        self._metadata: Optional[dict] = None
        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._user_info: dict = {}
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
            # Decode JWT payload without verification
            parts = access_token.split(".")
            if len(parts) == 3:
                payload = parts[1]
                padding = 4 - len(payload) % 4
                if padding != 4:
                    payload += "=" * padding
                claims_json = base64.urlsafe_b64decode(payload)
                claims = json.loads(claims_json)
                exp = claims.get("exp", 0)
                remaining = exp - time.time()
                return remaining > 60, int(remaining)
        except Exception:
            pass

        # If we can't decode, assume it's valid and let the API reject it
        logger.warning("Could not determine token expiry, assuming valid")
        return True, 3600

    def _extract_user_info(self, access_token: str) -> dict:
        """Extract user info from JWT token."""
        try:

            # Decode without verification (we trust the token from DEX)
            try:
                claims = jwt.decode(access_token, key=None)
                claims.validate()
            except (DecodeError, Exception):
                # If that fails, try extracting claims without validation
                # JWT format: header.payload.signature
                parts = access_token.split(".")
                if len(parts) != 3:
                    logger.error("Invalid JWT format")
                    return {}

                # Decode payload (add padding if needed)
                payload = parts[1]
                padding = 4 - len(payload) % 4
                if padding != 4:
                    payload += "=" * padding

                claims_json = base64.urlsafe_b64decode(payload)
                claims = json.loads(claims_json)

            logger.debug("Extracted claims from token: %s", claims)

            return {
                "username": claims.get("preferred_username")
                or claims.get("email")
                or claims.get("sub"),
                "email": claims.get("email"),
                "name": claims.get("name"),
                "groups": claims.get("groups", []),
                "exp": claims.get("exp"),
            }
        except Exception as e:
            logger.error("Error extracting user info: %s", e, exc_info=True)
            return {}

    def check_kanopy_token(self) -> Optional[dict]:
        """Check kanopy/evergreen token file for valid token."""
        # Try configured path first
        token_file_to_check = self.kanopy_token_file

        # If configured path doesn't exist, try default location
        # (useful in Docker where evergreen.yml might have host paths)
        if not token_file_to_check.exists():
            logger.debug(
                "Token file not found at configured path: %s", token_file_to_check
            )
            default_path = DEFAULT_KANOPY_TOKEN_FILE
            if default_path != token_file_to_check and default_path.exists():
                logger.info("Using default token file location: %s", default_path)
                token_file_to_check = default_path
            else:
                logger.debug(
                    "Token file not found at default path either: %s", default_path
                )
                return None

        logger.info("Found token file: %s", token_file_to_check)
        try:
            with open(token_file_to_check) as f:
                token_data = json.load(f)

            if "access_token" in token_data:
                is_valid, remaining = self._check_token_expiry(token_data)
                if is_valid:
                    logger.info(
                        "Kanopy token valid (%d min remaining)", remaining // 60
                    )
                    return token_data
                else:
                    logger.warning("Kanopy token expired")
        except Exception as e:
            logger.error("Error reading kanopy token: %s", e)

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
            # Check if token was already refreshed by another request
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
                        self._user_info = self._extract_user_info(self._access_token)

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
        """Save token to configured token file atomically."""
        # Determine which path to use for saving
        save_path = self.kanopy_token_file

        # If configured path isn't writable (e.g., in Docker with host paths),
        # fall back to default container location
        try:
            save_path.parent.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError) as e:
            logger.warning(
                "Cannot write to configured path %s: %s. Using default location.",
                save_path,
                e,
            )
            save_path = DEFAULT_KANOPY_TOKEN_FILE
            save_path.parent.mkdir(parents=True, exist_ok=True)

        # Write to temporary file first
        temp_file = save_path.with_suffix(".tmp")
        try:
            with open(temp_file, "w") as f:
                json.dump(token_data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())

            # Atomic rename
            temp_file.replace(save_path)
            logger.info("Token saved to %s", save_path)
        except Exception as e:
            logger.error("Failed to save token: %s", e)
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except OSError:
                    pass
            raise

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
                            self._user_info = self._extract_user_info(
                                self._access_token
                            )

                            # Save token to file
                            self._save_token(token_data)
                            logger.info("Authentication successful!")
                            return token_data
                        else:
                            # Parse error response
                            error_data = response.json()
                            error = error_data.get("error", "unknown_error")

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
                            else:
                                logger.error("Authentication failed: %s", error_data)
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
            token_data = self.check_kanopy_token()
            if token_data:
                self._access_token = token_data["access_token"]
                self._refresh_token = token_data.get("refresh_token")
                self._user_info = self._extract_user_info(self._access_token)
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
                    self._user_info = self._extract_user_info(self._access_token)
                    return True

            # Need to authenticate
            logger.warning("No valid token found - authentication required")
            token_data = await self.device_flow_auth()
            if token_data:
                self._access_token = token_data["access_token"]
                self._refresh_token = token_data.get("refresh_token")
                self._user_info = self._extract_user_info(self._access_token)
                return True

            return False

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
    def user_info(self) -> dict:
        """Get current user information."""
        return self._user_info

    @property
    def access_token(self) -> Optional[str]:
        """Get current access token."""
        return self._access_token

    @property
    def user_id(self) -> Optional[str]:
        """Get user ID (username) for API calls."""
        email = self._user_info.get("email")
        if email and "@" in email:
            # Extract username from email (before @)
            return email.split("@")[0]
        return self._user_info.get("username")
