"""Fake CLI helpers (P1.W1.9 + P1.W8.2, plan §9).

Re-exports the fake drivers and output builders from
:mod:`bench.baseline_coding_agent` under a stable import
path ``bench.fake_cli`` so bench tests can do::

    from bench.fake_cli import FakeClaudeDriver, build_fake_claude_output

Standalone executables ``scripts/fake_claude.py`` and
``scripts/fake_codex.py`` are the subprocess counterparts (used when the
harness spawns ``claude -p`` / ``codex exec`` as children).
"""

from __future__ import annotations

from bench.baseline_coding_agent import (
    BaselineLLMDriver,
    ClaudeCliError,
    ClaudeCliResult,
    CodexCliDriver,
    CodexCliError,
    FakeClaudeDriver,
    FakeCodexDriver,
    build_fake_claude_output,
    build_fake_codex_output,
    get_baseline_driver,
    parse_claude_output,
    parse_codex_output,
)

try:
    from bench.one_shot import (  # noqa: PLC0415
        OneShotGenerator,
        OneShotResult,
        build_one_shot_prompt,
        extract_spec_yaml,
        load_docs_text,
        run_one_shot_arm,
        run_trial_with_one_shot,
        try_parse_spec_yaml,
    )
except Exception:  # pragma: no cover
    OneShotGenerator = None  # type: ignore
    OneShotResult = None  # type: ignore
    build_one_shot_prompt = None  # type: ignore
    extract_spec_yaml = None  # type: ignore
    load_docs_text = None  # type: ignore
    run_one_shot_arm = None  # type: ignore
    run_trial_with_one_shot = None  # type: ignore
    try_parse_spec_yaml = None  # type: ignore

__all__ = [
    "BaselineLLMDriver",
    "ClaudeCliError",
    "ClaudeCliResult",
    "CodexCliDriver",
    "CodexCliError",
    "FakeClaudeDriver",
    "FakeCodexDriver",
    "OneShotGenerator",
    "OneShotResult",
    "build_fake_claude_output",
    "build_fake_codex_output",
    "build_one_shot_prompt",
    "extract_spec_yaml",
    "get_baseline_driver",
    "load_docs_text",
    "parse_claude_output",
    "parse_codex_output",
    "run_one_shot_arm",
    "run_trial_with_one_shot",
    "try_parse_spec_yaml",
]
