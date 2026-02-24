"""OIDC/OAuth Device Flow Authentication for Evergreen

This module manages DEX authentication using authlib with:
- Token file path configured in ~/.evergreen.yml (oauth.token_file_path)
- Device authorization flow for new authentication

"""

import asyncio
import fcntl
import json
import logging
import os
import tempfile
import time
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import httpx
import jwt as pyjwt
from authlib.integrations.httpx_client import AsyncOAuth2Client

from evergreen_mcp import USER_AGENT
from evergreen_mcp.utils import (
    EVERGREEN_CONFIG_FILE,
    ConfigParseError,
    load_evergreen_config,
)

logger = logging.getLogger(__name__)


class OIDCAuthenticationError(Exception):
    """Raised when OIDC authentication fails.

    This exception is used to signal authentication failures that should
    be handled by the calling code, such as failed device flow authentication
    or token refresh failures.
    """

    pass


class DeviceFlowSlowDown(Exception):
    """Raised when the OAuth server requests slower polling (RFC 8628 Section 3.5).

    Callers should increase their polling interval by at least 5 seconds
    before the next request.
    """

    pass


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
        config = load_evergreen_config(use_cache=False)
    except ConfigParseError as e:
        raise OIDCAuthenticationError(str(e)) from e

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


class FileLock:
    """POSIX advisory file lock using fcntl.flock().

    Provides both synchronous and async context manager interfaces for
    cross-process coordination. The async interface runs the blocking
    flock() call in an executor to avoid stalling the event loop.
    """

    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self._fd: Optional[int] = None

    def acquire(self, blocking: bool = True) -> bool:
        """Acquire the file lock.

        Args:
            blocking: If True, block until lock is acquired. If False,
                      return immediately with False if lock is held.

        Returns:
            True if lock was acquired, False if non-blocking and lock is held.
        """
        self._fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR)
        try:
            flags = fcntl.LOCK_EX
            if not blocking:
                flags |= fcntl.LOCK_NB
            fcntl.flock(self._fd, flags)
            return True
        except OSError:
            if not blocking:
                os.close(self._fd)
                self._fd = None
                return False
            raise

    def release(self):
        """Release the file lock."""
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False

    @asynccontextmanager
    async def async_acquire(self):
        """Async context manager that acquires the lock in an executor."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.acquire)
        try:
            yield self
        finally:
            self.release()


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

        # Cross-process file lock (sibling of token file)
        self.lock_file = (
            self.token_file.with_suffix(".lock") if self.token_file else None
        )
        # In-process lock for token refresh coordination
        self._refresh_lock = asyncio.Lock()

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
                async with httpx.AsyncClient(
                    timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}
                ) as http_client:
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

        try:
            with open(self.token_file) as f:
                token_data = json.load(f)
        except FileNotFoundError:
            logger.debug("Token file not found: %s", self.token_file)
            return None
        except Exception as e:
            logger.error("Error reading token file: %s", e)
            return None

        logger.info("Found token file: %s", self.token_file)
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

        return None

    async def refresh_token(self) -> Optional[dict]:
        """
        Attempt to refresh the token using authlib.

        Uses an in-process asyncio.Lock to prevent concurrent refresh
        requests from racing with rotating refresh tokens. After acquiring
        the lock, re-checks if the token is already valid (another
        coroutine may have refreshed while we waited).

        Returns:
            Token data dict if successful, None otherwise
        """
        if not self._refresh_token:
            logger.warning("No refresh token available")
            return None

        async with self._refresh_lock:
            # Re-check after acquiring lock — another coroutine may have
            # already refreshed the token while we were waiting
            if self._access_token:
                token_data = {
                    "access_token": self._access_token,
                    "refresh_token": self._refresh_token,
                }
                is_valid, _ = self._check_token_expiry(token_data)
                if is_valid:
                    logger.debug(
                        "Token already refreshed by another coroutine, skipping"
                    )
                    return token_data

            return await self._do_refresh_token()

    async def _do_refresh_token(self) -> Optional[dict]:
        """Execute the HTTP token refresh.

        Must be called under self._refresh_lock.
        """
        logger.info("Attempting token refresh...")
        try:
            await self._get_client()

            async with httpx.AsyncClient(
                timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}
            ) as http_client:
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

                    # Save to disk BEFORE updating in-memory state
                    # If save fails, memory and disk stay consistent
                    self._save_token(token_data)

                    # Token saved successfully - now update internal state
                    self._access_token = new_access_token
                    self._refresh_token = new_refresh_token
                    self._user_id = new_user_id

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

        # Write to random temporary file first (avoids cross-process collisions)
        temp_fd = None
        temp_path = None
        try:
            temp_fd = tempfile.NamedTemporaryFile(
                dir=str(self.token_file.parent),
                prefix=".tmp_token_",
                suffix=".json",
                mode="w",
                delete=False,
            )
            temp_path = Path(temp_fd.name)
            json.dump(token_data, temp_fd, indent=2)
            temp_fd.flush()
            os.fsync(temp_fd.fileno())
            temp_fd.close()
            temp_fd = None  # Mark as closed

            # Atomic rename
            temp_path.replace(self.token_file)
            logger.info("Token saved to %s", self.token_file)
        except Exception as e:
            logger.error("Failed to save token: %s", e)
            if temp_fd is not None:
                temp_fd.close()
            if temp_path is not None and temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass

    async def device_flow_auth(self) -> dict:
        """Perform device authorization flow with browser prompt and blocking poll.

        This is the startup authentication flow. It composes initiate_device_flow()
        and poll_device_flow() with browser opening and logging.

        Returns:
            Token data dict containing access_token and refresh_token

        Raises:
            OIDCAuthenticationError: If authentication fails or times out
        """
        device_data = await self.initiate_device_flow()

        verification_uri = device_data["verification_url"]
        user_code = device_data.get("user_code")
        device_code = device_data["device_code"]
        interval = device_data.get("interval", 5)
        expires_in = device_data.get("expires_in", 300)

        # Display auth instructions
        logger.info("=" * 70)
        logger.info("AUTHENTICATION REQUIRED - Please complete login in your browser")
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

        # Poll for token with maximum timeout
        max_attempts = expires_in // interval
        for attempt in range(max_attempts):
            await asyncio.sleep(interval)

            try:
                token_data = await self.poll_device_flow(device_code)
                if token_data:
                    return token_data
            except DeviceFlowSlowDown:
                interval += 5  # RFC 8628 Section 3.5
                logger.debug(
                    "Server requested slow down, increased interval to %d seconds",
                    interval,
                )
                continue

            logger.debug(
                "Authorization pending, polling... (%d/%d)",
                attempt + 1,
                max_attempts,
            )

        raise OIDCAuthenticationError(
            f"Device flow timed out after {expires_in} seconds"
        )

    async def ensure_authenticated(self) -> bool:
        """
        Main authentication flow with cross-process coordination.

        Fast-path check (no lock), then acquires file lock and re-checks
        the token file after acquiring the lock — another process may have
        completed authentication while we waited.

        Steps:
        1. Check if already authenticated (fast path, no lock)
        2. Acquire cross-process file lock
        3. Re-check token file (another process may have written it)
        4. Delegate to _do_authentication() if still needed
        """
        logger.info("Checking authentication status...")

        # Fast path: already authenticated in-memory
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

        # Acquire cross-process file lock before doing authentication
        if self.lock_file:
            file_lock = FileLock(self.lock_file)
            async with file_lock.async_acquire():
                # Re-check token file after acquiring lock — another process
                # may have completed auth while we were waiting
                token_data = self.check_token_file()
                if token_data:
                    self._access_token = token_data["access_token"]
                    self._refresh_token = token_data.get("refresh_token")
                    try:
                        self._user_id = self._extract_user_id(self._access_token)
                        logger.info(
                            "Token file valid after lock acquisition, "
                            "skipping authentication"
                        )
                        return True
                    except OIDCAuthenticationError as e:
                        logger.warning("Token file malformed after lock: %s", e)
                        self._access_token = None

                return await self._do_authentication()
        else:
            return await self._do_authentication()

    async def _do_authentication(self) -> bool:
        """Execute the authentication flow (token file -> refresh -> device flow).

        Must be called either under the cross-process file lock or when no
        lock file is configured.
        """
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

    async def initiate_device_flow(self) -> dict:
        """Start device authorization flow and return auth URL without blocking.

        Returns:
            Dict containing verification_url, user_code, device_code, interval, expires_in

        Raises:
            OIDCAuthenticationError: If device flow initiation fails
        """
        try:
            await self._get_client()

            logger.info("Initiating Device Authorization Flow...")

            device_auth_endpoint = self._metadata["device_authorization_endpoint"]

            async with httpx.AsyncClient(
                timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}
            ) as http_client:
                response = await http_client.post(
                    device_auth_endpoint,
                    data={
                        "client_id": self.client_id,
                        "scope": "openid profile email groups offline_access",
                    },
                )
                response.raise_for_status()
                device_data = response.json()

                verification_uri = device_data.get(
                    "verification_uri_complete"
                ) or device_data.get("verification_uri")

                return {
                    "verification_url": verification_uri,
                    "user_code": device_data.get("user_code"),
                    "device_code": device_data["device_code"],
                    "interval": device_data.get("interval", 5),
                    "expires_in": device_data.get("expires_in", 300),
                }

        except Exception as e:
            raise OIDCAuthenticationError(f"Failed to initiate device flow: {e}") from e

    async def poll_device_flow(self, device_code: str) -> Optional[dict]:
        """Poll once to check if device flow authentication completed.

        Args:
            device_code: Device code from initiate_device_flow

        Returns:
            Token data dict if authentication completed, None if still pending

        Raises:
            DeviceFlowSlowDown: If server requests slower polling. Callers
                should increase their interval by at least 5 seconds.
            OIDCAuthenticationError: If authentication failed (not pending)
        """
        try:
            await self._get_client()
            token_endpoint = self._metadata["token_endpoint"]

            async with httpx.AsyncClient(
                timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}
            ) as http_client:
                response = await http_client.post(
                    token_endpoint,
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        "device_code": device_code,
                        "client_id": self.client_id,
                    },
                )

                if response.status_code == 200:
                    token_data = self._normalize_token_data(response.json())

                    # Validate before persisting
                    new_access_token = token_data["access_token"]
                    new_user_id = self._extract_user_id(new_access_token)

                    # Save to disk BEFORE updating in-memory state
                    self._save_token(token_data)

                    # Disk write succeeded — now update memory
                    self._access_token = new_access_token
                    self._refresh_token = token_data.get("refresh_token")
                    self._user_id = new_user_id

                    logger.info("Authentication successful!")
                    return token_data

                # Parse error
                try:
                    error_data = response.json()
                    error = error_data.get("error", "unknown_error")
                    error_description = error_data.get("error_description", "")
                except Exception:
                    error = "unknown_error"
                    error_description = response.text or ""

                if error == "slow_down":
                    raise DeviceFlowSlowDown()

                if error == "authorization_pending" or response.status_code == 401:
                    return None  # Still waiting

                if error == "expired_token":
                    raise OIDCAuthenticationError(
                        "Device code expired - please restart authentication"
                    )

                raise OIDCAuthenticationError(
                    f"Authentication failed: {error} - {error_description}"
                )

        except (OIDCAuthenticationError, DeviceFlowSlowDown):
            raise
        except Exception as e:
            raise OIDCAuthenticationError(f"Poll error: {e}") from e

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
