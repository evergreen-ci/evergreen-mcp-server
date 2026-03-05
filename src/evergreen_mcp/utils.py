"""Shared utilities for Evergreen MCP Server."""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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


# ---------------------------------------------------------------------------
# Log error scanning
# ---------------------------------------------------------------------------

ERROR_KEYWORDS: List[str] = [
    # Go
    "panic",
    "fatal",
    "FAIL",
    "runtime error",
    "goroutine",
    "deadlock",
    "nil pointer dereference",
    "index out of range",
    "slice bounds out of range",
    "invalid memory address",
    # Python
    "Traceback",
    "Exception",
    "Error",
    "raise",
    "AssertionError",
    "ImportError",
    "ModuleNotFoundError",
    "AttributeError",
    "TypeError",
    "ValueError",
    "KeyError",
    "IndexError",
    "RuntimeError",
    "FileNotFoundError",
    "PermissionError",
    "OSError",
    "TimeoutError",
    "ConnectionError",
    # Java / JVM
    "Exception in thread",
    "java.lang",
    "NullPointerException",
    "ClassNotFoundException",
    "OutOfMemoryError",
    "StackOverflowError",
    # Node / JS
    "ReferenceError",
    "SyntaxError",
    "UnhandledPromiseRejection",
    "ECONNREFUSED",
    "ENOENT",
    "EACCES",
    "EPERM",
    # Kubernetes / containers
    "CrashLoopBackOff",
    "OOMKilled",
    "ImagePullBackOff",
    "ErrImagePull",
    "CreateContainerError",
    "RunContainerError",
    "pod has unbound",
    "Evicted",
    # Network / HTTP
    "connection refused",
    "connection reset",
    "connection timed out",
    "timeout",
    "ETIMEDOUT",
    "ECONNRESET",
    "502 Bad Gateway",
    "503 Service Unavailable",
    "504 Gateway Timeout",
    # Auth / permissions
    "permission denied",
    "access denied",
    "unauthorized",
    "forbidden",
    "401",
    "403",
    # Database
    "deadlock detected",
    "lock wait timeout",
    "duplicate key",
    "constraint violation",
    "connection pool exhausted",
    # Generic
    "CRITICAL",
    "FATAL",
    "EMERGENCY",
    "ALERT",
    "segfault",
    "core dumped",
    "killed",
    "signal",
    "out of memory",
    "disk full",
    "no space left",
    "failed",
    "failure",
    "error",
]


def _build_error_regex(keywords: List[str]) -> re.Pattern:
    """Compile *keywords* into one case-insensitive alternation pattern."""
    escaped = [re.escape(kw) for kw in keywords]
    return re.compile("|".join(escaped), re.IGNORECASE)


_ERROR_RE: re.Pattern = _build_error_regex(ERROR_KEYWORDS)


@dataclass
class LogScanResult:
    """Result of scanning a log for error keywords."""

    total_lines: int = 0
    matched_lines: int = 0
    top_terms: List[Tuple[str, int]] = field(default_factory=list)
    examples_by_term: Dict[str, List[str]] = field(default_factory=dict)
    matched_excerpt: str = ""


def scan_log_for_errors(
    log_text: str,
    *,
    keywords: Optional[List[str]] = None,
    max_examples: int = 3,
    top_n: int = 10,
) -> LogScanResult:
    """Scan *log_text* line-by-line for error keywords.

    Args:
        log_text: Raw log content.
        keywords: Custom keyword list; defaults to ``ERROR_KEYWORDS``.
        max_examples: Max example lines stored per term.
        top_n: How many top terms to include in the result.

    Returns:
        A ``LogScanResult`` with counts, top terms, and example lines.
    """
    regex = _build_error_regex(keywords) if keywords else _ERROR_RE

    lines = log_text.splitlines()
    total = len(lines)
    counter: Counter = Counter()
    examples: Dict[str, List[str]] = defaultdict(list)
    matched_indices: List[int] = []

    for idx, line in enumerate(lines):
        hits = regex.findall(line)
        if not hits:
            continue
        matched_indices.append(idx)
        for hit in hits:
            term = hit.lower()
            counter[term] += 1
            if len(examples[term]) < max_examples:
                examples[term].append(line.rstrip())

    top_terms = counter.most_common(top_n)

    # Build a short excerpt from the matched lines (last 50 matched lines max)
    excerpt_lines = [lines[i] for i in matched_indices[-50:]]
    matched_excerpt = "\n".join(excerpt_lines)

    return LogScanResult(
        total_lines=total,
        matched_lines=len(matched_indices),
        top_terms=top_terms,
        examples_by_term=dict(examples),
        matched_excerpt=matched_excerpt,
    )
