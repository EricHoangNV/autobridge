"""REQ-025 items 1-2: beacon fault generator tests (simulators package).

Property tests over hundreds of seeds: determinism, vendor coverage,
pool bounds, announced == served consistency, and byte-identical
inverse round-trips for every repair instance (the constructive
solvability proof — a failure here is a generator bug, never an
engine failure).
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal

import pytest

from simulators.common import faultgen
from simulators.common.faultgen import (
    ALL_FAMILIES,
    BEACON_LABEL,
    CANONICAL_STATUSES,
    FIAS_FAMILIES,
    HTTP_FAMILIES,
    VENDORS,
    cursor_token_for,
    dataset_seed_for,
    derive_eval_seed,
    derive_instance_seed,
    expected_for,
    fias_records_for,
    generate_instances,
    inverse_canonicalize,
    materialize,
    paginate,
    transport_response,
)
from simulators.common.generator import business_date_for_posted_at, property_timezone_map


def _eval(raw: bytes) -> bytes:
    assert len(raw) == 32
    return hashlib.sha256(raw + b"|" + BEACON_LABEL.encode("ascii")).digest()


def _seeds(k: int) -> list[bytes]:
    return [hashlib.sha256(f"beacon-test-{i}".encode()).digest() for i in range(k)]


# ---------------------------------------------------------------------------
# Seed derivation
# ---------------------------------------------------------------------------


def test_derive_eval_seed_vector() -> None:
    assert derive_eval_seed("ab" * 32) == _eval(bytes.fromhex("ab" * 32))
    assert derive_eval_seed("00" * 32) == _eval(bytes(32))
    # pinned regression vectors (recomputed from the documented formula)
    assert derive_eval_seed("ab" * 32).hex() == _eval(bytes.fromhex("ab" * 32)).hex()
    assert len(derive_eval_seed("ff" * 32)) == 32


def test_derive_eval_seed_rejects_garbage() -> None:
    for bad in ("", "zz" * 32, "ab" * 31, "ab" * 33, "ab", 123):
        with pytest.raises(ValueError):
            derive_eval_seed(bad)  # type: ignore[arg-type]


def test_derive_instance_seed_deterministic_and_distinct() -> None:
    seed = derive_eval_seed("ab" * 32)
    assert derive_instance_seed(seed, 0) == derive_instance_seed(seed, 0)
    assert derive_instance_seed(seed, 0) != derive_instance_seed(seed, 1)
    assert derive_instance_seed(seed, 7) == hashlib.sha256(seed + (7).to_bytes(8, "big")).digest()


def test_derive_instance_seed_rejects_garbage() -> None:
    seed = derive_eval_seed("ab" * 32)
    for bad_seed in (b"", b"short", "ab" * 32, None):
        with pytest.raises(ValueError):
            derive_instance_seed(bad_seed, 0)  # type: ignore[arg-type]
    for bad_index in (-1, True, "0", None):
        with pytest.raises(ValueError):
            derive_instance_seed(seed, bad_index)  # type: ignore[arg-type]


def test_dataset_seed_in_range() -> None:
    for raw in _seeds(20):
        assert 0 <= dataset_seed_for(raw) <= 1_000_000_000


# ---------------------------------------------------------------------------
# Instance generation: determinism, coverage, validity
# ---------------------------------------------------------------------------


def test_generate_instances_rejects_bad_input() -> None:
    seed = derive_eval_seed("ab" * 32)
    with pytest.raises(ValueError):
        generate_instances(b"short", 5)
    with pytest.raises(ValueError):
        generate_instances(seed, 0)
    with pytest.raises(ValueError):
        generate_instances(seed, -3)
    with pytest.raises(ValueError):
        generate_instances(seed, 10_001)


def test_generate_instances_deterministic() -> None:
    seed = derive_eval_seed("ab" * 32)
    assert generate_instances(seed, 8) == generate_instances(seed, 8)
    assert generate_instances(seed, 8) != generate_instances(derive_eval_seed("cd" * 32), 8)


def test_generate_instances_vendor_round_robin() -> None:
    seed = derive_eval_seed("ab" * 32)
    insts = generate_instances(seed, 10)
    assert [i["vendor"] for i in insts[:5]] == list(VENDORS)
    assert {i["vendor"] for i in insts} == set(VENDORS)


def test_generate_instances_json_round_trip() -> None:
    seed = derive_eval_seed("ab" * 32)
    insts = generate_instances(seed, 5)
    assert json.loads(json.dumps(insts)) == insts


def test_generate_instances_expected_matches_rule() -> None:
    for raw in _seeds(40):
        for inst in generate_instances(raw, 5):
            assert inst["expected"] == expected_for(str(inst["family"]), dict(inst["params"]))
            assert inst["expected"] in ("repair", "no_patch", "escalate", "degraded_question")


def test_generate_instances_family_vendor_constraints() -> None:
    for raw in _seeds(40):
        for inst in generate_instances(raw, 5):
            family, vendor = str(inst["family"]), str(inst["vendor"])
            assert family in ALL_FAMILIES
            if vendor == "fias-pabx":
                assert family in FIAS_FAMILIES
            else:
                assert family in HTTP_FAMILIES


def test_all_families_and_outcomes_covered() -> None:
    seen_families: set[str] = set()
    seen_outcomes: set[str] = set()
    for raw in _seeds(60):
        for inst in generate_instances(raw, 5):
            seen_families.add(str(inst["family"]))
            seen_outcomes.add(str(inst["expected"]))
    assert set(HTTP_FAMILIES) <= seen_families
    assert set(FIAS_FAMILIES) <= seen_families
    assert seen_outcomes == {"repair", "no_patch", "escalate", "degraded_question"}


def test_params_within_pools() -> None:
    for raw in _seeds(60):
        for inst in generate_instances(raw, 5):
            faultgen._check_instance(inst)
            params = dict(inst["params"])
            family = str(inst["family"])
            if family == "rename":
                assert params["new_name"] in faultgen.NAME_POOL
                assert params["new_name"] not in faultgen._record_keys(str(params["entity"]))
            elif family == "nesting":
                assert params["depth"] in (1, 2)
                assert len(params["parents"]) == params["depth"]
            elif family == "code_list":
                assert 2 <= len(params["new_codes"]) <= 4
                assert not (set(params["new_codes"]) & set(CANONICAL_STATUSES))
            elif family == "semantic" and params["kind"] != "decoy":
                assert Decimal(str(params["factor"])) in faultgen.SEMANTIC_FACTORS
            elif family == "transport_auth" and params["kind"] in ("throttle", "retry_after"):
                assert int(params["retry_after_s"]) in faultgen.RETRY_AFTER_POOL


# ---------------------------------------------------------------------------
# Materialization: announced == served (item 2 consistency)
# ---------------------------------------------------------------------------


def test_materialize_rename_serves_announced_keys() -> None:
    for raw in _seeds(30):
        for inst in generate_instances(raw, 5):
            if str(inst["family"]) != "rename":
                continue
            mat = materialize(inst)
            params = dict(inst["params"])
            key = "reservations" if params["entity"] == "reservation" else "folios"
            announced = set(mat["announced"]["keys"][key])
            assert params["new_name"] in announced
            assert params["field"] not in announced
            for rec in mat["dataset"][key]:
                assert params["new_name"] in rec
                assert params["field"] not in rec


def test_materialize_nesting_chain_present() -> None:
    for raw in _seeds(30):
        for inst in generate_instances(raw, 5):
            if str(inst["family"]) != "nesting":
                continue
            mat = materialize(inst)
            params = dict(inst["params"])
            key = "reservations" if params["entity"] == "reservation" else "folios"
            for rec in mat["dataset"][key]:
                node = rec
                for parent in reversed([str(p) for p in params["parents"]]):
                    assert parent in node
                    node = node[parent]
                assert isinstance(node, dict) and "property_id" in node


def test_materialize_code_list_vocab() -> None:
    for raw in _seeds(30):
        for inst in generate_instances(raw, 5):
            if str(inst["family"]) != "code_list":
                continue
            mat = materialize(inst)
            params = dict(inst["params"])
            new_codes = {str(c) for c in params["new_codes"]}
            mapping = {str(k): str(v) for k, v in params["mapping"].items()}
            served = {str(r.get("status")) for r in mat["dataset"]["reservations"]}
            unknown = params.get("unknown_code")
            if unknown is None:
                assert served <= (new_codes | (set(CANONICAL_STATUSES) - set(mapping.values()) | set(mapping.values())))
                for code in served & new_codes:
                    assert code in mapping
            else:
                assert str(unknown) in served
                assert str(unknown) not in mapping


def test_materialize_decoy_keeps_gross() -> None:
    seen = 0
    for raw in _seeds(60):
        for inst in generate_instances(raw, 5):
            if str(inst["family"]) != "semantic" or dict(inst["params"]).get("kind") != "decoy":
                continue
            seen += 1
            mat = materialize(inst)
            decoy = str(dict(inst["params"])["decoy_field"])
            assert mat["announced"]["keys"]["reservations"] and decoy in mat["announced"]["keys"]["reservations"]
            for rec in mat["dataset"]["reservations"]:
                assert decoy in rec
                Decimal(str(rec["total_amount"]))
                Decimal(str(rec[decoy]))
    assert seen > 0


def test_materialize_dates_cutoff_matches_announcement() -> None:
    seen = 0
    for raw in _seeds(40):
        for inst in generate_instances(raw, 5):
            params = dict(inst["params"])
            if str(inst["family"]) != "dates" or params.get("mode") != "cutoff":
                continue
            seen += 1
            mat = materialize(inst)
            tz_map = property_timezone_map(mat["dataset"]["properties"])
            for rec in mat["dataset"]["folios"]:
                posted_at = rec.get("posted_at")
                if isinstance(posted_at, str):
                    tz = tz_map.get(str(rec.get("property_id", "")), "UTC")
                    assert rec["business_date"] == business_date_for_posted_at(
                        posted_at, tz, cutoff_hour=int(params["cutoff_hour"])
                    )
    assert seen > 0


def test_dates_cutoff_always_manifests() -> None:
    """REQ-063 item 3: every cutoff fault moves at least one folio's
    business date (cutoff 5/6 fall outside the generator's late-posting
    window, so without the forcing rule they are silent no-ops)."""
    from simulators.common.generator import generate_dataset

    seen: dict[int, int] = {}
    for raw in _seeds(60):
        for inst in generate_instances(raw, 5):
            params = dict(inst["params"])
            if str(inst["family"]) != "dates" or params.get("mode") != "cutoff":
                continue
            cutoff = int(params["cutoff_hour"])
            seen[cutoff] = seen.get(cutoff, 0) + 1
            mat = materialize(inst)
            base = generate_dataset(seed=int(inst["dataset_seed"]))
            changed = [
                (b.get("business_date"), a.get("business_date"))
                for a, b in zip(mat["dataset"]["folios"], base["folios"], strict=True)
                if a.get("business_date") != b.get("business_date")
            ]
            assert changed, (inst["vendor"], params)
    assert set(seen) == {1, 2, 3}, seen


def test_dates_format_never_picks_baseline() -> None:
    """REQ-063 item 3: the baseline vendor format (%Y-%m-%d) served back is
    a silent no-op while the key expects a repair, so it is never chosen."""
    seen = 0
    for raw in _seeds(60):
        for inst in generate_instances(raw, 5):
            params = dict(inst["params"])
            if str(inst["family"]) != "dates" or params.get("mode") != "format":
                continue
            seen += 1
            assert str(params["date_format"]) != "%Y-%m-%d", params
    assert seen > 0


def test_rename_never_picks_unserved_meridian_fields() -> None:
    """REQ-063 item 3: Meridian never serves reservation total_amount or
    folio business_date on the wire, so renaming them is a silent no-op."""
    seen_meridian = 0
    for raw in _seeds(120):
        for inst in generate_instances(raw, 5):
            params = dict(inst["params"])
            if str(inst["family"]) != "rename":
                continue
            if str(inst["vendor"]) == "meridian":
                seen_meridian += 1
                assert (params.get("entity"), params.get("field")) not in (
                    ("reservation", "total_amount"),
                    ("folio", "business_date"),
                ), params
    assert seen_meridian > 0


def test_fias_layout_always_renames_a_mapped_field() -> None:
    """REQ-063 item 3: an added-only (or empty) layout announcement breaks
    no mapping, so every fias_layout instance renames a mapped wire id."""
    seen = 0
    for raw in _seeds(60):
        for inst in generate_instances(raw, 5):
            if str(inst["family"]) != "fias_layout":
                continue
            seen += 1
            renamed = dict(inst["params"].get("renamed", {}))
            assert renamed, inst["params"]
    assert seen > 0


def test_materialize_dates_format_parseable() -> None:
    import datetime as _dt

    for raw in _seeds(40):
        for inst in generate_instances(raw, 5):
            params = dict(inst["params"])
            if str(inst["family"]) != "dates" or params.get("mode") != "format":
                continue
            mat = materialize(inst)
            for rec in mat["dataset"]["reservations"] + mat["dataset"]["folios"]:
                for key in ("arrival_date", "departure_date", "business_date"):
                    val = rec.get(key)
                    if isinstance(val, str):
                        _dt.datetime.strptime(val, str(params["date_format"]))


def test_materialize_semantic_money_stays_decimal() -> None:
    for raw in _seeds(30):
        for inst in generate_instances(raw, 5):
            if str(inst["family"]) != "semantic":
                continue
            mat = materialize(inst)
            for rec in mat["dataset"]["reservations"] + mat["dataset"]["folios"]:
                for key in ("total_amount", "amount"):
                    val = rec.get(key)
                    if isinstance(val, str):
                        Decimal(val)
                        assert isinstance(val, str) and "e" not in val.lower()


# ---------------------------------------------------------------------------
# Behaviour contracts: announced == honored
# ---------------------------------------------------------------------------


def test_paginate_round_trips_every_strategy() -> None:
    records = [{"id": i} for i in range(23)]
    seen: set[str] = set()
    for raw in _seeds(40):
        for inst in generate_instances(raw, 5):
            if str(inst["family"]) != "pagination":
                continue
            seen.add(str(dict(inst["params"])["strategy"]))
            mat = materialize(inst)
            behavior = mat["behavior"]
            assert behavior is not None
            pages = paginate(records, behavior, page_size=7)
            assert [r for page in pages for r in page] == records
            assert mat["announced"]["contract"] == behavior
    assert seen == {"offset", "page", "cursor", "body_cursor"}


def test_cursor_tokens_opaque_and_unique() -> None:
    for raw in _seeds(10):
        for inst in generate_instances(raw, 5):
            params = dict(inst["params"])
            if str(inst["family"]) != "pagination" or str(params.get("strategy")) not in ("cursor", "body_cursor"):
                continue
            mat = materialize(inst)
            assert mat["behavior"] is not None
            tokens = [cursor_token_for(i, mat["behavior"]) for i in range(50)]
            assert len(set(tokens)) == 50
            for tok in tokens:
                assert "RES-" not in tok and "CHG-" not in tok


def test_paginate_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        paginate([], {"family": "rename"}, 7)
    with pytest.raises(ValueError):
        paginate([], {"family": "pagination", "strategy": "telepathy"}, 7)
    with pytest.raises(ValueError):
        paginate([], {"family": "pagination", "strategy": "offset"}, 0)


def test_transport_response_schedules() -> None:
    for raw in _seeds(40):
        for inst in generate_instances(raw, 5):
            if str(inst["family"]) != "transport_auth":
                continue
            behavior = materialize(inst)["behavior"]
            assert behavior is not None
            kind = str(behavior["kind"])
            if kind == "throttle":
                k = int(behavior["every_k"])
                for count in range(1, 3 * k + 1):
                    status, headers, terminal = transport_response(behavior, count)
                    assert not terminal
                    if count % k == 0:
                        assert status == 429
                        assert headers["Retry-After"] == str(int(behavior["retry_after_s"]))
                    else:
                        assert status == 200
            elif kind == "retry_after":
                assert transport_response(behavior, 1)[0] == 429
                assert transport_response(behavior, 1)[1]["Retry-After"] == str(int(behavior["retry_after_s"]))
                assert transport_response(behavior, 2)[0] == 200
            elif kind == "outage":
                m = int(behavior["fail_first_m"])
                for count in range(1, m + 3):
                    status, _h, terminal = transport_response(behavior, count)
                    assert not terminal
                    assert status == (503 if count <= m else 200)
            elif kind == "token_expired":
                assert transport_response(behavior, 1)[0] == 401
                assert transport_response(behavior, 1, refreshed=True)[0] == 200
            elif kind == "token_revoked":
                assert transport_response(behavior, 3) == (403, {}, True)
            elif kind == "endpoint_removed":
                assert transport_response(behavior, 3) == (404, {}, True)


def test_fias_layout_records_match_announcement() -> None:
    seen_reordered = False
    for raw in _seeds(40):
        for inst in generate_instances(raw, 5):
            if str(inst["family"]) != "fias_layout":
                continue
            behavior = materialize(inst)["behavior"]
            assert behavior is not None
            announced = [str(f) for f in behavior["announced_fields"]]
            expected = list(faultgen.FIAS_BASE_FIELDS)
            for old, new in dict(behavior["renamed"]).items():
                expected = [new if f == old else f for f in expected]
            expected.extend([str(f) for f in behavior["added"]])
            assert sorted(announced) == sorted(expected)
            if behavior["reordered"]:
                seen_reordered = True
            for rec in fias_records_for(behavior, count=4):
                assert sorted(rec.keys()) == sorted(announced)
    assert seen_reordered


def test_fias_records_reject_tampered_announcement() -> None:
    behavior = {
        "family": "fias_layout",
        "added": ["GX"],
        "renamed": {},
        "reordered": False,
        "announced_fields": ["RN", "WRONG"],
    }
    with pytest.raises(ValueError):
        fias_records_for(behavior)


# ---------------------------------------------------------------------------
# Solvability: byte-identical inverse for every repair instance
# ---------------------------------------------------------------------------


def test_inverse_round_trips_all_repair_instances() -> None:
    checked = 0
    for raw in _seeds(200):
        for inst in generate_instances(raw, 5):
            mat = materialize(inst)
            if str(inst["expected"]) == "repair":
                inverse_canonicalize(inst, mat["dataset"])
                checked += 1
    assert checked > 500


def test_inverse_rejects_non_repair() -> None:
    seen: set[str] = set()
    for raw in _seeds(60):
        for inst in generate_instances(raw, 5):
            if str(inst["expected"]) != "repair":
                seen.add(str(inst["expected"]))
                with pytest.raises(ValueError):
                    inverse_canonicalize(inst, materialize(inst)["dataset"])
    assert seen == {"no_patch", "escalate", "degraded_question"}


def test_materialize_rejects_unknown_family() -> None:
    seed = derive_eval_seed("ab" * 32)
    inst = generate_instances(seed, 5)[0]
    bad = dict(inst)
    bad["family"] = "telepathy"
    with pytest.raises(ValueError):
        materialize(bad)
    bad2 = dict(inst)
    bad2["vendor"] = "atlantis"
    with pytest.raises(ValueError):
        materialize(bad2)


def test_check_instance_rejects_malformed() -> None:
    seed = derive_eval_seed("ab" * 32)
    inst = generate_instances(seed, 5)[0]
    with pytest.raises(ValueError):
        faultgen._check_instance(["not", "a", "dict"])
    missing = {k: v for k, v in inst.items() if k != "params"}
    with pytest.raises(ValueError):
        faultgen._check_instance(missing)
    fias_http = dict(inst, vendor="atlas", family="fias_layout")
    with pytest.raises(ValueError):
        faultgen._check_instance(fias_http)
    http_fias = dict(inst, vendor="fias-pabx", family="rename")
    with pytest.raises(ValueError):
        faultgen._check_instance(http_fias)
    bad_outcome = dict(inst, expected="splendid")
    with pytest.raises(ValueError):
        faultgen._check_instance(bad_outcome)
    mismatch = dict(inst, expected="escalate")
    with pytest.raises(ValueError):
        faultgen._check_instance(mismatch)
    with pytest.raises(ValueError):
        faultgen._params_for("telepathy", "atlas", __import__("random").Random(0))
    with pytest.raises(ValueError):
        faultgen._behavior_for(dict(inst, family="rename"))
    with pytest.raises(ValueError):
        faultgen._families_for("atlantis")
    with pytest.raises(ValueError):
        faultgen.expected_for("telepathy", {})
    with pytest.raises(ValueError):
        faultgen._record_keys("ledger")


def test_behavior_helpers_reject_wrong_contracts() -> None:
    with pytest.raises(ValueError):
        cursor_token_for(3, {"family": "pagination", "strategy": "offset"})
    with pytest.raises(ValueError):
        cursor_token_for(3, {"family": "rename"})
    with pytest.raises(ValueError):
        transport_response({"family": "rename"}, 1)
    with pytest.raises(ValueError):
        transport_response({"family": "transport_auth", "kind": "telepathy"}, 1)
    with pytest.raises(ValueError):
        fias_records_for({"family": "rename"})


def test_invert_dates_handles_naive_and_garbage() -> None:
    naive = {"reservation_id": "r1", "property_id": "prop-001", "posted_at": "2026-10-05T00:00:00",
             "business_date": "2026-10-05"}
    out = faultgen._invert_dates(
        {"properties": [], "reservations": [naive], "folios": []},
        {"mode": "tz_shift", "shift_hours": 2},
        {"properties": []},
    )
    assert out["reservations"][0]["posted_at"] == "2026-10-04T22:00:00+00:00"
    garbage = {"reservation_id": "r2", "property_id": "prop-001", "posted_at": "not-a-date",
               "business_date": "2026-10-05"}
    with pytest.raises(faultgen.GeneratorBug):
        faultgen._invert_dates(
            {"properties": [], "reservations": [garbage], "folios": []},
            {"mode": "tz_shift", "shift_hours": 2},
            {"properties": []},
        )
    with pytest.raises(ValueError):
        faultgen._invert_dates({"properties": [], "reservations": [], "folios": []},
                               {"mode": "telepathy"}, {"properties": []})


def _ds(reservations: list[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
    return {"reservations": reservations, "folios": [], "properties": []}


def test_inverse_defensive_branches() -> None:
    with pytest.raises(faultgen.GeneratorBug):
        faultgen._invert_code_list(_ds([{"status": "active"}]),
                                   {"mapping": {"active": "confirmed"}, "new_codes": ["active"],
                                    "unknown_code": "active"})
    with pytest.raises(faultgen.GeneratorBug):
        faultgen._invert_code_list(_ds([{"status": "ZX"}]),
                                   {"mapping": {"active": "confirmed"}, "new_codes": ["active", "ZX"]})
    with pytest.raises(faultgen.GeneratorBug):
        faultgen._invert_semantic(_ds([{"total_amount": "1.00"}]), {"kind": "decoy"})
    with pytest.raises(faultgen.GeneratorBug):
        faultgen._invert_semantic(_ds([{"total_amount": "xx"}]), {"kind": "gross_net", "factor": "0.5"})
    base = {"reservations": [{"reservation_id": "r1"}], "folios": [], "properties": []}
    with pytest.raises(faultgen.GeneratorBug):
        faultgen._assert_same_dataset({"reservations": [], "folios": [], "properties": []}, base, "rename")
    with pytest.raises(faultgen.GeneratorBug):
        faultgen._assert_same_dataset(
            {"reservations": [{"reservation_id": "rX"}], "folios": [], "properties": []}, base, "rename")
    with pytest.raises(faultgen.GeneratorBug):
        faultgen._assert_same_dataset(
            {"reservations": [{"reservation_id": "r1", "extra": 1}], "folios": [], "properties": []},
            base, "rename")
    with pytest.raises(ValueError):
        faultgen._mutate_semantic([{}], {"kind": "telepathy"})
    with pytest.raises(ValueError):
        faultgen._mutate_dates([{}], {"mode": "telepathy"}, {})
    with pytest.raises(faultgen.GeneratorBug):
        faultgen._invert_nesting({"reservations": [{"a": 1}], "folios": []},
                                 {"entity": "reservation", "parents": ["nope"]})
    with pytest.raises(faultgen.GeneratorBug):
        faultgen._invert_nesting({"reservations": [{"p": [1]}], "folios": []},
                                 {"entity": "reservation", "parents": ["p"]})
    with pytest.raises(faultgen.GeneratorBug):
        faultgen.inverse_canonicalize(
            {"vendor": "atlas", "family": "rename", "index": 0, "params": {},
             "seed_hex": "ab" * 32, "dataset_seed": 1, "expected": "repair"},
            {"reservations": [], "folios": [], "properties": [{"property_id": "x"}]},
        )
