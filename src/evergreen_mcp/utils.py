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


def patch_version(patch: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return a patch's version object, or None if the patch has none.

    The GraphQL `Patch.version` field replaced the removed `versionFull`
    field (DEVPROD-38057). Tolerate both keys so responses cached or mocked
    under either name keep working.
    """
    version = patch.get("version") if patch else None
    if version is None:
        version = (patch or {}).get("versionFull")
    return version


def patch_author(patch: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Extract author info from a GraphQL Patch object.

    The `Patch.author` / `Patch.authorDisplayName` fields were removed in
    DEVPROD-38057; authorship now comes from `Patch.user` (the submitting
    user) and `Patch.githubPatchData` (for GitHub PR patches).
    Note: for GitHub PR patches the returned `author` is the GitHub username
    (githubPatchData.author), not the Evergreen user id.
    """
    patch = patch or {}
    user = patch.get("user") or {}
    github = patch.get("githubPatchData") or {}
    return {
        "author": github.get("author") or user.get("id"),
        "author_display_name": user.get("displayName"),
    }


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
    """Compile *keywords* into one case-insensitive alternation pattern.

    Numeric keywords are anchored on word boundaries; alphabetic ones are not,
    and deliberately so.

    The numeric keywords ("401", "403") otherwise match inside any longer number
    — a line number, a byte count, a timestamp, an Evergreen task id — so
    unrelated lines get pulled into the error summary ("line 2403 all quiet"
    matched on "403"). But anchoring the alphabetic keywords costs real recall,
    because the interesting forms are plural and compound: "errors were found",
    "FatalError", "ConnectionResetError", "TestFailure" all contain a keyword as
    a substring and must still match. Substring matching is the point for those.
    """
    parts = []
    for kw in keywords:
        escaped = re.escape(kw)
        # Leading \b whenever the keyword opens with a digit, so it cannot be
        # absorbed into a longer number. Trailing \b only when it also ends in a
        # digit ("403"); a keyword ending in a letter ("502 Bad Gateway") has no
        # numeric-absorption risk and anchoring it would only lose matches.
        if kw[:1].isdigit():
            prefix = r"\b"
            suffix = r"\b" if kw[-1:].isdigit() else ""
        else:
            prefix = suffix = ""
        parts.append(f"{prefix}{escaped}{suffix}")
    return re.compile("|".join(parts), re.IGNORECASE)


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
