"""Redaction helper — strips credential values (stub for W1)."""

from __future__ import annotations


def redact(text: str, secrets: list[str]) -> str:
    """Replace each secret value with ***."""
    out = text
    for s in secrets:
        if s:
            out = out.replace(s, "***")
    return out
