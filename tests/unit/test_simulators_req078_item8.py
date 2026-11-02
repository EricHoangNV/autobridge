"""REQ-078 item 8: beacon simulator defects fixed.

Meridian serializers crashed on non-ISO / renamed / nested dates
(``fromisoformat``); Atlas folio renames never served the new key; the
meridian ``dates/cutoff`` fault never manifests but was keyed ``repair``.
These tests pin the fixes: every beacon record-shape mutation is served
without a crash, renamed keys ride the wire, and the meridian cutoff
keys ``no_patch`` (the connector output is correct).
"""

from __future__ import annotations

import json
from typing import Any

from simulators.common import faultgen as fg

_RECORD_SHAPE_FAMILIES = ("rename", "nesting", "code_list", "semantic", "dates")


def _instances(vendor: str, seeds: tuple[str, ...] = ("ab", "cd")) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for hexs in seeds:
        seed = bytes.fromhex(hexs * 32)
        for idx in range(80):
            try:
                inst = fg.generate_instance(seed, idx)
            except ValueError:
                continue
            if inst["vendor"] == vendor and inst["family"] in _RECORD_SHAPE_FAMILIES:
                out.append(inst)
    return out


def _mutated(inst: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    from simulators.common.generator import generate_dataset

    return fg.apply_instance_records(generate_dataset(seed=int(inst["dataset_seed"])), inst)


def test_meridian_serializers_never_crash_on_beacon_mutations() -> None:
    """Every record-shape mutation serves: no fromisoformat 500."""
    from simulators.meridianpms import app as m

    insts = _instances("meridian")
    assert insts, "expected meridian record-shape instances"
    for inst in insts:
        mut = _mutated(inst)
        wires = [m.to_wire_reservation(r) for r in mut["reservations"]]
        orders = [m.to_wire_order_item(f) for f in mut["folios"]]
        customers = [
            m.to_wire_customer(
                m.account_id_for(str(r.get("reservation_id"))),
                r.get("guest_name") if isinstance(r.get("guest_name"), str) else None,
                "2026-10-26T10:00:00Z",
            )
            for r in mut["reservations"]
        ]
        # JSON-serializable through the app's exact encoder (Decimal-safe).
        json.dumps({"w": wires, "o": orders, "c": customers}, default=str)


def test_meridian_rename_serves_new_key() -> None:
    """A renamed field rides the wire so the ladder can pair by value."""
    from simulators.meridianpms import app as m

    seen = 0
    for inst in _instances("meridian"):
        if inst["family"] != "rename":
            continue
        params = dict(inst["params"])
        entity, field, new_name = str(params["entity"]), str(params["field"]), str(params["new_name"])
        mut = _mutated(inst)
        rows = mut["reservations"] if entity == "reservation" else mut["folios"]
        serve = m.to_wire_reservation if entity == "reservation" else m.to_wire_order_item
        for rec, wire in zip(rows, [serve(r) for r in rows], strict=True):
            assert new_name in rec or field in rec
            if new_name in rec:
                assert new_name in wire, (entity, field, new_name)
                seen += 1
                break
    assert seen > 0, "expected at least one served meridian rename"


def test_meridian_nesting_serves_parent_wrapper() -> None:
    """A nested record serves its parent wrapper (a path move, not a 500)."""
    from simulators.meridianpms import app as m

    seen = 0
    for inst in _instances("meridian"):
        if inst["family"] != "nesting":
            continue
        parents = [str(p) for p in inst["params"]["parents"]]
        mut = _mutated(inst)
        rows = mut["reservations"] if str(inst["params"]["entity"]) == "reservation" else mut["folios"]
        serve = m.to_wire_reservation if str(inst["params"]["entity"]) == "reservation" else m.to_wire_order_item
        wire = serve(rows[0])
        assert parents[-1] in wire, (parents, sorted(wire))
        seen += 1
    assert seen > 0, "expected at least one meridian nesting instance"


def test_meridian_format_serves_raw_non_iso_date() -> None:
    """A non-ISO date is observable on the wire (raw value surfaced).

    REQ-083 D18: dates/format is never drawn for meridian (a UTC-instant
    vendor serves no verbatim date field), so the draw yields no such
    instance — but the serializer still surfaces a raw non-ISO date
    instead of crashing, verified directly.
    """
    from simulators.meridianpms import app as m

    for inst in _instances("meridian"):
        assert not (
            inst["family"] == "dates" and inst["params"].get("mode") == "format"
        ), inst["params"]
    rec = {
        "reservation_id": "R1", "property_id": "p1", "arrival_date": "15/10/2026",
        "status": "confirmed",
    }
    wire = m.to_wire_reservation(rec)
    assert wire.get("ScheduledStartUtc") is None
    assert wire.get("arrival_date") == "15/10/2026"


def test_atlas_transaction_serves_renamed_folio_key() -> None:
    """Atlas folio renames serve the new camelCase key (old keeps default)."""
    from simulators.atlaspms import app as a

    seen = 0
    for inst in _instances("atlas"):
        if inst["family"] != "rename" or str(inst["params"].get("entity")) != "folio":
            continue
        field, new_name = str(inst["params"]["field"]), str(inst["params"]["new_name"])
        mut = _mutated(inst)
        wire = a.to_wire_transaction(mut["folios"][0])
        head, *tail = new_name.split("_")
        camel = head + "".join(p[:1].upper() + p[1:] for p in tail)
        assert camel in wire, (field, new_name, sorted(wire))
        seen += 1
    assert seen > 0, "expected at least one atlas folio rename"


def test_meridian_cutoff_keyed_no_patch() -> None:
    """Meridian cutoff changes only unserved business dates: no_patch."""
    assert fg.expected_for("dates", {"mode": "cutoff"}, "meridian") == "no_patch"
    assert fg.expected_for("dates", {"mode": "cutoff"}, "atlas") == "repair"
    assert fg.expected_for("dates", {"mode": "cutoff"}) == "repair"
    assert fg.expected_for("dates", {"mode": "cutoff"}, "harbor") == "repair"
    for inst in _instances("meridian"):
        if inst["family"] == "dates" and inst["params"].get("mode") == "cutoff":
            assert inst["expected"] == "no_patch", inst["params"]
            fg._check_instance(inst)
            from verifier import beacon as _beacon

            assert _beacon.reference_solve(inst) == "no_patch"
            assert _beacon.accepted_outcomes_for(inst) == {"no_patch"}
            ok, _ = _beacon.grade_arm_outcome(
                inst, {"outcome": "Complete", "patch_reached_release": False, "operator_question": False}
            )
            assert ok


def test_beacon_sync_pair_serves_and_detects_item8_families() -> None:
    """Item-4 sweep extension: rename/nesting/dates instances are served
    without a crash (no raise) and, when their canonical records differ,
    never end Complete with 0 signals."""
    from autobridge.drift.signals import signals_from_sync
    from bench import faults as _faults
    from bench.beacon_run import engine_spec_for

    seed_hex = "ab" * 32
    seed = bytes.fromhex(seed_hex)
    checked = 0
    for idx in range(80):
        inst = fg.generate_instance(seed, idx)
        if inst["vendor"] not in ("meridian", "atlas"):
            continue
        if inst["family"] not in ("rename", "nesting", "dates"):
            continue
        if checked >= 12:
            break
        spec = engine_spec_for({"vendor": str(inst["vendor"]), "index": idx}, None)
        if spec is None:  # pragma: no cover - every beacon vendor has a spec
            continue
        # Served without a crash: must not raise.
        faulted, baseline = _faults.beacon_sync_pair(
            vendor=str(inst["vendor"]), eval_seed_hex=seed_hex, index=idx, spec=spec
        )
        checked += 1
        if faulted.get("canonical_records") == baseline.get("canonical_records"):
            continue
        sigs = signals_from_sync(faulted, spec=spec, baseline_summary=baseline)
        assert not (faulted.get("outcome") == "Complete" and not sigs), (
            f"silent wrong records: {inst['vendor']}/{inst['family']}/{idx}"
        )
    assert checked > 0, "sweep observed no item-8 instances"
