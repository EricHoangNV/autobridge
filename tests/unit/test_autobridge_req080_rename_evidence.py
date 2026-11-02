"""REQ-080: a renamed field reaches the model as evidence; a misleading new
name expects a question.

1. Simulators keep no old key after a rename: every renameable field in
   every vendor serializer vanishes from the wire when renamed away; only
   the new key is served.
2. The detector reports new wire keys (with samples) whenever a mapped
   field goes empty/null across the sync, not only when its key disappears.
3. The L3 evidence carries the new wire keys and a redacted raw
   vendor-record sample; the L3 prompt lists both.
4. The answer key expects an escalation for a misleading rename and a
   repair only where the new name plausibly names the same field; the
   reference solver and the engine gate agree with the key.
5. The REQ-078 item-4 detection sweep extended with these cases: no
   rename instance whose records differ from the baseline ends Complete
   with 0 signals, and every served rename carries its new key.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from autobridge.llm.provider import Completion, FakeProvider, Usage


def _norm(name: Any) -> str:
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


# ---------------------------------------------------------------------------
# Item 4: answer key
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field,new_name,expected",
    [
        ("departure_date", "checkinDate", "escalate"),
        ("departure_date", "reservationStatus", "escalate"),
        ("departure_date", "lastModified", "escalate"),
        ("departure_date", "checkoutDate", "repair"),
        ("departure_date", "departureDay", "repair"),
        ("arrival_date", "arrivalDay", "repair"),
        ("arrival_date", "checkinDate", "repair"),
        ("guest_name", "primaryOccupant", "repair"),
        ("guest_name", "guestFullName", "repair"),
        ("guest_name", "postedInstant", "escalate"),
        ("status", "reservationStatus", "repair"),
        ("status", "recordUpdatedAt", "escalate"),
        ("total_amount", "grandTotal", "repair"),
        ("total_amount", "netTotal", "escalate"),
        ("amount", "valueTimestamp", "escalate"),
        ("business_date", "chargeMemo", "escalate"),
        ("posted_at", "memoText", "escalate"),
        ("posted_at", "transactionTime", "repair"),
        ("updated_at", "departureDay", "escalate"),
        ("updated_at", "recordUpdatedAt", "repair"),
        ("description", "lineDescription", "repair"),
    ],
)
def test_rename_answer_key_plausible_repairs_misleading_escalates(
    field: str, new_name: str, expected: str
) -> None:
    """The key is decided at generation time, never from engine output."""
    from simulators.common import faultgen as fg

    assert fg.expected_for("rename", {"field": field, "new_name": new_name}) == expected


def test_rename_vocabulary_partitions_the_pool() -> None:
    """Every NAME_POOL name plausibly names a field group — except the two
    decoy names (REQ-083 B8).

    The money names are shared by ``total_amount`` and ``amount`` (one
    money group under two canonical names); ``netTotal``/``totalNet`` live
    in DECOY_POOL (a net field next to gross reads ambiguous) and are
    owned by neither group, so they key an escalation, never a repair.
    Every other pool name has exactly one owner.
    """
    from simulators.common import faultgen as fg

    groups = fg.RENAME_PLAUSIBLE
    assert set(groups) == {
        "guest_name", "status", "total_amount", "amount",
        "arrival_date", "departure_date", "business_date",
        "updated_at", "description", "posted_at",
    }
    shared = {"grandtotal", "chargetotal", "amountgross"}
    decoy = {"nettotal", "totalnet"}
    for name in fg.NAME_POOL:
        owners = [f for f, g in groups.items() if _norm(name) in g]
        if _norm(name) in shared:
            assert owners == ["total_amount", "amount"], (name, owners)
        elif _norm(name) in decoy:
            assert owners == [], (name, owners)
            assert name in fg.DECOY_POOL or _norm(name) in {_norm(d) for d in fg.DECOY_POOL}, name
        else:
            assert len(owners) == 1, (name, owners)


def test_engine_has_no_name_vocabulary() -> None:
    """Engine code never copies the answer key's name vocabulary.

    The generator's ``NAME_POOL`` (and any per-field name list derived
    from it) is grading data: it lives in ``simulators``/``verifier``
    only. This test fails if any engine source names a pool member as a
    wire key — in its original form or folded into a string literal — or
    defines a per-field name vocabulary. Documented prose exemptions:
    ``"narrative"`` is a stop word in the docs-discovery stop list
    (``discovery/bootstrap.py``) and otherwise appears only as unquoted
    prose ("narrative docs"), never as a wire key.
    """
    from pathlib import Path

    from simulators.common import faultgen as fg

    def _fold(name: object) -> str:
        return "".join(ch for ch in str(name).lower() if ch.isalnum())

    root = Path(__file__).resolve().parents[2] / "src" / "autobridge"
    assert root.is_dir(), f"engine root missing: {root}"
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "RENAME_PLAUSIBLE" in text or "_names_different_field" in text:
            offenders.append(f"{path.name}: per-field name vocabulary")
            continue
        for name in fg.NAME_POOL:
            folded = _fold(name)
            if name == "narrative":
                # Prose use only ("narrative docs", stop-word tuple); a
                # wire-key use would quote the bare name outside the
                # exempted stop-word line.
                for lineno, line in enumerate(text.splitlines(), 1):
                    if '"narrative", "documentation"' in line:
                        continue
                    if f'"{name}"' in line or f"'{name}'" in line:
                        offenders.append(f"{path.name}:{lineno}: pool name {name!r}")
            else:
                if name in text:
                    offenders.append(f"{path.name}: pool name {name!r} (original form)")
                elif f'"{folded}"' in text or f"'{folded}'" in text or f"`{folded}`" in text:
                    offenders.append(f"{path.name}: pool name {name!r} (folded literal)")
    assert not offenders, "engine copies answer-key vocabulary:\n" + "\n".join(offenders)


def test_reference_solver_reaches_escalate_for_misleading_rename() -> None:
    """The reference solver proves the escalation trigger is served."""
    from simulators.common import faultgen as fg
    from verifier import beacon as _beacon

    seed = bytes.fromhex("12" * 32)
    inst = fg.generate_instance(seed, 45)
    assert inst["vendor"] == "atlas" and inst["family"] == "rename"
    assert inst["expected"] == "escalate"
    assert _beacon.reference_solve(inst) == "escalate"


def test_escalate_rename_grades_question_not_patch() -> None:
    """A patch on a misleading rename is not credited; a question passes."""
    from verifier import beacon as _beacon

    inst = {"family": "rename", "expected": "escalate", "params": {}}
    ok, _ = _beacon.grade_arm_outcome(
        inst, {"outcome": "Blocked", "patch_reached_release": False, "operator_question": True}
    )
    assert ok
    bad, _ = _beacon.grade_arm_outcome(
        inst, {"outcome": "Complete", "patch_reached_release": True, "operator_question": False}
    )
    assert not bad


# ---------------------------------------------------------------------------
# Item 1: simulators serve the rename faithfully
# ---------------------------------------------------------------------------

_ATLAS_RESERVATION_WIRE_KEY = {
    "guest_name": "guestName",
    "status": "status",
    "total_amount": "balance",
    "arrival_date": "startDate",
    "departure_date": "endDate",
    "updated_at": "dateModified",
}

_ATLAS_FOLIO_WIRE_KEY = {
    "description": "description",
    "amount": "amount",
    "business_date": "businessDate",
    "posted_at": "transactionDateTime",
}

_MERIDIAN_RESERVATION_DERIVED = {
    "status": ("State",),
    "arrival_date": ("CreatedUtc", "ScheduledStartUtc", "ActualStartUtc"),
    "departure_date": ("ScheduledEndUtc", "ActualEndUtc"),
    "updated_at": ("UpdatedUtc",),
}

_MERIDIAN_FOLIO_DERIVED = {
    "amount": ("UnitAmount",),
    "description": ("Name",),
    "posted_at": ("PostedUtc",),
}


def _rename_instances(vendor: str) -> list[dict[str, Any]]:
    from simulators.common import faultgen as fg

    out: list[dict[str, Any]] = []
    for hexs in ("ab", "cd", "ef", "12", "00"):
        seed = bytes.fromhex((hexs * 32)[:64])
        for idx in range(80):
            try:
                inst = fg.generate_instance(seed, idx)
            except ValueError:
                continue
            if inst["vendor"] == vendor and inst["family"] == "rename":
                out.append(inst)
    return out


def _mutated(inst: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    from simulators.common import faultgen as fg
    from simulators.common.generator import generate_dataset

    return fg.apply_instance_records(generate_dataset(seed=int(inst["dataset_seed"])), inst)


def test_atlas_rename_serves_new_key_without_old_key() -> None:
    """A served atlas rename has no old key on the wire (list + detail)."""
    from simulators.atlaspms import app as a

    seen = 0
    for inst in _rename_instances("atlas"):
        params = dict(inst["params"])
        entity, field, new_name = (
            str(params["entity"]), str(params["field"]), str(params["new_name"]),
        )
        mut = _mutated(inst)
        rows = mut["reservations"] if entity == "reservation" else mut["folios"]
        wire_key = (
            _ATLAS_RESERVATION_WIRE_KEY if entity == "reservation" else _ATLAS_FOLIO_WIRE_KEY
        )[field]
        for rec, wire in zip(
            rows,
            [a.to_wire_reservation(r) if entity == "reservation" else a.to_wire_transaction(r) for r in rows],
            strict=True,
        ):
            assert (new_name in rec) == (field not in rec)
            if new_name in rec:
                assert wire_key not in wire, (entity, field, new_name, sorted(wire))
                assert new_name in wire or a._snake_to_camel(new_name) in wire, (
                    entity, field, new_name, sorted(wire),
                )
                if entity == "reservation":
                    detail = a.to_wire_reservation_detail(rec)
                    assert wire_key not in detail, (field, new_name, sorted(detail))
                seen += 1
    assert seen > 0, "expected at least one served atlas rename"


def test_harbor_and_legacystay_rename_is_verbatim() -> None:
    """Harbor/LegacyStay serve records verbatim: old key gone, new key on the wire."""
    from simulators.harborpms import app as h
    from simulators.legacystay import app as leg

    seen = 0
    for vendor, mod in (("harbor", h), ("legacystay", leg)):
        serve_res = mod._to_harbor_reservation if vendor == "harbor" else mod._to_legacystay_reservation
        serve_fol = mod._to_harbor_folio if vendor == "harbor" else mod._to_legacystay_folio
        for inst in _rename_instances(vendor):
            params = dict(inst["params"])
            entity, field, new_name = (
                str(params["entity"]), str(params["field"]), str(params["new_name"]),
            )
            mut = _mutated(inst)
            rows = mut["reservations"] if entity == "reservation" else mut["folios"]
            serve = serve_res if entity == "reservation" else serve_fol
            for rec, wire in zip(rows, [serve(r) for r in rows], strict=True):
                if new_name in rec:
                    assert field not in wire, (vendor, entity, field, new_name)
                    assert new_name in wire, (vendor, entity, field, new_name)
                    seen += 1
    assert seen > 0, "expected at least one served harbor/legacystay rename"


def test_meridian_rename_drops_derived_keys_serves_new_key() -> None:
    """Meridian drops derived keys whose source field is gone; the new key rides verbatim."""
    from simulators.meridianpms import app as m

    seen = 0
    for inst in _rename_instances("meridian"):
        params = dict(inst["params"])
        entity, field, new_name = (
            str(params["entity"]), str(params["field"]), str(params["new_name"]),
        )
        mut = _mutated(inst)
        rows = mut["reservations"] if entity == "reservation" else mut["folios"]
        if entity == "reservation" and field == "guest_name":
            # Reservation wires carry no guest key; the name rides the
            # extras verbatim while customer rows drop their derived names
            # (endpoint construction, as customers_get_all does).
            for rec in rows:
                assert (new_name in rec) == (field not in rec)
                if new_name in rec:
                    wire = m.to_wire_reservation(rec)
                    assert new_name in wire, (new_name, sorted(wire))
                    cust = m.to_wire_customer(
                        m.account_id_for(str(rec.get("reservation_id"))),
                        rec.get("guest_name"),
                        None,
                    )
                    for key in ("FirstName", "LastName", "Email"):
                        cust_pop = dict(cust)
                        if field not in rec:
                            cust_pop.pop(key, None)
                        assert (key in cust_pop) == (field in rec), (new_name, key)
                    seen += 1
            continue
        serve = m.to_wire_reservation if entity == "reservation" else m.to_wire_order_item
        derived = (
            _MERIDIAN_RESERVATION_DERIVED if entity == "reservation" else _MERIDIAN_FOLIO_DERIVED
        ).get(field)
        for rec, wire in zip(rows, [serve(r) for r in rows], strict=True):
            if new_name in rec:
                for key in derived or ():
                    assert key not in wire, (entity, field, new_name, key)
                assert new_name in wire, (entity, field, new_name, sorted(wire))
                seen += 1
    assert seen > 0, "expected at least one served meridian rename"


def test_meridian_customer_rows_drop_renamed_guest_name() -> None:
    """Customer rows drop derived names when guest_name is renamed away (HTTP)."""
    from starlette.testclient import TestClient

    from simulators.common import beacon_serve
    from simulators.common import state as _sim_state
    from simulators.meridianpms import app as m

    target = None
    target_seed = ""
    for inst in _rename_instances("meridian"):
        params = dict(inst["params"])
        if (
            str(params["entity"]) == "reservation"
            and str(params["field"]) == "guest_name"
        ):
            target = inst
            target_seed = _seed_for(inst, "meridian")
            break
    assert target is not None, "expected a meridian guest_name rename"
    new_name = str(dict(target["params"])["new_name"])
    beacon_serve.set_beacon(target_seed, int(target["index"]))
    auth_body = {"ClientToken": "meridian-client-token", "AccessToken": "meridian-access-token"}
    try:
        client = TestClient(m.app, raise_server_exceptions=False)
        resp = client.post("/api/connector/v1/customers/getAll/2023-06-06", json=dict(auth_body))
        assert resp.status_code == 200, resp.text[:200]
        rows = resp.json()["Customers"]
        assert rows, "expected customer rows"
        assert all("FirstName" not in r for r in rows), [sorted(r) for r in rows[:2]]
        assert all("LastName" not in r for r in rows)
        # The new key rides the reservation wires verbatim.
        resp_r = client.post("/api/connector/v1/reservations/getAll/2023-06-06", json=dict(auth_body))
        assert resp_r.status_code == 200, resp_r.text[:200]
        assert any(new_name in r for r in resp_r.json()["Reservations"]), new_name
    finally:
        _sim_state.set_beacon_state(None, None)


# ---------------------------------------------------------------------------
# Item 2: detector reports new keys when a mapped field goes empty
# ---------------------------------------------------------------------------


def _wire_spec() -> dict[str, Any]:
    return {
        "spec_version": 3,
        "connector": {"id": "t", "vendor": "atlas"},
        "source": {
            "base_url": "http://example.com",
            "auth": {"type": "bearer", "credential_ref": "TEST_TOKEN"},
            "timezone_source": {"kind": "static", "value": "UTC"},
        },
        "operations": {
            "list_reservations": {
                "method": "GET",
                "path": "/reservations",
                "pagination": {"strategy": "page", "page_param": "page", "items_path": "$.data", "stop_when": ["empty_page"]},
            }
        },
        "entities": {
            "Reservation": {
                "from": "list_reservations",
                "key": "$.reservationID",
                "fields": {
                    "reservation_id": {"path": "$.reservationID"},
                    "departure_date": {"path": "$.endDate"},
                    "status": {"path": "$.status"},
                },
                "checks": [],
            }
        },
        "capabilities": {"read": ["Reservation"]},
        "health": {"canary": {"every": "5m", "operation": "list_reservations"}},
    }


def _wire_summary(wire: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "outcome": "Complete",
        "unmet_criteria": [],
        "operation_results": {
            "list_reservations": {
                "records": wire,
                "raw_pages": [{"success": True, "data": wire}],
            }
        },
        "canonical_records": {},
    }


def test_keys_changed_when_mapped_key_empty_with_new_key() -> None:
    """A mapped key present but empty in every record, alongside a
    genuinely new key, emits keys_changed with the new key and a sample."""
    from autobridge.drift.signals import signals_from_sync

    wire = [
        {"reservationID": f"R{i:04d}", "endDate": "", "status": "confirmed", "checkinDate": f"2026-11-{i + 1:02d}"}
        for i in range(12)
    ]
    sigs = signals_from_sync(_wire_summary(wire), spec=_wire_spec())
    changed = [s for s in sigs if s.kind == "keys_changed"]
    assert len(changed) == 1, [f"{s.kind}: {s.details}" for s in sigs]
    details = changed[0].details
    assert details["missing"] == ["endDate"]
    assert [a["key"] for a in details["added"]] == ["checkinDate"]
    assert details["added"][0]["sample"] == "2026-11-01"


def test_no_keys_changed_when_empty_without_new_keys() -> None:
    """An empty mapped field with no novel keys is not a keys_changed shape."""
    from autobridge.drift.signals import signals_from_sync

    wire = [
        {"reservationID": f"R{i:04d}", "endDate": "", "status": "confirmed"}
        for i in range(12)
    ]
    sigs = signals_from_sync(_wire_summary(wire), spec=_wire_spec())
    assert [s for s in sigs if s.kind == "keys_changed"] == []


# ---------------------------------------------------------------------------
# Item 3: the L3 prompt lists the new key and a raw record sample
# ---------------------------------------------------------------------------


def test_l3_prompt_lists_new_key_and_raw_record_sample() -> None:
    """From a beacon engine arm on a rename: new key + raw sample in the prompt."""
    from autobridge.repair.controller import prepare_climb_inputs
    from autobridge.repair.ladder import _l3_evidence_blocks
    from bench import faults as _faults
    from bench.ablation import derive_from_summaries
    from bench.beacon_run import engine_spec_for

    pre_spec = engine_spec_for({"vendor": "atlas", "index": 45}, None)
    assert pre_spec is not None
    faulted, baseline = _faults.beacon_sync_pair(
        vendor="atlas", eval_seed_hex="12" * 32, index=45, spec=dict(pre_spec),
    )
    diag = derive_from_summaries(dict(pre_spec), baseline_summary=baseline, faulted_summary=faulted)
    assert diag.drift_class == "SCHEMA_RENAME"
    prepared = prepare_climb_inputs(
        spec=dict(pre_spec),
        signals=list(diag.signals or ()),
        summary=diag.faulted_summary if isinstance(diag.faulted_summary, dict) else None,
        sync_run_id="req080-test",
        drift_class=diag.drift_class,
    )
    evidence = prepared.get("evidence") or {}
    assert evidence.get("new_wire_keys"), "new wire keys missing from L3 evidence"
    assert [e["key"] for e in evidence["new_wire_keys"]] == ["lastModified"]
    vendor_rows = [r for r in evidence.get("raw_records", []) if "lastModified" in r]
    assert vendor_rows, "no raw vendor record with the new key in L3 evidence"
    assert "endDate" not in vendor_rows[0], "old key rides the raw sample"
    blocks = _l3_evidence_blocks(evidence)
    assert blocks["raw_records_block"] != "(none recorded)"
    text = blocks["raw_records_block"] + blocks["observed_evidence_block"]
    assert "lastModified" in text
    assert "2026-10-16" in text


# ---------------------------------------------------------------------------
# Engine gate: misleading pairings climb, plausible ones repair at L1
# ---------------------------------------------------------------------------


def _rename_ctx(spec: dict[str, Any], signals: list[Any], drift_class: str = "SCHEMA_RENAME") -> Any:
    from autobridge.repair.context import RepairContext

    return RepairContext(
        spec=spec, signals=signals, fingerprint_delta=None, drift_class=drift_class, extra={}
    )


def test_lexically_close_rename_repairs_with_matching_values() -> None:
    """A near-identical new name with matching values repairs at L1.

    REQ-083 C14: the gate requires value agreement on MORE THAN ONE
    sample — two new-key values each agreeing with a lost value — plus
    the shape and lexical gates.
    """
    from autobridge.drift.signals import DriftSignal
    from autobridge.repair.rules import rename_field as rf

    sig = DriftSignal(
        kind="keys_changed",
        operation="list_reservations",
        entity="Reservation",
        severity="medium",
        message="rename",
        details={
            "entity": "Reservation",
            "missing": ["endDate"],
            "added": [{
                "key": "endDates",
                "sample": "2026-11-06",
                "samples": ["2026-11-06", "2026-11-07"],
                "type": "str",
            }],
            "lost_samples": {"endDate": ["2026-11-06", "2026-11-07"]},
        },
    )
    assert rf.precondition(_rename_ctx(_wire_spec(), [sig])) is True


def test_lexically_unrelated_names_decline_and_climb() -> None:
    """Unrelated new names (in or out of any list) are not an L1 match.

    ``checkinDate`` for ``endDate`` and ``checkOutTs`` (outside every
    generator list) both decline however the values pair: the drift
    climbs to the model with the new-key evidence instead.
    """
    from autobridge.drift.signals import DriftSignal
    from autobridge.repair.rules import rename_field as rf

    def _ctx(new_key: str, sample: str = "2026-11-06") -> Any:
        sig = DriftSignal(
            kind="keys_changed",
            operation="list_reservations",
            entity="Reservation",
            severity="medium",
            message="rename",
            details={
                "entity": "Reservation",
                "missing": ["endDate"],
                "added": [{"key": new_key, "sample": sample, "type": "str"}],
            },
        )
        return _rename_ctx(_wire_spec(), [sig])

    assert rf.precondition(_ctx("checkinDate")) is False
    assert rf.precondition(_ctx("reservationStatus", sample="confirmed")) is False
    assert rf.precondition(_ctx("checkOutTs")) is False
    # A distant name does not repair even when the sample pairs by value.
    assert rf.precondition(_ctx("guestIdentifier", sample="2026-11-06")) is False


def test_single_shaped_sample_declines_without_multi_sample_agreement() -> None:
    """REQ-083 C14: one shaped sample is not enough — the gate needs
    value agreement on more than one sample, otherwise it climbs."""
    from autobridge.drift.signals import DriftSignal
    from autobridge.repair.rules import rename_field as rf

    sig = DriftSignal(
        kind="keys_changed",
        operation="list_reservations",
        entity="Reservation",
        severity="medium",
        message="rename",
        details={
            "entity": "Reservation",
            "missing": ["endDate"],
            "added": [{
                "key": "endDates",
                "sample": "2026-11-06",
                "samples": ["2026-11-06"],
                "type": "str",
            }],
            "lost_samples": {"endDate": ["2026-11-06", "2026-11-07"]},
        },
    )
    assert rf.precondition(_rename_ctx(_wire_spec(), [sig])) is False


def test_disagreeing_values_decline_despite_lexical_closeness() -> None:
    """REQ-083 C14: new values that match no lost value decline, even
    with two samples and a lexically close name."""
    from autobridge.drift.signals import DriftSignal
    from autobridge.repair.rules import rename_field as rf

    sig = DriftSignal(
        kind="keys_changed",
        operation="list_reservations",
        entity="Reservation",
        severity="medium",
        message="rename",
        details={
            "entity": "Reservation",
            "missing": ["endDate"],
            "added": [{
                "key": "endDates",
                "sample": "confirmed",
                "samples": ["confirmed", "cancelled"],
                "type": "str",
            }],
            "lost_samples": {"endDate": ["2026-11-06", "2026-11-07"]},
        },
    )
    assert rf.precondition(_rename_ctx(_wire_spec(), [sig])) is False


def test_distant_delta_rename_declines_close_delta_fires() -> None:
    """Delta-inferred renames take the same generic gate (no word lists)."""
    from autobridge.repair.context import RepairContext
    from autobridge.repair.rules import rename_field as rf

    def _delta(old: str, new: str) -> Any:
        from autobridge.drift.fingerprint import FingerprintDelta

        return FingerprintDelta(
            fingerprint_changed=True,
            pagination_changed=False,
            keys_changed=False,
            types_changed=True,
            changed_entities=["Reservation"],
            pagination_details={},
            keys_details={},
            types_details={"Reservation": {"previous": {old: "str", "other": "str"}, "current": {new: "str", "other": "str"}}},
        )

    far = RepairContext(
        spec=_wire_spec(), signals=[], fingerprint_delta=_delta("endDate", "checkinDate"), drift_class="SCHEMA_RENAME", extra={},
    )
    assert rf.precondition(far) is False
    near = RepairContext(
        spec=_wire_spec(), signals=[], fingerprint_delta=_delta("endDate", "endDates"), drift_class="SCHEMA_RENAME", extra={},
    )
    assert rf.precondition(near) is True


def test_explicit_extra_rename_is_exempt_from_plausibility_gate() -> None:
    """A caller-stated rename (FIAS handshake, tests) still repairs at L1."""
    from autobridge.drift.signals import DriftSignal
    from autobridge.repair.rules import rename_field as rf

    sig = DriftSignal(
        kind="keys_changed",
        operation="list_reservations",
        entity="Reservation",
        severity="medium",
        message="rename",
        details={"entity": "Reservation", "missing": ["endDate"], "added": [{"key": "checkinDate", "sample": "x", "type": "str"}]},
    )
    from autobridge.repair.context import RepairContext

    ctx = RepairContext(
        spec=_wire_spec(),
        signals=[sig],
        fingerprint_delta=None,
        drift_class="SCHEMA_RENAME",
        extra={"rename": {"entity": "Reservation", "old_field": "endDate", "new_field": "checkinDate"}},
    )
    assert rf.precondition(ctx) is True


def test_misleading_rename_climbs_to_model_question() -> None:
    """End to end on the flagship: L1 declines, the model is asked, no patch."""
    from autobridge.repair.controller import RepairController
    from bench import faults as _faults
    from bench.ablation import derive_from_summaries
    from bench.beacon_run import engine_spec_for

    pre_spec = engine_spec_for({"vendor": "atlas", "index": 45}, None)
    assert pre_spec is not None
    faulted, baseline = _faults.beacon_sync_pair(
        vendor="atlas", eval_seed_hex="12" * 32, index=45, spec=dict(pre_spec),
    )
    diag = derive_from_summaries(dict(pre_spec), baseline_summary=baseline, faulted_summary=faulted)
    provider = FakeProvider(
        responses={
            "frontier": Completion(
                text=json.dumps(
                    {
                        "escalate": "Which vendor field should departure_date map to now that $.endDate is empty?",
                        "rationale": "lastModified holds the dates but names a different field",
                    }
                ),
                usage=Usage(10, 5),
            )
        }
    )
    res = RepairController().handle(
        spec=dict(pre_spec),
        drift_class="SCHEMA_RENAME",
        signals=list(diag.signals),
        fingerprint_delta=diag.fingerprint_delta,
        hypotheses=[],
        provider=provider,
        budget=None,
        validate_against=dict(pre_spec),
    )
    assert res.decision == "escalate"
    assert res.candidate_patch in (None, [])
    assert res.model_usage.get("frontier_calls", 0) >= 1


# ---------------------------------------------------------------------------
# Item 5: sweep extension — every rename carries its new key and detects
# ---------------------------------------------------------------------------


def test_detection_sweep_rename_new_key_served_and_detected() -> None:
    """Every beacon rename (plausible and misleading, all vendors): the
    served wire carries the new key without the old key, the faulted
    canonical records differ from the baseline, and the sync never ends
    Complete with 0 signals."""
    from autobridge.drift.signals import signals_from_sync
    from bench import faults as _faults
    from bench.beacon_run import engine_spec_for

    vendors = ("atlas", "harbor", "legacystay", "meridian")
    seen: set[tuple[str, str]] = set()
    silent: list[str] = []
    for vendor in vendors:
        for expected in ("repair", "escalate"):
            found = None
            for inst in _rename_instances(vendor):
                if inst["expected"] == expected:
                    found = inst
                    break
            assert found is not None, f"no {expected} rename for {vendor}"
            idx = int(found["index"])
            # Recover the seed that generated this instance.
            seed_hex = _seed_for(found, vendor)
            pre_spec = engine_spec_for({"vendor": vendor, "index": idx}, None)
            assert pre_spec is not None, (vendor, idx)
            faulted, baseline = _faults.beacon_sync_pair(
                vendor=vendor, eval_seed_hex=seed_hex, index=idx, spec=dict(pre_spec),
            )
            faulted_canon = faulted.get("canonical_records") or {}
            baseline_canon = baseline.get("canonical_records") or {}
            assert faulted_canon != baseline_canon, (vendor, idx, "records must differ")
            sigs = signals_from_sync(faulted, spec=dict(pre_spec), baseline_summary=baseline)
            if faulted.get("outcome") == "Complete" and not sigs:
                silent.append(f"{vendor}/{idx}/{expected}")
            seen.add((vendor, expected))
    assert seen == {(v, e) for v in vendors for e in ("repair", "escalate")}, seen
    assert not silent, f"silent renames (Complete, 0 signals): {silent}"


def _seed_for(inst: dict[str, Any], vendor: str) -> str:
    from simulators.common import faultgen as fg

    for hexs in ("ab", "cd", "ef", "12", "00"):
        seed = bytes.fromhex((hexs * 32)[:64])
        for idx in range(80):
            try:
                cand = fg.generate_instance(seed, idx)
            except ValueError:
                continue
            if (
                cand["vendor"] == vendor
                and cand["index"] == int(inst["index"])
                and cand["params"] == inst["params"]
            ):
                return (hexs * 32)[:64]
    raise AssertionError(f"seed not found for {inst}")


def test_l3_evidence_pairs_vanished_and_new_values() -> None:
    """REQ-083 C14: the L3 evidence shows the vanished key's last-good
    values next to the new key's values (from the stored baseline), so
    the model judges old and new together, never the new name alone."""
    from autobridge.drift.signals import DriftSignal
    from autobridge.repair.controller import build_generic_l3_evidence

    sig = DriftSignal(
        kind="keys_changed",
        operation="list_reservations",
        entity="Reservation",
        severity="medium",
        message="rename",
        details={
            "entity": "Reservation",
            "missing": ["endDate"],
            "added": [{
                "key": "checkinDate",
                "sample": "2026-11-06",
                "samples": ["2026-11-06", "2026-11-07"],
                "type": "str",
            }],
            "lost_samples": {"endDate": ["2026-11-06", "2026-11-07"]},
        },
    )
    summary = {
        "outcome": "Complete",
        "unmet_criteria": [],
        "operation_results": {
            "list_reservations": {
                "records": [
                    {"reservationID": "R1", "checkinDate": "2026-11-06"},
                    {"reservationID": "R2", "checkinDate": "2026-11-07"},
                ],
            }
        },
        "canonical_records": {
            "Reservation": [
                {"reservation_id": "R1", "departure_date": None},
                {"reservation_id": "R2", "departure_date": None},
            ]
        },
    }
    baseline = {
        "Reservation": [
            {"reservation_id": "R1", "departure_date": "2026-11-06"},
            {"reservation_id": "R2", "departure_date": "2026-11-07"},
        ]
    }
    evidence = build_generic_l3_evidence(
        _wire_spec(), [sig], None, summary, "SCHEMA_RENAME",
        failed_attempts=None, baseline=baseline,
    )
    assert evidence["new_wire_keys"], "new wire keys missing from L3 evidence"
    entry = next(e for e in evidence["new_wire_keys"] if e["key"] == "checkinDate")
    pair = entry.get("pairs_with")
    assert pair, "vanished key's last-good values not paired with the new key"
    assert pair["field"] == "departure_date", pair
    assert pair["value_match"] is True, pair
    assert "2026-11-06" in pair["baseline_sample"], pair
    # Without a baseline the pairing is absent but the new key still shows.
    plain = build_generic_l3_evidence(
        _wire_spec(), [sig], None, summary, "SCHEMA_RENAME", failed_attempts=None
    )
    plain_entry = next(e for e in plain["new_wire_keys"] if e["key"] == "checkinDate")
    assert plain_entry.get("pairs_with") is None
