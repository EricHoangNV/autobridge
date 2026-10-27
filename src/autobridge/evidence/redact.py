"""Credential redaction (P1.W1.7, DIRECTIVES §6).

A spec names credentials by ``credential_ref`` (an env key name); the value
is read at runtime from the environment and never written to a spec,
release, evidence record, log, prompt or API response. These helpers strip
every credential-ref *value* from free text and structured payloads before
they are stored or rendered.

The secret values themselves are never logged or retained here — callers
pass them (or the env mapping) in, and only redacted output comes out.
"""

from __future__ import annotations

import os
from typing import Any

#: Placeholder replacing every credential value.
REDACTED = "***"


def _ordered(secrets: list[str]) -> list[str]:
    """Non-empty secrets, longest first (so overlapping values mask fully)."""
    return sorted({s for s in secrets if s}, key=len, reverse=True)


def redact(text: str, secrets: list[str]) -> str:
    """Replace each secret value in *text* with ``***``.

    Empty secrets are skipped (replacing ``""`` would mask everything).
    Non-string input is returned unchanged.
    """
    if not isinstance(text, str):
        return text
    out = text
    for s in _ordered(secrets):
        out = out.replace(s, REDACTED)
    return out


def redact_payload(payload: Any, secrets: list[str]) -> Any:
    """Recursively redact secret values in a JSON-shaped payload.

    Returns a redacted deep copy; the input is never mutated. Dict keys are
    redacted too (a value could appear as a key); tuples become lists only
    if redaction changed them — in practice payloads are JSON shapes, so
    tuples are converted to lists for JSON round-trip safety.
    """
    ordered = _ordered(secrets)
    if not ordered:
        return payload
    if isinstance(payload, str):
        return redact(payload, ordered)
    if isinstance(payload, dict):
        return {redact(k, ordered): redact_payload(v, ordered) for k, v in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [redact_payload(v, ordered) for v in payload]
    return payload


def secrets_for_refs(
    credential_refs: list[str], env: dict[str, str] | None = None
) -> list[str]:
    """Resolve credential-ref names (env key names) to their secret values.

    Missing refs resolve to nothing — they are silently skipped so a prompt
    built from a partial environment still redacts everything it can.
    ``env`` defaults to ``os.environ``; pass an explicit mapping in tests.
    """
    source = os.environ if env is None else env
    values: list[str] = []
    for ref in credential_refs:
        value = source.get(ref)
        if value:
            values.append(value)
    return values


def redact_prompt(
    prompt: str,
    credential_refs: list[str],
    env: dict[str, str] | None = None,
) -> str:
    """Strip every credential-ref value from a fixture prompt.

    ``credential_refs`` are env key *names* (as stored on specs and
    connectors); the values are looked up in ``env`` (default: the process
    environment) and replaced with ``***``. Unknown refs are ignored.
    """
    return redact(prompt, secrets_for_refs(credential_refs, env))
