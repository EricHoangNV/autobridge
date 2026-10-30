"""LLM provider protocol, budgets and concrete adapters (P1.W1.8, plan §2.9).

Every model call goes through :class:`LLMProvider` wrapped by :class:`Budget`;
:class:`BudgetExhausted` escalates to L4, never a silent retry (DIRECTIVES §3).
Model ids live in :mod:`autobridge.config` (env), never in code.

All calls that carry a ``run_id`` are logged to the :class:`autobridge.control.models.ModelCall`
table via :func:`record_model_call`; the in-memory :class:`Budget` counters
(``frontier_tokens``, ``small_tokens``, ``calls``) are the source for
``frontier_tokens == 0`` assertions on known repairs.
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.orm import Session

# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Usage:
    """Token counts returned by the provider."""

    input_tokens: int = 0
    output_tokens: int = 0
    #: Reasoning/thinking tokens, a sub-count of ``output_tokens`` (the
    #: provider bills them as completion/output tokens). Never added to
    #: totals or cost on top of ``output_tokens``; reported separately so a
    #: step can say a frontier call spent its budget thinking (owner check:
    #: live L3 returned an empty message after 4k reasoning tokens).
    reasoning_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One tool use requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Completion:
    """Result of a single LLM call."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    #: True when the text came from a redacted replay recording, not a live
    #: model (REQ-021 item 5). The UI and ``make demo`` label these calls;
    #: :class:`BudgetedProvider` marks their ledger purpose accordingly.
    replayed: bool = False
    #: Capture date of the replay recording (``YYYY-MM-DD``, or
    #: ``"synthetic-test-fixture"``). Shown in replay labels so a stand-in is
    #: never presented as a model capture.
    captured_at: str = ""
    #: Provider stop reason (OpenAI ``finish_reason`` / Anthropic
    #: ``stop_reason`` mapped to the OpenAI vocabulary: ``"length"`` means
    #: the output cap cut the answer off). Empty when the provider did not
    #: report one (fakes, replay). Bench arms treat ``"length"`` as
    #: truncation (REQ-052), never as a model failure.
    finish_reason: str = ""


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


class BudgetExhausted(RuntimeError):
    """Raised when a :class:`Budget` ceiling is hit; ladder must escalate to L4."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class Budget:
    """Token / call / wall-clock ceiling enforced by :class:`BudgetedProvider`.

    ``None`` means unlimited for that dimension.  Counters start at zero
    and are incremented *after* a successful call (so the call that
    exceeds the ceiling is the one that raises).

    ``max_wall`` is seconds since the budget was created (or since
    ``wall_start``).  A caller can pass an explicit ``wall_start`` for
    deterministic tests.
    """

    max_frontier_tokens: int | None = None
    max_small_tokens: int | None = None
    max_calls: int | None = None
    max_wall: float | None = None

    # running counters (mutated by BudgetedProvider)
    frontier_tokens: int = 0
    small_tokens: int = 0
    calls: int = 0
    wall_start: float = field(default_factory=time.monotonic)

    def check_pre_call(self) -> None:
        """Raise :class:`BudgetExhausted` if call count or wall-clock is over."""
        if self.max_calls is not None and self.calls >= self.max_calls:
            raise BudgetExhausted(f"max_calls {self.max_calls} exceeded")
        if self.max_wall is not None:
            elapsed = time.monotonic() - self.wall_start
            if elapsed >= self.max_wall:
                raise BudgetExhausted(f"max_wall {self.max_wall}s exceeded (elapsed {elapsed:.1f}s)")

    def record(self, tier: str, usage: Usage) -> None:
        """Account *usage* for *tier* and raise if a token ceiling is passed."""
        if tier == "frontier":
            self.frontier_tokens += usage.total_tokens
            if (
                self.max_frontier_tokens is not None
                and self.frontier_tokens > self.max_frontier_tokens
            ):
                raise BudgetExhausted(
                    f"max_frontier_tokens {self.max_frontier_tokens} exceeded "
                    f"(now {self.frontier_tokens})"
                )
        elif tier == "small":
            self.small_tokens += usage.total_tokens
            if self.max_small_tokens is not None and self.small_tokens > self.max_small_tokens:
                raise BudgetExhausted(
                    f"max_small_tokens {self.max_small_tokens} exceeded "
                    f"(now {self.small_tokens})"
                )
        else:
            # Unknown tiers still count as calls but not against token budgets.
            pass
        self.calls += 1

    def remaining(self) -> dict[str, int | float | None]:
        """Snapshot of remaining budget (``None`` where unlimited)."""
        return {
            "frontier_tokens": (
                (self.max_frontier_tokens - self.frontier_tokens)
                if self.max_frontier_tokens is not None
                else None
            ),
            "small_tokens": (
                (self.max_small_tokens - self.small_tokens)
                if self.max_small_tokens is not None
                else None
            ),
            "calls": (
                (self.max_calls - self.calls) if self.max_calls is not None else None
            ),
        }


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class LLMProvider(Protocol):
    """Adapter every model call goes through (plan §2.9)."""

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 1024,
        tier: str = "small",
        purpose: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> Completion:
        """Send *messages* and return a :class:`Completion`.

        *tier* is ``"frontier"`` or ``"small"`` (checked by
        :class:`BudgetedProvider` and by :class:`ModelCall` CHECK).
        *purpose* is the ledger label (e.g. ``"hypothesis-enumeration"``).
        *tools* is an optional list of tool specs the model may call.
        """
        ...


# ---------------------------------------------------------------------------
# Cost estimation (very rough; Decimal end to end)
# ---------------------------------------------------------------------------

_COST_PER_1K: dict[str, Decimal] = {
    "frontier": Decimal("0.015"),  # ~ $15 / 1M
    "small": Decimal("0.001"),  # ~ $1  / 1M
}


def estimate_cost(tier: str, usage: Usage, model: str = "") -> Decimal:
    """Cost estimate as Decimal (never float, DIRECTIVES Quick Reference 5).

    When *model* matches a published price in
    :data:`autobridge.config.MODEL_PRICES_PER_MILLION`, input/output tokens
    are priced separately from it; otherwise the legacy flat per-tier rate
    applies. Real billing still comes from the provider dashboard.
    """
    if model:
        from autobridge.config import model_price_per_million  # noqa: PLC0415 (avoid cycle)

        priced = model_price_per_million(model)
        if priced is not None:
            in_rate, out_rate = (Decimal(priced[0]), Decimal(priced[1]))
            total = (
                Decimal(usage.input_tokens) / Decimal(1_000_000) * in_rate
                + Decimal(usage.output_tokens) / Decimal(1_000_000) * out_rate
            )
            return total.quantize(Decimal("0.000001"))
    rate = _COST_PER_1K.get(tier, Decimal("0"))
    return (Decimal(usage.total_tokens) / Decimal(1000) * rate).quantize(Decimal("0.000001"))


# ---------------------------------------------------------------------------
# ModelCall ledger helper
# ---------------------------------------------------------------------------


def record_model_call(
    session: Session,
    *,
    run_id: str,
    provider: str,
    model: str,
    tier: str,
    usage: Usage,
    purpose: str,
    latency_ms: float = 0.0,
    cost_estimate: Decimal | None = None,
    mode: str = "live",
    captured_at: str = "",
    outcome: str | None = None,
    prompt_id: str = "",
    prompt_version: str = "",
    prompt_hash: str = "",
) -> Any:
    """Insert a :class:`ModelCall` row and flush (caller commits).

    ``cost_estimate`` defaults to :func:`estimate_cost`.
    ``mode`` is ``"live"`` or ``"replay"`` (REQ-030 item 3: the model-call
    box reads it from the column, not the purpose suffix); ``captured_at``
    is the replay recording's capture date (empty for live calls);
    ``outcome`` is what the call produced (``ranked``, ``proposal``,
    ``proposal_rejected``, ``no_patch``, ``unparsable``, ``hypothesis``).
    ``prompt_id``/``prompt_version``/``prompt_hash`` name the engine prompt
    file the call rendered (REQ-045); empty for pre-file calls.
    Returns the inserted row.
    """
    from autobridge.control.models import ModelCall  # noqa: PLC0415 (avoid cycle)

    if cost_estimate is None:
        cost_estimate = estimate_cost(tier, usage, model=model)
    row = ModelCall(
        run_id=run_id,
        provider=provider,
        model=model,
        tier=tier,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        reasoning_tokens=usage.reasoning_tokens,
        latency_ms=latency_ms,
        cost_estimate=cost_estimate,
        purpose=purpose,
        mode=mode or "live",
        captured_at=captured_at or "",
        outcome=outcome,
        prompt_id=prompt_id or "",
        prompt_version=prompt_version or "",
        prompt_hash=prompt_hash or "",
    )
    session.add(row)
    session.flush()
    return row


def stamp_call_outcome(
    session: Session, *, run_id: str, tier: str, outcome: str
) -> None:
    """Set ``outcome`` on the newest :class:`ModelCall` row for (run, tier).

    The ledger row is written when the call returns, before the caller has
    parsed the text; the rung outcome (proposal vs unparsable, ranked vs
    fallback) is known only after. Best-effort: never raises.
    """
    try:
        from sqlalchemy import select as _select  # noqa: PLC0415

        from autobridge.control.models import ModelCall as _ModelCall  # noqa: PLC0415

        row = (
            session.execute(
                _select(_ModelCall)
                .where(_ModelCall.run_id == run_id, _ModelCall.tier == tier)
                .order_by(_ModelCall.created_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        if row is not None:
            row.outcome = outcome
            session.flush()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Budgeted wrapper — the only way to call a provider under a ceiling
# ---------------------------------------------------------------------------


class BudgetedProvider:
    """Wraps an :class:`LLMProvider` with a :class:`Budget`.

    * Every call checks :meth:`Budget.check_pre_call` first.
    * On success the usage is recorded via :meth:`Budget.record` (may raise
      :class:`BudgetExhausted`).
    * If *session* and *run_id* are given, a :class:`ModelCall` row is
      inserted for every successful call.
    * ``provider_name`` / ``model_fn`` control the ledger labels; by
      default they are derived from the wrapped provider.
    """

    def __init__(
        self,
        inner: LLMProvider,
        budget: Budget,
        *,
        session: Session | None = None,
        run_id: str | None = None,
        provider_name: str | None = None,
        model_fn: Callable[[str], str] | None = None,
    ) -> None:
        self.inner = inner
        self.budget = budget
        self.session = session
        self.run_id = run_id
        self.provider_name = provider_name or inner.__class__.__name__
        self.model_fn = model_fn

    def _model_for_tier(self, tier: str) -> str:
        if self.model_fn is not None:
            return self.model_fn(tier)
        # Fallback: ask the inner provider if it knows.
        getter = getattr(self.inner, "model_for_tier", None)
        if callable(getter):
            return getter(tier)
        return tier

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 1024,
        tier: str = "small",
        purpose: str = "",
        tools: list[dict[str, Any]] | None = None,
        outcome: str | None = None,
        prompt: dict[str, str] | None = None,
    ) -> Completion:
        self.budget.check_pre_call()
        if self.session is not None:
            # REQ-042: no DB transaction is held across a model call. The
            # ladder may have flushed rows (earlier ModelCalls, evidence)
            # in this session; commit them before the network call so a
            # 30 s frontier call never holds the write lock that keeps
            # control responsive. Best-effort: a failed commit rolls back
            # and the call still proceeds (the ledger row below re-flushes
            # what matters).
            try:
                self.session.commit()
            except Exception:
                with contextlib.suppress(Exception):
                    self.session.rollback()
        start = time.monotonic()
        result = self.inner.complete(
            messages, max_tokens=max_tokens, tier=tier, purpose=purpose, tools=tools
        )
        elapsed_ms = (time.monotonic() - start) * 1000.0
        # Record budget — may raise BudgetExhausted (caller's signal to escalate).
        self.budget.record(tier, result.usage)
        # Ledger side-effect (best-effort; budget violation still propagates).
        if self.session is not None and self.run_id is not None:
            ledger_purpose = purpose
            if result.replayed and "(replay" not in ledger_purpose:
                captured = (result.captured_at or "").strip()
                if captured.startswith("synthetic"):
                    ledger_purpose = f"{ledger_purpose} (replay test fixture)"
                elif captured:
                    ledger_purpose = f"{ledger_purpose} (replay {captured})"
                else:
                    ledger_purpose = f"{ledger_purpose} (replay)"
            record_model_call(
                self.session,
                run_id=self.run_id,
                provider=self.provider_name,
                model=self._model_for_tier(tier),
                tier=tier,
                usage=result.usage,
                purpose=ledger_purpose,
                latency_ms=elapsed_ms,
                mode="replay" if result.replayed else "live",
                captured_at=(result.captured_at or "").strip(),
                outcome=outcome,
                prompt_id=(prompt or {}).get("id", ""),
                prompt_version=(prompt or {}).get("version", ""),
                prompt_hash=(prompt or {}).get("hash", ""),
            )
            # REQ-042: commit right after the call too — the ModelCall row
            # is durable (and visible in the ledger) even while the ladder
            # keeps reasoning, and the lock is released before the next
            # call. A failed commit rolls back to the pre-call state; the
            # in-memory usage counters the repair row reports stay correct.
            try:
                self.session.commit()
            except Exception:
                with contextlib.suppress(Exception):
                    self.session.rollback()
        return result


# ---------------------------------------------------------------------------
# Anthropic provider
# ---------------------------------------------------------------------------


class AnthropicProvider:
    """Anthropic Chat Completions adapter.

    Model ids come from :mod:`autobridge.config` (env), not from code.
    ``tier`` selects between the two model ids; unknown tiers raise
    :class:`ValueError` before any network call.
    """

    provider_name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        frontier_model: str | None = None,
        small_model: str | None = None,
    ) -> None:
        # api_key from explicit arg, else env ANTHROPIC_API_KEY, else "" (client
        # construction deferred to first call so tests don't need a key).
        import os  # noqa: PLC0415

        from autobridge.config import (  # noqa: PLC0415 (deferred import)
            anthropic_frontier_model,
            anthropic_small_model,
        )

        self.api_key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY", "")
        self.frontier_model = frontier_model or anthropic_frontier_model()
        self.small_model = small_model or anthropic_small_model()
        self._client: Any | None = None

    def model_for_tier(self, tier: str) -> str:
        if tier == "frontier":
            return self.frontier_model
        if tier == "small":
            return self.small_model
        raise ValueError(f"unknown tier {tier!r}; expected 'frontier' or 'small'")

    def _get_client(self) -> Any:
        if self._client is None:
            import anthropic  # noqa: PLC0415

            self._client = anthropic.Anthropic(api_key=self.api_key or None)
        return self._client

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 1024,
        tier: str = "small",
        purpose: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> Completion:
        _ = purpose  # ledger label is handled by BudgetedProvider
        model = self.model_for_tier(tier)
        client = self._get_client()
        # Anthropic expects "system" separately; pull it out if present.
        system_parts: list[str] = []
        filtered: list[dict[str, Any]] = []
        for m in messages:
            if m.get("role") == "system":
                system_parts.append(str(m.get("content", "")))
            else:
                filtered.append(m)
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": filtered,
        }
        if system_parts:
            kwargs["system"] = "\n".join(system_parts)
        if tools:
            # Anthropic tool spec: {"name": ..., "description": ..., "input_schema": ...}
            kwargs["tools"] = tools
        resp = client.messages.create(**kwargs)
        # Parse response: text blocks + tool_use blocks + usage.
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in getattr(resp, "content", []):
            btype = getattr(block, "type", "")
            if btype == "text":
                text_parts.append(getattr(block, "text", ""))
            elif btype == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=getattr(block, "id", ""),
                        name=getattr(block, "name", ""),
                        arguments=dict(getattr(block, "input", {}) or {}),
                    )
                )
        usage_obj = getattr(resp, "usage", None)
        usage = Usage(
            input_tokens=int(getattr(usage_obj, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage_obj, "output_tokens", 0) or 0),
        )
        # Anthropic stop reasons use a different vocabulary; map the cap-hit
        # reason to the OpenAI one so bench truncation detection (REQ-052)
        # has a single value to check.
        raw_stop = str(getattr(resp, "stop_reason", "") or "")
        finish_reason = "length" if raw_stop == "max_tokens" else raw_stop
        return Completion(
            text="".join(text_parts),
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=finish_reason,
        )


# ---------------------------------------------------------------------------
# OpenAI provider (P2.S5.1 — Chat Completions, same LLMProvider contract)
# ---------------------------------------------------------------------------


def _to_openai_tool(spec: dict[str, Any]) -> dict[str, Any]:
    """Normalize a tool spec to the OpenAI function-calling shape.

    Accepts OpenAI-native specs (``{"type": "function", ...}``) unchanged and
    converts the Anthropic-style specs the engine emits
    (``{"name", "description", "input_schema"}``) to
    ``{"type": "function", "function": {...}}``. Unknown shapes pass through
    untouched — the API (not the prompt) enforces tool permissions.
    """
    if not isinstance(spec, dict):
        return spec
    if spec.get("type") == "function":
        return spec
    function: dict[str, Any] = {"name": spec.get("name", "")}
    if spec.get("description") is not None:
        function["description"] = spec["description"]
    parameters = spec.get("input_schema", spec.get("parameters", {"type": "object"}))
    function["parameters"] = parameters
    return {"type": "function", "function": function}


def _reasoning_tokens(usage_obj: Any) -> int:
    """Extract reasoning/thinking tokens from a Chat Completions usage object.

    Reasoning models (Muse Spark via the OpenAI-compatible API, OpenAI
    o-series) report thinking inside ``completion_tokens_details`` under the
    ``reasoning_tokens`` key; non-reasoning responses omit it. Accepts both
    attribute-style SDK objects and plain dicts; anything unexpected is 0 —
    a missing split never fails a call.
    """
    if usage_obj is None:
        return 0
    details = getattr(usage_obj, "completion_tokens_details", None)
    if details is None and isinstance(usage_obj, dict):
        details = usage_obj.get("completion_tokens_details")
    if details is None:
        return 0
    if isinstance(details, dict):
        raw = details.get("reasoning_tokens", 0)
    else:
        raw = getattr(details, "reasoning_tokens", 0)
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def _parse_openai_chat_response(resp: Any) -> Completion:
    """Parse an OpenAI Chat Completions response into a :class:`Completion`.

    Shared by :class:`OpenAIProvider` and :class:`MuseProvider` (both speak
    the same wire shape): first-choice message content, tool calls with
    JSON-decoded arguments, ``prompt_tokens`` / ``completion_tokens`` usage.
    """
    choices = list(getattr(resp, "choices", []) or [])
    message = choices[0].message if choices else None
    raw_text = getattr(message, "content", None) if message is not None else None
    text = raw_text if isinstance(raw_text, str) else ""
    tool_calls: list[ToolCall] = []
    for tc in getattr(message, "tool_calls", None) or []:
        fn = getattr(tc, "function", None)
        raw_args = getattr(fn, "arguments", None) if fn is not None else None
        args: Any = {}
        if isinstance(raw_args, dict):
            args = raw_args
        elif isinstance(raw_args, str) and raw_args.strip():
            import json as _json  # noqa: PLC0415

            try:
                args = _json.loads(raw_args)
            except Exception:
                args = {}
        if not isinstance(args, dict):
            args = {}
        tool_calls.append(
            ToolCall(
                id=str(getattr(tc, "id", "") or ""),
                name=str(getattr(fn, "name", "") or "") if fn is not None else "",
                arguments=dict(args),
            )
        )
    usage_obj = getattr(resp, "usage", None)
    usage = Usage(
        input_tokens=int(getattr(usage_obj, "prompt_tokens", 0) or 0),
        output_tokens=int(getattr(usage_obj, "completion_tokens", 0) or 0),
        reasoning_tokens=_reasoning_tokens(usage_obj),
    )
    first = choices[0] if choices else None
    finish_reason = str(getattr(first, "finish_reason", "") or "")
    return Completion(text=text, tool_calls=tool_calls, usage=usage, finish_reason=finish_reason)


class OpenAIProvider:
    """OpenAI Chat Completions adapter (P2.S5.1).

    Mirrors :class:`AnthropicProvider` behind :class:`LLMProvider`:
    model ids come from :mod:`autobridge.config` (env), not from code;
    ``tier`` selects between the two model ids; unknown tiers raise
    :class:`ValueError` before any network call. Unlike Anthropic, OpenAI
    accepts ``system`` messages inline, so messages pass through unchanged.

    Secrets: only the API key leaves the process (as the SDK's auth
    header); prompts still carry only redacted docs/responses, and tool
    permissions are enforced in code, not in the prompt (DIRECTIVES §3).
    """

    provider_name = "openai"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        frontier_model: str | None = None,
        small_model: str | None = None,
    ) -> None:
        import os  # noqa: PLC0415

        from autobridge.config import openai_frontier_model, openai_small_model  # noqa: PLC0415

        self.api_key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", "")
        self.frontier_model = frontier_model or openai_frontier_model()
        self.small_model = small_model or openai_small_model()
        self._client: Any | None = None

    def model_for_tier(self, tier: str) -> str:
        if tier == "frontier":
            return self.frontier_model
        if tier == "small":
            return self.small_model
        raise ValueError(f"unknown tier {tier!r}; expected 'frontier' or 'small'")

    def _get_client(self) -> Any:
        if self._client is None:
            import openai  # noqa: PLC0415

            self._client = openai.OpenAI(api_key=self.api_key or None)
        return self._client

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 1024,
        tier: str = "small",
        purpose: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> Completion:
        _ = purpose  # ledger label is handled by BudgetedProvider
        model = self.model_for_tier(tier)
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": list(messages),
        }
        if tools:
            kwargs["tools"] = [_to_openai_tool(t) for t in tools]
        resp = client.chat.completions.create(**kwargs)
        return _parse_openai_chat_response(resp)


# ---------------------------------------------------------------------------
# Meta Muse provider (REQ-021 item 5 — frontier tier, OpenAI-compatible)
# ---------------------------------------------------------------------------


class MuseProvider:
    """Meta Muse Chat Completions adapter (OpenAI-compatible wire shape).

    The frontier tier (and, by default, the small tier) of the ladder, behind
    the same :class:`LLMProvider` contract: every call goes through
    :class:`BudgetedProvider` into the :class:`ModelCall` ledger with
    model-specific cost estimates. Model ids and the base URL come from
    :mod:`autobridge.config` (env ``FRONTIER_MODEL`` / ``SMALL_MODEL`` /
    ``FRONTIER_BASE_URL``), never from code; only the API key leaves the
    process (as the SDK's auth header); tool permissions are enforced in
    code, not in the prompt (DIRECTIVES §3).

    Anthropic stays available as an alternative provider.
    """

    provider_name = "muse"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        frontier_model: str | None = None,
        small_model: str | None = None,
    ) -> None:
        from autobridge.config import (  # noqa: PLC0415 (deferred import)
            frontier_api_key,
            frontier_base_url,
        )
        from autobridge.config import (  # noqa: PLC0415 (deferred import)
            frontier_model as default_frontier_model,
        )
        from autobridge.config import (  # noqa: PLC0415 (deferred import)
            small_model as default_small_model,
        )

        self.api_key = api_key if api_key is not None else frontier_api_key()
        self.base_url = base_url if base_url is not None else frontier_base_url()
        self.frontier_model = frontier_model or default_frontier_model()
        self.small_model = small_model or default_small_model()
        self._client: Any | None = None

    def model_for_tier(self, tier: str) -> str:
        if tier == "frontier":
            return self.frontier_model
        if tier == "small":
            return self.small_model
        raise ValueError(f"unknown tier {tier!r}; expected 'frontier' or 'small'")

    def _get_client(self) -> Any:
        if self._client is None:
            import openai  # noqa: PLC0415

            self._client = openai.OpenAI(api_key=self.api_key or None, base_url=self.base_url)
        return self._client

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 1024,
        tier: str = "small",
        purpose: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> Completion:
        _ = purpose  # ledger label is handled by BudgetedProvider
        model = self.model_for_tier(tier)
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": list(messages),
        }
        if tools:
            kwargs["tools"] = [_to_openai_tool(t) for t in tools]
        resp = client.chat.completions.create(**kwargs)
        return _parse_openai_chat_response(resp)


# ---------------------------------------------------------------------------
# Replay provider (REQ-021 item 5 — deterministic stand-in for live calls)
# ---------------------------------------------------------------------------


class ReplayMissing(RuntimeError):
    """No recording exists for a prompt hash: fail loudly, never invent one."""

    def __init__(self, prompt_hash: str, recordings_dir: str) -> None:
        super().__init__(
            f"no replay recording for prompt {prompt_hash} in {recordings_dir} "
            "(record one from a live run, or run with a frontier key)"
        )
        self.prompt_hash = prompt_hash
        self.recordings_dir = recordings_dir


def prompt_hash(messages: list[dict[str, Any]]) -> str:
    """Stable sha256 over the canonical prompt JSON (replay lookup key)."""
    import hashlib  # noqa: PLC0415
    import json as _json  # noqa: PLC0415

    canonical = _json.dumps(messages, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _recording_to_completion(entry: dict[str, Any]) -> Completion:
    tool_calls = [
        ToolCall(
            id=str(tc.get("id", "")),
            name=str(tc.get("name", "")),
            arguments=dict(tc.get("arguments", {}) or {}),
        )
        for tc in entry.get("tool_calls", [])
        if isinstance(tc, dict)
    ]
    usage = Usage(
        input_tokens=int(entry.get("input_tokens", 0) or 0),
        output_tokens=int(entry.get("output_tokens", 0) or 0),
        reasoning_tokens=int(entry.get("reasoning_tokens", 0) or 0),
    )
    return Completion(
        text=str(entry.get("text", "")),
        tool_calls=tool_calls,
        usage=usage,
        replayed=True,
        captured_at=str(entry.get("captured_at", "") or ""),
    )


class ReplayProvider:
    """Deterministic replay of redacted frontier/small responses (REQ-021.5).

    Recordings live under ``demo/recordings/`` (or ``REPLAY_DIR``), one JSON
    file per scenario mapping prompt sha256 -> ``{text, tool_calls,
    input_tokens, output_tokens, reasoning_tokens, model}``. A missing recording raises
    :class:`ReplayMissing` — the demo fails loudly instead of inventing a
    response. Model ids recorded alongside are labels only; pricing follows
    the recorded model via :func:`estimate_cost`.
    """

    provider_name = "replay"

    def __init__(
        self,
        recordings_dir: str | None = None,
        *,
        frontier_model: str | None = None,
        small_model: str | None = None,
    ) -> None:

        from autobridge.config import (  # noqa: PLC0415 (deferred import)
            frontier_model as default_frontier_model,
        )
        from autobridge.config import (
            replay_dir,
        )
        from autobridge.config import (
            small_model as default_small_model,
        )

        self.recordings_dir = str(recordings_dir or replay_dir() or "")
        self.frontier_model = frontier_model or default_frontier_model()
        self.small_model = small_model or default_small_model()
        self._recordings: dict[str, dict[str, Any]] | None = None
        #: Capture date of the last served recording ("", a date, or
        #: "synthetic-test-fixture"). Lets callers label replay output.
        self.last_captured_at: str = ""
        self.last_prompt_hash: str = ""

    def model_for_tier(self, tier: str) -> str:
        if tier == "frontier":
            return self.frontier_model
        if tier == "small":
            return self.small_model
        raise ValueError(f"unknown tier {tier!r}; expected 'frontier' or 'small'")

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._recordings is None:
            import glob as _glob  # noqa: PLC0415
            import json as _json  # noqa: PLC0415
            import os as _os  # noqa: PLC0415

            merged: dict[str, dict[str, Any]] = {}
            if self.recordings_dir:
                for path in sorted(_glob.glob(_os.path.join(self.recordings_dir, "*.json"))):
                    try:
                        with open(path, encoding="utf-8") as fh:
                            data = _json.load(fh)
                    except Exception:
                        continue
                    if isinstance(data, dict):
                        for key, entry in data.items():
                            if isinstance(entry, dict):
                                merged[str(key)] = entry
            self._recordings = merged
        return self._recordings

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 1024,
        tier: str = "small",
        purpose: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> Completion:
        _ = (max_tokens, tier, purpose, tools)  # replay is input-determined, not parameter-tuned
        key = prompt_hash(messages)
        entry = self._load().get(key)
        if entry is None:
            raise ReplayMissing(key, self.recordings_dir or "(no recordings dir)")
        out = _recording_to_completion(entry)
        self.last_captured_at = str(entry.get("captured_at", "") or "")
        self.last_prompt_hash = key
        return out


def resolve_frontier_provider() -> tuple[Any, str]:
    """Pick the L2/L3 provider from env (REQ-021 item 5).

    ``FRONTIER_MODE``: ``"live"`` (needs ``FRONTIER_API_KEY``),
    ``"replay"`` (recordings under ``REPLAY_DIR``), ``"auto"`` (live when a
    key is configured, else replay) or ``"off"`` (no provider; callers must
    say plainly that L3 was skipped). Returns ``(provider, mode)`` where
    mode is ``"live"`` or ``"replay"``; ``"off"`` returns ``(None, "off")``.
    Model ids come from config in every mode.
    """
    from autobridge.config import frontier_api_key, frontier_mode  # noqa: PLC0415

    mode = frontier_mode()
    if mode == "off":
        return None, "off"
    if mode == "live" or (mode == "auto" and frontier_api_key()):
        if mode == "live" and not frontier_api_key():
            raise RuntimeError("FRONTIER_MODE=live needs FRONTIER_API_KEY (generate a dedicated Muse key)")
        return MuseProvider(), "live"
    if mode not in ("auto", "replay"):
        raise RuntimeError(f"FRONTIER_MODE must be live|replay|auto|off, got {mode!r}")
    return ReplayProvider(), "replay"


# ---------------------------------------------------------------------------
# Fake provider for tests (deterministic, no network)
# ---------------------------------------------------------------------------


class FakeProvider:
    """In-memory fake that returns canned :class:`Completion`s.

    * ``responses`` maps ``tier`` (or ``"default"``) to the completion to
      return.  A callable value is invoked with ``(messages, max_tokens,
      tier, purpose, tools)`` for dynamic fakes.
    * ``default_usage`` is used when a response has no explicit usage.
    * Every call is appended to :attr:`calls` for assertions.
    """

    provider_name = "fake"

    def __init__(
        self,
        responses: dict[str, Completion | Callable[..., Completion]] | None = None,
        *,
        default_text: str = "fake response",
        default_usage: Usage | None = None,
        frontier_model: str = "fake-frontier",
        small_model: str = "fake-small",
    ) -> None:
        self.responses = responses or {}
        self.default_text = default_text
        self.default_usage = default_usage or Usage(input_tokens=10, output_tokens=20)
        self.frontier_model = frontier_model
        self.small_model = small_model
        self.calls: list[dict[str, Any]] = []

    def model_for_tier(self, tier: str) -> str:
        if tier == "frontier":
            return self.frontier_model
        if tier == "small":
            return self.small_model
        return f"fake-{tier}"

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 1024,
        tier: str = "small",
        purpose: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> Completion:
        self.calls.append(
            {
                "messages": messages,
                "max_tokens": max_tokens,
                "tier": tier,
                "purpose": purpose,
                "tools": tools,
            }
        )
        key = tier if tier in self.responses else "default"
        candidate = self.responses.get(key)
        if candidate is None:
            return Completion(text=self.default_text, usage=self.default_usage)
        if callable(candidate):
            return candidate(messages, max_tokens=max_tokens, tier=tier, purpose=purpose, tools=tools)
        return candidate

    def last_purpose(self) -> str | None:
        return self.calls[-1]["purpose"] if self.calls else None
