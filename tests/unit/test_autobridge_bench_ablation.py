"""Tests for REQ-024 B ablation arms (engine vs. untrained frontier model)."""

from __future__ import annotations

from typing import Any

import pytest

from autobridge.bench.ablation import (
    ABLATION_ARMS,
    ENGINE_ARMS,
    FRONTIER_ARMS,
    AblationGrade,
    AblationTrialResult,
    ablation_case_for_scenario,
    ablation_provider,
    ablation_scenarios,
    assert_all_inputs_equal,
    grade_ablation_arm,
    run_ablation_trial,
    run_engine_arm,
    run_frontier_agent_arm,
    run_frontier_oneshot_arm,
    run_learning_curve,
    summarize_ablation,
    warm_knowledge,
    write_learning_curve_md,
)
from autobridge.bench.baseline_coding_agent import FakeOpenCodeDriver
from autobridge.bench.harness import HarnessConfig, make_trial_inputs
from autobridge.llm.provider import FakeProvider


def test_arm_constants() -> None:
    assert ABLATION_ARMS == (
        "frontier_oneshot",
        "frontier_agent",
        "engine_no_knowledge",
        "autobridge_cold",
        "autobridge_warm",
    )
    assert set(FRONTIER_ARMS) == {"frontier_oneshot", "frontier_agent"}
    assert set(ENGINE_ARMS) == {"engine_no_knowledge", "autobridge_cold", "autobridge_warm"}
    assert set(ablation_scenarios()) == {
        "offset_to_cursor",
        "rename_guest_id",
        "cursor_repeat_token",
        "page_to_cursor",
        "rate_limit_429",
        "transient_503",
        "token_expired",
        "gross_to_net",
        "status_vocab_change",
        "duplicate_late_records",
        "meridian_cursor_renamed",
        "meridian_state_renamed",
    }


def test_case_library_covers_dev_http_scenarios() -> None:
    for sid in ablation_scenarios():
        case = ablation_case_for_scenario(sid)
        assert case.scenario_id == sid
        assert case.spec, sid
        assert case.drift_class, sid
        assert case.extra is not None, sid


def test_case_unknown_scenario() -> None:
    with pytest.raises(KeyError):
        ablation_case_for_scenario("no_such_scenario")


def test_case_skips_non_http(monkeypatch: pytest.MonkeyPatch) -> None:
    import yaml as _yaml

    import autobridge.bench.ablation as ab

    doc = _yaml.safe_load(ab.CASES_PATH.read_text(encoding="utf-8")) or {}
    doc["cases"]["fias_record_layout_change"] = {
        "spec": {"kind": "generated", "connector": "atlaspms"},
        "drift_class": "FIELD_RENAME",
        "delta": {"kind": "generic"},
        "extra": {},
        "expected": "repair",
    }
    fias_yaml = ab.SCENARIOS_DIR / "fias_record_layout_change.yaml"
    assert fias_yaml.is_file()  # transport: fias_tcp in the fixture
    monkeypatch.setattr(ab, "_load_case_data", lambda: doc["cases"])
    with pytest.raises(ValueError, match="not an HTTP bench fault"):
        ablation_case_for_scenario("fias_record_layout_change")


def test_provider_factory_fake() -> None:
    assert isinstance(ablation_provider("fake"), FakeProvider)


def test_provider_factory_auto_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from autobridge.bench.ablation import ablation_provider as ap

    monkeypatch.delenv("FRONTIER_API_KEY", raising=False)
    prov = ap("auto")
    assert type(prov).__name__ == "ReplayProvider"


def test_provider_factory_live_without_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRONTIER_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="FRONTIER_API_KEY"):
        ablation_provider("live")


def test_engine_cold_structural_repair_zero_tokens() -> None:
    cfg = HarnessConfig()
    inputs = make_trial_inputs(cfg, "page_to_cursor", 0)
    case = ablation_case_for_scenario("page_to_cursor")
    res = run_engine_arm(inputs, case, ablation="autobridge_cold", provider=FakeProvider())
    assert res.arm == "autobridge_cold"
    assert res.success
    assert res.frontier_tokens == 0
    assert res.spec is not None and res.spec["decision"] == "propose"
    assert res.spec["ladder_level_reached"] == 1


def test_engine_no_knowledge_escalates_structural() -> None:
    cfg = HarnessConfig()
    inputs = make_trial_inputs(cfg, "page_to_cursor", 0)
    case = ablation_case_for_scenario("page_to_cursor")
    res = run_engine_arm(inputs, case, ablation="engine_no_knowledge", provider=FakeProvider())
    assert res.arm == "engine_no_knowledge"
    # L0+L1 disabled: the deterministic repair is unreachable; the ladder
    # escalates instead of guessing.
    assert res.spec is not None and res.spec["decision"] == "escalate"


def test_engine_semantic_climb_uses_provider() -> None:
    cfg = HarnessConfig()
    inputs = make_trial_inputs(cfg, "gross_to_net", 0)
    case = ablation_case_for_scenario("gross_to_net")
    res = run_engine_arm(inputs, case, ablation="autobridge_cold", provider=FakeProvider())
    assert res.spec is not None and res.spec["decision"] == "escalate"
    assert res.frontier_tokens > 0


def test_warm_cache_learns_dev_split() -> None:
    from autobridge.repair.knowledge import KnowledgeCache

    cache = warm_knowledge(["page_to_cursor"], provider=FakeProvider())
    assert isinstance(cache, KnowledgeCache)
    # Warm arm hits L0 on the learned drift.
    cfg = HarnessConfig()
    inputs = make_trial_inputs(cfg, "page_to_cursor", 0)
    case = ablation_case_for_scenario("page_to_cursor")
    res = run_engine_arm(inputs, case, ablation="autobridge_warm", provider=FakeProvider(), knowledge=cache)
    assert res.spec is not None and res.spec["ladder_level_reached"] == 0


def test_frontier_oneshot_arm_label() -> None:
    cfg = HarnessConfig()
    inputs = make_trial_inputs(cfg, "page_to_cursor", 0)
    res = run_frontier_oneshot_arm(inputs, provider=FakeProvider())
    assert res.arm == "frontier_oneshot"


def test_frontier_agent_arm_label() -> None:
    cfg = HarnessConfig()
    inputs = make_trial_inputs(cfg, "page_to_cursor", 0)
    res = run_frontier_agent_arm(inputs, driver=FakeOpenCodeDriver())
    assert res.arm == "frontier_agent"
    assert res.frontier_tokens == 30  # 10 + 20 default fake usage


def test_assert_all_inputs_equal_ok() -> None:
    cfg = HarnessConfig()
    named = {arm: make_trial_inputs(cfg, "page_to_cursor", 0) for arm in ABLATION_ARMS}
    assert_all_inputs_equal(named)  # must not raise


def test_assert_all_inputs_equal_names_pair() -> None:
    cfg = HarnessConfig()
    named = {arm: make_trial_inputs(cfg, "page_to_cursor", 0) for arm in ABLATION_ARMS}
    named["autobridge_warm"] = make_trial_inputs(cfg, "page_to_cursor", 1)
    with pytest.raises(AssertionError, match="autobridge_cold.*autobridge_warm|warm"):
        assert_all_inputs_equal(named)


def test_ablation_trial_with_fake_runners() -> None:
    from autobridge.bench.harness import ArmResult

    cfg = HarnessConfig()

    def _runner(arm: str) -> Any:
        def _run(inputs: Any) -> Any:
            return ArmResult(arm=arm, inputs=inputs, success=True)  # type: ignore[arg-type]

        return _run

    trial = run_ablation_trial(
        cfg, "page_to_cursor", 0, runners={arm: _runner(arm) for arm in ABLATION_ARMS}
    )
    assert isinstance(trial, AblationTrialResult)
    assert trial.matched
    assert set(trial.results) == set(ABLATION_ARMS)


def test_ablation_trial_default_runners_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRONTIER_API_KEY", raising=False)
    cfg = HarnessConfig()
    trial = run_ablation_trial(cfg, "page_to_cursor", 0, provider=FakeProvider())
    assert trial.matched
    assert set(trial.results) == set(ABLATION_ARMS)
    assert trial.results["autobridge_cold"].frontier_tokens == 0


def test_grade_and_summarize() -> None:
    from autobridge.bench.harness import ArmResult
    from autobridge.bench.scoring import VerifyOutcome

    cfg = HarnessConfig()
    inputs = make_trial_inputs(cfg, "page_to_cursor", 0)
    ok = VerifyOutcome(verdict="pass", regression="pass")
    trial = AblationTrialResult(
        scenario_id="page_to_cursor",
        trial=0,
        seed=inputs.seed,
        inputs=inputs,
        results={
            arm: ArmResult(arm=arm, inputs=inputs, success=True, frontier_tokens=0)  # type: ignore[arg-type]
            for arm in ABLATION_ARMS
        },
    )
    grades = [grade_ablation_arm(arm, trial, ok, expected="repair") for arm in ABLATION_ARMS]
    assert all(g.verified_completion == 1 for g in grades)
    assert all(g.wrong_patch_released == 0 for g in grades)
    summary = summarize_ablation(grades)
    assert set(summary["per_arm"]) == set(ABLATION_ARMS)
    cold = summary["per_arm"]["autobridge_cold"]
    assert cold["verified_completion_rate"]["mean"] == 1.0
    assert cold["wrong_patches_released"] == 0
    assert cold["frontier_tokens"]["n"] == 1


def test_grade_unreachable_verifier_never_guessed() -> None:
    from autobridge.bench.harness import ArmResult

    cfg = HarnessConfig()
    inputs = make_trial_inputs(cfg, "page_to_cursor", 0)
    trial = AblationTrialResult(
        scenario_id="page_to_cursor",
        trial=0,
        seed=inputs.seed,
        inputs=inputs,
        results={
            "autobridge_cold": ArmResult(  # type: ignore[arg-type]
                arm="autobridge_cold",
                inputs=inputs,
                success=True,
                spec={"decision": "propose", "candidate_patch": []},
            )
        },
    )
    g = grade_ablation_arm("autobridge_cold", trial, None, expected="repair")
    assert g.verified_completion == 0
    assert g.correct_escalation == 0


def test_grade_no_patch_outcome_classes() -> None:
    from autobridge.bench.harness import ArmResult

    cfg = HarnessConfig()
    inputs = make_trial_inputs(cfg, "rate_limit_429", 0)
    backoff = ArmResult(  # type: ignore[arg-type]
        arm="autobridge_cold",
        inputs=inputs,
        success=True,
        spec={"decision": "propose", "candidate_patch": []},
    )
    trial = AblationTrialResult(
        scenario_id="rate_limit_429", trial=0, seed=inputs.seed, inputs=inputs, results={"autobridge_cold": backoff}
    )
    g = grade_ablation_arm("autobridge_cold", trial, None, expected="no_patch")
    assert g.correct_escalation == 1
    assert g.verified_completion == 0

    releaser = ArmResult(  # type: ignore[arg-type]
        arm="engine_no_knowledge",
        inputs=inputs,
        success=True,
        spec={"decision": "propose", "candidate_patch": [{"op": "replace", "path": "/x", "value": 1}]},
    )
    trial2 = AblationTrialResult(
        scenario_id="rate_limit_429", trial=0, seed=inputs.seed, inputs=inputs, results={"engine_no_knowledge": releaser}
    )
    g2 = grade_ablation_arm("engine_no_knowledge", trial2, None, expected="no_patch")
    assert g2.correct_escalation == 0


def test_ablation_trial_runner_exception_becomes_failed_arm() -> None:
    cfg = HarnessConfig()

    def _boom(_inputs: Any) -> Any:
        raise RuntimeError("boom")

    trial = run_ablation_trial(cfg, "page_to_cursor", 0, runners={"autobridge_cold": _boom})
    assert trial.results["autobridge_cold"].success is False
    assert "boom" in (trial.results["autobridge_cold"].error or "")


def test_mean_ci95_shapes() -> None:
    from autobridge.bench.ablation import _mean_ci95

    assert _mean_ci95([]) == {"mean": None, "ci95_lo": None, "ci95_hi": None, "n": 0}
    one = _mean_ci95([2.0])
    assert one["mean"] == 2.0 and one["n"] == 1
    many = _mean_ci95([1.0, 2.0, 3.0, 4.0, 5.0])
    assert many["n"] == 5
    assert many["ci95_lo"] < many["mean"] < many["ci95_hi"]


def test_summary_clamps_bounds() -> None:
    from autobridge.bench.harness import ArmResult, HarnessConfig, make_trial_inputs

    cfg = HarnessConfig()
    inputs = make_trial_inputs(cfg, "gross_to_net", 0)
    grades = [
        AblationGrade(
            arm="autobridge_cold",
            scenario_id="gross_to_net",
            trial=t,
            verified_completion=0,
            wrong_patch_released=0,
            correct_escalation=1,
            frontier_tokens=1210 if t == 0 else 0,
            small_tokens=0,
            cost_usd=None,
            elapsed_ms=10.0,
        )
        for t in range(2)
    ]
    summary = summarize_ablation(grades)
    block = summary["per_arm"]["autobridge_cold"]
    assert block["frontier_tokens"]["ci95_lo"] == 0.0
    assert 0.0 <= block["correct_escalation_rate"]["mean"] <= 1.0
    arm_result_check = ArmResult(arm="autobridge_cold", inputs=inputs, success=True)  # type: ignore[arg-type]
    assert arm_result_check.arm == "autobridge_cold"


def test_frontier_agent_driver_default_opencode(monkeypatch: pytest.MonkeyPatch) -> None:
    from autobridge.bench.ablation import frontier_agent_driver
    from autobridge.bench.baseline_coding_agent import FakeOpenCodeDriver, OpenCodeCliDriver

    monkeypatch.delenv("BASELINE_DRIVER", raising=False)
    monkeypatch.setenv("FRONTIER_API_KEY", "test-key")
    assert isinstance(frontier_agent_driver(), OpenCodeCliDriver)
    monkeypatch.delenv("FRONTIER_API_KEY", raising=False)
    assert isinstance(frontier_agent_driver(), FakeOpenCodeDriver)


def test_frontier_agent_driver_kinds(monkeypatch: pytest.MonkeyPatch) -> None:
    from autobridge.bench.ablation import frontier_agent_driver
    from autobridge.bench.baseline_coding_agent import BaselineLLMDriver

    monkeypatch.setenv("BASELINE_DRIVER", "llm")
    assert isinstance(frontier_agent_driver(), BaselineLLMDriver)


def test_run_ablation_harness_offline(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from autobridge.bench.ablation import run_ablation_harness, write_ablation_results
    from autobridge.bench.harness import HarnessConfig

    monkeypatch.delenv("FRONTIER_API_KEY", raising=False)
    cfg = HarnessConfig(trials_per_scenario=1)
    trials, grades, summary = run_ablation_harness(
        cfg, ["page_to_cursor", "rate_limit_429"], provider=FakeProvider(), verifier_url=None
    )
    assert len(trials) == 2
    assert all(t.matched for t in trials)
    assert summary["trials"] == 2
    assert summary["skipped_scenarios"] == []
    assert set(summary["per_arm"]) == set(ABLATION_ARMS)
    # Offline without a verifier: nothing is verified, never guessed.
    assert summary["overall"]["verified_completion_rate"]["mean"] == 0.0
    paths = write_ablation_results(trials, summary, tmp_path)
    assert len(paths) == 2 and all(p.is_file() for p in paths)


def test_render_ablation_table() -> None:
    from autobridge.bench.ablation import render_ablation_table, summarize_ablation

    grades = [
        AblationGrade(
            arm=arm,
            scenario_id="page_to_cursor",
            trial=0,
            verified_completion=0,
            wrong_patch_released=0,
            correct_escalation=1,
            frontier_tokens=0,
            small_tokens=0,
            cost_usd=None,
            elapsed_ms=5.0,
        )
        for arm in ABLATION_ARMS
    ]
    table = render_ablation_table(summarize_ablation(grades))
    for arm in ABLATION_ARMS:
        assert arm in table


def test_learning_curve_shape(tmp_path: Any) -> None:
    result = run_learning_curve("page_to_cursor", repeats=3, provider=FakeProvider())
    assert result["scenario_id"] == "page_to_cursor"
    assert len(result["rows"]) == 3
    # Agent pays every time; engine arms learn the route.
    assert all(r["frontier_agent_frontier_tokens"] == 2000 for r in result["rows"])
    assert result["rows"][0]["autobridge_cold_rung"] == 1
    assert all(r["autobridge_cold_rung"] == 0 for r in result["rows"][1:])
    assert all(r["autobridge_warm_rung"] == 0 for r in result["rows"])
    md = write_learning_curve_md(result, tmp_path / "learning_curve.md")
    text = md.read_text(encoding="utf-8")
    assert "frontier_agent" in text and "autobridge_cold" in text
