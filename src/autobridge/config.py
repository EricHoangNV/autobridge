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


#: Default base URL for the Meta Muse OpenAI-compatible API (REQ-021 item 5).
DEFAULT_FRONTIER_BASE_URL = "https://api.meta.ai/v1"

#: Default frontier model id (REQ-021 item 5: Meta Spark).
DEFAULT_FRONTIER_MODEL = "muse-spark-1.3"

#: Published prices per million tokens: model id -> (input, output) USD.
#: (REQ-021 item 5: Spark $1.25 / $4.25, Wonder $4 / $20 per M. Cached-input
#: rates exist but :class:`Usage` does not track cached tokens, so uncached
#: input pricing applies to all input tokens.)
MODEL_PRICES_PER_MILLION: dict[str, tuple[str, str]] = {
    "muse-spark-1.3": ("1.25", "4.25"),
    "muse-wonder-1.0": ("4", "20"),
}


def frontier_api_key() -> str:
    """Dedicated Muse key (env ``FRONTIER_API_KEY``); empty when unset.

    Empty is allowed: providers defer client construction to first use, so
    stacks without a frontier key import and run fine until a live call is
    attempted (replay mode covers the demo in that case).
    """
    return os.environ.get("FRONTIER_API_KEY", "")


def frontier_base_url() -> str:
    """Muse OpenAI-compatible base URL (env ``FRONTIER_BASE_URL``)."""
    return os.environ.get("FRONTIER_BASE_URL", "") or DEFAULT_FRONTIER_BASE_URL


def frontier_model() -> str:
    """Frontier model id (env ``FRONTIER_MODEL``, default Spark)."""
    return os.environ.get("FRONTIER_MODEL", "") or DEFAULT_FRONTIER_MODEL


def small_model() -> str:
    """Small-tier model id (env ``SMALL_MODEL``, default: the frontier model).

    The small tier uses the same provider and key (REQ-021 item 5); only the
    model id may differ.
    """
    return os.environ.get("SMALL_MODEL", "") or frontier_model()


def model_price_per_million(model: str) -> tuple[str, str] | None:
    """Published (input, output) USD per 1M tokens for *model*, if known."""
    return MODEL_PRICES_PER_MILLION.get((model or "").strip())
