"""Allowlisted context is the primary boundary; redaction is defense in depth."""

import re

SENSITIVE_KEYS = re.compile(
    r"password|secret|token|community|private.?key|authorization|cookie", re.IGNORECASE
)
PATTERNS = [
    (r"-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?-----END [^-]*PRIVATE KEY-----", "[private key removed]"),
    (
        r"(?im)^.*\b(?:snmp-server community|enable secret|username .+ (?:secret|password)|password|api[_ -]?key|authorization:).*$",
        "[credential removed]",
    ),
    (r"\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{12,}", "[token removed]"),
    (r"https?://[^\s/@]+:[^\s/@]+@", "https://[credentials removed]@"),
]


def redact(value):
    if isinstance(value, dict):
        return {k: "[removed]" if SENSITIVE_KEYS.search(k) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        for pattern, replacement in PATTERNS:
            value = re.sub(pattern, replacement, value)
        return value
    return value
