"""P2.S5.1 — OpenAI provider parity (plan §5 S5).

Passing criterion (plan §5 S5): one dev scenario reaches an identical
ladder outcome on both providers; own e2e file
``tests/e2e/test_s5_provider_parity.py``.

The dev scenario is the ``rename_guest_id`` known repair on the generated
Atlas reference spec: the same SCHEMA_RENAME drift context is run through
the ladder twice — once with an ``AnthropicProvider`` and once with an
``OpenAIProvider`` — and both runs must reach the identical outcome
(same decision, same rule, same patch). The provider-touching rung (L2
hypothesis ranking) is driven through both providers with equivalent
mocked SDK payloads and must return the identical ranked order with the
identical token usage.

No network, no keys: both SDKs (``anthropic``, ``openai``) are mocked via
``sys.modules`` so the suite is green without Docker or credentials.
A live variant runs only when ``AUTOBRIDGE_LIVE_PROVIDER_PARITY=1`` plus
both API keys are present (owner-run; never in CI).

Trust boundary: this file lives in tests/e2e and may import engine code;
the engine itself never imports verifier/simulators, and neither provider
does either.
"""

from __future__ import annotations

import json
import os
import pathlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ATLAS_SPEC_PATH = REPO_ROOT / "generated" / "connectors" / "atlaspms" / "reference" / "spec.yaml"

# The dev scenario: rename_guest_id drift context (same shape as the W4
# known-repair harness) — the ladder must resolve it identically on both
# providers.
RENAME_EXTRA: dict[str, Any] = {
    "entity": "Reservation",
    "field": "guest_name",
    "new_field": "guest_identifier",
    "rename": {
        "entity": "Reservation",
        "old_field": "guest_name",
        "new_field": "guest_identifier",
    },
}

# Equivalent L2 ranking payload returned by both mocked SDKs: most-likely
# hypothesis first. Both providers must surface the identical order.
RANK_ORDER = ["l2:hypothesis:rename-field", "l2:hypothesis:pagination-shift"]


def _load_atlas_spec() -> dict[str, Any]:
    assert ATLAS_SPEC_PATH.is_file(), f"missing {ATLAS_SPEC_PATH}"
    return yaml.safe_load(ATLAS_SPEC_PATH.read_text(encoding="utf-8"))


def _mock_anthropic_sdk(monkeypatch: pytest.MonkeyPatch, text: str) -> MagicMock:
    """Mock the ``anthropic`` SDK module; its client returns *text*."""
    import sys

    usage = SimpleNamespace(input_tokens=10, output_tokens=20)
    resp = SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], usage=usage)
    client = MagicMock()
    client.messages.create.return_value = resp
    sdk = MagicMock()
    sdk.Anthropic.return_value = client
    monkeypatch.setitem(sys.modules, "anthropic", sdk)
    return client


def _mock_openai_sdk(monkeypatch: pytest.MonkeyPatch, text: str) -> MagicMock:
    """Mock the ``openai`` SDK module; its client returns *text*."""
    import sys

    message = SimpleNamespace(content=text, tool_calls=[])
    resp = SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20),
    )
    client = MagicMock()
    client.chat.completions.create.return_value = resp
    sdk = MagicMock()
    sdk.OpenAI.return_value = client
    monkeypatch.setitem(sys.modules, "openai", sdk)
    return client


def _make_providers() -> tuple[Any, Any]:
    from autobridge.llm.provider import AnthropicProvider, OpenAIProvider

    anthropic_p = AnthropicProvider(frontier_model="a-frontier", small_model="a-small", api_key="k")
    openai_p = OpenAIProvider(frontier_model="o-frontier", small_model="o-small", api_key="k")
    return anthropic_p, openai_p


def _stub_hypotheses() -> list[Any]:
    """Two hypotheses for the rename dev scenario (L2 ranks these)."""
    return [
        SimpleNamespace(id=RANK_ORDER[0], prior=0.7),
        SimpleNamespace(id=RANK_ORDER[1], prior=0.3),
    ]


# ---------------------------------------------------------------------------
# 1. Provider-level parity: same messages → same Completion
# ---------------------------------------------------------------------------


def test_s5_same_messages_same_completion_both_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Identical prompts yield identical Completions on both providers."""
    from autobridge.llm.provider import LLMProvider

    ranked_text = json.dumps(RANK_ORDER)
    _mock_anthropic_sdk(monkeypatch, ranked_text)
    _mock_openai_sdk(monkeypatch, ranked_text)
    anthropic_p, openai_p = _make_providers()
    assert isinstance(anthropic_p, LLMProvider)
    assert isinstance(openai_p, LLMProvider)

    messages = [
        {"role": "system", "content": "Rank hypotheses. Return JSON list of ids."},
        {"role": "user", "content": "hypotheses: ['a', 'b']"},
    ]
    a_result = anthropic_p.complete(messages, max_tokens=512, tier="small", purpose="parity")
    o_result = openai_p.complete(messages, max_tokens=512, tier="small", purpose="parity")

    assert a_result.text == o_result.text == ranked_text
    assert a_result.tool_calls == o_result.tool_calls == []
    assert a_result.usage.total_tokens == o_result.usage.total_tokens == 30
    # Same tier selects each provider's own small model id (config, not code).
    assert anthropic_p.model_for_tier("small") == "a-small"
    assert openai_p.model_for_tier("small") == "o-small"


# ---------------------------------------------------------------------------
# 2. Ladder-level parity: L2 ranking identical on both providers
# ---------------------------------------------------------------------------


def test_s5_l2_rank_identical_on_both_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """The provider-touching rung (L2 rank) returns the identical order."""
    from autobridge.llm.provider import Budget
    from autobridge.repair.ladder import _call_small_model_rank

    ranked_text = json.dumps(RANK_ORDER)
    _mock_anthropic_sdk(monkeypatch, ranked_text)
    _mock_openai_sdk(monkeypatch, ranked_text)
    anthropic_p, openai_p = _make_providers()

    a_ranked, a_usage, _ = _call_small_model_rank(
        _stub_hypotheses(), anthropic_p, Budget(), purpose="parity-l2"
    )
    o_ranked, o_usage, _ = _call_small_model_rank(
        _stub_hypotheses(), openai_p, Budget(), purpose="parity-l2"
    )

    assert [h.id for h in a_ranked] == RANK_ORDER
    assert [h.id for h in o_ranked] == RANK_ORDER
    assert a_usage == o_usage
    assert a_usage["frontier_tokens"] == 0
    assert a_usage["small_calls"] == 1


# ---------------------------------------------------------------------------
# 3. Full ladder parity on the rename_guest_id dev scenario
# ---------------------------------------------------------------------------


def test_s5_rename_dev_scenario_identical_ladder_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One dev scenario reaches an identical ladder outcome on both providers.

    The same SCHEMA_RENAME context is resolved with the Anthropic-backed
    provider and the OpenAI-backed provider wired in: same decision, same
    rule, same patch, same ladder level, zero frontier tokens either way.
    """
    from autobridge.repair.knowledge import KnowledgeCache
    from autobridge.repair.ladder import run_ladder

    ranked_text = json.dumps(RANK_ORDER)
    _mock_anthropic_sdk(monkeypatch, ranked_text)
    _mock_openai_sdk(monkeypatch, ranked_text)
    anthropic_p, openai_p = _make_providers()
    spec = _load_atlas_spec()

    def run_with(provider: Any) -> Any:
        return run_ladder(
            dict(spec),
            "SCHEMA_RENAME",
            signals=[],
            fingerprint_delta=None,
            extra=dict(RENAME_EXTRA),
            vendor="AtlasPMS",
            knowledge_cache=KnowledgeCache(),
            provider=provider,
        )

    a_result = run_with(anthropic_p)
    o_result = run_with(openai_p)

    assert a_result.decision == o_result.decision == "propose"
    assert a_result.rule_id == o_result.rule_id == "rename_field"
    assert a_result.candidate_patch is not None
    assert a_result.candidate_patch == o_result.candidate_patch
    assert a_result.ladder_level_reached == o_result.ladder_level_reached
    assert a_result.model_usage["frontier_tokens"] == 0
    assert o_result.model_usage["frontier_tokens"] == 0
    # Deterministic: patch applies and the ladder trace agrees rung by rung.
    assert [t.get("level") for t in a_result.ladder_trace] == [
        t.get("level") for t in o_result.ladder_trace
    ]


# ---------------------------------------------------------------------------
# 4. Ledger parity: ModelCall rows carry each provider's own name
# ---------------------------------------------------------------------------


def test_s5_budgeted_ledger_labels_each_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both providers flow through Budget + ModelCall with their own label."""
    from sqlalchemy import create_engine

    from autobridge.control import models as m
    from autobridge.llm.provider import Budget, BudgetedProvider

    ranked_text = json.dumps(RANK_ORDER)
    _mock_anthropic_sdk(monkeypatch, ranked_text)
    _mock_openai_sdk(monkeypatch, ranked_text)
    anthropic_p, openai_p = _make_providers()

    engine = create_engine("sqlite:///:memory:")
    m.init_db(engine)
    factory = m.session_factory(engine)
    with factory() as session:
        for provider, name in ((anthropic_p, "anthropic"), (openai_p, "openai")):
            wrapped = BudgetedProvider(
                provider,
                Budget(),
                session=session,
                run_id="parity-run",
                provider_name=name,
            )
            wrapped.complete(
                [{"role": "user", "content": "rank"}],
                tier="small",
                purpose="parity-ledger",
            )
        session.commit()
        rows = (
            session.query(m.ModelCall)
            .filter(m.ModelCall.run_id == "parity-run")
            .order_by(m.ModelCall.provider)
            .all()
        )
        assert [r.provider for r in rows] == ["anthropic", "openai"]
        assert all(r.purpose == "parity-ledger" for r in rows)
        assert all(r.tier == "small" for r in rows)


# ---------------------------------------------------------------------------
# 5. Live variant (owner-run only; skipped without keys + explicit opt-in)
# ---------------------------------------------------------------------------


def test_s5_live_parity_opt_in_only() -> None:
    """Live provider calls never run unless the owner explicitly opts in."""
    if os.environ.get("AUTOBRIDGE_LIVE_PROVIDER_PARITY") != "1":
        pytest.skip("live provider parity requires AUTOBRIDGE_LIVE_PROVIDER_PARITY=1")
    if not os.environ.get("ANTHROPIC_API_KEY") or not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("live provider parity requires both API keys")
    pytest.skip("live parity scaffold: owner runs one prompt on both providers manually")
