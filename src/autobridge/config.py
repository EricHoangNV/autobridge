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


#: Default cap on L3 frontier output tokens (REQ-043 item 5). Reasoning
#: models spend most of a small budget on thinking and return an empty
#: message (keyed demo 2026-10-30: 8,190 reasoning tokens, empty text at
#: 8,192); billing is per token used, not per cap, so the default is the
#: Muse maximum (131072, accepted live) rather than another guess.
DEFAULT_FRONTIER_MAX_TOKENS = 131072


def frontier_max_tokens() -> int:
    """L3 output-token cap (env ``FRONTIER_MAX_TOKENS``, default 131072)."""
    raw = (os.environ.get("FRONTIER_MAX_TOKENS", "") or "").strip()
    if not raw:
        return DEFAULT_FRONTIER_MAX_TOKENS
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"FRONTIER_MAX_TOKENS must be an integer, got {raw!r}") from None
    if value <= 0:
        raise ValueError(f"FRONTIER_MAX_TOKENS must be positive, got {value}")
    return value


def opencode_model() -> str:
    """Model for the opencode baseline driver (env ``OPENCODE_MODEL``).

    ``opencode run`` takes ``-m provider/model``; empty (unset) omits the
    flag and opencode uses its configured default.  REQ-024 requires every
    frontier arm to use the same model — set this to the same provider and
    model as ``FRONTIER_MODEL`` (``provider/model`` form); the bench
    records both ids per arm so a mismatch is visible in the scorecard.
    """
    return os.environ.get("OPENCODE_MODEL", "").strip()


def model_price_per_million(model: str) -> tuple[str, str] | None:
    """Published (input, output) USD per 1M tokens for *model*, if known."""
    return MODEL_PRICES_PER_MILLION.get((model or "").strip())


#: ConnectorSpec vocabulary versions the engine understands (REQ-021 item 2, REQ-022 C).
SPEC_VOCABULARIES = ("v1", "v2", "v3")


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


#: Env var naming the sync-scheduler interval in seconds (REQ-030 item 1).
SYNC_INTERVAL_ENV = "AUTOBRIDGE_SYNC_INTERVAL_S"


def sync_interval_seconds() -> float | None:
    """Scheduler tick in seconds (env ``AUTOBRIDGE_SYNC_INTERVAL_S``).

    Returns ``None`` (scheduler off) when unset, empty, blank or
    non-positive — "off when unset". A non-numeric value raises
    ``ValueError`` loudly: a typo'd interval must fail at startup, not
    silently disable the scheduler.
    """
    raw = (os.environ.get(SYNC_INTERVAL_ENV, "") or "").strip()
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        raise ValueError(
            f"{SYNC_INTERVAL_ENV} must be a number of seconds, got {raw!r}"
        ) from None
    if seconds <= 0:
        return None
    return seconds


def runtime_max_vocabulary() -> str:
    """Newest vocabulary the runtime executes (cap for sync pins)."""
    return SPEC_VOCABULARIES[-1]


def spec_vocabulary_default() -> str:
    """Default build vocabulary (env ``SPEC_VOCABULARY``, else ``"v1"``).

    ``"v1"`` keeps the safe refusal: docs that need POST/body-auth, event
    feeds (or any other newer construct) stop at the deterministic
    vocabulary guard with 0 model calls instead of compiling a half-working
    spec. Builds pin ``"v2"``/``"v3"`` explicitly when the docs need them;
    the UI and ``make demo`` show which vocabulary each build used.
    """
    raw = (os.environ.get("SPEC_VOCABULARY", "") or "").strip() or "v1"
    if raw not in SPEC_VOCABULARIES:
        raise ValueError(
            f"SPEC_VOCABULARY must be one of {list(SPEC_VOCABULARIES)}, got {raw!r}"
        )
    return raw
