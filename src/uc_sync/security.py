"""Secret redaction for logs and exceptions."""

from __future__ import annotations

import re

_PATTERNS = [
    re.compile(r"dapi[a-zA-Z0-9\-]+"),
    re.compile(r"(?i)(bearer\s+)[a-z0-9\.\-_]+"),
    re.compile(r"(?i)(client_secret[\"']?\s*[:=]\s*[\"']?)[^\"'\s]+"),
    re.compile(r"(?i)(password[\"']?\s*[:=]\s*[\"']?)[^\"'\s]+"),
]


def redact(text: str) -> str:
    out = text
    for pat in _PATTERNS:
        if pat.groups:
            out = pat.sub(r"\1***", out)
        else:
            out = pat.sub("***", out)
    return out
