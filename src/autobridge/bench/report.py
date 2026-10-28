"""Benchmark report — scorecard markdown (P1.W8.4, plan §2.10).

Renders a scored bench dict (see :mod:`autobridge.bench.scoring`) to
``experiments/scorecard.md``.  Wins **and** losses are both listed —
a scorecard that hides losses fails the W8 passing criteria.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

from autobridge.bench.scoring import validate_scorecard_schema

DEFAULT_SCORECARD_PATH = "experiments/scorecard.md"

_METRIC_LABELS: list[tuple[str, str]] = [
    ("verified_completion_rate", "verified completion"),
    ("repair_success_rate", "repair success"),
    ("false_fix_rate", "false-fix rate"),
    ("unsafe_rate", "unsafe rate"),
    ("escalation_quality_avg", "escalation quality (avg)"),
    ("avg_elapsed_ms", "avg elapsed (ms)"),
    ("total_tool_calls", "tool calls (total)"),
    ("total_model_calls", "model calls (total)"),
    ("total_frontier_tokens", "frontier tokens (total)"),
    ("total_cost_usd", "est. cost USD (total)"),
    ("zero_frontier_solves", "zero-frontier solves"),
    ("zero_frontier_rate", "zero-frontier rate"),
]


def _leak_audit_line(leak_audit: Any) -> str:
    """Render the REQ-024 A1 leak-audit header line (counts only, never secrets)."""
    if not isinstance(leak_audit, dict):
        return "Leak audit: not run (owner runs scripts/heldout_leak_audit.py --write before the Nov 3 run)"
    sources = leak_audit.get("sources", {}) if isinstance(leak_audit.get("sources"), dict) else {}
    parts: list[str] = []
    for name, unit in (("transcripts", "files"), ("git-blobs", "blobs"), ("worktrees", "files"), ("project", "files")):
        entry = sources.get(name, {})
        count_key = unit if isinstance(entry, dict) and unit in entry else "files"
        hits = entry.get("hits", "?") if isinstance(entry, dict) else "?"
        total = entry.get(count_key, "?") if isinstance(entry, dict) else "?"
        parts.append(f"{hits} hits in {total} {unit}")
    verdict = "clean" if leak_audit.get("ok") else "FAIL"
    return "Leak audit: " + verdict + " — " + ", ".join(parts)


def _result_cell(arms_t: dict[str, Any], nm: str) -> str:
    """Render one per-trial arm cell (PASS/fail + verdict/regression flags)."""
    s = arms_t.get(nm)
    if s is None:
        return "—"
    flag = "PASS" if s.get("verified_completion") == 1 else "fail"
    extra = ""
    if s.get("false_fix") == 1:
        extra += " FALSE-FIX"
    if s.get("zero_frontier") == 1:
        extra += " zero-frontier"
    return f"{flag} ({s.get('verdict')}/{s.get('regression')}){extra}"


def render_scorecard_markdown(scorecard: dict[str, Any]) -> str:
    """Render *scorecard* as markdown (validates schema first)."""
    validate_scorecard_schema(scorecard)
    lines: list[str] = []
    lines.append("# AutoBridge Benchmark Scorecard")
    lines.append("")
    lines.append(f"Generated: {scorecard.get('generated_at', 'unknown')}")
    lines.append(f"Seed: {scorecard.get('seed', 'unknown')}")
    lines.append(f"Trials: {scorecard.get('matched_trials', 0)}/{scorecard.get('total_trials', 0)} matched")
    if scorecard.get("heldout"):
        lines.append(f"Heldout seeds: private (manifest {scorecard.get('heldout_manifest_sha', 'unknown')})")
    else:
        lines.append("Heldout seeds: n/a — dev run on public YAML seeds")
    lines.append(_leak_audit_line(scorecard.get("leak_audit")))
    lines.append("")
    lines.append("## Aggregate metrics (per arm)")
    lines.append("")
    arms = scorecard["arms"]
    names = sorted(arms.keys())
    lines.append("| metric | " + " | ".join(names) + " |")
    lines.append("| --- | " + " | ".join(["---"] * len(names)) + " |")
    for key, label in _METRIC_LABELS:
        vals = [str(arms[n].get(key, "—")) for n in names]
        lines.append(f"| {label} | " + " | ".join(vals) + " |")
    lines.append("| trials (n) | " + " | ".join(str(arms[n].get('n', 0)) for n in names) + " |")
    lines.append("")
    lines.append("## Per-trial results")
    lines.append("")
    lines.append("| scenario | trial | matched | winner | autobridge | baseline | one_shot | loss notes |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for t in scorecard["trials"]:
        arms_t = t.get("arms", {})
        notes = "; ".join(t.get("loss_notes", []))
        lines.append(
            f"| {t.get('scenario_id')} | {t.get('trial')} | {t.get('matched')} | "
            f"{t.get('winner')} | {_result_cell(arms_t, 'autobridge')} | {_result_cell(arms_t, 'baseline')} | {_result_cell(arms_t, 'one_shot')} | {notes} |"
        )
    lines.append("")
    lines.append("## Wins")
    lines.append("")
    for w in scorecard["wins"]:
        lines.append(f"- {w}")
    if not scorecard["wins"]:
        lines.append("- (none)")
    lines.append("")
    lines.append("## Losses")
    lines.append("")
    for loss in scorecard["losses"]:
        lines.append(f"- {loss}")
    if not scorecard["losses"]:
        lines.append("- (none — every arm verified on every trial)")
    lines.append("")
    return "\n".join(lines)


def write_scorecard(scorecard: dict[str, Any], path: str | pathlib.Path | None = None) -> pathlib.Path:
    """Validate, render and write the scorecard markdown. Returns the path."""
    out = pathlib.Path(path) if path else pathlib.Path(DEFAULT_SCORECARD_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_scorecard_markdown(scorecard) + "\n", encoding="utf-8")
    return out


def write_scorecard_json(scorecard: dict[str, Any], path: str | pathlib.Path) -> pathlib.Path:
    """Write the JSON-safe scorecard dict (Decimal already stringified)."""
    out = pathlib.Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(scorecard, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out


def load_scorecard_json(path: str | pathlib.Path) -> dict[str, Any]:
    """Load a scorecard JSON file (validates schema)."""
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"scorecard JSON must be an object: {path}")
    validate_scorecard_schema(data)
    return data


def main(argv: list[str] | None = None) -> int:
    """CLI: render ``experiments/scorecard.md`` from a scored JSON file.

    Usage: ``python -m autobridge.bench.report --input <scored.json>
    [--output experiments/scorecard.md]``.  Scoring itself (which needs the
    verifier) lives in :mod:`autobridge.bench.scoring`; this CLI only
    renders so ``make bench`` stays idempotent.
    """
    import argparse

    parser = argparse.ArgumentParser(description="Render bench scorecard markdown")
    parser.add_argument("--input", default="experiments/benchmark_runs/_scorecard.json")
    parser.add_argument("--output", default=DEFAULT_SCORECARD_PATH)
    args = parser.parse_args(argv)
    try:
        scorecard = load_scorecard_json(args.input)
    except FileNotFoundError as exc:
        print(f"scorecard input not found/invalid: {exc}", flush=True)
        return 2
    except (ValueError, AssertionError) as exc:
        print(f"scorecard schema invalid: {exc}", flush=True)
        return 2
    try:
        out = write_scorecard(scorecard, args.output)
    except AssertionError as exc:
        print(f"scorecard schema invalid: {exc}", flush=True)
        return 1
    print(f"wrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def healthz() -> dict[str, str]:
    """Health check for the report module."""
    return {"status": "ok", "service": "bench-report"}
