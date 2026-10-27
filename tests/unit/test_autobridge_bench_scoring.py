"""Scoring + report tests (P1.W8.4, plan §2.10).

Scoring goes through an injected ``verify_fn`` (hidden-mode payloads);
no verifier/simulator imports here (trust boundary).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from autobridge.bench.harness import ArmResult, HarnessConfig, TrialResult, make_trial_inputs
from autobridge.bench.report import (
    load_scorecard_json,
    render_scorecard_markdown,
    write_scorecard,
    write_scorecard_json,
)
from autobridge.bench.scoring import (
    REQUIRED_ARM_METRICS,
    VerifyOutcome,
    aggregate_scores,
    normalize_verify_response,
    score_arm,
    score_bench,
    score_escalation_quality,
    score_trial,
    validate_scorecard_schema,
    verify_via_http,
)


def _inp(scenario: str = "baseline", trial: int = 0):
    cfg = HarnessConfig()
    return make_trial_inputs(cfg, scenario, trial)


def _arm(arm: str, success: bool = True, **kw: Any) -> ArmResult:
    return ArmResult(arm=arm, inputs=_inp(), success=success, **kw)  # type: ignore[arg-type]


def _pass_payload(**kw: Any) -> dict[str, Any]:
    d: dict[str, Any] = {"verdict": "pass", "score": 1.0, "failed_checks": [], "regression": "pass"}
    d.update(kw)
    return d


def _fail_payload(**kw: Any) -> dict[str, Any]:
    d: dict[str, Any] = {"verdict": "fail", "score": 0.25, "failed_checks": [{"name": "record_set_mismatch", "count": 3}], "regression": "pass"}
    d.update(kw)
    return d


# --- normalize_verify_response ---


def test_normalize_pass() -> None:
    oc = normalize_verify_response(_pass_payload())
    assert oc.passed and not oc.regression_failed and oc.score == 1.0


def test_normalize_unknown_verdict_becomes_fail() -> None:
    oc = normalize_verify_response({"verdict": "maybe", "score": 0.9})
    assert oc.verdict == "fail" and not oc.passed


def test_normalize_non_dict() -> None:
    oc = normalize_verify_response("nope")  # type: ignore[arg-type]
    assert oc.verdict == "fail"


def test_normalize_bad_score_and_checks() -> None:
    oc = normalize_verify_response({"verdict": "pass", "score": "bad", "failed_checks": ["oops", {"name": "x"}]})
    assert oc.passed and oc.score == 1.0
    assert oc.failed_checks[0] == {"name": "oops", "count": 0}


def test_normalize_unknown_regression() -> None:
    oc = normalize_verify_response({"verdict": "pass", "regression": "maybe"})
    assert oc.regression == "fail"
    oc2 = normalize_verify_response({"verdict": "fail", "regression": "maybe"})
    assert oc2.regression == "pass"


def test_normalize_regression_failures_list() -> None:
    oc = normalize_verify_response(_pass_payload(regression="fail", regression_failures=[{"scenario_id": "baseline"}]))
    assert oc.regression_failed and oc.regression_failures == [{"scenario_id": "baseline"}]


# --- verify_via_http failure path (no network) ---


def test_verify_via_http_unreachable_returns_fail() -> None:
    out = verify_via_http(None, "baseline", verifier_url="http://127.0.0.1:1", timeout_s=1.0)
    assert out["verdict"] == "fail"


# --- escalation quality ---


def test_escalation_none_when_verified() -> None:
    assert score_escalation_quality(True, None) is None
    assert score_escalation_quality(True, {"reason": "x", "question": "What?"}) is None


def test_escalation_valid_bundle() -> None:
    assert score_escalation_quality(False, {"reason": "CAPABILITY_REMOVED", "question": "Which endpoint replaces it?"}) == 1.0


def test_escalation_missing_or_invalid() -> None:
    assert score_escalation_quality(False, None) == 0.0
    assert score_escalation_quality(False, "bundle") == 0.0  # type: ignore[arg-type]
    assert score_escalation_quality(False, {"reason": "", "question": "What?"}) == 0.0
    assert score_escalation_quality(False, {"reason": "r", "question": "no question mark"}) == 0.0
    assert score_escalation_quality(False, {"reason": "r", "question": "One? Two?"}) == 0.0


# --- score_arm ---


def test_score_arm_verified_zero_frontier() -> None:
    s = score_arm(arm="autobridge", scenario_id="baseline", trial=0, success=True, frontier_tokens=0, cost_usd=Decimal("0.001"), outcome=VerifyOutcome(verdict="pass", score=1.0))
    assert (s.verified_completion, s.repair_success, s.zero_frontier) == (1, 1, 1)
    assert s.escalation_quality is None
    assert s.false_fix == 0 and s.unsafe == 0
    d = s.to_dict()
    assert d["cost_usd"] == "0.001" and d["verdict"] == "pass"


def test_score_arm_failed_run_needs_escalation() -> None:
    s = score_arm(arm="baseline", scenario_id="s", trial=0, success=False, outcome=VerifyOutcome(verdict="fail", score=0.0), escalation_bundle=None)
    assert s.verified_completion == 0 and s.escalation_quality == 0.0


def test_score_arm_false_fix_and_unsafe() -> None:
    oc = VerifyOutcome(verdict="pass", score=1.0, regression="fail")
    s = score_arm(arm="baseline", scenario_id="s", trial=0, success=True, frontier_tokens=5, outcome=oc)
    assert s.false_fix == 1 and s.unsafe == 1 and s.repair_success == 0 and s.zero_frontier == 0


def test_score_arm_unsafe_trap_check() -> None:
    oc = VerifyOutcome(verdict="fail", score=0.5, failed_checks=[{"name": "invalid_patch_trap", "count": 1}])
    s = score_arm(arm="autobridge", scenario_id="s", trial=0, success=True, outcome=oc)
    assert s.unsafe == 1 and s.false_fix == 0


def test_score_arm_none_outcome_and_bad_cost() -> None:
    s = score_arm(arm="x", scenario_id="s", trial=0, success=True, cost_usd="not-a-number", outcome=None)
    assert s.verified_completion == 0 and s.cost_usd == Decimal("0")
    s2 = score_arm(arm="x", scenario_id="s", trial=0, success=True, cost_usd=None, outcome=VerifyOutcome())
    assert s2.cost_usd == Decimal("0")


# --- score_trial ---


def _trial(ab_ok: bool = True, bl_ok: bool = False) -> TrialResult:
    inp = _inp()
    return TrialResult(
        scenario_id="baseline", trial=0, seed=inp.seed, inputs=inp,
        autobridge=_arm("autobridge", success=ab_ok),
        baseline=_arm("baseline", success=bl_ok),
        matched=True,
    )


def test_score_trial_winner_and_losses() -> None:
    def fake_verify(_spec: Any, scenario_id: str) -> dict[str, Any]:
        return _pass_payload() if scenario_id == "baseline" else _fail_payload()
    # autobridge success=True + pass; baseline success=False → fail
    scored = score_trial(_trial(True, False), fake_verify)
    assert scored["winner"] == "autobridge"
    assert scored["arms"]["autobridge"]["verified_completion"] == 1
    assert any("baseline" in n for n in scored["loss_notes"])


def test_score_trial_false_fix_loss_note_and_bundles() -> None:
    def fake_verify(_spec: Any, _scenario_id: str) -> dict[str, Any]:
        return _pass_payload(regression="fail", regression_failures=[{"scenario_id": "baseline"}])
    inp = _inp()
    trial = TrialResult(
        scenario_id="baseline", trial=0, seed=inp.seed, inputs=inp,
        autobridge=_arm("autobridge", success=True), baseline=_arm("baseline", success=True), matched=True,
    )
    scored = score_trial(trial, fake_verify, escalation_bundles={"autobridge": {"reason": "r", "question": "Which mapping is correct?"}})
    assert scored["arms"]["autobridge"]["false_fix"] == 1
    assert any("FALSE FIX" in n for n in scored["loss_notes"])


def test_score_trial_unmatched_and_verify_raises() -> None:
    def boom(_spec: Any, _scenario_id: str) -> dict[str, Any]:
        raise RuntimeError("verifier down")
    inp = _inp()
    trial = TrialResult(
        scenario_id="baseline", trial=0, seed=inp.seed, inputs=inp,
        autobridge=_arm("autobridge", success=True), baseline=_arm("baseline", success=True),
        matched=False, matched_error="mismatch",
    )
    scored = score_trial(trial, boom)
    assert scored["arms"]["autobridge"]["verified_completion"] == 0
    assert any("not matched" in n for n in scored["loss_notes"])


def test_score_trial_dict_input_with_one_shot_and_tie() -> None:
    trial_dict: dict[str, Any] = {
        "scenario_id": "baseline", "trial": 1, "matched": True,
        "autobridge": {"arm": "autobridge", "success": False, "elapsed_ms": 5, "tool_calls": 0, "model_calls": 0, "frontier_tokens": 0, "cost_usd": None, "spec": "not-a-dict"},
        "baseline": {"arm": "baseline", "success": False, "elapsed_ms": 5, "tool_calls": 0, "model_calls": 0, "frontier_tokens": 0, "cost_usd": None, "spec": None},
        "one_shot": {"arm": "one_shot", "success": False, "elapsed_ms": 5, "tool_calls": 0, "model_calls": 1, "frontier_tokens": 0, "cost_usd": None, "spec": None},
    }
    scored = score_trial(trial_dict, lambda _spec, _sid: _fail_payload())
    assert scored["winner"] == "tie-none-verified"
    assert set(scored["arms"]) == {"autobridge", "baseline", "one_shot"}


def test_score_trial_tie_verified() -> None:
    trial_dict: dict[str, Any] = {
        "scenario_id": "baseline", "trial": 0, "matched": True,
        "autobridge": {"arm": "autobridge", "success": True, "elapsed_ms": 5, "tool_calls": 0, "model_calls": 0, "frontier_tokens": 0, "cost_usd": None, "spec": None},
        "baseline": {"arm": "baseline", "success": True, "elapsed_ms": 5, "tool_calls": 0, "model_calls": 0, "frontier_tokens": 0, "cost_usd": None, "spec": None},
    }
    scored = score_trial(trial_dict, lambda _spec, _sid: _pass_payload())
    assert scored["winner"].startswith("tie-")


def test_score_trial_empty_arms() -> None:
    scored = score_trial({"scenario_id": "s", "trial": 0, "matched": True, "autobridge": None, "baseline": None}, lambda _spec, _sid: _pass_payload())
    assert scored["winner"] == "none"


# --- aggregate + score_bench ---


def _scored_trial(sid: str, trial: int, ab_pass: bool, bl_pass: bool) -> dict[str, Any]:
    def fake_verify(_spec: Any, _scenario: str) -> dict[str, Any]:
        return _pass_payload()
    inp = _inp(sid, trial)
    t = TrialResult(
        scenario_id=sid, trial=trial, seed=inp.seed, inputs=inp,
        autobridge=_arm("autobridge", success=True), baseline=_arm("baseline", success=True), matched=True,
    )
    # Score then force per-arm outcomes by patching via outcome override:
    _ = fake_verify
    scored = score_trial(t, lambda _spec, _s: _pass_payload() if (ab_pass or bl_pass) else _fail_payload())
    if not ab_pass:
        scored["arms"]["autobridge"] = score_arm(arm="autobridge", scenario_id=sid, trial=trial, success=True, outcome=VerifyOutcome(verdict="fail"), escalation_bundle={"reason": "r", "question": "Which fix is right?"}).to_dict()
    if not bl_pass:
        scored["arms"]["baseline"] = score_arm(arm="baseline", scenario_id=sid, trial=trial, success=False, outcome=VerifyOutcome(verdict="fail")).to_dict()
    # Recompute winner/loss notes consistently
    from autobridge.bench.scoring import _pick_winner  # noqa: PLC0415

    scored["winner"] = _pick_winner(scored["arms"])
    return scored


def test_aggregate_all_required_metrics() -> None:
    trials = [_scored_trial("baseline", 0, True, False), _scored_trial("baseline", 1, False, False)]
    agg = aggregate_scores(trials)
    for arm_metrics in agg["arms"].values():
        for metric in REQUIRED_ARM_METRICS:
            assert metric in arm_metrics
    assert agg["arms"]["autobridge"]["n"] == 2
    assert agg["arms"]["autobridge"]["verified_completion_rate"] == 0.5
    assert len(agg["wins"]) == 2 and len(agg["losses"]) >= 1


def test_score_bench_end_to_end_with_objects() -> None:
    from autobridge.bench.harness import BenchResult, HarnessConfig  # noqa: PLC0415

    cfg = HarnessConfig(seed=7)
    inp = make_trial_inputs(cfg, "baseline", 0)
    bench = BenchResult(config=cfg)
    bench.trials.append(TrialResult(
        scenario_id="baseline", trial=0, seed=inp.seed, inputs=inp,
        autobridge=ArmResult(arm="autobridge", inputs=inp, success=True, frontier_tokens=0, cost_usd=Decimal("0.002"), tool_calls=2, model_calls=3),
        baseline=ArmResult(arm="baseline", inputs=inp, success=True, frontier_tokens=100, cost_usd=Decimal("0.01"), tool_calls=0, model_calls=1),
        matched=True,
    ))
    card = score_bench(bench, lambda _spec, _sid: _pass_payload(), seed=None)
    validate_scorecard_schema(card)
    assert card["seed"] == 7 and card["total_trials"] == 1 and card["matched_trials"] == 1
    assert card["arms"]["autobridge"]["zero_frontier_solves"] == 1
    assert card["arms"]["baseline"]["total_model_calls"] == 1


def test_score_bench_dict_input_and_bundles() -> None:
    bench_dict: dict[str, Any] = {
        "config": {"seed": 42},
        "trials": [{
            "scenario_id": "baseline", "trial": 0, "matched": True,
            "autobridge": {"arm": "autobridge", "success": True, "elapsed_ms": 1, "tool_calls": 0, "model_calls": 0, "frontier_tokens": 0, "cost_usd": None, "spec": None},
            "baseline": {"arm": "baseline", "success": True, "elapsed_ms": 1, "tool_calls": 0, "model_calls": 0, "frontier_tokens": 0, "cost_usd": None, "spec": None},
        }],
    }
    card = score_bench(bench_dict, lambda _spec, _sid: _fail_payload(), escalation_bundles={"baseline:0": {"autobridge": {"reason": "r", "question": "What is correct?"}}})
    assert card["arms"]["autobridge"]["escalation_quality_avg"] == 1.0


# --- validate_scorecard_schema failures ---


def test_validate_schema_missing_key() -> None:
    with pytest.raises(AssertionError):
        validate_scorecard_schema({"arms": {}})
    with pytest.raises(AssertionError):
        validate_scorecard_schema("nope")  # type: ignore[arg-type]


def test_validate_schema_empty_arms_and_bad_trials() -> None:
    base = score_bench({"config": {"seed": 1}, "trials": []}, lambda _spec, _sid: _pass_payload())
    bad = dict(base)
    bad["arms"] = {}
    with pytest.raises(AssertionError):
        validate_scorecard_schema(bad)
    bad2 = dict(base)
    bad2["trials"] = "not-a-list"  # type: ignore[dict-item]
    with pytest.raises(AssertionError):
        validate_scorecard_schema(bad2)
    bad3 = dict(base)
    bad3["wins"] = "nope"  # type: ignore[dict-item]
    with pytest.raises(AssertionError):
        validate_scorecard_schema(bad3)


def test_validate_schema_arm_missing_metric_and_trial_missing_key() -> None:
    base = score_bench({"config": {"seed": 1}, "trials": [{
        "scenario_id": "s", "trial": 0, "matched": True,
        "autobridge": {"arm": "autobridge", "success": True, "spec": None},
        "baseline": {"arm": "baseline", "success": True, "spec": None},
    }]}, lambda _spec, _sid: _pass_payload())
    bad = {**base, "arms": {"autobridge": {"n": 1}}}
    with pytest.raises(AssertionError):
        validate_scorecard_schema(bad)
    bad_t = {**base, "trials": [{"scenario_id": "s"}]}
    with pytest.raises(AssertionError):
        validate_scorecard_schema(bad_t)


# --- report ---


def _scorecard() -> dict[str, Any]:
    bench_dict: dict[str, Any] = {
        "config": {"seed": 11},
        "trials": [
            {"scenario_id": "baseline", "trial": 0, "matched": True,
             "autobridge": {"arm": "autobridge", "success": True, "elapsed_ms": 3, "tool_calls": 1, "model_calls": 2, "frontier_tokens": 0, "cost_usd": "0.001", "spec": None},
             "baseline": {"arm": "baseline", "success": False, "elapsed_ms": 9, "tool_calls": 0, "model_calls": 1, "frontier_tokens": 50, "cost_usd": "0.005", "spec": None}},
        ],
    }
    return score_bench(bench_dict, lambda _spec, sid: _pass_payload() if sid == "baseline" else _fail_payload())


def test_render_markdown_has_wins_and_losses() -> None:
    md = render_scorecard_markdown(_scorecard())
    assert "# AutoBridge Benchmark Scorecard" in md
    assert "## Wins" in md and "## Losses" in md
    assert "PASS" in md and "baseline" in md


def test_render_markdown_no_losses_case() -> None:
    bench_dict: dict[str, Any] = {
        "config": {"seed": 1},
        "trials": [{"scenario_id": "s", "trial": 0, "matched": True,
                    "autobridge": {"arm": "autobridge", "success": True, "spec": None},
                    "baseline": {"arm": "baseline", "success": True, "spec": None}}],
    }
    card = score_bench(bench_dict, lambda _spec, _sid: _pass_payload())
    md = render_scorecard_markdown(card)
    assert "(none — every arm verified on every trial)" in md


def test_render_markdown_empty_wins() -> None:
    card = _scorecard()
    card["wins"] = []
    md = render_scorecard_markdown(card)
    assert "- (none)" in md


def test_write_and_load_roundtrip(tmp_path: Any) -> None:
    card = _scorecard()
    md_path = write_scorecard(card, tmp_path / "scorecard.md")
    assert md_path.is_file() and "Losses" in md_path.read_text(encoding="utf-8")
    json_path = write_scorecard_json(card, tmp_path / "card.json")
    loaded = load_scorecard_json(json_path)
    assert loaded["total_trials"] == 1
    with pytest.raises(ValueError):
        load_scorecard_json(tmp_path / "scorecard.md")


def test_load_invalid_json_shape(tmp_path: Any) -> None:
    p = tmp_path / "bad.json"
    p.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError):
        load_scorecard_json(p)


def test_report_main_missing_input(tmp_path: Any) -> None:
    from autobridge.bench.report import main  # noqa: PLC0415

    rc = main(["--input", str(tmp_path / "nope.json"), "--output", str(tmp_path / "out.md")])
    assert rc == 2


def test_report_main_invalid_schema(tmp_path: Any) -> None:
    from autobridge.bench.report import main  # noqa: PLC0415

    bad = tmp_path / "bad.json"
    bad.write_text('{"arms": {}, "trials": []}', encoding="utf-8")
    rc = main(["--input", str(bad), "--output", str(tmp_path / "out.md")])
    assert rc == 2  # load fails schema → treated as invalid input


def test_report_main_ok(tmp_path: Any) -> None:
    from autobridge.bench.report import main  # noqa: PLC0415

    card = _scorecard()
    inp = tmp_path / "card.json"
    write_scorecard_json(card, inp)
    rc = main(["--input", str(inp), "--output", str(tmp_path / "scorecard.md")])
    assert rc == 0
    assert (tmp_path / "scorecard.md").is_file()


def test_report_main_schema_invalid_but_loadable(tmp_path: Any) -> None:
    from autobridge.bench.report import main  # noqa: PLC0415

    # Valid JSON dict that fails validate on render path: craft file that
    # passes load (full schema) then corrupt? Instead: file with valid JSON
    # but missing trials key content → load raises ValueError → rc 2.
    p = tmp_path / "partial.json"
    p.write_text('{"arms": {"a": {}}, "trials": [], "wins": [], "losses": [], "total_trials": 0, "matched_trials": 0}', encoding="utf-8")
    rc = main(["--input", str(p), "--output", str(tmp_path / "o.md")])
    assert rc in (1, 2)


# --- harness new fields backward compat ---


def test_arm_result_new_fields_defaults() -> None:
    arm = _arm("autobridge")
    assert arm.tool_calls == 0 and arm.model_calls == 0
    d = arm.to_dict()
    assert d["tool_calls"] == 0 and d["model_calls"] == 0


# --- healthz ---


def test_scoring_and_report_healthz() -> None:
    from autobridge.bench import report as rp  # noqa: PLC0415
    from autobridge.bench import scoring as sc  # noqa: PLC0415

    assert sc.healthz()["status"] == "ok"
    assert rp.healthz()["status"] == "ok"
