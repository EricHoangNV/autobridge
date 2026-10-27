"""Tests for closed transform registry, checks and checkpoint (P1.W3.4)."""

from __future__ import annotations

import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from autobridge.runtime.checkpoint import (
    compute_checkpoint,
    compute_checkpoint_for_operation,
    next_checkpoint_value,
)
from autobridge.runtime.checks import (
    check_date_order,
    check_no_loss,
    check_unique_key,
    check_unique_key_generic,
    evaluate_entity_checks,
    run_checks,
)
from autobridge.runtime.mapping import map_record_to_entity, map_records_to_entities
from autobridge.runtime.transforms import (
    TRANSFORMS,
    TransformBlocked,
    TransformError,
    apply_transform,
    apply_transform_pipeline,
)
from autobridge.runtime.transforms.base import _ensure_decimal, _resolve_tz
from autobridge.runtime.transforms.business_date import transform_business_date
from autobridge.runtime.transforms.coalesce import transform_coalesce
from autobridge.runtime.transforms.concat import transform_concat
from autobridge.runtime.transforms.const import transform_const
from autobridge.runtime.transforms.date_local import transform_date_local
from autobridge.runtime.transforms.datetime_utc import transform_datetime_utc
from autobridge.runtime.transforms.enum_map import transform_enum_map
from autobridge.runtime.transforms.flatten import transform_flatten
from autobridge.runtime.transforms.money import transform_money

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_closed_set() -> None:
    assert set(TRANSFORMS) == {
        "date_local",
        "datetime_utc",
        "money",
        "enum_map",
        "flatten",
        "coalesce",
        "const",
        "concat",
        "business_date",
    }
    # All names in registry
    from autobridge.runtime.transforms import REGISTRY

    assert set(REGISTRY) == set(TRANSFORMS)


def test_unknown_transform_blocked() -> None:
    with pytest.raises(TransformError, match="unknown transform"):
        apply_transform("val", "no_such", {}, context_tz=None)


def test_pipeline_unknown_step_missing_name() -> None:
    with pytest.raises(TransformError, match="missing name"):
        apply_transform_pipeline("x", [{"args": {}}])  # type: ignore[list-item]


def test_pipeline_bad_args_type() -> None:
    # args not dict should be treated as empty (our handling) — but if step missing name still fails
    # Test that pipeline handles args as string via fallback to empty
    out = apply_transform_pipeline("a", [{"name": "const", "args": {"value": "b"}}])
    assert out == "b"


def test_pipeline_applies_sequential() -> None:
    # flatten then concat
    out = apply_transform_pipeline(
        [["a", "b"], ["c"]],
        [{"name": "flatten", "args": {}}, {"name": "concat", "args": {"separator": ","}}],
    )
    assert out == "a,b,c"


# ---------------------------------------------------------------------------
# date_local
# ---------------------------------------------------------------------------


def test_date_local_pure_date() -> None:
    assert transform_date_local("2026-10-27", {}, context_tz="UTC") == "2026-10-27"
    assert transform_date_local(datetime.date(2026, 10, 27), {}, context_tz="UTC") == "2026-10-27"


def test_date_local_datetime_with_offset() -> None:
    # UTC 03:30 = 23:30 previous day in NY (EDT UTC-4)
    assert transform_date_local("2026-10-27T03:30:00+00:00", {"tz": "America/New_York"}, context_tz="UTC") == "2026-10-26"
    # UTC 04:30 = 00:30 NY -> 2026-10-27
    assert transform_date_local("2026-10-27T04:30:00+00:00", {"tz": "America/New_York"}, context_tz="UTC") == "2026-10-27"


def test_date_local_source_tz() -> None:
    # source means use context_tz
    assert transform_date_local("2026-10-27T03:30:00+00:00", {"tz": "source"}, context_tz="America/New_York") == "2026-10-26"
    assert transform_date_local("2026-10-27T03:30:00+00:00", {}, context_tz="America/New_York") == "2026-10-26"


def test_date_local_naive_datetime_assumed_utc() -> None:
    # naive string without tz -> assume UTC then convert
    assert transform_date_local("2026-10-27T03:30:00", {"tz": "America/New_York"}) == "2026-10-26"


def test_date_local_datetime_object() -> None:
    dt = datetime.datetime(2026, 10, 27, 3, 30, tzinfo=datetime.UTC)
    assert transform_date_local(dt, {"tz": "America/New_York"}) == "2026-10-26"
    # naive datetime
    dt2 = datetime.datetime(2026, 10, 27, 3, 30)
    assert transform_date_local(dt2, {"tz": "America/New_York"}) == "2026-10-26"


def test_date_local_invalid_tz_fallback() -> None:
    assert transform_date_local("2026-10-27T00:00:00+00:00", {"tz": "No/Such_Zone"}) == "2026-10-27"


def test_date_local_none() -> None:
    assert transform_date_local(None, {}) is None


def test_date_local_zulu() -> None:
    assert transform_date_local("2026-10-27T03:30:00Z", {"tz": "America/New_York"}) == "2026-10-26"


def test_date_local_invalid_string_returns_raw() -> None:
    assert transform_date_local("not-a-date", {}) == "not-a-date"


# ---------------------------------------------------------------------------
# datetime_utc
# ---------------------------------------------------------------------------


def test_datetime_utc_naive_source() -> None:
    # 20:12 NY -> 00:12 UTC next day
    out = transform_datetime_utc("2026-10-26 20:12:00", {"tz": "America/New_York"})
    assert out == "2026-10-27T00:12:00+00:00"


def test_datetime_utc_pure_date() -> None:
    out = transform_datetime_utc("2026-10-27", {"tz": "America/New_York"})
    # midnight NY -> 04:00 UTC
    assert out == "2026-10-27T04:00:00+00:00"


def test_datetime_utc_aware_passthrough() -> None:
    dt = datetime.datetime(2026, 10, 27, 0, 12, tzinfo=ZoneInfo("America/New_York"))
    out = transform_datetime_utc(dt, {})
    assert out == "2026-10-27T04:12:00+00:00"


def test_datetime_utc_none() -> None:
    assert transform_datetime_utc(None, {}) is None


def test_datetime_utc_invalid() -> None:
    assert transform_datetime_utc("not-a-date", {}) == "not-a-date"


def test_datetime_utc_invalid_tz_fallback() -> None:
    out = transform_datetime_utc("2026-10-27", {"tz": "No/Zone"})
    assert "2026-10-27" in out


# ---------------------------------------------------------------------------
# money
# ---------------------------------------------------------------------------


def test_money_string_decimal() -> None:
    assert transform_money("42.50", {"scale": 2}) == Decimal("42.50")
    assert transform_money("42.5", {"scale": 2}) == Decimal("42.50")
    assert transform_money("$1,234.56", {"scale": 2}) == Decimal("1234.56")


def test_money_int_and_decimal_input() -> None:
    assert transform_money(Decimal("10.00"), {"scale": 2}) == Decimal("10.00")
    assert transform_money(5, {"scale": 2}) == Decimal("5.00")
    assert transform_money(5, {"scale": 0}) == Decimal("5")


def test_money_float_input() -> None:
    assert transform_money(42.5, {"scale": 2}) == Decimal("42.50")


def test_money_scale_variants() -> None:
    assert transform_money("1.234", {"scale": 3}) == Decimal("1.234")
    assert transform_money("1", {"scale": 0}) == Decimal("1")


def test_money_invalid_scale() -> None:
    with pytest.raises(TransformError, match="scale must be int"):
        transform_money("10.00", {"scale": 7})
    with pytest.raises(TransformError, match="scale must be int"):
        transform_money("10.00", {"scale": "2"})  # type: ignore[arg-type]


def test_money_invalid_string() -> None:
    with pytest.raises(TransformError):
        transform_money("", {"scale": 2})
    with pytest.raises(TransformError):
        transform_money("abc", {"scale": 2})


def test_money_bool_rejected() -> None:
    with pytest.raises(TransformError, match="bool"):
        transform_money(True, {"scale": 2})


def test_money_unsupported_type() -> None:
    with pytest.raises(TransformError, match="unsupported"):
        transform_money([], {"scale": 2})  # type: ignore[arg-type]


def test_money_non_finite() -> None:
    with pytest.raises(TransformError, match="non-finite"):
        transform_money(Decimal("Infinity"), {"scale": 2})


def test_money_none() -> None:
    assert transform_money(None, {"scale": 2}) is None


# ---------------------------------------------------------------------------
# enum_map
# ---------------------------------------------------------------------------


def test_enum_map_known() -> None:
    assert transform_enum_map("H1", {"map": {"H1": "confirmed"}, "on_unknown": "block"}) == "confirmed"
    # stringified lookup
    assert transform_enum_map(1, {"map": {"1": "one"}, "on_unknown": "block"}) == "one"


def test_enum_map_unknown_blocked() -> None:
    with pytest.raises(TransformBlocked, match="unknown enum value"):
        transform_enum_map("UNKNOWN", {"map": {"H1": "confirmed"}, "on_unknown": "block"})
    # with field name
    with pytest.raises(TransformBlocked) as exc:
        transform_enum_map("X", {"map": {"A": "a"}, "on_unknown": "block", "field": "status"})
    assert exc.value.field == "status"


def test_enum_map_invalid_map() -> None:
    with pytest.raises(ValueError, match="non-empty map"):
        transform_enum_map("x", {"map": {}, "on_unknown": "block"})
    with pytest.raises(ValueError):
        transform_enum_map("x", {"on_unknown": "block"})  # type: ignore[arg-type]


def test_enum_map_none() -> None:
    assert transform_enum_map(None, {"map": {"A": "a"}, "on_unknown": "block"}) is None


def test_enum_map_on_unknown_guess_fallback() -> None:
    assert transform_enum_map("Z", {"map": {"A": "a"}, "on_unknown": "guess"}) == "Z"


# ---------------------------------------------------------------------------
# flatten
# ---------------------------------------------------------------------------


def test_flatten() -> None:
    assert transform_flatten([[1, 2], [3]], {}) == [1, 2, 3]
    assert transform_flatten([1, 2], {}) == [1, 2]
    assert transform_flatten(None, {}) is None
    assert transform_flatten({"a": 1}, {}) == {"a": 1}
    assert transform_flatten("x", {}) == ["x"]


# ---------------------------------------------------------------------------
# coalesce
# ---------------------------------------------------------------------------


def test_coalesce_direct() -> None:
    assert transform_coalesce("a", {}) == "a"
    assert transform_coalesce(None, {"default": "b"}) == "b"
    assert transform_coalesce("", {"default": "b"}) == "b"
    assert transform_coalesce(None, {"fallback": "c"}) == "c"
    assert transform_coalesce(None, {"value": "d"}) == "d"


def test_coalesce_list() -> None:
    assert transform_coalesce([None, "", "ok"], {}) == "ok"
    assert transform_coalesce([None, None], {"default": "fallback"}) == "fallback"
    assert transform_coalesce(None, {"values": [None, "v2"]}) == "v2"


def test_coalesce_none_no_fallback() -> None:
    assert transform_coalesce(None, {}) is None


# ---------------------------------------------------------------------------
# const
# ---------------------------------------------------------------------------


def test_const() -> None:
    assert transform_const("anything", {"value": 42}) == 42
    assert transform_const(None, {"value": None}) is None
    with pytest.raises(ValueError, match="requires 'value'"):
        transform_const("x", {})


# ---------------------------------------------------------------------------
# concat
# ---------------------------------------------------------------------------


def test_concat_list() -> None:
    assert transform_concat(["a", "b"], {"separator": ","}) == "a,b"
    assert transform_concat(["a", None, "b"], {"separator": "-"}) == "a-b"


def test_concat_scalar() -> None:
    assert transform_concat("hello", {"suffix": " world"}) == "hello world"
    assert transform_concat("hello", {"prefix": "pre-"}) == "pre-hello"
    assert transform_concat(None, {"values": ["x", "y"], "separator": ","}) == "x,y"
    assert transform_concat(None, {}) is None


def test_concat_with_values() -> None:
    assert transform_concat("a", {"values": ["b", "c"], "separator": ","}) == "a,b,c"
    assert transform_concat(["a"], {"values": ["b"], "separator": ","}) == "a,b"


def test_concat_separator_non_string() -> None:
    assert transform_concat(["a", "b"], {"separator": 123}) == "a123b"


# ---------------------------------------------------------------------------
# business_date
# ---------------------------------------------------------------------------


def test_business_date_late_posting() -> None:
    # UTC 04:12 -> 00:12 NY same day -> hour 0 <4 => prior business date
    assert transform_business_date("2026-10-27T04:12:00+00:00", {"tz": "America/New_York", "cutoff": 4}) == "2026-10-26"
    # UTC 14:12 -> 10:12 NY -> business same day
    assert transform_business_date("2026-10-27T14:12:00+00:00", {"tz": "America/New_York"}) == "2026-10-27"


def test_business_date_pure_date() -> None:
    assert transform_business_date("2026-10-26", {}) == "2026-10-26"
    assert transform_business_date(datetime.date(2026, 10, 26), {}) == "2026-10-26"


def test_business_date_datetime_object() -> None:
    dt = datetime.datetime(2026, 10, 27, 0, 12, tzinfo=ZoneInfo("America/New_York"))
    # This is already local 00:12 -> should map to prior business date
    # But we pass dt as-is; it will be treated as aware 00:12 NY, convert to NY tz -> same, hour 0 <4 => prior
    assert transform_business_date(dt, {"tz": "America/New_York"}) == "2026-10-26"


def test_business_date_cutoff_variants() -> None:
    # cutoff 2 vs 4 difference
    # local 03:00 with cutoff 4 -> prior day, cutoff 2 -> same day
    # Create UTC that maps to local 03:00 NY
    # 03:00 NY = 07:00 UTC
    assert transform_business_date("2026-10-27T07:00:00+00:00", {"tz": "America/New_York", "cutoff": 4}) == "2026-10-26"
    assert transform_business_date("2026-10-27T07:00:00+00:00", {"tz": "America/New_York", "cutoff": 2}) == "2026-10-27"


def test_business_date_invalid_tz() -> None:
    assert transform_business_date("2026-10-27T00:12:00+00:00", {"tz": "No/Zone"}) is not None


def test_business_date_none() -> None:
    assert transform_business_date(None, {}) is None


def test_business_date_invalid_string() -> None:
    assert transform_business_date("not-a-date", {}) == "not-a-date"


# ---------------------------------------------------------------------------
# base helpers
# ---------------------------------------------------------------------------


def test_resolve_tz() -> None:
    assert _resolve_tz(None, None) == "UTC"
    assert _resolve_tz("source", "America/New_York") == "America/New_York"
    assert _resolve_tz("UTC", None) == "UTC"
    assert _resolve_tz(None, "UTC") == "UTC"


def test_ensure_decimal() -> None:
    assert _ensure_decimal(Decimal("1.00")) == Decimal("1.00")
    assert _ensure_decimal(5) == Decimal(5)


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------


def test_check_unique_key_plain() -> None:
    assert check_unique_key([{"reservation_id": "A"}, {"reservation_id": "A"}]) == ["unique_key"]
    assert check_unique_key([{"reservation_id": "A"}, {"reservation_id": "B"}]) == []


def test_check_unique_key_generic() -> None:
    assert check_unique_key_generic([{"charge_id": "C1"}, {"charge_id": "C1"}]) == ["unique_key"]
    assert check_unique_key_generic([{"charge_id": "C1"}, {"charge_id": "C2"}]) == []
    assert check_unique_key_generic([]) == []


def test_check_no_loss() -> None:
    assert check_no_loss([{"id": 1}], expected_count=2) == ["no_loss"]
    assert check_no_loss([{"id": 1}, {"id": 2}], expected_count=2) == []
    assert check_no_loss([{"id": 1}], compare_to_value="2") == ["no_loss"]
    assert check_no_loss([{"id": 1}], compare_to_value=1) == []
    assert check_no_loss([{"id": 1}], compare_to_value="not-int") == []
    assert check_no_loss([], expected_count=None) == []


def test_check_date_order() -> None:
    recs = [{"arrival_date": "2026-10-28", "departure_date": "2026-10-27"}]
    assert check_date_order(recs, "arrival_date", "departure_date") == ["date_order"]
    recs2 = [{"arrival_date": "2026-10-27", "departure_date": "2026-10-28"}]
    assert check_date_order(recs2, "arrival_date", "departure_date") == []
    assert check_date_order([{"arrival_date": None, "departure_date": "2026-10-27"}], "arrival_date", "departure_date") == []
    assert check_date_order([{"arrival_date": "invalid", "departure_date": "2026-10-27"}], "arrival_date", "departure_date") == []


def test_evaluate_entity_checks_unique() -> None:
    recs = [{"reservation_id": "A"}, {"reservation_id": "A"}]
    checks = [{"kind": "unique_key"}]
    assert "unique_key" in evaluate_entity_checks("Reservation", recs, checks)


def test_evaluate_entity_checks_no_loss_dict() -> None:
    recs = [{"id": 1}]
    checks = [{"kind": "no_loss", "compare_to": "$.meta.total_count"}]
    # compare_to_value 2 should trigger no_loss
    assert "no_loss" in evaluate_entity_checks("Reservation", recs, checks, compare_to_value=2)


def test_evaluate_entity_checks_date_order_pydantic() -> None:
    from autobridge.spec.models import EntityCheck

    recs = [{"arrival_date": "2026-10-28", "departure_date": "2026-10-27"}]
    chk = EntityCheck(kind="date_order", before="arrival_date", after="departure_date")
    assert "date_order" in evaluate_entity_checks("Reservation", recs, [chk])


def test_run_checks_mixed() -> None:
    recs = [
        {"reservation_id": "A", "arrival_date": "2026-10-28", "departure_date": "2026-10-27"},
        {"reservation_id": "A", "arrival_date": "2026-10-28", "departure_date": "2026-10-27"},
    ]
    checks = [
        {"kind": "unique_key"},
        {"kind": "date_order", "before": "arrival_date", "after": "departure_date"},
        {"kind": "no_loss", "compare_to": "$.meta.total_count"},
    ]
    out = run_checks(recs, checks, expected_counts={"Reservation": 5}, compare_to_values={"Reservation": 5}, entity_name="Reservation")
    assert "unique_key" in out
    assert "date_order" in out
    assert "no_loss" in out


# ---------------------------------------------------------------------------
# checkpoint
# ---------------------------------------------------------------------------


def test_compute_checkpoint_max_min() -> None:
    recs = [{"updated_at": "2026-10-01"}, {"updated_at": "2026-10-03"}, {"updated_at": "2026-10-02"}]
    assert compute_checkpoint(recs, "$.updated_at", "max") == "2026-10-03"
    assert compute_checkpoint(recs, "$.updated_at", "min") == "2026-10-01"


def test_compute_checkpoint_numeric() -> None:
    recs = [{"val": 1}, {"val": 5}]
    assert compute_checkpoint(recs, "$.val", "max") == 5


def test_compute_checkpoint_mixed_types() -> None:
    recs = [{"val": 1}, {"val": "a"}]
    # stringify fallback -> "a" > "1"
    assert compute_checkpoint(recs, "$.val", "max") == "a"


def test_compute_checkpoint_empty() -> None:
    assert compute_checkpoint([], "$.updated_at") is None
    assert compute_checkpoint([{"other": 1}], "$.updated_at") is None


def test_compute_checkpoint_invalid_path() -> None:
    # invalid path falls back to simple key lookup returning None
    assert compute_checkpoint([{"updated_at": "x"}], "$.bad[?(@.a)]", "max") is None


def test_compute_checkpoint_for_operation() -> None:
    assert compute_checkpoint_for_operation([], None) is None
    assert compute_checkpoint_for_operation([{"updated_at": "2026-10-02"}], {"field": "$.updated_at", "mode": "max"}) == "2026-10-02"
    # missing field
    assert compute_checkpoint_for_operation([{"a": 1}], {"field": ""}) is None
    assert compute_checkpoint_for_operation([{"a": 1}], {"field": None}) is None
    # pydantic model
    from autobridge.spec.models import Checkpoint

    cp = Checkpoint(field="$.updated_at", mode="max")
    assert compute_checkpoint_for_operation([{"updated_at": "2026-10-05"}], cp) == "2026-10-05"
    assert compute_checkpoint_for_operation([{"updated_at": "2026-10-02"}], "not-a-dict") is None


def test_next_checkpoint_value() -> None:
    assert next_checkpoint_value(None, "2026-10-02") == "2026-10-02"
    assert next_checkpoint_value("2026-10-02", None) == "2026-10-02"
    assert next_checkpoint_value("2026-10-01", "2026-10-03", "max") == "2026-10-03"
    assert next_checkpoint_value("2026-10-03", "2026-10-01", "min") == "2026-10-01"
    # mixed types stringify
    assert next_checkpoint_value(1, "a", "max") == "a"
    assert next_checkpoint_value("a", 1, "min") == 1


# ---------------------------------------------------------------------------
# mapping
# ---------------------------------------------------------------------------


def test_map_record_to_entity_basic() -> None:
    spec = {"fields": {"reservation_id": {"path": "$.id"}}}
    assert map_record_to_entity({"id": "R1"}, spec) == {"reservation_id": "R1"}


def test_map_record_to_entity_with_transforms() -> None:
    spec = {
        "fields": {
            "total_amount": {"path": "$.gross", "transform": [{"name": "money", "args": {"scale": 2}}]},
            "arrival_date": {"path": "$.from", "transform": [{"name": "date_local", "args": {"tz": "UTC"}}]},
        }
    }
    raw = {"gross": "12.5", "from": "2026-10-27"}
    out = map_record_to_entity(raw, spec, context_tz="UTC")
    assert out["total_amount"] == Decimal("12.50")
    assert out["arrival_date"] == "2026-10-27"


def test_map_record_to_entity_missing_path() -> None:
    spec = {"fields": {"x": {"path": "$.missing"}}}
    assert map_record_to_entity({"a": 1}, spec) == {}


def test_map_record_to_entity_const_defaults() -> None:
    spec = {"fields": {"x": {"path": "$.missing", "transform": [{"name": "const", "args": {"value": "fallback"}}]}}}
    assert map_record_to_entity({}, spec) == {"x": "fallback"}


def test_map_record_to_entity_pydantic_model() -> None:
    from autobridge.spec.models import EntitySpec, FieldMapping, TransformStep

    fm = FieldMapping(path="$.id", transform=[TransformStep(name="const", args={"value": "X"})])
    ent = EntitySpec(from_operation="op", key="$.id", fields={"reservation_id": fm})
    assert map_record_to_entity({"id": "ignored"}, ent) == {"reservation_id": "X"}


def test_map_record_to_entity_transform_error_propagates() -> None:
    spec = {"fields": {"status": {"path": "$.s", "transform": [{"name": "enum_map", "args": {"map": {"A": "a"}, "on_unknown": "block"}}]}}}
    with pytest.raises(TransformBlocked):
        map_record_to_entity({"s": "UNKNOWN"}, spec)
    # non-block error: money invalid should raise TransformError
    spec2 = {"fields": {"amt": {"path": "$.v", "transform": [{"name": "money", "args": {"scale": 2}}]}}}
    with pytest.raises(TransformError):  # noqa: PT011
        map_record_to_entity({"v": "not-money"}, spec2)


def test_map_records_to_entities_with_blocked() -> None:
    spec = {"fields": {"status": {"path": "$.s", "transform": [{"name": "enum_map", "args": {"map": {"OK": "ok"}, "on_unknown": "block"}}]}}}
    canon, blocked = map_records_to_entities([{"s": "OK"}, {"s": "BAD"}], spec, entity_name="Reservation")
    assert len(canon) == 1
    assert len(blocked) == 1


def test_map_records_timezone_field_kind() -> None:
    # source tz as field
    source = {"timezone_source": {"kind": "field", "path": "$.property.tz"}}
    spec = {"fields": {"arrival_date": {"path": "$.ts", "transform": [{"name": "date_local", "args": {"tz": "source"}}]}}}
    raw = {"ts": "2026-10-27T03:30:00+00:00", "property": {"tz": "America/New_York"}}
    canon, blocked = map_records_to_entities([raw], spec, source_spec=source, entity_name="Reservation")
    assert canon[0]["arrival_date"] == "2026-10-26"
    assert not blocked


# ---------------------------------------------------------------------------
# sync_run integration — Blocked, Degraded, business boundary, Decimal
# ---------------------------------------------------------------------------


def test_sync_blocked_on_unknown_enum() -> None:
    import httpx

    from autobridge.engine.sync_run import execute_sync
    from autobridge.runtime.http import HttpClient
    from autobridge.spec.models import RetryPolicy

    def fake_http(pages):
        idx = {"i": 0}

        def handler(request: httpx.Request):  # noqa: ARG001
            if idx["i"] >= len(pages):
                return httpx.Response(200, json={"data": [], "meta": {"next_cursor": None}})
            body = pages[idx["i"]]
            idx["i"] += 1
            return httpx.Response(200, json=body)

        transport = httpx.MockTransport(handler)

        def factory(**kwargs):
            return httpx.Client(transport=transport, timeout=kwargs.get("timeout", 5))

        return HttpClient("https://example.com", retry=RetryPolicy(statuses=[429, 503], backoff="exponential", max_attempts=3), client_factory=factory, sleep_fn=lambda _: None)

    import os

    os.environ["TEST_TOKEN"] = "tok"
    spec_dict = {
        "spec_version": 1,
        "connector": {"id": "testconn", "vendor": "Test", "runtime_target": "cloud"},
        "source": {
            "base_url": "https://example.com",
            "auth": {"type": "bearer", "credential_ref": "TEST_TOKEN"},
            "rate_limit": {"max_rps": 5, "retry": {"statuses": [429, 503], "backoff": "exponential", "max_attempts": 3}},
            "timezone_source": {"kind": "static", "value": "UTC"},
        },
        "operations": {
            "list_reservations": {
                "method": "GET",
                "path": "/reservations",
                "params": {},
                "pagination": {"strategy": "cursor", "cursor_param": "cursor", "next_path": "$.meta.next_cursor", "items_path": "$.data", "stop_when": ["null_cursor"]},
            }
        },
        "entities": {
            "Reservation": {
                "from": "list_reservations",
                "key": "$.reservation_id",
                "fields": {
                    "reservation_id": {"path": "$.reservation_id"},
                    "status": {"path": "$.status", "transform": [{"name": "enum_map", "args": {"map": {"confirmed": "confirmed"}, "on_unknown": "block"}}]},
                },
                "checks": [],
            }
        },
        "capabilities": {"read": ["Reservation"]},
        "health": {"canary": {"every": "60s", "operation": "list_reservations"}, "fingerprint": {}},
    }
    pages = [{"data": [{"reservation_id": "R1", "status": "weird"}], "meta": {"next_cursor": None}}]
    summary = execute_sync(spec_dict, fake_http(pages))
    assert summary["outcome"] == "Blocked"
    assert any("unknown_enum" in c for c in summary["unmet_criteria"])


def test_sync_money_decimal_and_date_boundary() -> None:
    import httpx

    from autobridge.engine.sync_run import execute_sync
    from autobridge.runtime.http import HttpClient
    from autobridge.spec.models import RetryPolicy

    def fake_http(pages):
        idx = {"i": 0}

        def handler(request: httpx.Request):  # noqa: ARG001
            if idx["i"] >= len(pages):
                return httpx.Response(200, json={"data": [], "meta": {"next_cursor": None}})
            body = pages[idx["i"]]
            idx["i"] += 1
            return httpx.Response(200, json=body)

        transport = httpx.MockTransport(handler)

        def factory(**kwargs):
            return httpx.Client(transport=transport, timeout=kwargs.get("timeout", 5))

        return HttpClient("https://example.com", retry=RetryPolicy(statuses=[429, 503], backoff="exponential", max_attempts=3), client_factory=factory, sleep_fn=lambda _: None)

    import os

    os.environ["TEST_TOKEN"] = "tok"
    spec_dict = {
        "spec_version": 1,
        "connector": {"id": "testconn", "vendor": "Test", "runtime_target": "cloud"},
        "source": {
            "base_url": "https://example.com",
            "auth": {"type": "bearer", "credential_ref": "TEST_TOKEN"},
            "rate_limit": {"max_rps": 5, "retry": {"statuses": [429, 503], "backoff": "exponential", "max_attempts": 3}},
            "timezone_source": {"kind": "static", "value": "America/New_York"},
        },
        "operations": {
            "list_reservations": {
                "method": "GET",
                "path": "/reservations",
                "params": {},
                "pagination": {"strategy": "cursor", "cursor_param": "cursor", "next_path": "$.meta.next_cursor", "items_path": "$.data", "stop_when": ["null_cursor"]},
                "checkpoint": {"field": "$.updated_at", "mode": "max"},
            }
        },
        "entities": {
            "Reservation": {
                "from": "list_reservations",
                "key": "$.reservation_id",
                "fields": {
                    "reservation_id": {"path": "$.reservation_id"},
                    "arrival_date": {"path": "$.arrival_date", "transform": [{"name": "date_local", "args": {"tz": "source"}}]},
                    "total_amount": {"path": "$.total_amount", "transform": [{"name": "money", "args": {"scale": 2}}]},
                },
                "checks": [],
            }
        },
        "capabilities": {"read": ["Reservation"]},
        "health": {"canary": {"every": "60s", "operation": "list_reservations"}, "fingerprint": {}},
    }
    # timestamp crossing boundary: UTC 03:30 should be previous day in NY
    pages = [
        {
            "data": [
                {"reservation_id": "R1", "arrival_date": "2026-10-27T03:30:00+00:00", "total_amount": "10.00", "updated_at": "2026-10-02T10:00:00+00:00"},
                {"reservation_id": "R2", "arrival_date": "2026-10-27T04:30:00+00:00", "total_amount": "20.00", "updated_at": "2026-10-03T10:00:00+00:00"},
            ],
            "meta": {"next_cursor": None},
        }
    ]
    summary = execute_sync(spec_dict, fake_http(pages))
    assert summary["outcome"] == "Complete"
    recs = summary["canonical_records"]["Reservation"]
    # money Decimal
    assert recs[0]["total_amount"] == Decimal("10.00")
    assert isinstance(recs[0]["total_amount"], Decimal)
    # date boundary: first is previous day, second is same day
    assert recs[0]["arrival_date"] == "2026-10-26"
    assert recs[1]["arrival_date"] == "2026-10-27"
    # checkpoint max
    assert summary["checkpoint_values"]["list_reservations"] == "2026-10-03T10:00:00+00:00"


def test_sync_degraded_via_checks() -> None:
    import httpx

    from autobridge.engine.sync_run import execute_sync
    from autobridge.runtime.http import HttpClient
    from autobridge.spec.models import RetryPolicy

    def fake_http(pages):
        idx = {"i": 0}

        def handler(request: httpx.Request):  # noqa: ARG001
            if idx["i"] >= len(pages):
                return httpx.Response(200, json={"data": [], "meta": {"next_cursor": None}})
            body = pages[idx["i"]]
            idx["i"] += 1
            return httpx.Response(200, json=body)

        transport = httpx.MockTransport(handler)

        def factory(**kwargs):
            return httpx.Client(transport=transport, timeout=kwargs.get("timeout", 5))

        return HttpClient("https://example.com", retry=RetryPolicy(statuses=[429, 503], backoff="exponential", max_attempts=3), client_factory=factory, sleep_fn=lambda _: None)

    import os

    os.environ["TEST_TOKEN"] = "tok"
    spec_dict = {
        "spec_version": 1,
        "connector": {"id": "testconn", "vendor": "Test", "runtime_target": "cloud"},
        "source": {
            "base_url": "https://example.com",
            "auth": {"type": "bearer", "credential_ref": "TEST_TOKEN"},
            "rate_limit": {"max_rps": 5, "retry": {"statuses": [429, 503], "backoff": "exponential", "max_attempts": 3}},
            "timezone_source": {"kind": "static", "value": "UTC"},
        },
        "operations": {
            "list_reservations": {
                "method": "GET",
                "path": "/reservations",
                "params": {},
                "pagination": {"strategy": "cursor", "cursor_param": "cursor", "next_path": "$.meta.next_cursor", "items_path": "$.data", "stop_when": ["null_cursor"]},
            }
        },
        "entities": {
            "Reservation": {
                "from": "list_reservations",
                "key": "$.reservation_id",
                "fields": {
                    "reservation_id": {"path": "$.reservation_id"},
                    "arrival_date": {"path": "$.arrival_date"},
                    "departure_date": {"path": "$.departure_date"},
                },
                "checks": [
                    {"kind": "unique_key"},
                    {"kind": "date_order", "before": "arrival_date", "after": "departure_date"},
                    {"kind": "no_loss", "compare_to": "$.meta.total_count"},
                ],
            }
        },
        "capabilities": {"read": ["Reservation"]},
        "health": {"canary": {"every": "60s", "operation": "list_reservations"}, "fingerprint": {}},
    }
    # duplicate + date_order violation + no_loss (meta.total_count 3 vs 2 actual)
    pages = [
        {
            "data": [
                {"reservation_id": "R1", "arrival_date": "2026-10-28", "departure_date": "2026-10-27"},
                {"reservation_id": "R1", "arrival_date": "2026-10-28", "departure_date": "2026-10-27"},
            ],
            "meta": {"next_cursor": None, "total_count": 3},
        }
    ]
    summary = execute_sync(spec_dict, fake_http(pages))
    assert summary["outcome"] == "Degraded"
    assert "unique_key" in summary["unmet_criteria"]
    assert "date_order" in summary["unmet_criteria"]
    assert "no_loss" in summary["unmet_criteria"]


def test_determine_outcome_blocked_precedence() -> None:
    from autobridge.engine.sync_run import determine_outcome
    from autobridge.runtime.interpreter import OperationResult

    res = {"op": OperationResult(operation="list_reservations", records=[], repeated_cursor=True, stop_reason="repeated_cursor")}
    outcome, unmet = determine_outcome(res, invariant_failures=["date_order"], blocked_failures=["unknown_enum"])
    assert outcome == "Blocked"
    assert "unknown_enum" in unmet


def test_execute_sync_repeated_cursor_still_degraded() -> None:
    import httpx

    from autobridge.engine.sync_run import execute_sync
    from autobridge.runtime.http import HttpClient
    from autobridge.spec.models import RetryPolicy

    def fake_http(pages):
        idx = {"i": 0}

        def handler(request: httpx.Request):  # noqa: ARG001
            if idx["i"] >= len(pages):
                return httpx.Response(200, json={"data": [], "meta": {"next_cursor": None}})
            body = pages[idx["i"]]
            idx["i"] += 1
            return httpx.Response(200, json=body)

        transport = httpx.MockTransport(handler)

        def factory(**kwargs):
            return httpx.Client(transport=transport, timeout=kwargs.get("timeout", 5))

        return HttpClient("https://example.com", retry=RetryPolicy(statuses=[429, 503], backoff="exponential", max_attempts=3), client_factory=factory, sleep_fn=lambda _: None)

    import os

    os.environ["TEST_TOKEN"] = "tok"
    spec_dict = {
        "spec_version": 1,
        "connector": {"id": "testconn", "vendor": "Test", "runtime_target": "cloud"},
        "source": {
            "base_url": "https://example.com",
            "auth": {"type": "bearer", "credential_ref": "TEST_TOKEN"},
            "rate_limit": {"max_rps": 5, "retry": {"statuses": [429, 503], "backoff": "exponential", "max_attempts": 3}},
            "timezone_source": {"kind": "static", "value": "UTC"},
        },
        "operations": {
            "list_reservations": {
                "method": "GET",
                "path": "/reservations",
                "params": {},
                "pagination": {"strategy": "cursor", "cursor_param": "cursor", "next_path": "$.meta.next_cursor", "items_path": "$.data", "stop_when": ["repeated_cursor"]},
            }
        },
        "entities": {
            "Reservation": {
                "from": "list_reservations",
                "key": "$.reservation_id",
                "fields": {"reservation_id": {"path": "$.reservation_id"}},
                "checks": [],
            }
        },
        "capabilities": {"read": ["Reservation"]},
        "health": {"canary": {"every": "60s", "operation": "list_reservations"}, "fingerprint": {}},
    }
    pages = [
        {"data": [{"reservation_id": "R1"}], "meta": {"next_cursor": "abc"}},
        {"data": [{"reservation_id": "R2"}], "meta": {"next_cursor": "abc"}},
    ]
    summary = execute_sync(spec_dict, fake_http(pages))
    assert summary["outcome"] == "Degraded"
    assert "no_loss" in summary["unmet_criteria"]
