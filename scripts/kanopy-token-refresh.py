#!/usr/bin/env python3
"""Background token refresher for Kanopy OIDC.

Reads ~/.evergreen.yml for OAuth config. On startup, ensures a valid
token exists (triggers kanopy-oidc browser login if needed). Then
refreshes the access token every 9 minutes using the refresh_token.

Usage:
    python3 scripts/kanopy-token-refresh.py &
"""

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [kanopy-refresh] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

REFRESH_INTERVAL = 9 * 60  # 9 minutes
EXPIRY_BUFFER = 60  # refresh if < 60s remaining


def load_config() -> dict:
    """Load OAuth config from ~/.evergreen.yml."""
    config_path = Path.home() / ".evergreen.yml"
    with open(config_path) as f:
        config = yaml.safe_load(f)
    oauth = config.get("oauth", {})
    return {
        "issuer": oauth["issuer"],
        "client_id": oauth["client_id"],
        "token_file": Path(oauth["token_file_path"]),
    }


def read_token(token_file: Path) -> dict | None:
    """Read token data from file."""
    try:
        with open(token_file) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_token(token_file: Path, token_data: dict):
    """Atomically save token data to file."""
    token_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = token_file.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(token_data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(token_file)


def is_expired(token_data: dict) -> bool:
    """Check if access token expires within EXPIRY_BUFFER seconds."""
    expires_at = token_data.get("expires_at", 0)
    return time.time() >= (expires_at - EXPIRY_BUFFER)


def trigger_login():
    """Run kanopy-oidc kube login interactively (opens browser)."""
    log.info("No valid token — launching kanopy-oidc login (check your browser)...")
    try:
        subprocess.run(["kanopy-oidc", "kube", "login"], check=True)
        log.info("Login successful")
    except FileNotFoundError:
        log.error("kanopy-oidc not found. Install from: "
                  "https://github.com/kanopy-platform/kanopy-oidc/releases")
        sys.exit(1)
    except subprocess.CalledProcessError:
        log.error("Login failed")
        sys.exit(1)


def refresh(config: dict, token_data: dict) -> dict | None:
    """Use refresh_token to get a new access_token from Dex."""
    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        log.error("No refresh_token in token file")
        return None

    # Discover token endpoint
    discovery_url = f"{config['issuer']}/.well-known/openid-configuration"
    try:
        meta = httpx.get(discovery_url, timeout=10).json()
        token_endpoint = meta["token_endpoint"]
    except Exception as e:
        log.error("Failed to fetch OIDC metadata: %s", e)
        return None

    try:
        resp = httpx.post(
            token_endpoint,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": config["client_id"],
            },
            timeout=10,
        )
        if resp.status_code != 200:
            log.error("Refresh failed (%d): %s", resp.status_code, resp.text[:200])
            return None

        new_token = resp.json()
        if "expires_at" not in new_token and "expires_in" in new_token:
            new_token["expires_at"] = time.time() + new_token["expires_in"]
        return new_token
    except Exception as e:
        log.error("Refresh request failed: %s", e)
        return None


def ensure_valid_token(config: dict, token_file: Path) -> dict:
    """Ensure a valid token exists. Login via browser if needed."""
    token_data = read_token(token_file)

    # No token file at all → login
    if not token_data:
        trigger_login()
        token_data = read_token(token_file)
        if not token_data:
            log.error("Still no token after login")
            sys.exit(1)
        return token_data

    # Token still valid → done
    if not is_expired(token_data):
        return token_data

    # Token expired → try silent refresh first
    log.info("Token expired, attempting silent refresh...")
    new_token = refresh(config, token_data)
    if new_token:
        save_token(token_file, new_token)
        return new_token

    # Refresh failed (refresh_token also dead) → browser login
    log.warning("Silent refresh failed, falling back to browser login")
    trigger_login()
    token_data = read_token(token_file)
    if not token_data:
        log.error("Still no token after login")
        sys.exit(1)
    return token_data


def main():
    config = load_config()
    token_file = config["token_file"]
    log.info("Token file: %s", token_file)
    log.info("Refresh interval: %d minutes", REFRESH_INTERVAL // 60)

    # Startup: ensure we have a valid token (login if needed)
    token_data = ensure_valid_token(config, token_file)
    remaining = int(token_data.get("expires_at", 0) - time.time())
    log.info("Ready — token valid (%d min %ds remaining)", remaining // 60, remaining % 60)

    # Loop: refresh every 9 minutes
    while True:
        time.sleep(REFRESH_INTERVAL)

        token_data = read_token(token_file)
        if not token_data:
            token_data = ensure_valid_token(config, token_file)
            continue

        if is_expired(token_data):
            log.info("Refreshing token...")
            new_token = refresh(config, token_data)
            if new_token:
                save_token(token_file, new_token)
                remaining = int(new_token.get("expires_at", 0) - time.time())
                log.info("Refreshed — valid for %d min %ds", remaining // 60, remaining % 60)
            else:
                # Silent refresh failed — need browser login
                log.warning("Silent refresh failed, need interactive login")
                trigger_login()
        else:
            remaining = int(token_data.get("expires_at", 0) - time.time())
            log.info("Token still valid (%d min %ds remaining)", remaining // 60, remaining % 60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Stopped")
        sys.exit(0)
