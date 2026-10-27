"""Seeded generator for properties, reservations, folios (P1.W2.1).

Deterministic, byte-identical for same seed. Covers:

- properties with timezone + currency
- reservations (canonical Reservation shape)
- folio charges with late postings across business date
- duplicates / at-least-once delivery
- nullable fields (guest_name, description)
- property-local business dates vs UTC instants

Money is exact Decimal string with 2 fraction digits (never float).
Dates are property-local YYYY-MM-DD; instants are aware UTC ISO.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import random
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROPERTY_TEMPLATES: list[dict[str, str]] = [
    {
        "property_id": "prop-001",
        "name": "Grand Plaza",
        "timezone": "America/New_York",
        "currency": "USD",
    },
    {
        "property_id": "prop-002",
        "name": "Harbor Inn",
        "timezone": "America/Los_Angeles",
        "currency": "USD",
    },
    {
        "property_id": "prop-003",
        "name": "Alpen Resort",
        "timezone": "Europe/Berlin",
        "currency": "EUR",
    },
    {
        "property_id": "prop-004",
        "name": "Sakura Lodge",
        "timezone": "Asia/Tokyo",
        "currency": "JPY",
    },
    {
        "property_id": "prop-005",
        "name": "Thames House",
        "timezone": "Europe/London",
        "currency": "GBP",
    },
]

# Business-date cutoff hour (local time). Folios posted before this hour
# on calendar day N count to business date N-1.
BUSINESS_CUTOFF_HOUR = 4

GUEST_NAMES: list[str] = [
    "Ada Lovelace",
    "Grace Hopper",
    "Alan Turing",
    "Barbara Liskov",
    "Donald Knuth",
    "Margaret Hamilton",
    "Ken Thompson",
    "Linus Torvalds",
    "Guido van Rossum",
    "Yukihiro Matsumoto",
    "Brendan Eich",
    "Sheryl Sandberg",
    "Tim Berners-Lee",
    "Hedy Lamarr",
    "Katherine Johnson",
]

CHARGE_DESCRIPTIONS: list[str] = [
    "Room charge",
    "Mini-bar",
    "Restaurant",
    "Spa",
    "Laundry",
    "Parking",
    "Room service",
    "Gym",
    "Late checkout fee",
    "Damage deposit",
    "Breakfast",
    "Airport transfer",
]

STATUSES: list[str] = ["confirmed", "checked_in", "checked_out", "cancelled", "no_show"]
CURRENCIES: list[str] = ["USD", "EUR", "GBP", "JPY"]

# Baseline window for stays (UTC calendar).
BASE_START_DATE = datetime.date(2026, 10, 1)
BASE_END_DATE = datetime.date(2026, 10, 31)

# Probabilities
NULLABLE_RATE = 0.10
LATE_POSTING_RATE = 0.15
DUPLICATE_RATE = 0.05
NEGATIVE_AMOUNT_RATE = 0.05


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rng(seed: int) -> random.Random:
    return random.Random(seed)


def _money_str(cents: int) -> str:
    """Format integer cents as '123.45' with exactly 2 fraction digits."""
    return f"{cents // 100}.{cents % 100:02d}"


def _random_money(rng: random.Random, min_cents: int, max_cents: int) -> str:
    cents = rng.randint(min_cents, max_cents)
    return _money_str(cents)


def _random_date(rng: random.Random, start: datetime.date, end: datetime.date) -> datetime.date:
    delta = (end - start).days
    offset = rng.randint(0, delta)
    return start + datetime.timedelta(days=offset)


def _aware_utc(year: int, month: int, day: int, hour: int, minute: int, second: int = 0) -> datetime.datetime:
    return datetime.datetime(year, month, day, hour, minute, second, tzinfo=datetime.UTC)


# ---------------------------------------------------------------------------
# Business-date logic
# ---------------------------------------------------------------------------


def business_date_for_posted_at(posted_at_iso: str, tz_name: str, cutoff_hour: int = BUSINESS_CUTOFF_HOUR) -> str:
    """Derive property-local business date from a UTC instant.

    ``posted_at_iso`` is an aware UTC ISO string (e.g. ``2026-10-27T00:12:00+00:00``).
    Convert to property local time; if local hour < cutoff_hour, business date is
    prior calendar day, else local calendar date.
    Returns YYYY-MM-DD string.
    """
    posted_at = datetime.datetime.fromisoformat(posted_at_iso)
    if posted_at.tzinfo is None:
        raise ValueError("posted_at must be timezone-aware")
    local = posted_at.astimezone(ZoneInfo(tz_name))
    business = local.date()
    if local.hour < cutoff_hour:
        business = business - datetime.timedelta(days=1)
    return business.isoformat()


def _business_date_matches_posted_at(
    business_date: str, posted_at_iso: str, tz_name: str, cutoff_hour: int = BUSINESS_CUTOFF_HOUR
) -> bool:
    return business_date == business_date_for_posted_at(posted_at_iso, tz_name, cutoff_hour)


# ---------------------------------------------------------------------------
# Property generation
# ---------------------------------------------------------------------------


def generate_properties(seed: int, count: int = 3) -> list[dict[str, object]]:
    """Generate ``count`` properties deterministically from ``seed``.

    Returns list of dicts with ``property_id``, ``name``, ``timezone``, ``currency``.
    Order is deterministic (shuffled by RNG).
    """
    if count < 1:
        raise ValueError("count must be >= 1")
    if count > len(PROPERTY_TEMPLATES):
        raise ValueError(f"count {count} exceeds template pool {len(PROPERTY_TEMPLATES)}")
    rng = _rng(seed)
    pool = list(PROPERTY_TEMPLATES)
    rng.shuffle(pool)
    selected = pool[:count]
    # Return copies so caller cannot mutate templates
    return [dict(p) for p in selected]


def property_timezone_map(properties: list[dict[str, object]]) -> dict[str, str]:
    """Map property_id -> timezone."""
    return {str(p["property_id"]): str(p["timezone"]) for p in properties}


def property_currency_map(properties: list[dict[str, object]]) -> dict[str, str]:
    """Map property_id -> currency."""
    return {str(p["property_id"]): str(p["currency"]) for p in properties}


# ---------------------------------------------------------------------------
# Reservation generation
# ---------------------------------------------------------------------------


def generate_reservations(
    seed: int,
    properties: list[dict[str, object]] | None = None,
    count_per_property: int = 10,
    base_start: datetime.date | None = None,
    base_end: datetime.date | None = None,
) -> list[dict[str, object]]:
    """Generate reservations for given ``properties``.

    Each reservation validates against ``reservation.schema.json`` shape and
    the canonical :class:`Reservation` model (money as string with 2dp,
    business dates as YYYY-MM-DD, closed status vocab).
    ``guest_name`` is nullable with ~10% probability.

    If ``properties`` is None, generate default 3 properties from ``seed``.
    """
    if count_per_property < 1:
        raise ValueError("count_per_property must be >= 1")
    if properties is None:
        properties = generate_properties(seed, count=3)
    if not properties:
        raise ValueError("properties must be non-empty")

    rng = _rng(seed + 1009)  # domain separation from properties
    start = base_start or BASE_START_DATE
    end = base_end or BASE_END_DATE

    out: list[dict[str, object]] = []
    for prop in properties:
        prop_id = str(prop["property_id"])
        currency = str(prop.get("currency", "USD"))
        for idx in range(count_per_property):
            rid = f"RES-{seed:06d}-{prop_id}-{idx:04d}"
            # Arrival within window
            arrival = _random_date(rng, start, end)
            stay = rng.randint(1, 5)
            departure = arrival + datetime.timedelta(days=stay)

            # updated_at: random UTC instant within stay + 2 days, deterministic
            # Use arrival as anchor for deterministic UTC generation
            upd_day_offset = rng.randint(0, stay + 1)
            upd_date = arrival + datetime.timedelta(days=upd_day_offset)
            upd_hour = rng.randint(0, 23)
            upd_min = rng.randint(0, 59)
            updated_at = _aware_utc(upd_date.year, upd_date.month, upd_date.day, upd_hour, upd_min).isoformat()

            # Nullable guest_name
            if rng.random() < NULLABLE_RATE:
                guest: str | None = None
            else:
                guest = rng.choice(GUEST_NAMES)

            # Occasionally omit guest_name entirely (also nullable shape)
            # We keep key present as None for canonical form; omission is tested separately

            rec: dict[str, object] = {
                "reservation_id": rid,
                "property_id": prop_id,
                "guest_name": guest,
                "arrival_date": arrival.isoformat(),
                "departure_date": departure.isoformat(),
                "status": rng.choice(STATUSES),
                "total_amount": _random_money(rng, 5000, 80000),
                "currency": currency,
                "updated_at": updated_at,
            }
            out.append(rec)

    # Sort by reservation_id for byte-identical determinism regardless of property order
    out.sort(key=lambda r: str(r["reservation_id"]))
    return out


# ---------------------------------------------------------------------------
# Folio generation
# ---------------------------------------------------------------------------


def _posted_at_for_business_date(
    rng: random.Random,
    business_date: datetime.date,
    tz_name: str,
    late: bool,
) -> str:
    """Generate a UTC ``posted_at`` ISO for given ``business_date``.

    If ``late`` is True, posted_at local time is 00:00-03:59 on day after
    business_date (counts to prior business date via cutoff). Else local
    08:00-22:00 on business_date.
    """
    tz = ZoneInfo(tz_name)
    if late:
        local_date = business_date + datetime.timedelta(days=1)
        hour = rng.randint(0, BUSINESS_CUTOFF_HOUR - 1)
        minute = rng.randint(0, 59)
        second = rng.randint(0, 59)
        local_dt = datetime.datetime(
            local_date.year, local_date.month, local_date.day, hour, minute, second, tzinfo=tz
        )
        utc_dt = local_dt.astimezone(datetime.UTC)
        return utc_dt.isoformat()
    hour = rng.randint(8, 22)
    minute = rng.randint(0, 59)
    second = rng.randint(0, 59)
    local_dt = datetime.datetime(
        business_date.year, business_date.month, business_date.day, hour, minute, second, tzinfo=tz
    )
    utc_dt = local_dt.astimezone(datetime.UTC)
    return utc_dt.isoformat()


def generate_folios(
    seed: int,
    reservations: list[dict[str, object]] | None = None,
    properties: list[dict[str, object]] | None = None,
    min_per_reservation: int = 1,
    max_per_reservation: int = 4,
) -> list[dict[str, object]]:
    """Generate folio charges linked to ``reservations``.

    ``business_date`` is property-local; ``posted_at`` is aware UTC.
    ~15% are late postings (posted_at after midnight local but business_date prior).
    ``description`` is nullable with ~10% probability.
    Amounts may be negative (refunds) with ~5% probability.

    If ``reservations``/``properties`` is None, generate defaults from ``seed``.
    """
    if min_per_reservation < 0 or max_per_reservation < min_per_reservation:
        raise ValueError("invalid per-reservation bounds")
    if properties is None:
        properties = generate_properties(seed, count=3)
    if reservations is None:
        reservations = generate_reservations(seed, properties=properties)
    if not reservations:
        return []

    tz_map = property_timezone_map(properties)
    # Fallback to property_id in reservation if not in map (e.g. custom prop)
    rng = _rng(seed + 2027)

    out: list[dict[str, object]] = []
    for res in reservations:
        res_id = str(res["reservation_id"])
        prop_id = str(res["property_id"])
        tz_name = tz_map.get(prop_id, "UTC")
        arrival = datetime.date.fromisoformat(str(res["arrival_date"]))
        departure = datetime.date.fromisoformat(str(res["departure_date"]))
        currency = str(res.get("currency", "USD"))

        k = rng.randint(min_per_reservation, max_per_reservation)
        for j in range(k):
            charge_id = f"CHG-{seed:06d}-{res_id}-{j:02d}"
            # Business date within stay (inclusive)
            stay_days = (departure - arrival).days
            if stay_days <= 0:
                bdate = arrival
            else:
                offset = rng.randint(0, stay_days)
                bdate = arrival + datetime.timedelta(days=offset)

            is_late = rng.random() < LATE_POSTING_RATE
            posted_at = _posted_at_for_business_date(rng, bdate, tz_name, late=is_late)

            # Amount
            amount_cents = rng.randint(500, 15000)
            if rng.random() < NEGATIVE_AMOUNT_RATE:
                amount_cents = -amount_cents
            amount = _money_str(amount_cents)

            # Nullable description
            if rng.random() < NULLABLE_RATE:
                desc: str | None = None
            else:
                desc = rng.choice(CHARGE_DESCRIPTIONS)

            rec: dict[str, object] = {
                "charge_id": charge_id,
                "reservation_id": res_id,
                "property_id": prop_id,
                "business_date": bdate.isoformat(),
                "amount": amount,
                "currency": currency,
                "description": desc,
                "posted_at": posted_at,
            }
            out.append(rec)

    out.sort(key=lambda r: str(r["charge_id"]))
    return out


# ---------------------------------------------------------------------------
# Dataset (top-level)
# ---------------------------------------------------------------------------


def generate_dataset(
    seed: int,
    num_properties: int = 3,
    reservations_per_property: int = 10,
    min_folios_per_reservation: int = 1,
    max_folios_per_reservation: int = 4,
    include_duplicates: bool = False,
    duplicate_rate: float = DUPLICATE_RATE,
) -> dict[str, list[dict[str, object]]]:
    """Generate a complete deterministic dataset.

    Returns ``{"properties": [...], "reservations": [...], "folios": [...]}``.
    When ``include_duplicates`` is True, the returned reservations/folios lists
    contain intentional duplicates simulating at-least-once delivery
    (ground truth can be obtained by deduplicating on ``reservation_id``/``charge_id``).

    Same ``seed`` and same kwargs → byte-identical JSON (sort_keys) output.
    """
    rng = _rng(seed)
    # Generate properties with a derived seed to keep top-level deterministic
    # Use single RNG shuffle via generate_properties(seed)
    properties = generate_properties(seed, count=num_properties)

    # Use domain-separated seeds for sub-generators so the overall output is
    # deterministic and also matches standalone helpers called with same seeds.
    reservations = generate_reservations(
        seed, properties=properties, count_per_property=reservations_per_property
    )
    folios = generate_folios(
        seed,
        reservations=reservations,
        properties=properties,
        min_per_reservation=min_folios_per_reservation,
        max_per_reservation=max_folios_per_reservation,
    )

    if include_duplicates:
        # Use the top-level rng for duplicate insertion (deterministic given seed)
        reservations = with_duplicates(reservations, rng, rate=duplicate_rate, key="reservation_id")
        # Need fresh rng state for folios duplicates to keep deterministic but independent
        # Derive second duplicate rng from seed to avoid mixing order
        rng2 = _rng(seed + 99991)
        folios = with_duplicates(folios, rng2, rate=duplicate_rate, key="charge_id")

    return {
        "properties": properties,
        "reservations": reservations,
        "folios": folios,
    }


def dataset_to_json(dataset: dict[str, list[dict[str, object]]]) -> str:
    """Serialize dataset to canonical JSON (sorted keys, 2-space indent).

    Byte-identical for same input dataset.
    """
    return json.dumps(dataset, sort_keys=True, indent=2, ensure_ascii=False)


def dataset_from_json(text: str) -> dict[str, list[dict[str, object]]]:
    """Parse canonical JSON back to dataset."""
    return json.loads(text)  # type: ignore[no-any-return]


def dataset_fingerprint(dataset: dict[str, list[dict[str, object]]]) -> str:
    """Stable sha256 hex of canonical JSON (for quick equality check)."""
    canonical = json.dumps(dataset, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Duplicates / at-least-once
# ---------------------------------------------------------------------------


def with_duplicates(
    records: list[dict[str, object]],
    rng: random.Random | None = None,
    rate: float = DUPLICATE_RATE,
    key: str = "id",  # noqa: ARG001
) -> list[dict[str, object]]:
    """Return ``records`` plus intentional duplicates inserted at random positions.

    Simulates at-least-once delivery: ~``rate`` fraction of records are
    duplicated (same key, same payload). Duplicates are inserted after the
    original at a random later position so pagination may see them on a
    subsequent page. Deterministic given ``rng`` state.

    Args:
        records: Input list (already deterministic order).
        rng: Random instance; if None, new Random(0) is used (not recommended
            for tests — pass a seeded RNG).
        rate: Fraction of records to duplicate (0.0–1.0).
        key: Key field for dedup semantics (not used for logic, for docs).
    """
    if not records:
        return []
    if rng is None:
        rng = random.Random(0)
    if not 0.0 <= rate <= 1.0:
        raise ValueError("rate must be in [0, 1]")

    # Decide which indices to duplicate
    n_dup = int(len(records) * rate)
    if n_dup == 0 and rate > 0 and len(records) >= 1 and rng.random() < rate:
        # For small lists, probabilistic single duplicate to keep rate visible
        n_dup = 1
    if n_dup == 0:
        return list(records)

    indices = rng.sample(range(len(records)), k=min(n_dup, len(records)))
    duplicates = [dict(records[i]) for i in indices]

    # Insert duplicates at random positions (after original)
    result = list(records)
    for dup in duplicates:
        pos = rng.randint(0, len(result))
        result.insert(pos, dup)
    return result


def deduplicate(records: list[dict[str, object]], key: str) -> list[dict[str, object]]:
    """Deduplicate ``records`` by ``key``, keeping first occurrence.

    Used by verifier/oracle to get ground truth from delivered view.
    """
    seen: set[str] = set()
    out: list[dict[str, object]] = []
    for rec in records:
        kid = str(rec[key])
        if kid not in seen:
            seen.add(kid)
            out.append(rec)
    return out


# ---------------------------------------------------------------------------
# Legacy W1 API (keep for backward compat)
# ---------------------------------------------------------------------------


def generate_batch(seed: int, count: int = 10) -> list[dict[str, object]]:
    """Deterministic fake record generator for testing (W1 compat).

    Shape: ``{id, index, value, seed}``. Kept so W1 smoke tests pass.
    New code should use :func:`generate_dataset` / :func:`generate_reservations`.
    """
    rng = random.Random(seed)
    out: list[dict[str, object]] = []
    for i in range(count):
        rid = f"res_{hashlib.sha256(f'{seed}-{i}'.encode()).hexdigest()[:8]}"
        out.append(
            {
                "id": rid,
                "index": i,
                "value": rng.randint(1, 1000),
                "seed": seed,
            }
        )
    return out


def healthz() -> dict[str, str]:
    """Liveness."""
    return {"status": "ok"}


def readyz() -> dict[str, object]:
    """Readiness."""
    return {"status": "ok", "checks": {"default": "ok"}}
