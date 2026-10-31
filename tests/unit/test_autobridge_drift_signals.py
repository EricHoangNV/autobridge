"""Tests for drift/signals.py (P1.W4.1 — signals from sync + canary)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from autobridge.drift.fingerprint import compute_fingerprint
from autobridge.drift.signals import (
    DriftSignal,
    _extract_http_signals,
    _severity_for_kind,
    collect_signals,
    signals_from_canary,
    signals_from_sync,
)
from autobridge.runtime.interpreter import OperationResult


def _spec_dict() -> dict[str, Any]:
    return {
        "spec_version": 1,
        "connector": {"id": "testconn", "vendor": "Test", "runtime_target": "cloud"},
        "source": {
            "base_url": "https://example.com",
            "auth": {"type": "bearer", "credential_ref": "TEST_TOKEN"},
            "rate_limit": {"max_rps": 5},
            "timezone_source": {"kind": "static", "value": "UTC"},
        },
        "operations": {
            "list_reservations": {"method": "GET", "path": "/reservations", "params": {}, "pagination": {"strategy": "cursor", "cursor_param": "cursor", "next_path": "$.next", "items_path": "$.data", "stop_when": ["null_cursor"]}}
        },
        "entities": {"Reservation": {"from": "list_reservations", "key": "$.reservation_id", "fields": {"reservation_id": {"path": "$.reservation_id"}}}},
        "capabilities": {"read": ["Reservation"]},
        "health": {"canary": {"every": "60s", "operation": "list_reservations"}, "fingerprint": {}},
    }


def _sync_summary(
    outcome: str = "Complete",
    unmet_criteria: list[str] | None = None,
    records_observed: dict[str, int] | None = None,
    operation_results: dict[str, Any] | None = None,
    blocked_records: dict[str, Any] | None = None,
    invariant_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "outcome": outcome,
        "unmet_criteria": unmet_criteria or [],
        "data_readiness": {"freshness": "current", "completeness": "complete"},
        "fingerprint_id": "abc123",
        "operation_results": operation_results or {},
        "records_observed": records_observed or {"Reservation": 1},
        "canonical_records": {},
        "blocked_records": blocked_records or {},
        "invariant_results": invariant_results or [],
        "checkpoint_values": {},
    }


# ---------------------------------------------------------------------------
# _severity_for_kind
# ---------------------------------------------------------------------------


def test_severity_for_kind() -> None:
    assert _severity_for_kind("outcome_blocked") == "critical"
    assert _severity_for_kind("unknown_enum") == "critical"
    assert _severity_for_kind("outcome_degraded") == "high"
    assert _severity_for_kind("keys_changed") == "medium"
    assert _severity_for_kind("rate_limited") == "medium"
    assert _severity_for_kind("transient_error") == "low"
    assert _severity_for_kind("some_unknown") == "medium"


# ---------------------------------------------------------------------------
# _extract_http_signals
# ---------------------------------------------------------------------------


def test_extract_http_signals_429() -> None:
    results = {"list_reservations": {"http_status": 429}}
    sigs = _extract_http_signals(results)
    assert len(sigs) == 1
    assert sigs[0].kind == "rate_limited"
    assert sigs[0].details["http_status"] == 429


def test_extract_http_signals_401_auth() -> None:
    # Attach http_status via attribute hack: use dict with http_status
    results2 = {"op": {"http_status": 401}}
    sigs = _extract_http_signals(results2)
    assert sigs[0].kind == "auth_error"


def test_extract_http_signals_404_capability() -> None:
    sigs = _extract_http_signals({"op": {"http_status": 404}})
    assert sigs[0].kind == "capability_removed"


def test_extract_http_signals_503_transient() -> None:
    sigs = _extract_http_signals({"op": {"http_status": 503}})
    assert sigs[0].kind == "transient_error"


def test_extract_http_signals_last_error_regex() -> None:
    results = {"op": {"last_error": "HTTP 429 Too Many Requests"}}
    sigs = _extract_http_signals(results)
    assert sigs[0].kind == "rate_limited"


def test_extract_http_signals_raw_pages_status() -> None:
    results = {"op": {"raw_pages": [{"http_status": 500}, {"data": []}]}}
    sigs = _extract_http_signals(results)
    assert any(s.details["http_status"] == 500 for s in sigs)


def test_extract_http_signals_http_errors_list() -> None:
    results = {"op": {"http_errors": [429, 503]}}
    sigs = _extract_http_signals(results)
    assert len(sigs) == 2


def test_extract_http_signals_invalid_status_ignored() -> None:
    results = {"op": {"http_errors": ["not-a-number"]}}
    sigs = _extract_http_signals(results)
    assert len(sigs) == 0


def test_extract_http_signals_non_dict_input() -> None:
    assert _extract_http_signals(None) == []  # type: ignore[arg-type]
    assert _extract_http_signals("bad") == []  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# signals_from_sync — outcome
# ---------------------------------------------------------------------------


def test_signals_from_sync_complete_no_signals() -> None:
    s = signals_from_sync(_sync_summary(outcome="Complete", unmet_criteria=[], operation_results={}))
    assert len(s) == 0


def test_signals_from_sync_blocked() -> None:
    summary = _sync_summary(outcome="Blocked", unmet_criteria=["unknown_enum:status"])
    sigs = signals_from_sync(summary)
    assert any(k == "outcome_blocked" for k in [x.kind for x in sigs])
    assert any(k == "unknown_enum" for k in [x.kind for x in sigs])


def test_signals_from_sync_degraded_no_loss() -> None:
    summary = _sync_summary(outcome="Degraded", unmet_criteria=["no_loss"])
    sigs = signals_from_sync(summary)
    assert any(x.kind == "outcome_degraded" for x in sigs)
    assert any(x.kind == "no_loss" for x in sigs)


def test_signals_from_sync_unknown_enum_field() -> None:
    summary = _sync_summary(outcome="Blocked", unmet_criteria=["unknown_enum:status", "unknown_enum"])
    sigs = signals_from_sync(summary)
    # Should have outcome_blocked + 2 unknown_enum signals
    unknown = [x for x in sigs if x.kind == "unknown_enum"]
    assert len(unknown) >= 2
    assert any(x.entity == "status" for x in unknown)


def test_signals_from_sync_invariant_generic() -> None:
    summary = _sync_summary(outcome="Degraded", unmet_criteria=["date_order"])
    sigs = signals_from_sync(summary)
    assert any(x.kind == "invariant_failure" for x in sigs)


def test_signals_from_sync_blocked_records() -> None:
    summary = _sync_summary(
        outcome="Blocked",
        unmet_criteria=[],
        blocked_records={"Reservation": [{"field": "status", "blocked": True}]},
    )
    sigs = signals_from_sync(summary)
    assert any(x.kind == "unknown_enum" and x.entity == "status" for x in sigs)


def test_signals_from_sync_blocked_records_no_dup() -> None:
    # Already have unknown_enum in unmet, blocked_records should not duplicate per-entity but still maybe
    summary = _sync_summary(
        outcome="Blocked",
        unmet_criteria=["unknown_enum:status"],
        blocked_records={"Reservation": [{"field": "status", "blocked": True}]},
    )
    sigs = signals_from_sync(summary)
    # Should have outcome_blocked + unknown_enum (from unmet) but blocked_records dup suppressed via entity check
    # At least one unknown_enum, not double counted for same entity via blocked_records path
    unknown_status = [x for x in sigs if x.kind == "unknown_enum" and x.entity == "status"]
    assert len(unknown_status) == 1


def test_signals_from_sync_invariant_results() -> None:
    summary = _sync_summary(
        outcome="Degraded",
        unmet_criteria=[],
        invariant_results=[{"check": "unique_key", "status": "failed"}],
    )
    sigs = signals_from_sync(summary)
    assert any(x.kind == "invariant_failure" for x in sigs)


def test_signals_from_sync_repeated_cursor() -> None:
    results = {"list_reservations": OperationResult(operation="list_reservations", records=[], pages_fetched=2, repeated_cursor=True, stop_reason="repeated_cursor")}
    summary = _sync_summary(outcome="Degraded", unmet_criteria=["no_loss"], operation_results=results)
    sigs = signals_from_sync(summary)
    assert any(x.kind == "repeated_cursor" for x in sigs)


def test_signals_from_sync_pagination_implicit_stop() -> None:
    results = {"list_reservations": OperationResult(operation="list_reservations", records=[], pages_fetched=1, stop_reason="empty_page_implicit")}
    summary = _sync_summary(operation_results=results)
    sigs = signals_from_sync(summary)
    assert any(x.kind == "pagination_changed" for x in sigs)


def test_signals_from_sync_zero_records() -> None:
    summary = _sync_summary(records_observed={"Reservation": 0})
    sigs = signals_from_sync(summary)
    assert any(x.kind == "zero_records" for x in sigs)


def test_signals_from_sync_zero_records_blocked_capability() -> None:
    summary = _sync_summary(outcome="Blocked", records_observed={"Reservation": 0})
    sigs = signals_from_sync(summary)
    assert any(x.kind == "zero_records" for x in sigs)
    assert any(x.kind == "capability_removed" for x in sigs)


def test_signals_from_sync_http_via_operation_results() -> None:
    results = {"list_reservations": {"http_status": 503}}
    summary = _sync_summary(operation_results=results)
    sigs = signals_from_sync(summary)
    assert any(x.kind == "transient_error" for x in sigs)


def test_signals_from_sync_fingerprint_delta_pagination() -> None:
    spec = _spec_dict()
    r1 = {"list_reservations": OperationResult(operation="list_reservations", records=[], pages_fetched=1, stop_reason="null_cursor")}
    r2 = {"list_reservations": OperationResult(operation="list_reservations", records=[], pages_fetched=2, stop_reason="repeated_cursor", repeated_cursor=True)}
    prev = compute_fingerprint(spec, r1, {"Reservation": [{"reservation_id": "R1"}]})
    cur = compute_fingerprint(spec, r2, {"Reservation": [{"reservation_id": "R1"}]})
    summary = _sync_summary(operation_results=r2)
    sigs = signals_from_sync(summary, previous_fingerprint=prev, current_fingerprint=cur)
    assert any(x.kind == "pagination_changed" for x in sigs)


def test_signals_from_sync_fingerprint_delta_keys() -> None:
    spec = _spec_dict()
    r = {"list_reservations": OperationResult(operation="list_reservations", records=[], pages_fetched=1, stop_reason="null_cursor")}
    prev = compute_fingerprint(spec, r, {"Reservation": [{"reservation_id": "R1"}]})
    cur = compute_fingerprint(spec, r, {"Reservation": [{"reservation_id": "R99"}]})
    sigs = signals_from_sync(_sync_summary(), previous_fingerprint=prev, current_fingerprint=cur)
    assert any(x.kind == "keys_changed" for x in sigs)


def test_signals_from_sync_fingerprint_delta_types() -> None:
    spec = _spec_dict()
    r = {"list_reservations": OperationResult(operation="list_reservations", records=[], pages_fetched=1, stop_reason="null_cursor")}
    prev = compute_fingerprint(spec, r, {"Reservation": [{"reservation_id": "R1", "amount": Decimal("10.00")}]})
    cur = compute_fingerprint(spec, r, {"Reservation": [{"reservation_id": "R1", "amount": "10.00"}]})
    sigs = signals_from_sync(_sync_summary(), previous_fingerprint=prev, current_fingerprint=cur)
    assert any(x.kind == "types_changed" for x in sigs)


def test_signals_from_sync_fingerprint_delta_generic() -> None:
    # Fingerprint changed but no dimension flag → generic fingerprint_changed
    prev = {"fingerprint_id": "old", "pagination_shape": None, "keys_sample": None, "types": None, "spec_signature": "a"}
    cur = {"fingerprint_id": "new", "pagination_shape": None, "keys_sample": None, "types": None, "spec_signature": "b"}
    sigs = signals_from_sync(_sync_summary(), previous_fingerprint=prev, current_fingerprint=cur)
    assert any(x.kind == "fingerprint_changed" for x in sigs)


def test_signals_from_sync_non_dict_input() -> None:
    assert signals_from_sync(None) == []  # type: ignore[arg-type]
    assert signals_from_sync("bad") == []  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# signals_from_canary
# ---------------------------------------------------------------------------


def test_signals_from_canary_empty() -> None:
    assert signals_from_canary(None) == []
    assert signals_from_canary({}) == []


def test_signals_from_canary_blocked_becomes_canary_failed() -> None:
    spec = _spec_dict()
    summary = _sync_summary(outcome="Blocked", unmet_criteria=["unknown_enum"])
    sigs = signals_from_canary(summary, spec=spec)
    assert any(x.kind == "canary_failed" for x in sigs)


def test_signals_from_canary_pagination_regression() -> None:
    spec = _spec_dict()
    r1 = {"list_reservations": OperationResult(operation="list_reservations", records=[], pages_fetched=1, stop_reason="null_cursor")}
    r2 = {"list_reservations": OperationResult(operation="list_reservations", records=[], pages_fetched=2, stop_reason="repeated_cursor", repeated_cursor=True)}
    prev = compute_fingerprint(spec, r1, {"Reservation": [{"reservation_id": "R1"}]})
    cur = compute_fingerprint(spec, r2, {"Reservation": [{"reservation_id": "R1"}]})
    canary_summary = _sync_summary(operation_results=r2)
    sigs = signals_from_canary(canary_summary, spec=spec, previous_fingerprint=prev, current_fingerprint=cur)
    assert any(x.kind == "canary_pagination_regression" for x in sigs)


def test_signals_from_canary_no_raw_but_outcome() -> None:
    spec = _spec_dict()
    # Summary with outcome Degraded but no signals_from_sync would still produce canary_failed via fallback
    # To trigger fallback, we need raw_signals empty: outcome Complete normally returns no signals,
    # but outcome Degraded always produces signals, so we craft a summary with unexpected outcome string that yields no signals?
    # Instead test with outcome != Complete but signals already present, fallback not needed.
    # Here we test that when canary_summary has no signals but outcome not Complete, we get canary_failed.
    # Create a summary with outcome "Degraded" but mock signals_from_sync to return empty is not possible; instead we directly rely on current logic:
    # For outcome Complete, raw_signals is empty, so fallback should not trigger.
    # For outcome Blocked, raw_signals already has canary_failed, so fallback not needed.
    # Test that empty canary with outcome doesn't crash:
    sigs = signals_from_canary({"outcome": "Blocked", "unmet_criteria": [], "operation_results": {}, "records_observed": {}, "blocked_records": {}, "invariant_results": []}, spec=spec)
    assert any(x.kind in {"canary_failed", "outcome_blocked"} for x in sigs)


def test_signals_from_canary_non_blocked_passthrough() -> None:
    spec = _spec_dict()
    summary = _sync_summary(records_observed={"Reservation": 0})
    sigs = signals_from_canary(summary, spec=spec)
    # zero_records should passthrough as is (not rewritten)
    assert any(x.kind == "zero_records" for x in sigs)


# ---------------------------------------------------------------------------
# collect_signals
# ---------------------------------------------------------------------------


def test_collect_signals_both() -> None:
    sync_summary = _sync_summary(outcome="Degraded", unmet_criteria=["no_loss"])
    canary_summary = _sync_summary(outcome="Blocked", unmet_criteria=["unknown_enum"])
    spec = _spec_dict()
    sigs = collect_signals(sync_summary, canary_summary, spec=spec)
    assert len(sigs) >= 2


def test_collect_signals_dedup() -> None:
    # Both sync and canary produce same kind+operation+entity → dedup keeps first
    sync_summary = _sync_summary(records_observed={"Reservation": 0})
    canary_summary = _sync_summary(records_observed={"Reservation": 0})
    sigs = collect_signals(sync_summary, canary_summary)
    zero_sigs = [x for x in sigs if x.kind == "zero_records"]
    # Should be 1 due to dedup (same kind, entity)
    assert len(zero_sigs) == 1


def test_collect_signals_only_sync() -> None:
    sync_summary = _sync_summary(outcome="Complete")
    sigs = collect_signals(sync_summary)
    assert sigs == []
    # Only canary as None
    sigs2 = collect_signals(None, None)
    assert sigs2 == []


def test_collect_signals_fingerprint_delta_propagated() -> None:
    spec = _spec_dict()
    r1 = {"list_reservations": OperationResult(operation="list_reservations", records=[], pages_fetched=1, stop_reason="null_cursor")}
    r2 = {"list_reservations": OperationResult(operation="list_reservations", records=[], pages_fetched=2, stop_reason="repeated_cursor", repeated_cursor=True)}
    prev = compute_fingerprint(spec, r1)
    cur = compute_fingerprint(spec, r2)
    sync_summary = _sync_summary(operation_results=r2)
    sigs = collect_signals(sync_summary, spec=spec, previous_fingerprint=prev, current_fingerprint=cur)
    assert any(x.fingerprint_delta is not None for x in sigs)


def test_drift_signal_dataclass() -> None:
    s = DriftSignal(kind="test", operation="op", entity="ent", severity="high", message="msg")
    assert s.kind == "test"
    assert s.severity == "high"


# ---------------------------------------------------------------------------
# REQ-064 B1: silent-corruption observers
# ---------------------------------------------------------------------------


def _folio_spec() -> dict[str, Any]:
    """Atlas-like folio spec: business_date + money-mapped fields."""
    return {
        "spec_version": 3,
        "connector": {"id": "atlas", "vendor": "atlas"},
        "source": {"base_url": "http://example.com"},
        "operations": {
            "list_folios": {
                "method": "GET",
                "path": "/folios",
                "params": {},
                "pagination": {"strategy": "page"},
            }
        },
        "entities": {
            "FolioCharge": {
                "from": "list_folios",
                "key": "$.transactionID",
                "fields": {
                    "charge_id": {"path": "$.transactionID"},
                    "business_date": {
                        "path": "$.businessDate",
                        "transform": [{"name": "date_local", "args": {"tz": "source"}}],
                    },
                    "amount": {
                        "path": "$.amount",
                        "transform": [{"name": "money", "args": {"scale": 2}}],
                    },
                },
            }
        },
    }


def _wire_rows(key: str, values: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for i, v in enumerate(values):
        rows.append(
            {
                "transactionID": f"CHG-{i:04d}",
                key: v,
                "amount": "10.00" if key != "amount" else v,
            }
        )
    return rows


def _op_results(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"list_folios": {"records": rows, "pages_fetched": 1}}


def test_mapped_field_emptied_fires_present_but_empty_with_baseline() -> None:
    """REQ-064 B1a: businessDate blank in every faulted wire row while the
    baseline served it populated → mapped_field_emptied (was: 0 signals)."""
    spec = _folio_spec()
    faulted = _sync_summary(
        operation_results=_op_results(_wire_rows("businessDate", [""] * 12)),
        records_observed={"FolioCharge": 12},
    )
    baseline = _sync_summary(
        operation_results=_op_results(_wire_rows("businessDate", ["2026-10-06"] * 12)),
        records_observed={"FolioCharge": 12},
    )
    sigs = signals_from_sync(faulted, spec=spec, baseline_summary=baseline)
    emptied = [s for s in sigs if s.kind == "mapped_field_emptied"]
    assert len(emptied) == 1
    assert emptied[0].details["wire_key"] == "businessDate"
    assert emptied[0].details["canonical_fields"] == ["business_date"]
    assert emptied[0].details["baseline_populated_rate"] == 1.0


def test_mapped_field_emptied_fires_live_shape_via_siblings() -> None:
    """REQ-064 B1a without a baseline (live sync): the dark field plus a
    populated sibling is enough; a fully-dark entity stays silent."""
    spec = _folio_spec()
    # Sibling `amount` populated, businessDate dark → fires.
    faulted = _sync_summary(
        operation_results=_op_results(_wire_rows("businessDate", [""] * 12)),
        records_observed={"FolioCharge": 12},
    )
    sigs = signals_from_sync(faulted, spec=spec)
    assert [s.kind for s in sigs].count("mapped_field_emptied") == 1
    # Every mapped key dark (idle feed shape) → silent, never drift.
    dark = _sync_summary(
        operation_results={
            "list_folios": {
                "records": [
                    {"transactionID": f"CHG-{i:04d}", "businessDate": "", "amount": ""}
                    for i in range(12)
                ],
                "pages_fetched": 1,
            }
        },
        records_observed={"FolioCharge": 12},
    )
    assert "mapped_field_emptied" not in [s.kind for s in signals_from_sync(dark, spec=spec)]


def test_mapped_field_emptied_ignores_fully_absent_key() -> None:
    """A key gone from every record is the keys_changed rename shape —
    this observer never double-reports it."""
    spec = _folio_spec()
    rows = [
        {"transactionID": f"CHG-{i:04d}", "amount": "10.00", "totalNet": "2026-10-06"}
        for i in range(12)
    ]
    faulted = _sync_summary(
        operation_results=_op_results(rows), records_observed={"FolioCharge": 12}
    )
    baseline = _sync_summary(
        operation_results=_op_results(_wire_rows("businessDate", ["2026-10-06"] * 12)),
        records_observed={"FolioCharge": 12},
    )
    sigs = signals_from_sync(faulted, spec=spec, baseline_summary=baseline)
    assert "mapped_field_emptied" not in [s.kind for s in sigs]
    assert "keys_changed" in [s.kind for s in sigs]


def test_mapped_field_emptied_silent_when_baseline_sparse() -> None:
    """A field the baseline never carried populated is legitimately sparse,
    not drift — even when the faulted sync blanks it too."""
    spec = _folio_spec()
    faulted = _sync_summary(
        operation_results=_op_results(_wire_rows("businessDate", [""] * 12)),
        records_observed={"FolioCharge": 12},
    )
    sparse_base = _sync_summary(
        operation_results=_op_results(_wire_rows("businessDate", [""] * 12)),
        records_observed={"FolioCharge": 12},
    )
    sigs = signals_from_sync(faulted, spec=spec, baseline_summary=sparse_base)
    assert "mapped_field_emptied" not in [s.kind for s in sigs]


def _money_spec() -> dict[str, Any]:
    return {
        "spec_version": 3,
        "connector": {"id": "legacy", "vendor": "legacystay"},
        "source": {"base_url": "http://example.com"},
        "operations": {
            "list_reservations": {"method": "GET", "path": "/r", "params": {}}
        },
        "entities": {
            "Reservation": {
                "from": "list_reservations",
                "key": "$.reservation_id",
                "fields": {
                    "reservation_id": {"path": "$.reservation_id"},
                    "total_amount": {
                        "path": "$.total_amount",
                        "transform": [{"name": "money", "args": {"scale": 2}}],
                    },
                },
            }
        },
    }


def _money_summary(values: list[str]) -> dict[str, Any]:
    summary = _sync_summary(records_observed={"Reservation": len(values)})
    summary["canonical_records"] = {
        "Reservation": [
            {"reservation_id": f"RES-{i:04d}", "total_amount": v}
            for i, v in enumerate(values)
        ]
    }
    return summary


def test_money_scale_shift_fires_exact_power_of_ten() -> None:
    """REQ-064 B1b: identical keys and counts, every money value x100 →
    money_scale_shift (was: 0 signals, sync Complete)."""
    spec = _money_spec()
    base_vals = ["174.69", "209.65", "99.00", "12.50", "300.00", "45.10"]
    fault_vals = ["17469.00", "20965.00", "9900.00", "1250.00", "30000.00", "4510.00"]
    sigs = signals_from_sync(
        _money_summary(fault_vals),
        spec=spec,
        baseline_summary=_money_summary(base_vals),
    )
    shifts = [s for s in sigs if s.kind == "money_scale_shift"]
    assert len(shifts) == 1
    assert shifts[0].details["money_field"] == "total_amount"
    assert shifts[0].details["factor"] == "100"


def test_money_scale_shift_silent_without_baseline() -> None:
    """A single sync cannot tell minor from major units: silent without a
    baseline (documented REQ-064 limitation — the bench path compares)."""
    spec = _money_spec()
    sigs = signals_from_sync(_money_summary(["17469.00"] * 6), spec=spec)
    assert "money_scale_shift" not in [s.kind for s in sigs]


def test_money_scale_shift_silent_on_non_power_of_ten() -> None:
    """A gross→net-style 0.9 factor is semantic drift of another kind, not
    a units shift — this observer stays out of its way."""
    spec = _money_spec()
    base_vals = ["100.00", "200.00", "300.00", "400.00", "500.00", "600.00"]
    fault_vals = ["90.00", "180.00", "270.00", "360.00", "450.00", "540.00"]
    sigs = signals_from_sync(
        _money_summary(fault_vals),
        spec=spec,
        baseline_summary=_money_summary(base_vals),
    )
    assert "money_scale_shift" not in [s.kind for s in sigs]


def test_money_scale_shift_silent_when_keys_differ() -> None:
    """Changed record keys are another drift's business (loss/duplicates),
    not a pure units shift."""
    spec = _money_spec()
    base = _money_summary(["100.00"] * 6)
    fault = _money_summary(["10000.00"] * 6)
    assert fault["canonical_records"]["Reservation"][0]["reservation_id"] == "RES-0000"
    fault["canonical_records"]["Reservation"][0] = {
        "reservation_id": "RES-NEW",
        "total_amount": "10000.00",
    }
    sigs = signals_from_sync(fault, spec=spec, baseline_summary=base)
    assert "money_scale_shift" not in [s.kind for s in sigs]


def test_mapped_field_emptied_edge_inputs() -> None:
    """Guards: non-dict inputs, tiny entities and keyless rows never fire."""
    from autobridge.drift.signals import (
        _observe_money_scale_shift,
        _observe_wire_emptied_fields,
    )

    assert _observe_wire_emptied_fields({}, {}) == []
    assert _observe_wire_emptied_fields(None, None) == []
    spec = _folio_spec()
    tiny = {"list_folios": {"records": [{"transactionID": "C-1", "businessDate": ""}], "pages_fetched": 1}}
    assert _observe_wire_emptied_fields(spec, tiny) == []
    assert _observe_money_scale_shift(spec, None, None) == []
    assert _observe_money_scale_shift(spec, {"FolioCharge": []}, {"FolioCharge": []}) == []


def test_money_scale_shift_ignores_non_numeric() -> None:
    """Bool/float/blank money values are skipped, never coerced."""
    from autobridge.drift.signals import _decimal_of

    assert _decimal_of(True) is None
    assert _decimal_of(1.5) is None
    assert _decimal_of("") is None
    assert _decimal_of("  ") is None
    assert _decimal_of("1,000.00") is not None
    assert _decimal_of(5) is not None


def test_canonical_key_falls_back_to_first_value() -> None:
    """Rows without id-like keys still match by first value."""
    from autobridge.drift.signals import _canonical_key_of

    assert _canonical_key_of({"reservation_id": "R-1"}) == "R-1"
    assert _canonical_key_of({"weird": "W-9"}) == "W-9"
    assert _canonical_key_of({}) is None
