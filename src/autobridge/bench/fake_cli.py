"""Fake CLI helpers (P1.W1.9, plan §9).

Re-exports the fake driver and output builders from
:mod:`autobridge.bench.baseline_coding_agent` under a stable import
path ``autobridge.bench.fake_cli`` so bench tests can do::

    from autobridge.bench.fake_cli import FakeClaudeDriver, build_fake_claude_output

The standalone executable ``scripts/fake_claude.py`` is the subprocess
counterpart (used when the harness spawns ``claude -p`` as a child).
"""

from __future__ import annotations

from autobridge.bench.baseline_coding_agent import (
    ClaudeCliResult,
    FakeClaudeDriver,
    build_fake_claude_output,
    parse_claude_output,
)

__all__ = [
    "ClaudeCliResult",
    "FakeClaudeDriver",
    "build_fake_claude_output",
    "parse_claude_output",
]
