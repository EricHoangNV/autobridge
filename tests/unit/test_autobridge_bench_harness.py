"""Tests for bench harness — matched-budget (P1.W8.1, plan §2.10)."""

from __future__ import annotations

import hashlib
import json
import pathlib  # noqa: TC003
from decimal import Decimal  # noqa: TC002

import pytest

from bench.baseline_coding_agent import FakeClaudeDriver
from bench.harness import (
    DEFAULT_CREDENTIAL_REFS,
    DEFAULT_DOCS_VOLUME,
    DEFAULT_RUNTIME_TARGET,
    DEFAULT_SANDBOX_IMAGE,
    DEFAULT_SANDBOX_URL,
    DEFAULT_TOKEN_CEILING,
    DEFAULT_WALL_CLOCK_S,
    ArmResult,
    BenchResult,
    HarnessConfig,
    HarnessInputs,
    TrialResult,
    assert_budget_fields_equal,
    assert_inputs_equal,
    check_heldout_sha,
    committed_heldout_manifest_path,
    docs_ref_for_scenario,
    heldout_manifest_sha,
    is_matched,
    list_scenarios,
    make_trial_inputs,
    run_autobridge_arm,
    run_baseline_arm,
    run_baseline_via_llm_provider,
    run_harness,
    run_trial,
    verify_matched_trial,
    write_bench_summary,
    write_results,
)

# ---------------------------------------------------------------------------
# HarnessConfig
# ---------------------------------------------------------------------------


def test_harness_config_defaults() -> None:
    cfg = HarnessConfig()
    assert cfg.sandbox_image == DEFAULT_SANDBOX_IMAGE
    assert cfg.sandbox_url == DEFAULT_SANDBOX_URL
    assert cfg.docs_volume == DEFAULT_DOCS_VOLUME
    assert cfg.credential_refs == DEFAULT_CREDENTIAL_REFS
    assert cfg.token_ceiling == DEFAULT_TOKEN_CEILING
    assert cfg.wall_clock_s == DEFAULT_WALL_CLOCK_S
    assert cfg.trials_per_scenario == 1


def test_harness_config_invalid_token_ceiling() -> None:
    # REQ-060 item 3: token_ceiling 0 = no limit (valid); negatives fail.
    assert HarnessConfig(token_ceiling=0).token_ceiling == 0
    with pytest.raises(ValueError, match="token_ceiling"):
        HarnessConfig(token_ceiling=-1)


def test_harness_config_invalid_wall_clock() -> None:
    with pytest.raises(ValueError, match="wall_clock_s"):
        HarnessConfig(wall_clock_s=0)


def test_harness_config_invalid_trials() -> None:
    with pytest.raises(ValueError, match="trials_per_scenario"):
        HarnessConfig(trials_per_scenario=0)


def test_harness_config_custom() -> None:
    cfg = HarnessConfig(
        sandbox_image="my-image",
        sandbox_url="http://custom:8000",
        docs_volume="/tmp/docs",
        credential_refs=("MY_KEY",),
        visible_tests=("baseline", "rename_guest_id"),
        token_ceiling=5000,
        wall_clock_s=60.0,
        seed=123,
        scenarios=("baseline",),
    )
    assert cfg.sandbox_image == "my-image"
    assert cfg.credential_refs == ("MY_KEY",)
    assert cfg.visible_tests == ("baseline", "rename_guest_id")
    assert cfg.seed == 123


# ---------------------------------------------------------------------------
# docs_ref_for_scenario
# ---------------------------------------------------------------------------


def test_docs_ref_for_scenario() -> None:
    ref = docs_ref_for_scenario("baseline")
    assert "baseline" in ref
    assert DEFAULT_DOCS_VOLUME in ref


def test_docs_ref_for_scenario_custom_volume() -> None:
    ref = docs_ref_for_scenario("baseline", docs_volume="/my/docs")
    assert ref == "/my/docs/baseline"


def test_docs_ref_for_scenario_unknown() -> None:
    ref = docs_ref_for_scenario("unknown_scenario_xyz")
    assert "unknown_scenario_xyz" in ref


# ---------------------------------------------------------------------------
# list_scenarios
# ---------------------------------------------------------------------------


def test_list_scenarios_dev() -> None:
    dev_ids = list_scenarios(visibility="dev")
    assert "baseline" in dev_ids
    assert "rename_guest_id" in dev_ids
    # heldout not in dev
    assert "endpoint_removed" not in dev_ids
    assert "token_revoked" not in dev_ids


def test_list_scenarios_heldout() -> None:
    heldout_ids = list_scenarios(visibility="heldout")
    assert "endpoint_removed" in heldout_ids or "tz_boundary_shift" in heldout_ids


def test_list_scenarios_all() -> None:
    all_ids = list_scenarios(visibility=None)
    assert len(all_ids) >= 10
    assert "baseline" in all_ids


# ---------------------------------------------------------------------------
# make_trial_inputs — deterministic
# ---------------------------------------------------------------------------


def test_make_trial_inputs_basic() -> None:
    cfg = HarnessConfig()
    inp = make_trial_inputs(cfg, "baseline", 0)
    assert inp.scenario_id == "baseline"
    assert inp.trial == 0
    assert inp.sandbox_image == DEFAULT_SANDBOX_IMAGE
    assert inp.sandbox_url == DEFAULT_SANDBOX_URL
    assert inp.docs_volume == DEFAULT_DOCS_VOLUME
    assert inp.credential_refs == DEFAULT_CREDENTIAL_REFS
    assert inp.visible_tests == ("baseline",)
    assert inp.token_ceiling == DEFAULT_TOKEN_CEILING
    assert inp.wall_clock_s == DEFAULT_WALL_CLOCK_S
    assert inp.runtime_target == DEFAULT_RUNTIME_TARGET


def test_make_trial_inputs_deterministic() -> None:
    cfg = HarnessConfig(seed=42)
    a = make_trial_inputs(cfg, "baseline", 0)
    b = make_trial_inputs(cfg, "baseline", 0)
    assert a == b
    assert a.seed == b.seed
    assert a.fingerprint() == b.fingerprint()


def test_make_trial_inputs_different_trial_different_seed() -> None:
    cfg = HarnessConfig(seed=42)
    a = make_trial_inputs(cfg, "baseline", 0)
    b = make_trial_inputs(cfg, "baseline", 1)
    assert a.seed != b.seed
    assert a.trial != b.trial


def test_make_trial_inputs_different_scenario_different_seed() -> None:
    cfg = HarnessConfig(seed=42)
    a = make_trial_inputs(cfg, "baseline", 0)
    b = make_trial_inputs(cfg, "rename_guest_id", 0)
    assert a.seed != b.seed


def test_make_trial_inputs_different_config_different() -> None:
    cfg1 = HarnessConfig(seed=42, token_ceiling=1000)
    cfg2 = HarnessConfig(seed=42, token_ceiling=2000)
    a = make_trial_inputs(cfg1, "baseline", 0)
    b = make_trial_inputs(cfg2, "baseline", 0)
    assert a.token_ceiling != b.token_ceiling


def test_make_trial_inputs_invalid_scenario() -> None:
    cfg = HarnessConfig()
    with pytest.raises(ValueError, match="scenario_id"):
        make_trial_inputs(cfg, "", 0)


def test_make_trial_inputs_invalid_trial() -> None:
    cfg = HarnessConfig()
    with pytest.raises(ValueError, match="trial"):
        make_trial_inputs(cfg, "baseline", -1)


def test_make_trial_inputs_runtime_target_copied() -> None:
    cfg = HarnessConfig(runtime_target={"kind": "docker_sandbox", "extra": "x"})
    inp = make_trial_inputs(cfg, "baseline", 0)
    # Mutating original should not affect inputs (dict copy)
    cfg.runtime_target["extra"] = "mutated"
    assert inp.runtime_target["extra"] == "x"


# ---------------------------------------------------------------------------
# HarnessInputs — fingerprint, budget_fields
# ---------------------------------------------------------------------------


def test_harness_inputs_fingerprint_stable() -> None:
    cfg = HarnessConfig(seed=99)
    inp = make_trial_inputs(cfg, "baseline", 0)
    fp1 = inp.fingerprint()
    fp2 = inp.fingerprint()
    assert fp1 == fp2
    assert len(fp1) == 16


def test_harness_inputs_fingerprint_differs_on_change() -> None:
    cfg = HarnessConfig(seed=42)
    a = make_trial_inputs(cfg, "baseline", 0)
    # Same but with different token ceiling should differ
    b = make_trial_inputs(HarnessConfig(seed=42, token_ceiling=9999), "baseline", 0)
    assert a.fingerprint() != b.fingerprint()


def test_harness_inputs_budget_fields() -> None:
    cfg = HarnessConfig()
    inp = make_trial_inputs(cfg, "baseline", 0)
    bf = inp.budget_fields()
    assert bf["docs_volume"] == DEFAULT_DOCS_VOLUME
    assert bf["sandbox_url"] == DEFAULT_SANDBOX_URL
    assert bf["sandbox_image"] == DEFAULT_SANDBOX_IMAGE
    assert bf["credential_refs"] == DEFAULT_CREDENTIAL_REFS
    assert bf["visible_tests"] == ("baseline",)
    assert bf["token_ceiling"] == DEFAULT_TOKEN_CEILING
    assert bf["wall_clock_s"] == DEFAULT_WALL_CLOCK_S


def test_harness_inputs_identical_both_arms() -> None:
    """Core harness invariant: both arms for same scenario×trial get identical budget."""
    cfg = HarnessConfig()
    a = make_trial_inputs(cfg, "baseline", 0)
    b = make_trial_inputs(cfg, "baseline", 0)
    assert a.budget_fields() == b.budget_fields()
    assert a == b


# ---------------------------------------------------------------------------
# assert_inputs_equal — harness self-test
# ---------------------------------------------------------------------------


def test_assert_inputs_equal_pass() -> None:
    cfg = HarnessConfig()
    a = make_trial_inputs(cfg, "baseline", 0)
    b = make_trial_inputs(cfg, "baseline", 0)
    assert_inputs_equal(a, b)  # should not raise


def test_assert_inputs_equal_same_object() -> None:
    cfg = HarnessConfig()
    a = make_trial_inputs(cfg, "baseline", 0)
    assert_inputs_equal(a, a)


def test_assert_inputs_equal_fails_on_docs_volume() -> None:
    cfg = HarnessConfig()
    a = make_trial_inputs(cfg, "baseline", 0)
    b = make_trial_inputs(HarnessConfig(docs_volume="/other/docs"), "baseline", 0)
    with pytest.raises(AssertionError, match="docs_volume"):
        assert_inputs_equal(a, b)


def test_assert_inputs_equal_fails_on_sandbox_url() -> None:
    cfg = HarnessConfig()
    a = make_trial_inputs(cfg, "baseline", 0)
    b = make_trial_inputs(HarnessConfig(sandbox_url="http://other:8000"), "baseline", 0)
    with pytest.raises(AssertionError, match="sandbox_url"):
        assert_inputs_equal(a, b)


def test_assert_inputs_equal_fails_on_sandbox_image() -> None:
    cfg = HarnessConfig()
    a = make_trial_inputs(cfg, "baseline", 0)
    b = make_trial_inputs(HarnessConfig(sandbox_image="other-image"), "baseline", 0)
    with pytest.raises(AssertionError, match="sandbox_image"):
        assert_inputs_equal(a, b)


def test_assert_inputs_equal_fails_on_credential_refs() -> None:
    cfg = HarnessConfig()
    a = make_trial_inputs(cfg, "baseline", 0)
    b = make_trial_inputs(HarnessConfig(credential_refs=("OTHER_KEY",)), "baseline", 0)
    with pytest.raises(AssertionError, match="credential_refs"):
        assert_inputs_equal(a, b)


def test_assert_inputs_equal_fails_on_visible_tests() -> None:
    cfg = HarnessConfig()
    a = make_trial_inputs(cfg, "baseline", 0)
    b = make_trial_inputs(HarnessConfig(visible_tests=("other",)), "baseline", 0)
    with pytest.raises(AssertionError, match="visible_tests"):
        assert_inputs_equal(a, b)


def test_assert_inputs_equal_fails_on_token_ceiling() -> None:
    cfg = HarnessConfig()
    a = make_trial_inputs(cfg, "baseline", 0)
    b = make_trial_inputs(HarnessConfig(token_ceiling=999), "baseline", 0)
    with pytest.raises(AssertionError, match="token_ceiling"):
        assert_inputs_equal(a, b)


def test_assert_inputs_equal_fails_on_wall_clock() -> None:
    cfg = HarnessConfig()
    a = make_trial_inputs(cfg, "baseline", 0)
    b = make_trial_inputs(HarnessConfig(wall_clock_s=999), "baseline", 0)
    with pytest.raises(AssertionError, match="wall_clock_s"):
        assert_inputs_equal(a, b)


def test_assert_inputs_equal_fails_on_scenario_id() -> None:
    cfg = HarnessConfig()
    a = make_trial_inputs(cfg, "baseline", 0)
    b = make_trial_inputs(cfg, "rename_guest_id", 0)
    with pytest.raises(AssertionError, match="scenario_id"):
        assert_inputs_equal(a, b)


def test_assert_inputs_equal_fails_on_runtime_target() -> None:
    cfg = HarnessConfig()
    a = make_trial_inputs(cfg, "baseline", 0)
    b = make_trial_inputs(HarnessConfig(runtime_target={"kind": "local_process"}), "baseline", 0)
    with pytest.raises(AssertionError, match="runtime_target"):
        assert_inputs_equal(a, b)


def test_is_matched_true() -> None:
    cfg = HarnessConfig()
    a = make_trial_inputs(cfg, "baseline", 0)
    b = make_trial_inputs(cfg, "baseline", 0)
    assert is_matched(a, b) is True


def test_is_matched_false() -> None:
    cfg = HarnessConfig()
    a = make_trial_inputs(cfg, "baseline", 0)
    b = make_trial_inputs(HarnessConfig(token_ceiling=999), "baseline", 0)
    assert is_matched(a, b) is False


def test_assert_budget_fields_equal_pass() -> None:
    cfg = HarnessConfig()
    a = make_trial_inputs(cfg, "baseline", 0)
    b = make_trial_inputs(cfg, "baseline", 0)
    assert_budget_fields_equal(a, b)


def test_assert_budget_fields_equal_fails() -> None:
    cfg = HarnessConfig()
    a = make_trial_inputs(cfg, "baseline", 0)
    b = make_trial_inputs(HarnessConfig(docs_volume="/other"), "baseline", 0)
    with pytest.raises(AssertionError, match="docs_volume"):
        assert_budget_fields_equal(a, b)


# ---------------------------------------------------------------------------
# ArmResult / TrialResult / BenchResult
# ---------------------------------------------------------------------------


def test_arm_result_total_tokens() -> None:
    cfg = HarnessConfig()
    inp = make_trial_inputs(cfg, "baseline", 0)
    arm = ArmResult(arm="autobridge", inputs=inp, ran=True, input_tokens=10, output_tokens=20)
    assert arm.total_tokens == 30


def test_arm_result_to_dict() -> None:
    cfg = HarnessConfig()
    inp = make_trial_inputs(cfg, "baseline", 0)
    arm = ArmResult(
        arm="autobridge",
        inputs=inp,
        ran=True,
        input_tokens=5,
        output_tokens=10,
        cost_usd=Decimal("0.01"),
        verdict="pass",
        score=1.0,
    )
    d = arm.to_dict()
    assert d["arm"] == "autobridge"
    assert d["scenario_id"] == "baseline"
    assert d["total_tokens"] == 15
    assert d["cost_usd"] == "0.01"
    assert d["budget"]["docs_volume"] == DEFAULT_DOCS_VOLUME
    assert d["inputs_fingerprint"] == inp.fingerprint()


def test_arm_result_to_dict_no_cost() -> None:
    cfg = HarnessConfig()
    inp = make_trial_inputs(cfg, "baseline", 0)
    arm = ArmResult(arm="baseline", inputs=inp, ran=False, error="boom")
    d = arm.to_dict()
    assert d["cost_usd"] is None
    assert d["error"] == "boom"


def test_trial_result_to_dict() -> None:
    cfg = HarnessConfig()
    inp = make_trial_inputs(cfg, "baseline", 0)
    ab = ArmResult(arm="autobridge", inputs=inp, ran=True)
    bl = ArmResult(arm="baseline", inputs=inp, ran=True)
    trial = TrialResult(scenario_id="baseline", trial=0, seed=inp.seed, inputs=inp, autobridge=ab, baseline=bl, matched=True)
    d = trial.to_dict()
    assert d["scenario_id"] == "baseline"
    assert d["matched"] is True
    assert "autobridge" in d
    assert "baseline" in d
    assert d["inputs_fingerprint"] == inp.fingerprint()


def test_bench_result_all_matched() -> None:
    cfg = HarnessConfig()
    inp = make_trial_inputs(cfg, "baseline", 0)
    ab = ArmResult(arm="autobridge", inputs=inp, ran=True)
    bl = ArmResult(arm="baseline", inputs=inp, ran=True)
    t1 = TrialResult(scenario_id="baseline", trial=0, seed=inp.seed, inputs=inp, autobridge=ab, baseline=bl, matched=True)
    t2 = TrialResult(scenario_id="baseline", trial=1, seed=inp.seed + 1, inputs=inp, autobridge=ab, baseline=bl, matched=False)
    bench = BenchResult(config=cfg, trials=[t1, t2])
    assert bench.all_matched() is False
    assert bench.to_dict()["total_trials"] == 2
    assert bench.to_dict()["matched_trials"] == 1


def test_bench_result_empty() -> None:
    cfg = HarnessConfig()
    bench = BenchResult(config=cfg)
    assert bench.all_matched() is True  # vacuously
    assert bench.to_dict()["total_trials"] == 0


# ---------------------------------------------------------------------------
# verify_matched_trial
# ---------------------------------------------------------------------------


def test_verify_matched_trial_pass() -> None:
    cfg = HarnessConfig()
    inp = make_trial_inputs(cfg, "baseline", 0)
    ab = ArmResult(arm="autobridge", inputs=inp, ran=True)
    bl = ArmResult(arm="baseline", inputs=inp, ran=True)
    trial = TrialResult(scenario_id="baseline", trial=0, seed=inp.seed, inputs=inp, autobridge=ab, baseline=bl, matched=True)
    verify_matched_trial(trial)  # no raise


def test_verify_matched_trial_fail() -> None:
    cfg = HarnessConfig()
    inp = make_trial_inputs(cfg, "baseline", 0)
    ab = ArmResult(arm="autobridge", inputs=inp, ran=True)
    bl = ArmResult(arm="baseline", inputs=inp, ran=True)
    trial = TrialResult(scenario_id="baseline", trial=0, seed=inp.seed, inputs=inp, autobridge=ab, baseline=bl, matched=False, matched_error="docs_volume differ")
    with pytest.raises(AssertionError, match="not matched"):
        verify_matched_trial(trial)


# ---------------------------------------------------------------------------
# check_heldout_sha — REQ-007: temp HELDOUT_DIR + temp manifest, never the real dir
# ---------------------------------------------------------------------------


def _write_manifest(tmp_path: pathlib.Path, files: dict[str, bytes]) -> pathlib.Path:
    """Write *files* into tmp_path plus a matching SHA256SUMS; return manifest path."""
    lines = []
    for rel, blob in files.items():
        (tmp_path / rel).write_bytes(blob)
        lines.append(f"{hashlib.sha256(blob).hexdigest()}  {rel}\n")
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_text("".join(lines))
    return manifest


def test_check_heldout_sha_no_manifest(tmp_path: pathlib.Path) -> None:
    # Missing manifest file — no-op (heldout not frozen on that path).
    check_heldout_sha(heldout_dir=tmp_path, manifest_path=tmp_path / "SHA256SUMS")  # no raise


def test_check_heldout_sha_valid(tmp_path: pathlib.Path) -> None:
    manifest = _write_manifest(tmp_path, {"data.yaml": b"hello"})
    check_heldout_sha(heldout_dir=tmp_path, manifest_path=manifest)  # no raise


def test_check_heldout_sha_mismatch(tmp_path: pathlib.Path) -> None:
    (tmp_path / "data.yaml").write_text("hello")
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_text("deadbeef  data.yaml\n")
    with pytest.raises(AssertionError, match="mismatch"):
        check_heldout_sha(heldout_dir=tmp_path, manifest_path=manifest)


def test_check_heldout_sha_missing_file(tmp_path: pathlib.Path) -> None:
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_text("abc123  missing.yaml\n")
    with pytest.raises(AssertionError, match="missing"):
        check_heldout_sha(heldout_dir=tmp_path, manifest_path=manifest)


def test_check_heldout_sha_with_comments(tmp_path: pathlib.Path) -> None:
    f = tmp_path / "data.yaml"
    f.write_text("hello")
    sha = hashlib.sha256(b"hello").hexdigest()
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_text(f"# comment\n\n{sha}  data.yaml\n")
    check_heldout_sha(heldout_dir=tmp_path, manifest_path=manifest)  # no raise


def test_check_heldout_sha_requires_heldout_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    # Committed manifest is frozen in the repo; without HELDOUT_DIR the
    # gate refuses (REQ-007) instead of checking repo files.
    assert committed_heldout_manifest_path().is_file()
    monkeypatch.delenv("HELDOUT_DIR", raising=False)
    with pytest.raises(AssertionError, match="HELDOUT_DIR not set"):
        check_heldout_sha()


def test_heldout_manifest_sha_hex() -> None:
    sha = heldout_manifest_sha()
    assert sha is not None
    assert len(sha) == 64
    assert all(c in "0123456789abcdef" for c in sha)


def test_heldout_manifest_sha_missing(tmp_path: pathlib.Path) -> None:
    assert heldout_manifest_sha(tmp_path / "SHA256SUMS") is None


# ---------------------------------------------------------------------------
# run_autobridge_arm — synthetic and fake client
# ---------------------------------------------------------------------------


def test_run_autobridge_arm_synthetic() -> None:
    cfg = HarnessConfig()
    inp = make_trial_inputs(cfg, "baseline", 0)
    result = run_autobridge_arm(inp)
    assert result.arm == "autobridge"
    assert result.ran is True
    assert result.inputs == inp


def test_run_autobridge_arm_with_fake_client_success() -> None:
    cfg = HarnessConfig()
    inp = make_trial_inputs(cfg, "baseline", 0)

    class FakeResp:
        status_code = 201
        text = "ok"

    class FakeClient:
        def post(self, url, json=None, timeout=None):  # noqa: ARG002,ARG001
            assert "connectors" in url
            assert json is not None
            assert json["base_url"] == inp.sandbox_url
            return FakeResp()

    result = run_autobridge_arm(inp, client=FakeClient())
    assert result.ran is True


def test_run_autobridge_arm_with_fake_client_error() -> None:
    cfg = HarnessConfig()
    inp = make_trial_inputs(cfg, "baseline", 0)

    class FakeResp:
        status_code = 500
        text = "internal error"

    class FakeClient:
        def post(self, url, json=None, timeout=None):  # noqa: ARG002,ARG001
            return FakeResp()

    result = run_autobridge_arm(inp, client=FakeClient())
    assert result.ran is False
    assert "500" in result.error


def test_run_autobridge_arm_client_raises() -> None:
    cfg = HarnessConfig()
    inp = make_trial_inputs(cfg, "baseline", 0)

    class BadClient:
        def post(self, url, json=None, timeout=None):  # noqa: ARG002,ARG001
            raise RuntimeError("network down")

    result = run_autobridge_arm(inp, client=BadClient())
    assert result.ran is False
    assert "network down" in result.error


def test_run_autobridge_arm_custom_control_url() -> None:
    cfg = HarnessConfig()
    inp = make_trial_inputs(cfg, "baseline", 0)

    class FakeResp:
        status_code = 200
        text = "ok"

    class FakeClient:
        def post(self, url, json=None, timeout=None):  # noqa: ARG002,ARG001
            assert url.startswith("http://custom:9999")
            return FakeResp()

    result = run_autobridge_arm(inp, client=FakeClient(), control_api_url="http://custom:9999")
    assert result.ran is True


# ---------------------------------------------------------------------------
# run_baseline_arm
# ---------------------------------------------------------------------------


def test_run_baseline_arm_synthetic() -> None:
    cfg = HarnessConfig()
    inp = make_trial_inputs(cfg, "baseline", 0)
    result = run_baseline_arm(inp)
    assert result.arm == "baseline"
    assert result.ran is True
    assert result.inputs == inp


def test_run_baseline_arm_with_fake_driver() -> None:
    cfg = HarnessConfig()
    inp = make_trial_inputs(cfg, "baseline", 0)
    driver = FakeClaudeDriver(default_text="my spec")
    result = run_baseline_arm(inp, driver=driver)
    assert result.ran is True
    assert result.total_tokens > 0
    assert driver.last_prompt is not None
    # Prompt should contain scenario and docs info
    assert "baseline" in driver.last_prompt


def test_run_baseline_arm_driver_raises() -> None:
    cfg = HarnessConfig()
    inp = make_trial_inputs(cfg, "baseline", 0)

    class BadDriver:
        def run(self, prompt, cwd=None, **kw):  # noqa: ARG002,ARG001
            raise RuntimeError("cli crashed")

    result = run_baseline_arm(inp, driver=BadDriver())
    assert result.ran is False
    assert "cli crashed" in result.error


def test_run_baseline_arm_cost_extraction() -> None:
    cfg = HarnessConfig()
    inp = make_trial_inputs(cfg, "baseline", 0)
    from autobridge.llm.provider import Usage
    from bench.baseline_coding_agent import ClaudeCliResult

    canned = ClaudeCliResult(text="spec", usage=Usage(input_tokens=100, output_tokens=200), model="claude-test", cost_usd=Decimal("0.05"))
    driver = FakeClaudeDriver(responses={"baseline": canned})
    result = run_baseline_arm(inp, driver=driver)
    assert result.input_tokens == 100
    assert result.output_tokens == 200
    assert result.frontier_tokens == 300
    assert result.cost_usd == Decimal("0.05")


# ---------------------------------------------------------------------------
# run_baseline_via_llm_provider — fallback
# ---------------------------------------------------------------------------


def test_run_baseline_via_llm_provider_synthetic() -> None:
    cfg = HarnessConfig()
    inp = make_trial_inputs(cfg, "baseline", 0)
    result = run_baseline_via_llm_provider(inp)
    assert result.arm == "baseline"
    assert result.ran is True
    assert "fallback" in result.model


def test_run_baseline_via_llm_provider_with_fake_provider() -> None:
    cfg = HarnessConfig()
    inp = make_trial_inputs(cfg, "baseline", 0)
    from autobridge.llm.provider import FakeProvider

    provider = FakeProvider(responses={"ConnectorSpec": '{"connector_id": "test"}'})
    result = run_baseline_via_llm_provider(inp, provider=provider)
    assert result.ran is True
    assert result.total_tokens >= 0


def test_run_baseline_via_llm_provider_error() -> None:
    cfg = HarnessConfig()
    inp = make_trial_inputs(cfg, "baseline", 0)

    class BadProvider:
        def complete(self, messages, **kw):  # noqa: ARG002,ARG001
            raise RuntimeError("provider down")

    result = run_baseline_via_llm_provider(inp, provider=BadProvider())
    assert result.ran is False
    assert "provider down" in result.error


# ---------------------------------------------------------------------------
# run_trial — matched-budget orchestration
# ---------------------------------------------------------------------------


def test_run_trial_both_arms_identical_inputs() -> None:
    """Harness self-test: both arms receive identical HarnessInputs."""
    cfg = HarnessConfig()
    captured_a: list[HarnessInputs] = []
    captured_b: list[HarnessInputs] = []

    def ab_runner(inp: HarnessInputs) -> ArmResult:
        captured_a.append(inp)
        return ArmResult(arm="autobridge", inputs=inp, ran=True)

    def bl_runner(inp: HarnessInputs) -> ArmResult:
        captured_b.append(inp)
        return ArmResult(arm="baseline", inputs=inp, ran=True)

    trial = run_trial(cfg, "baseline", 0, autobridge_runner=ab_runner, baseline_runner=bl_runner)

    assert trial.matched is True
    assert trial.matched_error is None
    assert len(captured_a) == 1
    assert len(captured_b) == 1
    # The core assertion: both arms got identical inputs
    assert_inputs_equal(captured_a[0], captured_b[0])
    assert captured_a[0].scenario_id == "baseline"
    assert captured_a[0].trial == 0


def test_run_trial_proves_six_budget_fields_equal() -> None:
    cfg = HarnessConfig(
        docs_volume="my-docs",
        sandbox_url="http://my-sandbox:8000",
        sandbox_image="my-image",
        credential_refs=("MY_KEY",),
        visible_tests=("baseline", "offset_to_cursor"),
        token_ceiling=5000,
        wall_clock_s=99.0,
    )
    seen: list[dict] = []

    def ab_runner(inp: HarnessInputs) -> ArmResult:
        seen.append(inp.budget_fields())
        return ArmResult(arm="autobridge", inputs=inp, ran=True)

    def bl_runner(inp: HarnessInputs) -> ArmResult:
        seen.append(inp.budget_fields())
        return ArmResult(arm="baseline", inputs=inp, ran=True)

    trial = run_trial(cfg, "rename_guest_id", 1, autobridge_runner=ab_runner, baseline_runner=bl_runner)
    assert trial.matched is True
    assert seen[0] == seen[1]
    assert seen[0]["docs_volume"] == "my-docs"
    assert seen[0]["sandbox_url"] == "http://my-sandbox:8000"
    assert seen[0]["sandbox_image"] == "my-image"
    assert seen[0]["credential_refs"] == ("MY_KEY",)
    assert seen[0]["token_ceiling"] == 5000
    assert seen[0]["wall_clock_s"] == 99.0


def test_run_trial_default_runners() -> None:
    cfg = HarnessConfig()
    trial = run_trial(cfg, "baseline", 0)
    assert trial.matched is True
    assert trial.autobridge.ran is True
    assert trial.baseline.ran is True


def test_run_trial_non_armresult_coercion() -> None:
    cfg = HarnessConfig()

    def ab_runner(inp: HarnessInputs) -> bool:  # type: ignore[return]  # noqa: ARG001
        return True

    def bl_runner(inp: HarnessInputs) -> bool:  # type: ignore[return]  # noqa: ARG001
        return False

    trial = run_trial(cfg, "baseline", 0, autobridge_runner=ab_runner, baseline_runner=bl_runner)  # type: ignore[arg-type]
    assert trial.matched is True
    assert trial.autobridge.ran is True
    assert trial.baseline.ran is False


def test_run_trial_same_runtime_sandbox_image() -> None:
    """Both arms run inside the same runtime-sandbox image."""
    cfg = HarnessConfig(sandbox_image="custom-sandbox:v2")
    captured: list[str] = []

    def ab_runner(inp: HarnessInputs) -> ArmResult:
        captured.append(inp.sandbox_image)
        return ArmResult(arm="autobridge", inputs=inp, ran=True)

    def bl_runner(inp: HarnessInputs) -> ArmResult:
        captured.append(inp.sandbox_image)
        return ArmResult(arm="baseline", inputs=inp, ran=True)

    run_trial(cfg, "baseline", 0, autobridge_runner=ab_runner, baseline_runner=bl_runner)
    assert captured[0] == "custom-sandbox:v2"
    assert captured[0] == captured[1]


# ---------------------------------------------------------------------------
# run_harness — full loop
# ---------------------------------------------------------------------------


def test_run_harness_default() -> None:
    cfg = HarnessConfig(trials_per_scenario=1, scenarios=("baseline",))
    result = run_harness(cfg)
    assert len(result.trials) == 1
    assert result.trials[0].scenario_id == "baseline"
    assert result.trials[0].matched is True
    assert result.all_matched() is True
    assert result.finished_at is not None


def test_run_harness_multiple_scenarios_and_trials() -> None:
    cfg = HarnessConfig(trials_per_scenario=2, scenarios=("baseline", "rename_guest_id"))
    result = run_harness(cfg)
    assert len(result.trials) == 4  # 2 scenarios × 2 trials
    # All matched regardless of scenario/trial
    assert result.all_matched() is True
    # Each scenario appears twice
    scenario_counts: dict[str, int] = {}
    for t in result.trials:
        scenario_counts[t.scenario_id] = scenario_counts.get(t.scenario_id, 0) + 1
    assert scenario_counts["baseline"] == 2
    assert scenario_counts["rename_guest_id"] == 2


def test_run_harness_scenarios_override() -> None:
    cfg = HarnessConfig(scenarios=("baseline", "rename_guest_id"))
    result = run_harness(cfg, scenarios=["baseline"])
    assert len(result.trials) == 1
    assert result.trials[0].scenario_id == "baseline"


def test_run_harness_with_custom_runners_records_calls() -> None:
    cfg = HarnessConfig(scenarios=("baseline",), trials_per_scenario=1)
    ab_calls: list[str] = []
    bl_calls: list[str] = []

    def ab_runner(inp: HarnessInputs) -> ArmResult:
        ab_calls.append(inp.scenario_id)
        return ArmResult(arm="autobridge", inputs=inp, ran=True, elapsed_ms=10)

    def bl_runner(inp: HarnessInputs) -> ArmResult:
        bl_calls.append(inp.scenario_id)
        return ArmResult(arm="baseline", inputs=inp, ran=True, elapsed_ms=20)

    result = run_harness(cfg, autobridge_runner=ab_runner, baseline_runner=bl_runner)
    assert ab_calls == ["baseline"]
    assert bl_calls == ["baseline"]
    assert result.trials[0].autobridge.elapsed_ms == 10
    assert result.trials[0].baseline.elapsed_ms == 20


def test_run_harness_idempotent() -> None:
    """Same seed → byte-identical trial inputs (reproducible)."""
    cfg = HarnessConfig(seed=42, scenarios=("baseline",), trials_per_scenario=1)
    r1 = run_harness(cfg)
    r2 = run_harness(cfg)
    assert r1.trials[0].inputs.fingerprint() == r2.trials[0].inputs.fingerprint()
    assert r1.trials[0].seed == r2.trials[0].seed


def test_run_harness_different_seed_different_inputs() -> None:
    cfg1 = HarnessConfig(seed=42, scenarios=("baseline",), trials_per_scenario=1)
    cfg2 = HarnessConfig(seed=99, scenarios=("baseline",), trials_per_scenario=1)
    r1 = run_harness(cfg1)
    r2 = run_harness(cfg2)
    assert r1.trials[0].seed != r2.trials[0].seed


def test_run_harness_matched_budget_for_all_trials() -> None:
    """Every trial's budget fields are identical (self-test across full harness)."""
    cfg = HarnessConfig(scenarios=("baseline", "rename_guest_id"), trials_per_scenario=2)
    result = run_harness(cfg)
    for trial in result.trials:
        assert trial.matched is True
        assert_inputs_equal(trial.autobridge.inputs, trial.baseline.inputs)
        assert_budget_fields_equal(trial.autobridge.inputs, trial.baseline.inputs)


def test_run_harness_default_scenarios_is_dev() -> None:
    cfg = HarnessConfig(trials_per_scenario=1)
    # Don't pass scenarios — should use dev list
    result = run_harness(cfg)
    assert len(result.trials) >= 1
    # All should be dev scenarios
    assert all("baseline" in t.scenario_id or t.matched for t in result.trials)


# ---------------------------------------------------------------------------
# write_results / write_bench_summary
# ---------------------------------------------------------------------------


def test_write_results_creates_files(tmp_path: pathlib.Path) -> None:
    cfg = HarnessConfig(scenarios=("baseline",), trials_per_scenario=1)
    bench = run_harness(cfg)
    paths = write_results(bench, output_dir=tmp_path)
    assert len(paths) == 1
    assert paths[0].exists()
    assert paths[0].name == "baseline--trial-0.jsonl"
    data = json.loads(paths[0].read_text(encoding="utf-8"))
    assert data["scenario_id"] == "baseline"
    assert data["matched"] is True
    assert "autobridge" in data
    assert "baseline" in data


def test_write_results_multiple_trials(tmp_path: pathlib.Path) -> None:
    cfg = HarnessConfig(scenarios=("baseline", "rename_guest_id"), trials_per_scenario=1)
    bench = run_harness(cfg)
    paths = write_results(bench, output_dir=tmp_path)
    assert len(paths) == 2
    names = sorted(p.name for p in paths)
    assert "baseline--trial-0.jsonl" in names
    assert "rename_guest_id--trial-0.jsonl" in names


def test_write_results_no_credentials_leak(tmp_path: pathlib.Path) -> None:
    cfg = HarnessConfig(scenarios=("baseline",), trials_per_scenario=1)
    bench = run_harness(cfg)
    paths = write_results(bench, output_dir=tmp_path)
    content = paths[0].read_text(encoding="utf-8")
    # Only credential_refs (key names) should appear, never values
    assert "ATLAS_API_KEY" in content
    # Ensure no secret value leaked (values are never stored)
    assert "secret" not in content.lower()


def test_write_bench_summary(tmp_path: pathlib.Path) -> None:
    cfg = HarnessConfig(scenarios=("baseline",), trials_per_scenario=1)
    bench = run_harness(cfg)
    bench.finished_at = bench.started_at + 1.0
    out = write_bench_summary(bench, path=tmp_path / "summary.json")
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["total_trials"] == 1
    assert data["matched_trials"] == 1


# ---------------------------------------------------------------------------
# main — CLI
# ---------------------------------------------------------------------------


def test_main_default_args(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from bench.harness import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AUTOBRIDGE_BENCH_SKIP_PROBE", "1")
    ret = main(["--scenarios", "baseline", "--trials", "1", "--output-dir", str(tmp_path / "out"), "--no-score"])
    assert ret == 0
    out = tmp_path / "out"
    assert (out / "baseline--trial-0.jsonl").exists()
    assert (out / "_summary.json").exists()


def test_main_custom_seed(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from bench.harness import main

    monkeypatch.setenv("AUTOBRIDGE_BENCH_SKIP_PROBE", "1")
    ret = main(["--scenarios", "baseline", "--trials", "1", "--seed", "99", "--output-dir", str(tmp_path / "out"), "--no-score"])
    assert ret == 0
    data = json.loads((tmp_path / "out" / "baseline--trial-0.jsonl").read_text(encoding="utf-8"))
    assert data["seed"] != 0


def test_main_with_bench_scenarios_env(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from bench.harness import main

    monkeypatch.setenv("BENCH_SCENARIOS", "baseline")
    monkeypatch.setenv("AUTOBRIDGE_BENCH_SKIP_PROBE", "1")
    ret = main(["--output-dir", str(tmp_path / "out"), "--no-score"])
    assert ret == 0
    assert (tmp_path / "out" / "baseline--trial-0.jsonl").exists()


def test_main_heldout_sha_failure(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from bench.harness import main

    # Heldout run without HELDOUT_DIR is refused (exit 2).
    monkeypatch.delenv("HELDOUT_DIR", raising=False)
    ret = main(["--scenarios", "baseline", "--visibility", "heldout", "--output-dir", str(tmp_path / "out1"), "--no-score"])
    assert ret == 2

    # Heldout run with a dir missing the committed files is refused (exit 2).
    tampered = tmp_path / "tampered"
    tampered.mkdir()
    (tampered / "data.yaml").write_text("hello")
    (tampered / "SHA256SUMS").write_text("deadbeef  data.yaml\n")
    monkeypatch.setenv("HELDOUT_DIR", str(tampered))
    ret = main(["--scenarios", "baseline", "--visibility", "heldout", "--output-dir", str(tmp_path / "out2"), "--no-score"])
    assert ret == 2


# ---------------------------------------------------------------------------
# Import health (compose service)
# ---------------------------------------------------------------------------


def test_harness_picks_host_urls_outside_container(monkeypatch: pytest.MonkeyPatch) -> None:
    from bench.harness import (
        DEFAULT_CONTROL_API_URL,
        DEFAULT_CONTROL_API_URL_HOST,
        DEFAULT_VERIFIER_URL_HOST,
        HarnessConfig,
        _resolve_default_url,
    )

    monkeypatch.setattr("bench.harness._is_in_container", lambda: False)
    assert _resolve_default_url(DEFAULT_CONTROL_API_URL, DEFAULT_CONTROL_API_URL_HOST) == DEFAULT_CONTROL_API_URL_HOST
    assert HarnessConfig().control_api_url == DEFAULT_CONTROL_API_URL_HOST
    assert HarnessConfig().verifier_url == DEFAULT_VERIFIER_URL_HOST
    monkeypatch.setattr("bench.harness._is_in_container", lambda: True)
    assert _resolve_default_url(DEFAULT_CONTROL_API_URL, DEFAULT_CONTROL_API_URL_HOST) == DEFAULT_CONTROL_API_URL
    assert HarnessConfig().control_api_url == DEFAULT_CONTROL_API_URL


def test_probe_unreachable_returns_nonzero() -> None:
    from bench.harness import (
        ArmResult,
        BenchResult,
        HarnessConfig,
        HarnessInputs,
        TrialResult,
        _guard_zero_tool_calls,
        _probe_services,
    )

    cfg = HarnessConfig(control_api_url="http://127.0.0.1:1", verifier_url="http://127.0.0.1:1")
    msg = _probe_services(cfg, timeout_s=0.2)
    assert msg is not None and "127.0.0.1:1" in msg

    inp = HarnessInputs(
        scenario_id="s", trial=0, seed=0, sandbox_image="x", sandbox_url="http://x",
        docs_volume="d", docs_ref="d/s", credential_refs=(), visible_tests=(), token_ceiling=100,
        wall_clock_s=1.0, control_api_url=cfg.control_api_url, verifier_url=cfg.verifier_url, runtime_target={},
    )
    bench = BenchResult(config=cfg, trials=[
        TrialResult(scenario_id="s", trial=0, seed=0, inputs=inp,
                    autobridge=ArmResult(arm="autobridge", inputs=inp, ran=True, tool_calls=0),
                    baseline=ArmResult(arm="baseline", inputs=inp, ran=True, tool_calls=0),
                    matched=True),
    ])
    err = _guard_zero_tool_calls(bench)
    assert err is not None and "0 tool calls" in err


def test_bench_app_health_import() -> None:
    from fastapi.testclient import TestClient

    from bench.app import app

    client = TestClient(app)
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200
