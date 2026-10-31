"""Tests for one_shot_generator secondary diagnostic control (P1.W8.3, plan §2.10)."""

from __future__ import annotations

import dataclasses
import pathlib
from typing import Any

import pytest

from autobridge.llm.provider import Budget, BudgetExhausted, Completion, FakeProvider, Usage
from bench.baseline_coding_agent import get_baseline_driver
from bench.fake_cli import (
    OneShotGenerator,
    OneShotResult,
    build_one_shot_prompt,
    extract_spec_yaml,
    load_docs_text,
    run_one_shot_arm,
    run_trial_with_one_shot,
    try_parse_spec_yaml,
)
from bench.harness import HarnessConfig, HarnessInputs, make_trial_inputs
from bench.one_shot import healthz

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _inputs(
    scenario_id: str = "atlas_basic",
    docs_ref: str = "simulators/docs/atlas_basic",
    token_ceiling: int = 5000,
    wall_clock_s: float = 60.0,
) -> HarnessInputs:
    cfg = HarnessConfig(token_ceiling=token_ceiling, wall_clock_s=wall_clock_s)
    inp = make_trial_inputs(cfg, scenario_id, 0)
    # Override the docs ref when the caller points at a fixture (tmp file/dir,
    # nonexistent path, or truncation target).  Without this the generated
    # ref would resolve to the real scenario docs via the volume fallback.
    if docs_ref != "simulators/docs/atlas_basic":
        inp = dataclasses.replace(inp, docs_ref=docs_ref)
    return inp


def _fake_provider(text: str, usage: Usage | None = None) -> FakeProvider:
    u = usage or Usage(input_tokens=10, output_tokens=20)
    return FakeProvider(
        responses={"default": Completion(text=text, usage=u)},
        frontier_model="fake-frontier",
        small_model="fake-small",
    )


# ---------------------------------------------------------------------------
# load_docs_text
# ---------------------------------------------------------------------------


def test_load_docs_text_missing_returns_empty(tmp_path: pathlib.Path) -> None:
    # Fully missing: docs_ref points nowhere AND the volume has no scenario
    # docs (otherwise the volume/scenario fallback legitimately finds docs).
    inp = _inputs(docs_ref="/nonexistent/path/xyz")
    inp = dataclasses.replace(inp, docs_volume=str(tmp_path))
    assert load_docs_text(inp) == ""


def test_load_docs_text_file(tmp_path: pathlib.Path) -> None:
    p = tmp_path / "openapi.yaml"
    p.write_text("openapi: 3.0\ninfo:\n  title: Test", encoding="utf-8")
    inp = _inputs(docs_ref=str(p))
    txt = load_docs_text(inp)
    assert "openapi" in txt


def test_load_docs_text_dir_with_files(tmp_path: pathlib.Path) -> None:
    d = tmp_path / "docs"
    d.mkdir()
    (d / "README.md").write_text("# Hello\ncontent", encoding="utf-8")
    (d / "openapi.yaml").write_text("openapi: 3.0", encoding="utf-8")
    inp = _inputs(docs_ref=str(d))
    txt = load_docs_text(inp)
    assert "Hello" in txt
    assert "openapi" in txt


def test_load_docs_text_max_chars(tmp_path: pathlib.Path) -> None:
    p = tmp_path / "big.yaml"
    p.write_text("a" * 2000, encoding="utf-8")
    inp = _inputs(docs_ref=str(p))
    txt = load_docs_text(inp, max_chars=100)
    assert len(txt) == 100


def test_load_docs_text_via_docs_volume_fallback(tmp_path: pathlib.Path) -> None:
    # HarnessInputs docs_volume is our volume; docs_ref may be sibling file name
    vol = tmp_path / "vol"
    vol.mkdir()
    (vol / "atlas_basic").mkdir()
    (vol / "atlas_basic" / "README.md").write_text("atlas readme", encoding="utf-8")
    cfg = HarnessConfig(docs_volume=str(vol))
    inp = make_trial_inputs(cfg, "atlas_basic", 0)
    txt = load_docs_text(inp)
    assert "atlas readme" in txt


def test_load_docs_text_truncates_120k(tmp_path: pathlib.Path) -> None:
    # Default max_chars=120k
    p = tmp_path / "big2.yaml"
    p.write_text("x" * 130_000, encoding="utf-8")
    inp = _inputs(docs_ref=str(p))
    txt = load_docs_text(inp)
    assert len(txt) == 120_000


# ---------------------------------------------------------------------------
# build_one_shot_prompt
# ---------------------------------------------------------------------------


def test_build_one_shot_prompt_contains_fields() -> None:
    inp = _inputs()
    prompt = build_one_shot_prompt(inp)
    assert "atlas_basic" in prompt
    assert "simulators/docs" in prompt or "atlas_basic" in prompt
    assert "token_ceiling" in prompt.lower() or "Token ceiling" in prompt
    assert "credential_ref" not in prompt.lower() or "Credential refs" in prompt
    # Secrets must not appear (only ref names)
    assert inp.credential_refs[0] in prompt  # ref name appears
    # But no secret value (none in inputs)
    assert "ConnectorSpec" in prompt


def test_build_one_shot_prompt_with_docs_text() -> None:
    inp = _inputs()
    prompt = build_one_shot_prompt(inp, docs_text="VENDOR DOCS HERE")
    assert "VENDOR DOCS HERE" in prompt


def test_build_one_shot_prompt_empty_docs() -> None:
    inp = _inputs()
    prompt = build_one_shot_prompt(inp, docs_text="")
    assert "atlas_basic" in prompt


def test_build_one_shot_prompt_no_secret_values() -> None:
    # Ensure prompt contains only credential refs, never values
    inp = _inputs()
    prompt = build_one_shot_prompt(inp)
    # Our test env doesn't have secret values, but we can check that any env value is not leaked
    # e.g., if ATLAS_API_KEY env set, it shouldn't appear
    import os

    val = os.environ.get("ATLAS_API_KEY", "")
    if val:
        assert val not in prompt


def test_build_one_shot_prompt_runtime_target() -> None:
    inp = _inputs()
    prompt = build_one_shot_prompt(inp)
    assert "runtime_target" in prompt.lower() or "docker_sandbox" in prompt


def test_build_one_shot_prompt_deterministic() -> None:
    inp1 = _inputs()
    inp2 = _inputs()
    assert build_one_shot_prompt(inp1) == build_one_shot_prompt(inp2)


# ---------------------------------------------------------------------------
# extract_spec_yaml
# ---------------------------------------------------------------------------


def test_extract_spec_yaml_bare() -> None:
    txt = "name: test\nversion: 1"
    assert extract_spec_yaml(txt) == txt.strip()


def test_extract_spec_yaml_fenced() -> None:
    txt = "here is yaml:\n```yaml\nname: foo\nversion: 1\n```\ndone"
    assert extract_spec_yaml(txt) == "name: foo\nversion: 1"


def test_extract_spec_yaml_fence_without_lang() -> None:
    txt = "```\nname: bar\n```"
    assert extract_spec_yaml(txt) == "name: bar"


def test_extract_spec_yaml_multiple_fences_prefers_colon() -> None:
    txt = "```yaml\nnot yaml\n```\n```yaml\nname: real\nkey: val\n```"
    out = extract_spec_yaml(txt)
    assert "name: real" in out


def test_extract_spec_yaml_empty() -> None:
    assert extract_spec_yaml("") == ""
    assert extract_spec_yaml("   ") == ""


def test_extract_spec_yaml_no_fence_but_yaml() -> None:
    txt = "api_version: v1\nkind: Connector"
    assert extract_spec_yaml(txt) == txt


def test_extract_spec_yaml_fence_case_insensitive() -> None:
    txt = "```YAML\nname: foo\n```"
    assert extract_spec_yaml(txt) == "name: foo"


# ---------------------------------------------------------------------------
# try_parse_spec_yaml
# ---------------------------------------------------------------------------


def test_try_parse_spec_yaml_valid() -> None:
    txt = "name: test\nversion: 1"
    d = try_parse_spec_yaml(txt)
    assert d == {"name": "test", "version": 1}


def test_try_parse_spec_yaml_fenced() -> None:
    txt = "```yaml\nname: foo\nversion: 2\n```"
    d = try_parse_spec_yaml(txt)
    assert d == {"name": "foo", "version": 2}


def test_try_parse_spec_yaml_invalid_returns_none() -> None:
    txt = "not: [yaml: : :"
    assert try_parse_spec_yaml(txt) is None


def test_try_parse_spec_yaml_non_dict_returns_none() -> None:
    txt = "- item1\n- item2"
    assert try_parse_spec_yaml(txt) is None


def test_try_parse_spec_yaml_empty_returns_none() -> None:
    assert try_parse_spec_yaml("") is None
    assert try_parse_spec_yaml("   ") is None


def test_try_parse_spec_yaml_nested() -> None:
    txt = "a:\n  b:\n    c: 1\n"
    d = try_parse_spec_yaml(txt)
    assert d == {"a": {"b": {"c": 1}}}


# ---------------------------------------------------------------------------
# OneShotResult
# ---------------------------------------------------------------------------


def test_one_shot_result_total_tokens() -> None:
    r = OneShotResult(usage=Usage(input_tokens=5, output_tokens=7))
    assert r.total_tokens == 12


def test_one_shot_result_defaults() -> None:
    r = OneShotResult()
    assert r.text == ""
    assert r.spec_yaml == ""
    assert r.spec_dict is None
    assert r.total_tokens == 0


# ---------------------------------------------------------------------------
# OneShotGenerator
# ---------------------------------------------------------------------------


def test_one_shot_generator_default_provider() -> None:
    gen = OneShotGenerator()
    assert gen.provider is not None
    assert gen.budget is None
    assert gen.tier == "frontier"


def test_one_shot_generator_custom_provider_and_budget() -> None:
    prov = FakeProvider()
    budget = Budget(max_frontier_tokens=1000)
    gen = OneShotGenerator(provider=prov, budget=budget, tier="small", max_tokens=100)
    assert gen.provider is prov
    assert gen.budget is budget
    assert gen.tier == "small"
    assert gen.max_tokens == 100


def test_one_shot_generator_run_success() -> None:
    prov = _fake_provider("name: foo\nversion: 1")
    gen = OneShotGenerator(provider=prov)
    res = gen.run("make a spec")
    assert res.text == "name: foo\nversion: 1"
    assert res.spec_yaml == "name: foo\nversion: 1"
    assert res.spec_dict == {"name": "foo", "version": 1}
    assert res.usage.total_tokens == 30
    assert res.model == "fake-frontier"
    assert res.cost_usd is not None
    assert res.duration_ms >= 0
    assert prov.calls[0]["purpose"] == "bench-one-shot"


def test_one_shot_generator_run_with_fenced_yaml() -> None:
    prov = _fake_provider("```yaml\nname: bar\n```")
    gen = OneShotGenerator(provider=prov)
    res = gen.run("prompt")
    assert res.spec_yaml == "name: bar"
    assert res.spec_dict == {"name": "bar"}


def test_one_shot_generator_run_with_budget() -> None:
    prov = _fake_provider("name: x\n", usage=Usage(input_tokens=5, output_tokens=5))
    budget = Budget(max_frontier_tokens=100)
    gen = OneShotGenerator(provider=prov, budget=budget)
    res = gen.run("hi")
    assert res.total_tokens == 10
    assert budget.frontier_tokens == 10


def test_one_shot_generator_budget_exhausted_raises_claude_error() -> None:
    from bench.baseline_coding_agent import ClaudeCliError

    prov = _fake_provider("hello", usage=Usage(input_tokens=100, output_tokens=100))
    budget = Budget(max_frontier_tokens=10)
    gen = OneShotGenerator(provider=prov, budget=budget)
    with pytest.raises(ClaudeCliError, match="OneShotGenerator failed"):
        gen.run("prompt")
    # Cause should be BudgetExhausted
    try:
        gen.run("prompt2")
    except ClaudeCliError as e:
        assert isinstance(e.__cause__, BudgetExhausted)


def test_one_shot_generator_custom_model() -> None:
    prov = _fake_provider("hi")
    gen = OneShotGenerator(provider=prov, model="my-model")
    # But provider's model_for_tier overrides custom when provider has method
    # Our fake has model_for_tier, so custom is fallback only if provider lacks
    res = gen.run("p")
    # Should be provider's frontier model
    assert res.model == "fake-frontier"


def test_one_shot_generator_small_tier_model() -> None:
    prov = _fake_provider("hi")
    gen = OneShotGenerator(provider=prov, tier="small")
    res = gen.run("p")
    assert res.model == "fake-small"


def test_one_shot_generator_no_model_for_tier_fallback() -> None:
    class NoModelProvider:
        def complete(self, messages, max_tokens=1024, tier="small", purpose="", tools=None):  # noqa: ARG002
            return Completion(text="name: a\n", usage=Usage(input_tokens=1, output_tokens=1))

    gen = OneShotGenerator(provider=NoModelProvider(), model="fallback-model")
    res = gen.run("prompt")
    assert res.model == "fallback-model"


def test_one_shot_generator_docs_loader() -> None:
    prov = _fake_provider("name: from_docs\n")
    loader = lambda inp: "DOCS CONTENT FOR " + inp.scenario_id  # noqa: E731
    gen = OneShotGenerator(provider=prov, docs_loader=loader)
    inp = _inputs(scenario_id="my_scenario")
    res = gen.generate(inp)
    # Prompt should contain docs content
    assert "DOCS CONTENT FOR my_scenario" in gen.calls[0]["prompt"]
    assert res.text == "name: from_docs\n"


def test_one_shot_generator_docs_loader_exception_falls_back() -> None:
    def bad_loader(inp):  # noqa: ARG001
        raise RuntimeError("loader fail")

    prov = _fake_provider("name: ok\n")
    gen = OneShotGenerator(provider=prov, docs_loader=bad_loader)
    inp = _inputs()
    res = gen.generate(inp)
    assert res.text == "name: ok\n"


def test_one_shot_generator_load_docs_exception_handled() -> None:
    # load_docs_text may raise but _build_prompt catches
    prov = _fake_provider("hi")
    gen = OneShotGenerator(provider=prov, docs_loader=None)
    # Patch load_docs_text to raise
    import bench.one_shot as mod

    orig = mod.load_docs_text
    try:
        mod.load_docs_text = lambda _inp, _max_chars=120_000: (_ for _ in ()).throw(RuntimeError("boom"))  # type: ignore
        inp = _inputs()
        res = gen.generate(inp)
        assert res.text == "hi"
    finally:
        mod.load_docs_text = orig


def test_one_shot_generator_generate_builds_prompt() -> None:
    prov = _fake_provider("name: gen\n")
    gen = OneShotGenerator(provider=prov)
    inp = _inputs(scenario_id="gen_test")
    res = gen.generate(inp)
    assert "gen_test" in gen.last_prompt
    assert res.spec_dict == {"name": "gen"}


def test_one_shot_generator_last_prompt() -> None:
    prov = _fake_provider("a")
    gen = OneShotGenerator(provider=prov)
    assert gen.last_prompt is None
    gen.run("first prompt")
    assert gen.last_prompt == "first prompt"
    gen.run("second")
    assert gen.last_prompt == "second"


def test_one_shot_generator_run_cwd() -> None:
    prov = _fake_provider("yaml: 1\n")
    gen = OneShotGenerator(provider=prov)
    res = gen.run("prompt", cwd=pathlib.Path("/tmp"))
    assert gen.calls[0]["cwd"] == "/tmp"
    assert res.text == "yaml: 1\n"


def test_one_shot_generator_run_provider_exception() -> None:
    from bench.baseline_coding_agent import ClaudeCliError

    class BadProvider:
        def complete(self, *a, **kw):  # noqa: ANN002, ANN003, ARG002
            raise RuntimeError("provider down")

    gen = OneShotGenerator(provider=BadProvider())
    with pytest.raises(ClaudeCliError):
        gen.run("hi")


# ---------------------------------------------------------------------------
# run_one_shot_arm
# ---------------------------------------------------------------------------


def test_run_one_shot_arm_success() -> None:
    prov = _fake_provider("name: spec1\nversion: 1")
    inp = _inputs()
    arm = run_one_shot_arm(inp, provider=prov)
    assert arm.arm == "one_shot"
    assert arm.ran is True
    assert arm.inputs == inp
    assert arm.spec == {"name": "spec1", "version": 1}
    assert arm.input_tokens == 10
    assert arm.output_tokens == 20
    assert arm.frontier_tokens == 30
    assert arm.cost_usd is not None
    assert arm.model == "fake-frontier"


def test_run_one_shot_arm_no_spec_dict() -> None:
    prov = _fake_provider("just some text with no yaml")
    inp = _inputs()
    arm = run_one_shot_arm(inp, provider=prov)
    assert arm.ran is True
    assert arm.spec is None


def test_run_one_shot_arm_default_budget_from_inputs() -> None:
    prov = _fake_provider("name: x\n", usage=Usage(input_tokens=2, output_tokens=3))
    inp = _inputs(token_ceiling=1000, wall_clock_s=10.0)
    arm = run_one_shot_arm(inp, provider=prov)
    assert arm.ran is True
    assert arm.frontier_tokens == 5


def test_run_one_shot_arm_small_tier_no_frontier_tokens() -> None:
    prov = _fake_provider("name: x\n")
    inp = _inputs()
    arm = run_one_shot_arm(inp, provider=prov, tier="small")
    assert arm.frontier_tokens == 0  # small tier doesn't count as frontier


def test_run_one_shot_arm_custom_model() -> None:
    # Provider without model_for_tier override, use custom model
    class P:
        def complete(self, messages, max_tokens=1024, tier="small", purpose="", tools=None):  # noqa: ARG002
            return Completion(text="name: a\n", usage=Usage(1, 1))

    inp = _inputs()
    arm = run_one_shot_arm(inp, provider=P(), model="custom-model")
    assert arm.model == "custom-model"


def test_run_one_shot_arm_failure_provider() -> None:
    class Bad:
        def complete(self, *a, **kw):  # noqa: ANN002, ANN003, ARG002
            raise RuntimeError("oops")

    inp = _inputs()
    arm = run_one_shot_arm(inp, provider=Bad())
    assert arm.ran is False
    assert "RuntimeError" in arm.error or "oops" in arm.error


def test_run_one_shot_arm_budget_exhausted() -> None:
    prov = _fake_provider("hello", usage=Usage(input_tokens=500, output_tokens=500))
    budget = Budget(max_frontier_tokens=10)
    inp = _inputs()
    arm = run_one_shot_arm(inp, provider=prov, budget=budget)
    assert arm.ran is False
    assert "BudgetExhausted" in arm.error


def test_run_one_shot_arm_docs_loader() -> None:
    prov = _fake_provider("name: docload\n")
    inp = _inputs(scenario_id="s1")
    arm = run_one_shot_arm(inp, provider=prov, docs_loader=lambda x: "DOCS " + x.scenario_id)
    assert arm.ran is True
    assert arm.spec == {"name": "docload"}


def test_run_one_shot_arm_with_explicit_budget() -> None:
    prov = _fake_provider("a: 1\n", usage=Usage(1, 1))
    budget = Budget(max_frontier_tokens=100)
    inp = _inputs()
    arm = run_one_shot_arm(inp, provider=prov, budget=budget)
    assert arm.ran
    assert budget.frontier_tokens == 2


# ---------------------------------------------------------------------------
# run_trial_with_one_shot
# ---------------------------------------------------------------------------


def test_run_trial_with_one_shot_basic() -> None:
    cfg = HarnessConfig(seed=42)
    trial = run_trial_with_one_shot(cfg, "atlas_basic", 0)
    assert trial.scenario_id == "atlas_basic"
    assert trial.trial == 0
    assert trial.matched is True
    assert trial.matched_error is None
    assert trial.autobridge.arm == "autobridge"
    assert trial.baseline.arm == "baseline"
    # One-shot attached dynamically
    assert hasattr(trial, "one_shot")
    assert trial.one_shot.arm == "one_shot"
    # All three inputs equal
    assert trial.autobridge.inputs == trial.baseline.inputs
    assert trial.autobridge.inputs == trial.one_shot.inputs


def test_run_trial_with_one_shot_custom_runners() -> None:
    from bench.harness import ArmResult

    cfg = HarnessConfig(seed=1)

    def ab_runner(inp):
        return ArmResult(arm="autobridge", inputs=inp, ran=True, model="ab")

    def bl_runner(inp):
        return ArmResult(arm="baseline", inputs=inp, ran=True, model="bl")

    def os_runner(inp):
        return ArmResult(arm="one_shot", inputs=inp, ran=True, model="os")

    trial = run_trial_with_one_shot(cfg, "atlas_basic", 0, autobridge_runner=ab_runner, baseline_runner=bl_runner, one_shot_runner=os_runner)
    assert trial.autobridge.model == "ab"
    assert trial.baseline.model == "bl"
    assert trial.one_shot.model == "os"


def test_run_trial_with_one_shot_provider_override() -> None:
    cfg = HarnessConfig(seed=2)
    prov = _fake_provider("name: provider_spec\n")
    trial = run_trial_with_one_shot(cfg, "harbor_basic", 1, one_shot_provider=prov)
    assert trial.one_shot.ran is True
    assert trial.one_shot.spec == {"name": "provider_spec"}


def test_run_trial_with_one_shot_empty_scenario_raises() -> None:
    cfg = HarnessConfig()
    with pytest.raises(ValueError, match="scenario_id"):
        run_trial_with_one_shot(cfg, "", 0)


def test_run_trial_with_one_shot_default_config() -> None:
    trial = run_trial_with_one_shot(scenario_id="atlas_basic", trial=0)
    assert trial.matched is True


def test_run_trial_with_one_shot_same_seed_deterministic() -> None:
    cfg = HarnessConfig(seed=99)
    t1 = run_trial_with_one_shot(cfg, "s1", 0)
    t2 = run_trial_with_one_shot(cfg, "s1", 0)
    assert t1.inputs.fingerprint() == t2.inputs.fingerprint()
    assert t1.inputs.seed == t2.inputs.seed


def test_run_trial_with_one_shot_different_trials_different_inputs() -> None:
    cfg = HarnessConfig(seed=55)
    t1 = run_trial_with_one_shot(cfg, "s1", 0)
    t2 = run_trial_with_one_shot(cfg, "s1", 1)
    assert t1.inputs.seed != t2.inputs.seed


# ---------------------------------------------------------------------------
# Matched-budget integration
# ---------------------------------------------------------------------------


def test_one_shot_matched_budget_same_as_harness() -> None:
    from bench.harness import assert_inputs_equal

    cfg = HarnessConfig(seed=123, token_ceiling=8000)
    inp = make_trial_inputs(cfg, "atlas_basic", 0)
    # One-shot arm should receive identical inputs
    prov = _fake_provider("name: x\n")
    arm = run_one_shot_arm(inp, provider=prov)
    # Verify arm's inputs equal original
    assert_inputs_equal(arm.inputs, inp)
    # And budget fields
    from bench.harness import assert_budget_fields_equal

    inp2 = make_trial_inputs(cfg, "atlas_basic", 0)
    assert_budget_fields_equal(arm.inputs, inp2)


def test_one_shot_vs_baseline_same_inputs() -> None:
    from bench.harness import run_baseline_arm

    cfg = HarnessConfig(seed=7)
    inp = make_trial_inputs(cfg, "s1", 0)
    prov_one = _fake_provider("name: one\n")
    arm_one = run_one_shot_arm(inp, provider=prov_one)
    # Baseline with same inputs
    from bench.baseline_coding_agent import FakeClaudeDriver

    driver = FakeClaudeDriver(default_text="name: baseline\n")
    arm_base = run_baseline_arm(inp, driver=driver)
    assert arm_one.inputs == arm_base.inputs


# ---------------------------------------------------------------------------
# Factory integration
# ---------------------------------------------------------------------------


def test_get_baseline_driver_one_shot() -> None:
    driver = get_baseline_driver("one_shot")
    assert isinstance(driver, OneShotGenerator)
    assert driver.tier == "frontier"


def test_get_baseline_driver_one_shot_variants() -> None:
    for kind in ("one_shot", "oneshot", "one-shot", "ONE_SHOT"):
        d = get_baseline_driver(kind)
        assert isinstance(d, OneShotGenerator), kind


def test_get_baseline_driver_one_shot_with_provider() -> None:
    prov = FakeProvider()
    d = get_baseline_driver("one_shot", provider=prov)
    assert d.provider is prov


# ---------------------------------------------------------------------------
# healthz
# ---------------------------------------------------------------------------


def test_healthz() -> None:
    h = healthz()
    assert h["status"] == "ok"
    assert "one_shot" in h["service"]


def test_harness_arm_literal_includes_one_shot() -> None:
    # Verify harness now accepts one_shot arm
    from bench.harness import ArmResult

    inp = _inputs()
    arm = ArmResult(arm="one_shot", inputs=inp, ran=True)
    assert arm.arm == "one_shot"
    assert arm.to_dict()["arm"] == "one_shot"


# ---------------------------------------------------------------------------
# Secrets never in prompt
# ---------------------------------------------------------------------------


def test_one_shot_prompt_never_contains_secret_values() -> None:
    # Create a fake env var that looks like a credential value
    import os

    os.environ["FAKE_SECRET_FOR_TEST"] = "supersecretvalue123"
    inp = _inputs()
    # The prompt built should contain credential refs (env key names) but not values
    prompt = build_one_shot_prompt(inp, docs_text=None)
    assert "supersecretvalue123" not in prompt
    assert inp.credential_refs[0] in prompt
    # Cleanup
    del os.environ["FAKE_SECRET_FOR_TEST"]


def test_reexport_via_fake_cli() -> None:
    # Ensure fake_cli re-exports work
    from bench.fake_cli import build_one_shot_prompt as bosp
    from bench.fake_cli import extract_spec_yaml as esy
    from bench.fake_cli import run_one_shot_arm as rosa

    assert bosp is build_one_shot_prompt
    assert esy is extract_spec_yaml
    assert rosa is run_one_shot_arm


def test_oneshot_vendor_scoped_docs_fit_shared_budget() -> None:
    """REQ-028: the whole-volume docs fallback blows the shared token
    ceiling (the old ``frontier_oneshot`` failure); the ablation arm scopes
    the prompt to the one vendor docs file the engine arms read, which fits.
    A prompt-measuring fake (chars/4 usage, like a real provider accounts)
    proves the mechanism without a frontier key."""
    import bench.ablation as ab
    from bench.one_shot import load_docs_text as _volume_docs
    from bench.one_shot import run_one_shot_arm as _run
    from bench.one_shot import vendor_docs_loader

    def _metered(messages: Any, max_tokens: int = 4096, tier: str = "frontier", purpose: str = "", tools: Any = None) -> Completion:
        _ = (max_tokens, tier, purpose, tools)  # metered by prompt size only
        chars = sum(len(str(m.get("content", ""))) for m in messages)
        return Completion(text="name: scoped\n", usage=Usage(input_tokens=chars // 4, output_tokens=50))

    metered = FakeProvider(responses={"default": _metered}, frontier_model="fake-frontier", small_model="fake-small")
    case = ab.ablation_case_for_scenario("rename_guest_id")
    # REQ-060: the default ceiling is 0 (no limit); this legacy-budget
    # test pins an explicit ceiling to prove the exhaustion mechanism.
    cfg = HarnessConfig(token_ceiling=20000)
    inp = make_trial_inputs(cfg, "rename_guest_id", 0)
    assert inp.token_ceiling == 20000
    whole = _volume_docs(inp)
    assert len(whole) > 50000, "volume fallback must concatenate the whole docs dir (the old failure)"
    whole_arm = _run(inp, provider=FakeProvider(responses={"default": _metered}), docs_loader=lambda _i: whole)
    assert whole_arm.ran is False
    assert "BudgetExhausted" in (whole_arm.error or ""), whole_arm.error
    assert "atlaspms-openapi" in case.docs_ref
    scoped_arm = _run(inp, provider=metered, docs_loader=vendor_docs_loader(case.docs_ref))
    assert scoped_arm.ran is True, scoped_arm.error
    # The loader reads the same file the engine arms read for this connector.
    assert "openapi" in vendor_docs_loader(case.docs_ref)(inp).lower() or len(vendor_docs_loader(case.docs_ref)(inp)) > 1000
