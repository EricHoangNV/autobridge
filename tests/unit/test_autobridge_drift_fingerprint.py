"""Tests for drift/fingerprint.py (P1.W4.1: keys, types, pagination_shape + delta)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from autobridge.drift.fingerprint import (
    _hash_string,
    compute_fingerprint,
    compute_fingerprint_id,
    diff_fingerprints,
    fingerprint_config,
    fingerprint_keys,
    fingerprint_pagination_shape,
    fingerprint_types,
)
from autobridge.runtime.interpreter import OperationResult


def _spec_dict(pagination: dict[str, Any] | None = None) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "spec_version": 1,
        "connector": {"id": "testconn", "vendor": "Test", "runtime_target": "cloud"},
        "source": {
            "base_url": "https://example.com",
            "auth": {"type": "bearer", "credential_ref": "TEST_TOKEN"},
            "rate_limit": {"max_rps": 5},
            "timezone_source": {"kind": "static", "value": "UTC"},
        },
        "operations": {
            "list_reservations": {
                "method": "GET",
                "path": "/reservations",
                "params": {},
                "pagination": pagination
                or {
                    "strategy": "cursor",
                    "cursor_param": "cursor",
                    "next_path": "$.meta.next_cursor",
                    "items_path": "$.data",
                    "stop_when": ["null_cursor"],
                },
            }
        },
        "entities": {"Reservation": {"from": "list_reservations", "key": "$.reservation_id", "fields": {"reservation_id": {"path": "$.reservation_id"}}}},
        "capabilities": {"read": ["Reservation"]},
        "health": {"canary": {"every": "60s", "operation": "list_reservations"}, "fingerprint": {}},
    }
    return spec


# ---------------------------------------------------------------------------
# fingerprint_config
# ---------------------------------------------------------------------------


def test_fingerprint_config_defaults() -> None:
    assert fingerprint_config({}) == {"keys": True, "types": True, "pagination_shape": True}
    assert fingerprint_config({"health": {"fingerprint": {"keys": False}}}) == {"keys": False, "types": True, "pagination_shape": True}
    assert fingerprint_config({"health": {"fingerprint": "bad"}}) == {"keys": True, "types": True, "pagination_shape": True}
    assert fingerprint_config({"health": "bad"}) == {"keys": True, "types": True, "pagination_shape": True}
    assert fingerprint_config({"health": {"fingerprint": {"pagination_shape": False}}}) == {"keys": True, "types": True, "pagination_shape": False}


# ---------------------------------------------------------------------------
# _hash_string
# ---------------------------------------------------------------------------


def test_hash_string() -> None:
    assert len(_hash_string("hello")) == 12
    assert _hash_string("a") != _hash_string("b")
    assert _hash_string("") == _hash_string("")


# ---------------------------------------------------------------------------
# fingerprint_keys
# ---------------------------------------------------------------------------


def test_fingerprint_keys_empty() -> None:
    assert fingerprint_keys(None) == {}
    assert fingerprint_keys({}) == {}
    assert fingerprint_keys({"Reservation": []}) == {"Reservation": "empty"}


def test_fingerprint_keys_normal() -> None:
    recs = {"Reservation": [{"reservation_id": "R1"}, {"reservation_id": "R2"}]}
    out = fingerprint_keys(recs)
    assert "Reservation" in out
    assert len(out["Reservation"]) == 12
    # Deterministic: same input same hash
    assert fingerprint_keys(recs) == fingerprint_keys(recs)


def test_fingerprint_keys_fallback_first_value() -> None:
    # No reservation_id/charge_id/id/key → fallback to first value
    recs = {"Entity": [{"foo": "bar"}, {"foo": "baz"}]}
    out = fingerprint_keys(recs)
    assert "Entity" in out
    assert out["Entity"] != "empty"


def test_fingerprint_keys_charge_id() -> None:
    recs = {"FolioCharge": [{"charge_id": "C1"}, {"reservation_id": "R1"}]}
    out = fingerprint_keys(recs)
    assert out["FolioCharge"] != "empty"


def test_fingerprint_keys_null_key_skipped() -> None:
    recs = {"Reservation": [{"reservation_id": None, "charge_id": None, "id": None}]}
    # All tried keys are None → fallback to first value (None is skipped, but fallback uses next iter value which is None? Actually next iter value is None, stringified "None")
    out = fingerprint_keys(recs)
    assert out["Reservation"] != ""


def test_fingerprint_keys_sorted_stability() -> None:
    # Order of input should not affect sorted sample
    recs1 = {"R": [{"reservation_id": "R2"}, {"reservation_id": "R1"}]}
    recs2 = {"R": [{"reservation_id": "R1"}, {"reservation_id": "R2"}]}
    assert fingerprint_keys(recs1) == fingerprint_keys(recs2)


# ---------------------------------------------------------------------------
# fingerprint_types
# ---------------------------------------------------------------------------


def test_fingerprint_types_empty() -> None:
    assert fingerprint_types(None) == {}
    assert fingerprint_types({}) == {}
    assert fingerprint_types({"Reservation": []}) == {}


def test_fingerprint_types_all_types() -> None:
    rec = {"Reservation": [{"bool_f": True, "int_f": 1, "float_f": 3.14, "dec_f": Decimal("10.00"), "str_f": "hi", "null_f": None}]}
    out = fingerprint_types(rec)
    assert out["Reservation"]["bool_f"] == "bool"
    assert out["Reservation"]["int_f"] == "int"
    assert out["Reservation"]["float_f"] == "float"
    assert out["Reservation"]["dec_f"] == "Decimal"
    assert out["Reservation"]["str_f"] == "str"
    assert out["Reservation"]["null_f"] == "null"


def test_fingerprint_types_int_not_bool() -> None:
    # bool is subclass of int, ensure bool detection precedence
    rec = {"R": [{"a": True, "b": 1}]}
    out = fingerprint_types(rec)
    assert out["R"]["a"] == "bool"
    assert out["R"]["b"] == "int"


def test_fingerprint_types_unknown_type() -> None:
    class Custom:
        pass

    rec = {"R": [{"c": Custom()}]}
    out = fingerprint_types(rec)
    assert out["R"]["c"] == "Custom"


def test_fingerprint_types_first_record_only() -> None:
    rec = {"R": [{"a": "str"}, {"a": 123}]}
    out = fingerprint_types(rec)
    assert out["R"]["a"] == "str"


# ---------------------------------------------------------------------------
# fingerprint_pagination_shape
# ---------------------------------------------------------------------------


def test_fingerprint_pagination_shape_basic() -> None:
    spec = _spec_dict()
    results = {"list_reservations": OperationResult(operation="list_reservations", records=[], pages_fetched=2, stop_reason="null_cursor", repeated_cursor=False)}
    shape = fingerprint_pagination_shape(spec, results)
    assert "list_reservations" in shape
    assert shape["list_reservations"]["strategy"] == "cursor"
    assert shape["list_reservations"]["pages_fetched"] == 2
    assert shape["list_reservations"]["stop_reason"] == "null_cursor"
    assert shape["list_reservations"]["stop_when"] == ["null_cursor"]


def test_fingerprint_pagination_shape_sorted_stop_when() -> None:
    spec = _spec_dict({"strategy": "cursor", "cursor_param": "cursor", "next_path": "$.next", "items_path": "$.data", "stop_when": ["empty_page", "null_cursor"]})
    results: dict[str, Any] = {}
    shape = fingerprint_pagination_shape(spec, results)
    assert shape["list_reservations"]["stop_when"] == ["empty_page", "null_cursor"]


def test_fingerprint_pagination_shape_missing_results() -> None:
    spec = _spec_dict()
    shape = fingerprint_pagination_shape(spec, {})
    assert shape["list_reservations"]["pages_fetched"] == 0
    assert shape["list_reservations"]["stop_reason"] is None


def test_fingerprint_pagination_shape_dict_results() -> None:
    # Support plain dict operation results
    spec = _spec_dict()
    results = {"list_reservations": {"pages_fetched": 3, "stop_reason": "empty_page", "repeated_cursor": True}}
    shape = fingerprint_pagination_shape(spec, results)
    assert shape["list_reservations"]["pages_fetched"] == 3
    assert shape["list_reservations"]["repeated_cursor"] is True


def test_fingerprint_pagination_shape_non_dict_spec() -> None:
    shape = fingerprint_pagination_shape({}, {})
    assert shape == {}
    shape2 = fingerprint_pagination_shape({"operations": "bad"}, {})
    assert shape2 == {}


def test_fingerprint_pagination_shape_missing_pagination() -> None:
    spec = {"operations": {"op": {"method": "GET", "path": "/x"}}}
    shape = fingerprint_pagination_shape(spec, {})
    assert shape["op"]["strategy"] is None
    assert shape["op"]["pages_fetched"] == 0


# ---------------------------------------------------------------------------
# compute_fingerprint / compute_fingerprint_id
# ---------------------------------------------------------------------------


def test_compute_fingerprint_basic() -> None:
    spec = _spec_dict()
    results = {"list_reservations": OperationResult(operation="list_reservations", records=[{"reservation_id": "R1"}], pages_fetched=1, stop_reason="null_cursor")}
    fp = compute_fingerprint(spec, results, {"Reservation": [{"reservation_id": "R1"}]})
    assert len(fp.fingerprint_id) == 16
    assert fp.spec_signature
    assert fp.pagination_shape is not None
    assert fp.keys_sample is not None
    assert fp.types is not None
    assert fp.config == {"keys": True, "types": True, "pagination_shape": True}


def test_compute_fingerprint_id_stable() -> None:
    spec = _spec_dict()
    results = {"list_reservations": OperationResult(operation="list_reservations", records=[{"reservation_id": "R1"}], pages_fetched=1, stop_reason="null_cursor")}
    id1 = compute_fingerprint_id(spec, results, {"Reservation": [{"reservation_id": "R1"}]})
    id2 = compute_fingerprint_id(spec, results, {"Reservation": [{"reservation_id": "R1"}]})
    assert id1 == id2
    assert len(id1) == 16


def test_compute_fingerprint_pagination_change() -> None:
    spec1 = _spec_dict({"strategy": "cursor", "cursor_param": "cursor", "next_path": "$.next", "items_path": "$.data", "stop_when": ["null_cursor"]})
    spec2 = _spec_dict({"strategy": "offset", "offset_param": "offset", "limit_param": "limit", "next_path": "$.next", "items_path": "$.data", "stop_when": ["empty_page"]})
    results = {"list_reservations": OperationResult(operation="list_reservations", records=[{"reservation_id": "R1"}], pages_fetched=1, stop_reason="null_cursor")}
    assert compute_fingerprint_id(spec1, results) != compute_fingerprint_id(spec2, results)


def test_compute_fingerprint_keys_change() -> None:
    spec = _spec_dict()
    results = {"list_reservations": OperationResult(operation="list_reservations", records=[], pages_fetched=1, stop_reason="null_cursor")}
    id1 = compute_fingerprint_id(spec, results, {"Reservation": [{"reservation_id": "R1"}]})
    id2 = compute_fingerprint_id(spec, results, {"Reservation": [{"reservation_id": "R99"}]})
    assert id1 != id2


def test_compute_fingerprint_types_change() -> None:
    spec = _spec_dict()
    results = {"list_reservations": OperationResult(operation="list_reservations", records=[], pages_fetched=1, stop_reason="null_cursor")}
    id1 = compute_fingerprint_id(spec, results, {"Reservation": [{"reservation_id": "R1", "amount": Decimal("10.00")}]})
    id2 = compute_fingerprint_id(spec, results, {"Reservation": [{"reservation_id": "R1", "amount": "10.00"}]})
    assert id1 != id2


def test_compute_fingerprint_no_pagination_shape() -> None:
    spec = _spec_dict()
    spec["health"] = {"canary": {"every": "60s", "operation": "list_reservations"}, "fingerprint": {"keys": True, "types": True, "pagination_shape": False}}
    results = {"list_reservations": OperationResult(operation="list_reservations", records=[{"reservation_id": "R1"}], pages_fetched=1, stop_reason="null_cursor")}
    fp = compute_fingerprint(spec, results, {"Reservation": [{"reservation_id": "R1"}]})
    assert fp.pagination_shape is None
    assert fp.keys_sample is not None
    assert len(fp.fingerprint_id) == 16


def test_compute_fingerprint_keys_disabled() -> None:
    spec = _spec_dict()
    spec["health"] = {"canary": {"every": "60s", "operation": "list_reservations"}, "fingerprint": {"keys": False, "types": True, "pagination_shape": True}}
    results = {"list_reservations": OperationResult(operation="list_reservations", records=[], pages_fetched=1, stop_reason="null_cursor")}
    fp = compute_fingerprint(spec, results, {"Reservation": [{"reservation_id": "R1"}]})
    assert fp.keys_sample is None


def test_compute_fingerprint_types_disabled() -> None:
    spec = _spec_dict()
    spec["health"] = {"canary": {"every": "60s", "operation": "list_reservations"}, "fingerprint": {"keys": True, "types": False, "pagination_shape": True}}
    results = {"list_reservations": OperationResult(operation="list_reservations", records=[], pages_fetched=1, stop_reason="null_cursor")}
    fp = compute_fingerprint(spec, results, {"Reservation": [{"reservation_id": "R1"}]})
    assert fp.types is None


def test_compute_fingerprint_empty_canonical() -> None:
    spec = _spec_dict()
    results = {"list_reservations": OperationResult(operation="list_reservations", records=[], pages_fetched=1, stop_reason="null_cursor")}
    fp = compute_fingerprint(spec, results, {"Reservation": []})
    # keys_sample should have "empty" for empty entity
    assert fp.keys_sample == {"Reservation": "empty"}
    assert fp.types is None


def test_compute_fingerprint_no_canonical() -> None:
    spec = _spec_dict()
    results = {"list_reservations": OperationResult(operation="list_reservations", records=[], pages_fetched=1, stop_reason="null_cursor")}
    fp = compute_fingerprint(spec, results, None)
    assert fp.keys_sample is None
    assert fp.types is None


def test_compute_fingerprint_pages_drift() -> None:
    spec = _spec_dict()
    results1 = {"list_reservations": OperationResult(operation="list_reservations", records=[], pages_fetched=1, stop_reason="null_cursor")}
    results2 = {"list_reservations": OperationResult(operation="list_reservations", records=[], pages_fetched=5, stop_reason="repeated_cursor", repeated_cursor=True)}
    assert compute_fingerprint_id(spec, results1) != compute_fingerprint_id(spec, results2)


# ---------------------------------------------------------------------------
# diff_fingerprints
# ---------------------------------------------------------------------------


def test_diff_fingerprints_no_previous() -> None:
    spec = _spec_dict()
    results = {"list_reservations": OperationResult(operation="list_reservations", records=[], pages_fetched=1, stop_reason="null_cursor")}
    cur = compute_fingerprint(spec, results, {"Reservation": [{"reservation_id": "R1"}]})
    delta = diff_fingerprints(None, cur)
    assert delta.fingerprint_changed is True
    assert delta.pagination_changed is False
    assert delta.keys_changed is False


def test_diff_fingerprints_same() -> None:
    spec = _spec_dict()
    results = {"list_reservations": OperationResult(operation="list_reservations", records=[], pages_fetched=1, stop_reason="null_cursor")}
    cur = compute_fingerprint(spec, results, {"Reservation": [{"reservation_id": "R1"}]})
    delta = diff_fingerprints(cur, cur)
    assert delta.fingerprint_changed is False
    assert delta.pagination_changed is False
    assert delta.keys_changed is False
    assert delta.types_changed is False
    assert delta.changed_entities == []


def test_diff_fingerprints_pagination_changed() -> None:
    spec = _spec_dict()
    r1 = {"list_reservations": OperationResult(operation="list_reservations", records=[], pages_fetched=1, stop_reason="null_cursor")}
    r2 = {"list_reservations": OperationResult(operation="list_reservations", records=[], pages_fetched=2, stop_reason="repeated_cursor", repeated_cursor=True)}
    prev = compute_fingerprint(spec, r1, {"Reservation": [{"reservation_id": "R1"}]})
    cur = compute_fingerprint(spec, r2, {"Reservation": [{"reservation_id": "R1"}]})
    delta = diff_fingerprints(prev, cur)
    assert delta.fingerprint_changed
    assert delta.pagination_changed
    assert "list_reservations" in delta.pagination_details
    assert "previous" in delta.pagination_details["list_reservations"]


def test_diff_fingerprints_keys_changed() -> None:
    spec = _spec_dict()
    results = {"list_reservations": OperationResult(operation="list_reservations", records=[], pages_fetched=1, stop_reason="null_cursor")}
    prev = compute_fingerprint(spec, results, {"Reservation": [{"reservation_id": "R1"}]})
    cur = compute_fingerprint(spec, results, {"Reservation": [{"reservation_id": "R99"}]})
    delta = diff_fingerprints(prev, cur)
    assert delta.keys_changed
    assert "Reservation" in delta.keys_details
    assert delta.changed_entities == ["Reservation"]


def test_diff_fingerprints_types_changed() -> None:
    spec = _spec_dict()
    results = {"list_reservations": OperationResult(operation="list_reservations", records=[], pages_fetched=1, stop_reason="null_cursor")}
    prev = compute_fingerprint(spec, results, {"Reservation": [{"reservation_id": "R1", "amount": Decimal("10.00")}]})
    cur = compute_fingerprint(spec, results, {"Reservation": [{"reservation_id": "R1", "amount": "10.00"}]})
    delta = diff_fingerprints(prev, cur)
    assert delta.types_changed
    assert "Reservation" in delta.types_details


def test_diff_fingerprints_string_previous() -> None:
    spec = _spec_dict()
    results = {"list_reservations": OperationResult(operation="list_reservations", records=[], pages_fetched=1, stop_reason="null_cursor")}
    cur = compute_fingerprint(spec, results, {"Reservation": [{"reservation_id": "R1"}]})
    delta = diff_fingerprints("abc", cur)
    assert delta.fingerprint_changed is True


def test_diff_fingerprints_dict_previous() -> None:
    prev_dict = {"fingerprint_id": "old", "pagination_shape": {"x": 1}, "keys_sample": {"Reservation": "old"}, "types": {"Reservation": {"a": "str"}}}
    cur_dict = {"fingerprint_id": "new", "pagination_shape": {"x": 2}, "keys_sample": {"Reservation": "new"}, "types": {"Reservation": {"a": "int"}}}
    delta = diff_fingerprints(prev_dict, cur_dict)
    assert delta.pagination_changed
    assert delta.keys_changed
    assert delta.types_changed


def test_diff_fingerprints_spec_signature_change() -> None:
    spec1 = _spec_dict({"strategy": "cursor", "cursor_param": "cursor", "next_path": "$.next", "items_path": "$.data", "stop_when": ["null_cursor"]})
    spec2 = _spec_dict({"strategy": "offset", "offset_param": "offset", "limit_param": "limit", "next_path": "$.next", "items_path": "$.data", "stop_when": ["empty_page"]})
    results: dict[str, Any] = {}
    prev = compute_fingerprint(spec1, results)
    cur = compute_fingerprint(spec2, results)
    delta = diff_fingerprints(prev, cur)
    assert delta.fingerprint_changed
    # pagination_changed should be True because pagination_shape differs
    assert delta.pagination_changed


def test_diff_fingerprints_missing_keys_types() -> None:
    # One side has keys, other missing (toggled config)
    prev = {"fingerprint_id": "a", "keys_sample": {"R": "x"}, "types": None, "pagination_shape": None}
    cur = {"fingerprint_id": "b", "keys_sample": None, "types": None, "pagination_shape": None}
    delta = diff_fingerprints(prev, cur)
    assert delta.keys_changed


def test_diff_fingerprints_fingerprint_id_only() -> None:
    prev = {"fingerprint_id": "abc"}
    cur = {"fingerprint_id": "abc"}
    delta = diff_fingerprints(prev, cur)
    assert delta.fingerprint_changed is False
    prev2 = {"fingerprint_id": "abc"}
    cur2 = {"fingerprint_id": "def"}
    delta2 = diff_fingerprints(prev2, cur2)
    assert delta2.fingerprint_changed is True


def test_diff_fingerprints_changed_entities_union() -> None:
    spec = _spec_dict()
    results = {"list_reservations": OperationResult(operation="list_reservations", records=[], pages_fetched=1, stop_reason="null_cursor")}
    prev = compute_fingerprint(spec, results, {"Reservation": [{"reservation_id": "R1", "amount": Decimal("1.00")}]})
    cur = compute_fingerprint(spec, results, {"Reservation": [{"reservation_id": "R2", "amount": "2"}]})
    delta = diff_fingerprints(prev, cur)
    assert delta.keys_changed and delta.types_changed
    assert delta.changed_entities == ["Reservation"]


def test_fingerprint_pagination_shape_covers_body_cursor_fields() -> None:
    """REQ-020 item 6: body_cursor request paths are fingerprinted for drift."""
    from autobridge.drift.fingerprint import fingerprint_pagination_shape

    spec = _spec_dict(
        {
            "strategy": "body_cursor",
            "cursor_path": "Limitation.Cursor",
            "count_path": "Limitation.Count",
            "count_value": 100,
            "next_path": "$.Cursor",
            "items_path": "$.Reservations",
            "stop_when": ["null_cursor", "empty_page"],
        }
    )
    shape = fingerprint_pagination_shape(spec, {})
    assert shape["list_reservations"]["strategy"] == "body_cursor"
    assert shape["list_reservations"]["cursor_path"] == "Limitation.Cursor"
    assert shape["list_reservations"]["count_path"] == "Limitation.Count"
    assert shape["list_reservations"]["count_value"] == 100
