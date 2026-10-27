"""Coding-agent baseline driver (P1.W1.9 + P1.W8.2, plan §2.10).

Runs the ``claude -p`` (or ``codex``) headless CLI inside the same
``runtime-sandbox`` image the harness uses for AutoBridge.  The driver
captures token usage from the CLI's JSON output so the bench can report
``frontier_tokens``, ``cost`` and ``elapsed`` on equal footing.

The W1.9 fake lives alongside this module: :class:`FakeClaudeDriver`
and helpers :func:`build_fake_claude_output` / :func:`parse_claude_output`
let unit tests exercise the driver without a real API key or Docker.

CLI contract (``claude -p <prompt> --output-format json``)::

    {
        "type": "result",
        "result": "generated spec yaml ...",
        "usage": {"input_tokens": 1200, "output_tokens": 3400},
        "model": "claude-sonnet-4-5-20250929",
        "cost_usd": 0.012,
        "session_id": "..."
    }

Text fallback: when ``--output-format json`` is not used, stdout is
plain text and usage is zero.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from autobridge.llm.provider import Usage

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ClaudeCliResult:
    """Parsed result of a single ``claude -p`` invocation."""

    text: str = ""
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    cost_usd: Decimal | None = None
    raw_output: str = ""
    exit_code: int = 0
    duration_ms: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.usage.total_tokens


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_claude_output(stdout: str) -> ClaudeCliResult:
    """Parse ``claude -p --output-format json`` stdout.

    Handles:
    * Single JSON object with ``result`` / ``usage`` / ``model`` / ``cost_usd``.
    * JSON with ``content`` array (alternate shape).
    * Line-delimited JSON (last JSON line wins).
    * Plain text fallback.
    """
    raw = stdout.strip()
    if not raw:
        return ClaudeCliResult(text="", raw_output=stdout)

    # Try single JSON object
    try:
        data = json.loads(raw)
        return _from_json_dict(data, stdout)
    except (json.JSONDecodeError, ValueError):
        pass

    # Try line-delimited JSON — last parseable line wins
    lines = raw.splitlines()
    last_data: dict[str, Any] | None = None
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            cand = json.loads(line)
            if isinstance(cand, dict):
                last_data = cand
                break
        except (json.JSONDecodeError, ValueError):
            continue
    if last_data is not None:
        return _from_json_dict(last_data, stdout)

    # Plain text fallback
    return ClaudeCliResult(text=raw, raw_output=stdout)


def _from_json_dict(data: dict[str, Any], raw: str) -> ClaudeCliResult:
    text = ""
    # Primary shape: {"result": "...."}
    if "result" in data:
        text = str(data.get("result", ""))
    # Alternate: {"content": [{"type": "text", "text": "..."}]}
    elif "content" in data and isinstance(data["content"], list):
        parts: list[str] = []
        for block in data["content"]:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        text = "".join(parts)
    # Alternate: {"text": "..."}
    elif "text" in data:
        text = str(data.get("text", ""))
    else:
        # Unknown JSON shape — treat as text
        text = raw

    # Usage extraction
    usage_raw = data.get("usage", {})
    usage = Usage()
    if isinstance(usage_raw, dict):
        try:
            inp = int(usage_raw.get("input_tokens", 0) or 0)
            out = int(usage_raw.get("output_tokens", 0) or 0)
            usage = Usage(input_tokens=max(0, inp), output_tokens=max(0, out))
        except (ValueError, TypeError):
            usage = Usage()

    model = str(data.get("model", "") or "")
    cost_val = data.get("cost_usd", data.get("cost"))
    cost: Decimal | None = None
    if cost_val is not None:
        try:
            cost = Decimal(str(cost_val))
        except Exception:
            cost = None

    return ClaudeCliResult(
        text=text,
        usage=usage,
        model=model,
        cost_usd=cost,
        raw_output=raw,
    )


def build_fake_claude_output(
    text: str,
    *,
    input_tokens: int = 10,
    output_tokens: int = 20,
    model: str = "fake-claude",
    cost_usd: str | float | None = None,
) -> str:
    """Build a JSON string matching the ``claude -p`` output contract.

    Used by the fake CLI and by tests to generate canned stdout.
    """
    payload: dict[str, Any] = {
        "type": "result",
        "result": text,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        "model": model,
    }
    if cost_usd is not None:
        payload["cost_usd"] = cost_usd
    return json.dumps(payload)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ClaudeCliError(RuntimeError):
    """Raised when the CLI exits non-zero or times out."""

    def __init__(
        self,
        message: str,
        *,
        exit_code: int | None = None,
        stderr: str = "",
        stdout: str = "",
    ) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.stderr = stderr
        self.stdout = stdout


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


class ClaudeCodeDriver:
    """Headless ``claude -p`` driver with usage capture.

    *cli_path* defaults to ``CLAUDE_CLI_PATH`` env or ``"claude"``.
    Pass a fake binary path in tests (e.g. the ``fake_claude`` script).
    """

    def __init__(
        self,
        *,
        cli_path: str | None = None,
        timeout_s: float = 120.0,
        model: str | None = None,
        extra_args: list[str] | None = None,
    ) -> None:
        env_path = os.environ.get("CLAUDE_CLI_PATH", "")
        self.cli_path = cli_path or env_path or "claude"
        self.timeout_s = timeout_s
        self.model = model
        self.extra_args = list(extra_args or [])
        self.calls: list[dict[str, Any]] = []

    def _build_command(self, prompt: str) -> list[str]:
        cmd: list[str] = [self.cli_path, "-p", prompt, "--output-format", "json"]
        if self.model:
            cmd.extend(["--model", self.model])
        cmd.extend(self.extra_args)
        return cmd

    def run(
        self,
        prompt: str,
        *,
        cwd: Path | None = None,
        timeout_s: float | None = None,
    ) -> ClaudeCliResult:
        """Execute ``claude -p`` with *prompt* and return parsed result."""
        cmd = self._build_command(prompt)
        t = timeout_s if timeout_s is not None else self.timeout_s
        self.calls.append({"cmd": cmd, "prompt": prompt, "cwd": str(cwd) if cwd else None})
        start = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=t,
                cwd=str(cwd) if cwd else None,
            )
        except subprocess.TimeoutExpired as e:
            raise ClaudeCliError(
                f"claude CLI timed out after {t}s",
                stderr=getattr(e, "stderr", "") or "",
                stdout=getattr(e, "stdout", "") or "",
            ) from e
        except FileNotFoundError as e:
            raise ClaudeCliError(
                f"claude CLI not found: {self.cli_path!r}",
                stderr=str(e),
            ) from e

        elapsed = (time.monotonic() - start) * 1000.0
        if proc.returncode != 0:
            raise ClaudeCliError(
                f"claude CLI failed with exit {proc.returncode}: {proc.stderr.strip()[:500]}",
                exit_code=proc.returncode,
                stderr=proc.stderr,
                stdout=proc.stdout,
            )
        result = parse_claude_output(proc.stdout)
        result.exit_code = proc.returncode
        result.duration_ms = elapsed
        return result

    @property
    def last_prompt(self) -> str | None:
        return self.calls[-1]["prompt"] if self.calls else None


# ---------------------------------------------------------------------------
# Codex CLI driver (P1.W8.2, plan §2.10)
#
# Adapter stub for the ``codex`` headless CLI.  The interface mirrors
# :class:`ClaudeCodeDriver` so the harness can swap drivers without
# changing inputs.  The real ``codex`` binary is not required for unit
# tests — :class:`FakeCodexDriver` and ``scripts/fake_codex.py`` provide
# the W1 fake path, and :class:`BaselineLLMDriver` is the documented
# fallback when neither CLI is available (plan §9).
# ---------------------------------------------------------------------------


def parse_codex_output(stdout: str) -> ClaudeCliResult:
    """Parse ``codex exec --json`` stdout.

    Codex JSON shapes handled:

    * ``{"output": "…", "usage": {"input_tokens": …, "output_tokens": …}, "model": "…"}``
    * ``{"result": "…", "tokens": {"prompt": …, "completion": …}}`` (alt shape)
    * Same shapes as :func:`parse_claude_output` (shared harness helper).

    Falls back to plain text when no JSON is present.  Token counts are
    clamped to ≥ 0.
    """
    raw = stdout.strip()
    if not raw:
        return ClaudeCliResult(text="", raw_output=stdout)

    # Try single JSON object first
    try:
        data = json.loads(raw)
        return _from_codex_json_dict(data, stdout)
    except (json.JSONDecodeError, ValueError):
        pass

    # Line-delimited JSON — last parseable line wins
    lines = raw.splitlines()
    last_data: dict[str, Any] | None = None
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            cand = json.loads(line)
            if isinstance(cand, dict):
                last_data = cand
                break
        except (json.JSONDecodeError, ValueError):
            continue
    if last_data is not None:
        return _from_codex_json_dict(last_data, stdout)

    return ClaudeCliResult(text=raw, raw_output=stdout)


def _from_codex_json_dict(data: dict[str, Any], raw: str) -> ClaudeCliResult:
    text = ""
    if "output" in data:
        text = str(data.get("output", ""))
    elif "result" in data:
        text = str(data.get("result", ""))
    elif "content" in data and isinstance(data["content"], list):
        parts: list[str] = []
        for block in data["content"]:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        text = "".join(parts)
    elif "text" in data:
        text = str(data.get("text", ""))
    elif "message" in data:
        text = str(data.get("message", ""))
    else:
        text = raw

    # Usage — try several key variants
    usage_raw: Any = data.get("usage", data.get("tokens", data.get("token_usage", {})))
    usage = Usage()
    if isinstance(usage_raw, dict):
        try:
            # Accept both {input_tokens, output_tokens} and {prompt, completion}
            inp = usage_raw.get("input_tokens", usage_raw.get("prompt", usage_raw.get("prompt_tokens", 0)))
            out = usage_raw.get("output_tokens", usage_raw.get("completion", usage_raw.get("completion_tokens", 0)))
            inp = int(inp or 0)
            out = int(out or 0)
            usage = Usage(input_tokens=max(0, inp), output_tokens=max(0, out))
        except (ValueError, TypeError):
            usage = Usage()

    model = str(data.get("model", data.get("model_id", "") or ""))
    cost_val = data.get("cost_usd", data.get("cost", data.get("price")))
    cost: Decimal | None = None
    if cost_val is not None:
        try:
            cost = Decimal(str(cost_val))
        except Exception:
            cost = None

    return ClaudeCliResult(text=text, usage=usage, model=model, cost_usd=cost, raw_output=raw)


def build_fake_codex_output(
    text: str,
    *,
    input_tokens: int = 10,
    output_tokens: int = 20,
    model: str = "fake-codex",
    cost_usd: str | float | None = None,
) -> str:
    """Build a JSON string matching the ``codex exec --json`` contract."""
    payload: dict[str, Any] = {
        "output": text,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        "model": model,
    }
    if cost_usd is not None:
        payload["cost_usd"] = cost_usd
    return json.dumps(payload)


class CodexCliError(ClaudeCliError):
    """Raised when the Codex CLI exits non-zero or times out."""


class CodexCliDriver:
    """Headless ``codex exec`` driver with usage capture (P1.W8.2).

    CLI contract (``codex exec --json "<prompt>"``)::

        {
            "output": "generated spec yaml …",
            "usage": {"input_tokens": 1200, "output_tokens": 3400},
            "model": "codex-mini-latest",
            "cost_usd": 0.012
        }

    This is an adapter stub: the harness can drive it via the same
    :class:`HarnessInputs` as :class:`ClaudeCodeDriver`, and unit tests
    use :class:`FakeCodexDriver` / ``scripts/fake_codex.py`` (W1 fake).
    When the real ``codex`` binary is unavailable the documented fallback
    is :class:`BaselineLLMDriver` — routing the baseline through
    :class:`autobridge.llm.provider.LLMProvider` with an equivalent agent
    loop (plan §9, see also :func:`autobridge.bench.harness.run_baseline_via_llm_provider`).

    ``cli_path`` defaults to ``CODEX_CLI_PATH`` env or ``"codex"``.
    """

    def __init__(
        self,
        *,
        cli_path: str | None = None,
        timeout_s: float = 120.0,
        model: str | None = None,
        extra_args: list[str] | None = None,
    ) -> None:
        self.cli_path = cli_path or os.environ.get("CODEX_CLI_PATH", "") or "codex"
        self.timeout_s = timeout_s
        self.model = model
        self.extra_args = list(extra_args or [])
        self.calls: list[dict[str, Any]] = []

    def _build_command(self, prompt: str) -> list[str]:
        cmd: list[str] = [self.cli_path, "exec", "--json", prompt]
        if self.model:
            cmd.extend(["--model", self.model])
        cmd.extend(self.extra_args)
        return cmd

    def run(
        self,
        prompt: str,
        *,
        cwd: Path | None = None,
        timeout_s: float | None = None,
    ) -> ClaudeCliResult:
        """Execute ``codex exec --json`` with *prompt*."""
        cmd = self._build_command(prompt)
        t = timeout_s if timeout_s is not None else self.timeout_s
        self.calls.append({"cmd": cmd, "prompt": prompt, "cwd": str(cwd) if cwd else None})
        start = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=t,
                cwd=str(cwd) if cwd else None,
            )
        except subprocess.TimeoutExpired as e:
            raise CodexCliError(
                f"codex CLI timed out after {t}s",
                stderr=getattr(e, "stderr", "") or "",
                stdout=getattr(e, "stdout", "") or "",
            ) from e
        except FileNotFoundError as e:
            # Surface as CodexCliError with fallback hint
            raise CodexCliError(
                f"codex CLI not found: {self.cli_path!r} — "
                "fallback: use BaselineLLMDriver via LLMProvider (plan §9)",
                stderr=str(e),
            ) from e

        elapsed = (time.monotonic() - start) * 1000.0
        if proc.returncode != 0:
            raise CodexCliError(
                f"codex CLI failed with exit {proc.returncode}: {proc.stderr.strip()[:500]}",
                exit_code=proc.returncode,
                stderr=proc.stderr,
                stdout=proc.stdout,
            )
        result = parse_codex_output(proc.stdout)
        result.exit_code = proc.returncode
        result.duration_ms = elapsed
        return result

    @property
    def last_prompt(self) -> str | None:
        return self.calls[-1]["prompt"] if self.calls else None


# ---------------------------------------------------------------------------
# In-memory fake drivers (no subprocess) — W1 fakes reused in bench tests
# ---------------------------------------------------------------------------


class FakeCodexDriver:
    """In-memory fake for the Codex CLI (no subprocess).

    Mirrors :class:`FakeClaudeDriver` so tests can exercise the Codex
    adapter stub without a real binary.
    """

    def __init__(
        self,
        responses: dict[str, ClaudeCliResult | str | Any] | None = None,
        *,
        default_text: str = "fake codex response",
        default_usage: Usage | None = None,
        default_model: str = "fake-codex",
    ) -> None:
        self.responses = dict(responses or {})
        self.default_text = default_text
        self.default_usage = default_usage or Usage(input_tokens=10, output_tokens=20)
        self.default_model = default_model
        self.calls: list[dict[str, Any]] = []

    def run(self, prompt: str, *, cwd: Path | None = None, **_kwargs: Any) -> ClaudeCliResult:
        self.calls.append({"prompt": prompt, "cwd": str(cwd) if cwd else None})
        if prompt in self.responses:
            return self._coerce(self.responses[prompt], prompt)
        for key, val in self.responses.items():
            if key in prompt:
                return self._coerce(val, prompt)
        return ClaudeCliResult(
            text=self.default_text,
            usage=self.default_usage,
            model=self.default_model,
            raw_output=build_fake_codex_output(
                self.default_text,
                input_tokens=self.default_usage.input_tokens,
                output_tokens=self.default_usage.output_tokens,
                model=self.default_model,
            ),
        )

    def _coerce(self, val: Any, prompt: str) -> ClaudeCliResult:
        if isinstance(val, ClaudeCliResult):
            return val
        if callable(val):
            res = val(prompt)
            if isinstance(res, ClaudeCliResult):
                return res
            return ClaudeCliResult(text=str(res), usage=self.default_usage, model=self.default_model)
        if isinstance(val, str):
            return ClaudeCliResult(
                text=val,
                usage=self.default_usage,
                model=self.default_model,
                raw_output=build_fake_codex_output(
                    val,
                    input_tokens=self.default_usage.input_tokens,
                    output_tokens=self.default_usage.output_tokens,
                    model=self.default_model,
                ),
            )
        return ClaudeCliResult(text=str(val), usage=self.default_usage, model=self.default_model)

    @property
    def last_prompt(self) -> str | None:
        return self.calls[-1]["prompt"] if self.calls else None


class FakeClaudeDriver:
    """In-memory fake that returns canned results (no subprocess).

    *responses* maps prompt substring → :class:`ClaudeCliResult` or
    callable ``(prompt) -> ClaudeCliResult`` or plain text string.
    ``default_text`` is used when no key matches.
    """

    def __init__(
        self,
        responses: dict[str, ClaudeCliResult | str | Any] | None = None,
        *,
        default_text: str = "fake claude response",
        default_usage: Usage | None = None,
        default_model: str = "fake-claude",
    ) -> None:
        self.responses = dict(responses or {})
        self.default_text = default_text
        self.default_usage = default_usage or Usage(input_tokens=10, output_tokens=20)
        self.default_model = default_model
        self.calls: list[dict[str, Any]] = []

    def run(self, prompt: str, *, cwd: Path | None = None, **_kwargs: Any) -> ClaudeCliResult:
        self.calls.append({"prompt": prompt, "cwd": str(cwd) if cwd else None})
        # Direct hit
        if prompt in self.responses:
            val = self.responses[prompt]
            return self._coerce(val, prompt)
        # Substring match
        for key, val in self.responses.items():
            if key in prompt:
                return self._coerce(val, prompt)
        # Default
        return ClaudeCliResult(
            text=self.default_text,
            usage=self.default_usage,
            model=self.default_model,
            raw_output=build_fake_claude_output(
                self.default_text,
                input_tokens=self.default_usage.input_tokens,
                output_tokens=self.default_usage.output_tokens,
                model=self.default_model,
            ),
        )

    def _coerce(self, val: Any, prompt: str) -> ClaudeCliResult:
        if isinstance(val, ClaudeCliResult):
            return val
        if callable(val):
            res = val(prompt)
            if isinstance(res, ClaudeCliResult):
                return res
            return ClaudeCliResult(text=str(res), usage=self.default_usage, model=self.default_model)
        if isinstance(val, str):
            return ClaudeCliResult(
                text=val,
                usage=self.default_usage,
                model=self.default_model,
                raw_output=build_fake_claude_output(
                    val,
                    input_tokens=self.default_usage.input_tokens,
                    output_tokens=self.default_usage.output_tokens,
                    model=self.default_model,
                ),
            )
        return ClaudeCliResult(text=str(val), usage=self.default_usage, model=self.default_model)

    @property
    def last_prompt(self) -> str | None:
        return self.calls[-1]["prompt"] if self.calls else None


# ---------------------------------------------------------------------------
# Fallback driver — LLMProvider with equivalent agent loop (plan §9)
# ---------------------------------------------------------------------------


class BaselineLLMDriver:
    """Fallback baseline that routes through :class:`LLMProvider` (plan §9).

    When neither ``claude`` nor ``codex`` CLI is available (no API key,
    no binary, or offline CI) the benchmark can still run a baseline by
    driving the same ``LLMProvider`` interface AutoBridge uses internally.

    The driver implements the same ``run(prompt, cwd=None) -> ClaudeCliResult``
    shape as :class:`ClaudeCodeDriver` / :class:`CodexCliDriver` so the
    harness can swap drivers without changing :class:`HarnessInputs`.
    Token usage is taken from the provider's :class:`Usage` so
    ``frontier_tokens`` / ``cost_usd`` remain comparable.

    * ``provider`` — any :class:`LLMProvider` (e.g. :class:`FakeProvider`
      in tests or :class:`AnthropicProvider` live).
    * ``budget`` — optional :class:`Budget`; when given the call is wrapped
      in :class:`BudgetedProvider` so ``max_frontier_tokens`` / ``max_wall``
      from :class:`HarnessInputs` are enforced.
    * ``tier`` — model tier for the baseline loop (default ``"frontier"``,
      matching the harness ``token_ceiling``).
    * ``max_tokens`` — per-call completion limit.

    This is the documented fallback for ``make bench`` when the headless
    CLI cannot be invoked.  It provides an equivalent single-turn agent
    loop — prompt → completion — without tool use, matching the baseline's
    ``one_shot`` diagnostic control (P1.W8.3 explores a richer loop).
    """

    def __init__(
        self,
        provider: Any | None = None,
        *,
        budget: Any | None = None,
        tier: str = "frontier",
        max_tokens: int = 4096,
        model: str | None = None,
    ) -> None:
        # Lazy import to avoid cycle
        if provider is None:
            from autobridge.llm.provider import FakeProvider  # noqa: PLC0415

            provider = FakeProvider()
        self.provider = provider
        self.budget = budget
        self.tier = tier
        self.max_tokens = max_tokens
        self.model_name = model or getattr(provider, "frontier_model", None) or "baseline-llm-fallback"
        self.calls: list[dict[str, Any]] = []

    def run(self, prompt: str, *, cwd: Path | None = None, **_kwargs: Any) -> ClaudeCliResult:
        self.calls.append({"prompt": prompt, "cwd": str(cwd) if cwd else None})
        start = time.monotonic()
        try:
            # Wrap with Budget when provided so harness token_ceiling / wall-clock
            # are enforced identically for both arms (matched-budget contract).
            if self.budget is not None:
                from autobridge.llm.provider import BudgetedProvider  # noqa: PLC0415

                inner = BudgetedProvider(self.provider, self.budget)
                comp = inner.complete(
                    [{"role": "user", "content": prompt}],
                    max_tokens=self.max_tokens,
                    tier=self.tier,
                    purpose="bench-baseline-llm-fallback",
                )
            else:
                comp = self.provider.complete(
                    [{"role": "user", "content": prompt}],
                    max_tokens=self.max_tokens,
                    tier=self.tier,
                    purpose="bench-baseline-llm-fallback",
                )
            elapsed = (time.monotonic() - start) * 1000.0
            # Estimate cost via provider tier (Decimal end to end, directive §4)
            try:
                from autobridge.llm.provider import estimate_cost  # noqa: PLC0415

                cost = estimate_cost(self.tier, comp.usage)
            except Exception:
                cost = None
            model_name = self.model_name
            # Try to resolve tier-specific model from provider
            try:
                mf = getattr(self.provider, "model_for_tier", None)
                if callable(mf):
                    model_name = mf(self.tier)
            except Exception:
                pass
            return ClaudeCliResult(
                text=comp.text,
                usage=comp.usage,
                model=model_name,
                cost_usd=cost,
                raw_output=comp.text,
                duration_ms=elapsed,
            )
        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000.0
            # Surface as failed result via exception — harness catches and marks arm success=False
            raise ClaudeCliError(
                f"BaselineLLMDriver failed: {type(exc).__name__}: {exc}",
                stderr=str(exc),
            ) from exc

    @property
    def last_prompt(self) -> str | None:
        return self.calls[-1]["prompt"] if self.calls else None


def get_baseline_driver(
    kind: str = "claude",
    *,
    provider: Any | None = None,
    cli_path: str | None = None,
    timeout_s: float = 120.0,
    model: str | None = None,
) -> Any:
    """Factory returning a baseline driver by *kind*.

    * ``"claude"`` → :class:`ClaudeCodeDriver`
    * ``"codex"`` → :class:`CodexCliDriver`
    * ``"llm"`` / ``"fallback"`` → :class:`BaselineLLMDriver` (plan §9)

    The factory exists so ``make bench`` and tests can select the baseline
    without importing driver classes directly.  The fallback is
    :class:`BaselineLLMDriver` — routing through :class:`LLMProvider`
    with the same ``Budget`` ceilings the harness uses for AutoBridge,
    documented in :mod:`autobridge.bench.harness` and ``docs/benchmark.md``.
    """
    kind_norm = kind.lower().strip()
    if kind_norm in ("claude", "claude_code", "claude_code_headless"):
        return ClaudeCodeDriver(cli_path=cli_path, timeout_s=timeout_s, model=model)
    if kind_norm in ("codex", "codex_cli"):
        return CodexCliDriver(cli_path=cli_path, timeout_s=timeout_s, model=model)
    if kind_norm in ("llm", "fallback", "provider", "baseline_llm"):
        return BaselineLLMDriver(provider=provider, model=model)
    raise ValueError(f"unknown baseline driver kind {kind!r}; expected 'claude', 'codex' or 'llm'")
