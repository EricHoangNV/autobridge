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


#: ConnectorSpec vocabulary versions the engine understands (REQ-021 item 2).
SPEC_VOCABULARIES = ("v1", "v2")


#: Frontier provider modes (REQ-021 item 5).
FRONTIER_MODES = ("live", "replay", "auto", "off")


def frontier_mode() -> str:
    """How L2/L3 calls are served (env ``FRONTIER_MODE``, default ``"auto"``).

    ``"live"`` calls the Muse API (needs ``FRONTIER_API_KEY``); ``"replay"``
    serves redacted recordings (missing recording fails loudly); ``"auto"``
    is live when a key is configured, else replay; ``"off"`` means no
    provider — callers record the skip plainly instead of calling.
    """
    raw = (os.environ.get("FRONTIER_MODE", "") or "").strip().lower() or "auto"
    if raw not in FRONTIER_MODES:
        raise ValueError(f"FRONTIER_MODE must be one of {list(FRONTIER_MODES)}, got {raw!r}")
    return raw


def replay_dir() -> str:
    """Directory of replay recordings (env ``REPLAY_DIR``, default ``demo/recordings``)."""
    return (os.environ.get("REPLAY_DIR", "") or "").strip() or "demo/recordings"


def runtime_max_vocabulary() -> str:
    """Newest vocabulary the runtime executes (cap for sync pins)."""
    return SPEC_VOCABULARIES[-1]


def spec_vocabulary_default() -> str:
    """Default build vocabulary (env ``SPEC_VOCABULARY``, else ``"v1"``).

    ``"v1"`` keeps the safe refusal: docs that need POST/body-auth (or any
    other v2-only construct) stop at the deterministic vocabulary guard
    with 0 model calls instead of compiling a half-working spec. Builds and
    syncs pin ``"v2"`` explicitly when the docs need it; the UI and
    ``make demo`` show which vocabulary each build used.
    """
    raw = (os.environ.get("SPEC_VOCABULARY", "") or "").strip() or "v1"
    if raw not in SPEC_VOCABULARIES:
        raise ValueError(
            f"SPEC_VOCABULARY must be one of {list(SPEC_VOCABULARIES)}, got {raw!r}"
        )
    return raw
