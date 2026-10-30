"""REQ-052: frontier arms make real, complete, gradable attempts.

Item 1: one-shot gets the engine L3's output room (FRONTIER_MAX_TOKENS);
truncation (finish_reason "length", or a full cap with no parseable spec)
is labelled "truncated", never silently scored as a model failure.
Item 2: the agent arm records the model the run itself reports, fails a
live run whose model differs from FRONTIER_MODEL, runs under the shared
wall-clock, and labels timeouts "timed_out".
Item 3: every frontier attempt is graded (produced/applied/verdict/failed
checks) and the summary counts fixable/unfixable/labels per arm.
Item 4: with a key, the learning curve uses the live agent (refusing the
fake driver) and reports per-repeat verification.
Item 5: one-shot cost uses the published Muse prices, not the $15/M flat.
"""

from __future__ import annotations

import subprocess
from decimal import Decimal
from typing import Any

import pytest

from autobridge.config import DEFAULT_FRONTIER_MAX_TOKENS
from autobridge.llm.provider import Completion, FakeProvider, Usage


def _inputs(scenario_id: str = "page_to_cursor", trial: int = 0) -> Any:
    from bench.harness import HarnessConfig, make_trial_inputs

    return make_trial_inputs(HarnessConfig(), scenario_id, trial)


# ---------------------------------------------------------------------------
# Item 1: output room + truncation
# ---------------------------------------------------------------------------


def test_one_shot_default_output_room_is_frontier_cap() -> None:
    from bench.one_shot import OneShotGenerator

    gen = OneShotGenerator(provider=FakeProvider())
    assert gen.max_tokens == DEFAULT_FRONTIER_MAX_TOKENS
    assert gen.max_tokens > 4096


def test_one_shot_explicit_max_tokens_wins() -> None:
    from bench.one_shot import OneShotGenerator

    gen = OneShotGenerator(provider=FakeProvider(), max_tokens=100)
    assert gen.max_tokens == 100


def test_one_shot_truncation_labelled_not_scored() -> None:
    from bench.one_shot import run_one_shot_arm

    long_text = "x" * 5000  # no YAML mapping inside
    prov = FakeProvider(
        responses={
            "frontier": Completion(
                text=long_text,
                usage=Usage(input_tokens=10, output_tokens=100),
                finish_reason="length",
            )
        }
    )
    res = run_one_shot_arm(_inputs(), provider=prov)
    assert res.ran is False
    assert res.outcome == "truncated"
    assert res.verdict is None
    assert "length" in (res.error or "")


def test_one_shot_full_cap_without_spec_is_truncated() -> None:
    from bench.one_shot import OneShotGenerator

    prov = FakeProvider(
        responses={
            "frontier": Completion(
                text="prose without any yaml mapping",
                usage=Usage(input_tokens=10, output_tokens=50),
            )
        }
    )
    gen = OneShotGenerator(provider=prov, max_tokens=50)
    out = gen.generate(_inputs())
    assert out.truncated is True
    assert "no parseable spec" in out.truncation_reason


def test_one_shot_parseable_spec_is_not_truncated() -> None:
    from bench.one_shot import run_one_shot_arm

    prov = FakeProvider(
        responses={
            "frontier": Completion(
                text="```yaml\nspec_version: 1\n```",
                usage=Usage(input_tokens=10, output_tokens=20),
                finish_reason="stop",
            )
        }
    )
    res = run_one_shot_arm(_inputs(), provider=prov)
    assert res.ran is True
    assert res.outcome is None
    assert res.spec == {"spec_version": 1}


def test_one_shot_garbage_is_no_output_but_ran() -> None:
    from bench.one_shot import run_one_shot_arm

    prov = FakeProvider(
        responses={
            "frontier": Completion(
                text="just some prose, no spec",
                usage=Usage(input_tokens=10, output_tokens=20),
                finish_reason="stop",
            )
        }
    )
    res = run_one_shot_arm(_inputs(), provider=prov)
    assert res.ran is True
    assert res.spec is None
    assert res.outcome == "no_output"


# ---------------------------------------------------------------------------
# Item 5: real Muse prices for the one-shot
# ---------------------------------------------------------------------------


def test_one_shot_cost_uses_published_spark_prices() -> None:
    from bench.one_shot import run_one_shot_arm

    prov = FakeProvider(
        frontier_model="muse-spark-1.3",
        responses={
            "frontier": Completion(
                text="```yaml\nspec_version: 1\n```",
                usage=Usage(input_tokens=1_000, output_tokens=1_000),
            )
        },
    )
    res = run_one_shot_arm(_inputs(), provider=prov)
    # Spark: $1.25/M in + $4.25/M out — not the $15/M flat placeholder
    # ($15 in + $15 out = $0.03 for 2k tokens).
    assert res.cost_usd == Decimal("0.005500")
    assert res.cost_usd != Decimal("0.030000")


# ---------------------------------------------------------------------------
# Item 2: agent model record/check, wall-clock, timed_out
# ---------------------------------------------------------------------------


def test_agent_records_driver_model_not_default_label() -> None:
    from bench.baseline_coding_agent import FakeOpenCodeDriver
    from bench.harness import run_baseline_arm

    driver = FakeOpenCodeDriver(default_model="fake-opencode")
    res = run_baseline_arm(_inputs(), driver=driver)
    assert res.model == "fake-opencode"
    assert res.model != "baseline-claude"


def test_agent_uses_result_model_first() -> None:
    from bench.baseline_coding_agent import ClaudeCliResult, FakeClaudeDriver
    from bench.harness import run_baseline_arm

    canned = ClaudeCliResult(text="spec", usage=Usage(1, 1), model="reported-model")
    driver = FakeClaudeDriver(responses={"page_to_cursor": canned}, default_model="driver-model")
    res = run_baseline_arm(_inputs(), driver=driver)
    assert res.model == "reported-model"


def test_agent_timeout_labelled_timed_out() -> None:
    from bench.baseline_coding_agent import OpenCodeCliDriver
    from bench.harness import run_baseline_arm

    class SlowDriver(OpenCodeCliDriver):
        def run(self, _prompt: str, **_kwargs: Any) -> Any:
            raise subprocess.TimeoutExpired(cmd="opencode", timeout=1)

    res = run_baseline_arm(_inputs(), driver=SlowDriver(cli_path="opencode"))
    assert res.ran is False
    assert res.outcome == "timed_out"


def test_agent_wall_clock_reaches_driver() -> None:
    from bench.harness import HarnessConfig, make_trial_inputs, run_baseline_arm

    seen: dict[str, Any] = {}

    class ProbeDriver:
        default_model = "probe-model"

        def run(self, _prompt: str, **kwargs: Any) -> Any:
            from bench.baseline_coding_agent import ClaudeCliResult

            seen.update(kwargs)
            return ClaudeCliResult(text="ok", usage=Usage(1, 1), model="probe-model")

    cfg = HarnessConfig(wall_clock_s=321.0)
    res = run_baseline_arm(make_trial_inputs(cfg, "page_to_cursor", 0), driver=ProbeDriver())
    assert res.ran is True
    assert seen.get("timeout_s") == 321.0


def test_frontier_agent_live_model_mismatch_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    from bench.ablation import run_frontier_agent_arm
    from bench.baseline_coding_agent import FakeOpenCodeDriver

    monkeypatch.setenv("FRONTIER_API_KEY", "test-key")
    monkeypatch.setenv("FRONTIER_MODEL", "muse-spark-1.3")
    driver = FakeOpenCodeDriver(default_model="some-other-model")
    res = run_frontier_agent_arm(_inputs(), driver=driver)
    assert res.ran is False
    assert res.outcome == "model_mismatch"
    assert "FRONTIER_MODEL" in (res.error or "")


def test_frontier_agent_live_model_match_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    from bench.ablation import run_frontier_agent_arm
    from bench.baseline_coding_agent import FakeOpenCodeDriver

    monkeypatch.setenv("FRONTIER_API_KEY", "test-key")
    monkeypatch.setenv("FRONTIER_MODEL", "muse-spark-1.3")
    driver = FakeOpenCodeDriver(default_model="muse-spark-1.3", default_text="no yaml here")
    res = run_frontier_agent_arm(_inputs(), driver=driver)
    assert res.ran is True
    assert res.model == "muse-spark-1.3"
    assert res.outcome in (None, "no_output")


def test_frontier_agent_offline_fake_skips_check(monkeypatch: pytest.MonkeyPatch) -> None:
    from bench.ablation import run_frontier_agent_arm
    from bench.baseline_coding_agent import FakeOpenCodeDriver

    monkeypatch.delenv("FRONTIER_API_KEY", raising=False)
    driver = FakeOpenCodeDriver(default_model="fake-opencode")
    res = run_frontier_agent_arm(_inputs(), driver=driver)
    assert res.ran is True
    assert res.outcome in (None, "no_output")


# ---------------------------------------------------------------------------
# Item 3: grading frontier attempts + summary counts
# ---------------------------------------------------------------------------


def _trial_with_frontier(spec: dict[str, Any] | None, *, outcome: str | None = None) -> Any:
    from bench.ablation import ABLATION_ARMS, AblationTrialResult
    from bench.harness import ArmResult

    inputs = _inputs()
    results: dict[str, Any] = {}
    for arm in ABLATION_ARMS:
        results[arm] = ArmResult(
            arm=arm,  # type: ignore[arg-type]
            inputs=inputs,
            ran=spec is not None,
            spec=spec if arm.startswith("frontier") else None,
            error=None if spec is not None else "boom",
            outcome=outcome,
        )
    return AblationTrialResult(
        scenario_id="page_to_cursor", trial=0, seed=inputs.seed, inputs=inputs, results=results
    )


def test_frontier_full_spec_verified_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    from bench import scoring
    from bench.ablation import grade_ablation_trial

    monkeypatch.setattr(
        scoring,
        "verify_via_http",
        lambda _spec, _scenario_id, **_kw: {"verdict": "pass", "score": 1.0, "failed_checks": []},
    )
    trial = _trial_with_frontier({"spec_version": 1, "operations": {}, "entities": {}})
    grades = grade_ablation_trial(trial, verifier_url="http://verifier:8000")
    by_arm = {g.arm: g for g in grades}
    assert by_arm["frontier_oneshot"].verdict == "pass"
    assert by_arm["frontier_oneshot"].verified_completion == 1
    assert by_arm["frontier_oneshot"].produced == 1
    assert by_arm["frontier_oneshot"].outcome_label is None
    # Written back onto the trial row: verdict, not a null mystery.
    assert trial.results["frontier_oneshot"].verdict == "pass"


def test_frontier_full_spec_verified_fail_names_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    from bench import scoring
    from bench.ablation import grade_ablation_trial

    monkeypatch.setattr(
        scoring,
        "verify_via_http",
        lambda _spec, _scenario_id, **_kw: {
            "verdict": "fail",
            "score": 0.0,
            "failed_checks": [{"name": "money_invariant", "count": 1}],
        },
    )
    trial = _trial_with_frontier({"spec_version": 1, "operations": {}, "entities": {}})
    grades = grade_ablation_trial(trial, verifier_url="http://verifier:8000")
    by_arm = {g.arm: g for g in grades}
    assert by_arm["frontier_agent"].verdict == "fail"
    assert by_arm["frontier_agent"].failed_checks == ("money_invariant",)
    assert by_arm["frontier_agent"].verified_completion == 0
    assert trial.results["frontier_agent"].failed_checks == [{"name": "money_invariant"}]


def test_frontier_truncated_row_keeps_label() -> None:
    from bench.ablation import grade_ablation_trial

    trial = _trial_with_frontier(None, outcome="truncated")
    grades = grade_ablation_trial(trial, verifier_url=None)
    by_arm = {g.arm: g for g in grades}
    assert by_arm["frontier_oneshot"].verdict is None
    assert by_arm["frontier_oneshot"].outcome_label == "truncated"
    assert by_arm["frontier_oneshot"].produced == 0
    assert trial.results["frontier_oneshot"].outcome == "truncated"


def test_unfixable_holdback_counts_produced_spec_as_attempt() -> None:
    import dataclasses as _dc

    from bench.ablation import AblationTrialResult, grade_ablation_arm

    trial = _trial_with_frontier({"spec_version": 1})
    # rate_limit_429 expects no_patch: producing a spec is not holding back.
    inputs = _inputs("rate_limit_429", 0)
    rebuilt = AblationTrialResult(
        scenario_id="rate_limit_429",
        trial=0,
        seed=inputs.seed,
        inputs=inputs,
        # Re-point arm rows at the new inputs (frozen ArmResult: replace).
        results={k: _dc.replace(v, inputs=inputs) for k, v in trial.results.items()},
    )
    grade = grade_ablation_arm("frontier_oneshot", rebuilt, None, expected="no_patch")
    assert grade.correct_escalation == 0
    grade_empty = grade_ablation_arm(
        "frontier_agent", rebuilt, None, expected="no_patch", produced=0
    )
    assert grade_empty.correct_escalation == 1


def test_summary_quality_counts_and_table() -> None:
    from bench.ablation import (
        AblationGrade,
        render_frontier_quality_table,
        summarize_ablation,
    )

    def g(arm: str, sid: str, verified: int, esc: int, label: str | None) -> AblationGrade:
        return AblationGrade(
            arm=arm,
            scenario_id=sid,
            trial=0,
            verified_completion=verified,
            wrong_patch_released=0,
            correct_escalation=esc,
            frontier_tokens=10,
            small_tokens=0,
            cost_usd=None,
            elapsed_ms=1.0,
            produced=1 if label != "truncated" else 0,
            applied=1 if verified else 0,
            verdict="pass" if verified else ("fail" if label is None else None),
            outcome_label=label,
        )

    grades = [
        g("frontier_oneshot", "page_to_cursor", 1, 1, None),
        g("frontier_oneshot", "rate_limit_429", 0, 0, None),
        g("frontier_agent", "page_to_cursor", 0, 0, "timed_out"),
        g("frontier_agent", "rate_limit_429", 0, 1, None),
    ]
    summary = summarize_ablation(grades)
    quality = summary["quality"]
    assert quality["frontier_oneshot"]["fixable_fixed"] == 1
    assert quality["frontier_oneshot"]["fixable_total"] == 1
    assert quality["frontier_agent"]["timed_out"] == 1
    assert quality["frontier_agent"]["unfixable_held_back"] == 1
    table = render_frontier_quality_table(summary)
    assert "frontier_oneshot" in table and "frontier_agent" in table


# ---------------------------------------------------------------------------
# Item 4: learning curve — live agent, fake refusal, verified column
# ---------------------------------------------------------------------------


def test_learning_curve_refuses_fake_driver_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from bench.ablation import run_learning_curve
    from bench.baseline_coding_agent import FakeOpenCodeDriver

    monkeypatch.setenv("FRONTIER_API_KEY", "test-key")
    with pytest.raises(RuntimeError, match="live agent driver"):
        run_learning_curve(
            "page_to_cursor",
            repeats=1,
            provider=FakeProvider(),
            driver=FakeOpenCodeDriver(),
        )


def test_learning_curve_reports_agent_verified() -> None:
    from bench.ablation import run_learning_curve

    result = run_learning_curve(
        "page_to_cursor",
        repeats=2,
        provider=FakeProvider(),
        verify=lambda _spec, _sid: True,
    )
    assert len(result["rows"]) == 2
    # Fake driver produces no parseable spec → unverified (honest, not True).
    assert [r["frontier_agent_verified"] for r in result["rows"]] == ["unverified", "unverified"]
