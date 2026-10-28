"""Tests for the Meta Muse frontier provider, pricing and redaction (REQ-021 item 5)."""

from __future__ import annotations

import os
from decimal import Decimal
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from sqlalchemy import create_engine

from autobridge import config as config_mod
from autobridge.control import models as m
from autobridge.llm.provider import (
    BudgetedProvider,
    Completion,
    FakeProvider,
    MuseProvider,
    OpenAIProvider,
    Usage,
    _parse_openai_chat_response,
    estimate_cost,
    record_model_call,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _clear_frontier_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("FRONTIER_API_KEY", "FRONTIER_BASE_URL", "FRONTIER_MODEL", "SMALL_MODEL"):
        monkeypatch.delenv(key, raising=False)


# ---------------------------------------------------------------------------
# Config: frontier settings come from env, never from code
# ---------------------------------------------------------------------------


def test_frontier_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_frontier_env(monkeypatch)
    assert config_mod.frontier_api_key() == ""
    assert config_mod.frontier_base_url() == "https://api.meta.ai/v1"
    assert config_mod.frontier_model() == "muse-spark-1.3"
    # Small tier defaults to the frontier model (same provider and key).
    assert config_mod.small_model() == "muse-spark-1.3"


def test_frontier_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRONTIER_API_KEY", "meta-key-123")
    monkeypatch.setenv("FRONTIER_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("FRONTIER_MODEL", "muse-wonder-1.0")
    monkeypatch.setenv("SMALL_MODEL", "muse-spark-1.3")
    assert config_mod.frontier_api_key() == "meta-key-123"
    assert config_mod.frontier_base_url() == "https://example.invalid/v1"
    assert config_mod.frontier_model() == "muse-wonder-1.0"
    assert config_mod.small_model() == "muse-spark-1.3"


def test_small_model_follows_frontier_model(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_frontier_env(monkeypatch)
    monkeypatch.setenv("FRONTIER_MODEL", "muse-wonder-1.0")
    assert config_mod.small_model() == "muse-wonder-1.0"


# ---------------------------------------------------------------------------
# MuseProvider: tiers, base_url wiring, parsing
# ---------------------------------------------------------------------------


def _stub_openai_response(
    text: str = "hello",
    *,
    tool_calls: list[object] | None = None,
    prompt_tokens: int = 10,
    completion_tokens: int = 20,
) -> SimpleNamespace:
    message = SimpleNamespace(content=text, tool_calls=tool_calls or [])
    choice = SimpleNamespace(message=message)
    usage = SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    return SimpleNamespace(choices=[choice], usage=usage)


def _stub_client(response: object, captured: dict[str, object]) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.return_value = response
    captured["client"] = client
    return client


def test_muse_provider_tiers_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_frontier_env(monkeypatch)
    monkeypatch.setenv("FRONTIER_MODEL", "muse-wonder-1.0")
    monkeypatch.setenv("SMALL_MODEL", "muse-spark-1.3")
    p = MuseProvider(api_key="k")
    assert p.provider_name == "muse"
    assert p.model_for_tier("frontier") == "muse-wonder-1.0"
    assert p.model_for_tier("small") == "muse-spark-1.3"


def test_muse_provider_unknown_tier_raises_before_network() -> None:
    p = MuseProvider(api_key="k", frontier_model="f", small_model="s")
    try:
        p.model_for_tier("ultra")
    except ValueError as exc:
        assert "ultra" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


def test_muse_provider_uses_base_url_and_key(monkeypatch: pytest.MonkeyPatch) -> None:
    import openai as openai_mod

    constructed: dict[str, object] = {}
    real_openai = openai_mod.OpenAI

    def fake_openai(**kwargs: object) -> MagicMock:
        constructed.update(kwargs)
        return _stub_client(_stub_openai_response(), {})

    monkeypatch.setattr(openai_mod, "OpenAI", fake_openai)
    try:
        p = MuseProvider(
            api_key="meta-key-abc",
            base_url="https://api.meta.ai/v1",
            frontier_model="muse-spark-1.3",
            small_model="muse-spark-1.3",
        )
        out = p.complete([{"role": "user", "content": "hi"}], tier="frontier", purpose="t")
    finally:
        monkeypatch.setattr(openai_mod, "OpenAI", real_openai)
    assert constructed["api_key"] == "meta-key-abc"
    assert constructed["base_url"] == "https://api.meta.ai/v1"
    assert out.text == "hello"
    assert out.usage.total_tokens == 30


def test_muse_provider_parses_tool_calls() -> None:
    tc = SimpleNamespace(
        id="call-1",
        function=SimpleNamespace(name="propose_patch", arguments='{"a": 1}'),
    )
    resp = _stub_openai_response(
        text="", tool_calls=[tc], prompt_tokens=5, completion_tokens=7
    )
    out = _parse_openai_chat_response(resp)
    assert out.text == ""
    assert len(out.tool_calls) == 1
    assert out.tool_calls[0].name == "propose_patch"
    assert out.tool_calls[0].arguments == {"a": 1}
    assert (out.usage.input_tokens, out.usage.output_tokens) == (5, 7)


def test_openai_provider_refactor_unchanged() -> None:
    # The shared parser keeps OpenAIProvider behavior identical.
    p = OpenAIProvider(api_key="k", frontier_model="gpt-x", small_model="gpt-y")
    assert p.model_for_tier("frontier") == "gpt-x"
    resp = _stub_openai_response(text="ok")
    assert _parse_openai_chat_response(resp).text == "ok"


def test_muse_provider_flows_through_budget_and_ledger(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path}/muse.db")
    m.init_db(engine)
    factory = m.session_factory(engine)
    p = MuseProvider(api_key="k", frontier_model="muse-spark-1.3", small_model="s")
    p._client = _stub_client(_stub_openai_response(prompt_tokens=1000, completion_tokens=0), {})
    from autobridge.llm.provider import Budget

    with factory() as s:
        budgeted = BudgetedProvider(p, Budget(), session=s, run_id="run-1")
        budgeted.complete([{"role": "user", "content": "hi"}], tier="frontier", purpose="ladder-l3")
        s.commit()
        rows = s.query(m.ModelCall).all()
        assert len(rows) == 1
        assert rows[0].provider == "MuseProvider"
        assert rows[0].model == "muse-spark-1.3"
        # Spark input price: 1000 tokens @ $1.25/1M = $0.00125
        assert rows[0].cost_estimate == Decimal("0.001250")


# ---------------------------------------------------------------------------
# Pricing: published per-model prices, fallback to tier rates
# ---------------------------------------------------------------------------


def test_estimate_cost_spark_pricing() -> None:
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert estimate_cost("frontier", usage, model="muse-spark-1.3") == Decimal("5.500000")


def test_estimate_cost_wonder_pricing() -> None:
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert estimate_cost("frontier", usage, model="muse-wonder-1.0") == Decimal("24.000000")


def test_estimate_cost_unknown_model_falls_back_to_tier() -> None:
    usage = Usage(input_tokens=1000, output_tokens=0)
    assert estimate_cost("frontier", usage, model="no-such-model") == Decimal("0.015000")
    assert estimate_cost("frontier", usage) == Decimal("0.015000")


def test_record_model_call_uses_model_pricing(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path}/muse2.db")
    m.init_db(engine)
    factory = m.session_factory(engine)
    with factory() as s:
        row = record_model_call(
            s,
            run_id="r1",
            provider="muse",
            model="muse-spark-1.3",
            tier="frontier",
            usage=Usage(input_tokens=8000, output_tokens=2000),
            purpose="ladder-l3",
        )
        s.commit()
        # 8000 @ 1.25/M + 2000 @ 4.25/M = 0.01 + 0.0085
        assert row.cost_estimate == Decimal("0.018500")


# ---------------------------------------------------------------------------
# Redaction: FRONTIER_API_KEY is covered by the SSE secret sweep
# ---------------------------------------------------------------------------


def test_frontier_api_key_redacted_from_sse(monkeypatch: pytest.MonkeyPatch) -> None:
    from autobridge.control.sse import collect_secrets
    from autobridge.evidence.redact import redact_payload

    monkeypatch.setenv("FRONTIER_API_KEY", "meta-frontier-key-xyz-999")
    secrets = collect_secrets(None)
    assert "meta-frontier-key-xyz-999" in secrets
    payload = {"note": "called with meta-frontier-key-xyz-999 inline"}
    assert redact_payload(payload, secrets)["note"] == "called with *** inline"


def test_fake_provider_still_satisfies_protocol() -> None:
    # Guard: the ladder's default test double is untouched by the refactor.
    fake = FakeProvider()
    assert fake.model_for_tier("frontier") == "fake-frontier"
    out = fake.complete([{"role": "user", "content": "x"}], tier="small")
    assert isinstance(out, Completion)
    # BudgetedProvider still records through the shared path.
    assert BudgetedProvider is not None


def test_muse_provider_env_key_default_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_frontier_env(monkeypatch)
    assert os.environ.get("FRONTIER_API_KEY", "") == ""
    p = MuseProvider()
    assert p.api_key == ""
    assert p.base_url == "https://api.meta.ai/v1"
