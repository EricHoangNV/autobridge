"""P1.W5.5: escalation bundle and ambiguity helpers."""

from autobridge.llm.provider import BudgetExhausted
from autobridge.repair.escalation import (
    AmbiguityResult,
    EscalationBundle,
    degraded_from_ambiguity,
    escalation_for_no_fix,
    escalation_from_budget_exhausted,
)


def _prov(source="docs", line=1, snippet="note"):
    from autobridge.discovery.docs_reader import Provenance

    return Provenance(source=source, line=line, snippet=snippet)


def _hyp(id="date:date:business", family="date", strategy="business", prior=0.25):
    from autobridge.discovery.hypotheses import Hypothesis

    return Hypothesis(
        id=id,
        family=family,
        claim=f"claim {id}",
        candidate_mapping_fragment={"family": family, "field": "date", "strategy": strategy, "transform": {"name": "business_date"}},
        prior=prior,
        evidence_for=(_prov(),),
        field="date",
        strategy=strategy,
    )


def test_escalation_bundle_fields():
    b = EscalationBundle(reason="test", question="q?", evidence_ids=("a",), suggested_action="manual")
    assert b.reason == "test"
    assert b.question == "q?"
    assert b.evidence_ids == ("a",)


def test_escalation_from_budget_exhausted_probe():
    exc = BudgetExhausted("max_probes 5 exceeded")
    bundle = escalation_from_budget_exhausted(exc, evidence_ids=["e1"], context="probe")
    assert "probe" in bundle.question.lower() or "budget" in bundle.question.lower()
    assert bundle.reason == "max_probes 5 exceeded"
    assert bundle.evidence_ids == ("e1",)


def test_escalation_from_budget_exhausted_token():
    exc = BudgetExhausted("max_frontier_tokens 10 exceeded")
    bundle = escalation_from_budget_exhausted(exc, evidence_ids=[], context="L3 drift")
    assert "token" in bundle.question.lower() or "budget" in bundle.question.lower()
    assert "frontier" in bundle.reason or "max_frontier" in bundle.reason


def test_escalation_from_budget_exhausted_empty():
    exc = BudgetExhausted("max_calls 3 exceeded")
    bundle = escalation_from_budget_exhausted(exc)
    assert bundle.reason == "max_calls 3 exceeded"
    assert "budget" in bundle.question.lower()


def test_escalation_for_no_fix():
    b = escalation_for_no_fix("No L1 rule for UNKNOWN", evidence_ids=["e1"])
    assert "UNKNOWN" in b.reason
    assert b.evidence_ids == ("e1",)
    assert "human" in b.question.lower() or "review" in b.question.lower()


def test_escalation_for_no_fix_custom_question():
    b = escalation_for_no_fix("x", question="What is correct?")
    assert b.question == "What is correct?"


def test_degraded_from_ambiguity_single_family():
    h1 = _hyp(id="date:date:business", strategy="business")
    h2 = _hyp(id="date:date:arrival", strategy="arrival")
    amb = degraded_from_ambiguity([h1, h2], reason="probing exhausted")
    assert isinstance(amb, AmbiguityResult)
    assert len(amb.open_questions) == 1
    assert "date" in amb.open_questions[0].lower()
    assert h1.id in amb.surviving_hypothesis_ids
    assert h2.id in amb.surviving_hypothesis_ids


def test_degraded_from_ambiguity_multi_family():
    h1 = _hyp(id="date:date:business", family="date", strategy="business")
    _hyp(id="pagination:/reservations:cursor", family="pagination", strategy="cursor")
    # need valid hypothesis for pagination family
    from autobridge.discovery.docs_reader import Provenance

    prov = Provenance(source="docs", line=1, snippet="pagination")
    from autobridge.discovery.hypotheses import Hypothesis

    h2b = Hypothesis(
        id="pagination:/reservations:cursor",
        family="pagination",
        claim="pagination cursor",
        candidate_mapping_fragment={"family": "pagination", "strategy": "cursor", "operation": "/reservations", "pagination": {"strategy": "cursor"}},
        prior=0.33,
        evidence_for=(prov,),
        field="/reservations",
        strategy="cursor",
    )
    amb = degraded_from_ambiguity([h1, h2b])
    assert len(amb.open_questions) == 2
    # sorted by family
    assert amb.open_questions[0].startswith("Ambiguous date") or amb.open_questions[0].startswith("Ambiguous pagination")


def test_degraded_from_ambiguity_empty():
    amb = degraded_from_ambiguity([], reason="none")
    assert len(amb.open_questions) == 1
    assert "ambiguous" in amb.open_questions[0].lower()


def test_degraded_surviving_ids():
    h1 = _hyp(id="h1", family="date", strategy="business")
    h2 = _hyp(id="h2", family="date", strategy="arrival")
    amb = degraded_from_ambiguity([h1, h2])
    assert set(amb.surviving_hypothesis_ids) == {"h1", "h2"}
    assert amb.reason != ""
