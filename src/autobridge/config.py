"""Runtime configuration — env-driven, no secrets in code."""

from __future__ import annotations

import os

# Model ids live in config (env), never in code (DIRECTIVES §3).
# There are no literal defaults: a missing variable fails loudly via
# require_env naming the env var (REQ-005).


def get_env(key: str, default: str = "") -> str:
    """Read an env var by key (credential_ref pattern)."""
    return os.environ.get(key, default)


def require_env(key: str) -> str:
    """Read a required env var; raise if missing."""
    value = os.environ.get(key)
    if value is None or value == "":
        raise RuntimeError(f"Missing required env var: {key}")
    return value


def anthropic_frontier_model() -> str:
    """Frontier model id for Anthropic (env ``ANTHROPIC_FRONTIER_MODEL``)."""
    return require_env("ANTHROPIC_FRONTIER_MODEL")


def anthropic_small_model() -> str:
    """Small model id for Anthropic (env ``ANTHROPIC_SMALL_MODEL``)."""
    return require_env("ANTHROPIC_SMALL_MODEL")


def openai_frontier_model() -> str:
    """Frontier model id for OpenAI (env ``OPENAI_FRONTIER_MODEL``)."""
    return require_env("OPENAI_FRONTIER_MODEL")


def openai_small_model() -> str:
    """Small model id for OpenAI (env ``OPENAI_SMALL_MODEL``)."""
    return require_env("OPENAI_SMALL_MODEL")
