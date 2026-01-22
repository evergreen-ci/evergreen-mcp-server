
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


ERROR_KEYWORDS = [
    # Universal - high signal error indicators
    "error", "errors", "fatal", "panic", "crash", "crashed", "exception",
    "unhandled", "uncaught", "abort", "aborted", "fail", "failed", "failure",
    "critical", "severe", "emergency", "assert", "assertion", "corrupt",
    "corruption", "invariant", "violation",

    # Traces / crashes - very high signal
    "stack trace", "stacktrace", "traceback", "call stack",
    "segmentation fault", "segfault", "core dumped", "bus error",
    "illegal instruction", "SIGSEGV", "SIGABRT", "SIGILL", "SIGFPE",
    "SIGKILL", "SIGTERM",
    "goroutine",

    # Python
    "Traceback (most recent call last)",
    "RuntimeError", "ValueError", "TypeError", "KeyError", "IndexError",
    "AttributeError", "ImportError", "ModuleNotFoundError",
    "ZeroDivisionError", "AssertionError", "SyntaxError", "IndentationError",

    # Java/JVM
    "Exception in thread", "Caused by",
    "NullPointerException", "ClassNotFoundException", "NoClassDefFoundError",
    "IllegalArgumentException", "IllegalStateException",
    "IndexOutOfBoundsException", "ConcurrentModificationException",
    "OutOfMemoryError", "StackOverflowError", "LinkageError", "VerifyError",

    # Go - critical patterns for detecting panics
    "panic:", "fatal error:", "runtime error",
    "nil pointer dereference", "index out of range",
    "deadlock", "all goroutines are asleep",
    "concurrent map read and map write",
    "panic recovered",
    "runtime.gopanic",
    "runtime.throw",

    # Node / JS
    "UnhandledPromiseRejection", "UnhandledPromiseRejectionWarning",
    "TypeError:", "ReferenceError:", "RangeError:", "SyntaxError:",
    "ERR_", "ECONNREFUSED", "ECONNRESET", "EADDRINUSE", "EPIPE", "ENOMEM",
    "ENOSPC", "MODULE_NOT_FOUND",

    # Network errors - but avoid matching port numbers
    "connection refused", "connection reset", "connection timeout",
    "timed out", "read timeout", "write timeout",
    "TLS handshake failed", "SSL error", "certificate verify failed", "x509",
    "broken pipe", "no route to host", "host unreachable",
    "network unreachable", "dns error", "NXDOMAIN", "SERVFAIL",

    # Auth / OIDC / OAuth / JWT - specific error contexts
    "unauthorized", "forbidden", "access denied", "permission denied",
    "not authorized", "auth failed", "authentication failed",
    "authorization failed", "invalid token", "expired token", "token expired",
    "missing token", "invalid credentials", "bad credentials",
    "invalid signature", "jwt expired", "jwt invalid",
    "oidc error", "oauth error", "invalid grant", "invalid scope",
    "invalid client",

    # DB / storage / FS
    "SQLSTATE", "constraint violation", "unique constraint",
    "foreign key constraint", "deadlock detected", "lock wait timeout",
    "serialization failure", "duplicate key", "relation does not exist",
    "table not found", "column not found", "invalid input syntax",
    "too many connections", "connection pool exhausted",
    "no space left on device", "disk full", "read-only file system",
    "file not found", "directory not found", "too many open files", "EMFILE",

    # K8s / containers
    "OOMKilled", "out of memory", "killed process", "signal: killed",
    "CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull",
    "FailedScheduling", "NodeNotReady", "Readiness probe failed",
    "Liveness probe failed", "Pod evicted", "Back-off restarting",
    
    "fassert",
    "invariant failure",
    "tripwire",
]


def _build_error_regex(keywords: List[str]) -> re.Pattern:
    """
    Build a regex that matches:
      - exact phrases (case-insensitive)
      - 'ERR_*' patterns
      - common level prefixes
    
    NOTE: We explicitly EXCLUDE generic 4xx/5xx HTTP status code patterns 
    because they produce too many false positives (matching timestamps, 
    port numbers, document IDs, etc.). Specific status codes like 401, 404, 
    500 should be in the keyword list if needed.
    """
    # Escape keywords, but keep some patterns flexible
    escaped = []
    for kw in keywords:
        if kw == "ERR_":
            escaped.append(r"ERR_[A-Z0-9_]+")
        elif kw in ("4xx", "5xx"):
            # SKIP generic HTTP status patterns - they cause too many false positives
            # with timestamps (17:07:10,500), port numbers, etc.
            # Specific codes (401, 404, 500) should be explicit keywords if needed
            continue
        else:
            escaped.append(re.escape(kw))

    # A few additional high-signal patterns not covered by keywords
    # These are clear error indicators that won't match timestamps
    extras = [
        r"\bERROR\b", r"\bFATAL\b", r"\bPANIC\b", r"\bCRITICAL\b",
        r"\bSEVERE\b",
        # Note: Removed WARN/WARNING - too noisy, focus on actual errors
    ]

    pattern = r"(" + "|".join(extras + escaped) + r")"
    return re.compile(pattern, re.IGNORECASE)


ERROR_REGEX = _build_error_regex(ERROR_KEYWORDS)


@dataclass
class LogScanResult:
    total_lines: int
    matched_lines: int
    top_terms: List[Tuple[str, int]]
    examples_by_term: Dict[str, List[str]]
    matched_excerpt: Optional[str] = None  # optional joined matched lines


def scan_log_for_errors(
    log_text: str,
    *,
    max_examples_per_term: int = 3,
    max_total_matched_lines: int = 2000,
    include_matched_excerpt: bool = False,
) -> LogScanResult:
    """
    Scan log text for error-like keywords/patterns.
    Returns counts + examples + optional excerpt of matched lines.

    Notes:
    - This is line-based (works well on huge logs).
    - Keeps limited examples per term to avoid huge memory use.
    """
    if not log_text:
        return LogScanResult(
            total_lines=0,
            matched_lines=0,
            top_terms=[],
            examples_by_term={},
            matched_excerpt="" if include_matched_excerpt else None,
        )

    lines = log_text.splitlines()
    total_lines = len(lines)

    counter = Counter()
    examples_by_term: Dict[str, List[str]] = defaultdict(list)

    matched_lines_out: List[str] = []
    matched_line_count = 0

    for line in lines:
        m = ERROR_REGEX.search(line)
        if not m:
            continue

        matched_line_count += 1
        if include_matched_excerpt and len(matched_lines_out) < max_total_matched_lines:
            matched_lines_out.append(line)

        # Count *all* occurrences per line (not just first)
        for hit in ERROR_REGEX.findall(line):
            # Normalize term keys a bit (lowercase)
            term = hit.lower()
            counter[term] += 1
            if len(examples_by_term[term]) < max_examples_per_term:
                examples_by_term[term].append(line)

    top_terms = counter.most_common(30)

    return LogScanResult(
        total_lines=total_lines,
        matched_lines=matched_line_count,
        top_terms=top_terms,
        examples_by_term=dict(examples_by_term),
        matched_excerpt="\n".join(matched_lines_out) if include_matched_excerpt else None,
    )