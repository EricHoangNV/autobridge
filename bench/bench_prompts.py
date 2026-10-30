"""Bench prompt files — ``config/prompts/bench/`` loader (REQ-046).

Bench prompts live in files with the same loader/front-matter rules as
the engine prompts (REQ-045: ``autobridge.llm.prompts.parse_prompt_file`` —
``id == filename``, ``tier ∈ {small, frontier}``, declared ``inputs``
all render). They are loaded once at bench start
(:func:`ensure_bench_prompts_loaded`); editing a file takes effect on
the next bench run — never hot-reloaded, never per call. Each arm's
scorecard row records its prompt ``id``/``version``/content ``hash``
(see :func:`bench_prompt_ref`), so a re-run after an edit is traceable.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

#: Env var overriding the bench prompts directory (mirrors PROMPTS_ENV).
BENCH_PROMPTS_ENV = "AUTOBRIDGE_BENCH_PROMPTS_DIR"

#: Every bench prompt file that must exist (file ``<id>.md``).
REQUIRED_BENCH_PROMPT_IDS: tuple[str, ...] = ("frontier_oneshot", "frontier_agent")


def default_bench_prompts_dir() -> Path:
    """Bench prompts directory: env override, else the repo checkout."""
    override = os.environ.get(BENCH_PROMPTS_ENV, "").strip()
    if override:
        return Path(override)
    # bench/bench_prompts.py -> repo root is parents[1].
    return Path(__file__).resolve().parents[1] / "config" / "prompts" / "bench"


def load_bench_prompts(directory: str | Path | None = None) -> dict[str, Any]:
    """Load and validate every required bench prompt; fail closed naming the file."""
    from autobridge.llm.prompts import parse_prompt_file  # noqa: PLC0415

    resolved = Path(directory) if directory is not None else default_bench_prompts_dir()
    loaded: dict[str, Any] = {}
    for prompt_id in REQUIRED_BENCH_PROMPT_IDS:
        loaded[prompt_id] = parse_prompt_file(resolved / f"{prompt_id}.md")
    return loaded


_BENCH_LOADED: dict[str, Any] | None = None


def get_bench_prompts() -> dict[str, Any]:
    """Process-wide loaded bench prompts (loaded once, never per call)."""
    global _BENCH_LOADED
    if _BENCH_LOADED is None:
        _BENCH_LOADED = load_bench_prompts()
    return _BENCH_LOADED


def ensure_bench_prompts_loaded() -> dict[str, Any]:
    """Load and validate all bench prompts now — call at bench start."""
    return get_bench_prompts()


def reset_bench_prompts_cache() -> None:
    """Drop the process-wide cache (tests only; production never hot-reloads)."""
    global _BENCH_LOADED
    _BENCH_LOADED = None


def get_bench_prompt(prompt_id: str) -> Any:
    """One loaded bench prompt by id (``KeyError`` for unknown ids)."""
    return get_bench_prompts()[prompt_id]


def bench_prompt_ref(prompt_id: str) -> dict[str, str]:
    """Ledger reference for a bench prompt: ``id``, ``version``, content ``hash``."""
    template = get_bench_prompt(prompt_id)
    return {"id": template.id, "version": template.version, "hash": template.content_hash}


def healthz() -> dict[str, str]:
    """Health check for the bench prompts module."""
    return {"status": "ok", "service": "bench_prompts"}
