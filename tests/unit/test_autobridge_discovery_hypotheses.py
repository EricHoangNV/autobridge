"""Tests for discovery/hypotheses.py (P1.W5.3)."""

from __future__ import annotations

import pytest

from autobridge.discovery.classifiers import (
    DateFieldClassification,
    DateSemanticsClassification,
    PaginationClassification,
    PaginationGuess,
    ProbeObservation,
    StatusVocabularyClassification,
    classify_all,
)
from autobridge.discovery.docs_reader import (
    InterfaceDescription,
    OperationDescription,
    ParamDescription,
    Provenance,
    ResponseField,
    SchemaDescription,
    read_docs_file,
)
from autobridge.discovery.hypotheses import (
    AMOUNT_STRATEGIES,
    DATE_STRATEGIES,
    PAGINATION_STRATEGIES,
    Hypothesis,
    HypothesisEnumerator,
    enumerate_amount_hypotheses,
    enumerate_date_hypotheses,
    enumerate_pagination_hypotheses,
    enumerate_status_hypotheses,
)


def _prov(source: str = "docs", line: int = 1, snippet: str = "snippet") -> Provenance:
    return Provenance(source=source, line=line, snippet=snippet)


def _iface_minimal() -> InterfaceDescription:
    return InterfaceDescription(source="test.md", doc_type="markdown")


# ---------------------------------------------------------------------------
# Hypothesis model validation
# ---------------------------------------------------------------------------


def test_hypothesis_requires_evidence() -> None:
    with pytest.raises(ValueError, match="at least one evidence"):
        Hypothesis(
            id="date:date:created",
            family="date",
            claim="claim",
            candidate_mapping_fragment={"a": 1},
            prior=0.25,
            evidence_for=(),
        )


def test_hypothesis_invalid_family() -> None:
    with pytest.raises(ValueError, match="family must be"):
        Hypothesis(
            id="x",
            family="bogus",
            claim="c",
            candidate_mapping_fragment={"a": 1},
            prior=0.5,
            evidence_for=(_prov(),),
        )


def test_hypothesis_empty_id() -> None:
    with pytest.raises(ValueError, match="id must be non-empty"):
        Hypothesis(
            id="",
            family="date",
            claim="c",
            candidate_mapping_fragment={"a": 1},
            prior=0.5,
            evidence_for=(_prov(),),
        )


def test_hypothesis_empty_claim() -> None:
    with pytest.raises(ValueError, match="claim must be non-empty"):
        Hypothesis(
            id="date:date:created",
            family="date",
            claim="",
            candidate_mapping_fragment={"a": 1},
            prior=0.5,
            evidence_for=(_prov(),),
        )


def test_hypothesis_invalid_prior_low() -> None:
    with pytest.raises(ValueError, match="prior must be"):
        Hypothesis(
            id="date:date:created",
            family="date",
            claim="c",
            candidate_mapping_fragment={"a": 1},
            prior=-0.1,
            evidence_for=(_prov(),),
        )


def test_hypothesis_invalid_prior_high() -> None:
    with pytest.raises(ValueError, match="prior must be"):
        Hypothesis(
            id="date:date:created",
            family="date",
            claim="c",
            candidate_mapping_fragment={"a": 1},
            prior=1.5,
            evidence_for=(_prov(),),
        )


def test_hypothesis_empty_fragment() -> None:
    with pytest.raises(ValueError, match="candidate_mapping_fragment must be non-empty"):
        Hypothesis(
            id="date:date:created",
            family="date",
            claim="c",
            candidate_mapping_fragment={},
            prior=0.5,
            evidence_for=(_prov(),),
        )


def test_hypothesis_fragment_not_dict() -> None:
    with pytest.raises(ValueError, match="must be a dict"):
        Hypothesis(
            id="date:date:created",
            family="date",
            claim="c",
            candidate_mapping_fragment="not-a-dict",  # type: ignore[arg-type]
            prior=0.5,
            evidence_for=(_prov(),),
        )


def test_hypothesis_to_evidence_payload() -> None:
    h = Hypothesis(
        id="date:date:created",
        family="date",
        claim="date is created",
        candidate_mapping_fragment={"family": "date", "strategy": "created"},
        prior=0.25,
        evidence_for=(_prov(source="a.md", line=2, snippet="x"),),
        evidence_against=(_prov(source="b.md", line=3, snippet="y"),),
        field="date",
        strategy="created",
    )
    payload = h.to_evidence_payload()
    assert payload["hypothesis_id"] == "date:date:created"
    assert payload["family"] == "date"
    assert payload["prior"] == 0.25
    assert "a.md:2" in payload["evidence_for"][0]
    assert "b.md:3" in payload["evidence_against"][0]
    assert payload["fragment"]["strategy"] == "created"


def test_hypothesis_valid_with_min_fields() -> None:
    h = Hypothesis(
        id="status:status:clear",
        family="status",
        claim="clear",
        candidate_mapping_fragment={"family": "status"},
        prior=0.5,
        evidence_for=(_prov(),),
    )
    assert h.field is None
    assert h.strategy is None


# ---------------------------------------------------------------------------
# Date family
# ---------------------------------------------------------------------------


def test_enumerate_date_four_strategies() -> None:
    iface = _iface_minimal()
    iface.date_notes = [("field `date` duplicates arrival_date/business_date, ambiguous", _prov())]
    hyps = enumerate_date_hypotheses(iface)
    assert len(hyps) == 4
    strategies = {h.strategy for h in hyps}
    assert strategies == set(DATE_STRATEGIES)
    for h in hyps:
        assert h.family == "date"
        assert h.field == "date"
        assert h.prior == 0.25
        assert len(h.evidence_for) >= 1
        assert h.candidate_mapping_fragment["family"] == "date"
        assert "transform" in h.candidate_mapping_fragment


def test_enumerate_date_with_classification_ambiguous() -> None:
    iface = _iface_minimal()
    iface.date_notes = [("arrival_date property-local, business_date cutoff 04:00, date ambiguous", _prov(source="harbor.md", line=10))]
    iface.schemas = [
        SchemaDescription(name="Reservation", fields=[ResponseField(name="arrival_date"), ResponseField(name="date")], provenance=_prov(source="harbor.md", line=5))
    ]
    date_cls = DateSemanticsClassification(
        fields=(
            DateFieldClassification(field="date", semantics="ambiguous_date", confidence="high", evidence=(_prov(source="harbor.md", line=10),)),
            DateFieldClassification(field="arrival_date", semantics="property_local_date", confidence="high", evidence=(_prov(source="harbor.md", line=5),)),
        ),
        ambiguous_fields=("date",),
        business_date_cutoff="04:00",
        reasoning="cutoff",
    )
    hyps = enumerate_date_hypotheses(iface, date_cls)
    assert len(hyps) == 4
    assert all(h.field == "date" for h in hyps)
    # business hypothesis should have business_date transform with cutoff
    business = [h for h in hyps if h.strategy == "business"][0]
    assert business.candidate_mapping_fragment["transform"]["name"] == "business_date"
    assert business.candidate_mapping_fragment["transform"]["cutoff"] == "04:00"
    assert "business_date" in business.claim or "business" in business.claim.lower()


def test_enumerate_date_with_custom_ambiguous_field() -> None:
    iface = _iface_minimal()
    iface.date_notes = [("field `txn_date` ambiguous", _prov())]
    date_cls = DateSemanticsClassification(
        fields=(
            DateFieldClassification(field="txn_date", semantics="ambiguous_date", confidence="high", evidence=(_prov(),)),
        ),
        ambiguous_fields=("txn_date",),
        reasoning="ambig",
    )
    hyps = enumerate_date_hypotheses(iface, date_cls)
    assert all(h.field == "txn_date" for h in hyps)
    assert all("txn_date" in h.id for h in hyps)


def test_enumerate_date_fallback_provenance_when_no_notes() -> None:
    iface = _iface_minimal()
    # no date_notes, no schemas, no classification
    hyps = enumerate_date_hypotheses(iface)
    assert len(hyps) == 4
    for h in hyps:
        assert len(h.evidence_for) >= 1
        # fallback provenance source should be test.md
        assert h.evidence_for[0].source == "test.md"


def test_enumerate_date_with_schema_provenance() -> None:
    iface = _iface_minimal()
    schema_prov = _prov(source="schema.json", line=3)
    iface.schemas = [
        SchemaDescription(name="Folio", fields=[ResponseField(name="business_date"), ResponseField(name="arrival_date")], provenance=schema_prov)
    ]
    hyps = enumerate_date_hypotheses(iface)
    assert len(hyps) == 4
    assert any(p.source == "schema.json" for p in hyps[0].evidence_for)


def test_enumerate_date_cutoff_fallback_when_empty_pool() -> None:
    iface = _iface_minimal()
    # No date_notes but classification has cutoff and no fields evidence?
    date_cls = DateSemanticsClassification(
        fields=(),
        ambiguous_fields=("date",),
        business_date_cutoff="04:00",
        reasoning="cutoff",
    )
    hyps = enumerate_date_hypotheses(iface, date_cls)
    assert len(hyps) == 4
    # should still have evidence via cutoff fallback
    assert all(len(h.evidence_for) >= 1 for h in hyps)


def test_enumerate_date_business_prior_uniform() -> None:
    iface = _iface_minimal()
    iface.date_notes = [("cutoff 04:00", _prov())]
    hyps = enumerate_date_hypotheses(iface)
    assert sum(h.prior for h in hyps) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Pagination family
# ---------------------------------------------------------------------------


def _make_op(path: str, hint: str = "none", params: list[str] | None = None) -> OperationDescription:
    return OperationDescription(
        operation_id=f"get_{path}",
        method="GET",
        path=path,
        params=[ParamDescription(name=p, location="query") for p in (params or [])],
        pagination_hint=hint,
        provenance=_prov(source="test.md", line=10, snippet=path),
    )


def test_enumerate_pagination_per_operation_filtered() -> None:
    iface = _iface_minimal()
    iface.operations = [
        _make_op("/reservations", hint="cursor", params=["cursor", "limit"]),
        _make_op("/folios", hint="cursor", params=["cursor"]),
        _make_op("/properties", hint="none", params=[]),
    ]
    iface.pagination_notes = [("cursor pagination for /reservations", _prov(source="test.md", line=20))]
    pagination_cls = PaginationClassification(
        per_operation=(
            PaginationGuess(operation="/reservations", strategy="cursor", confidence="high", evidence=(_prov(source="test.md", line=20),)),
            PaginationGuess(operation="/folios", strategy="cursor", confidence="high", evidence=(_prov(source="test.md", line=21),)),
            PaginationGuess(operation="/properties", strategy="none", confidence="high", evidence=(_prov(source="test.md", line=22),)),
        )
    )
    hyps = enumerate_pagination_hypotheses(iface, pagination_cls)
    # Only 2 paginated ops => 6 hypotheses
    assert len(hyps) == 6
    strategies = {h.strategy for h in hyps}
    assert strategies == set(PAGINATION_STRATEGIES)
    ops = {h.field for h in hyps}
    assert ops == {"/reservations", "/folios"}
    assert "/properties" not in ops
    for h in hyps:
        assert len(h.evidence_for) >= 1


def test_enumerate_pagination_global_fallback_no_ops() -> None:
    iface = _iface_minimal()
    iface.provenances = [_prov(source="docs.md", line=1)]
    hyps = enumerate_pagination_hypotheses(iface, None)
    assert len(hyps) == 3
    assert {h.strategy for h in hyps} == set(PAGINATION_STRATEGIES)
    assert all(h.field == "pagination" for h in hyps)
    assert all(h.id.startswith("pagination:global:") for h in hyps)


def test_enumerate_pagination_global_fallback_empty_provenances() -> None:
    iface = _iface_minimal()
    # no operations, no provenance, no pagination notes
    hyps = enumerate_pagination_hypotheses(iface)
    assert len(hyps) == 3
    assert all(len(h.evidence_for) >= 1 for h in hyps)
    # fallback provenance source should be test.md
    assert hyps[0].evidence_for[0].source == "test.md"


def test_enumerate_pagination_fallback_with_pagination_notes() -> None:
    iface = _iface_minimal()
    # no ops, but has pagination notes
    note_prov = _prov(source="docs.md", line=5, snippet="cursor pagination")
    iface.pagination_notes = [("cursor pagination", note_prov)]
    hyps = enumerate_pagination_hypotheses(iface)
    assert len(hyps) == 3
    assert hyps[0].evidence_for[0].source == "docs.md"


def test_enumerate_pagination_per_op_probe_provenance() -> None:
    iface = _iface_minimal()
    iface.operations = [_make_op("/reservations", hint="cursor")]
    iface.pagination_notes = []
    # classification has evidence
    guess_prov = _prov(source="probe", line=1, snippet="next_cursor")
    pagination_cls = PaginationClassification(
        per_operation=(PaginationGuess(operation="/reservations", strategy="cursor", confidence="high", evidence=(guess_prov,)),)
    )
    hyps = enumerate_pagination_hypotheses(iface, pagination_cls)
    assert len(hyps) == 3
    assert any(p.source == "probe" for h in hyps for p in h.evidence_for)


def test_enumerate_pagination_op_without_classification_but_with_hint() -> None:
    iface = _iface_minimal()
    iface.operations = [_make_op("/folios", hint="offset")]
    hyps = enumerate_pagination_hypotheses(iface, None)
    assert len(hyps) == 3
    assert all(h.field == "/folios" for h in hyps)


def test_enumerate_pagination_fragments_contain_details() -> None:
    iface = _iface_minimal()
    iface.operations = [_make_op("/reservations", hint="cursor")]
    hyps = enumerate_pagination_hypotheses(iface)
    for h in hyps:
        assert "pagination" in h.candidate_mapping_fragment
        assert "strategy" in h.candidate_mapping_fragment
        assert h.candidate_mapping_fragment["operation"] == "/reservations"


def test_enumerate_pagination_allops_fallback_param_hint() -> None:
    # When candidate_paths filtered to none but ops have param pagination hints,
    # fallback to param hint should still produce hypotheses
    iface = _iface_minimal()
    # operations with offset/cursor params but hint none and no notes
    iface.operations = [
        _make_op("/list", hint="none", params=["offset", "limit"]),
    ]
    hyps = enumerate_pagination_hypotheses(iface, None)
    # Should produce 3 via param_hints fallback (offset in params)
    assert len(hyps) == 3
    assert all(h.field == "/list" for h in hyps)


# ---------------------------------------------------------------------------
# Amount family
# ---------------------------------------------------------------------------


def test_enumerate_amount_per_field() -> None:
    iface = _iface_minimal()
    schema_prov = _prov(source="openapi.yaml", line=10)
    iface.schemas = [
        SchemaDescription(
            name="Reservation",
            fields=[
                ResponseField(name="amount"),
                ResponseField(name="total_amount"),
                ResponseField(name="guest_name"),
            ],
            provenance=schema_prov,
        )
    ]
    hyps = enumerate_amount_hypotheses(iface)
    # 2 money fields *3 strategies =6
    assert len(hyps) == 6
    fields = {h.field for h in hyps}
    assert fields == {"amount", "total_amount"}
    assert {h.strategy for h in hyps} == set(AMOUNT_STRATEGIES)
    for h in hyps:
        assert len(h.evidence_for) >= 1
        assert h.prior == pytest.approx(1.0 / 3.0)
        assert h.candidate_mapping_fragment["transform"]["name"] == "money"


def test_enumerate_amount_generic_fallback_no_fields() -> None:
    iface = _iface_minimal()
    iface.schemas = []
    iface.provenances = [_prov(source="docs.md", line=1)]
    hyps = enumerate_amount_hypotheses(iface)
    assert len(hyps) == 3
    assert all(h.field == "amount" for h in hyps)
    assert {h.strategy for h in hyps} == set(AMOUNT_STRATEGIES)


def test_enumerate_amount_generic_fallback_empty_provenances() -> None:
    iface = _iface_minimal()
    # No schemas, no provenances -> synthetic fallback
    hyps = enumerate_amount_hypotheses(iface)
    assert len(hyps) == 3
    assert hyps[0].evidence_for[0].source == "test.md"
    assert hyps[0].evidence_for[0].snippet == "amount field (generic fallback)"


def test_enumerate_amount_generic_fallback_with_schema_provenance() -> None:
    iface = _iface_minimal()
    schema_prov = _prov(source="schema.json", line=2)
    iface.schemas = [SchemaDescription(name="Dummy", fields=[ResponseField(name="other")], provenance=schema_prov)]
    hyps = enumerate_amount_hypotheses(iface)
    # No money field, so fallback uses schema provenance
    assert len(hyps) == 3
    assert hyps[0].evidence_for[0].source == "schema.json"


def test_enumerate_amount_op_response_field_detection() -> None:
    iface = _iface_minimal()
    iface.operations = [
        OperationDescription(
            operation_id="op1",
            method="GET",
            path="/folios",
            response_fields=[ResponseField(name="net_amount")],
            provenance=_prov(source="openapi.yaml", line=5),
        )
    ]
    hyps = enumerate_amount_hypotheses(iface)
    assert any(h.field == "net_amount" for h in hyps)
    assert len([h for h in hyps if h.field == "net_amount"]) == 3


def test_enumerate_amount_required_field_detection() -> None:
    iface = _iface_minimal()
    iface.schemas = [
        SchemaDescription(name="Folio", fields=[], required=["total_amount"], provenance=_prov(source="api.yaml", line=3))
    ]
    hyps = enumerate_amount_hypotheses(iface)
    assert any(h.field == "total_amount" for h in hyps)


def test_enumerate_amount_claim_and_fragment() -> None:
    iface = _iface_minimal()
    iface.schemas = [
        SchemaDescription(name="Folio", fields=[ResponseField(name="amount")], provenance=_prov())
    ]
    hyps = enumerate_amount_hypotheses(iface)
    gross = [h for h in hyps if h.strategy == "gross"][0]
    assert "gross" in gross.claim.lower()
    assert gross.candidate_mapping_fragment["semantics"] == "gross"
    assert gross.candidate_mapping_fragment["field"] == "amount"


def test_enumerate_amount_empty_prov_for_field_uses_fallback() -> None:
    iface = _iface_minimal()
    # Create iface with money field but no provenance (None provenance)
    iface.schemas = [
        SchemaDescription(name="Folio", fields=[ResponseField(name="amount")], provenance=None)  # type: ignore[arg-type]
    ]
    hyps = enumerate_amount_hypotheses(iface)
    assert len(hyps) == 3
    assert all(len(h.evidence_for) >= 1 for h in hyps)


# ---------------------------------------------------------------------------
# Status family
# ---------------------------------------------------------------------------


def test_enumerate_status_two_hypotheses_clear() -> None:
    iface = _iface_minimal()
    iface.status_notes = [("status values confirmed, checked_in, checked_out", _prov(source="docs.md", line=5))]
    status_cls = StatusVocabularyClassification(
        vocabulary=("cancelled", "confirmed", "checked_in"),
        kind="clear",
        mapping_hint={},
        confidence="high",
        evidence=(_prov(source="docs.md", line=5),),
        reasoning="clear",
    )
    hyps = enumerate_status_hypotheses(iface, status_cls)
    assert len(hyps) == 2
    strategies = {h.strategy for h in hyps}
    assert strategies == {"clear", "opaque"}
    for h in hyps:
        assert h.family == "status"
        assert len(h.evidence_for) >= 1
        assert h.candidate_mapping_fragment["transform"]["name"] == "enum_map"


def test_enumerate_status_opaque_kind() -> None:
    iface = _iface_minimal()
    iface.status_notes = [("status H1, H2, H3 opaque", _prov(source="harbor.md", line=10))]
    status_cls = StatusVocabularyClassification(
        vocabulary=("H1", "H2", "H3"),
        kind="opaque",
        mapping_hint={"H1": "confirmed", "H2": "checked_in"},
        confidence="high",
        evidence=(_prov(source="harbor.md", line=10),),
        reasoning="opaque",
    )
    hyps = enumerate_status_hypotheses(iface, status_cls)
    assert len(hyps) == 2
    opaque = [h for h in hyps if h.strategy == "opaque"][0]
    assert opaque.candidate_mapping_fragment["transform"]["mapping"]["H1"] == "confirmed"
    assert opaque.prior == 0.5


def test_enumerate_status_no_evidence_fallback() -> None:
    iface = _iface_minimal()
    hyps = enumerate_status_hypotheses(iface, None)
    assert len(hyps) == 2
    assert all(len(h.evidence_for) >= 1 for h in hyps)
    assert hyps[0].evidence_for[0].source == "test.md"


def test_enumerate_status_from_schema_status_field() -> None:
    iface = _iface_minimal()
    iface.schemas = [
        SchemaDescription(name="Reservation", fields=[ResponseField(name="reservation_status", description="enum ['confirmed']")], provenance=_prov(source="openapi.yaml", line=8))
    ]
    hyps = enumerate_status_hypotheses(iface, None)
    assert len(hyps) == 2
    assert hyps[0].evidence_for[0].source == "openapi.yaml"


def test_enumerate_status_infer_from_notes_when_no_classification() -> None:
    iface = _iface_minimal()
    iface.status_notes = [("status codes H1 and H2 map", _prov(source="docs.md", line=2))]
    hyps = enumerate_status_hypotheses(iface, None)
    assert len(hyps) == 2
    # Should infer opaque kind
    opaque = [h for h in hyps if h.strategy == "opaque"][0]
    assert "H1" in opaque.candidate_mapping_fragment["transform"]["mapping"]


def test_enumerate_status_infer_clear_from_notes() -> None:
    iface = _iface_minimal()
    iface.status_notes = [("status confirmed and cancelled are valid", _prov(source="docs.md", line=2))]
    hyps = enumerate_status_hypotheses(iface, None)
    assert len(hyps) == 2
    clear = [h for h in hyps if h.strategy == "clear"][0]
    assert clear.prior == pytest.approx(0.6) or clear.prior == 0.6


def test_enumerate_status_prior_opaque_vs_clear() -> None:
    iface = _iface_minimal()
    iface.status_notes = [("H1 opaque", _prov())]
    clear_cls = StatusVocabularyClassification(vocabulary=("H1",), kind="opaque", evidence=(_prov(),), confidence="high", reasoning="x")
    hyps_opaque = enumerate_status_hypotheses(iface, clear_cls)
    assert [h for h in hyps_opaque if h.strategy == "clear"][0].prior == 0.5
    assert [h for h in hyps_opaque if h.strategy == "opaque"][0].prior == 0.5

    clear_cls2 = StatusVocabularyClassification(vocabulary=("confirmed",), kind="clear", evidence=(_prov(),), confidence="high", reasoning="x")
    hyps_clear = enumerate_status_hypotheses(iface, clear_cls2)
    assert [h for h in hyps_clear if h.strategy == "clear"][0].prior == 0.6
    assert [h for h in hyps_clear if h.strategy == "opaque"][0].prior == 0.4


# ---------------------------------------------------------------------------
# Enumerator top-level
# ---------------------------------------------------------------------------


def test_enumerator_all_harbor_real_fixture() -> None:
    iface = read_docs_file("simulators/docs/harborpms.md")
    cls = classify_all(iface)
    enum = HypothesisEnumerator()
    hyps = enum.enumerate_all(iface, cls)
    # date 4 + pagination 12 + amount 6 + status 2 =24 for harbor with current logic
    # Check at least 11 (4+3+3+1 minimal) and that each family present
    assert len(hyps) >= 10
    families = {h.family for h in hyps}
    assert families == {"date", "pagination", "amount", "status"}
    # every hypothesis has evidence
    for h in hyps:
        assert len(h.evidence_for) >= 1, f"{h.id} missing evidence"
        assert h.candidate_mapping_fragment
    # sorted by family then id
    ids = [(h.family, h.id) for h in hyps]
    assert ids == sorted(ids)


def test_enumerator_all_atlas_real_fixture() -> None:
    iface = read_docs_file("simulators/docs/atlaspms-openapi.yaml")
    cls = classify_all(iface)
    enum = HypothesisEnumerator()
    hyps = enum.enumerate_all(iface, cls)
    assert len(hyps) >= 10
    families = {h.family for h in hyps}
    assert families == {"date", "pagination", "amount", "status"}
    for h in hyps:
        assert len(h.evidence_for) >= 1


def test_enumerator_all_without_classification() -> None:
    iface = _iface_minimal()
    iface.operations = [_make_op("/test", hint="cursor")]
    iface.schemas = [SchemaDescription(name="Test", fields=[ResponseField(name="amount")], provenance=_prov())]
    iface.date_notes = [("date ambiguous", _prov())]
    iface.status_notes = [("status confirmed", _prov())]
    iface.pagination_notes = [("cursor pagination", _prov())]
    enum = HypothesisEnumerator()
    hyps = enum.enumerate_all(iface, None)
    assert len(hyps) >= 10
    for h in hyps:
        assert len(h.evidence_for) >= 1


def test_enumerator_enumerate_all_families_structure() -> None:
    iface = _iface_minimal()
    enum = HypothesisEnumerator()
    grouped = enum.enumerate_all_families(iface, None)
    assert set(grouped.keys()) == {"date", "pagination", "amount", "status"}
    assert len(grouped["date"]) == 4
    assert len(grouped["status"]) == 2
    assert len(grouped["pagination"]) == 3  # global fallback when no ops
    assert len(grouped["amount"]) == 3


def test_enumerator_individual_family_methods() -> None:
    iface = _iface_minimal()
    iface.date_notes = [("date ambiguous", _prov())]
    iface.operations = [_make_op("/reservations", hint="cursor")]
    iface.schemas = [SchemaDescription(name="Folio", fields=[ResponseField(name="amount")], provenance=_prov())]
    iface.status_notes = [("H1", _prov())]
    cls = classify_all(iface)
    enum = HypothesisEnumerator()
    date_hyps = enum.enumerate_date(iface, cls)
    assert len(date_hyps) == 4
    pag_hyps = enum.enumerate_pagination(iface, cls)
    assert len(pag_hyps) == 3
    amount_hyps = enum.enumerate_amount(iface, cls)
    assert len(amount_hyps) == 3
    status_hyps = enum.enumerate_status(iface, cls)
    assert len(status_hyps) == 2


def test_enumerator_probe_results_accepted() -> None:
    iface = _iface_minimal()
    enum = HypothesisEnumerator()
    probes = [ProbeObservation(operation="/reservations", response={"data": []})]
    hyps = enum.enumerate_all(iface, None, probe_results=probes)
    assert len(hyps) >= 10


def test_enumerator_determinism() -> None:
    iface = read_docs_file("simulators/docs/harborpms.md")
    cls = classify_all(iface)
    enum = HypothesisEnumerator()
    hyps1 = enum.enumerate_all(iface, cls)
    hyps2 = enum.enumerate_all(iface, cls)
    assert [h.id for h in hyps1] == [h.id for h in hyps2]
    # also test enumerator is reusable
    hyps3 = enum.enumerate_all(iface)
    hyps4 = enum.enumerate_all(iface)
    assert [h.id for h in hyps3] == [h.id for h in hyps4]


def test_every_hypothesis_links_to_evidence_record() -> None:
    """Plan requirement: every hypothesis links to at least one evidence record."""
    iface = read_docs_file("simulators/docs/harborpms.md")
    cls = classify_all(iface)
    enum = HypothesisEnumerator()
    for h in enum.enumerate_all(iface, cls):
        assert h.evidence_for, f"hypothesis {h.id} has no evidence_for"
        # evidence_for entries must be Provenance with source+line
        for prov in h.evidence_for:
            assert prov.source
            assert prov.line >= 1


def test_date_family_strategies_match_task() -> None:
    """Task specifies date family: created/arrival/business/UTC."""
    iface = _iface_minimal()
    hyps = enumerate_date_hypotheses(iface)
    assert {h.strategy for h in hyps} == {"created", "arrival", "business", "utc"}


def test_pagination_family_strategies_match_task() -> None:
    """Task specifies pagination family: offset/cursor/page."""
    iface = _iface_minimal()
    iface.operations = [_make_op("/reservations", hint="cursor")]
    hyps = enumerate_pagination_hypotheses(iface)
    assert {h.strategy for h in hyps} == {"offset", "cursor", "page"}


def test_amount_family_strategies_match_task() -> None:
    """Task specifies amount family: gross/net/tax-inclusive."""
    iface = _iface_minimal()
    hyps = enumerate_amount_hypotheses(iface)
    assert {h.strategy for h in hyps} == {"gross", "net", "tax_inclusive"}


def test_hypothesis_ids_unique_within_run() -> None:
    iface = read_docs_file("simulators/docs/harborpms.md")
    cls = classify_all(iface)
    enum = HypothesisEnumerator()
    hyps = enum.enumerate_all(iface, cls)
    ids = [h.id for h in hyps]
    assert len(ids) == len(set(ids)), "hypothesis ids must be unique"


# ---------------------------------------------------------------------------
# Spec v2: body_cursor hypothesis for POST ops with a body-paging signal
# ---------------------------------------------------------------------------

from autobridge.discovery.docs_reader import BodyField  # noqa: E402
from autobridge.discovery.hypotheses import BODY_CURSOR_STRATEGY  # noqa: E402


def _post_op_with_body_paging(path: str = "/widgets/getAll/2023-06-06") -> OperationDescription:
    return OperationDescription(
        operation_id="widgetsGetAll",
        method="POST",
        path=path,
        body_fields=[BodyField(name="ClientToken", required=True)],
        body_required=["ClientToken"],
        body_cursor_path="Limitation.Cursor",
        body_count_path="Limitation.Count",
        body_count_max=1000,
        response_items_field="Widgets",
        response_cursor_field="Cursor",
        pagination_hint="body_cursor",
        provenance=_prov(),
    )


def test_pagination_body_cursor_only_for_post_ops_with_signal() -> None:
    iface = _iface_minimal()
    iface.operations = [_post_op_with_body_paging()]
    hyps = enumerate_pagination_hypotheses(iface)
    by_strategy = {h.strategy for h in hyps}
    assert by_strategy == {"offset", "cursor", "page", BODY_CURSOR_STRATEGY}
    body = next(h for h in hyps if h.strategy == BODY_CURSOR_STRATEGY)
    assert body.id == "pagination:/widgets/getAll/2023-06-06:body_cursor"
    assert body.candidate_mapping_fragment["pagination"]["cursor_path"] == "Limitation.Cursor"
    assert body.candidate_mapping_fragment["pagination"]["count_path"] == "Limitation.Count"
    assert body.candidate_mapping_fragment["pagination"]["count_value"] == 100
    assert body.candidate_mapping_fragment["pagination"]["next_path"] == "$.Cursor"
    assert body.candidate_mapping_fragment["pagination"]["items_path"] == "$.Widgets"
    assert body.evidence_for


def test_pagination_get_ops_keep_classic_three_strategies() -> None:
    iface = _iface_minimal()
    get_op = OperationDescription(
        operation_id="listThings",
        method="GET",
        path="/things",
        params=[ParamDescription(name="cursor", location="query")],
        pagination_hint="cursor",
        provenance=_prov(),
    )
    iface.operations = [get_op, _post_op_with_body_paging()]
    hyps = enumerate_pagination_hypotheses(iface)
    get_hyps = [h for h in hyps if h.field == "/things"]
    assert {h.strategy for h in get_hyps} == {"offset", "cursor", "page"}
    post_hyps = [h for h in hyps if h.field == "/widgets/getAll/2023-06-06"]
    assert {h.strategy for h in post_hyps} == {"offset", "cursor", "page", BODY_CURSOR_STRATEGY}


def test_pagination_post_without_signal_has_no_body_cursor() -> None:
    iface = _iface_minimal()
    plain_post = OperationDescription(
        operation_id="doThing",
        method="POST",
        path="/things/do",
        pagination_hint="none",
        provenance=_prov(),
    )
    iface.operations = [plain_post]
    hyps = enumerate_pagination_hypotheses(iface)
    assert all(h.strategy != BODY_CURSOR_STRATEGY for h in hyps)
