"""W8.5 — matched-budget benchmark end to end (P1.W8.5, plan §2.10, §4 W8).

Service confirmation: runs one dev scenario through both arms and
validates the scorecard schema.

The test:
  1. Checks the heldout SHA gate (no-op until the owner freezes heldout
     per P1.W2.8; hard fail on mismatch).
  2. Runs one dev scenario (``baseline``) × 1 trial through both arms via
     :func:`autobridge.bench.harness.run_harness` and asserts the harness
     self-test: identical docs volume, sandbox URL, credentials (refs
     only), visible tests, token ceiling and wall-clock for both arms.
  3. Scores the trial via verifier hidden mode — the live verifier over
     HTTP when the stack is up, otherwise a deterministic hidden-mode
     shaped payload — and validates the scorecard with
     :func:`autobridge.bench.scoring.validate_scorecard_schema`.
  4. Renders ``experiments/scorecard.md`` shape (wins AND losses sections)
     and asserts the seed is in the payload (reproducible from seeds).

Fake/default runners mean the suite is green without Docker; the live
verifier path is exercised when the stack is up. No shared state —
random-order safe (all artifacts go to ``tmp_path``).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pathlib

from autobridge.bench.harness import (
    HarnessConfig,
    assert_budget_fields_equal,
    assert_inputs_equal,
    assert_results_reproducible,
    check_heldout_sha,
    run_harness,
    score_and_report,
    verify_matched_trial,
    write_results,
)
from autobridge.bench.report import render_scorecard_markdown
from autobridge.bench.scoring import validate_scorecard_schema, verify_via_http

SCENARIO_ID = "baseline"
SEED = 42


def _hidden_mode_payload(verdict: str) -> dict[str, Any]:
    """Deterministic verifier hidden-mode shaped payload (no fixtures back)."""
    if verdict == "pass":
        return {"verdict": "pass", "score": 1.0, "failed_checks": [], "regression": "pass"}
    return {
        "verdict": "fail",
        "score": 0.25,
        "failed_checks": [{"name": "record_set_mismatch", "count": 3}],
        "regression": "pass",
    }


def _verify_fn(spec: Any, scenario_id: str) -> dict[str, Any]:
    """Hidden-mode verify: live verifier when reachable, else deterministic.

    Tries the real ``POST /verify`` hidden mode first (service
    confirmation against the stack); on transport error falls back to a
    deterministic payload so the schema assertion still runs offline.
    Either way no fixture rows flow back — hidden mode only.
    """
    live = verify_via_http(spec, scenario_id)
    failed = live.get("failed_checks", [])
    unreachable = any(
        isinstance(f, dict) and f.get("name") == "verifier_unreachable" for f in failed
    )
    if not unreachable:
        return live
    # Offline fallback: deterministic pass payload so the scorecard schema
    # assertion still runs without the stack (hidden-mode shape only).
    return _hidden_mode_payload("pass")


def test_07_benchmark_matched_budget(tmp_path: pathlib.Path) -> None:
    # 1. Heldout SHA gate runs before the run (no-op until frozen).
    check_heldout_sha()

    # 2. One dev scenario through both arms with matched budgets.
    cfg = HarnessConfig(seed=SEED, scenarios=(SCENARIO_ID,), trials_per_scenario=1)
    bench = run_harness(cfg)
    assert len(bench.trials) == 1
    trial = bench.trials[0]
    assert trial.matched, f"harness self-test failed: {trial.matched_error}"
    verify_matched_trial(trial)  # post-hoc audit, raises on mismatch
    assert_inputs_equal(trial.autobridge.inputs, trial.baseline.inputs)
    assert_budget_fields_equal(trial.autobridge.inputs, trial.baseline.inputs)
    budget_a = trial.autobridge.inputs.budget_fields()
    budget_b = trial.baseline.inputs.budget_fields()
    assert set(budget_a) == {
        "docs_volume",
        "docs_ref",
        "sandbox_url",
        "sandbox_image",
        "credential_refs",
        "visible_tests",
        "token_ceiling",
        "wall_clock_s",
    }
    assert budget_a == budget_b

    # Same seed re-run is reproducible (inputs + canonical bytes identical).
    bench2 = run_harness(cfg)
    assert_results_reproducible(bench, bench2)

    # 3. Score via verifier hidden mode + validate the scorecard schema.
    out = tmp_path / "runs"
    scorecard = score_and_report(
        bench,
        _verify_fn,
        output_dir=out,
        scorecard_path=tmp_path / "scorecard.md",
    )
    validate_scorecard_schema(scorecard)  # the W8.5 schema assertion
    assert scorecard["total_trials"] == 1
    assert scorecard["matched_trials"] == 1
    assert scorecard["seed"] == SEED == cfg.seed
    assert bench.trials[0].seed == bench.trials[0].inputs.seed  # derived deterministically

    # JSONL payloads carry the seed (reproducible from seeds).
    paths = write_results(bench, output_dir=out)
    assert len(paths) == 1
    payload = json.loads(paths[0].read_text(encoding="utf-8"))
    assert payload["seed"] == bench.trials[0].seed
    assert payload["matched"] is True

    # 4. Scorecard renders wins AND losses sections.
    scored_json = json.loads((out / "_scorecard.json").read_text(encoding="utf-8"))
    validate_scorecard_schema(scored_json)
    md = render_scorecard_markdown(scorecard)
    assert "## Wins" in md
    assert "## Losses" in md
    assert (tmp_path / "scorecard.md").read_text(encoding="utf-8")
