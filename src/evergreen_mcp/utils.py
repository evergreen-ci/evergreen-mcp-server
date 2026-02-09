"""Shared utilities for Evergreen MCP Server."""

from pathlib import Path
from typing import Any

import yaml

# Evergreen config file location
EVERGREEN_CONFIG_FILE = Path.home() / ".evergreen.yml"

# Cached config to avoid repeated file reads
_cached_config: dict[str, Any] | None = None


class ConfigParseError(Exception):
    """Raised when ~/.evergreen.yml cannot be parsed."""

    pass


def load_evergreen_config(*, use_cache: bool = True) -> dict[str, Any]:
    """Load ~/.evergreen.yml config file.

    Args:
        use_cache: If True, return cached config if available. Set to False
                   to force a fresh read from disk.

    Returns:
        The parsed config dict, or empty dict if file doesn't exist.

    Raises:
        ConfigParseError: If the config file exists but cannot be parsed.
    """
    global _cached_config

    if use_cache and _cached_config is not None:
        return _cached_config

    config: dict[str, Any] = {}
    if EVERGREEN_CONFIG_FILE.exists():
        try:
            with open(EVERGREEN_CONFIG_FILE) as f:
                config = yaml.safe_load(f) or {}
        except Exception as e:
            raise ConfigParseError(
                f"Failed to parse {EVERGREEN_CONFIG_FILE}: {e}"
            ) from e

    if use_cache:
        _cached_config = config

    return config

