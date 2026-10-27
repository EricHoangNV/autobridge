"""P1.W2.1: seeded generator tests — byte-identical, late postings, duplicates, nullable."""

from __future__ import annotations

import datetime
import json
import random
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from simulators.common.generator import (
    BUSINESS_CUTOFF_HOUR,
    business_date_for_posted_at,
    dataset_fingerprint,
    dataset_from_json,
    dataset_to_json,
    deduplicate,
    generate_dataset,
    generate_folios,
    generate_properties,
    generate_reservations,
    property_currency_map,
    property_timezone_map,
    with_duplicates,
)

# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


def test_generate_properties_count_and_shape() -> None:
    props = generate_properties(seed=1, count=3)
    assert len(props) == 3
    for p in props:
        assert "property_id" in p
        assert "name" in p
        assert "timezone" in p
        assert "currency" in p
        assert ZoneInfo(str(p["timezone"]))  # valid tz


def test_generate_properties_deterministic() -> None:
    a = generate_properties(seed=99, count=3)
    b = generate_properties(seed=99, count=3)
    assert a == b


def test_generate_properties_different_seed_differs() -> None:
    a = generate_properties(seed=1, count=3)
    b = generate_properties(seed=2, count=3)
    assert a != b


def test_generate_properties_different_counts() -> None:
    for n in [1, 2, 5]:
        props = generate_properties(seed=42, count=n)
        assert len(props) == n


def test_generate_properties_invalid_count() -> None:
    with pytest.raises(ValueError, match="count must be"):
        generate_properties(seed=1, count=0)
    with pytest.raises(ValueError, match="exceeds template"):
        generate_properties(seed=1, count=10)


def test_property_maps() -> None:
    props = generate_properties(seed=7, count=3)
    tzmap = property_timezone_map(props)
    curmap = property_currency_map(props)
    assert set(tzmap) == {str(p["property_id"]) for p in props}
    assert set(curmap) == {str(p["property_id"]) for p in props}
    for _pid, tz in tzmap.items():
        assert ZoneInfo(tz)


# ---------------------------------------------------------------------------
# Reservations
# ---------------------------------------------------------------------------


def test_generate_reservations_count_and_shape() -> None:
    props = generate_properties(seed=42, count=2)
    res = generate_reservations(seed=42, properties=props, count_per_property=5)
    assert len(res) == 10  # 2 * 5
    for r in res:
        assert "reservation_id" in r
        assert "property_id" in r
        assert "arrival_date" in r
        assert "departure_date" in r
        assert "status" in r
        assert "total_amount" in r
        assert "currency" in r
        assert "updated_at" in r
        assert "guest_name" in r


def test_generate_reservations_deterministic() -> None:
    props = generate_properties(seed=123, count=2)
    a = generate_reservations(seed=123, properties=props, count_per_property=3)
    b = generate_reservations(seed=123, properties=props, count_per_property=3)
    assert a == b


def test_generate_reservations_byte_identical_json() -> None:
    props = generate_properties(seed=77, count=3)
    a = generate_reservations(seed=77, properties=props)
    b = generate_reservations(seed=77, properties=props)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_generate_reservations_money_format() -> None:
    props = generate_properties(seed=1, count=1)
    res = generate_reservations(seed=1, properties=props, count_per_property=20)
    for r in res:
        amt = str(r["total_amount"])
        assert "." in amt
        assert amt.split(".")[1].__len__() == 2
        # Decimal exact
        d = Decimal(amt)
        assert d.as_tuple().exponent == -2
        assert d.is_finite()


def test_generate_reservations_date_order() -> None:
    props = generate_properties(seed=5, count=3)
    res = generate_reservations(seed=5, properties=props, count_per_property=10)
    for r in res:
        arr = datetime.date.fromisoformat(str(r["arrival_date"]))
        dep = datetime.date.fromisoformat(str(r["departure_date"]))
        assert dep >= arr


def test_generate_reservations_status_vocab() -> None:
    props = generate_properties(seed=5, count=1)
    res = generate_reservations(seed=5, properties=props, count_per_property=50)
    statuses = {r["status"] for r in res}
    assert statuses.issubset({"confirmed", "checked_in", "checked_out", "cancelled", "no_show"})


def test_generate_reservations_currency_matches_property() -> None:
    props = generate_properties(seed=10, count=3)
    curmap = property_currency_map(props)
    res = generate_reservations(seed=10, properties=props, count_per_property=5)
    for r in res:
        assert r["currency"] == curmap[str(r["property_id"])]


def test_generate_reservations_updated_at_aware() -> None:
    props = generate_properties(seed=2, count=1)
    res = generate_reservations(seed=2, properties=props, count_per_property=5)
    for r in res:
        dt = datetime.datetime.fromisoformat(str(r["updated_at"]))
        assert dt.tzinfo is not None


def test_generate_reservations_nullable_guest_name() -> None:
    props = generate_properties(seed=42, count=1)
    res = generate_reservations(seed=42, properties=props, count_per_property=100)
    nulls = [r for r in res if r["guest_name"] is None]
    # With 10% rate, 100 samples should have at least 1 and less than 30 nulls (probabilistic but deterministic seed)
    assert 1 <= len(nulls) <= 30
    # Check non-null are strings
    for r in res:
        if r["guest_name"] is not None:
            assert isinstance(r["guest_name"], str)
            assert len(str(r["guest_name"])) > 0


def test_generate_reservations_sorted_order() -> None:
    props = generate_properties(seed=3, count=2)
    res = generate_reservations(seed=3, properties=props, count_per_property=4)
    ids = [str(r["reservation_id"]) for r in res]
    assert ids == sorted(ids)


def test_business_date_matches_posted_at_helper() -> None:
    from simulators.common.generator import _business_date_matches_posted_at

    iso = "2026-10-27T14:00:00+00:00"
    assert _business_date_matches_posted_at("2026-10-27", iso, "America/New_York") is True
    assert _business_date_matches_posted_at("2026-10-26", iso, "America/New_York") is False


def test_generate_reservations_default_properties() -> None:
    res = generate_reservations(seed=99, properties=None, count_per_property=2)
    assert len(res) == 6  # default 3 *2


def test_generate_reservations_invalid_count() -> None:
    props = generate_properties(seed=1, count=1)
    with pytest.raises(ValueError, match="count_per_property"):
        generate_reservations(seed=1, properties=props, count_per_property=0)
    with pytest.raises(ValueError, match="non-empty"):
        generate_reservations(seed=1, properties=[], count_per_property=1)


# ---------------------------------------------------------------------------
# Business date
# ---------------------------------------------------------------------------


def test_business_date_for_posted_at_normal() -> None:
    # 2026-10-27 14:00 UTC is 10:00 EDT (America/New_York, UTC-4) → business date 2026-10-27
    iso = "2026-10-27T14:00:00+00:00"
    assert business_date_for_posted_at(iso, "America/New_York") == "2026-10-27"


def test_business_date_for_posted_at_late() -> None:
    # 2026-10-27 04:30 UTC is 00:30 EDT (since EDT UTC-4) → business date should be prior day (2026-10-26)
    iso = "2026-10-27T04:30:00+00:00"
    assert business_date_for_posted_at(iso, "America/New_York") == "2026-10-26"


def test_business_date_for_posted_at_cutoff_boundary() -> None:
    # Exactly at cutoff 04:00 local should be new business date, 03:59 is prior
    # For UTC timezone, cutoff 4 means 03:59 local → prior day, 04:00 → same day
    # Use UTC tz: 2026-10-27T03:59:00+00:00 local 03:59 → business 2026-10-26
    assert business_date_for_posted_at("2026-10-27T03:59:00+00:00", "UTC") == "2026-10-26"
    assert business_date_for_posted_at("2026-10-27T04:00:00+00:00", "UTC") == "2026-10-27"


def test_business_date_for_posted_at_different_timezones() -> None:
    # Same UTC instant can be different business dates in different zones
    # 2026-10-27T02:00 UTC is 2026-10-26 22:00 EDT but 2026-10-27 11:00 JST
    iso = "2026-10-27T02:00:00+00:00"
    assert business_date_for_posted_at(iso, "America/New_York") == "2026-10-26"
    assert business_date_for_posted_at(iso, "Asia/Tokyo") == "2026-10-27"


def test_business_date_for_posted_at_naive_raises() -> None:
    with pytest.raises(ValueError, match="aware"):
        business_date_for_posted_at("2026-10-27T00:00:00", "UTC")


def test_business_date_custom_cutoff() -> None:
    # With cutoff 3, 02:59 is prior, 03:00 is new
    assert business_date_for_posted_at("2026-10-27T02:59:00+00:00", "UTC", cutoff_hour=3) == "2026-10-26"
    assert business_date_for_posted_at("2026-10-27T03:00:00+00:00", "UTC", cutoff_hour=3) == "2026-10-27"


# ---------------------------------------------------------------------------
# Folios
# ---------------------------------------------------------------------------


def test_generate_folios_count_and_shape() -> None:
    props = generate_properties(seed=42, count=2)
    res = generate_reservations(seed=42, properties=props, count_per_property=3)
    folios = generate_folios(seed=42, reservations=res, properties=props, min_per_reservation=1, max_per_reservation=2)
    assert 6 <= len(folios) <= 12
    for f in folios:
        assert "charge_id" in f
        assert "reservation_id" in f
        assert "property_id" in f
        assert "business_date" in f
        assert "amount" in f
        assert "currency" in f
        assert "description" in f
        assert "posted_at" in f


def test_generate_folios_deterministic() -> None:
    props = generate_properties(seed=77, count=2)
    res = generate_reservations(seed=77, properties=props, count_per_property=2)
    a = generate_folios(seed=77, reservations=res, properties=props)
    b = generate_folios(seed=77, reservations=res, properties=props)
    assert a == b
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_generate_folios_late_posting_invariant() -> None:
    props = generate_properties(seed=123, count=3)
    res = generate_reservations(seed=123, properties=props, count_per_property=5)
    folios = generate_folios(seed=123, reservations=res, properties=props)
    tzmap = property_timezone_map(props)
    for f in folios:
        derived = business_date_for_posted_at(str(f["posted_at"]), tzmap[str(f["property_id"])])
        assert derived == f["business_date"], f"{f} derived {derived}"


def test_generate_folios_late_postings_exist() -> None:
    props = generate_properties(seed=42, count=3)
    res = generate_reservations(seed=42, properties=props, count_per_property=20)
    folios = generate_folios(seed=42, reservations=res, properties=props)
    tzmap = property_timezone_map(props)
    late = []
    for f in folios:
        pa = datetime.datetime.fromisoformat(str(f["posted_at"]))
        local = pa.astimezone(ZoneInfo(tzmap[str(f["property_id"])]))
        if local.hour < BUSINESS_CUTOFF_HOUR:
            late.append(f)
            # For late, posted_at local is next calendar day, but business_date is prior
            assert f["business_date"] != local.date().isoformat()
    assert len(late) >= 1, "seed 42 should have at least one late posting"
    # Overall dataset should have ~15% late
    assert 1 <= len(late) <= len(folios) // 2


def test_generate_folios_nullable_description() -> None:
    props = generate_properties(seed=42, count=1)
    res = generate_reservations(seed=42, properties=props, count_per_property=50)
    folios = generate_folios(seed=42, reservations=res, properties=props)
    nulls = [f for f in folios if f["description"] is None]
    assert 1 <= len(nulls) <= len(folios) // 2
    for f in folios:
        if f["description"] is not None:
            assert isinstance(f["description"], str)


def test_generate_folios_money_format_and_negative() -> None:
    props = generate_properties(seed=1, count=1)
    res = generate_reservations(seed=1, properties=props, count_per_property=30)
    folios = generate_folios(seed=1, reservations=res, properties=props)
    for f in folios:
        amt = str(f["amount"])
        assert "." in amt
        assert amt.lstrip("-").split(".")[1].__len__() == 2
        Decimal(amt)  # valid decimal
    # Check at least one negative over large sample (5% rate)
    props2 = generate_properties(seed=99, count=1)
    res2 = generate_reservations(seed=99, properties=props2, count_per_property=100)
    folios2 = generate_folios(seed=99, reservations=res2, properties=props2)
    negatives = [f for f in folios2 if Decimal(str(f["amount"])) < 0]
    assert len(negatives) >= 1


def test_generate_folios_business_date_within_stay() -> None:
    props = generate_properties(seed=42, count=2)
    res = generate_reservations(seed=42, properties=props, count_per_property=5)
    folios = generate_folios(seed=42, reservations=res, properties=props)
    res_map = {str(r["reservation_id"]): r for r in res}
    for f in folios:
        rid = str(f["reservation_id"])
        r = res_map[rid]
        bdate = datetime.date.fromisoformat(str(f["business_date"]))
        arr = datetime.date.fromisoformat(str(r["arrival_date"]))
        dep = datetime.date.fromisoformat(str(r["departure_date"]))
        assert arr <= bdate <= dep, f"{f} not within {r}"


def test_generate_folios_sorted() -> None:
    props = generate_properties(seed=5, count=1)
    res = generate_reservations(seed=5, properties=props, count_per_property=3)
    folios = generate_folios(seed=5, reservations=res, properties=props)
    ids = [str(f["charge_id"]) for f in folios]
    assert ids == sorted(ids)


def test_generate_folios_default_args() -> None:
    folios = generate_folios(seed=55)
    assert len(folios) > 0


def test_generate_folios_empty_reservations() -> None:
    assert generate_folios(seed=1, reservations=[], properties=generate_properties(seed=1, count=1)) == []


def test_generate_folios_same_day_stay() -> None:
    props = generate_properties(seed=1, count=1)
    # Manual same-day reservation to hit bdate=arrival branch (stay_days <=0)
    res = [
        {
            "reservation_id": "RES-1",
            "property_id": str(props[0]["property_id"]),
            "arrival_date": "2026-10-15",
            "departure_date": "2026-10-15",
            "currency": str(props[0]["currency"]),
            "status": "confirmed",
            "total_amount": "100.00",
            "guest_name": "Ada",
            "updated_at": "2026-10-15T12:00:00+00:00",
        }
    ]
    folios = generate_folios(seed=1, reservations=res, properties=props, min_per_reservation=1, max_per_reservation=1)
    assert len(folios) == 1
    assert folios[0]["business_date"] == "2026-10-15"


def test_generate_folios_invalid_bounds() -> None:
    props = generate_properties(seed=1, count=1)
    res = generate_reservations(seed=1, properties=props, count_per_property=1)
    with pytest.raises(ValueError, match="bounds"):
        generate_folios(seed=1, reservations=res, properties=props, min_per_reservation=-1)
    with pytest.raises(ValueError, match="bounds"):
        generate_folios(seed=1, reservations=res, properties=props, min_per_reservation=3, max_per_reservation=2)


def test_generate_folios_currency_matches_reservation() -> None:
    props = generate_properties(seed=8, count=2)
    res = generate_reservations(seed=8, properties=props, count_per_property=3)
    folios = generate_folios(seed=8, reservations=res, properties=props)
    res_cur = {str(r["reservation_id"]): str(r["currency"]) for r in res}
    for f in folios:
        assert f["currency"] == res_cur[str(f["reservation_id"])]


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------


def test_generate_dataset_shape() -> None:
    ds = generate_dataset(seed=42)
    assert "properties" in ds
    assert "reservations" in ds
    assert "folios" in ds
    assert len(ds["properties"]) == 3
    assert len(ds["reservations"]) == 30
    assert len(ds["folios"]) > 0


def test_generate_dataset_deterministic_byte_identical() -> None:
    a = generate_dataset(seed=123)
    b = generate_dataset(seed=123)
    assert a == b
    assert dataset_to_json(a) == dataset_to_json(b)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_generate_dataset_different_seed() -> None:
    a = generate_dataset(seed=1)
    b = generate_dataset(seed=2)
    assert a != b
    assert dataset_fingerprint(a) != dataset_fingerprint(b)


def test_generate_dataset_json_round_trip() -> None:
    ds = generate_dataset(seed=42)
    text = dataset_to_json(ds)
    ds2 = dataset_from_json(text)
    assert ds == ds2


def test_generate_dataset_fingerprint_stable() -> None:
    ds = generate_dataset(seed=42)
    fp1 = dataset_fingerprint(ds)
    fp2 = dataset_fingerprint(generate_dataset(seed=42))
    assert fp1 == fp2
    assert len(fp1) == 64  # sha256 hex


def test_generate_dataset_custom_counts() -> None:
    ds = generate_dataset(seed=10, num_properties=1, reservations_per_property=2, min_folios_per_reservation=1, max_folios_per_reservation=1)
    assert len(ds["properties"]) == 1
    assert len(ds["reservations"]) == 2
    assert len(ds["folios"]) == 2


def test_generate_dataset_invariants() -> None:
    ds = generate_dataset(seed=99)
    props = ds["properties"]
    tzmap = property_timezone_map(props)
    for f in ds["folios"]:
        derived = business_date_for_posted_at(str(f["posted_at"]), tzmap[str(f["property_id"])])
        assert derived == f["business_date"]


def test_generate_dataset_with_duplicates() -> None:
    base = generate_dataset(seed=42, include_duplicates=False)
    dup = generate_dataset(seed=42, include_duplicates=True)
    # Dup should have >= base (duplicates inserted)
    assert len(dup["reservations"]) >= len(base["reservations"])
    assert len(dup["folios"]) >= len(base["folios"])
    # Dedup should recover base
    dedup_res = deduplicate(dup["reservations"], key="reservation_id")
    dedup_fol = deduplicate(dup["folios"], key="charge_id")
    assert len(dedup_res) == len(base["reservations"])
    assert len(dedup_fol) == len(base["folios"])
    # Byte-identical for same seed+dup flag
    dup2 = generate_dataset(seed=42, include_duplicates=True)
    assert dataset_to_json(dup) == dataset_to_json(dup2)


def test_generate_dataset_deterministic_across_calls() -> None:
    # Same seed with same kwargs must be byte-identical via JSON dumps
    for seed in [0, 1, 42, 999]:
        a = json.dumps(generate_dataset(seed=seed), sort_keys=True)
        b = json.dumps(generate_dataset(seed=seed), sort_keys=True)
        assert a == b


# ---------------------------------------------------------------------------
# Duplicates
# ---------------------------------------------------------------------------


def test_with_duplicates_basic() -> None:
    rng = random.Random(42)
    records = [{"id": str(i), "v": i} for i in range(10)]
    duped = with_duplicates(records, rng=rng, rate=0.2)
    assert len(duped) > len(records)
    assert len(duped) == len(records) + int(len(records) * 0.2)


def test_with_duplicates_deterministic() -> None:
    rng1 = random.Random(123)
    rng2 = random.Random(123)
    records = [{"id": str(i)} for i in range(5)]
    assert with_duplicates(records, rng=rng1, rate=0.4) == with_duplicates(records, rng=rng2, rate=0.4)


def test_with_duplicates_empty() -> None:
    assert with_duplicates([], rng=random.Random(0), rate=0.5) == []


def test_with_duplicates_rate_zero() -> None:
    records = [{"id": "a"}]
    rng = random.Random(1)
    assert with_duplicates(records, rng=rng, rate=0.0) == records


def test_with_duplicates_rate_one() -> None:
    records = [{"id": str(i)} for i in range(3)]
    rng = random.Random(1)
    duped = with_duplicates(records, rng=rng, rate=1.0)
    assert len(duped) == 6


def test_with_duplicates_invalid_rate() -> None:
    with pytest.raises(ValueError, match="rate must be"):
        with_duplicates([{"id": "a"}], rng=random.Random(0), rate=1.5)
    with pytest.raises(ValueError, match="rate must be"):
        with_duplicates([{"id": "a"}], rng=random.Random(0), rate=-0.1)


def test_with_duplicates_default_rng() -> None:
    records = [{"id": str(i)} for i in range(10)]
    duped = with_duplicates(records, rate=0.3)
    assert len(duped) >= len(records)


def test_with_duplicates_small_list_probabilistic_branch() -> None:
    # len=1, rate=0.1, with mocked rng that returns <rate to trigger n_dup=1
    class FakeRNG(random.Random):
        def random(self) -> float:  # type: ignore[override]
            return 0.05

        def sample(self, population: object, k: int = 0) -> list[int]:  # type: ignore[override]  # noqa: ARG002
            return [0]

        def randint(self, a: int, b: int) -> int:  # type: ignore[override]  # noqa: ARG002
            return a

    rng = FakeRNG()
    records = [{"id": "only"}]
    duped = with_duplicates(records, rng=rng, rate=0.1)
    assert len(duped) == 2
    assert duped.count({"id": "only"}) == 2


def test_deduplicate_keeps_first() -> None:
    records = [{"id": "a", "v": 1}, {"id": "b", "v": 2}, {"id": "a", "v": 99}]
    dedup = deduplicate(records, key="id")
    assert len(dedup) == 2
    assert dedup[0]["v"] == 1
    assert dedup[1]["id"] == "b"


def test_deduplicate_empty() -> None:
    assert deduplicate([], key="id") == []


# ---------------------------------------------------------------------------
# Cross-validation against canonical schemas/models
# ---------------------------------------------------------------------------


def test_generated_reservations_validate_against_pydantic() -> None:
    from autobridge.spec.entities import FolioCharge, Reservation

    ds = generate_dataset(seed=123)
    for rec in ds["reservations"]:
        r = Reservation.model_validate(rec)
        assert isinstance(r.total_amount, Decimal)
        assert isinstance(r.arrival_date, datetime.date)
    for rec in ds["folios"]:
        f = FolioCharge.model_validate(rec)
        assert isinstance(f.amount, Decimal)
        assert isinstance(f.business_date, datetime.date)


def test_generated_reservations_validate_against_json_schema() -> None:
    from autobridge.spec.entities import validate_against_schema

    ds = generate_dataset(seed=456)
    for rec in ds["reservations"]:
        validate_against_schema(rec, "reservation.schema.json")
    for rec in ds["folios"]:
        validate_against_schema(rec, "folio-charge.schema.json")


def test_generated_with_nulls_still_valid() -> None:
    from autobridge.spec.entities import validate_against_schema

    ds = generate_dataset(seed=42)
    # Find at least one nullable example and validate it
    null_guest = [r for r in ds["reservations"] if r["guest_name"] is None]
    assert len(null_guest) >= 1
    for r in null_guest[:3]:
        validate_against_schema(r, "reservation.schema.json")
    null_desc = [f for f in ds["folios"] if f["description"] is None]
    assert len(null_desc) >= 1
    for f in null_desc[:3]:
        validate_against_schema(f, "folio-charge.schema.json")


# ---------------------------------------------------------------------------
# Legacy generate_batch
# ---------------------------------------------------------------------------


def test_generate_batch_still_exists() -> None:
    from simulators.common.generator import generate_batch

    a = generate_batch(seed=42, count=5)
    assert len(a) == 5
    assert all("id" in r and "index" in r and "value" in r for r in a)
    # deterministic
    assert a == generate_batch(seed=42, count=5)
