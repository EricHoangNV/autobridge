"""Runtime configuration — env-driven, no secrets in code."""

from __future__ import annotations

import os

# Model ids live in config (env), never in code (DIRECTIVES §3).
# Defaults are safe placeholders for local/test runs.
DEFAULT_ANTHROPIC_FRONTIER_MODEL = "claude-sonnet-4-5-20250929"
DEFAULT_ANTHROPIC_SMALL_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_OPENAI_FRONTIER_MODEL = "gpt-4o"
DEFAULT_OPENAI_SMALL_MODEL = "gpt-4o-mini"


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
    return get_env("ANTHROPIC_FRONTIER_MODEL", DEFAULT_ANTHROPIC_FRONTIER_MODEL)


def anthropic_small_model() -> str:
    """Small model id for Anthropic (env ``ANTHROPIC_SMALL_MODEL``)."""
    return get_env("ANTHROPIC_SMALL_MODEL", DEFAULT_ANTHROPIC_SMALL_MODEL)


def openai_frontier_model() -> str:
    """Frontier model id for OpenAI (env ``OPENAI_FRONTIER_MODEL``)."""
    return get_env("OPENAI_FRONTIER_MODEL", DEFAULT_OPENAI_FRONTIER_MODEL)


def openai_small_model() -> str:
    """Small model id for OpenAI (env ``OPENAI_SMALL_MODEL``)."""
    return get_env("OPENAI_SMALL_MODEL", DEFAULT_OPENAI_SMALL_MODEL)
