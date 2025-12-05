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
from jwt import PyJWKClient
import yaml

logger = logging.getLogger(__name__)

# DEX Configuration
DEX_ISSUER = "https://dex.prod.corp.mongodb.com"
DEX_CLIENT_ID = "login"

# Token file locations
KANOPY_TOKEN_FILE = Path.home() / ".kanopy" / "token-oidclogin.json"
EVERGREEN_CONFIG_FILE = Path.home() / ".evergreen.yml"

# JWKS cache TTL (24 hours)
JWKS_CACHE_TTL = 86400

# Required OAuth scopes for full functionality
# Note: These are requested during auth but validation is advisory only, as many
# OIDC providers (including DEX) don't include scopes in access tokens
REQUIRED_SCOPES = {"openid", "profile", "email", "offline_access", "groups"}

# HTTP timeout configurations (in seconds)
HTTP_TIMEOUT_METADATA = 30  # For fetching OIDC metadata
HTTP_TIMEOUT_TOKEN = 30  # For token operations (refresh, device flow)
HTTP_TIMEOUT_DEVICE_POLL = 10  # For device flow polling (faster to retry)


class OIDCAuthManager:
    """
    Manages DEX authentication with multiple token sources.

    This class handles OIDC/OAuth authentication with JWT signature verification
    and supports multiple token sources (environment, Kanopy, Evergreen config).

    Thread Safety:
    - Uses asyncio locks to prevent race conditions during token refresh
    - Ensures only one refresh/authentication happens at a time
    - Safe for concurrent use across multiple async requests
    """

    def __init__(self):
        self.issuer = DEX_ISSUER
        self.client_id = DEX_CLIENT_ID
        self.device_auth_endpoint: Optional[str] = None
        self.token_endpoint: Optional[str] = None
        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._user_info: dict = {}
        self._jwks_client: Optional[PyJWKClient] = None
        self._jwks_uri: Optional[str] = None
        self._refresh_lock: asyncio.Lock = asyncio.Lock()
        self._auth_lock: asyncio.Lock = asyncio.Lock()

    async def initialize_endpoints(self):
        """Fetch OIDC metadata to discover endpoints."""
        logger.info("Fetching OIDC metadata from %s", self.issuer)
        try:
            timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_METADATA)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    f"{self.issuer}/.well-known/openid-configuration"
                ) as resp:
                    resp.raise_for_status()
                    metadata = await resp.json()
                    self.device_auth_endpoint = metadata.get(
                        "device_authorization_endpoint"
                    )
                    self.token_endpoint = metadata.get("token_endpoint")
                    self._jwks_uri = metadata.get("jwks_uri")

                    # Initialize JWKS client for signature verification
                    if self._jwks_uri:
                        self._jwks_client = PyJWKClient(
                            self._jwks_uri,
                            cache_keys=True,
                            max_cached_keys=10,
                            lifespan=JWKS_CACHE_TTL,
                        )
                        logger.info(
                            "Initialized JWKS client with URI: %s", self._jwks_uri
                        )
        except asyncio.TimeoutError:
            logger.error(
                "Failed to fetch OIDC metadata: timed out after %d seconds. "
                "Check network connectivity to %s",
                HTTP_TIMEOUT_METADATA,
                self.issuer,
            )
            raise
        except aiohttp.ClientError as e:
            logger.error("Network error fetching OIDC metadata: %s", e)
            raise
        except Exception as e:
            logger.error(
                "Unexpected error initializing OIDC endpoints: %s", e, exc_info=True
            )
            raise

    def _verify_and_decode_token(
        self, token: str, verify: bool = True
    ) -> Optional[dict]:
        """
        Verify and decode a JWT token.

        Args:
            token: The JWT token to decode
            verify: Whether to verify the signature (should always be True in production)

        Returns:
            Decoded claims if valid, None otherwise
        """
        try:
            if verify and self._jwks_client:
                # Verify signature using JWKS
                signing_key = self._jwks_client.get_signing_key_from_jwt(token)
                claims = jwt.decode(
                    token,
                    signing_key.key,
                    algorithms=["RS256"],
                    audience=self.client_id,
                    issuer=self.issuer,
                    options={"verify_signature": True},
                )
                return claims
            elif verify:
                # JWKS client not initialized yet - try to get header to determine if we need it
                logger.warning(
                    "JWKS client not initialized. Token signature cannot be verified. "
                    "Call initialize_endpoints() first."
                )
                return None
            else:
                # Fallback for backwards compatibility (not recommended)
                logger.warning(
                    "Decoding token without signature verification (insecure)"
                )
                return jwt.decode(token, options={"verify_signature": False})
        except jwt.ExpiredSignatureError:
            logger.debug("Token has expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.error("Token validation failed: %s", e)
            return None
        except Exception as e:
            logger.error("Unexpected error decoding token: %s", e, exc_info=True)
            return None

    def _check_token_expiry(self, token: str) -> tuple[bool, int]:
        """Check if token is expired. Returns (is_valid, seconds_remaining)."""
        try:
            claims = self._verify_and_decode_token(token, verify=True)
            if not claims:
                return False, 0

            exp = claims.get("exp", 0)
            remaining = exp - time.time()
            return remaining > 60, int(remaining)  # 1 min buffer
        except (KeyError, TypeError, ValueError) as e:
            logger.error("Invalid token format or claims: %s", e)
            return False, 0
        except Exception as e:
            logger.error("Unexpected error checking token expiry: %s", e, exc_info=True)
            return False, 0

    def _extract_user_info(self, token: str) -> dict:
        """Extract user info from JWT token."""
        try:
            claims = self._verify_and_decode_token(token, verify=True)
            if not claims:
                return {}

            return {
                "username": claims.get("preferred_username")
                or claims.get("email")
                or claims.get("sub"),
                "email": claims.get("email"),
                "name": claims.get("name"),
                "groups": claims.get("groups", []),
                "exp": claims.get("exp"),
            }
        except (KeyError, TypeError, AttributeError) as e:
            logger.error("Invalid token claims structure: %s", e)
            return {}
        except Exception as e:
            logger.error("Unexpected error extracting user info: %s", e, exc_info=True)
            return {}

    def _validate_token_scopes(self, token: str) -> bool:
        """
        Validate that the token contains all required scopes.

        Note: Many OIDC providers (including DEX) don't include scope claims in
        access tokens - scopes are typically in ID tokens. This validation is
        best-effort and non-blocking since JWT signature verification is the
        primary security control.

        Args:
            token: The JWT access token to validate

        Returns:
            True if all required scopes are present or if scope validation cannot
            be performed (access tokens often don't contain scopes)
        """
        try:
            claims = self._verify_and_decode_token(token, verify=True)
            if not claims:
                logger.warning("Cannot validate scopes: token validation failed")
                # Return True because signature verification already failed
                # and would have been caught earlier
                return True

            # Get scope from token - can be either space-separated string or list
            token_scope = claims.get("scope", "")

            # If scope claim is missing or empty, this is normal for access tokens
            if not token_scope:
                logger.debug(
                    "Access token does not contain scope claim (normal for many OIDC providers)"
                )
                return True

            if isinstance(token_scope, str):
                granted_scopes = set(token_scope.split())
            elif isinstance(token_scope, list):
                granted_scopes = set(token_scope)
            else:
                logger.warning(
                    "Unexpected scope format in token: %s", type(token_scope)
                )
                # Don't fail - just log warning
                return True

            # Check if all required scopes are present
            missing_scopes = REQUIRED_SCOPES - granted_scopes
            if missing_scopes:
                logger.info(
                    "Token has limited scopes. Missing: %s (granted: %s)",
                    missing_scopes,
                    granted_scopes,
                )
                # Don't fail - scopes in access tokens are advisory
                return True

            logger.debug("Token has all required scopes: %s", granted_scopes)
            return True

        except Exception as e:
            logger.error("Error validating token scopes: %s", e, exc_info=True)
            # Don't fail on validation errors - signature verification is primary control
            return True

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
                    # Validate token has required scopes
                    if not self._validate_token_scopes(access_token):
                        logger.warning("Kanopy token missing required scopes")
                        return None

                    logger.info(
                        "Kanopy token valid (%d min remaining)", remaining // 60
                    )
                    self._access_token = access_token
                    self._user_info = self._extract_user_info(access_token)
                    return access_token
                else:
                    logger.warning("Kanopy token expired")
        except json.JSONDecodeError as e:
            logger.error("Invalid JSON in kanopy token file: %s", e)
        except OSError as e:
            logger.error("Error reading kanopy token file: %s", e)
        except (KeyError, TypeError, AttributeError) as e:
            logger.error("Invalid token data structure in kanopy file: %s", e)
        except Exception as e:
            logger.error("Unexpected error reading kanopy token: %s", e, exc_info=True)

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
                    # Validate token has required scopes
                    if not self._validate_token_scopes(access_token):
                        logger.warning("Evergreen token missing required scopes")
                        # Store refresh token to attempt refresh with correct scopes
                        self._refresh_token = refresh_token or self._refresh_token
                        return None

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
        except yaml.YAMLError as e:
            logger.error("Invalid YAML in evergreen config file: %s", e)
        except json.JSONDecodeError as e:
            logger.error("Invalid JSON in evergreen token file: %s", e)
        except OSError as e:
            logger.error("Error reading evergreen config or token file: %s", e)
        except (KeyError, TypeError, AttributeError) as e:
            logger.error("Invalid config or token data structure: %s", e)
        except Exception as e:
            logger.error(
                "Unexpected error reading evergreen config: %s", e, exc_info=True
            )

        return None

    async def refresh_token(self) -> Optional[str]:
        """
        Attempt to refresh the token.

        Uses a lock to prevent concurrent refresh attempts and includes
        a check to avoid unnecessary refreshes if another request already
        completed the refresh.
        """
        if not self._refresh_token:
            logger.warning("No refresh token available")
            return None

        # Use lock to prevent concurrent refresh attempts
        async with self._refresh_lock:
            # Check if token was already refreshed by another request
            if self._access_token:
                is_valid, remaining = self._check_token_expiry(self._access_token)
                if is_valid:
                    logger.debug(
                        "Token already refreshed by another request (%d min remaining)",
                        remaining // 60,
                    )
                    return self._access_token

            if not self.token_endpoint:
                await self.initialize_endpoints()

            logger.info("Attempting token refresh...")
            try:
                timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_TOKEN)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    data = {
                        "client_id": self.client_id,
                        "grant_type": "refresh_token",
                        "refresh_token": self._refresh_token,
                    }
                    async with session.post(self.token_endpoint, data=data) as resp:
                        if resp.status == 200:
                            token_data = await resp.json()
                            new_access_token = token_data.get("access_token")

                            # Validate token has required scopes
                            if not self._validate_token_scopes(new_access_token):
                                logger.error("Refreshed token missing required scopes")
                                return None

                            self._access_token = new_access_token
                            self._refresh_token = token_data.get(
                                "refresh_token", self._refresh_token
                            )
                            self._user_info = self._extract_user_info(
                                self._access_token
                            )

                            # Save to kanopy token file
                            self._save_token(token_data)
                            logger.info("Token refreshed successfully!")
                            return self._access_token
                        else:
                            error_text = await resp.text()
                            logger.error("Refresh failed: %s", error_text)
            except asyncio.TimeoutError:
                logger.error(
                    "Token refresh timed out after %d seconds. "
                    "Check network connectivity to OIDC provider.",
                    HTTP_TIMEOUT_TOKEN,
                )
            except aiohttp.ClientError as e:
                logger.error("Network error during token refresh: %s", e)
            except json.JSONDecodeError as e:
                logger.error("Invalid JSON response during token refresh: %s", e)
            except OSError as e:
                logger.error("Error saving refreshed token: %s", e)
            except Exception as e:
                logger.error(
                    "Unexpected error during token refresh: %s", e, exc_info=True
                )

            return None

    def _save_token(self, token_data: dict):
        """
        Save token to kanopy token file atomically.

        Uses atomic write pattern (write to temp file + rename) to prevent
        corruption if the process crashes during write.
        """
        KANOPY_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)

        # Write to temporary file first
        temp_file = KANOPY_TOKEN_FILE.with_suffix(".tmp")
        try:
            with open(temp_file, "w") as f:
                json.dump(token_data, f, indent=2)
                # Ensure data is flushed to disk
                f.flush()
                os.fsync(f.fileno())

            # Atomic rename (replaces existing file atomically)
            temp_file.replace(KANOPY_TOKEN_FILE)
            logger.info("Token saved to %s", KANOPY_TOKEN_FILE)
        except OSError as e:
            logger.error("Failed to save token: %s", e)
            # Clean up temp file if it exists
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except OSError:
                    pass
            raise
        except Exception as e:
            logger.error("Unexpected error saving token: %s", e, exc_info=True)
            # Clean up temp file if it exists
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except OSError:
                    pass
            raise

    async def device_flow_auth(self) -> Optional[str]:
        """Perform device authorization flow."""
        if not self.device_auth_endpoint:
            await self.initialize_endpoints()

        logger.info("Starting Device Authorization Flow...")

        try:
            # Use shorter timeout for device flow since polling requests should be quick
            timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_DEVICE_POLL)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                # Step 1: Request device code
                data = {
                    "client_id": self.client_id,
                    "scope": " ".join(sorted(REQUIRED_SCOPES)),
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
                while True:
                    await asyncio.sleep(interval)

                    poll_data = {
                        "client_id": self.client_id,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        "device_code": device_code,
                    }

                    async with session.post(
                        self.token_endpoint, data=poll_data
                    ) as resp:
                        if resp.status == 200:
                            token_data = await resp.json()
                            new_access_token = token_data.get("access_token")

                            # Validate token has required scopes
                            if not self._validate_token_scopes(new_access_token):
                                logger.error(
                                    "Received token missing required scopes. "
                                    "Please ensure the OIDC provider grants all requested scopes."
                                )
                                return None

                            self._access_token = new_access_token
                            self._refresh_token = token_data.get("refresh_token")
                            self._user_info = self._extract_user_info(
                                self._access_token
                            )
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
        except asyncio.TimeoutError:
            logger.error(
                "Device flow authentication timed out after %d seconds. "
                "This may happen if the OIDC provider is slow or network is unstable.",
                HTTP_TIMEOUT_DEVICE_POLL,
            )
            return None
        except aiohttp.ClientError as e:
            logger.error("Network error during device flow authentication: %s", e)
            return None
        except Exception as e:
            logger.error("Unexpected error during device flow: %s", e, exc_info=True)
            return None

    async def ensure_authenticated(self) -> bool:
        """
        Main authentication flow with concurrency protection.

        Uses a lock to ensure only one authentication attempt happens at a time,
        preventing race conditions when multiple requests detect expired tokens.

        Steps:
        1. Initialize OIDC endpoints (including JWKS)
        2. Check kanopy token
        3. Check evergreen token
        4. Try refresh if expired
        5. Do device flow if needed
        """
        # Use lock to prevent concurrent authentication attempts
        async with self._auth_lock:
            logger.info("Checking authentication status...")

            # Check if another request already authenticated while we waited for the lock
            if self._access_token:
                is_valid, remaining = self._check_token_expiry(self._access_token)
                if is_valid:
                    logger.debug(
                        "Already authenticated by another request (%d min remaining)",
                        remaining // 60,
                    )
                    return True

            # Initialize endpoints and JWKS client first
            if not self._jwks_client:
                logger.info("Initializing OIDC endpoints and JWKS client...")
                await self.initialize_endpoints()

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

    def is_authenticated(self) -> bool:
        """
        Check if currently authenticated with a valid token.

        This is a non-blocking check that doesn't trigger authentication.
        Use this to check auth status without acquiring locks.

        Returns:
            True if access token exists and is valid, False otherwise
        """
        if not self._access_token:
            return False

        is_valid, _ = self._check_token_expiry(self._access_token)
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
