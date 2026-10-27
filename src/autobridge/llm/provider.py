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


def estimate_cost(tier: str, usage: Usage) -> Decimal:
    """Rough cost estimate; real billing comes from the provider dashboard."""
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
) -> Any:
    """Insert a :class:`ModelCall` row and flush (caller commits).

    ``cost_estimate`` defaults to :func:`estimate_cost`.
    Returns the inserted row.
    """
    from autobridge.control.models import ModelCall  # noqa: PLC0415 (avoid cycle)

    if cost_estimate is None:
        cost_estimate = estimate_cost(tier, usage)
    row = ModelCall(
        run_id=run_id,
        provider=provider,
        model=model,
        tier=tier,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        latency_ms=latency_ms,
        cost_estimate=cost_estimate,
        purpose=purpose,
    )
    session.add(row)
    session.flush()
    return row


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
    ) -> Completion:
        self.budget.check_pre_call()
        start = time.monotonic()
        result = self.inner.complete(
            messages, max_tokens=max_tokens, tier=tier, purpose=purpose, tools=tools
        )
        elapsed_ms = (time.monotonic() - start) * 1000.0
        # Record budget — may raise BudgetExhausted (caller's signal to escalate).
        self.budget.record(tier, result.usage)
        # Ledger side-effect (best-effort; budget violation still propagates).
        if self.session is not None and self.run_id is not None:
            record_model_call(
                self.session,
                run_id=self.run_id,
                provider=self.provider_name,
                model=self._model_for_tier(tier),
                tier=tier,
                usage=result.usage,
                purpose=purpose,
                latency_ms=elapsed_ms,
            )
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
        return Completion(text="".join(text_parts), tool_calls=tool_calls, usage=usage)


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
        # Parse response: first choice message content + tool_calls + usage.
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
        )
        return Completion(text=text, tool_calls=tool_calls, usage=usage)


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
