"""OIDC/OAuth Device Flow Authentication for Evergreen

This module manages DEX authentication with multiple token sources:
- Environment variables (for Docker/containerized deployments)
- ~/.kanopy/token-oidclogin.json (Kanopy token file)
- ~/.evergreen.yml oauth configuration (Evergreen token file)
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

import aiohttp
import jwt
import yaml

logger = logging.getLogger(__name__)

# DEX Configuration
DEX_ISSUER = "https://dex.prod.corp.mongodb.com"
DEX_CLIENT_ID = "login"

# Token file locations
KANOPY_TOKEN_FILE = Path.home() / ".kanopy" / "token-oidclogin.json"
EVERGREEN_CONFIG_FILE = Path.home() / ".evergreen.yml"


class OIDCAuthManager:
    """Manages DEX authentication with multiple token sources."""

    def __init__(self):
        self.issuer = DEX_ISSUER
        self.client_id = DEX_CLIENT_ID
        self.device_auth_endpoint: Optional[str] = None
        self.token_endpoint: Optional[str] = None
        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._user_info: dict = {}

    async def initialize_endpoints(self):
        """Fetch OIDC metadata to discover endpoints."""
        logger.info("Fetching OIDC metadata from %s", self.issuer)
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.issuer}/.well-known/openid-configuration"
            ) as resp:
                resp.raise_for_status()
                metadata = await resp.json()
                self.device_auth_endpoint = metadata.get("device_authorization_endpoint")
                self.token_endpoint = metadata.get("token_endpoint")

    def _check_token_expiry(self, token: str) -> tuple[bool, int]:
        """Check if token is expired. Returns (is_valid, seconds_remaining)."""
        try:
            claims = jwt.decode(token, options={"verify_signature": False})
            exp = claims.get("exp", 0)
            remaining = exp - time.time()
            return remaining > 60, int(remaining)  # 1 min buffer
        except Exception:
            return False, 0

    def _extract_user_info(self, token: str) -> dict:
        """Extract user info from JWT token."""
        try:
            claims = jwt.decode(token, options={"verify_signature": False})
            return {
                "username": claims.get("preferred_username")
                or claims.get("email")
                or claims.get("sub"),
                "email": claims.get("email"),
                "name": claims.get("name"),
                "groups": claims.get("groups", []),
                "exp": claims.get("exp"),
            }
        except Exception:
            return {}

    def check_kanopy_token(self) -> Optional[str]:
        """Check ~/.kanopy/token-oidclogin.json for valid token."""
        if not KANOPY_TOKEN_FILE.exists():
            logger.debug("Kanopy token file not found: %s", KANOPY_TOKEN_FILE)
            return None

        logger.info("Found kanopy token file: %s", KANOPY_TOKEN_FILE)
        try:
            with open(KANOPY_TOKEN_FILE) as f:
                data = json.load(f)

            access_token = data.get("access_token")
            self._refresh_token = data.get("refresh_token")

            if access_token:
                is_valid, remaining = self._check_token_expiry(access_token)
                if is_valid:
                    logger.info("Kanopy token valid (%d min remaining)", remaining // 60)
                    self._access_token = access_token
                    self._user_info = self._extract_user_info(access_token)
                    return access_token
                else:
                    logger.warning("Kanopy token expired")
        except Exception as e:
            logger.error("Error reading kanopy token: %s", e)

        return None

    def check_evergreen_token(self) -> Optional[str]:
        """Check ~/.evergreen.yml for oauth token configuration."""
        if not EVERGREEN_CONFIG_FILE.exists():
            logger.debug("Evergreen config not found: %s", EVERGREEN_CONFIG_FILE)
            return None

        logger.info("Found evergreen config: %s", EVERGREEN_CONFIG_FILE)
        try:
            with open(EVERGREEN_CONFIG_FILE) as f:
                config = yaml.safe_load(f)

            oauth = config.get("oauth", {})
            token_file_path = oauth.get("token_file_path")

            if not token_file_path:
                logger.debug("No oauth.token_file_path in evergreen config")
                return None

            token_path = Path(token_file_path)
            if not token_path.exists():
                logger.warning("Evergreen token file not found: %s", token_path)
                return None

            logger.info("Found evergreen token file: %s", token_path)
            with open(token_path) as f:
                data = json.load(f)

            access_token = data.get("access_token")
            refresh_token = data.get("refresh_token")

            if access_token:
                is_valid, remaining = self._check_token_expiry(access_token)
                if is_valid:
                    logger.info(
                        "Evergreen token valid (%d min remaining)", remaining // 60
                    )
                    self._access_token = access_token
                    self._refresh_token = refresh_token or self._refresh_token
                    self._user_info = self._extract_user_info(access_token)
                    return access_token
                else:
                    logger.warning("Evergreen token expired")
                    # Store refresh token for potential refresh
                    self._refresh_token = refresh_token or self._refresh_token
        except Exception as e:
            logger.error("Error reading evergreen config: %s", e)

        return None

    async def refresh_token(self) -> Optional[str]:
        """Attempt to refresh the token."""
        if not self._refresh_token:
            logger.warning("No refresh token available")
            return None

        if not self.token_endpoint:
            await self.initialize_endpoints()

        logger.info("Attempting token refresh...")
        try:
            async with aiohttp.ClientSession() as session:
                data = {
                    "client_id": self.client_id,
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                }
                async with session.post(self.token_endpoint, data=data) as resp:
                    if resp.status == 200:
                        token_data = await resp.json()
                        self._access_token = token_data.get("access_token")
                        self._refresh_token = token_data.get(
                            "refresh_token", self._refresh_token
                        )
                        self._user_info = self._extract_user_info(self._access_token)

                        # Save to kanopy token file
                        self._save_token(token_data)
                        logger.info("Token refreshed successfully!")
                        return self._access_token
                    else:
                        error_text = await resp.text()
                        logger.error("Refresh failed: %s", error_text)
        except Exception as e:
            logger.error("Refresh error: %s", e)

        return None

    def _save_token(self, token_data: dict):
        """Save token to kanopy token file."""
        KANOPY_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(KANOPY_TOKEN_FILE, "w") as f:
            json.dump(token_data, f, indent=2)
        logger.info("Token saved to %s", KANOPY_TOKEN_FILE)

    async def device_flow_auth(self) -> Optional[str]:
        """Perform device authorization flow."""
        if not self.device_auth_endpoint:
            await self.initialize_endpoints()

        logger.info("Starting Device Authorization Flow...")

        async with aiohttp.ClientSession() as session:
            # Step 1: Request device code
            data = {
                "client_id": self.client_id,
                "scope": "openid profile email offline_access groups",
            }
            async with session.post(self.device_auth_endpoint, data=data) as resp:
                resp.raise_for_status()
                device_data = await resp.json()

            verification_uri = device_data.get(
                "verification_uri_complete"
            ) or device_data.get("verification_uri")
            user_code = device_data.get("user_code")
            device_code = device_data.get("device_code")
            interval = device_data.get("interval", 5)

            # Display auth instructions
            logger.info("=" * 70)
            logger.info("🔐 AUTHENTICATION REQUIRED - Please complete login in your browser")
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
            while True:
                await asyncio.sleep(interval)

                poll_data = {
                    "client_id": self.client_id,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": device_code,
                }

                async with session.post(self.token_endpoint, data=poll_data) as resp:
                    if resp.status == 200:
                        token_data = await resp.json()
                        self._access_token = token_data.get("access_token")
                        self._refresh_token = token_data.get("refresh_token")
                        self._user_info = self._extract_user_info(self._access_token)
                        self._save_token(token_data)
                        logger.info("Authentication successful!")
                        return self._access_token

                    error_data = await resp.json()
                    error = error_data.get("error")

                    if error == "authorization_pending":
                        logger.debug("Authorization pending, polling...")
                        continue
                    elif error == "slow_down":
                        interval += 2
                        continue
                    elif error == "expired_token":
                        logger.error("Authentication request expired")
                        return None
                    else:
                        logger.error("Authentication failed: %s", error)
                        return None

    async def ensure_authenticated(self) -> bool:
        """
        Main authentication flow:
        1. Check kanopy token
        2. Check evergreen token
        3. Try refresh if expired
        4. Do device flow if needed
        """
        logger.info("Checking authentication status...")

        # Check kanopy token
        logger.info("Checking Kanopy token...")
        token = self.check_kanopy_token()
        if token:
            return True

        # Check evergreen token
        logger.info("Checking Evergreen token...")
        token = self.check_evergreen_token()
        if token:
            return True

        # Try refresh if we have a refresh token
        if self._refresh_token:
            logger.info("Attempting token refresh...")
            token = await self.refresh_token()
            if token:
                return True

        # Need to authenticate
        logger.warning("No valid token found - authentication required")
        token = await self.device_flow_auth()
        return token is not None

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

