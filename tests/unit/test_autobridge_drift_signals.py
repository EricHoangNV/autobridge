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
