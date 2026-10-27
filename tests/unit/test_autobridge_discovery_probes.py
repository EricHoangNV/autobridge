"""Tests for discovery/probes.py (P1.W5.4)."""

from __future__ import annotations

import time
from typing import Any

import pytest

from autobridge.discovery.classifiers import classify_all
from autobridge.discovery.docs_reader import (
    InterfaceDescription,
    OperationDescription,
    Provenance,
    SchemaDescription,
    read_docs_file,
)
from autobridge.discovery.hypotheses import Hypothesis, HypothesisEnumerator
from autobridge.discovery.probes import (
    ProbeBudget,
    ProbeSelector,
    ProbeSpec,
    _candidates_from_interface,
    _entropy,
    _predicted_observation,
    expected_information_gain,
)
from autobridge.llm.provider import Budget, BudgetExhausted


def _prov(source: str = "docs", line: int = 1, snippet: str = "snippet") -> Provenance:
    return Provenance(source=source, line=line, snippet=snippet)


def _iface_minimal() -> InterfaceDescription:
    return InterfaceDescription(source="test.md", doc_type="markdown")


def _make_op(path: str, method: str = "GET", hint: str = "none") -> OperationDescription:
    return OperationDescription(
        operation_id=f"get_{path}",
        method=method,
        path=path,
        params=[],
        pagination_hint=hint,
        provenance=_prov(source="test.md", line=10, snippet=path),
    )


# ---------------------------------------------------------------------------
# ProbeSpec
# ---------------------------------------------------------------------------


def test_probe_spec_requires_leading_slash() -> None:
    with pytest.raises(ValueError, match="must start with /"):
        ProbeSpec(operation="reservations")


def test_probe_spec_requires_get() -> None:
    with pytest.raises(ValueError, match="only allows GET"):
        ProbeSpec(operation="/reservations", method="POST")


def test_probe_spec_get_case_insensitive() -> None:
    p = ProbeSpec(operation="/reservations", method="get")
    assert p.method == "get"
    assert p.operation == "/reservations"


def test_probe_spec_key_empty_params() -> None:
    p = ProbeSpec(operation="/reservations")
    assert p.key() == "/reservations"


def test_probe_spec_key_with_params_sorted() -> None:
    p = ProbeSpec(operation="/reservations", params={"b": "2", "a": "1"})
    assert p.key() == "/reservations?a=1&b=2"


def test_probe_spec_to_kwargs() -> None:
    p = ProbeSpec(operation="/folios", params={"limit": 10})
    kwargs = p.to_probe_observation_kwargs()
    assert kwargs["operation"] == "/folios"
    assert kwargs["params"] == {"limit": 10}


def test_probe_spec_description_and_provenance() -> None:
    prov = _prov(source="a.md", line=5)
    p = ProbeSpec(operation="/x", description="desc", provenance=prov)
    assert p.description == "desc"
    assert p.provenance == prov


# ---------------------------------------------------------------------------
# ProbeBudget
# ---------------------------------------------------------------------------


def test_probe_budget_unlimited_default() -> None:
    b = ProbeBudget()
    b.check_pre_probe()
    b.record_probe()
    assert b.probes_used == 1
    assert b.requests_used == 1


def test_probe_budget_max_probes_exceeded_pre_check() -> None:
    b = ProbeBudget(max_probes=1)
    b.probes_used = 1
    with pytest.raises(BudgetExhausted, match="max_probes"):
        b.check_pre_probe()


def test_probe_budget_max_probes_exceeded_on_record() -> None:
    b = ProbeBudget(max_probes=1)
    b.record_probe()
    assert b.probes_used == 1
    with pytest.raises(BudgetExhausted, match="max_probes"):
        b.record_probe()


def test_probe_budget_max_requests_exceeded_pre_check() -> None:
    b = ProbeBudget(max_requests=1)
    b.requests_used = 1
    with pytest.raises(BudgetExhausted, match="max_requests"):
        b.check_pre_probe()


def test_probe_budget_max_requests_exceeded_on_record() -> None:
    b = ProbeBudget(max_requests=1)
    b.record_probe(requests=1)
    with pytest.raises(BudgetExhausted, match="max_requests"):
        b.record_probe(requests=1)


def test_probe_budget_max_tokens_pre_check() -> None:
    b = ProbeBudget(max_tokens=10)
    b.tokens_used = 10
    with pytest.raises(BudgetExhausted, match="max_tokens"):
        b.check_pre_probe()


def test_probe_budget_max_tokens_on_record_probe() -> None:
    b = ProbeBudget(max_tokens=5)
    with pytest.raises(BudgetExhausted, match="max_tokens"):
        b.record_probe(tokens=6)


def test_probe_budget_record_tokens() -> None:
    b = ProbeBudget(max_tokens=10)
    b.record_tokens(5)
    assert b.tokens_used == 5
    b.record_tokens(4)
    assert b.tokens_used == 9
    with pytest.raises(BudgetExhausted, match="max_tokens"):
        b.record_tokens(2)


def test_probe_budget_record_tokens_zero_noop() -> None:
    b = ProbeBudget(max_tokens=1)
    b.record_tokens(0)
    assert b.tokens_used == 0
    b.record_tokens(-1)
    assert b.tokens_used == 0


def test_probe_budget_wall_exceeded() -> None:
    b = ProbeBudget(max_wall=0.01, wall_start=time.monotonic() - 0.02)
    with pytest.raises(BudgetExhausted, match="max_wall"):
        b.check_pre_probe()
    # also on record
    b2 = ProbeBudget(max_wall=0.01, wall_start=time.monotonic() - 0.02)
    with pytest.raises(BudgetExhausted, match="max_wall"):
        b2.record_probe()


def test_probe_budget_wall_not_exceeded() -> None:
    b = ProbeBudget(max_wall=10)
    b.check_pre_probe()
    b.record_probe()
    assert b.probes_used == 1


def test_probe_budget_record_request() -> None:
    b = ProbeBudget(max_requests=2)
    b.record_request()
    assert b.requests_used == 1
    b.record_request()
    assert b.requests_used == 2
    with pytest.raises(BudgetExhausted, match="max_requests"):
        b.record_request()


def test_probe_budget_record_request_unlimited() -> None:
    b = ProbeBudget()
    b.record_request()
    assert b.requests_used == 1


def test_probe_budget_remaining() -> None:
    b = ProbeBudget(max_probes=5, max_requests=10, max_tokens=100, max_wall=60)
    b.probes_used = 2
    b.requests_used = 3
    b.tokens_used = 50
    rem = b.remaining()
    assert rem["probes"] == 3
    assert rem["requests"] == 7
    assert rem["tokens"] == 50
    assert rem["wall"] is not None
    assert rem["wall"] > 0


def test_probe_budget_remaining_unlimited() -> None:
    b = ProbeBudget()
    rem = b.remaining()
    assert rem["probes"] is None
    assert rem["requests"] is None
    assert rem["tokens"] is None
    assert rem["wall"] is None


def test_probe_budget_max_wall_seconds_alias() -> None:
    b = ProbeBudget(max_wall=30)
    assert b.max_wall_seconds == 30
    b2 = ProbeBudget()
    assert b2.max_wall_seconds is None


def test_probe_budget_record_probe_with_zero_requests() -> None:
    b = ProbeBudget(max_probes=5, max_requests=5)
    b.record_probe(requests=0)
    assert b.probes_used == 1
    assert b.requests_used == 0


# ---------------------------------------------------------------------------
# Candidates enumeration
# ---------------------------------------------------------------------------


def test_candidates_from_interface_get_only() -> None:
    iface = _iface_minimal()
    iface.operations = [
        _make_op("/reservations", method="GET"),
        _make_op("/folios", method="POST"),
        _make_op("/properties", method="GET"),
    ]
    cands = _candidates_from_interface(iface)
    ops = {c.operation for c in cands}
    assert ops == {"/reservations", "/properties"}
    # sorted
    assert [c.operation for c in cands] == sorted(ops)


def test_candidates_from_interface_dedup() -> None:
    iface = _iface_minimal()
    iface.operations = [
        _make_op("/reservations", method="GET"),
        _make_op("/reservations", method="GET"),
    ]
    cands = _candidates_from_interface(iface)
    assert len(cands) == 1


def test_candidates_from_interface_no_ops() -> None:
    iface = _iface_minimal()
    cands = _candidates_from_interface(iface)
    assert cands == []


def test_candidates_from_interface_normalizes_path() -> None:
    iface = _iface_minimal()
    iface.operations = [
        OperationDescription(operation_id="x", method="GET", path="reservations", provenance=_prov()),
    ]
    cands = _candidates_from_interface(iface)
    assert cands[0].operation == "/reservations"


def test_candidates_from_interface_description_includes_summary() -> None:
    iface = _iface_minimal()
    iface.operations = [
        OperationDescription(operation_id="x", method="GET", path="/a", summary="list things", provenance=_prov()),
    ]
    cands = _candidates_from_interface(iface)
    assert "list things" in cands[0].description


# ---------------------------------------------------------------------------
# Entropy + scoring
# ---------------------------------------------------------------------------


def test_entropy_single_prior_zero() -> None:
    assert _entropy([1.0]) == 0.0


def test_entropy_empty_zero() -> None:
    assert _entropy([]) == 0.0


def test_entropy_uniform_two() -> None:
    # uniform 2 values => 1 bit
    assert _entropy([0.5, 0.5]) == pytest.approx(1.0)


def test_entropy_uniform_four() -> None:
    assert _entropy([0.25, 0.25, 0.25, 0.25]) == pytest.approx(2.0)


def test_entropy_non_normalized() -> None:
    # not summing to 1, should normalize internally
    assert _entropy([1, 1]) == pytest.approx(1.0)


def test_entropy_zero_prior_filtered() -> None:
    assert _entropy([0.0, 0.5, 0.5]) == pytest.approx(1.0)


def test_predicted_observation_date_relevant() -> None:
    iface = _iface_minimal()
    iface.date_notes = [("date ambiguous", _prov())]
    enum = HypothesisEnumerator()
    hyps = enum.enumerate_date(iface, None)
    probe = ProbeSpec(operation="/reservations")
    for h in hyps:
        pred = _predicted_observation(h, probe)
        assert pred == h.strategy


def test_predicted_observation_date_irrelevant_properties() -> None:
    iface = _iface_minimal()
    iface.date_notes = [("date ambiguous", _prov())]
    enum = HypothesisEnumerator()
    hyps = enum.enumerate_date(iface, None)
    probe = ProbeSpec(operation="/properties")
    for h in hyps:
        assert _predicted_observation(h, probe) is None


def test_predicted_observation_pagination_relevant() -> None:
    iface = _iface_minimal()
    iface.operations = [_make_op("/reservations", hint="cursor")]
    enum = HypothesisEnumerator()
    hyps = enum.enumerate_pagination(iface, None)
    # hyps are for /reservations with 3 strategies
    probe = ProbeSpec(operation="/reservations")
    for h in hyps:
        pred = _predicted_observation(h, probe)
        assert pred == h.strategy
    # different op should be irrelevant
    probe2 = ProbeSpec(operation="/folios")
    for h in hyps:
        assert _predicted_observation(h, probe2) is None


def test_predicted_observation_amount_relevant() -> None:
    iface = _iface_minimal()
    iface.schemas = [SchemaDescription(name="Folio", fields=[], provenance=_prov())]
    enum = HypothesisEnumerator()
    hyps = enum.enumerate_amount(iface, None)
    probe = ProbeSpec(operation="/folios")
    for h in hyps:
        assert _predicted_observation(h, probe) == h.strategy
    probe2 = ProbeSpec(operation="/properties")
    for h in hyps:
        assert _predicted_observation(h, probe2) is None


def test_predicted_observation_status_relevant() -> None:
    iface = _iface_minimal()
    iface.status_notes = [("status H1", _prov())]
    enum = HypothesisEnumerator()
    hyps = enum.enumerate_status(iface, None)
    probe = ProbeSpec(operation="/reservations")
    for h in hyps:
        assert _predicted_observation(h, probe) == h.strategy
    probe2 = ProbeSpec(operation="/properties")
    for h in hyps:
        assert _predicted_observation(h, probe2) is None


def test_predicted_observation_unknown_family() -> None:
    # hypothesis with bogus family should return None
    # Use valid family but probe irrelevant path: already tested via date/properties
    # Also test with empty strategy
    h = Hypothesis(
        id="date:date:created",
        family="date",
        claim="c",
        candidate_mapping_fragment={"a": 1},
        prior=0.5,
        evidence_for=(_prov(),),
        field="date",
        strategy=None,
    )
    probe = ProbeSpec(operation="/reservations")
    # strategy None -> predicted "unknown" bucket
    assert _predicted_observation(h, probe) == "unknown"


def test_expected_information_gain_empty_hypotheses() -> None:
    probe = ProbeSpec(operation="/reservations")
    assert expected_information_gain(probe, []) == 0.0


def test_expected_information_gain_single_hypothesis_zero() -> None:
    iface = _iface_minimal()
    hyps = HypothesisEnumerator().enumerate_date(iface, None)[:1]
    probe = ProbeSpec(operation="/reservations")
    assert expected_information_gain(probe, hyps) == 0.0


def test_expected_information_gain_date_probe_positive() -> None:
    iface = _iface_minimal()
    iface.date_notes = [("date ambiguous", _prov())]
    hyps = HypothesisEnumerator().enumerate_date(iface, None)
    probe = ProbeSpec(operation="/reservations")
    gain = expected_information_gain(probe, hyps)
    # 4 uniform hypotheses perfectly split into 4 groups => gain = 2 bits, H_before=2, H_after=0
    assert gain == pytest.approx(2.0)


def test_expected_information_gain_irrelevant_probe_zero() -> None:
    iface = _iface_minimal()
    iface.date_notes = [("date ambiguous", _prov())]
    hyps = HypothesisEnumerator().enumerate_date(iface, None)
    probe = ProbeSpec(operation="/properties")
    gain = expected_information_gain(probe, hyps)
    assert gain == pytest.approx(0.0)


def test_expected_information_gain_pagination() -> None:
    iface = _iface_minimal()
    iface.operations = [_make_op("/reservations", hint="cursor")]
    hyps = HypothesisEnumerator().enumerate_pagination(iface, None)
    probe = ProbeSpec(operation="/reservations")
    gain = expected_information_gain(probe, hyps)
    # 3 uniform => H_before = log2 3 ~1.585, H_after 0 => gain = 1.585
    assert gain == pytest.approx(1.58496, rel=1e-4)
    probe2 = ProbeSpec(operation="/folios")
    gain2 = expected_information_gain(probe2, hyps)
    assert gain2 == pytest.approx(0.0)


def test_expected_information_gain_mixed_families() -> None:
    iface = read_docs_file("simulators/docs/harborpms.md")
    cls = classify_all(iface)
    hyps = HypothesisEnumerator().enumerate_all(iface, cls)
    # /reservations should have higher gain than /properties for mixed set
    p_res = ProbeSpec(operation="/reservations")
    p_props = ProbeSpec(operation="/properties")
    gain_res = expected_information_gain(p_res, hyps)
    gain_props = expected_information_gain(p_props, hyps)
    assert gain_res > gain_props
    assert gain_res > 0


# ---------------------------------------------------------------------------
# ProbeSelector
# ---------------------------------------------------------------------------


def test_selector_candidates_get_only() -> None:
    iface = _iface_minimal()
    iface.operations = [_make_op("/a", method="GET"), _make_op("/b", method="POST")]
    sel = ProbeSelector(iface)
    cands = sel.candidates()
    assert len(cands) == 1
    assert cands[0].operation == "/a"


def test_selector_candidates_sorted() -> None:
    iface = _iface_minimal()
    iface.operations = [_make_op("/z"), _make_op("/a")]
    sel = ProbeSelector(iface)
    ops = [c.operation for c in sel.candidates()]
    assert ops == ["/a", "/z"]


def test_selector_score_delegates() -> None:
    iface = _iface_minimal()
    iface.date_notes = [("date ambiguous", _prov())]
    hyps = HypothesisEnumerator().enumerate_date(iface, None)
    sel = ProbeSelector(iface)
    probe = ProbeSpec(operation="/reservations")
    assert sel.score(probe, hyps) == expected_information_gain(probe, hyps)


def test_selector_select_next_best_splits() -> None:
    iface = _iface_minimal()
    iface.operations = [_make_op("/reservations"), _make_op("/properties")]
    iface.date_notes = [("date ambiguous", _prov())]
    hyps = HypothesisEnumerator().enumerate_date(iface, None)
    sel = ProbeSelector(iface)
    best = sel.select_next(hyps)
    assert best is not None
    assert best.operation == "/reservations"


def test_selector_select_next_empty_hypotheses_none() -> None:
    iface = _iface_minimal()
    iface.operations = [_make_op("/a")]
    sel = ProbeSelector(iface)
    assert sel.select_next([]) is None


def test_selector_select_next_no_candidates_none() -> None:
    iface = _iface_minimal()
    iface.operations = []
    sel = ProbeSelector(iface)
    iface2 = _iface_minimal()
    hyps = HypothesisEnumerator().enumerate_date(iface2, None)
    assert sel.select_next(hyps) is None


def test_selector_select_next_respects_attempted_operation() -> None:
    iface = _iface_minimal()
    iface.operations = [_make_op("/reservations"), _make_op("/properties")]
    iface.date_notes = [("date ambiguous", _prov())]
    hyps = HypothesisEnumerator().enumerate_date(iface, None)
    sel = ProbeSelector(iface)
    best = sel.select_next(hyps, attempted={"/reservations"})
    assert best is not None
    assert best.operation == "/properties"


def test_selector_select_next_respects_attempted_key_with_params() -> None:
    iface = _iface_minimal()
    iface.operations = [_make_op("/reservations"), _make_op("/properties")]
    iface.date_notes = [("date ambiguous", _prov())]
    hyps = HypothesisEnumerator().enumerate_date(iface, None)
    sel = ProbeSelector(iface)
    # attempted set contains full key with params (probe spec with params)
    probe_with_params = ProbeSpec(operation="/reservations", params={"a": "1"})
    # For our candidates, key is "/reservations" (no params), so attempted key with params
    # should not match unless we use operation; but we test that bare operation also works
    best = sel.select_next(hyps, attempted={probe_with_params.key()})
    # key is "/reservations?a=1" not in candidates, so best still /reservations
    assert best is not None
    assert best.operation == "/reservations"
    # Now mark bare operation as attempted, should skip
    best2 = sel.select_next(hyps, attempted={"/reservations"})
    assert best2.operation == "/properties"


def test_selector_select_next_all_attempted_none() -> None:
    iface = _iface_minimal()
    iface.operations = [_make_op("/a")]
    sel = ProbeSelector(iface)
    hyps = HypothesisEnumerator().enumerate_date(iface, None)
    assert sel.select_next(hyps, attempted={"/a"}) is None


def test_selector_select_next_tie_break_smallest_op() -> None:
    # When two probes have equal gain (both irrelevant => 0), tie broken by smallest op
    iface = _iface_minimal()
    iface.operations = [_make_op("/b"), _make_op("/a")]
    # Use pagination hypotheses for /x which is irrelevant to both, gain 0 for both
    iface2 = _iface_minimal()
    iface2.operations = [_make_op("/x", hint="cursor")]
    hyps = HypothesisEnumerator().enumerate_pagination(iface2, None)  # for /x
    sel = ProbeSelector(iface)
    best = sel.select_next(hyps)
    # both gain 0, smallest op /a should win
    assert best.operation == "/a"


def test_selector_budget_pre_check_raises() -> None:
    iface = _iface_minimal()
    iface.operations = [_make_op("/a")]
    iface.date_notes = [("date ambiguous", _prov())]
    hyps = HypothesisEnumerator().enumerate_date(iface, None)
    budget = ProbeBudget(max_probes=0)
    sel = ProbeSelector(iface, budget=budget)
    with pytest.raises(BudgetExhausted, match="max_probes"):
        sel.select_next(hyps)


def test_selector_budget_wall_pre_check() -> None:
    iface = _iface_minimal()
    iface.operations = [_make_op("/a")]
    hyps = HypothesisEnumerator().enumerate_date(iface, None)
    budget = ProbeBudget(max_wall=0.01, wall_start=time.monotonic() - 0.05)
    sel = ProbeSelector(iface, budget=budget)
    with pytest.raises(BudgetExhausted, match="max_wall"):
        sel.select_next(hyps)


def test_selector_budget_llm_budget_pre_check() -> None:
    iface = _iface_minimal()
    iface.operations = [_make_op("/a")]
    hyps = HypothesisEnumerator().enumerate_date(iface, None)
    llm_b = Budget(max_calls=0)
    sel = ProbeSelector(iface, llm_budget=llm_b)
    with pytest.raises(BudgetExhausted, match="max_calls"):
        sel.select_next(hyps)


def test_selector_budget_translation_from_llm_budget() -> None:
    iface = _iface_minimal()
    iface.operations = [_make_op("/a"), _make_op("/b")]
    hyps = HypothesisEnumerator().enumerate_date(iface, None)
    llm_b = Budget(max_calls=1, max_frontier_tokens=10, max_small_tokens=10)
    # llm_b.calls 0, so selector should allow one probe, then second should fail after record
    sel = ProbeSelector(iface, budget=llm_b)
    assert sel.budget is not None
    assert sel.budget.max_probes == 1
    assert sel.budget.max_tokens == 20
    best = sel.select_next(hyps)
    assert best is not None
    # record one probe, now budget exhausted
    sel.record_probe_result()
    assert sel.budget.probes_used == 1
    # original budget synced?
    assert llm_b.calls == 1
    with pytest.raises(BudgetExhausted):
        sel.select_next(hyps)


def test_selector_rank_probes_ordered() -> None:
    iface = _iface_minimal()
    iface.operations = [_make_op("/reservations"), _make_op("/properties"), _make_op("/folios")]
    iface.date_notes = [("date ambiguous", _prov())]
    hyps = HypothesisEnumerator().enumerate_date(iface, None)
    sel = ProbeSelector(iface)
    ranked = sel.rank_probes(hyps)
    assert len(ranked) == 3
    # /reservations and /folios both relevant (high gain), /properties low
    # Check ordering descending gain
    gains = [g for _, g in ranked]
    assert gains == sorted(gains, reverse=True)
    # top should be reservations or folios (lexicographically smaller among ties is /folios? Let's check)
    # Both have equal gain 2 bits, tie broken by op ascending, so /folios before /reservations
    assert ranked[0][0].operation == "/folios"
    assert ranked[1][0].operation == "/reservations"
    assert ranked[2][0].operation == "/properties"


def test_selector_rank_probes_respects_attempted() -> None:
    iface = _iface_minimal()
    iface.operations = [_make_op("/a"), _make_op("/b")]
    hyps = HypothesisEnumerator().enumerate_date(iface, None)
    sel = ProbeSelector(iface)
    ranked = sel.rank_probes(hyps, attempted={"/a"})
    assert len(ranked) == 1
    assert ranked[0][0].operation == "/b"


def test_selector_best_probe_alias() -> None:
    iface = _iface_minimal()
    iface.operations = [_make_op("/a")]
    hyps = HypothesisEnumerator().enumerate_date(iface, None)
    sel = ProbeSelector(iface)
    assert sel.best_probe(hyps) == sel.select_next(hyps)


def test_selector_record_probe_result_no_budget_noop() -> None:
    iface = _iface_minimal()
    iface.operations = [_make_op("/a")]
    sel = ProbeSelector(iface, budget=None)
    sel.record_probe_result(requests=1, tokens=5)
    # should not raise


def test_selector_record_probe_result_updates_budget() -> None:
    iface = _iface_minimal()
    iface.operations = [_make_op("/a")]
    budget = ProbeBudget(max_probes=5, max_requests=5, max_tokens=10)
    sel = ProbeSelector(iface, budget=budget)
    sel.record_probe_result(requests=2, tokens=3)
    assert sel.budget.probes_used == 1
    assert sel.budget.requests_used == 2
    assert sel.budget.tokens_used == 3


def test_selector_remaining_budget() -> None:
    iface = _iface_minimal()
    sel = ProbeSelector(iface, budget=None)
    assert sel.remaining_budget() is None
    budget = ProbeBudget(max_probes=5)
    sel2 = ProbeSelector(iface, budget=budget)
    assert sel2.remaining_budget() is not None
    assert sel2.remaining_budget()["probes"] == 5


def test_selector_with_llm_budget_record_tokens() -> None:
    iface = _iface_minimal()
    iface.operations = [_make_op("/a")]
    # fake llm budget that records tokens
    class FakeLLMBudget:
        def __init__(self) -> None:
            self.record_calls: list[tuple[str, Any]] = []

        def check_pre_call(self) -> None:
            pass

        def record(self, tier: str, usage: Any) -> None:
            self.record_calls.append((tier, usage))

    fake = FakeLLMBudget()
    budget = ProbeBudget(max_probes=10)
    sel = ProbeSelector(iface, budget=budget, llm_budget=fake)
    sel.record_probe_result(requests=1, tokens=7)
    assert fake.record_calls[0][0] == "small"
    assert fake.record_calls[0][1].total_tokens == 7


def test_selector_record_no_llm_budget_tokens_still_updates_probe_budget() -> None:
    iface = _iface_minimal()
    iface.operations = [_make_op("/a")]
    budget = ProbeBudget(max_tokens=5)
    sel = ProbeSelector(iface, budget=budget)
    with pytest.raises(BudgetExhausted):
        sel.record_probe_result(tokens=6)


def test_selector_harbor_real_fixture_picks_best() -> None:
    iface = read_docs_file("simulators/docs/harborpms.md")
    cls = classify_all(iface)
    hyps = HypothesisEnumerator().enumerate_all(iface, cls)
    sel = ProbeSelector(iface, budget=ProbeBudget(max_probes=10, max_requests=20, max_wall=30, max_tokens=1000))
    best = sel.select_next(hyps)
    assert best is not None
    # Harbor has reservations/folios as date-bearing; best should be one of them, not properties if properties irrelevant
    assert best.operation in ("/reservations", "/folios", "/reservations/{reservation_id}/folios", "/folios/{charge_id}", "/reservations/{reservation_id}")
    # Ensure best gain > worst gain
    ranked = sel.rank_probes(hyps)
    assert ranked[0][1] >= ranked[-1][1]


def test_selector_atlas_real_fixture() -> None:
    iface = read_docs_file("simulators/docs/atlaspms-openapi.yaml")
    cls = classify_all(iface)
    hyps = HypothesisEnumerator().enumerate_all(iface, cls)
    sel = ProbeSelector(iface)
    best = sel.select_next(hyps)
    assert best is not None
    assert best.method == "GET"
    assert best.operation.startswith("/")


def test_probe_spec_must_be_get_enforced_by_selector() -> None:
    iface = _iface_minimal()
    iface.operations = [
        OperationDescription(operation_id="x", method="GET", path="/a", provenance=_prov()),
        OperationDescription(operation_id="y", method="POST", path="/b", provenance=_prov()),
    ]
    sel = ProbeSelector(iface)
    assert len(sel.candidates()) == 1
    assert sel.candidates()[0].operation == "/a"


def test_entropy_clamp_tiny_negative() -> None:
    # Craft priors that could cause tiny negative due to float error: uniform 3 with extra tiny?
    # Our entropy never negative, but EIG clamps tiny negatives; test via equal gain zero case
    iface = _iface_minimal()
    iface.operations = [_make_op("/a")]
    hyps = HypothesisEnumerator().enumerate_date(iface, None)
    probe = ProbeSpec(operation="/properties")
    # gain zero, should not be negative
    assert expected_information_gain(probe, hyps) == 0.0


def test_selector_candidates_cached() -> None:
    iface = _iface_minimal()
    iface.operations = [_make_op("/a")]
    sel = ProbeSelector(iface)
    c1 = sel.candidates()
    c2 = sel.candidates()
    assert c1 == c2
    assert sel._candidates is not None  # type: ignore[attr-defined]


def test_selector_llm_budget_none_no_check() -> None:
    iface = _iface_minimal()
    iface.operations = [_make_op("/a")]
    hyps = HypothesisEnumerator().enumerate_date(iface, None)
    sel = ProbeSelector(iface, llm_budget=None)
    assert sel.select_next(hyps) is not None
