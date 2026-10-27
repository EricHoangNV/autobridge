"""Tests for LLMProvider protocol, Budget, providers and ledger (P1.W1.8)."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine

from autobridge.control import models as m
from autobridge.control.models import ModelCall

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.orm import Session
from autobridge.llm.provider import (
    AnthropicProvider,
    Budget,
    BudgetedProvider,
    BudgetExhausted,
    Completion,
    FakeProvider,
    OpenAIProvider,
    ToolCall,
    Usage,
    estimate_cost,
    record_model_call,
)


@pytest.fixture()
def session(tmp_path: Path) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{tmp_path}/llm.db")
    m.init_db(engine)
    factory = m.session_factory(engine)
    with factory() as s:
        yield s


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


def test_usage_total() -> None:
    assert Usage(input_tokens=10, output_tokens=20).total_tokens == 30
    assert Usage().total_tokens == 0


def test_completion_defaults() -> None:
    c = Completion()
    assert c.text == ""
    assert c.tool_calls == []
    assert c.usage.total_tokens == 0


def test_tool_call_defaults() -> None:
    tc = ToolCall(id="x", name="search")
    assert tc.arguments == {}


def test_estimate_cost() -> None:
    # frontier ~ 0.015 per 1k, small ~ 0.001
    assert estimate_cost("frontier", Usage(input_tokens=1000, output_tokens=0)) == Decimal("0.015000")
    assert estimate_cost("small", Usage(input_tokens=500, output_tokens=500)) == Decimal("0.001000")
    assert estimate_cost("unknown", Usage(input_tokens=100, output_tokens=100)) == Decimal("0.000000")


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


def test_budget_no_limits_never_exhausted() -> None:
    b = Budget()
    b.check_pre_call()
    b.record("small", Usage(input_tokens=10000, output_tokens=10000))
    b.record("frontier", Usage(input_tokens=10000, output_tokens=10000))
    assert b.calls == 2
    assert b.small_tokens == 20000
    assert b.frontier_tokens == 20000


def test_budget_max_calls() -> None:
    b = Budget(max_calls=1)
    b.check_pre_call()  # ok
    b.record("small", Usage(input_tokens=1, output_tokens=1))
    assert b.calls == 1
    with pytest.raises(BudgetExhausted, match="max_calls"):
        b.check_pre_call()


def test_budget_max_frontier_tokens() -> None:
    b = Budget(max_frontier_tokens=10)
    b.record("frontier", Usage(input_tokens=5, output_tokens=5))  # 10 exactly ok
    assert b.frontier_tokens == 10
    with pytest.raises(BudgetExhausted, match="max_frontier_tokens"):
        b.record("frontier", Usage(input_tokens=1, output_tokens=0))


def test_budget_max_small_tokens() -> None:
    b = Budget(max_small_tokens=5)
    b.record("small", Usage(input_tokens=3, output_tokens=2))  # 5 ok
    with pytest.raises(BudgetExhausted, match="max_small_tokens"):
        b.record("small", Usage(input_tokens=1, output_tokens=0))


def test_budget_frontier_does_not_count_against_small() -> None:
    b = Budget(max_small_tokens=5, max_frontier_tokens=100)
    b.record("frontier", Usage(input_tokens=50, output_tokens=50))
    # small still 0
    assert b.small_tokens == 0
    b.record("small", Usage(input_tokens=2, output_tokens=2))
    assert b.small_tokens == 4


def test_budget_small_does_not_count_against_frontier() -> None:
    b = Budget(max_frontier_tokens=5, max_small_tokens=100)
    b.record("small", Usage(input_tokens=50, output_tokens=50))
    assert b.frontier_tokens == 0


def test_budget_wall_exhausted() -> None:
    b = Budget(max_wall=0.0)
    with pytest.raises(BudgetExhausted, match="max_wall"):
        b.check_pre_call()


def test_budget_wall_not_yet() -> None:
    b = Budget(max_wall=100.0)
    b.check_pre_call()  # should not raise


def test_budget_unknown_tier_only_counts_calls() -> None:
    b = Budget(max_frontier_tokens=10, max_small_tokens=10)
    b.record("mystery", Usage(input_tokens=100, output_tokens=100))
    assert b.frontier_tokens == 0
    assert b.small_tokens == 0
    assert b.calls == 1


def test_budget_remaining() -> None:
    b = Budget(max_frontier_tokens=100, max_small_tokens=50, max_calls=10)
    b.record("frontier", Usage(input_tokens=30, output_tokens=10))
    b.record("small", Usage(input_tokens=10, output_tokens=10))
    rem = b.remaining()
    assert rem["frontier_tokens"] == 60
    assert rem["small_tokens"] == 30
    assert rem["calls"] == 8


def test_budget_remaining_unlimited_is_none() -> None:
    b = Budget()
    rem = b.remaining()
    assert rem["frontier_tokens"] is None
    assert rem["small_tokens"] is None
    assert rem["calls"] is None


def test_budget_exhausted_reason() -> None:
    exc = BudgetExhausted("frontier tokens gone")
    assert exc.reason == "frontier tokens gone"
    assert "frontier tokens gone" in str(exc)


# ---------------------------------------------------------------------------
# record_model_call helper
# ---------------------------------------------------------------------------


def test_record_model_call_inserts_row(session: Session) -> None:
    row = record_model_call(
        session,
        run_id="build:abc",
        provider="anthropic",
        model="claude-haiku",
        tier="small",
        usage=Usage(input_tokens=10, output_tokens=20),
        purpose="test purpose",
        latency_ms=123.0,
    )
    session.commit()
    assert row.id is not None
    fetched = session.get(ModelCall, row.id)
    assert fetched is not None
    assert fetched.purpose == "test purpose"
    assert fetched.input_tokens == 10
    assert fetched.output_tokens == 20
    assert fetched.cost_estimate == estimate_cost("small", Usage(10, 20))


def test_record_model_call_explicit_cost(session: Session) -> None:
    row = record_model_call(
        session,
        run_id="repair:x",
        provider="fake",
        model="fake-small",
        tier="small",
        usage=Usage(input_tokens=1, output_tokens=1),
        purpose="explicit",
        cost_estimate=Decimal("0.123456"),
    )
    assert row.cost_estimate == Decimal("0.123456")


def test_record_model_call_frontier_cost(session: Session) -> None:
    row = record_model_call(
        session,
        run_id="sync:y",
        provider="anthropic",
        model="claude-sonnet",
        tier="frontier",
        usage=Usage(input_tokens=1000, output_tokens=500),
        purpose="frontier call",
    )
    # 1500 tokens frontier @ 0.015/1k = 0.0225
    assert row.cost_estimate == Decimal("0.022500")


# ---------------------------------------------------------------------------
# FakeProvider
# ---------------------------------------------------------------------------


def test_fake_provider_default() -> None:
    fake = FakeProvider()
    c = fake.complete([{"role": "user", "content": "hi"}], tier="small", purpose="p1")
    assert c.text == "fake response"
    assert c.usage.input_tokens == 10
    assert len(fake.calls) == 1
    assert fake.calls[0]["purpose"] == "p1"
    assert fake.last_purpose() == "p1"


def test_fake_provider_per_tier_responses() -> None:
    fake = FakeProvider(
        responses={
            "small": Completion(text="small answer", usage=Usage(1, 2)),
            "frontier": Completion(text="frontier answer", usage=Usage(3, 4)),
        }
    )
    assert fake.complete([], tier="small").text == "small answer"
    assert fake.complete([], tier="frontier").text == "frontier answer"
    assert fake.complete([], tier="unknown").text == "fake response"


def test_fake_provider_default_key() -> None:
    fake = FakeProvider(responses={"default": Completion(text="default hit", usage=Usage(5, 5))})
    assert fake.complete([], tier="small").text == "default hit"
    assert fake.complete([], tier="frontier").text == "default hit"


def test_fake_provider_callable_response() -> None:
    def dynamic(messages: list[dict[str, object]], **_kwargs: object) -> Completion:
        return Completion(text=f"echo:{messages[0]['content']}", usage=Usage(1, 1))

    fake = FakeProvider(responses={"small": dynamic})
    c = fake.complete([{"role": "user", "content": "hello"}])
    assert c.text == "echo:hello"


def test_fake_provider_tools_forwarded() -> None:
    fake = FakeProvider()
    tools = [{"name": "search", "description": "search docs"}]
    fake.complete([], tools=tools, max_tokens=512)
    assert fake.calls[0]["tools"] == tools
    assert fake.calls[0]["max_tokens"] == 512


def test_fake_provider_model_for_tier() -> None:
    fake = FakeProvider(frontier_model="ff", small_model="ss")
    assert fake.model_for_tier("frontier") == "ff"
    assert fake.model_for_tier("small") == "ss"
    assert fake.model_for_tier("other") == "fake-other"


def test_fake_provider_last_purpose_empty() -> None:
    assert FakeProvider().last_purpose() is None


def test_fake_provider_messages_recorded() -> None:
    fake = FakeProvider()
    msgs = [{"role": "user", "content": "hi"}]
    fake.complete(msgs, tier="frontier", purpose="probe")
    assert fake.calls[0]["messages"] == msgs
    assert fake.calls[0]["tier"] == "frontier"


# ---------------------------------------------------------------------------
# BudgetedProvider
# ---------------------------------------------------------------------------


def test_budgeted_provider_pass_through() -> None:
    inner = FakeProvider(responses={"small": Completion(text="ok", usage=Usage(5, 5))})
    budget = Budget(max_small_tokens=100, max_calls=5)
    wrapped = BudgetedProvider(inner, budget)
    c = wrapped.complete([{"role": "user", "content": "hi"}], tier="small", purpose="test")
    assert c.text == "ok"
    assert budget.small_tokens == 10
    assert budget.calls == 1


def test_budgeted_provider_budget_exhausted_pre_call() -> None:
    inner = FakeProvider()
    budget = Budget(max_calls=0)
    budget.calls = 0
    # Need to set max_calls=0 and calls=0 -> check_pre_call should raise because 0 >= 0
    wrapped = BudgetedProvider(inner, budget)
    with pytest.raises(BudgetExhausted, match="max_calls"):
        wrapped.complete([], tier="small")


def test_budgeted_provider_budget_exhausted_tokens() -> None:
    inner = FakeProvider(
        responses={"small": Completion(text="hi", usage=Usage(input_tokens=10, output_tokens=10))}
    )
    budget = Budget(max_small_tokens=5)
    wrapped = BudgetedProvider(inner, budget)
    with pytest.raises(BudgetExhausted, match="max_small_tokens"):
        wrapped.complete([], tier="small")


def test_budgeted_provider_records_model_call(session: Session) -> None:
    inner = FakeProvider()
    budget = Budget()
    wrapped = BudgetedProvider(inner, budget, session=session, run_id="repair:test123")
    wrapped.complete([], tier="small", purpose="ledger test")
    session.commit()
    rows = session.query(ModelCall).all()
    assert len(rows) == 1
    assert rows[0].purpose == "ledger test"
    assert rows[0].run_id == "repair:test123"
    assert rows[0].provider == "FakeProvider"


def test_budgeted_provider_custom_provider_name_and_model_fn(session: Session) -> None:
    inner = FakeProvider()
    budget = Budget()
    wrapped = BudgetedProvider(
        inner,
        budget,
        session=session,
        run_id="build:1",
        provider_name="custom",
        model_fn=lambda tier: f"model-{tier}",
    )
    wrapped.complete([], tier="frontier", purpose="p")
    session.commit()
    row = session.query(ModelCall).one()
    assert row.provider == "custom"
    assert row.model == "model-frontier"


def test_budgeted_provider_uses_inner_model_for_tier(session: Session) -> None:
    inner = FakeProvider(frontier_model="my-frontier", small_model="my-small")
    budget = Budget()
    wrapped = BudgetedProvider(inner, budget, session=session, run_id="build:2")
    wrapped.complete([], tier="frontier", purpose="p")
    session.commit()
    assert session.query(ModelCall).one().model == "my-frontier"


def test_budgeted_provider_fallback_model_when_no_method(session: Session) -> None:
    class BareProvider:
        def complete(  # type: ignore[no-untyped-def]  # noqa: ARG001,ARG002
            self, messages, *, max_tokens=1024, tier="small", purpose="", tools=None  # noqa: ARG001,ARG002
        ):
            return Completion(text="bare", usage=Usage(1, 1))

    inner = BareProvider()  # type: ignore[arg-type]
    budget = Budget()
    wrapped = BudgetedProvider(inner, budget, session=session, run_id="build:bare")
    wrapped.complete([], tier="frontier")
    session.commit()
    # Bare provider has no model_for_tier, fallback returns tier name itself
    assert session.query(ModelCall).one().model == "frontier"


def test_budgeted_provider_no_ledger_when_no_session() -> None:
    inner = FakeProvider()
    budget = Budget()
    wrapped = BudgetedProvider(inner, budget)  # no session
    c = wrapped.complete([], tier="small")
    assert c.text == "fake response"
    assert budget.calls == 1


def test_budgeted_provider_no_ledger_when_no_run_id(session: Session) -> None:
    inner = FakeProvider()
    budget = Budget()
    wrapped = BudgetedProvider(inner, budget, session=session, run_id=None)
    wrapped.complete([], tier="small")
    assert session.query(ModelCall).count() == 0


def test_budgeted_provider_frontier_tokens_tracked() -> None:
    inner = FakeProvider(responses={"frontier": Completion(text="x", usage=Usage(20, 30))})
    budget = Budget()
    wrapped = BudgetedProvider(inner, budget)
    wrapped.complete([], tier="frontier")
    assert budget.frontier_tokens == 50
    assert budget.small_tokens == 0


def test_budgeted_provider_purpose_forwarded() -> None:
    inner = FakeProvider()
    budget = Budget()
    wrapped = BudgetedProvider(inner, budget)
    wrapped.complete([], purpose="probe-selection")
    assert inner.calls[0]["purpose"] == "probe-selection"


# ---------------------------------------------------------------------------
# AnthropicProvider
# ---------------------------------------------------------------------------


def test_anthropic_provider_model_for_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_FRONTIER_MODEL", "frontier-123")
    monkeypatch.setenv("ANTHROPIC_SMALL_MODEL", "small-456")
    p = AnthropicProvider()
    assert p.model_for_tier("frontier") == "frontier-123"
    assert p.model_for_tier("small") == "small-456"
    with pytest.raises(ValueError, match="unknown tier"):
        p.model_for_tier("huge")


def test_anthropic_provider_explicit_models() -> None:
    p = AnthropicProvider(frontier_model="f1", small_model="s1")
    assert p.frontier_model == "f1"
    assert p.small_model == "s1"


def test_anthropic_provider_api_key_from_arg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_FRONTIER_MODEL", "f-env")
    monkeypatch.setenv("ANTHROPIC_SMALL_MODEL", "s-env")
    p = AnthropicProvider(api_key="sk-test-123")
    assert p.api_key == "sk-test-123"


def test_anthropic_provider_api_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    monkeypatch.setenv("ANTHROPIC_FRONTIER_MODEL", "f-env")
    monkeypatch.setenv("ANTHROPIC_SMALL_MODEL", "s-env")
    assert AnthropicProvider().api_key == "sk-env"


def test_anthropic_provider_complete_parses_response() -> None:
    p = AnthropicProvider(frontier_model="f", small_model="s", api_key="k")
    # Mock anthropic client
    mock_client = MagicMock()
    p._client = mock_client
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "hello world"
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.id = "toolu_1"
    tool_block.name = "search"
    tool_block.input = {"q": "docs"}
    usage_obj = MagicMock()
    usage_obj.input_tokens = 10
    usage_obj.output_tokens = 25
    mock_client.messages.create.return_value = MagicMock(
        content=[text_block, tool_block], usage=usage_obj
    )
    result = p.complete(
        [{"role": "user", "content": "hi"}],
        tier="small",
        purpose="test",
        max_tokens=512,
    )
    assert result.text == "hello world"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "search"
    assert result.usage.input_tokens == 10
    assert result.usage.output_tokens == 25
    # purpose is not forwarded to Anthropic API (handled by ledger)
    call_kwargs = mock_client.messages.create.call_args.kwargs
    assert call_kwargs["model"] == "s"
    assert call_kwargs["max_tokens"] == 512


def test_anthropic_provider_system_extraction() -> None:
    p = AnthropicProvider(frontier_model="f", small_model="s", api_key="k")
    mock_client = MagicMock()
    p._client = mock_client
    mock_client.messages.create.return_value = MagicMock(
        content=[], usage=MagicMock(input_tokens=0, output_tokens=0)
    )
    p.complete(
        [
            {"role": "system", "content": "you are helpful"},
            {"role": "user", "content": "hi"},
            {"role": "system", "content": "more system"},
        ],
        tier="small",
    )
    kwargs = mock_client.messages.create.call_args.kwargs
    assert kwargs["system"] == "you are helpful\nmore system"
    # Only non-system messages forwarded
    assert all(m["role"] != "system" for m in kwargs["messages"])


def test_anthropic_provider_tools_forwarded() -> None:
    p = AnthropicProvider(frontier_model="f", small_model="s", api_key="k")
    mock_client = MagicMock()
    p._client = mock_client
    mock_client.messages.create.return_value = MagicMock(
        content=[], usage=MagicMock(input_tokens=0, output_tokens=0)
    )
    tools = [{"name": "search", "description": "x", "input_schema": {"type": "object"}}]
    p.complete([{"role": "user", "content": "hi"}], tools=tools)
    assert mock_client.messages.create.call_args.kwargs["tools"] == tools


def test_anthropic_provider_no_tools_means_no_tools_kwarg() -> None:
    p = AnthropicProvider(frontier_model="f", small_model="s", api_key="k")
    mock_client = MagicMock()
    p._client = mock_client
    mock_client.messages.create.return_value = MagicMock(
        content=[], usage=MagicMock(input_tokens=0, output_tokens=0)
    )
    p.complete([{"role": "user", "content": "hi"}])
    assert "tools" not in mock_client.messages.create.call_args.kwargs


def test_anthropic_provider_unknown_tier_raises_before_network() -> None:
    p = AnthropicProvider(frontier_model="f", small_model="s", api_key="k")
    with pytest.raises(ValueError, match="unknown tier"):
        p.complete([{"role": "user", "content": "hi"}], tier="mega")


def test_anthropic_provider_lazy_client_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    # No _client yet; _get_client creates one
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-lazy")
    monkeypatch.setenv("ANTHROPIC_FRONTIER_MODEL", "f-env")
    monkeypatch.setenv("ANTHROPIC_SMALL_MODEL", "s-env")
    p = AnthropicProvider()
    assert p._client is None
    # Mock anthropic module
    import sys

    fake_anth = MagicMock()
    monkeypatch.setitem(sys.modules, "anthropic", fake_anth)
    fake_anth.Anthropic.return_value = MagicMock()
    c = p._get_client()
    assert c is not None
    assert p._client is c


# ---------------------------------------------------------------------------
# OpenAIProvider (P2.S5.1 — live Chat Completions adapter)
# ---------------------------------------------------------------------------


def test_openai_provider_model_for_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_FRONTIER_MODEL", "gpt-frontier")
    monkeypatch.setenv("OPENAI_SMALL_MODEL", "gpt-small")
    p = OpenAIProvider()
    assert p.model_for_tier("frontier") == "gpt-frontier"
    assert p.model_for_tier("small") == "gpt-small"
    with pytest.raises(ValueError, match="unknown tier"):
        p.model_for_tier("mega")


def test_openai_provider_explicit_models() -> None:
    p = OpenAIProvider(frontier_model="o-f", small_model="o-s")
    assert p.frontier_model == "o-f"
    assert p.small_model == "o-s"


def _openai_mock_client(
    text: str = "hello world",
    tool_calls: list | None = None,
    prompt_tokens: int = 10,
    completion_tokens: int = 25,
) -> MagicMock:
    """Mocked openai client: chat.completions.create returns a fixed response."""
    from types import SimpleNamespace

    fn_calls = []
    for tc in tool_calls or []:
        fn_calls.append(
            SimpleNamespace(
                id=tc.get("id", "call_1"),
                function=SimpleNamespace(
                    name=tc.get("name", "search"),
                    arguments=tc.get("arguments", '{"q": "docs"}'),
                ),
            )
        )
    message = SimpleNamespace(content=text, tool_calls=fn_calls)
    resp = SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = resp
    return mock_client


def test_openai_provider_complete_parses_response() -> None:
    p = OpenAIProvider(frontier_model="f", small_model="s", api_key="k")
    p._client = _openai_mock_client(
        tool_calls=[{"id": "call_1", "name": "search", "arguments": '{"q": "docs"}'}]
    )
    result = p.complete(
        [{"role": "user", "content": "hi"}],
        tier="small",
        purpose="test",
        max_tokens=512,
    )
    assert result.text == "hello world"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].id == "call_1"
    assert result.tool_calls[0].name == "search"
    assert result.tool_calls[0].arguments == {"q": "docs"}
    assert result.usage.input_tokens == 10
    assert result.usage.output_tokens == 25
    call_kwargs = p._client.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "s"
    assert call_kwargs["max_tokens"] == 512


def test_openai_provider_frontier_model_selected() -> None:
    p = OpenAIProvider(frontier_model="f", small_model="s", api_key="k")
    p._client = _openai_mock_client(text="frontier answer")
    result = p.complete([{"role": "user", "content": "hi"}], tier="frontier")
    assert result.text == "frontier answer"
    assert p._client.chat.completions.create.call_args.kwargs["model"] == "f"


def test_openai_provider_system_messages_pass_through() -> None:
    """OpenAI accepts system roles inline: messages are forwarded unchanged."""
    p = OpenAIProvider(frontier_model="f", small_model="s", api_key="k")
    p._client = _openai_mock_client(text="")
    messages = [
        {"role": "system", "content": "you are helpful"},
        {"role": "user", "content": "hi"},
    ]
    p.complete(messages, tier="small")
    forwarded = p._client.chat.completions.create.call_args.kwargs["messages"]
    assert forwarded == messages


def test_openai_provider_anthropic_style_tools_converted() -> None:
    p = OpenAIProvider(frontier_model="f", small_model="s", api_key="k")
    p._client = _openai_mock_client(text="")
    tools = [{"name": "search", "description": "x", "input_schema": {"type": "object"}}]
    p.complete([{"role": "user", "content": "hi"}], tools=tools)
    sent = p._client.chat.completions.create.call_args.kwargs["tools"]
    assert sent == [
        {
            "type": "function",
            "function": {"name": "search", "description": "x", "parameters": {"type": "object"}},
        }
    ]


def test_openai_provider_native_tools_pass_through() -> None:
    p = OpenAIProvider(frontier_model="f", small_model="s", api_key="k")
    p._client = _openai_mock_client(text="")
    native = [{"type": "function", "function": {"name": "search", "parameters": {"type": "object"}}}]
    p.complete([{"role": "user", "content": "hi"}], tools=native)
    assert p._client.chat.completions.create.call_args.kwargs["tools"] == native


def test_openai_provider_no_tools_means_no_tools_kwarg() -> None:
    p = OpenAIProvider(frontier_model="f", small_model="s", api_key="k")
    p._client = _openai_mock_client(text="")
    p.complete([{"role": "user", "content": "hi"}])
    assert "tools" not in p._client.chat.completions.create.call_args.kwargs


def test_openai_provider_bad_tool_arguments_become_empty_dict() -> None:
    p = OpenAIProvider(frontier_model="f", small_model="s", api_key="k")
    p._client = _openai_mock_client(
        tool_calls=[{"id": "call_9", "name": "search", "arguments": "not-json{{{"}]
    )
    result = p.complete([{"role": "user", "content": "hi"}])
    assert result.tool_calls[0].arguments == {}


def test_openai_provider_null_content_becomes_empty_text() -> None:
    p = OpenAIProvider(frontier_model="f", small_model="s", api_key="k")
    p._client = _openai_mock_client(text=None)  # type: ignore[arg-type]
    result = p.complete([{"role": "user", "content": "hi"}])
    assert result.text == ""


def test_openai_provider_unknown_tier_raises_before_network() -> None:
    p = OpenAIProvider(frontier_model="f", small_model="s", api_key="k")
    with pytest.raises(ValueError, match="unknown tier"):
        p.complete([{"role": "user", "content": "hi"}], tier="mega")
    assert p._client is None  # no client (hence no network) was built


def test_openai_provider_lazy_client_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    # No _client yet; _get_client creates one
    monkeypatch.setenv("OPENAI_API_KEY", "sk-lazy")
    monkeypatch.setenv("OPENAI_FRONTIER_MODEL", "f-env")
    monkeypatch.setenv("OPENAI_SMALL_MODEL", "s-env")
    p = OpenAIProvider()
    assert p._client is None
    # Mock openai module
    import sys

    fake_openai = MagicMock()
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    fake_openai.OpenAI.return_value = MagicMock()
    c = p._get_client()
    assert c is not None
    assert p._client is c
    fake_openai.OpenAI.assert_called_once_with(api_key="sk-lazy")


def test_openai_provider_api_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("OPENAI_FRONTIER_MODEL", "o-f-env")
    monkeypatch.setenv("OPENAI_SMALL_MODEL", "o-s-env")
    assert OpenAIProvider().api_key == "sk-openai"


# ---------------------------------------------------------------------------
# Config model ids
# ---------------------------------------------------------------------------


def test_config_missing_env_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in [
        "ANTHROPIC_FRONTIER_MODEL",
        "ANTHROPIC_SMALL_MODEL",
        "OPENAI_FRONTIER_MODEL",
        "OPENAI_SMALL_MODEL",
    ]:
        monkeypatch.delenv(k, raising=False)
    from autobridge.config import (
        anthropic_frontier_model,
        anthropic_small_model,
        openai_frontier_model,
        openai_small_model,
    )

    for fn, key in [
        (anthropic_frontier_model, "ANTHROPIC_FRONTIER_MODEL"),
        (anthropic_small_model, "ANTHROPIC_SMALL_MODEL"),
        (openai_frontier_model, "OPENAI_FRONTIER_MODEL"),
        (openai_small_model, "OPENAI_SMALL_MODEL"),
    ]:
        with pytest.raises(RuntimeError, match=key):
            fn()


def test_config_env_provides_model_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_FRONTIER_MODEL", "my-frontier")
    from autobridge.config import anthropic_frontier_model

    assert anthropic_frontier_model() == "my-frontier"


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_fake_satisfies_protocol() -> None:
    from autobridge.llm.provider import LLMProvider

    fake = FakeProvider()
    assert isinstance(fake, LLMProvider)


def test_anthropic_satisfies_protocol() -> None:
    from autobridge.llm.provider import LLMProvider

    p = AnthropicProvider(frontier_model="f", small_model="s", api_key="k")
    assert isinstance(p, LLMProvider)


def test_openai_satisfies_protocol() -> None:
    from autobridge.llm.provider import LLMProvider

    p = OpenAIProvider(frontier_model="f", small_model="s")
    assert isinstance(p, LLMProvider)


def test_budgeted_satisfies_protocol() -> None:
    from autobridge.llm.provider import LLMProvider

    inner = FakeProvider()
    wrapped = BudgetedProvider(inner, Budget())
    assert isinstance(wrapped, LLMProvider)
