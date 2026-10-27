"""One-shot generator — secondary diagnostic control (P1.W8.3, plan §2.10).

The baseline (``claude_code_headless`` / ``codex_cli``) is an *agentic* loop:
it gets a prompt, tools and a wall-clock budget and may iterate.  The
``one_shot_generator`` is the diagnostic contrast: a single ``LLMProvider``
completion that must produce a ``ConnectorSpec`` YAML in one pass, with the
same :class:`autobridge.bench.harness.HarnessInputs` the other two arms
receive.  If the agentic baseline beats the one-shot, the gain is from tool
use / iteration, not just model size.

Matched-budget contract
------------------------
The generator receives the *same* :class:`HarnessInputs` as AutoBridge and the
baseline:

* ``docs_volume`` / ``docs_ref`` — read-only docs mount + scenario ref
* ``sandbox_url`` / ``sandbox_image`` — sandbox to target
* ``credential_refs`` / ``visible_tests`` — credential names + visible tests
* ``token_ceiling`` / ``wall_clock_s`` — Budget ceilings
* ``runtime_target`` — ``{"kind": "docker_sandbox", ...}``

The harness self-test :func:`autobridge.bench.harness.assert_inputs_equal`
still holds: construct inputs once via :func:`make_trial_inputs` and pass
copies to each arm.

All model calls go through :class:`autobridge.llm.provider.LLMProvider` +
:class:`Budget` and are counted toward ``ModelCall`` / ``frontier_tokens``
(cost via :func:`estimate_cost`).  ``BudgetExhausted`` escalates rather than
silently retrying (DIRECTIVES §3).

Spec extraction
---------------
Model text may contain a bare YAML document or a fenced block
`````yaml ... `````.  :func:`extract_spec_yaml` normalizes either shape.
:func:`try_parse_spec_yaml` returns a mapping when valid YAML is found,
otherwise ``None`` (the arm still succeeds — scoring via verifier is W8.4).
"""

from __future__ import annotations

import pathlib
import re
import time
from dataclasses import dataclass, field
from decimal import Decimal  # noqa: TC003
from typing import TYPE_CHECKING, Any

from autobridge.llm.provider import Budget, BudgetedProvider, Usage, estimate_cost

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Docs helpers — same volume the harness mounts for both arms
# ---------------------------------------------------------------------------


def load_docs_text(
    inputs: Any,
    *,
    max_chars: int = 120_000,
) -> str:
    """Load docs text for *inputs* from the local filesystem.

    Tries *inputs.docs_ref* as a path, then *inputs.docs_volume* + scenario
    subdir, then returns an empty string when no file exists (host run
    outside Docker — the prompt still carries the ref so the harness remains
    reproducible).

    The caller may pass the result to :func:`build_one_shot_prompt`.
    Truncated to *max_chars* to stay under the token ceiling.
    """
    candidates: list[pathlib.Path] = []
    # Direct path (may be absolute or relative)
    try:
        ref = str(getattr(inputs, "docs_ref", ""))
        if ref:
            candidates.append(pathlib.Path(ref))
            # Also try relative to docs_volume
            dv = str(getattr(inputs, "docs_volume", ""))
            if dv:
                candidates.append(pathlib.Path(dv) / pathlib.Path(ref).name)
                candidates.append(pathlib.Path(dv) / ref)
    except Exception:
        pass
    # Volume root
    try:
        dv2 = str(getattr(inputs, "docs_volume", ""))
        if dv2:
            candidates.append(pathlib.Path(dv2))
            # Scenario subdir fallback
            sid = str(getattr(inputs, "scenario_id", ""))
            if sid:
                candidates.append(pathlib.Path(dv2) / sid)
                # Try common doc file names inside
                for name in ("openapi.yaml", "openapi.json", "README.md", "docs.md"):
                    candidates.append(pathlib.Path(dv2) / sid / name)
    except Exception:
        pass

    for p in candidates:
        try:
            if p.is_file():
                txt = p.read_text(encoding="utf-8", errors="replace")
                return txt[:max_chars] if len(txt) > max_chars else txt
            if p.is_dir():
                # Concatenate small files in directory
                parts: list[str] = []
                for child in sorted(p.glob("*")):
                    if child.is_file() and child.suffix in (".md", ".yaml", ".yml", ".json", ".txt"):
                        try:
                            t = child.read_text(encoding="utf-8", errors="replace")
                            parts.append(f"# {child.name}\n{t}")
                            if sum(len(x) for x in parts) > max_chars:
                                break
                        except Exception:
                            continue
                if parts:
                    joined = "\n\n".join(parts)
                    return joined[:max_chars] if len(joined) > max_chars else joined
        except Exception:
            continue
    return ""


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def build_one_shot_prompt(
    inputs: Any,
    docs_text: str | None = None,
) -> str:
    """Build the one-shot prompt for *inputs*.

    The prompt carries the same context both other arms receive:

    * scenario id + trial + seed (for reproducibility audit)
    * docs_volume / docs_ref (mount + scenario ref)
    * sandbox_url / sandbox_image / runtime_target
    * credential_refs / visible_tests (names only, never values)
    * token_ceiling / wall_clock_s

    When *docs_text* is given it is appended after the instruction block
    (truncated by the caller to stay under budget).

    Secrets policy (DIRECTIVES §6): only ``credential_ref`` names appear;
    values are never in the prompt (read at runtime from env inside the
    arm that needs them).
    """
    scenario_id = getattr(inputs, "scenario_id", "unknown")
    trial = getattr(inputs, "trial", 0)
    seed = getattr(inputs, "seed", 0)
    docs_volume = getattr(inputs, "docs_volume", "")
    docs_ref = getattr(inputs, "docs_ref", "")
    sandbox_url = getattr(inputs, "sandbox_url", "")
    sandbox_image = getattr(inputs, "sandbox_image", "")
    credential_refs = getattr(inputs, "credential_refs", ())
    visible_tests = getattr(inputs, "visible_tests", ())
    token_ceiling = getattr(inputs, "token_ceiling", 0)
    wall_clock_s = getattr(inputs, "wall_clock_s", 0)
    runtime_target = getattr(inputs, "runtime_target", {})

    creds_str = ", ".join(credential_refs) if credential_refs else "(none)"
    tests_str = ", ".join(visible_tests) if visible_tests else "(none)"

    parts: list[str] = []
    parts.append(
        "You are a ConnectorSpec generator for AutoBridge (declarative YAML/JSON, no code).\n"
        f"Scenario: {scenario_id!r} trial={trial} seed={seed}\n"
        f"Docs volume: {docs_volume!r}  scenario docs ref: {docs_ref!r}\n"
        f"Sandbox URL: {sandbox_url!r}  image: {sandbox_image!r}  runtime_target: {runtime_target!r}\n"
        f"Credential refs (env key names only): {creds_str}\n"
        f"Visible tests (what a passing spec must satisfy): {tests_str}\n"
        f"Token ceiling: {token_ceiling}  wall-clock: {wall_clock_s}s\n"
        "\n"
        "Task: produce a single ConnectorSpec YAML for this scenario.  Use only the closed transform set "
        "(date_local, datetime_utc, money, enum_map, flatten, coalesce, const, concat, business_date) "
        "and JSONPath subset — no generated code.  Money must be Decimal; dates are property-local business dates.  "
        "Auth must use credential_ref (env key name), never an inline secret value.\n"
        "\n"
        "Output ONLY the YAML document (no prose).  If you use a fence, use ```yaml ... ```."
    )
    if docs_text:
        parts.append("\n# Vendor docs\n")
        parts.append(docs_text)
    parts.append(
        "\n\nRespond with the ConnectorSpec YAML now.  No explanation, no tool calls."
    )
    return "".join(parts)


# ---------------------------------------------------------------------------
# YAML extraction helpers
# ---------------------------------------------------------------------------

_YAML_FENCE_RE = re.compile(r"```(?:yaml|yml)?\s*\n?(.*?)\n?```", re.DOTALL | re.IGNORECASE)


def extract_spec_yaml(text: str) -> str:
    """Extract YAML body from *text*.

    If *text* contains a fenced block `````yaml ... ````` the first such block
    is returned (stripped).  Otherwise *text* stripped is returned.  Empty
    input returns ``""``.
    """
    if not text or not text.strip():
        return ""
    m = _YAML_FENCE_RE.search(text)
    if m:
        body = m.group(1).strip()
        # If multiple fences, prefer the largest candidate that looks like YAML
        # (contains ':').  Otherwise keep first.
        if body and ":" in body:
            return body
        # Search for any fenced block containing ':'
        for cand in _YAML_FENCE_RE.finditer(text):
            c = cand.group(1).strip()
            if ":" in c and len(c) > len(body):
                body = c
        return body
    return text.strip()


def try_parse_spec_yaml(text: str) -> dict[str, Any] | None:
    """Try to parse extracted YAML in *text* as a mapping.

    Returns the mapping when *text* parses to a dict, else ``None`` (never
    raises).  Empty or non-YAML input returns ``None``.
    """
    yaml_text = extract_spec_yaml(text)
    if not yaml_text:
        return None
    try:
        import yaml  # noqa: PLC0415

        data = yaml.safe_load(yaml_text)
        if isinstance(data, dict):
            return data
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# OneShotGenerator — single LLM call via LLMProvider + Budget
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class OneShotResult:
    """Result of a single one-shot generation (model text + usage)."""

    text: str = ""
    spec_yaml: str = ""
    spec_dict: dict[str, Any] | None = None
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    cost_usd: Decimal | None = None
    duration_ms: float = 0.0
    raw_output: str = ""

    @property
    def total_tokens(self) -> int:
        return self.usage.total_tokens


class OneShotGenerator:
    """Single-call generator via :class:`LLMProvider` + :class:`Budget`.

    This is the secondary diagnostic control for the benchmark (plan §2.10,
    P1.W8.3): one ``LLMProvider.complete`` against the same
    :class:`HarnessInputs` the agentic baselines receive.  Token / wall-clock
    ceilings are enforced through :class:`Budget` / :class:`BudgetedProvider`
    so the harness matched-budget self-test still holds.

    Parameters
    ----------
    provider:
        Any :class:`LLMProvider` (``AnthropicProvider`` live, ``FakeProvider``
        in tests).  When ``None`` a :class:`FakeProvider` is created.
    budget:
        Optional :class:`Budget`.  When given the call is wrapped in
        :class:`BudgetedProvider` so ``max_frontier_tokens`` / ``max_wall``
        from :class:`HarnessInputs` are enforced (recommended for harness
        use).
    tier:
        Model tier for the call (``"frontier"`` or ``"small"``).  Defaults
        to ``"frontier"`` to match the harness ``token_ceiling``.
    max_tokens:
        Per-call completion limit.
    model:
        Override model id for the result label (else from provider's
        ``model_for_tier``).
    docs_loader:
        Optional callable ``(HarnessInputs) -> str`` to inject docs text
        into the prompt (for tests).  When ``None`` :func:`load_docs_text`
        is used.
    """

    def __init__(
        self,
        provider: Any | None = None,
        *,
        budget: Budget | None = None,
        tier: str = "frontier",
        max_tokens: int = 4096,
        model: str | None = None,
        docs_loader: Any | None = None,
    ) -> None:
        if provider is None:
            from autobridge.llm.provider import FakeProvider  # noqa: PLC0415

            provider = FakeProvider()
        self.provider = provider
        self.budget = budget
        self.tier = tier
        self.max_tokens = max_tokens
        self.model_name = model or getattr(provider, "frontier_model", None) or getattr(provider, "small_model", None) or "one-shot-fallback"
        self.docs_loader = docs_loader
        self.calls: list[dict[str, Any]] = []

    def _resolve_model(self) -> str:
        try:
            fn = getattr(self.provider, "model_for_tier", None)
            if callable(fn):
                return fn(self.tier)
        except Exception:
            pass
        return self.model_name

    def _model_cost(self, usage: Usage) -> Decimal | None:
        try:
            return estimate_cost(self.tier, usage)
        except Exception:
            return None

    def _build_prompt(self, inputs: Any) -> str:
        docs_text: str | None = None
        if self.docs_loader is not None:
            try:
                docs_text = self.docs_loader(inputs)
            except Exception:
                docs_text = None
        else:
            try:
                docs_text = load_docs_text(inputs)
            except Exception:
                docs_text = None
        return build_one_shot_prompt(inputs, docs_text=docs_text)

    def run(
        self,
        prompt: str,
        *,
        cwd: Path | None = None,
        timeout_s: float | None = None,
    ) -> OneShotResult:
        """Execute a single completion for *prompt*.

        Compatible shape with :class:`ClaudeCodeDriver.run` /
        :class:`CodexCliDriver.run` (``prompt, cwd=None``) so the harness
        can swap drivers without changing :class:`HarnessInputs`.  Returns
        :class:`OneShotResult` (not ``ClaudeCliResult``) — callers may adapt
        via :func:`run_one_shot_arm`.

        Raises :class:`ClaudeCliError` on transport / budget failure so the
        harness can mark the arm ``success=False`` uniformly.
        """
        _ = (cwd, timeout_s)  # kept for driver parity; wall-clock is via Budget
        self.calls.append({"prompt": prompt, "cwd": str(cwd) if cwd else None})
        start = time.monotonic()
        try:
            if self.budget is not None:
                inner = BudgetedProvider(self.provider, self.budget)
                comp = inner.complete(
                    [{"role": "user", "content": prompt}],
                    max_tokens=self.max_tokens,
                    tier=self.tier,
                    purpose="bench-one-shot",
                )
            else:
                comp = self.provider.complete(
                    [{"role": "user", "content": prompt}],
                    max_tokens=self.max_tokens,
                    tier=self.tier,
                    purpose="bench-one-shot",
                )
            elapsed = (time.monotonic() - start) * 1000.0
            yaml_body = extract_spec_yaml(comp.text)
            spec_d = try_parse_spec_yaml(comp.text)
            return OneShotResult(
                text=comp.text,
                spec_yaml=yaml_body,
                spec_dict=spec_d,
                usage=comp.usage,
                model=self._resolve_model(),
                cost_usd=self._model_cost(comp.usage),
                duration_ms=elapsed,
                raw_output=comp.text,
            )
        except Exception as exc:
            # Preserve BudgetExhausted semantics: surface so harness marks failure
            from autobridge.bench.baseline_coding_agent import ClaudeCliError  # noqa: PLC0415

            # Re-raise BudgetExhausted as ClaudeCliError with cause so callers
            # that catch ClaudeCliError handle both uniformly, while callers
            # that specifically catch BudgetExhausted still see the cause chain.
            raise ClaudeCliError(
                f"OneShotGenerator failed: {type(exc).__name__}: {exc}",
                stderr=str(exc),
            ) from exc

    def generate(
        self,
        inputs: Any,
    ) -> OneShotResult:
        """Build prompt from *inputs* and execute one completion.

        Convenience wrapper used by :func:`run_one_shot_arm` and by
        ``run_trial_with_one_shot``.  Docs are loaded via ``docs_loader``
        or :func:`load_docs_text`.
        """
        prompt = self._build_prompt(inputs)
        # Record association with scenario for audit
        self.calls.append({"inputs": getattr(inputs, "scenario_id", None), "via": "generate"})
        # Pop the marker before run appends the real entry — keep calls clean
        # (the extra entry is for debug; we remove it to keep call count = 1 per generate)
        self.calls.pop()
        return self.run(prompt)

    @property
    def last_prompt(self) -> str | None:
        return self.calls[-1]["prompt"] if self.calls and "prompt" in self.calls[-1] else None


# ---------------------------------------------------------------------------
# Harness-level helper — ArmResult adapter (so scoring sees a uniform shape)
# ---------------------------------------------------------------------------


def run_one_shot_arm(
    inputs: Any,
    *,
    provider: Any | None = None,
    budget: Budget | None = None,
    tier: str = "frontier",
    max_tokens: int = 4096,
    model: str | None = None,
    docs_loader: Any | None = None,
) -> Any:  # ArmResult
    """Run the one-shot arm for *inputs* via ``LLMProvider``.

    Returns an :class:`autobridge.bench.harness.ArmResult` with
    ``arm="one_shot"`` so the bench scorer can report it alongside
    ``autobridge`` and ``baseline``.  On any error an ``ArmResult`` with
    ``success=False`` and ``error`` is returned (never raises except for
    programming errors).

    The Budget ceilings default to ``inputs.token_ceiling`` /
    ``inputs.wall_clock_s`` when no explicit ``budget`` is given — this
    preserves the harness matched-budget contract (same ceilings for all
    arms).
    """
    from autobridge.bench.harness import ArmResult  # noqa: PLC0415

    # Default budget from harness inputs when caller did not supply one
    if budget is None:
        try:
            tc = int(getattr(inputs, "token_ceiling", 0) or 0)
            wc = float(getattr(inputs, "wall_clock_s", 0) or 0)
            budget = Budget(
                max_frontier_tokens=tc if tc > 0 else None,
                max_small_tokens=tc if tc > 0 else None,
                max_wall=wc if wc > 0 else None,
            )
        except Exception:
            budget = None

    gen = OneShotGenerator(
        provider=provider,
        budget=budget,
        tier=tier,
        max_tokens=max_tokens,
        model=model,
        docs_loader=docs_loader,
    )
    start = time.monotonic()
    try:
        result = gen.generate(inputs)
        elapsed = (time.monotonic() - start) * 1000.0
        # Prefer generator's duration when available
        elapsed = result.duration_ms or elapsed
        spec_payload: dict[str, Any] | None = None
        if result.spec_dict is not None:
            spec_payload = result.spec_dict
        return ArmResult(
            arm="one_shot",
            inputs=inputs,
            success=True,
            elapsed_ms=elapsed,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            frontier_tokens=result.usage.total_tokens if tier == "frontier" else 0,
            cost_usd=result.cost_usd,
            model=result.model,
            spec=spec_payload,
            tool_calls=0,
            model_calls=1,
        )
    except Exception as exc:
        elapsed = (time.monotonic() - start) * 1000.0
        # Unwrap to give user the budget reason when present
        cause = getattr(exc, "__cause__", None)
        if cause is not None:
            from autobridge.llm.provider import BudgetExhausted  # noqa: PLC0415

            if isinstance(cause, BudgetExhausted):
                return ArmResult(
                    arm="one_shot",
                    inputs=inputs,
                    success=False,
                    elapsed_ms=elapsed,
                    error=f"{type(cause).__name__}: {cause}",
                    model=model or tier,
                )
        return ArmResult(
            arm="one_shot",
            inputs=inputs,
            success=False,
            elapsed_ms=elapsed,
            error=f"{type(exc).__name__}: {exc}"[:2000],
            model=model or tier,
        )


# ---------------------------------------------------------------------------
# Three-way trial — autobridge + baseline + one_shot with matched audit
# ---------------------------------------------------------------------------


def run_trial_with_one_shot(
    config: Any | None = None,
    scenario_id: str = "",
    trial: int = 0,
    *,
    autobridge_runner: Any | None = None,
    baseline_runner: Any | None = None,
    one_shot_runner: Any | None = None,
    one_shot_provider: Any | None = None,
) -> Any:  # TrialResult variant with one_shot
    """Run one scenario × trial with three arms under matched budget.

    1. Build :class:`HarnessInputs` once via :func:`make_trial_inputs`.
    2. Clone it for three arms and assert :func:`assert_inputs_equal`
       (harness self-test — all three arms receive identical docs volume,
       sandbox URL, credentials, visible tests, token ceiling and wall-clock).
    3. Run each arm (each receives the identical inputs).
    4. Return a :class:`TrialResult` extended with ``one_shot``.

    For backward compatibility the returned object is a
    :class:`autobridge.bench.harness.TrialResult` with ``one_shot`` set;
    two-arm callers unaffected (``one_shot`` defaults to ``None`` in
    :func:`autobridge.bench.harness.run_trial`).

    *one_shot_runner* is ``(HarnessInputs) -> ArmResult``.  When ``None``
    and *one_shot_provider* is given (or even when both are ``None``) the
    default :func:`run_one_shot_arm` is used so the one-shot arm is
    runnable in unit tests without extras.
    """
    from autobridge.bench.harness import (  # noqa: PLC0415
        HarnessConfig,
        TrialResult,
        assert_budget_fields_equal,
        assert_inputs_equal,
        make_trial_inputs,
        run_autobridge_arm,
        run_baseline_arm,
    )

    # Always include one_shot as diagnostic; callers that need strict two-arm
    # shape should call harness.run_trial directly.

    cfg = config or HarnessConfig()
    if not scenario_id:
        raise ValueError("scenario_id must be non-empty")

    inputs = make_trial_inputs(cfg, scenario_id, trial)
    inputs_a = inputs
    inputs_b = make_trial_inputs(cfg, scenario_id, trial)
    inputs_c = make_trial_inputs(cfg, scenario_id, trial)

    matched = True
    matched_error: str | None = None
    try:
        assert_inputs_equal(inputs_a, inputs_b)
        assert_inputs_equal(inputs_b, inputs_c)
        assert_budget_fields_equal(inputs_a, inputs_c)
    except AssertionError as exc:
        matched = False
        matched_error = str(exc)

    # Run autobridge arm
    if autobridge_runner is not None:
        ab_result = autobridge_runner(inputs_a)
    else:
        ab_result = run_autobridge_arm(inputs_a)

    # Run baseline arm
    if baseline_runner is not None:
        bl_result = baseline_runner(inputs_b)
    else:
        bl_result = run_baseline_arm(inputs_b)

    # Run one-shot arm
    if one_shot_runner is not None:
        os_result = one_shot_runner(inputs_c)
    else:
        # Provider override for harness wiring
        if one_shot_provider is not None:
            os_result = run_one_shot_arm(inputs_c, provider=one_shot_provider)
        else:
            os_result = run_one_shot_arm(inputs_c)

    return TrialResult(
        scenario_id=scenario_id,
        trial=trial,
        seed=inputs.seed,
        inputs=inputs,
        autobridge=ab_result,
        baseline=bl_result,
        matched=matched,
        matched_error=matched_error,
        one_shot=os_result,
    )


def healthz() -> dict[str, str]:
    """Health check for the one-shot module."""
    return {"status": "ok", "service": "one_shot_generator"}
