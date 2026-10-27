"""Canonical entity schema tests (P1.W1.4): reference examples validate.

Every reference example must validate against BOTH its committed JSON Schema
file (the cross-package interop contract for the verifier and simulators) and
its pydantic model (the engine-side view). Money is Decimal end to end, dates
are property-local business dates, vocabularies are closed.
"""

from __future__ import annotations

import datetime
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from pydantic import TypeAdapter, ValidationError

from autobridge.spec import entities as e
from autobridge.spec.entities import (
    DiscoveryResult,
    FolioCharge,
    Reservation,
    RuntimeTarget,
    load_reference_examples,
    schema_path,
    validate_against_schema,
    validate_entity,
)

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_FILES = [
    "reservation.schema.json",
    "folio-charge.schema.json",
    "runtime-target.json",
    "discovery-result.v0.json",
]

RUNTIME_ADAPTER: TypeAdapter[Any] = TypeAdapter(RuntimeTarget)


def _schema(name: str) -> dict[str, Any]:
    return json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))


def _reservation(**overrides: Any) -> dict[str, Any]:
    data = dict(e.REFERENCE_RESERVATION)
    data.update(overrides)
    return data


def _folio(**overrides: Any) -> dict[str, Any]:
    data = dict(e.REFERENCE_FOLIO_CHARGE)
    data.update(overrides)
    return data


# --- schema files are valid JSON Schemas ---


def test_schema_files_exist_and_are_valid_schemas() -> None:
    validator = jsonschema.Draft202012Validator
    for name in SCHEMA_FILES:
        path = ROOT / "schemas" / name
        assert path.is_file(), f"missing committed schema {name}"
        validator.check_schema(_schema(name))


# --- reference examples validate against model + schema ---


def test_reference_reservation_validates_model_and_schema() -> None:
    record = Reservation.model_validate(e.REFERENCE_RESERVATION)
    assert record.reservation_id == "RES-1001"
    assert record.total_amount == Decimal("289.99")
    assert isinstance(record.total_amount, Decimal)
    assert record.arrival_date == datetime.date(2026, 10, 27)
    assert record.status == "checked_in"
    validate_against_schema(e.REFERENCE_RESERVATION, "reservation.schema.json")
    validate_entity("Reservation", e.REFERENCE_RESERVATION)


def test_reference_folio_charge_validates_model_and_schema() -> None:
    record = FolioCharge.model_validate(e.REFERENCE_FOLIO_CHARGE)
    assert record.amount == Decimal("42.50")
    # Late posting: UTC instant is past midnight, business date stays prior day.
    assert record.business_date == datetime.date(2026, 10, 26)
    assert record.posted_at is not None
    validate_against_schema(e.REFERENCE_FOLIO_CHARGE, "folio-charge.schema.json")
    validate_entity("FolioCharge", e.REFERENCE_FOLIO_CHARGE)


def test_reference_runtime_targets_validate_model_and_schema() -> None:
    for kind, data in e.REFERENCE_RUNTIME_TARGETS.items():
        parsed = RUNTIME_ADAPTER.validate_python(data)
        assert parsed.kind == kind  # type: ignore[union-attr]
        validate_against_schema(data, "runtime-target.json")


def test_reference_discovery_result_validates_model_and_schema() -> None:
    record = DiscoveryResult.model_validate(e.REFERENCE_DISCOVERY_RESULT)
    assert record.status == "discovered"
    validate_against_schema(e.REFERENCE_DISCOVERY_RESULT, "discovery-result.v0.json")


def test_load_reference_examples_covers_every_schema() -> None:
    examples = load_reference_examples()
    assert sorted(examples) == sorted(SCHEMA_FILES)
    validate_against_schema(examples["reservation.schema.json"], "reservation.schema.json")
    validate_against_schema(examples["folio-charge.schema.json"], "folio-charge.schema.json")
    validate_against_schema(
        examples["discovery-result.v0.json"], "discovery-result.v0.json"
    )
    for data in examples["runtime-target.json"].values():
        validate_against_schema(data, "runtime-target.json")


def test_model_json_round_trips_through_schema() -> None:
    """Model → JSON dump → schema: the two contracts agree on the wire form."""
    dumped_res = Reservation.model_validate(e.REFERENCE_RESERVATION).model_dump(mode="json")
    assert dumped_res["total_amount"] == "289.99"
    assert dumped_res["arrival_date"] == "2026-10-27"
    validate_against_schema(dumped_res, "reservation.schema.json")
    dumped_folio = FolioCharge.model_validate(e.REFERENCE_FOLIO_CHARGE).model_dump(mode="json")
    assert dumped_folio["amount"] == "42.50"
    validate_against_schema(dumped_folio, "folio-charge.schema.json")


# --- money is Decimal, never float ---


def test_money_exact_decimal_arithmetic() -> None:
    record = Reservation.model_validate(_reservation(total_amount="19.99"))
    assert record.total_amount + Decimal("0.01") == Decimal("20.00")
    assert Decimal("0.10") + Decimal("0.20") == Decimal("0.30")


@pytest.mark.parametrize("bad", [19.99, True, 1999, "19.9", "19.999", "20", "abc", "NaN", "Infinity"])
def test_money_rejects_non_canonical_forms(bad: Any) -> None:
    with pytest.raises(ValidationError):
        Reservation.model_validate(_reservation(total_amount=bad))


def test_money_accepts_decimal_and_negative_refund() -> None:
    record = FolioCharge.model_validate(_folio(amount=Decimal("-12.50")))
    assert record.amount == Decimal("-12.50")
    validate_against_schema(_folio(amount="-12.50"), "folio-charge.schema.json")


def test_money_schema_rejects_float_and_short_scale() -> None:
    with pytest.raises(jsonschema.ValidationError):
        validate_against_schema(_reservation(total_amount=19.99), "reservation.schema.json")  # type: ignore[dict-item]
    with pytest.raises(jsonschema.ValidationError):
        validate_against_schema(_folio(amount="42.5"), "folio-charge.schema.json")


# --- dates are property-local business dates ---


def test_business_date_never_shifts_like_an_instant() -> None:
    record = Reservation.model_validate(_reservation(arrival_date="2026-10-27"))
    assert record.arrival_date == datetime.date(2026, 10, 27)
    assert isinstance(record.arrival_date, datetime.date)


@pytest.mark.parametrize(
    "bad",
    ["2026-10-27T14:00:00Z", "2026-10-27T00:00:00+00:00", "27.10.2026", "2026-02-30", "2026-13-01"],
)
def test_business_date_rejects_datetimes_and_impossible_dates(bad: str) -> None:
    with pytest.raises(ValidationError):
        Reservation.model_validate(_reservation(arrival_date=bad))


@pytest.mark.parametrize(
    "bad", ["2026-10-27T14:00:00Z", "2026-10-27T00:00:00+00:00", "27.10.2026"]
)
def test_business_date_schema_rejects_non_date_shapes(bad: str) -> None:
    # The JSON Schema pattern is syntactic (YYYY-MM-DD shape); the calendar
    # check (Feb 30, month 13) lives in the model and the verifier checks.
    with pytest.raises(jsonschema.ValidationError):
        validate_against_schema(_reservation(arrival_date=bad), "reservation.schema.json")


def test_business_date_accepts_date_instances() -> None:
    record = Reservation.model_validate(
        _reservation(
            arrival_date=datetime.date(2026, 10, 27), departure_date=datetime.date(2026, 10, 28)
        )
    )
    assert record.arrival_date == datetime.date(2026, 10, 27)


def test_datetime_instance_rejected_as_business_date() -> None:
    with pytest.raises(ValidationError, match="never a datetime"):
        Reservation.model_validate(
            _reservation(arrival_date=datetime.datetime(2026, 10, 27, 12, 0))
        )


def test_departure_before_arrival_rejected() -> None:
    with pytest.raises(ValidationError, match="departure_date"):
        Reservation.model_validate(
            _reservation(arrival_date="2026-10-27", departure_date="2026-10-26")
        )


def test_same_day_stay_accepted() -> None:
    record = Reservation.model_validate(
        _reservation(arrival_date="2026-10-27", departure_date="2026-10-27")
    )
    assert record.departure_date == record.arrival_date


def test_naive_instant_rejected_but_aware_accepted() -> None:
    with pytest.raises(ValidationError):
        Reservation.model_validate(_reservation(updated_at="2026-10-27T14:05:00"))
    record = Reservation.model_validate(_reservation(updated_at="2026-10-27T14:05:00+00:00"))
    assert record.updated_at is not None


def test_nullable_demo_fields_accept_null_and_absence() -> None:
    assert Reservation.model_validate(_reservation(guest_name=None)).guest_name is None
    assert FolioCharge.model_validate(_folio(description=None)).description is None
    minimal = _reservation()
    del minimal["guest_name"]
    Reservation.model_validate(minimal)
    validate_against_schema(minimal, "reservation.schema.json")


# --- closed vocabularies: unknown values Block, never guessed ---


@pytest.mark.parametrize(
    "status", ["confirmed", "checked_in", "checked_out", "cancelled", "no_show"]
)
def test_all_canonical_statuses_accepted(status: str) -> None:
    assert Reservation.model_validate(_reservation(status=status)).status == status
    validate_against_schema(_reservation(status=status), "reservation.schema.json")


def test_unknown_status_rejected_by_model_and_schema() -> None:
    with pytest.raises(ValidationError):
        Reservation.model_validate(_reservation(status="ON_HOLD"))
    with pytest.raises(jsonschema.ValidationError):
        validate_against_schema(_reservation(status="ON_HOLD"), "reservation.schema.json")


def test_lowercase_currency_rejected_by_model_and_schema() -> None:
    with pytest.raises(ValidationError):
        Reservation.model_validate(_reservation(currency="usd"))
    with pytest.raises(jsonschema.ValidationError):
        validate_against_schema(_folio(currency="usd"), "folio-charge.schema.json")


# --- strictness: no extras, no missing keys ---


def test_extra_property_rejected_by_model_and_schema() -> None:
    with pytest.raises(ValidationError):
        Reservation.model_validate(_reservation(vendor_note="x"))
    with pytest.raises(jsonschema.ValidationError):
        validate_against_schema(_reservation(vendor_note="x"), "reservation.schema.json")
    with pytest.raises(jsonschema.ValidationError):
        validate_against_schema(_folio(vendor_note="x"), "folio-charge.schema.json")


def test_missing_required_rejected_by_model_and_schema() -> None:
    data = _reservation()
    del data["total_amount"]
    with pytest.raises(ValidationError):
        Reservation.model_validate(data)
    with pytest.raises(jsonschema.ValidationError):
        validate_against_schema(data, "reservation.schema.json")


# --- runtime targets ---


def test_runtime_target_defaults_and_allowlist() -> None:
    local = RUNTIME_ADAPTER.validate_python({"kind": "local_process"})
    assert local.timeout_s == 60  # type: ignore[union-attr]
    sandbox = RUNTIME_ADAPTER.validate_python(
        {"kind": "docker_sandbox", "network_allowlist": ["atlas.example.com"]}
    )
    assert sandbox.network_allowlist == ["atlas.example.com"]  # type: ignore[union-attr]
    assert sandbox.memory_mb == 512  # type: ignore[union-attr]


def test_edge_kind_parses_but_is_reserved() -> None:
    edge = RUNTIME_ADAPTER.validate_python({"kind": "edge"})
    assert edge.kind == "edge"  # type: ignore[union-attr]
    assert "edge" in e.RESERVED_RUNTIME_KINDS


def test_unknown_runtime_kind_rejected_by_model_and_schema() -> None:
    with pytest.raises(ValidationError):
        RUNTIME_ADAPTER.validate_python({"kind": "bare_metal"})
    with pytest.raises(jsonschema.ValidationError):
        validate_against_schema({"kind": "bare_metal"}, "runtime-target.json")


@pytest.mark.parametrize(
    "bad",
    [{"kind": "local_process", "timeout_s": 0}, {"kind": "docker_sandbox", "memory_mb": 32}],
)
def test_runtime_target_bounds_rejected(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        RUNTIME_ADAPTER.validate_python(bad)
    with pytest.raises(jsonschema.ValidationError):
        validate_against_schema(bad, "runtime-target.json")


# --- discovery result (reserved v0) ---


@pytest.mark.parametrize("status", list(e.DISCOVERY_STATUSES))
def test_all_discovery_statuses_accepted(status: str) -> None:
    data = dict(e.REFERENCE_DISCOVERY_RESULT)
    data["status"] = status
    assert DiscoveryResult.model_validate(data).status == status
    validate_against_schema(data, "discovery-result.v0.json")


def test_unknown_discovery_status_rejected_by_model_and_schema() -> None:
    data = dict(e.REFERENCE_DISCOVERY_RESULT)
    data["status"] = "archived"
    with pytest.raises(ValidationError):
        DiscoveryResult.model_validate(data)
    with pytest.raises(jsonschema.ValidationError):
        validate_against_schema(data, "discovery-result.v0.json")


def test_minimal_discovery_record_accepted() -> None:
    data = {"discovery_id": "d1", "status": "needs_confirmation", "vendor": "LegacyStay"}
    assert DiscoveryResult.model_validate(data).candidate_entities == []
    validate_against_schema(data, "discovery-result.v0.json")


def test_bad_candidate_entity_rejected() -> None:
    data = dict(e.REFERENCE_DISCOVERY_RESULT)
    data["candidate_entities"] = ["RoomService"]
    with pytest.raises(ValidationError):
        DiscoveryResult.model_validate(data)
    with pytest.raises(jsonschema.ValidationError):
        validate_against_schema(data, "discovery-result.v0.json")


# --- model/schema consistency guards ---


@pytest.mark.parametrize(
    ("model_cls", "schema_file"),
    [(Reservation, "reservation.schema.json"), (FolioCharge, "folio-charge.schema.json")],
)
def test_model_fields_match_schema_properties(
    model_cls: type[Reservation] | type[FolioCharge], schema_file: str
) -> None:
    schema = _schema(schema_file)
    assert set(model_cls.model_fields) == set(schema["properties"])
    required_from_model = {
        name for name, f in model_cls.model_fields.items() if f.is_required()
    }
    assert required_from_model == set(schema["required"])


def test_validate_entity_rejects_unknown_entity() -> None:
    with pytest.raises(ValueError, match="unknown canonical entity"):
        validate_entity("RoomService", {})


def test_schema_path_rejects_traversal() -> None:
    for bad in ["../connector-spec.v1.json", "/abs/path.json", ".hidden.json", "sub/dir.json"]:
        with pytest.raises(ValueError, match="bare filename"):
            schema_path(bad)
