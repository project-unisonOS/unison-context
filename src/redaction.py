"""
PII redaction helpers for unison-context.
"""

from __future__ import annotations

from typing import Any, Dict

PII_KEYS = {
    "pin",
    "password",
    "auth",
    "faceprint",
    "voiceprint",
    "biometric",
    "token",
    "secret",
}


def redact(obj: Any) -> Any:
    """
    Recursively redact sensitive fields in dictionaries/lists.
    Returns a copy with PII keys replaced by "***".
    """
    if isinstance(obj, dict):
        redacted: Dict[str, Any] = {}
        for k, v in obj.items():
            if k.lower() in PII_KEYS:
                redacted[k] = "***"
            else:
                redacted[k] = redact(v)
        return redacted
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj
