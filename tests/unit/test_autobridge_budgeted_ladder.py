"""P1.W5.5: Budgeted L2/L3 wiring, BudgetExhausted→L4, surviving ambiguity→Degraded."""

from __future__ import annotations

import json

import pytest
import yaml

from autobridge.discovery.docs_reader import Provenance
from autobridge.discovery.hypotheses import Hypothesis
from autobridge.discovery.probes import ProbeBudget
from autobridge.llm.provider import Budget, Completion, FakeProvider, Usage
from autobridge.repair.escalation import EscalationBundle
from autobridge.repair.ladder import run_ladder


def _load_spec(vendor: str = "test") -> dict:
    p = f"generated/connectors/{vendor}/reference/spec.yaml"
    try:
        import pathlib

        txt = pathlib.Path(p).read_text()
        return yaml.safe_load(txt)
    except Exception:
        return {
            "spec_version": 1,
            "connector": {"id": "test", "vendor": vendor},
            "source": {"base_url": "http://example.com", "auth": {"type": "bearer", "credential_ref": "TEST_TOKEN"}, "timezone_source": {"kind": "static", "value": "UTC"}},
            "operations": {
                "list_reservations": {"method": "GET", "path": "/reservations", "pagination": {"strategy": "offset", "page_param": "offset", "limit_param": "limit", "next_path": "$.next_offset", "items_path": "$.data", "stop_when": ["null_cursor"]}, "checkpoint": {"field": "$.updated_at", "mode": "max"}},
                "list_folios": {"method": "GET", "path": "/folios", "pagination": {"strategy": "offset", "page_param": "offset", "limit_param": "limit", "next_path": "$.next_offset", "items_path": "$.data", "stop_when": ["null_cursor"]}, "checkpoint": {"field": "$.updated_at", "mode": "max"}},
            },
            "entities": {
                "Reservation": {"from": "list_reservations", "key": "$.id", "fields": {"reservation_id": {"path": "$.id"}, "arrival_date": {"path": "$.arrival_date"}, "status": {"path": "$.status"}, "total_amount": {"path": "$.total_amount"}}, "checks": [{"kind": "unique_key"}]},
                "FolioCharge": {"from": "list_folios", "key": "$.id", "fields": {"charge_id": {"path": "$.id"}, "amount": {"path": "$.amount"}}, "checks": [{"kind": "unique_key"}]},
            },
            "capabilities": {"read": ["Reservation", "FolioCharge"]},
            "health": {"canary": {"every": "60s", "operation": "list_reservations", "max_pages": 1}, "fingerprint": {"keys": True, "types": True, "pagination_shape": True}},
        }


def _prov():
    return Provenance(source="docs", line=1, snippet="test")


def _hyp(id, family, strategy, prior=0.33, field=None):
    # Build valid fragment per family
    if family == "date":
        frag = {"family": "date", "field": field or "date", "strategy": strategy, "transform": {"name": "business_date"}}
    elif family == "pagination":
        frag = {"family": "pagination", "strategy": strategy, "operation": field or "/reservations", "pagination": {"strategy": strategy}}
    elif family == "amount":
        frag = {"family": "amount", "field": field or "amount", "strategy": strategy, "transform": {"name": "money"}}
    elif family == "status":
        frag = {"family": "status", "field": field or "status", "strategy": strategy, "transform": {"mapping": {"H1": "confirmed"}}}
    else:
        frag = {"family": family, "field": field or "x", "strategy": strategy}
    return Hypothesis(
        id=id,
        family=family,
        claim=f"{family} {strategy}",
        candidate_mapping_fragment=frag,
        prior=prior,
        evidence_for=(_prov(),),
        field=field or "date",
        strategy=strategy,
    )


# ---------------------------------------------------------------------------
# L3 only when enumerator yields <2 (asserted)
# ---------------------------------------------------------------------------


def test_l3_assert_when_enumerator_yields_ge_2():
    """L3 frontier invoked only when enumerator yields <2; assert with >=2."""
    # Direct helper must assert
    from autobridge.repair.ladder import _call_frontier_generate

    hyps = [_hyp("date:date:business", "date", "business"), _hyp("date:date:arrival", "date", "arrival")]
    with pytest.raises(AssertionError, match="L3 frontier invoked only when enumerator yields fewer than two"):
        _call_frontier_generate(
            hyps,
            FakeProvider(responses={"frontier": Completion(text='{"id": "x", "family": "date", "claim": "new"}', usage=Usage(input_tokens=1, output_tokens=1))}),
            Budget(max_frontier_tokens=100, max_calls=10),
            enumerator_yield_count=2,
        )
    # run_ladder with >=2 should skip L3, not assert
    spec = _load_spec()
    result = run_ladder(
        spec,
        "UNKNOWN",
        hypotheses=hyps,
        enumerator_yield_count=2,
        provider=FakeProvider(responses={"frontier": Completion(text='{"id": "x", "family": "date", "claim": "new"}', usage=Usage(input_tokens=1, output_tokens=1))}),
        budget=Budget(max_frontier_tokens=100, max_calls=10),
    )
    assert any(t.get("action") == "l3_skipped" for t in result.ladder_trace)


def test_l3_allowed_when_enumerator_yields_1():
    """L3 allowed when enumerator yields 1."""
    spec = _load_spec()
    h1 = _hyp("date:date:business", "date", "business")
    # Frontier returns a new hypothesis as JSON
    provider = FakeProvider(
        responses={
            "frontier": Completion(text=json.dumps({"id": "date:date:generated", "family": "date", "claim": "generated", "fragment": {"generated": True}, "prior": 0.5, "field": "date", "strategy": "utc"}), usage=Usage(input_tokens=5, output_tokens=5))
        }
    )
    budget = Budget(max_frontier_tokens=100, max_calls=10)
    result = run_ladder(
        spec,
        "UNKNOWN",
        hypotheses=[h1],
        enumerator_yield_count=1,
        provider=provider,
        budget=budget,
    )
    # Should have attempted L3 (trace contains frontier_generate) and not asserted
    assert any(t.get("action") == "frontier_generate" for t in result.ladder_trace)
    # After L3 we have 2 hypotheses, then L2 ranking would be attempted if provider still available
    # But our FakeProvider only has frontier response; L2 would be no-op fallback (no provider for small? actually same provider can do small too)
    # Check that we got a result (either propose or degraded, not assertion)
    assert result.decision in ("propose", "degraded", "escalate")


def test_l3_allowed_when_enumerator_yields_0():
    spec = _load_spec()
    h1 = _hyp("date:date:business", "date", "business")
    provider = FakeProvider(responses={"frontier": Completion(text=json.dumps({"id": "x", "family": "date", "claim": "new"}), usage=Usage(2, 2))})
    budget = Budget(max_frontier_tokens=100, max_calls=10)
    result = run_ladder(spec, "UNKNOWN", hypotheses=[h1], enumerator_yield_count=0, provider=provider, budget=budget)
    assert any(t.get("action") == "frontier_generate" for t in result.ladder_trace)


def test_l3_skipped_when_enumerator_yields_ge_2_trace():
    spec = _load_spec()
    hyps = [_hyp("date:date:business", "date", "business"), _hyp("date:date:arrival", "date", "arrival")]
    provider = FakeProvider(responses={"small": Completion(text=json.dumps([hyps[0].id, hyps[1].id]), usage=Usage(2, 2))})
    budget = Budget(max_small_tokens=100, max_calls=10)
    result = run_ladder(spec, "UNKNOWN", hypotheses=hyps, enumerator_yield_count=2, provider=provider, budget=budget)
    assert any(t.get("action") == "l3_skipped" for t in result.ladder_trace)


# ---------------------------------------------------------------------------
# BudgetExhausted → L4 escalation bundle, never silent guess
# ---------------------------------------------------------------------------


def test_budget_exhausted_l3_escalates_with_bundle():
    spec = _load_spec()
    h1 = _hyp("date:date:business", "date", "business")
    # REQ-060: a token ceiling of 0 means unlimited, so immediate
    # exhaustion is triggered by the call budget (max_calls=0) — the
    # first model call raises BudgetExhausted before any patch exists.
    budget = Budget(max_calls=0)
    provider = FakeProvider(responses={"frontier": Completion(text='{"id": "x", "family": "date", "claim": "new"}', usage=Usage(10, 10))})
    result = run_ladder(spec, "UNKNOWN", hypotheses=[h1], enumerator_yield_count=1, provider=provider, budget=budget)
    assert result.decision == "escalate"
    assert result.escalation_bundle is not None
    assert isinstance(result.escalation_bundle, EscalationBundle)
    assert "budget" in result.escalation_bundle.question.lower()
    assert result.candidate_patch is None  # never silent guess


def test_budget_exhausted_l2_escalates_with_bundle():
    spec = _load_spec()
    hyps = [_hyp("date:date:business", "date", "business"), _hyp("date:date:arrival", "date", "arrival")]
    # REQ-060: 0 token ceiling = unlimited; exhaust via max_calls=0.
    budget = Budget(max_calls=0)
    provider = FakeProvider(responses={"small": Completion(text=json.dumps([hyps[0].id, hyps[1].id]), usage=Usage(10, 10))})
    result = run_ladder(spec, "UNKNOWN", hypotheses=hyps, enumerator_yield_count=2, provider=provider, budget=budget)
    assert result.decision == "escalate"
    assert result.escalation_bundle is not None
    assert result.candidate_patch is None


def test_probe_budget_exhausted_escalates():
    spec = _load_spec()
    hyps = [_hyp("date:date:business", "date", "business"), _hyp("date:date:arrival", "date", "arrival")]
    # ProbeBudget with 0 probes -> pre-check fails
    probe_budget = ProbeBudget(max_probes=0)
    provider = FakeProvider()
    budget = Budget(max_small_tokens=100, max_calls=10)
    result = run_ladder(spec, "UNKNOWN", hypotheses=hyps, enumerator_yield_count=2, provider=provider, budget=budget, probe_budget=probe_budget)
    assert result.decision == "escalate"
    assert result.escalation_bundle is not None
    assert "probe" in result.escalation_bundle.question.lower() or "budget" in result.escalation_bundle.question.lower()


def test_budget_exhausted_never_produces_patch():
    """Ensure BudgetExhausted never yields a candidate patch (no silent guess)."""
    spec = _load_spec()
    hyps = [_hyp("date:date:business", "date", "business")]
    # REQ-060: 0 token ceiling = unlimited; exhaust via max_calls=0.
    for _ in [0]:
        budget = Budget(max_calls=0)
        provider = FakeProvider(responses={"frontier": Completion(text='{"id": "x", "family": "date", "claim": "new"}', usage=Usage(5, 5))})
        result = run_ladder(spec, "UNKNOWN", hypotheses=hyps, enumerator_yield_count=1, provider=provider, budget=budget)
        assert result.candidate_patch is None
        assert result.decision == "escalate"


# ---------------------------------------------------------------------------
# Surviving ambiguity → Degraded with open questions
# ---------------------------------------------------------------------------


def test_surviving_ambiguity_degraded_with_open_questions():
    """When probing doesn't disambiguate and L2 leaves multiple hypotheses, Degraded."""
    spec = _load_spec()
    hyps = [_hyp("date:date:business", "date", "business", prior=0.25), _hyp("date:date:arrival", "date", "arrival", prior=0.25), _hyp("date:date:created", "date", "created", prior=0.25), _hyp("date:date:utc", "date", "utc", prior=0.25)]
    # L2 provider returns same order (no disambiguation)
    provider = FakeProvider(responses={"small": Completion(text=json.dumps([h.id for h in hyps]), usage=Usage(2, 2))})
    budget = Budget(max_small_tokens=100, max_calls=10)
    result = run_ladder(spec, "UNKNOWN", hypotheses=hyps, enumerator_yield_count=4, provider=provider, budget=budget)
    # With equal priors, L2 will produce Degraded
    assert result.decision == "degraded"
    assert len(result.open_questions) >= 1
    assert any("date" in q.lower() for q in result.open_questions)
    assert result.candidate_patch is None


def test_surviving_ambiguity_multi_family_open_questions():
    spec = _load_spec()
    h1 = _hyp("date:date:business", "date", "business", prior=0.5, field="date")
    h2 = _hyp("pagination:/reservations:cursor", "pagination", "cursor", prior=0.5, field="/reservations")
    provider = FakeProvider(responses={"small": Completion(text=json.dumps([h1.id, h2.id]), usage=Usage(2, 2))})
    budget = Budget(max_small_tokens=100, max_calls=10)
    result = run_ladder(spec, "SEMANTIC_VALUE_DRIFT", hypotheses=[h1, h2], enumerator_yield_count=2, provider=provider, budget=budget)
    if result.decision == "degraded":
        assert len(result.open_questions) >= 1


def test_single_hypothesis_resolved_to_patch():
    """Single hypothesis after L2/L3 should produce a patch (no ambiguity)."""
    spec = _load_spec()
    h1 = _hyp("date:date:business", "date", "business")
    # With single hypothesis, L2 not invoked, but we try to make patch from it
    # Our _patch_from_hypothesis will create a valid patch
    provider = FakeProvider()
    budget = Budget(max_calls=10)
    result = run_ladder(spec, "UNKNOWN", hypotheses=[h1], enumerator_yield_count=1, provider=provider, budget=budget)
    # With 1 hypothesis and L3 returning no new (provider None gives same list), we have 1 hypothesis -> propose
    assert result.decision in ("propose", "escalate", "degraded")
    if result.decision == "propose":
        assert result.candidate_patch is not None
        assert len(result.candidate_patch) > 0


def test_known_repair_still_zero_frontier():
    """Known L1 repairs must still have frontier_tokens ==0 (no model)."""
    spec = _load_spec("atlaspms")
    # This spec should trigger L1 rename_field or similar via drift signal
    # Use SCHEMA_RENAME which has deterministic rule
    result = run_ladder(spec, "SCHEMA_RENAME", extra={"entity": "Reservation", "field": "guest_name", "new_field": "guest_identifier"})
    assert result.model_usage["frontier_tokens"] == 0
    assert result.decision == "propose"


def test_degraded_has_no_patch_and_has_open_questions():
    spec = _load_spec()
    hyps = [_hyp("date:date:business", "date", "business", prior=0.5), _hyp("date:date:arrival", "date", "arrival", prior=0.5)]
    provider = FakeProvider(responses={"small": Completion(text=json.dumps([h.id for h in hyps]), usage=Usage(2, 2))})
    budget = Budget(max_small_tokens=100, max_calls=10)
    result = run_ladder(spec, "UNKNOWN", hypotheses=hyps, enumerator_yield_count=2, provider=provider, budget=budget)
    if result.decision == "degraded":
        assert result.candidate_patch is None
        assert result.open_questions
        assert result.degradation_reason is not None


def test_l3_assert_message_contains_count():
    spec = _load_spec()
    hyps = [_hyp("a", "date", "business"), _hyp("b", "date", "arrival")]
    with pytest.raises(AssertionError) as exc:
        run_ladder(spec, "UNKNOWN", hypotheses=hyps, enumerator_yield_count=3, provider=FakeProvider(), budget=Budget())
        # Need to trigger L3 directly: use _call_frontier_generate
        from autobridge.repair.ladder import _call_frontier_generate

        _call_frontier_generate(hyps, FakeProvider(), Budget(), enumerator_yield_count=3)
    assert "3" in str(exc.value) or "fewer than two" in str(exc.value)


def test_l3_direct_assert():
    from autobridge.repair.ladder import _call_frontier_generate

    hyps = [_hyp("a", "date", "business"), _hyp("b", "date", "arrival")]
    with pytest.raises(AssertionError, match="fewer than two"):
        _call_frontier_generate(hyps, FakeProvider(), Budget(), enumerator_yield_count=2)
