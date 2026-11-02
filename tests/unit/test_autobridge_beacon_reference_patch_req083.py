"""REQ-083 A2: every repair-keyed family is expressible as a spec patch.

The generator's reference repair, expressed as a JSON Patch in the
closed transform/pointer vocabulary, applied through the REAL
``sync_candidate`` path (vendor public URL + spec ``base_url`` path +
the credential env), passes the verifier's records judge. Where no
spec patch can express the repair the prover returns ``None`` and the
key must stay reachable another way (semantic scaling via its accepted
escalation; ``tz_shift`` is removed from the draw by B7).
"""

from __future__ import annotations

import contextlib
import importlib

import pytest

import verifier.beacon as vbeacon
from bench import beacon_run
from bench import faults as faults
from bench import reference_patch as refpatch

# Fixed eval seeds (32 bytes hex) + index range; the scan picks the first
# repair instance per (vendor, family, kind). Wide enough that every
# expressible repair family is hit for every vendor.
_EVAL_SEEDS = ["11" * 32, "22" * 32, "33" * 32, "44" * 32, "55" * 32, "66" * 32, "77" * 32, "88" * 32]
_SCAN_INDICES = range(60)

_VENDORS = ["atlas", "harbor", "legacystay", "meridian"]


def _find_repair(vendor: str, family: str, kind: str | None = None) -> tuple[str, int, dict]:
    """First repair instance for (vendor, family[, kind]) over the fixed seeds."""
    import simulators.common.faultgen as faultgen

    for seed_hex in _EVAL_SEEDS:
        for index in _SCAN_INDICES:
            if index % 5 != _VENDORS.index(vendor):
                continue
            inst = faultgen.generate_instance(bytes.fromhex(seed_hex), index)
            if str(inst["family"]) != family or str(inst["expected"]) != "repair":
                continue
            if kind is not None and str(dict(inst["params"]).get("kind", "")) != kind:
                continue
            if kind is not None or family != "semantic":
                return seed_hex, index, inst
            # semantic repair: minor_major is patch-expressible, gross_net is not
            if str(dict(inst["params"]).get("kind", "")) == "minor_major":
                return seed_hex, index, inst
    raise AssertionError(f"no repair instance for {(vendor, family, kind)} in fixed seeds")


def _wire_by_op(summary: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    op_results = summary.get("operation_results", {})
    if isinstance(op_results, dict):
        for op, res in op_results.items():
            recs = res.get("records") if isinstance(res, dict) else getattr(res, "records", None)
            if isinstance(recs, list):
                out[str(op)] = [r for r in recs if isinstance(r, dict)]
    return out


@contextlib.contextmanager
def _faulted_transport(vendor: str, eval_seed_hex: str, index: int, spec: dict):
    """In-process faulted serving for ``sync_candidate`` (same app + beacon slot)."""
    from simulators.common import beacon_serve as _beacon

    with faults.isolated_sim_state(), faults.fake_env():
        _beacon.set_beacon(eval_seed_hex, index)
        app = importlib.import_module(faults._BEACON_VENDOR_APPS[vendor]).app
        import urllib.parse as _urlparse

        from autobridge.runtime.http import resolve_env_placeholders as _resolve

        base_path = _urlparse.urlsplit(_resolve(str(spec["source"]["base_url"]))).path.rstrip("/")
        fake_base = faults._FAKE_BASE_URL + base_path

        def factory(**_kw):
            from starlette.testclient import TestClient

            return TestClient(app, base_url=fake_base)

        try:
            yield factory
        finally:
            from simulators.common import state as _sim_state

            _sim_state.set_beacon_state(None, None)


def _prove(vendor: str, family: str, kind: str | None = None) -> None:
    seed_hex, index, inst = _find_repair(vendor, family, kind)
    _prove_instance(seed_hex, index, inst)


def _prove_instance(seed_hex: str, index: int, inst: dict) -> None:
    vendor = str(inst["vendor"])
    vendors = beacon_run.load_vendors()
    desc = {"index": index, "vendor": vendor}
    spec = beacon_run.engine_spec_for(desc, vendors)
    assert spec is not None
    faulted, baseline = faults.beacon_sync_pair(
        vendor=vendor, eval_seed_hex=seed_hex, index=index, spec=spec
    )
    patch = refpatch.reference_patch_for(
        inst, spec,
        faulted_wire=_wire_by_op(faulted), baseline_wire=_wire_by_op(baseline),
    )
    assert patch, f"no reference patch for {(vendor, inst['params'])}"
    from autobridge.spec.patch import apply_patch

    patched = apply_patch(dict(spec), list(patch))
    with _faulted_transport(vendor, seed_hex, index, spec) as factory:
        summary = beacon_run.sync_candidate(
            patched, "http://localhost:9", client_factory=factory
        )
    assert summary.get("outcome") == "Complete", (
        f"{(vendor, inst['params'])}: candidate sync {summary.get('outcome')} "
        f"{summary.get('unmet_criteria')} patch={patch}"
    )
    produced = beacon_run.canonical_to_records(summary.get("canonical_records", {}))
    base_records = beacon_run.unfaulted_baseline_records(vendor, seed_hex, index, spec)
    judged = vbeacon.judge_produced_records(inst, produced, patched, base_records)
    assert judged["verdict"] == "pass", (
        f"{(vendor, inst['params'])}: judge failed {judged['failed_checks']} patch={patch}"
    )


@pytest.mark.parametrize("vendor", _VENDORS)
def test_reference_rename_repairs_through_sync_candidate(vendor: str) -> None:
    _prove(vendor, "rename")


@pytest.mark.parametrize("vendor", _VENDORS)
def test_reference_nesting_repairs_through_sync_candidate(vendor: str) -> None:
    _prove(vendor, "nesting")


@pytest.mark.parametrize("vendor", _VENDORS)
def test_reference_pagination_repairs_through_sync_candidate(vendor: str) -> None:
    _prove(vendor, "pagination")


@pytest.mark.parametrize("vendor", _VENDORS)
def test_reference_code_list_repairs_through_sync_candidate(vendor: str) -> None:
    _prove(vendor, "code_list")


def _find_dates_mode(vendor: str, mode: str) -> tuple[str, int, dict]:
    import simulators.common.faultgen as faultgen

    for seed_hex in _EVAL_SEEDS:
        for index in _SCAN_INDICES:
            if index % 5 != _VENDORS.index(vendor):
                continue
            inst = faultgen.generate_instance(bytes.fromhex(seed_hex), index)
            if (
                str(inst["family"]) == "dates"
                and str(inst["expected"]) == "repair"
                and str(dict(inst["params"]).get("mode", "")) == mode
            ):
                return seed_hex, index, inst
    raise AssertionError(f"no dates/{mode} repair for {vendor}")


@pytest.mark.parametrize("vendor", [v for v in _VENDORS if v != "meridian"])
def test_reference_dates_format_repairs_through_sync_candidate(vendor: str) -> None:
    # REQ-083 D18: meridian never draws dates/format (UTC-instant vendor).
    seed_hex, index, inst = _find_dates_mode(vendor, "format")
    _prove_instance(seed_hex, index, inst)


@pytest.mark.parametrize("vendor", [v for v in _VENDORS if v != "meridian"])
def test_reference_dates_cutoff_repairs_through_sync_candidate(vendor: str) -> None:
    # Meridian cutoff is keyed no_patch (the wire never serves it).
    seed_hex, index, inst = _find_dates_mode(vendor, "cutoff")
    _prove_instance(seed_hex, index, inst)


def test_reference_minor_major_repairs_through_sync_candidate() -> None:
    import simulators.common.faultgen as faultgen

    for seed_hex in _EVAL_SEEDS:
        for index in _SCAN_INDICES:
            inst = faultgen.generate_instance(bytes.fromhex(seed_hex), index)
            if (
                str(inst["family"]) == "semantic"
                and str(inst["expected"]) == "repair"
                and str(dict(inst["params"]).get("kind", "")) == "minor_major"
            ):
                _prove(str(inst["vendor"]), "semantic")
                return
    raise AssertionError("no semantic/minor_major repair in fixed seeds")


def test_reference_fias_layout_repairs_through_sync_candidate() -> None:
    import simulators.common.faultgen as faultgen

    for seed_hex in _EVAL_SEEDS:
        for index in _SCAN_INDICES:
            if index % 5 != 4:
                continue
            inst = faultgen.generate_instance(bytes.fromhex(seed_hex), index)
            if str(inst["family"]) == "fias_layout" and str(inst["expected"]) == "repair":
                break
        else:
            continue
        break
    else:
        raise AssertionError("no fias_layout repair in fixed seeds")
    vendors = beacon_run.load_vendors()
    desc = {"index": index, "vendor": "fias-pabx"}
    spec = beacon_run.engine_spec_for(desc, vendors)
    assert spec is not None
    faulted, baseline = faults.beacon_sync_pair(
        vendor="fias-pabx", eval_seed_hex=seed_hex, index=index, spec=spec
    )
    patch = refpatch.reference_patch_for(
        inst, spec,
        faulted_wire=_wire_by_op(faulted), baseline_wire=_wire_by_op(baseline),
    )
    assert patch, f"no reference patch for fias_layout {inst['params']}"
    from autobridge.spec.patch import apply_patch

    patched = apply_patch(dict(spec), list(patch))
    faulted2, _baseline2 = faults.beacon_sync_pair(
        vendor="fias-pabx", eval_seed_hex=seed_hex, index=index, spec=patched
    )
    assert faulted2.get("outcome") == "Complete", faulted2.get("unmet_criteria")
    produced = beacon_run.canonical_to_records(faulted2.get("canonical_records", {}))
    # The comparison baseline is the PRE-DRIFT spec over the unfaulted
    # feed (the one judging regime), never the patched spec.
    base_records = beacon_run.unfaulted_baseline_records("fias-pabx", seed_hex, index, spec)
    judged = vbeacon.judge_produced_records(inst, produced, patched, base_records)
    assert judged["verdict"] == "pass", judged["failed_checks"]


def test_reference_gross_net_has_no_spec_patch() -> None:
    """gross_net scaling is patch-inexpressible (no factor arg, plan-level
    vocabulary) — the key stays reachable via its accepted escalation."""
    import simulators.common.faultgen as faultgen
    from verifier.beacon import accepted_outcomes_for, reference_solve

    seen = 0
    for seed_hex in _EVAL_SEEDS:
        for index in _SCAN_INDICES:
            inst = faultgen.generate_instance(bytes.fromhex(seed_hex), index)
            if (
                str(inst["family"]) != "semantic"
                or str(dict(inst["params"]).get("kind", "")) != "gross_net"
            ):
                continue
            seen += 1
            vendors = beacon_run.load_vendors()
            spec = beacon_run.engine_spec_for(
                {"index": index, "vendor": str(inst["vendor"])}, vendors
            )
            assert spec is not None
            assert refpatch.reference_patch_for(inst, spec) is None
            assert "degraded_question" in accepted_outcomes_for(inst)
            assert reference_solve(inst) == "repair"
    assert seen > 0


def test_reference_tz_shift_has_no_spec_patch() -> None:
    """tz_shift is patch-inexpressible (no hour/day offset arg) — B7
    removes it from the draw, so no drawn instance may carry it."""
    import simulators.common.faultgen as faultgen

    seen = 0
    for raw in ("a" * 64, "b" * 64, "c" * 64):
        for inst in faultgen.generate_instances(bytes.fromhex(raw), 25):
            if str(inst["family"]) == "dates" and str(dict(inst["params"]).get("mode")) == "tz_shift":
                seen += 1
    assert seen == 0, f"tz_shift still drawn {seen}x (B7 removes it)"


def test_every_drawn_date_format_is_parseable_by_the_closed_set() -> None:
    """REQ-083 C16: bench-side parity moved out of the engine test.

    Every date format the generator draws must parse with the date
    transforms' allow-listed ``input_format`` (no generator import in
    engine tests; the engine side only asserts the fixed examples).
    """
    import datetime

    import simulators.common.faultgen as faultgen
    from autobridge.runtime.transforms.base import is_allowed_date_input_format
    from autobridge.runtime.transforms.date_local import transform_date_local

    sample = datetime.date(2026, 10, 15)
    for fmt in faultgen.DATE_FORMATS:
        assert is_allowed_date_input_format(fmt), fmt
        assert transform_date_local(sample.strftime(fmt), {"input_format": fmt}) == "2026-10-15"


def test_unrepaired_rename_fails_the_judge() -> None:
    """REQ-083 B5: an unrepaired rename never passes as a repair.

    The mapped field goes empty while the new key holds the values; the
    judge must fail (field presence + value comparison), never credit
    the no-op. Uses the first atlas rename repair in the fixed scan
    (arrival_date -> arrivalDay); the value comparison itself is
    field-generic (see the departure_date unit test below).
    """
    import simulators.common.faultgen as faultgen

    for seed_hex in _EVAL_SEEDS:
        for index in _SCAN_INDICES:
            if index % 5 != 0:
                continue
            inst = faultgen.generate_instance(bytes.fromhex(seed_hex), index)
            if str(inst["family"]) == "rename" and str(inst["expected"]) == "repair":
                break
        else:
            continue
        break
    else:
        raise AssertionError("no atlas rename repair in scan")
    vendors = beacon_run.load_vendors()
    spec = beacon_run.engine_spec_for({"index": index, "vendor": "atlas"}, vendors)
    assert spec is not None
    faulted, _baseline = faults.beacon_sync_pair(
        vendor="atlas", eval_seed_hex=seed_hex, index=index, spec=spec
    )
    produced = beacon_run.canonical_to_records(faulted.get("canonical_records", {}))
    base_records = beacon_run.unfaulted_baseline_records("atlas", seed_hex, index, spec)
    judged = vbeacon.judge_produced_records(inst, produced, spec, base_records)
    assert judged["verdict"] == "fail", "unrepaired rename must not pass"
    names = {f["name"] for f in judged["failed_checks"]}
    assert names & {"field_missing", "field_value_mismatch", "business_date_mismatch"}, names


def test_judge_compares_every_canonical_field_value() -> None:
    """REQ-083 B5 unit proof: departure_date (and status, description,
    updated_at) are value-compared, not just presence-checked."""
    import verifier.beacon as vb

    base = {
        "reservations": [{
            "reservation_id": "R1",
            "arrival_date": "2026-11-03",
            "departure_date": "2026-11-06",
            "status": "confirmed",
            "total_amount": "200.00",
            "updated_at": "2026-11-03T10:00:00+00:00",
        }],
        "folios": [{
            "charge_id": "C1",
            "business_date": "2026-11-03",
            "description": "Room charge",
            "amount": "100.00",
        }],
    }
    inst = {
        "index": 0, "vendor": "atlas", "family": "rename",
        "params": {"entity": "reservation", "field": "departure_date", "new_name": "checkoutDate"},
        "expected": "repair", "dataset_seed": 1, "seed_hex": "00" * 32,
    }

    def judged(produced):
        return vb.judge_produced_records(
            inst,
            {"reservations": produced[0], "folios": produced[1]},
            None,
            {"reservations": base["reservations"], "folios": base["folios"]},
        )

    ok_res = [dict(base["reservations"][0])]
    ok_fol = [dict(base["folios"][0])]
    assert judged((ok_res, ok_fol))["verdict"] == "pass"

    # Empty mapped field with the value elsewhere: fail.
    bad_res = [dict(base["reservations"][0], departure_date=None)]
    fails = judged((bad_res, ok_fol))["failed_checks"]
    assert any(f["name"] == "field_value_mismatch" for f in fails), fails

    # Wrong value in any compared field: fail.
    for field, bad_value in (
        ("departure_date", "2026-11-07"),
        ("status", "cancelled"),
        ("updated_at", "2026-11-03T11:00:00+00:00"),
    ):
        rec = dict(base["reservations"][0], **{field: bad_value})
        fails = judged(([rec], ok_fol))["failed_checks"]
        assert any(f["name"] == "field_value_mismatch" for f in fails), (field, fails)
    rec = dict(base["folios"][0], description="Mini-bar")
    fails = judged((ok_res, [rec]))["failed_checks"]
    assert any(f["name"] == "field_value_mismatch" for f in fails), fails
    # Baseline None stays equivalent to absent (wire omits nulls).
    none_base = {
        "reservations": [dict(base["reservations"][0], status=None)],
        "folios": [dict(base["folios"][0], description=None)],
    }
    assert vb.judge_produced_records(
        inst, none_base, None, none_base
    )["verdict"] == "pass"
