"""Beacon fault generator — faults from public randomness (REQ-025 items 1–2).

A beacon evaluation derives its test data *and* fault details from a public
random value that did not exist when the engine code froze. This module is
the fault-generation half of that protocol:

- :func:`derive_eval_seed` turns a drand round's public ``randomness`` into
  a 32-byte eval seed (``sha256(randomness || "autobridge-eval-v1")``).
- :func:`derive_instance_seed` turns the eval seed plus an instance index
  into a per-instance seed (``sha256(eval_seed || index_be8)``).
- :func:`generate_instances` deterministically builds ``N`` fault instances
  across the five vendors from one eval seed. Each instance is a *family*
  plus seed-chosen *parameters* — never a fixed scenario id. Families and
  parameter pools are fixed in code below; only the parameters come from
  the seed, so the engine cannot have prepared for an instance even though
  the families are its everyday knowledge.
- :func:`materialize` shows what the mutated vendor serves for an instance
  (mutated dataset and/or a behaviour descriptor).
- :func:`inverse_canonicalize` is the constructive solvability proof used
  by the verifier's reference solver: for every ``repair`` instance it
  recovers the baseline dataset byte-identically (money stays
  :class:`~decimal.Decimal`, never float; semantic factors are exactly
  invertible rationals).

Trust boundary: this module lives in ``simulators/common`` and imports only
the shared seeded generator plus stdlib. The engine never imports it; the
verifier may (sanctioned ``simulators/common`` import); the bench harness
passes only seeds, never instances.

Outcome classes (answer-key vocabulary, REQ-025 item 1):

- ``repair`` — a verifier-accepted patch exists on hidden cases;
- ``no_patch`` — handled correctly with retries/refresh, no patch, no
  escalation (transport transients, link resync);
- ``escalate`` — no patch; the correct behaviour is to escalate
  (revoked token, removed endpoint, meaningless code);
- ``degraded_question`` — ambiguous semantic: ``Degraded`` with one
  operator question, never a guessed patch (decoy field present).
"""

from __future__ import annotations

import contextlib
import copy
import datetime
import hashlib
import json
import random
from decimal import Decimal
from typing import Any

from simulators.common.generator import (
    business_date_for_posted_at,
    generate_dataset,
    property_timezone_map,
)

# ---------------------------------------------------------------------------
# Protocol constants (fixed in code before the freeze; only parameters vary)
# ---------------------------------------------------------------------------

#: Domain label mixed into every seed derivation (see module docstring).
BEACON_LABEL = "autobridge-eval-v1"

#: Vendors covered by one beacon run, in stable round-robin order.
VENDORS: tuple[str, ...] = ("atlas", "harbor", "legacystay", "meridian", "fias-pabx")

#: HTTP fault families (served by the four REST vendors).
HTTP_FAMILIES: tuple[str, ...] = (
    "rename",
    "nesting",
    "pagination",
    "code_list",
    "semantic",
    "dates",
    "transport_auth",
)

#: FIAS fault families (``transport: fias_tcp``; served by sim-fias-pabx).
FIAS_FAMILIES: tuple[str, ...] = ("fias_layout", "fias_link")

#: All families, for validation.
ALL_FAMILIES: frozenset[str] = frozenset(HTTP_FAMILIES + FIAS_FAMILIES)

#: Outcome classes (answer-key vocabulary).
OUTCOMES: frozenset[str] = frozenset({"repair", "no_patch", "escalate", "degraded_question"})

# ---------------------------------------------------------------------------
# Parameter pools (fixed in code; the seed only picks from them)
# ---------------------------------------------------------------------------

#: Rename targets per entity (ids/keys/currency are never renamed).
RENAME_FIELDS: dict[str, tuple[str, ...]] = {
    "reservation": ("guest_name", "status", "total_amount", "arrival_date", "departure_date", "updated_at"),
    "folio": ("description", "amount", "business_date", "posted_at"),
}

#: (vendor, entity, field) triples the vendor's wire never serves, so
#: renaming them is a silent no-op while the key expects a repair
#: (REQ-063 item 3 — the bench sanity gate flags such instances loudly).
#: Meridian serves reservation money only through the folio join
#: (``UnitAmount``) and folio business dates only derived from
#: ``PostedUtc``; the dataset keys themselves never reach the wire.
RENAME_UNSERVED: dict[str, dict[str, tuple[str, ...]]] = {
    "meridian": {
        "reservation": ("total_amount",),
        "folio": ("business_date",),
    },
}

#: Fresh field names a rename may introduce. None of these may collide
#: with an existing record key (enforced at generation time as well).
NAME_POOL: tuple[str, ...] = (
    "primaryOccupant",
    "guestFullName",
    "visitorName",
    "occupant_name",
    "bookerName",
    "reservationStatus",
    "bookingState",
    "stay_status",
    "folioState",
    "grandTotal",
    "totalNet",
    "chargeTotal",
    "amountGross",
    "netTotal",
    "checkinDate",
    "arrivalDay",
    "checkoutDate",
    "departureDay",
    "postingDate",
    "businessDay",
    "lastModified",
    "recordUpdatedAt",
    "chargeMemo",
    "lineDescription",
    "narrative",
    "memoText",
    "postedInstant",
    "transactionTime",
    "createdInstant",
    "valueTimestamp",
)

#: Nesting parent keys (never collide with record keys; enforced too).
PARENT_POOL: tuple[str, ...] = (
    "guest",
    "stay",
    "details",
    "folioInfo",
    "extra",
    "meta",
    "booking",
    "charge",
    "localInfo",
    "audit",
)

#: Pagination request/response parameter names per REST vendor kind.
PAGE_PARAM_POOL: tuple[str, ...] = ("page", "pageNumber", "p", "pageIndex")
PER_PAGE_PARAM_POOL: tuple[str, ...] = ("perPage", "pageSize", "limit", "count", "size")
OFFSET_PARAM_POOL: tuple[str, ...] = ("offset", "start", "skip", "from")
CURSOR_PARAM_POOL: tuple[str, ...] = ("cursor", "pageToken", "nextToken", "continueToken")
LIMIT_PARAM_POOL: tuple[str, ...] = ("limit", "maxResults", "pageSize", "count")
ITEMS_PATH_POOL: tuple[tuple[str, ...], ...] = (
    ("data",),
    ("results",),
    ("items",),
    ("reservations",),
    ("payload", "records"),
)
NEXT_PATH_POOL: tuple[tuple[str, ...], ...] = (
    ("nextCursor",),
    ("pageToken",),
    ("paging", "next"),
    ("meta", "continuation"),
)
#: Meridian-style body-cursor leaf names (``Limitation`` contract family).
CURSOR_LEAF_POOL: tuple[str, ...] = ("Limitation", "limit", "pageToken", "continuation", "StartRow")

#: Fresh status codes (never collide with the canonical vocab below).
CANONICAL_STATUSES: tuple[str, ...] = ("confirmed", "checked_in", "checked_out", "cancelled", "no_show")
CODE_POOL: tuple[str, ...] = (
    "active",
    "in_house",
    "departed",
    "void",
    "noshow",
    "arrived",
    "due_in",
    "due_out",
    "stayover",
    "blocked",
    "tentative",
    "guaranteed",
)

#: Semantic factors are exactly invertible rationals, so the reference
#: solver can prove solvability by inversion (Decimal division by 2 and 4
#: is exact; 0.9-style factors would lose a cent on some amounts).
SEMANTIC_FACTORS: tuple[Decimal, ...] = (Decimal("0.5"), Decimal("0.25"))

#: Decoy field names for the ambiguous-semantic trap (gross stays intact).
DECOY_POOL: tuple[str, ...] = (
    "net_amount",
    "netTotal",
    "adjusted_total",
    "totalNet",
    "chargeNet",
)

#: Date-family modes.
DATE_MODES: tuple[str, ...] = ("cutoff", "tz_shift", "format")
DATE_FORMATS: tuple[str, ...] = ("%Y-%m-%d", "%d/%m/%Y", "%m-%d-%Y")

#: Transport/auth fault kinds.
TRANSPORT_KINDS: tuple[str, ...] = (
    "throttle",
    "retry_after",
    "outage",
    "token_expired",
    "token_revoked",
    "endpoint_removed",
)
#: Small Retry-After values (honest, AR-2-1-capped magnitudes).
RETRY_AFTER_POOL: tuple[int, ...] = (1, 2, 5)

#: FIAS PS base field ids announced by the PABX link record (LR).
FIAS_BASE_FIELDS: tuple[str, ...] = ("RN", "DA", "TI", "TA", "P#")
#: Fresh FIAS field ids a layout change may add or rename to.
FIAS_FIELD_POOL: tuple[str, ...] = ("GX", "R#", "AM", "TX", "CD", "SQ", "RG", "NT", "DT", "TM")

#: FIAS link fault kinds.
FIAS_LINK_KINDS: tuple[str, ...] = ("resync", "ack_timeout")

#: Baseline business-date cutoff hour (mirrors the shared generator).
BASELINE_CUTOFF_HOUR = 4


# ---------------------------------------------------------------------------
# Seed derivation
# ---------------------------------------------------------------------------


def derive_eval_seed(randomness_hex: str) -> bytes:
    """Derive the 32-byte eval seed from a beacon round's public randomness.

    ``eval_seed = sha256(randomness || "|" || BEACON_LABEL)``. Fails closed
    on malformed input (not 32 bytes of hex): raises :class:`ValueError`.
    """
    try:
        raw = bytes.fromhex(randomness_hex.strip())
    except (ValueError, AttributeError) as exc:
        raise ValueError("randomness must be hex") from exc
    if len(raw) != 32:
        raise ValueError(f"randomness must be 32 bytes, got {len(raw)}")
    return hashlib.sha256(raw + b"|" + BEACON_LABEL.encode("ascii")).digest()


def derive_instance_seed(eval_seed: bytes, index: int) -> bytes:
    """Derive the per-instance seed: ``sha256(eval_seed || index_be8)``."""
    if not isinstance(eval_seed, bytes) or len(eval_seed) != 32:
        raise ValueError("eval_seed must be 32 bytes")
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise ValueError("index must be a non-negative int")
    return hashlib.sha256(eval_seed + index.to_bytes(8, "big")).digest()


def _rng_for(instance_seed: bytes, domain: str) -> random.Random:
    """Domain-separated deterministic RNG for one generation step."""
    digest = hashlib.sha256(instance_seed + b"|" + domain.encode("ascii")).hexdigest()
    return random.Random(int(digest, 16))


def dataset_seed_for(instance_seed: bytes) -> int:
    """Deterministic dataset seed (fits the shared generator's range)."""
    return int.from_bytes(instance_seed[:4], "big") % 1_000_000_000


# ---------------------------------------------------------------------------
# Instance generation (item 1: family + seed-chosen parameters)
# ---------------------------------------------------------------------------


def _params_for(family: str, vendor: str, rng: random.Random) -> dict[str, Any]:
    """Pick family parameters from the fixed pools with ``rng``."""
    if family == "rename":
        entity = rng.choice(["reservation", "folio"])
        unserved = RENAME_UNSERVED.get(vendor, {}).get(entity, ())
        served = [f for f in RENAME_FIELDS[entity] if f not in unserved]
        field = rng.choice(served)
        taken = _record_keys(entity)
        choices = [n for n in NAME_POOL if n not in taken and n != field]
        return {"entity": entity, "field": field, "new_name": rng.choice(choices)}
    if family == "nesting":
        entity = rng.choice(["reservation", "folio"])
        taken = _record_keys(entity)
        choices = [p for p in PARENT_POOL if p not in taken]
        depth = rng.choice([1, 2])
        parents = rng.sample(choices, depth)
        return {"entity": entity, "depth": depth, "parents": parents}
    if family == "pagination":
        if vendor == "meridian":
            return {
                "strategy": "body_cursor",
                "cursor_leaf": rng.choice(list(CURSOR_LEAF_POOL)),
                "page_size_param": rng.choice(["Count", "limit", "pageSize"]),
            }
        strategy = rng.choice(["offset", "page", "cursor"])
        if strategy == "offset":
            return {
                "strategy": strategy,
                "offset_param": rng.choice(list(OFFSET_PARAM_POOL)),
                "limit_param": rng.choice(list(LIMIT_PARAM_POOL)),
                "items_path": list(rng.choice(ITEMS_PATH_POOL)),
            }
        if strategy == "page":
            return {
                "strategy": strategy,
                "page_param": rng.choice(list(PAGE_PARAM_POOL)),
                "per_page_param": rng.choice(list(PER_PAGE_PARAM_POOL)),
                "items_path": list(rng.choice(ITEMS_PATH_POOL)),
            }
        return {
            "strategy": strategy,
            "cursor_param": rng.choice(list(CURSOR_PARAM_POOL)),
            "limit_param": rng.choice(list(LIMIT_PARAM_POOL)),
            "items_path": list(rng.choice(ITEMS_PATH_POOL)),
            "next_path": list(rng.choice(list(NEXT_PATH_POOL))),
        }
    if family == "code_list":
        n_new = rng.randint(2, 4)
        new_codes = rng.sample(list(CODE_POOL), n_new)
        mapping = {code: rng.choice(list(CANONICAL_STATUSES)) for code in new_codes}
        unknown = rng.random() < 0.25
        unknown_code = None
        if unknown:
            unknown_code = rng.choice(new_codes)
            del mapping[unknown_code]
        return {"new_codes": new_codes, "mapping": mapping, "unknown_code": unknown_code}
    if family == "semantic":
        kind = rng.choice(["gross_net", "minor_major", "decoy"])
        if kind == "decoy":
            return {"kind": kind, "decoy_field": rng.choice(list(DECOY_POOL))}
        return {"kind": kind, "factor": str(rng.choice(list(SEMANTIC_FACTORS)))}
    if family == "dates":
        mode = rng.choice(list(DATE_MODES))
        if mode == "cutoff":
            # Only cutoffs inside the generator's 00:00-03:59 late-posting
            # window can move a business date; 5/6 never manifest, so they
            # are not offered (REQ-063 item 3). The selection guard below
            # still verifies manifestation on the instance's dataset.
            choices = [h for h in (1, 2, 3) if h != BASELINE_CUTOFF_HOUR]
            return {"mode": mode, "cutoff_hour": rng.choice(choices)}
        if mode == "tz_shift":
            choices = [h for h in (-3, -2, -1, 1, 2, 3)]
            return {"mode": mode, "shift_hours": rng.choice(choices)}
        # The baseline vendor format is ISO (%Y-%m-%d): serving it back is a
        # silent no-op while the key expects a repair (REQ-063 item 3), so it
        # is never chosen.
        choices = [f for f in DATE_FORMATS if f != "%Y-%m-%d"]
        return {"mode": mode, "date_format": rng.choice(choices)}
    if family == "transport_auth":
        kind = rng.choice(list(TRANSPORT_KINDS))
        params: dict[str, Any] = {"kind": kind}
        if kind == "throttle":
            params["every_k"] = rng.choice([2, 3, 4, 5])
            params["retry_after_s"] = rng.choice(list(RETRY_AFTER_POOL))
        elif kind == "retry_after":
            params["retry_after_s"] = rng.choice(list(RETRY_AFTER_POOL))
        elif kind == "outage":
            params["fail_first_m"] = rng.randint(1, 5)
        return params
    if family == "fias_layout":
        n_added = rng.randint(0, 2)
        added = rng.sample(list(FIAS_FIELD_POOL), n_added) if n_added else []
        renamable = [f for f in FIAS_BASE_FIELDS if f not in ("P#",)]
        # Every layout change renames at least one mapped wire id: an
        # added-only (or empty) announcement breaks no mapping, serves
        # byte-identical canonical output, and can never expect a repair
        # (REQ-063 item 3 — the sanity gate flags such silent no-ops).
        n_renamed = rng.randint(1, 2)
        renamed: dict[str, str] = {}
        if n_renamed:
            olds = rng.sample(renamable, min(n_renamed, len(renamable)))
            taken = set(FIAS_BASE_FIELDS) | set(added)
            pool = [f for f in FIAS_FIELD_POOL if f not in taken]
            for old in olds:
                if not pool:
                    break
                new = rng.choice(pool)
                pool.remove(new)
                taken.add(new)
                renamed[old] = new
        return {"added": added, "renamed": renamed, "reordered": rng.random() < 0.5}
    if family == "fias_link":
        kind = rng.choice(list(FIAS_LINK_KINDS))
        if kind == "resync":
            return {"kind": kind, "zero_bytes": rng.choice([4, 8, 16])}
        return {"kind": kind, "timeout_s": rng.choice([2, 5, 10])}
    raise ValueError(f"unknown family: {family}")


def _record_keys(entity: str) -> set[str]:
    if entity == "reservation":
        return {
            "reservation_id",
            "property_id",
            "guest_name",
            "arrival_date",
            "departure_date",
            "status",
            "total_amount",
            "currency",
            "updated_at",
        }
    if entity == "folio":
        return {
            "charge_id",
            "reservation_id",
            "property_id",
            "business_date",
            "amount",
            "currency",
            "description",
            "posted_at",
        }
    raise ValueError(f"unknown entity: {entity}")


def _families_for(vendor: str) -> tuple[str, ...]:
    if vendor == "fias-pabx":
        return FIAS_FAMILIES
    if vendor not in VENDORS:
        raise ValueError(f"unknown vendor: {vendor}")
    return HTTP_FAMILIES


def expected_for(family: str, params: dict[str, Any]) -> str:
    """Correct outcome class for a family + parameters (answer-key rule)."""
    if family in ("rename", "nesting", "pagination"):
        return "repair"
    if family == "code_list":
        return "escalate" if params.get("unknown_code") else "repair"
    if family == "semantic":
        return "degraded_question" if params.get("kind") == "decoy" else "repair"
    if family == "dates":
        return "repair"
    if family == "transport_auth":
        if params.get("kind") in ("token_revoked", "endpoint_removed"):
            return "escalate"
        return "no_patch"
    if family == "fias_layout":
        return "repair"
    if family == "fias_link":
        return "no_patch"
    raise ValueError(f"unknown family: {family}")


def _manifesting_cutoff(dataset_seed: int, picked: int) -> int:
    """Cutoff that moves at least one folio business date (REQ-063 item 3).

    Late postings cluster in 00:00–03:59 local, so cutoffs 5/6 usually
    change nothing: a silent no-op while the key expects a repair. Keep
    the seed-picked value when it manifests; otherwise take the first
    pool value that does (deterministic, no extra randomness). Selection
    is a pure function of the seed and ``posted_at`` is never touched, so
    the dates inverse still round-trips byte-identically. When nothing
    manifests (no late postings at all — vanishingly rare) the pick
    stands and the bench sanity gate catches it loudly.
    """
    dataset = generate_dataset(seed=dataset_seed)
    tz_map = property_timezone_map(dataset.get("properties", []))
    folios = dataset.get("folios", [])

    def _moves(cutoff: int) -> bool:
        for rec in folios:
            posted_at = rec.get("posted_at")
            if not isinstance(posted_at, str):
                continue
            tz_name = tz_map.get(str(rec.get("property_id", "")), "UTC")
            try:
                if business_date_for_posted_at(posted_at, tz_name, cutoff_hour=cutoff) != rec.get(
                    "business_date"
                ):
                    return True
            except Exception:
                continue
        return False

    if _moves(picked):
        return picked
    for cutoff in [h for h in (1, 2, 3) if h != BASELINE_CUTOFF_HOUR]:
        if _moves(cutoff):
            return cutoff
    return picked


def generate_instance(eval_seed: bytes, index: int) -> dict[str, Any]:
    """Build one beacon fault instance (``index`` selects vendor round-robin).

    Same construction as :func:`generate_instances`; split out so the
    simulator admin plane can serve a single instance without generating
    the whole run. Deterministic: same ``(eval_seed, index)`` → identical
    instance.
    """
    if not isinstance(eval_seed, bytes) or len(eval_seed) != 32:
        raise ValueError("eval_seed must be 32 bytes")
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise ValueError("index must be a non-negative int")
    iseed = derive_instance_seed(eval_seed, index)
    vendor = VENDORS[index % len(VENDORS)]
    families = _families_for(vendor)
    rng = _rng_for(iseed, "family")
    family = rng.choice(list(families))
    prng = _rng_for(iseed, f"params:{family}")
    params = _params_for(family, vendor, prng)
    dataset_seed = dataset_seed_for(iseed)
    if family == "dates" and params.get("mode") == "cutoff":
        params = dict(params, cutoff_hour=_manifesting_cutoff(dataset_seed, int(params["cutoff_hour"])))
    return {
        "vendor": vendor,
        "family": family,
        "index": index,
        "params": params,
        "seed_hex": iseed.hex(),
        "dataset_seed": dataset_seed,
        "expected": expected_for(family, params),
    }


def generate_instances(eval_seed: bytes, n: int) -> list[dict[str, Any]]:
    """Build ``n`` beacon fault instances from one eval seed.

    Vendors go round-robin (``index % 5``) so every run with ``n >= 5``
    covers all five vendors; the family and every parameter come from the
    instance seed. Deterministic: same ``(eval_seed, n)`` → identical list.
    """
    if not isinstance(eval_seed, bytes) or len(eval_seed) != 32:
        raise ValueError("eval_seed must be 32 bytes")
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise ValueError("n must be a positive int")
    if n > 10_000:
        raise ValueError("n too large (max 10000)")
    instances: list[dict[str, Any]] = []
    for index in range(n):
        instances.append(generate_instance(eval_seed, index))
    return instances


def apply_instance_records(
    dataset: dict[str, list[dict[str, Any]]], instance: dict[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    """Apply an instance's record-shape mutation to a caller-supplied dataset.

    Serving-layer entry point: the vendor apps generate their baseline from
    the *run* seed (simulator state), not from the instance's ``dataset_seed``
    — the mutation itself is a pure record transform, so it applies to any
    baseline. Record-shape families (rename, nesting, code_list, semantic,
    dates) return a mutated copy; behaviour families (pagination,
    transport_auth, fias_*) return the dataset unchanged — the app honors
    the behaviour contract through :func:`behavior_for_instance`.
    """
    _check_instance(instance)
    family = str(instance["family"])
    params = dict(instance["params"])
    if family == "rename":
        entity = str(params["entity"])
        return {
            "properties": [copy.deepcopy(p) for p in dataset.get("properties", [])],
            "reservations": _mutate_rename(dataset.get("reservations", []), params)
            if entity == "reservation"
            else [copy.deepcopy(r) for r in dataset.get("reservations", [])],
            "folios": _mutate_rename(dataset.get("folios", []), params)
            if entity == "folio"
            else [copy.deepcopy(r) for r in dataset.get("folios", [])],
        }
    if family == "nesting":
        entity = str(params["entity"])
        return {
            "properties": [copy.deepcopy(p) for p in dataset.get("properties", [])],
            "reservations": _mutate_nesting(dataset.get("reservations", []), params)
            if entity == "reservation"
            else [copy.deepcopy(r) for r in dataset.get("reservations", [])],
            "folios": _mutate_nesting(dataset.get("folios", []), params)
            if entity == "folio"
            else [copy.deepcopy(r) for r in dataset.get("folios", [])],
        }
    if family == "code_list":
        return {
            "properties": [copy.deepcopy(p) for p in dataset.get("properties", [])],
            "reservations": _mutate_code_list(dataset.get("reservations", []), params),
            "folios": [copy.deepcopy(r) for r in dataset.get("folios", [])],
        }
    if family == "semantic":
        return {
            "properties": [copy.deepcopy(p) for p in dataset.get("properties", [])],
            "reservations": _mutate_semantic(dataset.get("reservations", []), params),
            "folios": _mutate_semantic(dataset.get("folios", []), params),
        }
    if family == "dates":
        tz_map = property_timezone_map(dataset.get("properties", []))
        return {
            "properties": [copy.deepcopy(p) for p in dataset.get("properties", [])],
            "reservations": _mutate_dates(dataset.get("reservations", []), params, tz_map),
            "folios": _mutate_dates(dataset.get("folios", []), params, tz_map),
        }
    if family in ("pagination", "transport_auth", "fias_layout", "fias_link"):
        return {
            "properties": [copy.deepcopy(p) for p in dataset.get("properties", [])],
            "reservations": [copy.deepcopy(r) for r in dataset.get("reservations", [])],
            "folios": [copy.deepcopy(r) for r in dataset.get("folios", [])],
        }
    raise ValueError(f"unknown family: {family}")


def behavior_for_instance(instance: dict[str, Any]) -> dict[str, Any] | None:
    """Behaviour contract for an instance (None for record-shape families)."""
    _check_instance(instance)
    if str(instance["family"]) in ("pagination", "transport_auth", "fias_layout", "fias_link"):
        return _behavior_for(instance)
    return None


# ---------------------------------------------------------------------------
# Materialization: what the mutated vendor serves
# ---------------------------------------------------------------------------


def _mutate_rename(records: list[dict[str, Any]], params: dict[str, Any]) -> list[dict[str, Any]]:
    field, new_name = str(params["field"]), str(params["new_name"])
    out = []
    for rec in records:
        mutated = copy.deepcopy(rec)
        if field in mutated:
            mutated[new_name] = mutated.pop(field)
        out.append(mutated)
    return out


def _mutate_nesting(records: list[dict[str, Any]], params: dict[str, Any]) -> list[dict[str, Any]]:
    parents = [str(p) for p in params["parents"]]
    out = []
    for rec in records:
        mutated = copy.deepcopy(rec)
        wrapped: dict[str, Any] = mutated
        for parent in parents:
            wrapped = {parent: wrapped}
        out.append(wrapped)
    return out


def _mutate_code_list(records: list[dict[str, Any]], params: dict[str, Any]) -> list[dict[str, Any]]:
    # The new code is a deterministic function of the ORIGINAL status (a
    # per-status assignment), so the baseline status is never destroyed and
    # the mutation is exactly invertible. Canonical statuses with no mapped
    # new code are served unchanged (mixed vocab, still invertible).
    mapping = {str(k): str(v) for k, v in params["mapping"].items()}
    unknown_code = params.get("unknown_code")
    new_codes = {str(c) for c in params["new_codes"]}
    if new_codes & set(CANONICAL_STATUSES):
        raise GeneratorBug("new status codes must not collide with the canonical vocab")
    if unknown_code is not None and str(unknown_code) not in new_codes:
        raise GeneratorBug("unknown_code must be one of new_codes")
    by_canonical: dict[str, list[str]] = {}
    for new_code, canonical in mapping.items():
        by_canonical.setdefault(canonical, []).append(new_code)
    out = []
    for rec in records:
        mutated = copy.deepcopy(rec)
        status = mutated.get("status")
        # A deterministic slice of records carries the meaningless code so
        # the served shape really contains it (the escalation trigger).
        if (
            unknown_code is not None
            and isinstance(status, str)
            and status in CANONICAL_STATUSES
            and sum(ord(c) for c in str(rec.get("reservation_id", ""))) % 7 == 0
        ):
            mutated["status"] = str(unknown_code)
        elif isinstance(status, str) and status in by_canonical:
            candidates = sorted(by_canonical[status])
            pick = candidates[sum(ord(c) for c in status) % len(candidates)]
            mutated["status"] = pick
        out.append(mutated)
    return out


def _mutate_semantic(records: list[dict[str, Any]], params: dict[str, Any]) -> list[dict[str, Any]]:
    kind = str(params["kind"])
    out = []
    for rec in records:
        mutated = copy.deepcopy(rec)
        if kind == "decoy":
            amt_raw = None
            for key in ("total_amount", "amount"):
                if isinstance(mutated.get(key), str):
                    amt_raw = mutated[key]
                    break
            if amt_raw is not None:
                try:
                    net = (Decimal(str(amt_raw)) * Decimal("0.9")).quantize(Decimal("0.01"))
                    mutated[str(params["decoy_field"])] = format(net, ".2f")
                except Exception:
                    pass
        elif kind == "gross_net":
            # Served net keeps full Decimal precision (up to 4dp): money is
            # Decimal end to end, never float, and exactness is what makes
            # the instance provably solvable. The repaired canonical output
            # is 2dp (the baseline), so money_format still holds downstream.
            factor = Decimal(str(params["factor"]))
            for key in ("total_amount", "amount"):
                if isinstance(mutated.get(key), str):
                    try:
                        net = Decimal(str(mutated[key])) * factor
                        mutated[key] = format(net, "f")
                    except Exception:
                        pass
        elif kind == "minor_major":
            for key in ("total_amount", "amount"):
                if isinstance(mutated.get(key), str):
                    try:
                        cents = (Decimal(str(mutated[key])) * 100).to_integral_value()
                        mutated[key] = str(cents)
                    except Exception:
                        pass
        else:
            raise ValueError(f"unknown semantic kind: {kind}")
        out.append(mutated)
    return out


def _mutate_dates(
    records: list[dict[str, Any]],
    params: dict[str, Any],
    tz_map: dict[str, str],
) -> list[dict[str, Any]]:
    mode = str(params["mode"])
    out = []
    for rec in records:
        mutated = copy.deepcopy(rec)
        if mode == "cutoff":
            posted_at = mutated.get("posted_at")
            if isinstance(posted_at, str):
                tz_name = tz_map.get(str(mutated.get("property_id", "")), "UTC")
                with contextlib.suppress(Exception):
                    mutated["business_date"] = business_date_for_posted_at(
                        posted_at, tz_name, cutoff_hour=int(params["cutoff_hour"])
                    )
        elif mode == "tz_shift":
            shift = int(params["shift_hours"])
            for key in ("posted_at", "updated_at", "arrival_date", "departure_date", "business_date"):
                val = mutated.get(key)
                if not isinstance(val, str):
                    continue
                try:
                    if "T" in val:
                        dt = datetime.datetime.fromisoformat(val)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=datetime.UTC)
                        mutated[key] = (dt + datetime.timedelta(hours=shift)).isoformat()
                    else:
                        d = datetime.date.fromisoformat(val)
                        mutated[key] = (d + datetime.timedelta(days=1 if shift > 0 else -1)).isoformat()
                except Exception:
                    pass
            # business_date stays consistent with the *unshifted* cutoff rule
            # only if the vendor recomputes it; here the vendor shifts every
            # instant, so business_date (a plain date) shifts by sign days.
        elif mode == "format":
            fmt = str(params["date_format"])
            for key in ("arrival_date", "departure_date", "business_date"):
                val = mutated.get(key)
                if isinstance(val, str):
                    try:
                        d = datetime.date.fromisoformat(val)
                        mutated[key] = d.strftime(fmt)
                    except Exception:
                        pass
        else:
            raise ValueError(f"unknown dates mode: {mode}")
        out.append(mutated)
    return out


def materialize(instance: dict[str, Any]) -> dict[str, Any]:
    """Serve an instance: mutated dataset and/or behaviour descriptor.

    Returns ``{"dataset", "behavior", "announced"}`` where ``announced``
    is what the vendor publishes (served record keys, or the behaviour
    contract). Dataset families mutate records; behaviour families
    (pagination, transport_auth, fias_*) keep the baseline dataset and
    describe the wire contract the serving layer honors.
    """
    _check_instance(instance)
    family = str(instance["family"])
    params = dict(instance["params"])
    dataset = generate_dataset(seed=int(instance["dataset_seed"]))
    behavior: dict[str, Any] | None = None
    mutated = dataset

    if family == "rename":
        entity = str(params["entity"])
        mutated = {
            "properties": dataset["properties"],
            "reservations": _mutate_rename(dataset["reservations"], params)
            if entity == "reservation"
            else dataset["reservations"],
            "folios": _mutate_rename(dataset["folios"], params) if entity == "folio" else dataset["folios"],
        }
    elif family == "nesting":
        entity = str(params["entity"])
        mutated = {
            "properties": dataset["properties"],
            "reservations": _mutate_nesting(dataset["reservations"], params)
            if entity == "reservation"
            else dataset["reservations"],
            "folios": _mutate_nesting(dataset["folios"], params) if entity == "folio" else dataset["folios"],
        }
    elif family == "code_list":
        mutated = {
            "properties": dataset["properties"],
            "reservations": _mutate_code_list(dataset["reservations"], params),
            "folios": dataset["folios"],
        }
    elif family == "semantic":
        mutated = {
            "properties": dataset["properties"],
            "reservations": _mutate_semantic(dataset["reservations"], params),
            "folios": _mutate_semantic(dataset["folios"], params),
        }
    elif family == "dates":
        tz_map = property_timezone_map(dataset["properties"])
        mutated = {
            "properties": dataset["properties"],
            "reservations": _mutate_dates(dataset["reservations"], params, tz_map),
            "folios": _mutate_dates(dataset["folios"], params, tz_map),
        }
    elif family in ("pagination", "transport_auth", "fias_layout", "fias_link"):
        behavior = _behavior_for(instance)
    else:
        raise ValueError(f"unknown family: {family}")

    announced = _announced_shape(instance, mutated, behavior)
    return {"dataset": mutated, "behavior": behavior, "announced": announced}


def _behavior_for(instance: dict[str, Any]) -> dict[str, Any]:
    """Wire contract the serving layer honors for behaviour families."""
    family = str(instance["family"])
    params = dict(instance["params"])
    if family == "pagination":
        return {"family": family, **params}
    if family == "transport_auth":
        return {"family": family, **params}
    if family == "fias_layout":
        announced_fields = list(FIAS_BASE_FIELDS)
        for old, new in params["renamed"].items():
            announced_fields = [new if f == old else f for f in announced_fields]
        announced_fields.extend(params["added"])
        if params["reordered"]:
            iseed = bytes.fromhex(str(instance["seed_hex"]))
            rng = _rng_for(iseed, "fias-order")
            rng.shuffle(announced_fields)
        return {"family": family, "announced_fields": announced_fields, **params}
    if family == "fias_link":
        return {"family": family, **params}
    raise ValueError(f"no behavior for family: {family}")


def _announced_shape(
    instance: dict[str, Any],
    dataset: dict[str, list[dict[str, Any]]],
    behavior: dict[str, Any] | None,
) -> dict[str, Any]:
    """What the vendor publishes for this instance (served shape/contract)."""
    if behavior is not None:
        return {"type": "behavior", "contract": behavior}
    entity_keys: dict[str, list[str]] = {}
    for entity in ("reservations", "folios"):
        keys: set[str] = set()
        for rec in dataset.get(entity, []):
            keys.update(_leaf_keys(rec))
        entity_keys[entity] = sorted(keys)
    return {"type": "dataset", "entity": str(instance["params"].get("entity", "both")), "keys": entity_keys}


def _leaf_keys(rec: Any) -> set[str]:
    """Top-level keys, unwrapping single-key nesting levels."""
    keys: set[str] = set()
    if isinstance(rec, dict):
        for key, value in rec.items():
            keys.add(str(key))
            if isinstance(value, dict) and len(value) >= 1:
                keys.update(_leaf_keys(value))
    return keys


def _check_instance(instance: dict[str, Any]) -> None:
    if not isinstance(instance, dict):
        raise ValueError("instance must be a dict")
    for key in ("vendor", "family", "index", "params", "dataset_seed", "expected"):
        if key not in instance:
            raise ValueError(f"instance missing key: {key}")
    vendor, family = str(instance["vendor"]), str(instance["family"])
    if vendor not in VENDORS:
        raise ValueError(f"unknown vendor: {vendor}")
    if family not in ALL_FAMILIES:
        raise ValueError(f"unknown family: {family}")
    if family in FIAS_FAMILIES and vendor != "fias-pabx":
        raise ValueError(f"family {family} requires vendor fias-pabx")
    if family in HTTP_FAMILIES and vendor == "fias-pabx":
        raise ValueError(f"family {family} is HTTP-only")
    if str(instance["expected"]) not in OUTCOMES:
        raise ValueError(f"unknown outcome: {instance['expected']}")
    if str(instance["expected"]) != expected_for(family, dict(instance["params"])):
        raise ValueError("instance expected class does not match its family/params")


# ---------------------------------------------------------------------------
# Behaviour helpers: honor the announced contract (used by serving layers
# and by the consistency tests to prove announced == served)
# ---------------------------------------------------------------------------


def paginate(records: list[dict[str, Any]], behavior: dict[str, Any], page_size: int = 7) -> list[list[dict[str, Any]]]:
    """Split ``records`` into pages honoring a pagination behaviour contract.

    Cursor strategies emit opaque tokens derived from the index (no record
    content leaks into the token); every strategy round-trips the full set.
    """
    if behavior.get("family") != "pagination":
        raise ValueError("behavior is not a pagination contract")
    if page_size < 1:
        raise ValueError("page_size must be >= 1")
    strategy = str(behavior["strategy"])
    if strategy not in ("offset", "page", "cursor", "body_cursor"):
        raise ValueError(f"unknown strategy: {strategy}")
    return [records[i : i + page_size] for i in range(0, len(records), page_size)] or [[]]


def cursor_token_for(index: int, behavior: dict[str, Any]) -> str:
    """Opaque cursor token for ``index`` under a cursor/body_cursor contract."""
    if behavior.get("family") != "pagination":
        raise ValueError("behavior is not a pagination contract")
    if str(behavior["strategy"]) not in ("cursor", "body_cursor"):
        raise ValueError("strategy has no cursor")
    digest = hashlib.sha256(f"{index}|cursor".encode("ascii")).hexdigest()[:16]
    return f"c{index:06d}_{digest}"


def transport_response(
    behavior: dict[str, Any], request_count: int, refreshed: bool = False
) -> tuple[int, dict[str, str], bool]:
    """Simulate one response under a transport_auth contract.

    Returns ``(status, headers, terminal)``: ``terminal`` is True when no
    retry or refresh can help (revoked token, removed endpoint).
    """
    if behavior.get("family") != "transport_auth":
        raise ValueError("behavior is not a transport contract")
    kind = str(behavior["kind"])
    if kind == "throttle":
        every_k = int(behavior["every_k"])
        if request_count > 0 and request_count % every_k == 0:
            return (429, {"Retry-After": str(int(behavior["retry_after_s"]))}, False)
        return (200, {}, False)
    if kind == "retry_after":
        if request_count == 1:
            return (429, {"Retry-After": str(int(behavior["retry_after_s"]))}, False)
        return (200, {}, False)
    if kind == "outage":
        if request_count <= int(behavior["fail_first_m"]):
            return (503, {"Retry-After": "1"}, False)
        return (200, {}, False)
    if kind == "token_expired":
        if refreshed:
            return (200, {}, False)
        return (401, {}, False)
    if kind == "token_revoked":
        return (403, {}, True)
    if kind == "endpoint_removed":
        return (404, {}, True)
    raise ValueError(f"unknown transport kind: {kind}")


def fias_records_for(behavior: dict[str, Any], count: int = 5) -> list[dict[str, str]]:
    """Posting records honoring a fias_layout contract's announced fields."""
    if behavior.get("family") != "fias_layout":
        raise ValueError("behavior is not a fias_layout contract")
    fields = [str(f) for f in behavior["announced_fields"]]
    if set(fields) != set(_fias_expected_fields(behavior)):
        raise ValueError("announced fields do not match layout params")
    rng = _rng_for(hashlib.sha256(json.dumps(behavior, sort_keys=True).encode()).digest(), "fias-values")
    records = []
    for _ in range(count):
        records.append({f: f"{f}-{rng.randint(0, 9999):04d}" for f in fields})
    return records


def _fias_expected_fields(behavior: dict[str, Any]) -> list[str]:
    fields = list(FIAS_BASE_FIELDS)
    renamed = behavior.get("renamed", {})
    if isinstance(renamed, dict):
        for old, new in renamed.items():
            fields = [str(new) if f == old else f for f in fields]
    added = behavior.get("added", [])
    if isinstance(added, list):
        fields.extend(str(f) for f in added)
    return fields


# ---------------------------------------------------------------------------
# Solvability: constructive inverse for every repair instance
# ---------------------------------------------------------------------------


def inverse_canonicalize(instance: dict[str, Any], dataset: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """Recover the baseline canonical dataset from a materialized one.

    Defined for ``repair`` instances only; raises :class:`ValueError` for
    other outcome classes (nothing to invert: no patch is the answer).
    Raises :class:`GeneratorBug` if inversion does not reproduce the
    baseline — that is a generator bug (an impossible question), never an
    engine failure.
    """
    _check_instance(instance)
    if str(instance["expected"]) != "repair":
        raise ValueError(f"nothing to invert for outcome {instance['expected']}")
    family = str(instance["family"])
    params = dict(instance["params"])
    baseline = generate_dataset(seed=int(instance["dataset_seed"]))
    try:
        if family == "rename":
            recovered = _invert_rename(dataset, params)
        elif family == "nesting":
            recovered = _invert_nesting(dataset, params)
        elif family == "code_list":
            recovered = _invert_code_list(dataset, params)
        elif family == "semantic":
            recovered = _invert_semantic(dataset, params)
        elif family == "dates":
            recovered = _invert_dates(dataset, params, baseline)
        elif family in ("pagination", "fias_layout"):
            recovered = dataset
        else:
            # Unreachable: _check_instance allows only known families, and
            # every repair family is handled above.
            raise ValueError(f"no inverse for family: {family}")  # pragma: no cover
    except GeneratorBug:
        raise
    except Exception as exc:
        raise GeneratorBug(f"inverse failed for {family}: {exc}") from exc
    _assert_same_dataset(recovered, baseline, family)
    return recovered


def _invert_rename(dataset: dict[str, list[dict[str, Any]]], params: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    entity = str(params["entity"])
    key = "reservations" if entity == "reservation" else "folios"
    field, new_name = str(params["field"]), str(params["new_name"])
    out = copy.deepcopy(dataset)
    out[key] = [{**{k: v for k, v in rec.items() if k != new_name}, **({field: rec[new_name]} if new_name in rec else {})} for rec in out[key]]
    return out


def _invert_nesting(dataset: dict[str, list[dict[str, Any]]], params: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    entity = str(params["entity"])
    key = "reservations" if entity == "reservation" else "folios"
    parents = [str(p) for p in params["parents"]]
    out = copy.deepcopy(dataset)
    recovered = []
    for rec in out[key]:
        node: Any = rec
        # Mutation wraps outer-last (parents[0] is innermost), so unwrap
        # in reverse.
        for parent in reversed(parents):
            if not isinstance(node, dict) or parent not in node:
                raise GeneratorBug(f"nesting parent {parent!r} missing in served record")
            node = node[parent]
        if not isinstance(node, dict):
            raise GeneratorBug("nesting leaf is not a record")
        recovered.append(node)
    out[key] = recovered
    return out


def _invert_code_list(dataset: dict[str, list[dict[str, Any]]], params: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    if params.get("unknown_code"):
        raise GeneratorBug("code_list with unknown_code is escalate, not repair")
    mapping = {str(k): str(v) for k, v in params["mapping"].items()}
    new_codes = {str(c) for c in params["new_codes"]}
    out = copy.deepcopy(dataset)
    fixed = []
    for rec in out["reservations"]:
        status = rec.get("status")
        if isinstance(status, str) and status in new_codes:
            canonical = mapping.get(status)
            if canonical is None:
                raise GeneratorBug(f"new code {status!r} has no canonical meaning")
            rec = {**rec, "status": canonical}
        fixed.append(rec)
    out["reservations"] = fixed
    return out


def _invert_semantic(dataset: dict[str, list[dict[str, Any]]], params: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    kind = str(params["kind"])
    if kind == "decoy":
        raise GeneratorBug("decoy semantic is degraded_question, not repair")
    out = copy.deepcopy(dataset)
    for key in ("reservations", "folios"):
        fixed = []
        for rec in out[key]:
            rec = dict(rec)
            for mkey in ("total_amount", "amount"):
                if isinstance(rec.get(mkey), str):
                    try:
                        if kind == "gross_net":
                            factor = Decimal(str(params["factor"]))
                            rec[mkey] = format(Decimal(str(rec[mkey])) / factor, ".2f")
                        elif kind == "minor_major":
                            rec[mkey] = format(Decimal(str(rec[mkey])) / 100, ".2f")
                    except Exception as exc:
                        raise GeneratorBug(f"semantic inverse failed: {exc}") from exc
            fixed.append(rec)
        out[key] = fixed
    return out


def _invert_dates(
    dataset: dict[str, list[dict[str, Any]]],
    params: dict[str, Any],
    baseline: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    mode = str(params["mode"])
    out = copy.deepcopy(dataset)
    if mode == "cutoff":
        tz_map = property_timezone_map(baseline["properties"])
        for key in ("reservations", "folios"):
            for rec in out[key]:
                posted_at = rec.get("posted_at")
                if isinstance(posted_at, str):
                    tz_name = tz_map.get(str(rec.get("property_id", "")), "UTC")
                    rec["business_date"] = business_date_for_posted_at(posted_at, tz_name)
        return out
    if mode == "tz_shift":
        shift = int(params["shift_hours"])
        for key in ("reservations", "folios"):
            for rec in out[key]:
                for dkey in ("posted_at", "updated_at", "arrival_date", "departure_date", "business_date"):
                    val = rec.get(dkey)
                    if not isinstance(val, str):
                        continue
                    try:
                        if "T" in val:
                            dt = datetime.datetime.fromisoformat(val)
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=datetime.UTC)
                            rec[dkey] = (dt - datetime.timedelta(hours=shift)).isoformat()
                        else:
                            d = datetime.date.fromisoformat(val)
                            rec[dkey] = (d - datetime.timedelta(days=1 if shift > 0 else -1)).isoformat()
                    except Exception as exc:
                        raise GeneratorBug(f"tz_shift inverse failed: {exc}") from exc
        return out
    if mode == "format":
        fmt = str(params["date_format"])
        for key in ("reservations", "folios"):
            for rec in out[key]:
                for dkey in ("arrival_date", "departure_date", "business_date"):
                    val = rec.get(dkey)
                    if isinstance(val, str) and not _is_iso_date(val):
                        try:
                            rec[dkey] = datetime.datetime.strptime(val, fmt).date().isoformat()
                        except Exception as exc:
                            raise GeneratorBug(f"format inverse failed: {exc}") from exc
        return out
    raise ValueError(f"unknown dates mode: {mode}")


def _is_iso_date(val: str) -> bool:
    try:
        datetime.date.fromisoformat(val)
        return True
    except ValueError:
        return False


def _assert_same_dataset(
    recovered: dict[str, list[dict[str, Any]]],
    baseline: dict[str, list[dict[str, Any]]],
    family: str,
) -> None:
    """Byte-level equality (money as exact strings, dates as strings)."""
    for key in ("reservations", "folios", "properties"):
        rec_recovered = recovered.get(key, [])
        rec_baseline = baseline.get(key, [])
        if len(rec_recovered) != len(rec_baseline):
            raise GeneratorBug(f"{family}: record count mismatch for {key}")
        id_key = {"reservations": "reservation_id", "folios": "charge_id", "properties": "property_id"}[key]
        base_by_id = {str(r.get(id_key)): r for r in rec_baseline}
        for rec in rec_recovered:
            rid = str(rec.get(id_key))
            expected_rec = base_by_id.get(rid)
            if expected_rec is None:
                raise GeneratorBug(f"{family}: unknown record {rid}")
            if dict(rec) != dict(expected_rec):
                raise GeneratorBug(f"{family}: record {rid} does not round-trip")


class GeneratorBug(Exception):
    """A generated instance the reference solver cannot handle (item 2).

    Raising this means the *generator* is at fault — an impossible
    question — never the engine under evaluation.
    """
