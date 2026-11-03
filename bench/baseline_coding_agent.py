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
        "model": "<MODEL_ID_FROM_ENV>",
        "cost_usd": 0.012,
        "session_id": "..."
    }

Text fallback: when ``--output-format json`` is not used, stdout is
plain text and usage is zero.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from autobridge.llm.provider import Usage

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
    #: ``opencode run`` session id parsed from the event stream ("" when
    #: absent). The driver resolves the authoritative model/totals from
    #: the session row when it is reachable (REQ-060 item 4).
    session_id: str = ""
    #: ``opencode run`` ``{"type":"error"}`` event messages ("" when none).
    #: A text-less stream with errors raises instead of scoring silence.
    stream_error: str = ""

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


#: Sandbox permission config for the frontier-agent arm (REQ-091/AR-7-9).
#: Default-deny single source of truth: written to the sandbox dir's
#: ``opencode.json`` by :func:`bench.context.write_agent_sandbox` and
#: exported to the agent child as ``OPENCODE_PERMISSION`` so an inherited
#: value can never loosen it. The agent answers inline from the evidence
#: bundle in its sandbox dir — no file write anywhere is part of the
#: contract — so only ``read``/``glob``/``grep`` stay allowed inside the
#: sandbox dir; ``edit`` is denied outright and everything else falls
#: under the ``"*"`` deny baseline (no shell, no fetch, no subagents, no
#: outside-directory reads). Denying (not asking) also means a tool call
#: never ends in a silent headless "ask" rejection with no final answer.
AGENT_PERMISSION_CONFIG: dict[str, str] = {
    "*": "deny",
    "edit": "deny",
    "glob": "allow",
    "grep": "allow",
    "read": "allow",
}

#: XDG base-directory names pinned to the pristine CLI home
#: (REQ-091/AR-7-9): the child never inherits the operator's values for
#: these — they point at subdirectories of the per-run dir instead, so no
#: operator-global OpenCode config, plugin, skill or MCP server loads.
_AGENT_PRISTINE_XDG_VARS: tuple[str, str, str, str] = (
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_CACHE_HOME",
    "XDG_STATE_HOME",
)

#: Fixed env names that cross into an agent CLI child (REQ-091/AR-7-9):
#: what the OS/CLI needs to run (``PATH`` to resolve the binary,
#: ``TMPDIR`` for temp files, locale/terminal). ``HOME`` and the XDG
#: base-directory variables never cross from the parent — they are pinned
#: to the pristine per-run CLI home (see :func:`build_agent_child_env`).
#: ``PWD`` is set separately to the sandbox dir and ``OPENCODE_PERMISSION``
#: to :data:`AGENT_PERMISSION_CONFIG`.
_AGENT_CHILD_ENV_FIXED: frozenset[str] = frozenset({"LANG", "PATH", "TERM", "TMPDIR"})

#: Frontier-provider key variables, by pinned-model provider prefix
#: (REQ-088/AR-7-1, kept by REQ-091/AR-7-9): the CLI reads its own
#: provider's key for the pinned model (``META_MODEL_API_KEY`` for
#: ``meta/...``, etc.). The harness's own frontier key
#: (``FRONTIER_API_KEY``) always crosses — it is the designated
#: frontier-tier key, same sensitivity class, and the default when the
#: provider is unknown.
_AGENT_PROVIDER_KEY_VARS: dict[str, tuple[str, ...]] = {
    "meta": ("META_MODEL_API_KEY",),
    "baseten": ("BASETEN_API_KEY",),
    "modal": ("MODAL_BASE_URL", "MODAL_PROXY_TOKEN"),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
}
_AGENT_DEFAULT_KEY_VARS: tuple[str, ...] = ("ANTHROPIC_API_KEY", "FRONTIER_API_KEY", "OPENAI_API_KEY")


def agent_child_env_allowed_names(model: str | None = None) -> frozenset[str]:
    """Env names an agent CLI child may carry for *model* (REQ-091/AR-7-9).

    One explicit definition — no prefix pass-through: the fixed OS names,
    ``HOME`` plus the four XDG base-directory variables (pinned to the
    pristine per-run CLI home, never inherited), the ``OPENCODE_PERMISSION``
    override, and the provider key variables for the pinned model's
    provider. Anything else — any other ``OPENCODE_*``/``XDG_*`` name, any
    bench-internal endpoint, any simulator credential — stays out.
    """
    names = (
        set(_AGENT_CHILD_ENV_FIXED)
        | {"HOME", "PWD", "OPENCODE_PERMISSION"}
        | set(_AGENT_PRISTINE_XDG_VARS)
    )
    provider = (model or "").split("/")[0].strip().lower() if model and "/" in model else ""
    names.update(_AGENT_PROVIDER_KEY_VARS.get(provider, _AGENT_DEFAULT_KEY_VARS))
    names.add("FRONTIER_API_KEY")
    return frozenset(names)


def agent_child_env_allows(name: str, model: str | None = None) -> bool:
    """True when *name* may cross into an agent CLI child (REQ-091/AR-7-9).

    Pure membership in :func:`agent_child_env_allowed_names` — there is no
    open ``OPENCODE_*``/``XDG_*`` prefix pass-through anymore.
    """
    return name in agent_child_env_allowed_names(model)


def make_pristine_cli_home(base_dir: str | Path | None = None) -> Path:
    """Create a fresh empty per-run CLI home dir (REQ-091/AR-7-9).

    Under *base_dir* (the harness's temp area) when given, else the
    system temp dir. The agent CLI child runs with ``HOME`` and the XDG
    base-directory variables pinned here, so the operator's global
    OpenCode config, plugins, skills and MCP servers never load into a
    scored arm. The caller owns cleanup.
    """
    import tempfile  # noqa: PLC0415

    return Path(
        tempfile.mkdtemp(
            prefix="bench-agent-home-",
            dir=str(base_dir) if base_dir is not None else None,
        )
    )


def _pristine_home_paths(cli_home: str | Path) -> dict[str, str]:
    """HOME + XDG base-directory paths rooted at *cli_home* (REQ-091/AR-7-9).

    The XDG layout mirrors the ``HOME``-relative one (``~/.config``,
    ``~/.local/share``, ``~/.cache``, ``~/.local/state``) so the CLI finds
    the same empty tree whichever convention it resolves.
    """
    home = Path(cli_home)
    return {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
    }


def pristine_session_db_path(cli_home: str | Path) -> str:
    """Session DB path for a CLI that ran under pristine home (REQ-091/AR-7-9).

    ``~/.local/share/opencode/opencode.db`` resolved against *cli_home* —
    identical under the pinned ``HOME`` and the pinned ``XDG_DATA_HOME``.
    """
    return str(Path(cli_home) / ".local" / "share" / "opencode" / "opencode.db")


def build_agent_child_env(
    parent: Any,
    cwd: Path,
    *,
    model: str | None = None,
    cli_home: str | Path,
) -> dict[str, str]:
    """Build an allowlisted child env from *parent* for an agent CLI.

    REQ-091/AR-7-9: from scratch, not a scrubbed copy. Only the explicit
    names in :func:`agent_child_env_allowed_names` may cross — the fixed
    OS names plus the frontier provider's key variables. ``HOME`` and the
    XDG base-directory variables never cross from the parent: they are
    pinned to *cli_home* (a fresh empty per-run dir from
    :func:`make_pristine_cli_home`), so no operator-global OpenCode
    config, plugin, skill or MCP server loads. ``PWD`` is pinned to the
    sandbox dir (REQ-078: the CLI takes its working directory from the
    inherited ``PWD``) and ``OPENCODE_PERMISSION`` is set explicitly to
    :data:`AGENT_PERMISSION_CONFIG` so an inherited value can never
    loosen it. Nothing else crosses — no verifier/admin/control URLs, no
    simulator credential refs, no unrelated secrets, no other
    ``OPENCODE_*``/``XDG_*`` names.
    """
    child: dict[str, str] = {}
    pinned = {"PWD", "OPENCODE_PERMISSION", "HOME", *_AGENT_PRISTINE_XDG_VARS}
    for name in agent_child_env_allowed_names(model):
        if name in pinned:
            continue
        try:
            value = parent.get(name)
        except Exception:
            value = None
        if value is not None:
            child[name] = str(value)
    child.update(_pristine_home_paths(cli_home))
    child["PWD"] = str(cwd)
    child["OPENCODE_PERMISSION"] = json.dumps(AGENT_PERMISSION_CONFIG, sort_keys=True)
    return child


def _fresh_sandbox_copy(src: Path) -> Path:
    """Fresh copy of the harness-written sandbox files (REQ-091/AR-7-9).

    Copies only the known sandbox bundle (:data:`bench.context.SANDBOX_FILES`
    plus the harness-written ``opencode.json``) out of *src* into a new
    temp dir. Anything the first CLI invocation wrote inside the sandbox
    dir — a planted ``.opencode/`` plugin, a changed ``opencode.json``,
    any other file — stays behind, so a second CLI invocation never
    reloads agent-modified host code as its project config.
    """
    import shutil  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    from bench.context import SANDBOX_FILES  # noqa: PLC0415 (lazy: no import cycle)

    fresh = Path(tempfile.mkdtemp(prefix="bench-agent-reprompt-"))
    for name in list(SANDBOX_FILES) + ["opencode.json"]:
        candidate = src / name
        try:
            if candidate.is_file():
                shutil.copy2(candidate, fresh / name)
        except Exception:
            continue
    return fresh


def _sandbox_env(
    cwd: Path | None,
    model: str | None = None,
    *,
    cli_home: str | Path,
) -> dict[str, str] | None:
    """Child environment for an agent CLI spawned with *cwd*.

    REQ-078 (reopened 2026-11-02): agent CLIs take their working
    directory from the inherited ``PWD``, not the process cwd — a stale
    parent ``PWD`` lands the agent session in the repo instead of its
    sandbox dir, so the agent never sees its files. Pin ``PWD`` to the
    sandbox dir the harness wrote the shared context files to.
    REQ-091/AR-7-9: the env is an allowlist built from scratch (see
    :func:`build_agent_child_env`) rooted at the pristine per-run CLI
    home *cli_home* — no verifier/admin/control URLs, no simulator
    credentials, no unrelated secrets, no operator-global OpenCode
    config cross.
    ``None`` (no sandbox dir) means inherit the parent environment.
    """
    if cwd is None:
        return None
    return build_agent_child_env(os.environ, cwd, model=model, cli_home=cli_home)


class ClaudeCodeDriver:
    """Headless ``claude -p`` driver with usage capture.

    *cli_path* defaults to ``CLAUDE_CLI_PATH`` env or ``"claude"``.
    Pass a fake binary path in tests (e.g. the ``fake_claude`` script).
    """

    def __init__(
        self,
        *,
        cli_path: str | None = None,
        timeout_s: float = 600.0,
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
        import shutil as _shutil  # noqa: PLC0415

        cmd = self._build_command(prompt)
        t = timeout_s if timeout_s is not None else self.timeout_s
        self.calls.append({"cmd": cmd, "prompt": prompt, "cwd": str(cwd) if cwd else None})
        start = time.monotonic()
        # REQ-091/AR-7-9: same pristine per-run CLI home as the opencode
        # driver — never the operator's real home.
        cli_home = make_pristine_cli_home()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=t,
                # Never inherit stdin (REQ-061 item 2: same hang class as opencode).
                stdin=subprocess.DEVNULL,
                cwd=str(cwd) if cwd else None,
                # REQ-078: pin PWD to the sandbox dir; REQ-091: allowlisted
                # child env rooted at the pristine CLI home (helper docstring).
                env=_sandbox_env(cwd, model=self.model, cli_home=cli_home),
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
        finally:
            with contextlib.suppress(Exception):
                _shutil.rmtree(cli_home, ignore_errors=True)

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
            "model": "<MODEL_ID_FROM_ENV>",
            "cost_usd": 0.012
        }

    This is an adapter stub: the harness can drive it via the same
    :class:`HarnessInputs` as :class:`ClaudeCodeDriver`, and unit tests
    use :class:`FakeCodexDriver` / ``scripts/fake_codex.py`` (W1 fake).
    When the real ``codex`` binary is unavailable the documented fallback
    is :class:`BaselineLLMDriver` — routing the baseline through
    :class:`autobridge.llm.provider.LLMProvider` with an equivalent agent
    loop (plan §9, see also :func:`bench.harness.run_baseline_via_llm_provider`).

    ``cli_path`` defaults to ``CODEX_CLI_PATH`` env or ``"codex"``.
    """

    def __init__(
        self,
        *,
        cli_path: str | None = None,
        timeout_s: float = 600.0,
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
        import shutil as _shutil  # noqa: PLC0415

        cmd = self._build_command(prompt)
        t = timeout_s if timeout_s is not None else self.timeout_s
        self.calls.append({"cmd": cmd, "prompt": prompt, "cwd": str(cwd) if cwd else None})
        start = time.monotonic()
        # REQ-091/AR-7-9: same pristine per-run CLI home as the opencode
        # driver — never the operator's real home.
        cli_home = make_pristine_cli_home()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=t,
                # Never inherit stdin (REQ-061 item 2: same hang class as opencode).
                stdin=subprocess.DEVNULL,
                cwd=str(cwd) if cwd else None,
                # REQ-078: pin PWD to the sandbox dir; REQ-091: allowlisted
                # child env rooted at the pristine CLI home (helper docstring).
                env=_sandbox_env(cwd, model=self.model, cli_home=cli_home),
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
        finally:
            with contextlib.suppress(Exception):
                _shutil.rmtree(cli_home, ignore_errors=True)

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
# OpenCode CLI driver (REQ-024 B1, plan §2.10)
#
# Adapter for the ``opencode`` headless CLI (``opencode run --format json``).
# This is the default baseline driver for ``make bench`` (REQ-024: the
# untrained-frontier baseline is a coding agent driven with the same
# FRONTIER_MODEL every arm uses).  The interface mirrors
# :class:`ClaudeCodeDriver` so the harness can swap drivers without
# changing inputs; ``claude`` / ``codex`` stay available via
# :func:`get_baseline_driver`, and :class:`BaselineLLMDriver` remains the
# documented fallback when no CLI is available (plan §9).
#
# ``opencode run --format json`` streams one JSON event per line.  Event
# shapes vary by version, so :func:`parse_opencode_output` is defensive:
# it accumulates assistant text from known message shapes and takes the
# maximum input/output/reasoning counters seen (cumulative counters grow,
# so the max is the run total; per-step ``step_finish`` deltas are
# additionally summed via the session row when reachable — see
# :func:`resolve_opencode_session`).  Unknown shapes are ignored — never
# raised.  REQ-060 item 4 (shapes captured from ``opencode 1.18`` in the
# sandbox): assistant text lives in ``part.text`` of ``{"type":"text"}``
# events, usage in ``part.tokens`` (``input``/``output``/``reasoning``)
# of ``step_finish`` events, cost in ``part.cost``, and failures arrive
# as ``{"type":"error"}`` events on exit 0 — all of which the old parser
# missed (12/12 ``no_output`` / ``unknown`` / 0 tokens). The model id is
# not in the stream; the driver resolves it from the session row.
# ---------------------------------------------------------------------------


def _content_parts_text(content: Any) -> str:
    """Join ``{"type":"text","text":...}`` parts (and bare strings)."""
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for part in content:
        if isinstance(part, dict):
            t = part.get("text")
            if isinstance(t, str) and t.strip():
                parts.append(t)
        elif isinstance(part, str) and part.strip():
            parts.append(part)
    return "".join(parts)


def _opencode_event_text(event: dict[str, Any]) -> str:
    """Extract assistant text from one ``opencode run --format json`` event."""
    etype = str(event.get("type", ""))
    # Direct text/result/output/message shapes
    for key in ("text", "result", "output"):
        val = event.get(key)
        if isinstance(val, str) and val.strip():
            return val
    msg = event.get("message")
    if isinstance(msg, str) and msg.strip():
        return msg
    # Message with content parts: {"message": {"role":..,"content":[{"type":"text","text":..}]}},
    # or {"content": [...]} at top level.
    content = event.get("content")
    if isinstance(event.get("message"), dict):
        content = event["message"].get("content", content)
    text = _content_parts_text(content)
    if text:
        return text
    # REQ-060: real assistant text shape — {"type":"text","part":{"type":"text","text":...}}.
    # Reasoning/tool parts are never answer text (they would pollute patch parsing).
    part = event.get("part")
    if isinstance(part, dict) and part.get("type") == "text":
        t = part.get("text")
        if isinstance(t, str) and t.strip():
            return t
        text = _content_parts_text(part.get("content"))
        if text:
            return text
    # Delta shapes: {"type": "text_delta"/"message_delta", "delta": {"text": ...}}
    if etype in ("text_delta", "message_delta", "content_delta"):
        delta = event.get("delta")
        if isinstance(delta, dict):
            t = delta.get("text")
            if isinstance(t, str) and t:
                return t
        elif isinstance(delta, str) and delta:
            return delta
    return ""


def _usage_triple(container: Any) -> tuple[int, int, int]:
    """(input, output, reasoning) from one usage-like mapping; (0,0,0) if absent."""
    if not isinstance(container, dict):
        return (0, 0, 0)
    inp = container.get(
        "input_tokens",
        container.get(
            "inputTokens",
            container.get("input", container.get("prompt_tokens", container.get("promptTokens"))),
        ),
    )
    out = container.get(
        "output_tokens",
        container.get(
            "outputTokens",
            container.get("output", container.get("completion_tokens", container.get("completionTokens"))),
        ),
    )
    reasoning = container.get(
        "reasoning_tokens",
        container.get(
            "reasoningTokens", container.get("reasoning", container.get("thinking_tokens", container.get("thinkingTokens")))
        ),
    )
    try:
        i = int(inp) if inp is not None else 0
        o = int(out) if out is not None else 0
        r = int(reasoning) if reasoning is not None else 0
    except (TypeError, ValueError):
        return (0, 0, 0)
    if i or o or r:
        return (max(i, 0), max(o, 0), max(r, 0))
    return (0, 0, 0)


def _opencode_event_usage(event: dict[str, Any]) -> tuple[int, int, int]:
    """Extract (input_tokens, output_tokens, reasoning_tokens) from one event.

    REQ-060: the live shape is ``part.tokens`` (``input``/``output``/
    ``reasoning``) on ``step_finish`` events; cumulative shapes
    (``usage``, ``tokenUsage``, ``tokens``) are honored too.
    """
    part = event.get("part")
    if isinstance(part, dict):
        triple = _usage_triple(part.get("tokens"))
        if triple != (0, 0, 0):
            return triple
    for key in ("usage", "tokenUsage", "tokens"):
        triple = _usage_triple(event.get(key))
        if triple != (0, 0, 0):
            return triple
    return _usage_triple(event)


def _opencode_event_model(event: dict[str, Any]) -> str:
    """Best-effort model id from one event ("" when the stream says none)."""
    for container in (event, event.get("session"), event.get("part"), event.get("message")):
        if not isinstance(container, dict):
            continue
        for key in ("model", "modelID", "modelId", "model_id"):
            m = container.get(key)
            if isinstance(m, str) and m.strip():
                return m.strip()
    return ""


def _opencode_event_error(event: dict[str, Any]) -> str:
    """Error text from one ``{"type":"error"}`` event ("" when not an error)."""
    if str(event.get("type", "")) != "error":
        return ""
    err = event.get("error", event)
    if isinstance(err, str) and err.strip():
        return err.strip()
    if isinstance(err, dict):
        data = err.get("data")
        if isinstance(data, dict):
            for key in ("message", "error", "responseBody"):
                val = data.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()[:2000]
        for key in ("message", "text"):
            val = err.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()[:2000]
    return "opencode run reported an error event with no message"


def _opencode_event_session_id(event: dict[str, Any]) -> str:
    """Session id from one event ("" when absent)."""
    for key in ("sessionID", "sessionId", "session_id"):
        val = event.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def parse_opencode_output(stdout: str) -> ClaudeCliResult:
    """Parse ``opencode run --format json`` stdout.

    Accepts a JSON-lines event stream (one event per line) or a single
    JSON object (claude/codex shape for cross-driver tests).  Never
    raises: unparsable input yields empty text with zero usage. REQ-060:
    assistant text comes from ``part.text``, usage/cost from
    ``part.tokens``/``part.cost``, failures from ``{"type":"error"}``
    events (kept in :attr:`stream_error` so the driver fails loudly
    instead of scoring silence), and the session id is captured so the
    driver can resolve the authoritative model/totals.
    """
    raw = stdout.strip()
    if not raw:
        return ClaudeCliResult(text="", raw_output=stdout)
    texts: list[str] = []
    max_in = 0
    max_out = 0
    max_reasoning = 0
    total_cost = Decimal("0")
    has_cost = False
    model = ""
    session_id = ""
    errors: list[str] = []
    events: list[Any] = []
    # Whole-document JSON first (single object or array)
    try:
        whole = json.loads(raw)
        events = whole if isinstance(whole, list) else [whole]
    except ValueError:
        events = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except ValueError:
                # Plain-text line: keep as assistant text fallback
                texts.append(line)
    for event in events:
        if not isinstance(event, dict):
            if isinstance(event, str) and event.strip():
                texts.append(event)
            continue
        t = _opencode_event_text(event)
        if t:
            texts.append(t)
        i, o, r = _opencode_event_usage(event)
        max_in = max(max_in, i)
        max_out = max(max_out, o)
        max_reasoning = max(max_reasoning, r)
        part = event.get("part")
        cost_holder = part if isinstance(part, dict) and part.get("cost") is not None else event
        for cost_key in ("cost", "cost_usd", "total_cost"):
            c = cost_holder.get(cost_key) if isinstance(cost_holder, dict) else None
            if c is not None:
                with contextlib.suppress(Exception):
                    total_cost += Decimal(str(c))
                    has_cost = True
                break
        m = _opencode_event_model(event)
        if m:
            model = m
        err = _opencode_event_error(event)
        if err and err not in errors:
            errors.append(err)
        sid = _opencode_event_session_id(event)
        if sid and not session_id:
            session_id = sid
    # A claude-shaped single object may carry result text under "result"
    # while content parts were missed; whole-doc parse via parse_claude_output
    # is intentionally not reused so opencode semantics stay explicit.
    text = "".join(texts) if any("delta" in str(e.get("type", "")) for e in events if isinstance(e, dict)) else "\n".join(texts)
    return ClaudeCliResult(
        text=text,
        usage=Usage(input_tokens=max_in, output_tokens=max_out, reasoning_tokens=max_reasoning),
        model=model,
        cost_usd=total_cost if has_cost else None,
        raw_output=stdout,
        session_id=session_id,
        stream_error="\n".join(errors),
    )


def opencode_session_db_path() -> str:
    """Default opencode session database path (same user that ran the CLI)."""
    from pathlib import Path as _Path  # noqa: PLC0415

    return str(_Path.home() / ".local" / "share" / "opencode" / "opencode.db")


def resolve_opencode_session(session_id: str, *, db_path: str | None = None) -> dict[str, Any] | None:
    """Authoritative model/totals for an ``opencode run`` session (REQ-060 item 4).

    The ``--format json`` stream never names the model, and its per-step
    token events undercount multi-step runs — but the CLI records the
    run itself in its session database (model id, input/output/reasoning
    totals, cost). Returns ``{"model", "input_tokens",
    "output_tokens", "reasoning_tokens", "cost_usd"}`` (model in
    ``provider/id`` form when both are known), or ``None`` when the row
    is unreachable — the caller keeps the stream-parsed values. Never
    raises, never reads secrets (model id and counters only).
    """
    if not session_id or not session_id.strip():
        return None
    import sqlite3  # noqa: PLC0415

    path = db_path or opencode_session_db_path()
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
        try:
            row = con.execute(
                "SELECT model, cost, tokens_input, tokens_output, tokens_reasoning"
                " FROM session WHERE id = ?",
                (session_id.strip(),),
            ).fetchone()
        finally:
            con.close()
    except Exception:
        return None
    if row is None:
        return None
    model_raw, cost, tokens_in, tokens_out, tokens_reasoning = row
    model = ""
    try:
        import json as _json  # noqa: PLC0415

        parsed = _json.loads(model_raw) if isinstance(model_raw, str) else None
        if isinstance(parsed, dict):
            mid = str(parsed.get("id") or "").strip()
            provider = str(parsed.get("providerID") or parsed.get("provider") or "").strip()
            model = f"{provider}/{mid}" if provider and mid else mid
        elif isinstance(model_raw, str):
            model = model_raw.strip()
    except Exception:
        model = ""
    try:
        usage = {
            "input_tokens": max(int(tokens_in or 0), 0),
            "output_tokens": max(int(tokens_out or 0), 0),
            "reasoning_tokens": max(int(tokens_reasoning or 0), 0),
        }
    except (TypeError, ValueError):
        return {"model": model, "input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "cost_usd": None}
    cost_usd: Decimal | None = None
    with contextlib.suppress(Exception):
        cost_usd = Decimal(str(cost)) if cost is not None else None
    return {"model": model, **usage, "cost_usd": cost_usd}


def build_fake_opencode_output(
    text: str,
    *,
    input_tokens: int = 10,
    output_tokens: int = 20,
    model: str = "fake-opencode",
    cost_usd: str | float | None = None,
) -> str:
    """Build a JSON-lines string matching the ``opencode run`` event stream.

    Used by the fake CLI and by tests to generate canned stdout.
    """
    message = {"type": "message", "role": "assistant", "content": [{"type": "text", "text": text}]}
    done: dict[str, Any] = {
        "type": "done",
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        "model": model,
    }
    if cost_usd is not None:
        done["cost_usd"] = cost_usd
    return json.dumps(message) + "\n" + json.dumps(done)


class OpenCodeCliError(ClaudeCliError):
    """Raised when the opencode CLI exits non-zero or times out."""


class OpenCodeCliDriver:
    """Headless ``opencode run`` driver with usage capture (REQ-024 B1).

    Default baseline driver for ``make bench``.  Command::

        opencode run --format json [-m <provider/model>] <prompt>

    *model* defaults to :func:`autobridge.config.opencode_model`
    (env ``OPENCODE_MODEL``, else derived from ``FRONTIER_MODEL`` in
    ``provider/model`` form — REQ-061 item 1, so the agent arm always runs
    the same model as the one-shot and ``-m`` is always passed).  REQ-024
    requires every frontier arm to use the same model — the bench records
    both ids per arm so a mismatch is visible in the scorecard.

    REQ-060 item 4: the parsed stream misses the model id and undercounts
    multi-step runs, so after a successful run the driver resolves the
    authoritative model/totals from the CLI's own session row
    (:func:`resolve_opencode_session`, keyed by the stream's session
    id). A text-less stream with ``{"type":"error"}`` events raises
    instead of returning silence (the 12/12 ``no_output`` rows were
    unparsed text/usage plus unreported errors on exit 0).

    *cli_path* defaults to ``OPENCODE_CLI_PATH`` env or ``"opencode"``.
    *session_db_path* overrides the session database path (tests only;
    ``None`` means the default location).
    """

    def __init__(
        self,
        *,
        cli_path: str | None = None,
        timeout_s: float = 600.0,
        model: str | None = None,
        extra_args: list[str] | None = None,
        session_db_path: str | None = None,
    ) -> None:
        self.cli_path = cli_path or os.environ.get("OPENCODE_CLI_PATH", "") or "opencode"
        self.timeout_s = timeout_s
        if model is None:
            try:
                from autobridge.config import opencode_model as _opencode_model  # noqa: PLC0415

                model = _opencode_model() or None
            except Exception:
                model = None
        self.model = model
        self.extra_args = list(extra_args or [])
        self.session_db_path = session_db_path
        self.calls: list[dict[str, Any]] = []

    def _build_command(self, prompt: str) -> list[str]:
        cmd: list[str] = [self.cli_path, "run", "--format", "json"]
        if self.model:
            cmd.extend(["-m", self.model])
        cmd.extend(self.extra_args)
        cmd.append(prompt)
        return cmd

    def run(
        self,
        prompt: str,
        *,
        cwd: Path | None = None,
        timeout_s: float | None = None,
    ) -> ClaudeCliResult:
        """Execute ``opencode run --format json`` with *prompt*.

        REQ-078 item 6 (frontier-agent isolation): the agent is restricted
        to its sandbox dir and the vendor sandbox URL only. The run
        requires *cwd* (the sandbox dir the harness wrote the shared
        context files to — never the repo); the child environment is an
        allowlist built from scratch (REQ-091/AR-7-9: fixed OS names plus
        the frontier provider's key variables only — no open
        ``OPENCODE_*``/``XDG_*`` pass-through; ``HOME`` and the XDG base
        directories pinned to a fresh empty per-run CLI home, ``PWD``
        pinned to the sandbox dir, ``OPENCODE_PERMISSION`` set to the
        default-deny config), so the agent cannot regenerate the instance
        from the committed protocol, read generator/verifier sources,
        reach the unauthenticated ``/beacon/records`` endpoint as an
        oracle, write a project plugin it then reloads, or inherit the
        operator's global OpenCode config — it answers inline from the
        evidence bundle and never writes. Equal information holds both
        ways: the prompt + sandbox files are the agent's whole world,
        exactly the engine's equal inputs.
        """
        import dataclasses as _dataclasses  # noqa: PLC0415
        import os as _os  # noqa: PLC0415
        import shutil as _shutil  # noqa: PLC0415

        if cwd is None:
            raise OpenCodeCliError(
                "opencode run refused: no sandbox dir (cwd) — the agent "
                "runs only inside its sandbox dir, never the repo"
            )
        cmd = self._build_command(prompt)
        t = timeout_s if timeout_s is not None else self.timeout_s
        self.calls.append({"cmd": cmd, "prompt": prompt, "cwd": str(cwd) if cwd else None})
        start = time.monotonic()
        # REQ-091/AR-7-9 item 2: fresh empty per-run CLI home — the child
        # HOME/XDG_* point here, never at the operator's real home.
        # Cleaned up best-effort at the end (the session row is resolved
        # before that, and the grades carry the outcome).
        cli_home = make_pristine_cli_home()
        try:
            return self._run_in_home(
                cmd,
                cwd,
                cli_home,
                t,
                start,
                _os=_os,
                _dataclasses=_dataclasses,
            )
        finally:
            with contextlib.suppress(Exception):
                _shutil.rmtree(cli_home, ignore_errors=True)

    def _session_db_candidates(self, cli_home: Path) -> list[str]:
        """Session DB paths to try, pristine home first (REQ-091/AR-7-9)."""
        candidates = [pristine_session_db_path(cli_home)]
        fallback = self.session_db_path or opencode_session_db_path()
        if fallback not in candidates:
            candidates.append(fallback)
        return candidates

    def _apply_session_row(
        self,
        result: ClaudeCliResult,
        session_id: str,
        cli_home: Path,
        _dataclasses: Any,
    ) -> ClaudeCliResult:
        """Resolve model/totals from the CLI's own session row (REQ-060 item 4).

        The stream never names the model and undercounts multi-step runs.
        The row is read from the pristine per-run CLI home the child ran
        under (falling back to the configured/default path); keyed by the
        stream's session id, so a foreign row can never attach.
        """
        for db_path in self._session_db_candidates(cli_home):
            resolved = resolve_opencode_session(session_id, db_path=db_path)
            if resolved is None:
                continue
            usage = result.usage
            if resolved.get("model"):
                result = _dataclasses.replace(result, model=str(resolved["model"]))
            if (resolved.get("input_tokens", 0) or 0) + (resolved.get("output_tokens", 0) or 0) > 0:
                usage = Usage(
                    input_tokens=int(resolved.get("input_tokens", 0) or 0),
                    output_tokens=int(resolved.get("output_tokens", 0) or 0),
                    reasoning_tokens=int(resolved.get("reasoning_tokens", 0) or 0),
                )
                result = _dataclasses.replace(result, usage=usage)
            if resolved.get("cost_usd") is not None:
                result = _dataclasses.replace(result, cost_usd=resolved["cost_usd"])
            return result
        return result

    def _run_in_home(
        self,
        cmd: list[str],
        cwd: Path,
        cli_home: Path,
        t: float,
        start: float,
        *,
        _os: Any,
        _dataclasses: Any,
    ) -> ClaudeCliResult:
        import shutil as _shutil_inner  # noqa: PLC0415

        child_env = build_agent_child_env(_os.environ, cwd, model=self.model, cli_home=cli_home)
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=t,
                # REQ-061 item 2: never inherit stdin — opencode would wait
                # on it (the sandbox hang) instead of running headless.
                stdin=subprocess.DEVNULL,
                cwd=str(cwd),
                env=child_env,
            )
        except subprocess.TimeoutExpired as e:
            raise OpenCodeCliError(
                f"opencode CLI timed out after {t}s",
                stderr=getattr(e, "stderr", "") or "",
                stdout=getattr(e, "stdout", "") or "",
            ) from e
        except FileNotFoundError as e:
            raise OpenCodeCliError(
                f"opencode CLI not found: {self.cli_path!r} — "
                "fallback: use BaselineLLMDriver via LLMProvider (plan §9)",
                stderr=str(e),
            ) from e

        elapsed = (time.monotonic() - start) * 1000.0
        if proc.returncode != 0:
            # REQ-061 item 3: a non-zero exit usually leaves stderr empty
            # and the reason on stdout as {"type":"error"} events — parse
            # them and carry the stdout tail so the ablation row's error
            # names the actual cause instead of "exit 1: ".
            stream_error = ""
            with contextlib.suppress(Exception):
                stream_error = parse_opencode_output(proc.stdout).stream_error.strip()
            stderr_tail = proc.stderr.strip()[:500]
            stdout_tail = proc.stdout.strip()[-1000:]
            detail = " | ".join(p for p in (stderr_tail, stream_error[:1000], stdout_tail) if p)
            raise OpenCodeCliError(
                f"opencode CLI failed with exit {proc.returncode}: {detail[:2000]}",
                exit_code=proc.returncode,
                stderr=proc.stderr,
                stdout=proc.stdout,
            )
        result = parse_opencode_output(proc.stdout)
        if result.session_id:
            result = self._apply_session_row(result, result.session_id, cli_home, _dataclasses)
        result.exit_code = proc.returncode
        result.duration_ms = elapsed
        # REQ-081 item 7 / REQ-091 item 3: the stream may end on a tool
        # step with no final text — re-prompt once in the same session
        # for the final answer instead of scoring no_output. The re-prompt
        # runs in a freshly written copy of the original sandbox files
        # (same pristine CLI home, so ``--session`` still continues), never
        # in the directory the first invocation may have modified.
        if not result.text.strip() and result.session_id:
            try:
                _re_cmd = list(cmd)
                # Reuse the same model flag; continue the same session.
                _re_prompt = (
                    "Please provide your final answer now as one JSON object "
                    "only (the patch or escalation described in the task — no "
                    "tool calls needed)."
                )
                # Insert --session <id> after "run" so the same session
                # continues: opencode run --format json -m <model> --session
                # <id> <prompt>.
                try:
                    _run_idx = _re_cmd.index("run")
                except ValueError:
                    _run_idx = 1
                _re_cmd = (
                    _re_cmd[: _run_idx + 1]
                    + ["--session", str(result.session_id)]
                    + _re_cmd[_run_idx + 1 :]
                )
                _re_cmd = _re_cmd[:-1] + [_re_prompt] if _re_cmd else [_re_prompt]
                _re_cwd = _fresh_sandbox_copy(Path(str(cwd)))
                try:
                    _re_env = build_agent_child_env(
                        _os.environ, _re_cwd, model=self.model, cli_home=cli_home
                    )
                    _re_proc = subprocess.run(
                        _re_cmd,
                        capture_output=True,
                        text=True,
                        timeout=t,
                        stdin=subprocess.DEVNULL,
                        cwd=str(_re_cwd),
                        env=_re_env,
                    )
                finally:
                    # The fresh copy served one re-prompt; remove it
                    # best-effort (the grades carry the outcome).
                    with contextlib.suppress(Exception):
                        _shutil_inner.rmtree(_re_cwd, ignore_errors=True)
                if _re_proc.returncode == 0:
                    _re_result = parse_opencode_output(_re_proc.stdout)
                    if _re_result.text.strip():
                        if _re_result.session_id:
                            _re_result = self._apply_session_row(
                                _re_result, _re_result.session_id, cli_home, _dataclasses
                            )
                        # Merge usage: keep the max counters from both runs.
                        try:
                            _in = max(
                                int(result.usage.input_tokens or 0),
                                int(_re_result.usage.input_tokens or 0),
                            )
                            _out = max(
                                int(result.usage.output_tokens or 0),
                                int(_re_result.usage.output_tokens or 0),
                            )
                            _rea = max(
                                int(result.usage.reasoning_tokens or 0),
                                int(_re_result.usage.reasoning_tokens or 0),
                            )
                            _merged_usage = Usage(
                                input_tokens=_in,
                                output_tokens=_out,
                                reasoning_tokens=_rea,
                            )
                        except Exception:
                            _merged_usage = _re_result.usage
                        result = _dataclasses.replace(
                            _re_result,
                            usage=_merged_usage,
                            session_id=result.session_id,
                            exit_code=_re_proc.returncode,
                            duration_ms=(time.monotonic() - start) * 1000.0,
                        )
            except Exception:
                pass
        if not result.text.strip() and result.stream_error.strip():
            # The CLI exited 0 but reported only errors: fail loudly with
            # the reason instead of a silent no_output/unknown/0 row.
            raise OpenCodeCliError(
                f"opencode run produced no text: {result.stream_error.strip()[:1000]}",
                exit_code=proc.returncode,
                stderr=proc.stderr,
                stdout=proc.stdout,
            )
        return result

    @property
    def default_model(self) -> str | None:
        """The model this driver runs with (``-m`` value or ``None``).

        Duck-types :class:`FakeOpenCodeDriver` / :class:`FakeClaudeDriver`
        so provenance readers never crash on a live driver (REQ-060 item 5).
        """
        return self.model

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


class FakeOpenCodeDriver:
    """In-memory fake for the opencode CLI (no subprocess).

    Mirrors :class:`FakeClaudeDriver` so tests can exercise the default
    baseline driver without a real binary.
    """

    def __init__(
        self,
        responses: dict[str, ClaudeCliResult | str | Any] | None = None,
        *,
        default_text: str = "fake opencode response",
        default_usage: Usage | None = None,
        default_model: str = "fake-opencode",
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
            raw_output=build_fake_opencode_output(
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
                raw_output=build_fake_opencode_output(
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
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> None:
        # Lazy import to avoid cycle
        if provider is None:
            from autobridge.llm.provider import FakeProvider  # noqa: PLC0415

            provider = FakeProvider()
        if max_tokens is None:
            # REQ-052: same output room as the engine's L3 (never the old
            # 4096 cap that truncated reasoning models).
            try:
                from autobridge.config import frontier_max_tokens  # noqa: PLC0415

                max_tokens = frontier_max_tokens()
            except Exception:
                max_tokens = 131072
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
            # Surface as failed result via exception — harness catches and marks arm ran=False
            raise ClaudeCliError(
                f"BaselineLLMDriver failed: {type(exc).__name__}: {exc}",
                stderr=str(exc),
            ) from exc

    @property
    def last_prompt(self) -> str | None:
        return self.calls[-1]["prompt"] if self.calls else None


def get_baseline_driver(
    kind: str = "opencode",
    *,
    provider: Any | None = None,
    cli_path: str | None = None,
    timeout_s: float = 600.0,
    model: str | None = None,
) -> Any:
    """Factory returning a baseline driver by *kind*.

    * ``"opencode"`` → :class:`OpenCodeCliDriver` (default, REQ-024: the
      untrained-frontier baseline is a coding agent on the same
      FRONTIER_MODEL every arm uses)
    * ``"claude"`` → :class:`ClaudeCodeDriver`
    * ``"codex"`` → :class:`CodexCliDriver`
    * ``"llm"`` / ``"fallback"`` → :class:`BaselineLLMDriver` (plan §9)

    The factory exists so ``make bench`` and tests can select the baseline
    without importing driver classes directly.  The fallback is
    :class:`BaselineLLMDriver` — routing through :class:`LLMProvider`
    with the same ``Budget`` ceilings the harness uses for AutoBridge,
    documented in :mod:`bench.harness` and ``docs/benchmark.md``.
    """
    kind_norm = kind.lower().strip()
    if kind_norm in ("opencode", "opencode_cli"):
        return OpenCodeCliDriver(cli_path=cli_path, timeout_s=timeout_s, model=model)
    if kind_norm in ("claude", "claude_code", "claude_code_headless"):
        return ClaudeCodeDriver(cli_path=cli_path, timeout_s=timeout_s, model=model)
    if kind_norm in ("codex", "codex_cli"):
        return CodexCliDriver(cli_path=cli_path, timeout_s=timeout_s, model=model)
    if kind_norm in ("llm", "fallback", "provider", "baseline_llm"):
        return BaselineLLMDriver(provider=provider, model=model)
    if kind_norm in ("one_shot", "oneshot", "one-shot"):
        # Lazy import to avoid cycle (one_shot imports from this module)
        from bench.one_shot import OneShotGenerator  # noqa: PLC0415

        return OneShotGenerator(provider=provider, model=model)
    raise ValueError(f"unknown baseline driver kind {kind!r}; expected 'opencode', 'claude', 'codex', 'llm' or 'one_shot'")
