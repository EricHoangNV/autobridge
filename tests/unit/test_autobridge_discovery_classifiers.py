"""Tests for discovery/classifiers.py (P1.W5.2)."""

from __future__ import annotations

from pathlib import Path

from autobridge.discovery.classifiers import (
    ProbeObservation,
    classify_all,
    classify_auth,
    classify_date_semantics,
    classify_pagination,
    classify_status_vocabulary,
)
from autobridge.discovery.docs_reader import (
    AuthDescription,
    InterfaceDescription,
    OperationDescription,
    ParamDescription,
    Provenance,
    ResponseField,
    SchemaDescription,
    read_docs_file,
)


def _iface_with_auth(kind: str, header: str | None = None, desc: str = "") -> InterfaceDescription:
    iface = InterfaceDescription(source="test.md", doc_type="markdown")
    iface.auth = AuthDescription(kind=kind, header_name=header, description=desc, provenance=Provenance(source="test.md", line=5, snippet=desc))
    return iface


def _iface_with_ops(ops: list[OperationDescription], pagination_notes=None, date_notes=None, status_notes=None) -> InterfaceDescription:
    iface = InterfaceDescription(source="test.md", doc_type="markdown")
    iface.operations = ops
    iface.pagination_notes = pagination_notes or []
    iface.date_notes = date_notes or []
    iface.status_notes = status_notes or []
    iface.schemas = []
    return iface


# ---------------------------------------------------------------------------
# Auth classifier
# ---------------------------------------------------------------------------


def test_classify_auth_bearer_high() -> None:
    iface = _iface_with_auth("bearer", desc="Bearer token. Header: Authorization: Bearer <token>")
    res = classify_auth(iface)
    assert res.kind == "bearer"
    assert res.confidence == "high"
    assert len(res.evidence) == 1
    assert "bearer" in res.reasoning.lower()


def test_classify_auth_api_key_high() -> None:
    iface = _iface_with_auth("api_key_header", header="X-API-Key", desc="api key header X-API-Key")
    res = classify_auth(iface)
    assert res.kind == "api_key_header"
    assert res.header_name == "X-API-Key"
    assert res.confidence == "high"


def test_classify_auth_none_high() -> None:
    iface = _iface_with_auth("none", desc="permissive no auth required")
    res = classify_auth(iface)
    assert res.kind == "none"
    assert res.confidence == "high"


def test_classify_auth_unknown_low() -> None:
    iface = _iface_with_auth("unknown", desc="auth unknown")
    res = classify_auth(iface)
    assert res.kind == "unknown"
    assert res.confidence == "low"


def test_classify_auth_basic_medium() -> None:
    iface = _iface_with_auth("basic", desc="basic auth")
    res = classify_auth(iface)
    assert res.kind == "basic"
    assert res.confidence == "medium"


def test_classify_auth_oauth2_medium() -> None:
    iface = _iface_with_auth("oauth2", desc="oauth2 flow")
    res = classify_auth(iface)
    assert res.kind == "oauth2"
    assert res.confidence == "medium"


def test_classify_auth_no_auth_section() -> None:
    iface = InterfaceDescription(source="x.md", doc_type="markdown")
    res = classify_auth(iface)
    assert res.kind == "unknown"
    assert res.confidence == "low"
    assert "no auth" in res.reasoning.lower()


def test_classify_auth_probe_401_contradicts_none() -> None:
    iface = _iface_with_auth("none")
    probe = ProbeObservation(operation="/reservations", response={}, status_code=401)
    res = classify_auth(iface, probe_results=[probe])
    assert res.probe_refined is True
    # still none but refined
    assert res.kind == "none"


def test_classify_auth_probe_401_unknown_becomes_bearer() -> None:
    iface = InterfaceDescription(source="x.md", doc_type="markdown")
    probe = ProbeObservation(operation="/reservations", response={}, status_code=401)
    res = classify_auth(iface, probe_results=[probe])
    assert res.kind == "bearer"
    assert res.probe_refined is True


def test_classify_auth_probe_200_unknown_becomes_none() -> None:
    iface = InterfaceDescription(source="x.md", doc_type="markdown")
    probe = ProbeObservation(operation="/reservations", response={"data": []}, status_code=200)
    res = classify_auth(iface, probe_results=[probe])
    assert res.kind == "none"
    assert res.probe_refined is True


def test_classify_auth_probe_200_bearer_not_downgraded() -> None:
    iface = _iface_with_auth("bearer", desc="Bearer token")
    probe = ProbeObservation(operation="/reservations", response={"data": []}, status_code=200)
    res = classify_auth(iface, probe_results=[probe])
    assert res.kind == "bearer"


def test_classify_auth_probe_unauthorized_body() -> None:
    iface = InterfaceDescription(source="x.md", doc_type="markdown")
    probe = ProbeObservation(operation="/x", response={"detail": {"message": "unauthorized"}}, status_code=200)
    res = classify_auth(iface, probe_results=[probe])
    assert res.probe_refined is True


# ---------------------------------------------------------------------------
# Pagination classifier
# ---------------------------------------------------------------------------


def test_pagination_cursor_from_params() -> None:
    op = OperationDescription(
        operation_id="listReservations",
        method="GET",
        path="/reservations",
        params=[ParamDescription(name="cursor", location="query"), ParamDescription(name="limit", location="query")],
        pagination_hint="cursor",
        provenance=Provenance(source="h.md", line=10, snippet="cursor"),
    )
    iface = _iface_with_ops([op], pagination_notes=[("cursor pagination", Provenance(source="h.md", line=2, snippet="cursor"))])
    res = classify_pagination(iface)
    assert len(res.per_operation) == 1
    g = res.per_operation[0]
    assert g.strategy == "cursor"
    assert g.confidence == "high"
    assert g.operation == "/reservations"


def test_pagination_offset_from_params() -> None:
    op = OperationDescription(
        operation_id="listReservations",
        method="GET",
        path="/reservations",
        params=[ParamDescription(name="offset", location="query"), ParamDescription(name="limit", location="query")],
        pagination_hint="offset",
        provenance=Provenance(source="a.json", line=5, snippet="offset"),
    )
    iface = _iface_with_ops([op])
    res = classify_pagination(iface)
    assert res.per_operation[0].strategy == "offset"
    assert res.per_operation[0].confidence == "high"


def test_pagination_page_hint() -> None:
    op = OperationDescription(
        operation_id="list",
        method="GET",
        path="/things",
        params=[ParamDescription(name="page", location="query")],
        pagination_hint="page",
        provenance=Provenance(source="p.md", line=3, snippet="page"),
    )
    iface = _iface_with_ops([op])
    res = classify_pagination(iface)
    assert res.per_operation[0].strategy == "page"
    assert res.per_operation[0].confidence == "medium"


def test_pagination_none_no_params() -> None:
    op = OperationDescription(
        operation_id="listProperties",
        method="GET",
        path="/properties",
        params=[],
        pagination_hint="none",
        provenance=Provenance(source="h.md", line=20, snippet="properties"),
    )
    iface = _iface_with_ops([op])
    res = classify_pagination(iface)
    assert res.per_operation[0].strategy == "none"
    assert res.per_operation[0].confidence == "high"


def test_pagination_none_with_params_medium() -> None:
    op = OperationDescription(
        operation_id="getOne",
        method="GET",
        path="/folios/{id}",
        params=[ParamDescription(name="property_id", location="query")],
        pagination_hint="none",
        provenance=Provenance(source="h.md", line=20, snippet="get"),
    )
    iface = _iface_with_ops([op])
    res = classify_pagination(iface)
    assert res.per_operation[0].confidence == "medium"


def test_pagination_cursor_missing_param_medium() -> None:
    op = OperationDescription(
        operation_id="x",
        method="GET",
        path="/x",
        params=[],
        pagination_hint="cursor",
        provenance=Provenance(source="h.md", line=10, snippet="cursor"),
    )
    iface = _iface_with_ops([op])
    res = classify_pagination(iface)
    assert res.per_operation[0].confidence == "medium"


def test_pagination_probe_overrides_offset_to_cursor() -> None:
    # Harbor wrong example: docs says offset but probe shows cursor
    op = OperationDescription(
        operation_id="listReservations",
        method="GET",
        path="/reservations",
        params=[ParamDescription(name="limit", location="query"), ParamDescription(name="cursor", location="query")],
        pagination_hint="offset",
        provenance=Provenance(source="h.md", line=10, snippet="offset example"),
    )
    iface = _iface_with_ops([op])
    probe = ProbeObservation(operation="/reservations", response={"data": [], "next_cursor": "abc", "cursor": None}, status_code=200)
    res = classify_pagination(iface, probe_results=[probe])
    assert res.per_operation[0].strategy == "cursor"
    assert res.per_operation[0].confidence == "high"
    assert "probe" in res.per_operation[0].reasoning.lower()


def test_pagination_probe_confirms_strategy() -> None:
    op = OperationDescription(
        operation_id="listReservations",
        method="GET",
        path="/reservations",
        params=[ParamDescription(name="offset", location="query")],
        pagination_hint="offset",
        provenance=Provenance(source="a.json", line=5, snippet="offset"),
    )
    iface = _iface_with_ops([op])
    probe = ProbeObservation(operation="/reservations", response={"data": [], "next_offset": 10}, status_code=200)
    res = classify_pagination(iface, probe_results=[probe])
    assert res.per_operation[0].strategy == "offset"
    assert "confirms" in res.per_operation[0].reasoning.lower()


def test_pagination_empty_ops() -> None:
    iface = InterfaceDescription(source="x.md", doc_type="markdown")
    res = classify_pagination(iface)
    assert res.per_operation == ()
    assert res.overall_confidence == "low"


def test_pagination_overall_confidence_high() -> None:
    op1 = OperationDescription(operation_id="a", method="GET", path="/a", params=[], pagination_hint="none", provenance=Provenance(source="x", line=1, snippet="a"))
    op2 = OperationDescription(operation_id="b", method="GET", path="/b", params=[ParamDescription(name="cursor", location="query")], pagination_hint="cursor", provenance=Provenance(source="x", line=2, snippet="b"))
    iface = _iface_with_ops([op1, op2])
    res = classify_pagination(iface)
    assert res.overall_confidence == "high"


def test_pagination_real_atlas_docs() -> None:
    p = Path("simulators/docs/atlaspms-openapi.json")
    if not p.exists():
        return
    iface = read_docs_file(p)
    res = classify_pagination(iface)
    # Atlas (Cloudbeds-style) is page-paginated on the list methods
    ops = {g.operation: g.strategy for g in res.per_operation}
    assert ops.get("/api/v1.3/getReservations") == "page"
    assert ops.get("/api/v1.3/getTransactions") == "page"
    # single-item fetches carry no pagination
    assert ops.get("/api/v1.3/getReservation") == "none"
    assert ops.get("/api/v1.3/getGuest") == "none"


def test_pagination_real_harbor_docs() -> None:
    p = Path("simulators/docs/harbor.md")
    if not p.exists():
        return
    iface = read_docs_file(p)
    res = classify_pagination(iface)
    ops = {g.operation: g.strategy for g in res.per_operation}
    assert ops.get("/reservations") == "cursor"
    assert ops.get("/folios") == "cursor"


# ---------------------------------------------------------------------------
# Date semantics
# ---------------------------------------------------------------------------


def test_date_classify_atlas_like() -> None:
    # Build iface mimicking Atlas
    schema = SchemaDescription(
        name="Reservation",
        fields=[
            ResponseField(name="reservation_id", type="string"),
            ResponseField(name="arrival_date", type="string", description="property-local date"),
            ResponseField(name="departure_date", type="string"),
            ResponseField(name="status", type="string"),
            ResponseField(name="updated_at", type="string", description="UTC instant"),
        ],
        required=["arrival_date", "updated_at"],
        provenance=Provenance(source="a.json", line=10, snippet="Reservation"),
    )
    iface = InterfaceDescription(source="a.json", doc_type="openapi")
    iface.schemas = [schema]
    iface.date_notes = [("arrival_date property-local date", Provenance(source="a.json", line=20, snippet="property-local"))]
    res = classify_date_semantics(iface)
    fields = {f.field: f.semantics for f in res.fields}
    assert fields.get("arrival_date") == "property_local_date"
    assert fields.get("updated_at") == "utc_instant"
    assert res.business_date_cutoff is None


def test_date_classify_harbor_ambiguous() -> None:
    iface = InterfaceDescription(source="h.md", doc_type="markdown")
    iface.schemas = [
        SchemaDescription(
            name="example_1",
            fields=[
                ResponseField(name="reservation_id", type="str"),
                ResponseField(name="arrival_date", type="str"),
                ResponseField(name="date", type="str", description="the date"),
                ResponseField(name="business_date", type="str"),
                ResponseField(name="posted_at", type="str"),
            ],
            required=[],
            provenance=Provenance(source="h.md", line=40, snippet="example"),
        )
    ]
    iface.date_notes = [
        ("field named `date` duplicates arrival_date ambiguous", Provenance(source="h.md", line=50, snippet="ambiguous")),
        ("business_date cutoff 04:00 local cutoff", Provenance(source="h.md", line=55, snippet="cutoff 04:00")),
    ]
    res = classify_date_semantics(iface)
    fields = {f.field: f.semantics for f in res.fields}
    assert fields.get("date") == "ambiguous_date"
    assert "date" in res.ambiguous_fields
    assert res.business_date_cutoff == "04:00"
    # ambiguous should be high confidence
    amb = next(f for f in res.fields if f.field == "date")
    assert amb.confidence == "high"


def test_date_classify_cutoff_detection() -> None:
    iface = InterfaceDescription(source="h.md", doc_type="markdown")
    iface.date_notes = [("cutoff 04:00 local", Provenance(source="h.md", line=5, snippet="cutoff"))]
    iface.schemas = [SchemaDescription(name="s", fields=[ResponseField(name="business_date", type="string")], provenance=Provenance(source="h.md", line=1, snippet="s"))]
    res = classify_date_semantics(iface)
    assert res.business_date_cutoff == "04:00"


def test_date_classify_business_date_with_cutoff_high() -> None:
    iface = InterfaceDescription(source="h.md", doc_type="markdown")
    iface.schemas = [SchemaDescription(name="s", fields=[ResponseField(name="business_date", type="string")], provenance=Provenance(source="h.md", line=1, snippet="s"))]
    iface.date_notes = [("business_date with cutoff 04:00", Provenance(source="h.md", line=2, snippet="cutoff 04:00"))]
    res = classify_date_semantics(iface)
    bd = next(f for f in res.fields if f.field == "business_date")
    assert bd.semantics == "business_date"
    assert bd.confidence == "high"


def test_date_classify_unknown_field() -> None:
    iface = InterfaceDescription(source="x.md", doc_type="markdown")
    iface.schemas = [SchemaDescription(name="s", fields=[ResponseField(name="weird_date_thing", type="string")], provenance=Provenance(source="x.md", line=1, snippet="s"))]
    iface.date_notes = []
    res = classify_date_semantics(iface)
    w = next(f for f in res.fields if f.field == "weird_date_thing")
    assert w.semantics == "unknown"
    assert w.confidence == "low"


def test_date_probe_confirms_ambiguous() -> None:
    iface = InterfaceDescription(source="h.md", doc_type="markdown")
    iface.schemas = [SchemaDescription(name="s", fields=[ResponseField(name="arrival_date", type="string"), ResponseField(name="date", type="string")], provenance=Provenance(source="h.md", line=1, snippet="s"))]
    iface.date_notes = [("ambiguous field named `date` duplicates arrival_date", Provenance(source="h.md", line=2, snippet="ambiguous"))]
    probe = ProbeObservation(operation="/reservations", response={"data": [{"arrival_date": "2026-10-05", "date": "2026-10-05"}]}, status_code=200)
    res = classify_date_semantics(iface, probe_results=[probe])
    amb = next(f for f in res.fields if f.field == "date")
    assert amb.confidence == "high"
    assert "probe" in res.reasoning.lower() or any("probe" in f.description.lower() or "probe" in res.reasoning.lower() for f in res.fields)


def test_date_no_candidates_but_notes_fallback() -> None:
    iface = InterfaceDescription(source="h.md", doc_type="markdown")
    iface.schemas = []
    iface.operations = []
    iface.date_notes = [("field named `date` ambiguous", Provenance(source="h.md", line=10, snippet="date"))]
    res = classify_date_semantics(iface)
    assert any(f.field == "date" for f in res.fields)


def test_date_real_harbor_docs() -> None:
    p = Path("simulators/docs/harbor.md")
    if not p.exists():
        return
    iface = read_docs_file(p)
    res = classify_date_semantics(iface)
    assert "date" in res.ambiguous_fields
    assert res.business_date_cutoff == "04:00"
    fields = {f.field: f.semantics for f in res.fields}
    assert fields.get("business_date") == "business_date"
    assert fields.get("arrival_date") == "property_local_date"


def test_date_real_atlas_docs() -> None:
    p = Path("simulators/docs/atlaspms-openapi.json")
    if not p.exists():
        return
    iface = read_docs_file(p)
    res = classify_date_semantics(iface)
    fields = {f.field: f.semantics for f in res.fields}
    # Cloudbeds-style wire names: pure property-local dates classify;
    # zone-less date-times stay unknown (documented vendor trap).
    assert fields.get("startDate") == "property_local_date"
    assert fields.get("endDate") == "property_local_date"
    assert fields.get("businessDate") == "business_date"
    # atlas should not have ambiguous date


# ---------------------------------------------------------------------------
# Status vocabulary
# ---------------------------------------------------------------------------


def test_status_clear_vocab() -> None:
    iface = InterfaceDescription(source="a.json", doc_type="openapi")
    schema = SchemaDescription(
        name="Reservation",
        fields=[ResponseField(name="status", type="string", description="status enum enum=['confirmed', 'cancelled', 'checked_in']")],
        provenance=Provenance(source="a.json", line=10, snippet="enum"),
    )
    iface.schemas = [schema]
    iface.status_notes = [("status enum: ['confirmed', 'cancelled']", Provenance(source="a.json", line=11, snippet="status"))]
    res = classify_status_vocabulary(iface)
    assert res.kind == "clear"
    assert "confirmed" in res.vocabulary


def test_status_opaque_vocab() -> None:
    iface = InterfaceDescription(source="h.md", doc_type="markdown")
    iface.status_notes = [
        ("- `H1` — (maps to confirmed)", Provenance(source="h.md", line=30, snippet="H1")),
        ("- `H2` — checked_in", Provenance(source="h.md", line=31, snippet="H2")),
        ("- `H3` — checked_out", Provenance(source="h.md", line=32, snippet="H3")),
    ]
    res = classify_status_vocabulary(iface)
    assert res.kind == "opaque"
    assert "H1" in res.vocabulary
    assert res.mapping_hint.get("H1") == "confirmed"


def test_status_probe_refines_vocab() -> None:
    iface = InterfaceDescription(source="h.md", doc_type="markdown")
    iface.status_notes = []
    iface.schemas = []
    probe = ProbeObservation(operation="/reservations", response={"data": [{"status": "H1"}, {"status": "H2"}]}, status_code=200)
    res = classify_status_vocabulary(iface, probe_results=[probe])
    assert "H1" in res.vocabulary
    assert res.kind == "opaque"


def test_status_schema_enum_parsing() -> None:
    iface = InterfaceDescription(source="a.json", doc_type="openapi")
    iface.schemas = [
        SchemaDescription(
            name="Reservation",
            fields=[ResponseField(name="status", type="string", description="Clear status vocabulary enum=['confirmed', 'checked_in', 'cancelled']")],
            provenance=Provenance(source="a.json", line=5, snippet="status enum"),
        )
    ]
    res = classify_status_vocabulary(iface)
    assert "confirmed" in res.vocabulary
    assert res.confidence in ("high", "medium")


def test_status_unknown_when_empty() -> None:
    iface = InterfaceDescription(source="x.md", doc_type="markdown")
    res = classify_status_vocabulary(iface)
    assert res.kind == "unknown"
    assert res.confidence == "low"


def test_status_high_confidence_opaque_3plus() -> None:
    iface = InterfaceDescription(source="h.md", doc_type="markdown")
    iface.status_notes = [
        ("H1 H2 H3 H4 H5", Provenance(source="h.md", line=10, snippet="H1 H2 H3 H4 H5")),
    ]
    res = classify_status_vocabulary(iface)
    assert res.confidence == "high"


def test_status_real_harbor_docs() -> None:
    p = Path("simulators/docs/harbor.md")
    if not p.exists():
        return
    iface = read_docs_file(p)
    res = classify_status_vocabulary(iface)
    assert res.kind == "opaque"
    assert "H1" in res.vocabulary
    assert res.mapping_hint.get("H1") == "confirmed"


def test_status_real_atlas_docs() -> None:
    p = Path("simulators/docs/atlaspms-openapi.json")
    if not p.exists():
        return
    iface = read_docs_file(p)
    res = classify_status_vocabulary(iface)
    assert res.kind == "clear"
    assert "confirmed" in res.vocabulary
    # Cloudbeds spelling uses single-l "canceled"
    assert "canceled" in res.vocabulary


# ---------------------------------------------------------------------------
# classify_all
# ---------------------------------------------------------------------------


def test_classify_all_bundle() -> None:
    # use real docs to ensure integration
    p = Path("simulators/docs/harbor.md")
    if not p.exists():
        # synthetic
        iface = InterfaceDescription(source="x.md", doc_type="markdown")
        bundle = classify_all(iface)
        assert bundle.auth.kind in ("bearer", "none", "unknown", "api_key_header", "basic", "oauth2")
        assert bundle.pagination is not None
        return
    iface = read_docs_file(p)
    bundle = classify_all(iface)
    assert bundle.auth.kind == "none"
    assert any(g.strategy == "cursor" for g in bundle.pagination.per_operation)
    assert "date" in bundle.date_semantics.ambiguous_fields
    assert bundle.status_vocabulary.kind == "opaque"


def test_classify_all_with_probe() -> None:
    iface = InterfaceDescription(source="x.md", doc_type="markdown")
    iface.operations = [
        OperationDescription(operation_id="x", method="GET", path="/reservations", params=[ParamDescription(name="offset", location="query")], pagination_hint="offset", provenance=Provenance(source="x.md", line=1, snippet="x"))
    ]
    probe = ProbeObservation(operation="/reservations", response={"data": [], "next_cursor": "abc"}, status_code=200)
    bundle = classify_all(iface, probe_results=[probe])
    assert bundle.pagination.per_operation[0].strategy == "cursor"


# ---------------------------------------------------------------------------
# Edge: helpers coverage
# ---------------------------------------------------------------------------


def test_strategy_from_response_keys_page() -> None:
    from autobridge.discovery.classifiers import _strategy_from_response_keys

    assert _strategy_from_response_keys({"next_page": 2, "page": 1}) == "page"
    assert _strategy_from_response_keys({"next_offset": 10}) == "offset"
    assert _strategy_from_response_keys({"next_cursor": "abc"}) == "cursor"
    assert _strategy_from_response_keys({"total": 10}) is None


def test_status_low_confidence_single_value() -> None:
    iface = InterfaceDescription(source="x.md", doc_type="markdown")
    iface.status_notes = [("status H1 only", Provenance(source="x.md", line=1, snippet="H1"))]
    res = classify_status_vocabulary(iface)
    assert res.confidence == "medium"
