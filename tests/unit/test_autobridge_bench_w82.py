"""Tests for P1.W8.2 baseline drivers — codex adapter + fallback (plan §2.10, §9)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from autobridge.llm.provider import Budget, Completion, FakeProvider, Usage
from bench.baseline_coding_agent import (
    BaselineLLMDriver,
    ClaudeCliError,
    ClaudeCodeDriver,
    CodexCliDriver,
    CodexCliError,
    FakeCodexDriver,
    build_fake_codex_output,
    get_baseline_driver,
    parse_codex_output,
)
from bench.harness import HarnessConfig, make_trial_inputs, run_baseline_arm

# ---------------------------------------------------------------------------
# parse_codex_output — JSON shapes
# ---------------------------------------------------------------------------


def test_parse_codex_empty() -> None:
    r = parse_codex_output("")
    assert r.text == ""
    assert r.total_tokens == 0


def test_parse_codex_single_output() -> None:
    s = build_fake_codex_output("my text", input_tokens=10, output_tokens=20, model="codex-test")
    r = parse_codex_output(s)
    assert r.text == "my text"
    assert r.usage.input_tokens == 10
    assert r.usage.output_tokens == 20
    assert r.model == "codex-test"
    assert r.total_tokens == 30


def test_parse_codex_result_shape() -> None:
    payload = json.dumps({"result": "via result", "usage": {"input_tokens": 5, "output_tokens": 5}})
    r = parse_codex_output(payload)
    assert r.text == "via result"


def test_parse_codex_content_array() -> None:
    payload = json.dumps({"content": [{"type": "text", "text": "hello "}, {"type": "text", "text": "world"}], "usage": {"input_tokens": 1, "output_tokens": 1}})
    r = parse_codex_output(payload)
    assert r.text == "hello world"


def test_parse_codex_tokens_alt_shape() -> None:
    payload = json.dumps({"output": "hi", "tokens": {"prompt": 7, "completion": 3}})
    r = parse_codex_output(payload)
    assert r.usage.input_tokens == 7
    assert r.usage.output_tokens == 3


def test_parse_codex_cost() -> None:
    s = build_fake_codex_output("x", cost_usd=0.02)
    r = parse_codex_output(s)
    assert r.cost_usd == Decimal("0.02")


def test_parse_codex_cost_via_cost_key() -> None:
    payload = json.dumps({"output": "hi", "usage": {"input_tokens": 1, "output_tokens": 1}, "cost": 0.005})
    r = parse_codex_output(payload)
    assert r.cost_usd == Decimal("0.005")


def test_parse_codex_usage_invalid() -> None:
    payload = json.dumps({"output": "hi", "usage": {"input_tokens": "bad", "output_tokens": None}})
    r = parse_codex_output(payload)
    assert r.text == "hi"
    assert r.total_tokens == 0


def test_parse_codex_negative_clamped() -> None:
    payload = json.dumps({"output": "hi", "usage": {"input_tokens": -5, "output_tokens": -2}})
    r = parse_codex_output(payload)
    assert r.usage.input_tokens == 0
    assert r.usage.output_tokens == 0


def test_parse_codex_line_delimited_last_wins() -> None:
    line1 = json.dumps({"output": "first", "usage": {"input_tokens": 1, "output_tokens": 1}})
    line2 = json.dumps({"output": "second", "usage": {"input_tokens": 10, "output_tokens": 20}})
    r = parse_codex_output(f"not json\n{line1}\n{line2}\n")
    assert r.text == "second"
    assert r.total_tokens == 30


def test_parse_codex_plain_text_fallback() -> None:
    r = parse_codex_output("plain text no json")
    assert r.text == "plain text no json"
    assert r.total_tokens == 0


def test_parse_codex_unknown_shape() -> None:
    payload = json.dumps({"foo": "bar", "usage": {"input_tokens": 1, "output_tokens": 1}})
    r = parse_codex_output(payload)
    # Unknown shape returns raw JSON as text
    assert r.text == payload


def test_build_fake_codex_output() -> None:
    s = build_fake_codex_output("hello", input_tokens=1, output_tokens=2, model="m", cost_usd="0.01")
    data = json.loads(s)
    assert data["output"] == "hello"
    assert data["usage"]["input_tokens"] == 1
    assert data["cost_usd"] == "0.01"
    assert data["model"] == "m"


def test_build_fake_codex_output_no_cost() -> None:
    s = build_fake_codex_output("hello")
    data = json.loads(s)
    assert "cost_usd" not in data


# ---------------------------------------------------------------------------
# CodexCliDriver — construction
# ---------------------------------------------------------------------------


def test_codex_driver_defaults() -> None:
    d = CodexCliDriver()
    assert d.cli_path == "codex"
    assert d.timeout_s == 120.0
    assert d.last_prompt is None


def test_codex_driver_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_CLI_PATH", "/tmp/fake_codex")
    d = CodexCliDriver()
    assert d.cli_path == "/tmp/fake_codex"


def test_codex_driver_explicit_path_wins_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_CLI_PATH", "/tmp/env_path")
    d = CodexCliDriver(cli_path="/tmp/explicit")
    assert d.cli_path == "/tmp/explicit"


def test_codex_driver_build_command() -> None:
    d = CodexCliDriver(cli_path="codex", model="my-model", extra_args=["--verbose"])
    cmd = d._build_command("hello")
    assert cmd == ["codex", "exec", "--json", "hello", "--model", "my-model", "--verbose"]


def test_codex_driver_build_command_no_model() -> None:
    d = CodexCliDriver(cli_path="codex")
    assert "--model" not in d._build_command("hi")


# ---------------------------------------------------------------------------
# CodexCliDriver — subprocess via monkeypatch
# ---------------------------------------------------------------------------


def test_codex_driver_run_success_with_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_stdout = build_fake_codex_output("generated spec", input_tokens=100, output_tokens=200, model="codex-test")
    monkeypatch.setattr(
        "bench.baseline_coding_agent.subprocess.run",
        lambda *a, **_kw: subprocess.CompletedProcess(args=a[0], returncode=0, stdout=fake_stdout, stderr=""),
    )
    d = CodexCliDriver(cli_path="codex")
    res = d.run("make a spec")
    assert res.text == "generated spec"
    assert res.total_tokens == 300
    assert res.model == "codex-test"
    assert res.duration_ms >= 0
    assert d.last_prompt == "make a spec"
    assert d.calls[0]["prompt"] == "make a spec"


def test_codex_driver_run_nonzero_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "bench.baseline_coding_agent.subprocess.run",
        lambda *a, **_kw: subprocess.CompletedProcess(args=a[0], returncode=1, stdout="out", stderr="bad prompt"),
    )
    d = CodexCliDriver(cli_path="codex")
    with pytest.raises(CodexCliError, match="failed with exit 1"):
        d.run("prompt")


def test_codex_driver_run_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*a: object, **_kw: object) -> subprocess.CompletedProcess[str]:  # noqa: ARG001
        raise subprocess.TimeoutExpired(cmd="codex", timeout=1, output="o", stderr="e")

    monkeypatch.setattr("bench.baseline_coding_agent.subprocess.run", fake_run)
    d = CodexCliDriver(cli_path="codex", timeout_s=1)
    with pytest.raises(CodexCliError, match="timed out"):
        d.run("prompt")


def test_codex_driver_run_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*a: object, **_kw: object) -> subprocess.CompletedProcess[str]:  # noqa: ARG001
        raise FileNotFoundError("no such file")

    monkeypatch.setattr("bench.baseline_coding_agent.subprocess.run", fake_run)
    d = CodexCliDriver(cli_path="codex-not-exist")
    with pytest.raises(CodexCliError, match="not found"):
        d.run("prompt")
    # Should mention fallback
    try:
        d.run("prompt")
    except CodexCliError as e:
        assert "fallback" in str(e).lower() or "LLMProvider" in str(e)


def test_codex_driver_custom_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["timeout"] = kwargs.get("timeout")
        fake_stdout = build_fake_codex_output("ok")
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout=fake_stdout, stderr="")

    monkeypatch.setattr("bench.baseline_coding_agent.subprocess.run", fake_run)
    d = CodexCliDriver(cli_path="codex", timeout_s=10)
    d.run("hi", timeout_s=5)
    assert captured["timeout"] == 5


def test_codex_driver_is_codex_error_subclass() -> None:
    assert issubclass(CodexCliError, ClaudeCliError)
    e = CodexCliError("msg", exit_code=1, stderr="err", stdout="out")
    assert e.exit_code == 1


# ---------------------------------------------------------------------------
# FakeCodexDriver
# ---------------------------------------------------------------------------


def test_fake_codex_driver_default() -> None:
    f = FakeCodexDriver()
    assert f.last_prompt is None
    res = f.run("hello")
    assert res.text == "fake codex response"
    assert res.total_tokens == 30  # 10+20 default
    assert f.last_prompt == "hello"
    assert len(f.calls) == 1


def test_fake_codex_driver_direct_hit() -> None:
    f = FakeCodexDriver(responses={"hello": "world"})
    res = f.run("hello")
    assert res.text == "world"


def test_fake_codex_driver_substring() -> None:
    f = FakeCodexDriver(responses={"spec": "custom spec"})
    res = f.run("make a spec for harbor")
    assert res.text == "custom spec"


def test_fake_codex_driver_result_object() -> None:
    canned = ClaudeCodeDriver  # noqa: F841 (import check)
    from bench.baseline_coding_agent import ClaudeCliResult

    canned_res = ClaudeCliResult(text="canned", usage=Usage(5, 5), model="m")
    f = FakeCodexDriver(responses={"hi": canned_res})
    res = f.run("hi there")
    assert res.text == "canned"
    assert res.usage.input_tokens == 5


def test_fake_codex_driver_callable_returns_result() -> None:
    from bench.baseline_coding_agent import ClaudeCliResult

    def fn(prompt: str) -> ClaudeCliResult:
        return ClaudeCliResult(text=f"echo:{prompt}", usage=Usage(1, 1))

    f = FakeCodexDriver(responses={"echo": fn})
    res = f.run("echo this")
    assert res.text == "echo:echo this"


def test_fake_codex_driver_callable_returns_string() -> None:
    def fn(prompt: str) -> str:
        return f"str:{prompt}"

    f = FakeCodexDriver(responses={"echo": fn})
    res = f.run("echo this")
    assert res.text == "str:echo this"


def test_fake_codex_driver_string_coercion() -> None:
    f = FakeCodexDriver(responses={"a": 123})
    res = f.run("a prompt")
    assert res.text == "123"


def test_fake_codex_driver_custom_usage() -> None:
    f = FakeCodexDriver(default_usage=Usage(1, 2), default_model="my-model")
    res = f.run("hi")
    assert res.usage.input_tokens == 1
    assert res.model == "my-model"


def test_fake_codex_driver_cwd_captured() -> None:
    f = FakeCodexDriver()
    f.run("hi", cwd=Path("/tmp"))
    assert f.calls[0]["cwd"] == "/tmp"


# ---------------------------------------------------------------------------
# BaselineLLMDriver — fallback via LLMProvider (plan §9)
# ---------------------------------------------------------------------------


def test_baseline_llm_driver_default_fake() -> None:
    d = BaselineLLMDriver()
    assert d.last_prompt is None
    res = d.run("hello prompt")
    assert "fake response" in res.text
    assert res.total_tokens == 30  # FakeProvider default 10+20
    assert d.last_prompt == "hello prompt"
    assert len(d.calls) == 1
    assert res.duration_ms >= 0
    assert res.cost_usd is not None  # estimate_cost from tier frontier


def test_baseline_llm_driver_with_fake_provider() -> None:
    provider = FakeProvider(responses={"frontier": Completion(text="spec yaml", usage=Usage(5, 5))})
    d = BaselineLLMDriver(provider=provider, tier="frontier", max_tokens=100)
    res = d.run("make spec")
    assert res.text == "spec yaml"
    assert res.usage.input_tokens == 5
    assert provider.calls[0]["purpose"] == "bench-baseline-llm-fallback"
    assert provider.calls[0]["max_tokens"] == 100
    assert provider.calls[0]["tier"] == "frontier"


def test_baseline_llm_driver_small_tier() -> None:
    provider = FakeProvider(responses={"small": Completion(text="small spec", usage=Usage(2, 3))})
    d = BaselineLLMDriver(provider=provider, tier="small")
    res = d.run("hi")
    assert res.text == "small spec"
    assert res.usage.output_tokens == 3


def test_baseline_llm_driver_with_budget() -> None:
    provider = FakeProvider()
    budget = Budget(max_frontier_tokens=1000, max_calls=10, max_wall=60)
    d = BaselineLLMDriver(provider=provider, budget=budget, tier="frontier")
    res = d.run("hello")
    assert res.text  # success
    assert budget.calls == 1
    assert budget.frontier_tokens == res.total_tokens


def test_baseline_llm_driver_budget_exhausted() -> None:
    provider = FakeProvider()
    budget = Budget(max_calls=0)  # will exhaust pre-call
    d = BaselineLLMDriver(provider=provider, budget=budget)
    with pytest.raises(ClaudeCliError, match="BaselineLLMDriver failed"):
        d.run("hello")


def test_baseline_llm_driver_provider_error() -> None:
    class BadProvider:
        def complete(self, messages, **kw):  # noqa: ARG001, ARG002
            raise RuntimeError("provider down")

    d = BaselineLLMDriver(provider=BadProvider())
    with pytest.raises(ClaudeCliError, match="provider down"):
        d.run("hi")


def test_baseline_llm_driver_model_resolution() -> None:
    provider = FakeProvider(frontier_model="my-frontier", small_model="my-small")
    d = BaselineLLMDriver(provider=provider, tier="frontier")
    res = d.run("hi")
    assert res.model == "my-frontier"

    d2 = BaselineLLMDriver(provider=provider, tier="small")
    res2 = d2.run("hi")
    assert res2.model == "my-small"


def test_baseline_llm_driver_custom_model() -> None:
    _ = BaselineLLMDriver(provider=FakeProvider(), model="custom-model")
    # custom model used when provider has no model_for_tier
    class NoModelProvider:
        provider_name = "no-model"

        def complete(self, messages, **kw):  # noqa: ARG001, ARG002
            return Completion(text="hi", usage=Usage(1, 1))

    d2 = BaselineLLMDriver(provider=NoModelProvider(), model="custom-model")
    res = d2.run("hi")
    assert res.model == "custom-model"


def test_baseline_llm_driver_cwd() -> None:
    d = BaselineLLMDriver()
    d.run("hi", cwd=Path("/tmp/codex"))
    assert d.calls[0]["cwd"] == "/tmp/codex"


def test_baseline_llm_driver_cost_estimate() -> None:
    provider = FakeProvider(responses={"frontier": Completion(text="x", usage=Usage(100, 100))})
    d = BaselineLLMDriver(provider=provider, tier="frontier")
    res = d.run("hi")
    # frontier rate 0.015 /1k, total 200 => 0.003
    assert res.cost_usd is not None
    assert res.cost_usd == Decimal("0.003000")


# ---------------------------------------------------------------------------
# get_baseline_driver factory
# ---------------------------------------------------------------------------


def test_get_baseline_driver_claude() -> None:
    d = get_baseline_driver("claude")
    assert isinstance(d, ClaudeCodeDriver)

    d2 = get_baseline_driver("claude_code_headless")
    assert isinstance(d2, ClaudeCodeDriver)

    d3 = get_baseline_driver("CLAUDE")  # case insensitive
    assert isinstance(d3, ClaudeCodeDriver)


def test_get_baseline_driver_codex() -> None:
    d = get_baseline_driver("codex")
    assert isinstance(d, CodexCliDriver)

    d2 = get_baseline_driver("codex_cli")
    assert isinstance(d2, CodexCliDriver)


def test_get_baseline_driver_llm_fallback() -> None:
    d = get_baseline_driver("llm")
    assert isinstance(d, BaselineLLMDriver)

    d2 = get_baseline_driver("fallback")
    assert isinstance(d2, BaselineLLMDriver)

    d3 = get_baseline_driver("provider")
    assert isinstance(d3, BaselineLLMDriver)


def test_get_baseline_driver_with_provider() -> None:
    p = FakeProvider()
    d = get_baseline_driver("llm", provider=p)
    assert d.provider is p


def test_get_baseline_driver_with_cli_path() -> None:
    d = get_baseline_driver("claude", cli_path="/my/claude")
    assert d.cli_path == "/my/claude"

    d2 = get_baseline_driver("codex", cli_path="/my/codex")
    assert d2.cli_path == "/my/codex"


def test_get_baseline_driver_unknown() -> None:
    with pytest.raises(ValueError, match="unknown baseline driver kind"):
        get_baseline_driver("unknown")


# ---------------------------------------------------------------------------
# Matched-budget: baseline drivers receive identical HarnessInputs
# ---------------------------------------------------------------------------


def test_harness_with_codex_driver_matched() -> None:
    from bench.harness import run_trial

    cfg = HarnessConfig(sandbox_image="my-image", token_ceiling=5000)
    seen_a: list[dict] = []
    seen_b: list[dict] = []

    def ab_runner(inp):  # type: ignore[no-untyped-def]
        seen_a.append(inp.budget_fields())
        from bench.harness import ArmResult

        return ArmResult(arm="autobridge", inputs=inp, success=True)

    def bl_runner(inp):  # type: ignore[no-untyped-def]
        seen_b.append(inp.budget_fields())
        # Use FakeCodexDriver internally but same inputs
        from bench.harness import ArmResult

        driver = FakeCodexDriver(default_text="codex spec")
        result = driver.run(f"Build spec for {inp.scenario_id} docs {inp.docs_ref}")
        return ArmResult(
            arm="baseline",
            inputs=inp,
            success=True,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            model=result.model,
        )

    trial = run_trial(cfg, "baseline", 0, autobridge_runner=ab_runner, baseline_runner=bl_runner)
    assert trial.matched is True
    assert seen_a[0] == seen_b[0]
    assert seen_a[0]["sandbox_image"] == "my-image"
    assert seen_a[0]["token_ceiling"] == 5000


def test_harness_with_llm_fallback_matched() -> None:
    from bench.harness import ArmResult, run_trial

    cfg = HarnessConfig(token_ceiling=4000, wall_clock_s=99.0)
    provider = FakeProvider()

    def llm_runner(inp):  # type: ignore[no-untyped-def]
        # Simulate harness constructing Budget from inputs for fallback
        budget = Budget(max_frontier_tokens=inp.token_ceiling, max_wall=inp.wall_clock_s)
        driver = BaselineLLMDriver(provider=provider, budget=budget)
        result = driver.run(f"Build spec for {inp.scenario_id}")
        return ArmResult(
            arm="baseline",
            inputs=inp,
            success=True,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            frontier_tokens=result.total_tokens,
            model=result.model,
        )

    trial = run_trial(cfg, "baseline", 0, baseline_runner=llm_runner)
    assert trial.matched is True
    assert trial.baseline.frontier_tokens == 30
    assert trial.baseline.model in ("fake-frontier", "baseline-llm-fallback")


def test_run_baseline_arm_with_codex_driver() -> None:
    cfg = HarnessConfig()
    inp = make_trial_inputs(cfg, "baseline", 0)
    driver = FakeCodexDriver(default_text="codex baseline spec")
    result = run_baseline_arm(inp, driver=driver)
    assert result.arm == "baseline"
    assert result.success is True
    assert result.total_tokens == 30
    assert "baseline" in driver.last_prompt


def test_run_baseline_arm_with_llm_driver() -> None:
    cfg = HarnessConfig()
    inp = make_trial_inputs(cfg, "baseline", 0)
    provider = FakeProvider()
    driver = BaselineLLMDriver(provider=provider)
    result = run_baseline_arm(inp, driver=driver)
    assert result.arm == "baseline"
    assert result.success is True
    assert result.total_tokens == 30


# ---------------------------------------------------------------------------
# fake_codex.py script integration
# ---------------------------------------------------------------------------


def _fake_codex_path() -> Path:
    return Path(__file__).resolve().parents[2] / "scripts" / "fake_codex.py"


def test_fake_codex_script_direct_invocation() -> None:
    script = _fake_codex_path()
    proc = subprocess.run([sys.executable, str(script), "exec", "--json", "hello", "--output-format", "json"], capture_output=True, text=True, timeout=5)
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert "hello" in data["output"]
    assert data["usage"]["input_tokens"] == 10


def test_fake_codex_script_env_mapping() -> None:
    script = _fake_codex_path()
    env = os.environ.copy()
    env["FAKE_CODEX_RESPONSES"] = json.dumps({"hello": "mapped codex response"})
    env["FAKE_CODEX_USAGE"] = "5,7"
    proc = subprocess.run([sys.executable, str(script), "exec", "--json", "hello world"], capture_output=True, text=True, env=env, timeout=5)
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert data["output"] == "mapped codex response"
    assert data["usage"]["input_tokens"] == 5


def test_fake_codex_script_exit_code() -> None:
    script = _fake_codex_path()
    env = os.environ.copy()
    env["FAKE_CODEX_EXIT_CODE"] = "2"
    proc = subprocess.run([sys.executable, str(script), "exec", "--json", "hi"], capture_output=True, text=True, env=env, timeout=5)
    assert proc.returncode == 2


def test_fake_codex_script_cost() -> None:
    script = _fake_codex_path()
    env = os.environ.copy()
    env["FAKE_CODEX_COST"] = "0.03"
    proc = subprocess.run([sys.executable, str(script), "exec", "--json", "hi"], capture_output=True, text=True, env=env, timeout=5)
    data = json.loads(proc.stdout)
    assert data["cost_usd"] == 0.03


def test_fake_codex_script_model() -> None:
    script = _fake_codex_path()
    env = os.environ.copy()
    env["FAKE_CODEX_MODEL"] = "my-fake-codex-model"
    proc = subprocess.run([sys.executable, str(script), "exec", "--json", "hi"], capture_output=True, text=True, env=env, timeout=5)
    data = json.loads(proc.stdout)
    assert data["model"] == "my-fake-codex-model"


# ---------------------------------------------------------------------------
# fake_cli re-export includes new symbols
# ---------------------------------------------------------------------------


def test_fake_cli_reexport_new_symbols() -> None:
    from bench.fake_cli import BaselineLLMDriver as BLD
    from bench.fake_cli import CodexCliDriver as CCD
    from bench.fake_cli import FakeCodexDriver as FCD
    from bench.fake_cli import build_fake_codex_output as BFCO
    from bench.fake_cli import get_baseline_driver as GBD
    from bench.fake_cli import parse_codex_output as PCO

    assert BLD is BaselineLLMDriver
    assert CCD is CodexCliDriver
    assert FCD is FakeCodexDriver
    assert BFCO is build_fake_codex_output
    assert GBD is get_baseline_driver
    assert PCO is parse_codex_output
