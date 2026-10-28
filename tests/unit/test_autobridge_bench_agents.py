"""Tests for coding-agent CLI fakes and driver (P1.W1.9, plan §9)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from autobridge.bench.baseline_coding_agent import (
    ClaudeCliError,
    ClaudeCliResult,
    ClaudeCodeDriver,
    CodexCliDriver,
    CodexCliError,
    FakeClaudeDriver,
    FakeOpenCodeDriver,
    OpenCodeCliDriver,
    OpenCodeCliError,
    build_fake_claude_output,
    build_fake_opencode_output,
    get_baseline_driver,
    parse_claude_output,
    parse_opencode_output,
)
from autobridge.llm.provider import Usage

# ---------------------------------------------------------------------------
# ClaudeCliResult
# ---------------------------------------------------------------------------


def test_claude_cli_result_defaults() -> None:
    r = ClaudeCliResult()
    assert r.text == ""
    assert r.total_tokens == 0
    assert r.model == ""
    assert r.cost_usd is None


def test_claude_cli_result_total_tokens() -> None:
    r = ClaudeCliResult(usage=Usage(input_tokens=5, output_tokens=7))
    assert r.total_tokens == 12


# ---------------------------------------------------------------------------
# build_fake_claude_output
# ---------------------------------------------------------------------------


def test_build_fake_output_with_cost() -> None:
    s = build_fake_claude_output("hi", input_tokens=1, output_tokens=2, model="m", cost_usd="0.01")
    data = json.loads(s)
    assert data["result"] == "hi"
    assert data["usage"]["input_tokens"] == 1
    assert data["cost_usd"] == "0.01"


def test_build_fake_output_no_cost() -> None:
    s = build_fake_claude_output("hello")
    data = json.loads(s)
    assert "cost_usd" not in data
    assert data["model"] == "fake-claude"


# ---------------------------------------------------------------------------
# parse_claude_output — JSON shapes
# ---------------------------------------------------------------------------


def test_parse_empty() -> None:
    r = parse_claude_output("")
    assert r.text == ""
    assert r.total_tokens == 0


def test_parse_single_json_result() -> None:
    s = build_fake_claude_output("my text", input_tokens=10, output_tokens=20, model="claude-sonnet")
    r = parse_claude_output(s)
    assert r.text == "my text"
    assert r.usage.input_tokens == 10
    assert r.usage.output_tokens == 20
    assert r.model == "claude-sonnet"
    assert r.total_tokens == 30


def test_parse_single_json_with_cost() -> None:
    s = build_fake_claude_output("x", cost_usd=0.012)
    r = parse_claude_output(s)
    assert r.cost_usd == Decimal("0.012")


def test_parse_single_json_cost_invalid() -> None:
    payload = json.dumps({"result": "hi", "usage": {"input_tokens": 1, "output_tokens": 1}, "cost_usd": "not-a-number"})
    r = parse_claude_output(payload)
    assert r.text == "hi"
    assert r.cost_usd is None


def test_parse_content_array() -> None:
    payload = json.dumps({"content": [{"type": "text", "text": "hello "}, {"type": "text", "text": "world"}], "usage": {"input_tokens": 5, "output_tokens": 5}})
    r = parse_claude_output(payload)
    assert r.text == "hello world"


def test_parse_text_field() -> None:
    payload = json.dumps({"text": "direct text", "usage": {"input_tokens": 1, "output_tokens": 2}})
    r = parse_claude_output(payload)
    assert r.text == "direct text"


def test_parse_unknown_json_shape_fallback() -> None:
    payload = json.dumps({"foo": "bar", "usage": {"input_tokens": 1, "output_tokens": 1}})
    r = parse_claude_output(payload)
    assert r.text == payload


def test_parse_usage_invalid_types() -> None:
    payload = json.dumps({"result": "hi", "usage": {"input_tokens": "bad", "output_tokens": None}})
    r = parse_claude_output(payload)
    assert r.text == "hi"
    assert r.total_tokens == 0


def test_parse_usage_negative_clamped() -> None:
    payload = json.dumps({"result": "hi", "usage": {"input_tokens": -5, "output_tokens": -2}})
    r = parse_claude_output(payload)
    assert r.usage.input_tokens == 0
    assert r.usage.output_tokens == 0


def test_parse_no_usage_defaults() -> None:
    payload = json.dumps({"result": "hi"})
    r = parse_claude_output(payload)
    assert r.text == "hi"
    assert r.total_tokens == 0


def test_parse_usage_not_dict() -> None:
    payload = json.dumps({"result": "hi", "usage": "not-a-dict"})
    r = parse_claude_output(payload)
    assert r.text == "hi"
    assert r.total_tokens == 0


def test_parse_cost_via_cost_key() -> None:
    payload = json.dumps({"result": "hi", "usage": {"input_tokens": 1, "output_tokens": 1}, "cost": 0.005})
    r = parse_claude_output(payload)
    assert r.cost_usd == Decimal("0.005")


# ---------------------------------------------------------------------------
# parse — line-delimited JSON
# ---------------------------------------------------------------------------


def test_parse_line_delimited_last_wins() -> None:
    line1 = json.dumps({"result": "first", "usage": {"input_tokens": 1, "output_tokens": 1}})
    line2 = json.dumps({"result": "second", "usage": {"input_tokens": 10, "output_tokens": 20}})
    r = parse_claude_output(f"not json\n{line1}\n{line2}\n")
    assert r.text == "second"
    assert r.total_tokens == 30


def test_parse_line_delimited_mixed_with_text() -> None:
    payload = json.dumps({"result": "real", "usage": {"input_tokens": 2, "output_tokens": 3}})
    r = parse_claude_output(f"some log line\n{payload}\ntrailer text")
    assert r.text == "real"


def test_parse_plain_text_fallback() -> None:
    r = parse_claude_output("plain text output no json")
    assert r.text == "plain text output no json"
    assert r.total_tokens == 0


# ---------------------------------------------------------------------------
# ClaudeCliError
# ---------------------------------------------------------------------------


def test_claude_cli_error_fields() -> None:
    e = ClaudeCliError("msg", exit_code=1, stderr="err", stdout="out")
    assert "msg" in str(e)
    assert e.exit_code == 1
    assert e.stderr == "err"
    assert e.stdout == "out"


# ---------------------------------------------------------------------------
# ClaudeCodeDriver — construction
# ---------------------------------------------------------------------------


def test_driver_defaults() -> None:
    d = ClaudeCodeDriver()
    assert d.cli_path == "claude"
    assert d.timeout_s == 120.0


def test_driver_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CLI_PATH", "/tmp/fake_claude")
    d = ClaudeCodeDriver()
    assert d.cli_path == "/tmp/fake_claude"


def test_driver_explicit_path_wins_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CLI_PATH", "/tmp/env_path")
    d = ClaudeCodeDriver(cli_path="/tmp/explicit")
    assert d.cli_path == "/tmp/explicit"


def test_driver_build_command() -> None:
    d = ClaudeCodeDriver(cli_path="claude", model="sonnet", extra_args=["--verbose"])
    cmd = d._build_command("hello")
    assert cmd == ["claude", "-p", "hello", "--output-format", "json", "--model", "sonnet", "--verbose"]


def test_driver_build_command_no_model() -> None:
    d = ClaudeCodeDriver(cli_path="claude")
    assert "--model" not in d._build_command("hi")


# ---------------------------------------------------------------------------
# ClaudeCodeDriver — subprocess via fake script
# ---------------------------------------------------------------------------


def _fake_cli_path() -> str:
    return str(Path(__file__).resolve().parents[2] / "scripts" / "fake_claude.py")


def test_driver_run_via_fake_script() -> None:
    _ = ClaudeCodeDriver(cli_path=sys.executable, extra_args=[_fake_cli_path()])
    # Our fake script expects: python scripts/fake_claude.py -p <prompt> --output-format json
    # ClaudeCodeDriver will do: <cli_path> -p <prompt> --output-format json + extra_args
    # So cli_path=python, extra_args=[fake_script] won't produce correct order.
    # Instead we use a wrapper: run driver with cli_path that is python + script via a shim.
    # To test real subprocess, call fake script directly via subprocess.
    result = subprocess.run(
        [sys.executable, _fake_cli_path(), "-p", "hello", "--output-format", "json"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0
    parsed = parse_claude_output(result.stdout)
    assert "hello" in parsed.text
    assert parsed.total_tokens > 0


def test_driver_run_success_with_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    # Use monkeypatch to stub subprocess.run
    fake_stdout = build_fake_claude_output("generated spec", input_tokens=100, output_tokens=200, model="claude-test")
    monkeypatch.setattr(
        "autobridge.bench.baseline_coding_agent.subprocess.run",
        lambda *a, **_kw: subprocess.CompletedProcess(args=a[0], returncode=0, stdout=fake_stdout, stderr=""),  # noqa: ARG005
    )
    d = ClaudeCodeDriver(cli_path="claude")
    res = d.run("make a spec")
    assert res.text == "generated spec"
    assert res.total_tokens == 300
    assert res.model == "claude-test"
    assert res.duration_ms >= 0
    assert d.last_prompt == "make a spec"


def test_driver_run_nonzero_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "autobridge.bench.baseline_coding_agent.subprocess.run",
        lambda *a, **_kw: subprocess.CompletedProcess(args=a[0], returncode=1, stdout="out", stderr="bad prompt"),  # noqa: ARG005
    )
    d = ClaudeCodeDriver(cli_path="claude")
    with pytest.raises(ClaudeCliError, match="failed with exit 1"):
        d.run("prompt")


def test_driver_run_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*a: object, **_kw: object) -> subprocess.CompletedProcess[str]:  # noqa: ARG001
        raise subprocess.TimeoutExpired(cmd="claude", timeout=1, output="o", stderr="e")

    monkeypatch.setattr("autobridge.bench.baseline_coding_agent.subprocess.run", fake_run)
    d = ClaudeCodeDriver(cli_path="claude", timeout_s=1)
    with pytest.raises(ClaudeCliError, match="timed out"):
        d.run("prompt")


def test_driver_run_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*a: object, **_kw: object) -> subprocess.CompletedProcess[str]:  # noqa: ARG001
        raise FileNotFoundError("no such file")

    monkeypatch.setattr("autobridge.bench.baseline_coding_agent.subprocess.run", fake_run)
    d = ClaudeCodeDriver(cli_path="claude-not-exist")
    with pytest.raises(ClaudeCliError, match="not found"):
        d.run("prompt")


def test_driver_last_prompt_none() -> None:
    d = ClaudeCodeDriver()
    assert d.last_prompt is None


def test_driver_custom_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["timeout"] = kwargs.get("timeout")
        fake_stdout = build_fake_claude_output("ok")
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout=fake_stdout, stderr="")

    monkeypatch.setattr("autobridge.bench.baseline_coding_agent.subprocess.run", fake_run)
    d = ClaudeCodeDriver(cli_path="claude", timeout_s=10)
    d.run("hi", timeout_s=5)
    assert captured["timeout"] == 5


# ---------------------------------------------------------------------------
# FakeClaudeDriver
# ---------------------------------------------------------------------------


def test_fake_driver_default() -> None:
    f = FakeClaudeDriver()
    assert f.last_prompt is None
    res = f.run("hello")
    assert res.text == "fake claude response"
    assert res.total_tokens == 30  # 10+20 default
    assert f.last_prompt == "hello"
    assert len(f.calls) == 1


def test_fake_driver_direct_hit() -> None:
    f = FakeClaudeDriver(responses={"hello": "world"})
    res = f.run("hello")
    assert res.text == "world"


def test_fake_driver_substring() -> None:
    f = FakeClaudeDriver(responses={"spec": "custom spec"})
    res = f.run("make a spec for harbor")
    assert res.text == "custom spec"


def test_fake_driver_result_object() -> None:
    canned = ClaudeCliResult(text="canned", usage=Usage(5, 5), model="m")
    f = FakeClaudeDriver(responses={"hi": canned})
    res = f.run("hi there")
    assert res.text == "canned"
    assert res.usage.input_tokens == 5


def test_fake_driver_callable_returns_result() -> None:
    def fn(prompt: str) -> ClaudeCliResult:
        return ClaudeCliResult(text=f"echo:{prompt}", usage=Usage(1, 1))

    f = FakeClaudeDriver(responses={"echo": fn})
    res = f.run("echo this")
    assert res.text == "echo:echo this"


def test_fake_driver_callable_returns_string() -> None:
    def fn(prompt: str) -> str:
        return f"str:{prompt}"

    f = FakeClaudeDriver(responses={"echo": fn})
    res = f.run("echo this")
    assert res.text == "str:echo this"


def test_fake_driver_string_coercion() -> None:
    f = FakeClaudeDriver(responses={"a": 123})
    res = f.run("a prompt")
    assert res.text == "123"


def test_fake_driver_custom_usage() -> None:
    f = FakeClaudeDriver(default_usage=Usage(1, 2), default_model="my-model")
    res = f.run("hi")
    assert res.usage.input_tokens == 1
    assert res.model == "my-model"


def test_fake_driver_cwd_captured() -> None:
    f = FakeClaudeDriver()
    f.run("hi", cwd=Path("/tmp"))
    assert f.calls[0]["cwd"] == "/tmp"


# ---------------------------------------------------------------------------
# CodexCliDriver stub
# ---------------------------------------------------------------------------


def test_codex_driver_stub() -> None:
    d = CodexCliDriver()
    assert d.cli_path == "codex"
    # Adapter stub is present (P1.W8.2); when binary missing it raises CodexCliError
    # with fallback hint (plan §9) instead of NotImplementedError.
    with pytest.raises(CodexCliError, match="fallback|not found|codex"):
        d.run("prompt")


def test_codex_driver_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_CLI_PATH", "/tmp/codex_fake")
    d = CodexCliDriver()
    assert d.cli_path == "/tmp/codex_fake"


# ---------------------------------------------------------------------------
# fake_cli re-export
# ---------------------------------------------------------------------------


def test_fake_cli_reexport() -> None:
    from autobridge.bench.fake_cli import FakeClaudeDriver as F
    from autobridge.bench.fake_cli import build_fake_claude_output as B

    assert F is FakeClaudeDriver
    assert B is build_fake_claude_output


# ---------------------------------------------------------------------------
# Integration: fake_claude.py script
# ---------------------------------------------------------------------------


def test_fake_claude_script_direct_invocation(tmp_path: Path) -> None:  # noqa: ARG001
    script = Path(__file__).resolve().parents[2] / "scripts" / "fake_claude.py"
    # Default
    proc = subprocess.run([sys.executable, str(script), "-p", "hello", "--output-format", "json"], capture_output=True, text=True, timeout=5)
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert "hello" in data["result"]
    assert data["usage"]["input_tokens"] == 10


def test_fake_claude_script_env_mapping(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:  # noqa: ARG001
    script = Path(__file__).resolve().parents[2] / "scripts" / "fake_claude.py"
    env = os.environ.copy()
    env["FAKE_CLAUDE_RESPONSES"] = json.dumps({"hello": "mapped response"})
    env["FAKE_CLAUDE_USAGE"] = "5,7"
    proc = subprocess.run([sys.executable, str(script), "-p", "hello world", "--output-format", "json"], capture_output=True, text=True, env=env, timeout=5)
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert data["result"] == "mapped response"
    assert data["usage"]["input_tokens"] == 5


def test_fake_claude_script_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ARG001
    script = Path(__file__).resolve().parents[2] / "scripts" / "fake_claude.py"
    env = os.environ.copy()
    env["FAKE_CLAUDE_EXIT_CODE"] = "2"
    proc = subprocess.run([sys.executable, str(script), "-p", "hi"], capture_output=True, text=True, env=env, timeout=5)
    assert proc.returncode == 2


def test_fake_claude_script_cost(monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ARG001
    script = Path(__file__).resolve().parents[2] / "scripts" / "fake_claude.py"
    env = os.environ.copy()
    env["FAKE_CLAUDE_COST"] = "0.02"
    proc = subprocess.run([sys.executable, str(script), "-p", "hi"], capture_output=True, text=True, env=env, timeout=5)
    data = json.loads(proc.stdout)
    assert data["cost_usd"] == 0.02


def test_fake_claude_script_model(monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ARG001
    script = Path(__file__).resolve().parents[2] / "scripts" / "fake_claude.py"
    env = os.environ.copy()
    env["FAKE_CLAUDE_MODEL"] = "my-fake-model"
    proc = subprocess.run([sys.executable, str(script), "-p", "hi"], capture_output=True, text=True, env=env, timeout=5)
    data = json.loads(proc.stdout)
    assert data["model"] == "my-fake-model"


# ---------------------------------------------------------------------------
# OpenCodeCliDriver (REQ-024 B1 — default baseline driver)
# ---------------------------------------------------------------------------


def test_opencode_parse_empty() -> None:
    res = parse_opencode_output("")
    assert res.text == ""
    assert res.total_tokens == 0
    assert res.model == ""


def test_opencode_parse_fake_stream() -> None:
    stdout = build_fake_opencode_output("generated spec", input_tokens=100, output_tokens=200, model="spark-test")
    res = parse_opencode_output(stdout)
    assert res.text == "generated spec"
    assert res.usage.input_tokens == 100
    assert res.usage.output_tokens == 200
    assert res.model == "spark-test"


def test_opencode_parse_prompt_tokens_shape() -> None:
    stdout = json.dumps({"type": "step_finish", "tokenUsage": {"prompt_tokens": 30, "completion_tokens": 40}, "model": "m"})
    res = parse_opencode_output(stdout)
    assert res.usage.input_tokens == 30
    assert res.usage.output_tokens == 40


def test_opencode_parse_tokens_input_output_shape() -> None:
    stdout = json.dumps({"tokens": {"input": 7, "output": 9}})
    res = parse_opencode_output(stdout)
    assert res.total_tokens == 16


def test_opencode_parse_max_wins_across_events() -> None:
    lines = [
        json.dumps({"type": "message", "content": [{"type": "text", "text": "part one"}]}),
        json.dumps({"type": "usage", "usage": {"input_tokens": 50, "output_tokens": 60}}),
        json.dumps({"type": "done", "usage": {"input_tokens": 120, "output_tokens": 200}, "model": "spark"}),
    ]
    res = parse_opencode_output("\n".join(lines))
    assert "part one" in res.text
    assert res.total_tokens == 320
    assert res.model == "spark"


def test_opencode_parse_cost() -> None:
    stdout = build_fake_opencode_output("t", cost_usd=0.011)
    res = parse_opencode_output(stdout)
    assert res.cost_usd == Decimal("0.011")


def test_opencode_parse_plain_text_fallback() -> None:
    res = parse_opencode_output("just some text\nmore text")
    assert "just some text" in res.text
    assert res.total_tokens == 0


def test_opencode_parse_unknown_shape_never_raises() -> None:
    res = parse_opencode_output(json.dumps({"weird": [1, {"nested": True}]}))
    assert res.text == ""
    assert res.total_tokens == 0


def test_opencode_driver_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENCODE_CLI_PATH", raising=False)
    monkeypatch.delenv("OPENCODE_MODEL", raising=False)
    d = OpenCodeCliDriver()
    assert d.cli_path == "opencode"
    assert d.model is None
    assert d.last_prompt is None


def test_opencode_driver_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCODE_CLI_PATH", "/tmp/opencode_fake")
    monkeypatch.setenv("OPENCODE_MODEL", "meta/muse-spark-1.3")
    d = OpenCodeCliDriver()
    assert d.cli_path == "/tmp/opencode_fake"
    assert d.model == "meta/muse-spark-1.3"


def test_opencode_driver_build_command() -> None:
    d = OpenCodeCliDriver(model="meta/muse-spark-1.3")
    assert d._build_command("hi") == ["opencode", "run", "--format", "json", "-m", "meta/muse-spark-1.3", "hi"]


def test_opencode_driver_build_command_no_model() -> None:
    d = OpenCodeCliDriver(cli_path="opencode", model=None)
    # model=None + OPENCODE_MODEL unset in this env path is not guaranteed;
    # explicit empty string also omits -m.
    d.model = None
    cmd = d._build_command("hi")
    assert cmd[:4] == ["opencode", "run", "--format", "json"]
    assert "-m" not in cmd
    assert cmd[-1] == "hi"


def test_opencode_driver_run_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_stdout = build_fake_opencode_output("spec yaml", input_tokens=11, output_tokens=22, model="spark")
    monkeypatch.setattr(
        "autobridge.bench.baseline_coding_agent.subprocess.run",
        lambda *a, **_kw: subprocess.CompletedProcess(args=a[0], returncode=0, stdout=fake_stdout, stderr=""),  # noqa: ARG005
    )
    d = OpenCodeCliDriver(cli_path="opencode", model="meta/muse-spark-1.3")
    res = d.run("make a spec")
    assert res.text == "spec yaml"
    assert res.total_tokens == 33
    assert res.model == "spark"
    assert d.last_prompt == "make a spec"
    assert d.calls[0]["cmd"][4:6] == ["-m", "meta/muse-spark-1.3"]


def test_opencode_driver_run_nonzero_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "autobridge.bench.baseline_coding_agent.subprocess.run",
        lambda *a, **_kw: subprocess.CompletedProcess(args=a[0], returncode=1, stdout="out", stderr="bad"),  # noqa: ARG005
    )
    with pytest.raises(OpenCodeCliError, match="failed with exit 1"):
        OpenCodeCliDriver(cli_path="opencode").run("prompt")


def test_opencode_driver_run_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*a: object, **_kw: object) -> subprocess.CompletedProcess[str]:  # noqa: ARG001
        raise subprocess.TimeoutExpired(cmd="opencode", timeout=1, output="o", stderr="e")

    monkeypatch.setattr("autobridge.bench.baseline_coding_agent.subprocess.run", fake_run)
    with pytest.raises(OpenCodeCliError, match="timed out"):
        OpenCodeCliDriver(cli_path="opencode", timeout_s=1).run("prompt")


def test_opencode_driver_run_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*a: object, **_kw: object) -> subprocess.CompletedProcess[str]:  # noqa: ARG001
        raise FileNotFoundError("no such file")

    monkeypatch.setattr("autobridge.bench.baseline_coding_agent.subprocess.run", fake_run)
    with pytest.raises(OpenCodeCliError, match="not found"):
        OpenCodeCliDriver(cli_path="opencode-not-exist").run("prompt")


def test_fake_opencode_driver_default() -> None:
    f = FakeOpenCodeDriver()
    res = f.run("hello")
    assert res.text == "fake opencode response"
    assert res.total_tokens == 30
    assert res.model == "fake-opencode"


def test_fake_opencode_driver_substring() -> None:
    f = FakeOpenCodeDriver(responses={"spec": "custom spec"})
    assert f.run("make a spec").text == "custom spec"


def test_baseline_driver_default_is_opencode() -> None:
    d = get_baseline_driver()
    assert isinstance(d, OpenCodeCliDriver)


def test_baseline_driver_kinds() -> None:
    assert isinstance(get_baseline_driver("claude"), ClaudeCodeDriver)
    assert isinstance(get_baseline_driver("codex"), CodexCliDriver)
    assert isinstance(get_baseline_driver("opencode"), OpenCodeCliDriver)
    with pytest.raises(ValueError, match="unknown baseline driver kind"):
        get_baseline_driver("nope")
