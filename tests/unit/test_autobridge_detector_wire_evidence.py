"""REQ-027 sub-step 2 — the detector carries the evidence.

Every test below builds its inputs the way a real sync does: a plain
ConnectorSpec dict plus an ``operation_results`` entry holding the observed
``raw_pages`` envelopes and ``records``. No scenario id, no fault YAML, no
answer key appears anywhere — only what came off the wire.

Each test drives the full deterministic path:
``signals_from_sync`` -> ``classify`` -> ``RepairController.handle`` and
asserts the right L1 rule fires with ``frontier_tokens == 0``.
"""

from __future__ import annotations

from typing import Any

from autobridge.drift.classifier import classify
from autobridge.drift.fingerprint import FingerprintDelta
from autobridge.drift.signals import signals_from_sync
from autobridge.repair.controller import RepairController
from autobridge.runtime.interpreter import OperationResult


def _page_spec() -> dict[str, Any]:
    """Atlas-like page-paginated spec (baseline: pageNumber/pageSize + total)."""
    return {
        "spec_version": 1,
        "connector": {"id": "atlas", "vendor": "atlas"},
        "source": {
            "base_url": "http://atlas:8000",
            "auth": {"type": "bearer", "credential_ref": "ATLAS_API_KEY"},
            "timezone_source": {"kind": "static", "value": "UTC"},
        },
        "operations": {
            "list_reservations": {
                "method": "GET",
                "path": "/reservations",
                "params": {},
                "pagination": {
                    "strategy": "page",
                    "cursor_param": None,
                    "offset_param": None,
                    "page_param": "pageNumber",
                    "limit_param": "pageSize",
                    "next_path": None,
                    "items_path": "$.data",
                    "total_path": "$.total",
                    "stop_when": ["empty_page"],
                },
            }
        },
        "entities": {
            "Reservation": {
                "from": "list_reservations",
                "key": "$.reservation_id",
                "fields": {
                    "reservation_id": {"path": "$.reservation_id"},
                    "guest_name": {"path": "$.guest_name"},
                },
            }
        },
        "capabilities": {"read": ["Reservation"]},
        "health": {"canary": {"every": "60s", "operation": "list_reservations"}},
    }


def _wire_reservation(i: int, **overrides: Any) -> dict[str, Any]:
    rec = {"reservation_id": f"R{i:04d}", "guest_name": f"Guest {i}"}
    rec.update(overrides)
    return rec


def _summary(
    operation_results: dict[str, Any],
    outcome: str = "Degraded",
    unmet: list[str] | None = None,
    observed: dict[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "outcome": outcome,
        "unmet_criteria": unmet if unmet is not None else ["no_loss"],
        "data_readiness": {"freshness": "current", "completeness": "partial"},
        "fingerprint_id": "wire-test",
        "operation_results": operation_results,
        "records_observed": observed or {"Reservation": 1},
        "canonical_records": {},
        "blocked_records": {},
        "invariant_results": [],
        "checkpoint_values": {},
    }


def _zero_usage(result: Any) -> None:
    assert result.model_usage["frontier_tokens"] == 0
    assert result.model_usage["small_tokens"] == 0
    assert result.model_usage["frontier_calls"] == 0
    assert result.model_usage["small_calls"] == 0


def _runtime_delta(
    op: str = "list_reservations",
    strategy: str = "page",
    prev_pages: int = 5,
    prev_stop: str = "total_reached",
    cur_pages: int = 1,
    cur_stop: str = "no_next_token",
) -> FingerprintDelta:
    """A delta like the real path provides: strategies from the spec (same on
    both sides — the fingerprint never shows a strategy flip), runtime
    counters differing. Rules must therefore rely on wire evidence."""
    shape = {
        "strategy": strategy,
        "pages_fetched": 0,
        "stop_reason": None,
    }
    prev = dict(shape, pages_fetched=prev_pages, stop_reason=prev_stop)
    cur = dict(shape, pages_fetched=cur_pages, stop_reason=cur_stop)
    return FingerprintDelta(
        fingerprint_changed=True,
        pagination_changed=True,
        keys_changed=False,
        types_changed=False,
        changed_entities=[],
        pagination_details={op: {"previous": prev, "current": cur}},
        keys_details={},
        types_details={},
    )


def _nulled_types_delta() -> FingerprintDelta:
    """What the real path sees on a wire rename: the canonical record keeps
    the old field name (mapped to null), so the canonical-types fingerprint
    suggests a type change. The wire rename evidence must win."""
    prev = {"Reservation": {"reservation_id": "str", "guest_name": "str"}}
    cur = {"Reservation": {"reservation_id": "str", "guest_name": "null"}}
    return FingerprintDelta(
        fingerprint_changed=True,
        pagination_changed=False,
        keys_changed=False,
        types_changed=True,
        changed_entities=["Reservation"],
        pagination_details={},
        keys_details={},
        types_details={"Reservation": {"previous": prev["Reservation"], "current": cur["Reservation"]}},
    )


def test_page_to_cursor_from_wire_only() -> None:
    """Faulted wire (cursor envelope, no total) -> page_to_cursor, 0 calls."""
    spec = _page_spec()
    records = [_wire_reservation(i) for i in range(3)]
    results = {
        "list_reservations": OperationResult(
            operation="list_reservations",
            records=records,
            pages_fetched=1,
            raw_pages=[
                {
                    "success": True,
                    "data": records,
                    "count": 3,
                    "next_cursor": "cur_003",
                }
            ],
            stop_reason="no_next_token",
        )
    }
    signals = signals_from_sync(_summary(results), spec=spec)
    wire_sigs = [s for s in signals if "wire_evidence" in (s.details or {})]
    assert wire_sigs, "detector must attach wire pagination evidence"
    assert any(
        s.details.get("cursor_field") == "next_cursor" for s in wire_sigs
    ), [s.details for s in wire_sigs]

    classification = classify(signals)
    assert classification.drift_class == "PAGINATION_CHANGE"

    result = RepairController().handle_from_signals(
        spec=spec, signals=signals, fingerprint_delta=_runtime_delta(strategy="page")
    )
    assert result.decision == "propose"
    assert result.rule_id == "page_to_cursor"
    _zero_usage(result)
    assert result.candidate_patch
    by_path = {p["path"]: p["value"] for p in result.candidate_patch}
    assert by_path["/operations/list_reservations/pagination/strategy"] == "cursor"
    # The patch points next_path at the leaf the vendor actually serves;
    # cursor_param stays the request param the vendor reads.
    assert by_path["/operations/list_reservations/pagination/next_path"] == "$.next_cursor"
    assert by_path["/operations/list_reservations/pagination/cursor_param"] == "cursor"


def test_offset_to_cursor_from_wire_only() -> None:
    """Offset spec + cursor envelope on the wire -> offset_to_cursor."""
    spec = _page_spec()
    spec["operations"]["list_reservations"]["pagination"] = {
        "strategy": "offset",
        "cursor_param": None,
        "offset_param": "offset",
        "limit_param": "limit",
        "next_path": None,
        "items_path": "$.data",
        "stop_when": ["empty_page"],
    }
    records = [_wire_reservation(i) for i in range(2)]
    results = {
        "list_reservations": OperationResult(
            operation="list_reservations",
            records=records,
            pages_fetched=1,
            raw_pages=[
                {"success": True, "data": records, "count": 2, "next_cursor": "opaque"}
            ],
            stop_reason="no_next_token",
        )
    }
    signals = signals_from_sync(_summary(results), spec=spec)
    assert classify(signals).drift_class == "PAGINATION_CHANGE"
    result = RepairController().handle_from_signals(
        spec=spec, signals=signals, fingerprint_delta=_runtime_delta(strategy="offset", prev_stop="empty_page", cur_stop="no_next_token")
    )
    assert result.decision == "propose"
    assert result.rule_id == "offset_to_cursor"
    _zero_usage(result)


def test_rename_from_wire_only_on_complete_sync() -> None:
    """Wire rename with a Complete outcome still detects + repairs.

    The canonical-types fingerprint cannot show this (unmapped keys never
    reach canonical records); only the wire-schema observation carries it.
    """
    spec = _page_spec()
    wire = [
        {"reservation_id": f"R{i:04d}", "primaryOccupant": f"Guest {i}"}
        for i in range(3)
    ]
    results = {
        "list_reservations": OperationResult(
            operation="list_reservations",
            records=wire,
            pages_fetched=5,
            raw_pages=[
                {"success": True, "data": wire, "count": 3, "total": 3}
            ],
            stop_reason="total_reached",
        )
    }
    summary = _summary(results, outcome="Complete", unmet=[], observed={"Reservation": 3})
    signals = signals_from_sync(summary, spec=spec)
    wire_sigs = [s for s in signals if s.kind == "keys_changed"]
    assert wire_sigs, "detector must emit wire schema evidence"
    details = wire_sigs[0].details
    assert details["missing"] == ["guest_name"]
    assert [a["key"] for a in details["added"]] == ["primaryOccupant"]

    classification = classify(signals)
    assert classification.drift_class == "SCHEMA_RENAME"

    result = RepairController().handle_from_signals(
        spec=spec, signals=signals, fingerprint_delta=_nulled_types_delta()
    )
    assert result.decision == "propose"
    assert result.rule_id == "rename_field"
    _zero_usage(result)
    assert result.candidate_patch == [
        {
            "op": "replace",
            "path": "/entities/Reservation/fields/guest_name/path",
            "value": "$.primaryOccupant",
        }
    ]


def test_cursor_next_path_shift_from_wire_only() -> None:
    """Cursor spec whose next_path reads null + new leaf on wire -> next_path fix."""
    spec = _page_spec()
    spec["operations"]["list_reservations"]["pagination"] = {
        "strategy": "cursor",
        "cursor_param": "cursor",
        "next_path": "$.next_cursor",
        "items_path": "$.data",
        "stop_when": ["null_cursor", "repeated_cursor"],
    }
    records = [_wire_reservation(i) for i in range(2)]
    results = {
        "list_reservations": OperationResult(
            operation="list_reservations",
            records=records,
            pages_fetched=1,
            raw_pages=[
                {"success": True, "data": records, "count": 2, "NextCursor": "tok_1"}
            ],
            stop_reason="no_next_token",
        )
    }
    signals = signals_from_sync(_summary(results), spec=spec)
    assert classify(signals).drift_class == "PAGINATION_CHANGE"
    result = RepairController().handle_from_signals(
        spec=spec, signals=signals, fingerprint_delta=_runtime_delta(strategy="cursor")
    )
    assert result.decision == "propose"
    assert result.rule_id == "cursor_next_path_shift"
    _zero_usage(result)
    assert result.candidate_patch == [
        {
            "op": "replace",
            "path": "/operations/list_reservations/pagination/next_path",
            "value": "$.NextCursor",
        }
    ]


def test_nested_items_shift_from_wire_only() -> None:
    """items_path extracts nothing + one candidate array -> items_path fix."""
    spec = _page_spec()
    results = {
        "list_reservations": OperationResult(
            operation="list_reservations",
            records=[],
            pages_fetched=1,
            raw_pages=[
                {
                    "success": True,
                    "payload": {"records": [_wire_reservation(0)]},
                    "count": 1,
                    "total": 1,
                }
            ],
            stop_reason="empty_page_implicit",
        )
    }
    signals = signals_from_sync(
        _summary(results, observed={"Reservation": 0}), spec=spec
    )
    classification = classify(signals)
    assert classification.drift_class == "PAGINATION_CHANGE"
    result = RepairController().handle_from_signals(
        spec=spec, signals=signals, fingerprint_delta=_runtime_delta(cur_stop="empty_page_implicit")
    )
    assert result.rule_id == "nested_path_shift"
    assert result.decision == "propose"
    _zero_usage(result)
    assert result.candidate_patch == [
        {
            "op": "replace",
            "path": "/operations/list_reservations/pagination/items_path",
            "value": "$.payload.records",
        }
    ]


def test_baseline_sync_emits_no_wire_evidence() -> None:
    """Healthy sync (wire matches spec) stays quiet: no wire signals at all."""
    spec = _page_spec()
    records = [_wire_reservation(i) for i in range(2)]
    results = {
        "list_reservations": OperationResult(
            operation="list_reservations",
            records=records,
            pages_fetched=1,
            raw_pages=[
                {"success": True, "data": records, "count": 2, "total": 2}
            ],
            stop_reason="total_reached",
        )
    }
    summary = _summary(results, outcome="Complete", unmet=[], observed={"Reservation": 2})
    signals = signals_from_sync(summary, spec=spec)
    assert signals == [], [f"{s.kind}: {s.details}" for s in signals]
