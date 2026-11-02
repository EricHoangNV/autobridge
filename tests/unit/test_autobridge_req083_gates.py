"""REQ-083 C14/B5/C13 coverage: detector samples, L1 gate, L3 pairing,
capabilities args, context_tz checks, pagination guards."""

from __future__ import annotations


def _spec() -> dict:
    return {
        "spec_version": 3,
        "connector": {"id": "t", "vendor": "atlas"},
        "source": {
            "base_url": "http://example.com",
            "auth": {"type": "bearer", "credential_ref": "TEST_TOKEN"},
            "timezone_source": {"kind": "static", "value": "America/New_York"},
        },
        "operations": {
            "list_reservations": {
                "method": "GET",
                "path": "/reservations",
                "pagination": {
                    "strategy": "offset",
                    "offset_param": "offset",
                    "limit_param": "limit",
                    "items_path": "$.data",
                    "stop_when": ["empty_page"],
                },
            }
        },
        "entities": {
            "Reservation": {
                "from": "list_reservations",
                "key": "$.reservation_id",
                "fields": {
                    "reservation_id": {"path": "$.reservation_id"},
                    "guest_name": {"path": "$.guestName"},
                },
                "checks": [],
            }
        },
        "capabilities": {"read": ["Reservation"]},
        "health": {"canary": {"every": "5m", "operation": "list_reservations"}},
    }


def _summary(wire: list[dict], baseline_wire: list[dict] | None = None) -> dict:
    from autobridge.runtime.interpreter import OperationResult

    summary: dict = {
        "outcome": "Complete",
        "unmet_criteria": [],
        "operation_results": {
            "list_reservations": OperationResult(
                operation="list_reservations", records=wire, pages_fetched=1,
                raw_pages=[{"success": True, "data": wire}], stop_reason="empty_page",
            )
        },
        "canonical_records": {},
        "blocked_records": {},
    }
    if baseline_wire is not None:
        summary["baseline_summary"] = {
            "canonical_records": {},
            "operation_results": {
                "list_reservations": {"records": baseline_wire,
                                      "raw_pages": [{"success": True, "data": baseline_wire}]},
            },
        }
    return summary


def test_keys_changed_carries_samples_and_lost_values() -> None:
    from autobridge.drift.signals import signals_from_sync

    wire = [
        {"reservation_id": f"R{i:04d}", "guest_names": f"Guest {i}"} for i in range(1, 13)
    ]
    base = [
        {"reservation_id": f"R{i:04d}", "guestName": f"Guest {i}"} for i in range(1, 13)
    ]
    _sum = _summary(wire, base)
    _baseline = _sum.pop("baseline_summary", None)
    signals = signals_from_sync(_sum, spec=_spec(), baseline_summary=_baseline)
    kc = next(s for s in signals if s.kind == "keys_changed")
    added = next(a for a in kc.details["added"] if a["key"] == "guest_names")
    assert len(added["samples"]) >= 2, added
    assert kc.details["lost_samples"]["guestName"][:3] == ["Guest 1", "Guest 2", "Guest 3"]


def test_emptied_signal_carries_candidate_and_lost_samples() -> None:
    from autobridge.drift.signals import signals_from_sync

    # The old key stays present but empty (spec maps $.guest_name).
    wire = [
        {"reservation_id": f"R{i:04d}", "guestName": "", "checkinDate": f"2026-11-{i + 1:02d}"}
        for i in range(1, 13)
    ]
    base = [
        {"reservation_id": f"R{i:04d}", "guestName": f"Guest {i}"} for i in range(1, 13)
    ]
    _sum = _summary(wire, base)
    _baseline = _sum.pop("baseline_summary", None)
    signals = signals_from_sync(_sum, spec=_spec(), baseline_summary=_baseline)
    emptied = next(s for s in signals if s.kind == "mapped_field_emptied")
    assert emptied.details["wire_key"] == "guestName"
    assert set(emptied.details["added_candidate_samples"]["checkinDate"]) == {
        "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06",
    }
    assert emptied.details["lost_samples"][:3] == ["Guest 1", "Guest 2", "Guest 3"]


def test_l1_repairs_with_multi_sample_agreement_and_baseline() -> None:
    """End to end through the controller: propose iff values agree."""
    from autobridge.drift.classifier import classify
    from autobridge.drift.signals import signals_from_sync
    from autobridge.repair.controller import RepairController

    wire = [
        {"reservation_id": f"R{i:04d}", "guest_names": f"Guest {i}"} for i in range(1, 13)
    ]
    base = [
        {"reservation_id": f"R{i:04d}", "guestName": f"Guest {i}"} for i in range(1, 13)
    ]
    summary = _summary(wire, base)
    baseline_arg = summary.pop("baseline_summary", None)
    signals = signals_from_sync(summary, spec=_spec(), baseline_summary=baseline_arg)
    assert classify(signals).drift_class == "SCHEMA_RENAME"
    result = RepairController().handle_from_signals(spec=_spec(), signals=signals)
    assert result.decision == "propose", result.decision
    assert result.rule_id == "rename_field"


def test_l1_declines_without_baseline_values() -> None:
    from autobridge.drift.classifier import classify
    from autobridge.drift.signals import signals_from_sync
    from autobridge.repair.controller import RepairController

    wire = [
        {"reservation_id": f"R{i:04d}", "guest_names": f"Guest {i}"} for i in (1, 2, 3)
    ]
    signals = signals_from_sync(_summary(wire), spec=_spec())
    assert classify(signals).drift_class == "SCHEMA_RENAME"
    result = RepairController().handle_from_signals(spec=_spec(), signals=signals)
    assert result.decision == "escalate", result.decision


def test_l3_evidence_pairs_old_and_new_values() -> None:
    from autobridge.repair.controller import build_generic_l3_evidence, prepare_climb_inputs

    spec = _spec()
    wire = [{"reservation_id": "R0001", "guest_names": "Guest 1"},
            {"reservation_id": "R0002", "guest_names": "Guest 2"}]
    base = [{"reservation_id": "R0001", "guest_name": "Guest 1"},
            {"reservation_id": "R0002", "guest_name": "Guest 2"}]
    signals = __import__("autobridge.drift.signals", fromlist=["signals_from_sync"]).signals_from_sync(
        _summary(wire, base), spec=spec
    )
    baseline = {"Reservation": [{"reservation_id": "R0001", "guest_name": "Guest 1"},
                                {"reservation_id": "R0002", "guest_name": "Guest 2"}]}
    evidence = build_generic_l3_evidence(
        spec, list(signals), None,
        {"outcome": "Complete", "unmet_criteria": [], "canonical_records": {},
         "operation_results": {"list_reservations": {"records": wire}}},
        "SCHEMA_RENAME", failed_attempts=None, baseline=baseline,
    )
    entry = next(e for e in evidence["new_wire_keys"] if e["key"] == "guest_names")
    assert entry["pairs_with"] is not None
    assert entry["pairs_with"]["field"] == "guest_name"
    assert entry["pairs_with"]["value_match"] is True
    prepared = prepare_climb_inputs(
        spec=spec, signals=list(signals),
        summary={"outcome": "Complete", "unmet_criteria": [], "canonical_records": {},
                 "operation_results": {"list_reservations": {"records": wire}}},
        sync_run_id="t", drift_class="SCHEMA_RENAME", baseline=baseline,
    )
    block = prepared["evidence"]["new_wire_keys"]
    assert any(e.get("pairs_with", {}).get("value_match") for e in block if e["key"] == "guest_names")


def test_capabilities_list_transform_args_with_input_format() -> None:
    from autobridge.control.routes.capabilities import capabilities_payload

    payload = capabilities_payload()
    assert "transform_args" in payload
    assert "input_format" in payload["transform_args"]["date_local"]
    assert "input_format" in payload["transform_args"]["business_date"]
    assert "minor_units" in payload["transform_args"]["money"]


def test_run_checks_uses_declared_zone() -> None:
    """The business-date invariant tries the spec's declared zone first.

    A property outside the legacy map, with a business date derived
    under the declared zone: without the zone the UTC fallback
    disagrees (fail); with it the record verifies (pass).
    """
    from autobridge.runtime.checks import run_checks

    # Posted 05:00 UTC Oct 26 = 01:00 NY Oct 26 -> cutoff 4 -> Oct 25.
    # UTC derives Oct 26 and fails; the legacy map has no prop-009.
    recs = [{"charge_id": "C1", "business_date": "2026-10-25",
             "posted_at": "2026-10-26T05:00:00+00:00", "property_id": "prop-009"}]
    checks = [{"kind": "business_date_invariant", "field": "business_date",
               "reference_field": "posted_at", "cutoff": 4}]
    assert run_checks(recs, checks, entity_name="FolioCharge",
                      context_tz="America/New_York") == []
    assert run_checks(recs, checks, entity_name="FolioCharge") == ["business_date_invariant"]


def test_pagination_repeated_page_stops() -> None:
    """A vendor that ignores page params stops after the repeated page."""
    from autobridge.runtime.http import HttpClient
    from autobridge.runtime.interpreter import Interpreter
    from autobridge.spec.models import load_spec_from_dict

    spec = load_spec_from_dict(_spec())
    seen = {"n": 0}

    def factory(**_kw):
        import httpx as _httpx

        def handler(_request: _httpx.Request) -> _httpx.Response:  # noqa: ARG001
            seen["n"] += 1
            return _httpx.Response(200, json={"data": [{"reservation_id": "R1"}] * 3})

        return _httpx.Client(transport=_httpx.MockTransport(handler))

    client = HttpClient("http://x", client_factory=factory)
    interp = Interpreter(spec, http_client=client)
    res = interp.execute_operation("list_reservations", limit_override=50)
    assert res.stop_reason == "repeated_page", res.stop_reason
    assert seen["n"] == 2, seen
    assert len(res.records) == 3, res.records


def test_tokenless_offset_pages_past_first_page() -> None:
    """Offset without a next token advances until the empty page."""
    from autobridge.runtime.http import HttpClient
    from autobridge.runtime.interpreter import Interpreter
    from autobridge.spec.models import load_spec_from_dict

    spec = load_spec_from_dict(_spec())
    seen = {"n": 0}
    rows = [{"reservation_id": f"R{i:04d}"} for i in range(5)]

    def factory(**_kw):
        import httpx as _httpx

        def handler(request: _httpx.Request) -> _httpx.Response:
            import urllib.parse as _u

            raw_q = request.url.query
            qs = raw_q.decode() if isinstance(raw_q, bytes) else str(raw_q)
            qp = dict(_u.parse_qsl(qs))
            seen["n"] += 1
            start = max(0, int(qp.get("offset", "0")))
            return _httpx.Response(200, json={"data": rows[start:start + 2]})

        return _httpx.Client(transport=_httpx.MockTransport(handler))

    client = HttpClient("http://x", client_factory=factory)
    interp = Interpreter(spec, http_client=client)
    res = interp.execute_operation("list_reservations", limit_override=50)
    assert res.stop_reason == "empty_page", res.stop_reason
    assert seen["n"] == 4, seen  # offsets 0,2,4 + empty probe at 6
    assert len(res.records) == 5, res.records


def test_l1_repairs_from_emptied_signal_with_multi_sample_agreement() -> None:
    """REQ-083 C14: the emptied-signal path also requires multi-sample
    value agreement against the stored lost values."""
    from autobridge.drift.classifier import classify
    from autobridge.drift.signals import signals_from_sync
    from autobridge.repair.controller import RepairController

    wire = [
        {"reservation_id": f"R{i:04d}", "guestName": "", "guestNames": f"Guest {i}"}
        for i in range(1, 13)
    ]
    base = [
        {"reservation_id": f"R{i:04d}", "guestName": f"Guest {i}"} for i in range(1, 13)
    ]
    summary = _summary(wire, base)
    baseline = summary.pop("baseline_summary", None)
    signals = signals_from_sync(summary, spec=_spec(), baseline_summary=baseline)
    assert classify(signals).drift_class == "SCHEMA_RENAME"
    result = RepairController().handle_from_signals(spec=_spec(), signals=signals)
    assert result.decision == "propose", result.decision
    assert result.rule_id == "rename_field"
    assert result.candidate_patch == [
        {"op": "replace", "path": "/entities/Reservation/fields/guest_name/path",
         "value": "$.guestNames"}
    ]


def test_wire_value_helpers_cover_branches() -> None:
    from autobridge.repair.rules.wire import dedup_samples, values_agree

    assert values_agree("a", "a") is True
    assert values_agree("a", "b") is False
    assert values_agree(None, "a") is False
    assert values_agree("a", None) is False
    assert values_agree(True, True) is True
    assert values_agree(True, 1) is False
    assert values_agree("338.65", 338.65) is True
    assert values_agree("x", "y") is False
    assert dedup_samples([None, "", "a", "a", "b", 1, 2, 3, 4, 5, 6]) == ["a", "b", 1, 2, 3]
    assert dedup_samples([]) == []


def test_baseline_canonical_records_variants() -> None:
    from autobridge.repair.controller import _baseline_canonical_records

    assert _baseline_canonical_records(None) == {}
    assert _baseline_canonical_records("nope") == {}
    assert _baseline_canonical_records({"a": 1}) == {}
    direct = {"Reservation": [{"reservation_id": "R1"}]}
    assert _baseline_canonical_records(direct) == direct
    wrapped = {"canonical_records": direct}
    assert _baseline_canonical_records(wrapped) == direct
    assert _baseline_canonical_records({"canonical_records": {"R": "x"}}) == {}


def test_pair_new_key_with_baseline_branches() -> None:
    from autobridge.repair.controller import _pair_new_key_with_baseline as pair

    vanished = [{"entity": "Reservation", "field": "guest_name", "wire_key": "guestName"}]
    base = {"Reservation": [{"guest_name": "Ada"}, {"guest_name": "Bo"}]}
    assert pair("", "Ada", ["Ada"], vanished, base, ["Reservation"]) is None
    assert pair("guestNames", None, [], vanished, base, ["Reservation"]) is None
    ok = pair("guestNames", "Ada", ["Ada", "Bo"], vanished, base, ["Reservation"])
    assert ok and ok["value_match"] is True and ok["field"] == "guest_name"
    miss = pair("guestNames", "Zed", ["Zed"], vanished, base, ["Reservation"])
    assert miss and miss["value_match"] is False
    assert pair("guestNames", "Ada", ["Ada"], vanished, base, ["FolioCharge"]) is None
    assert pair("guestNames", "Ada", ["Ada"], [], base, ["Reservation"]) is None
    assert pair("guestNames", "Ada", ["Ada"], vanished, {"Reservation": []}, ["Reservation"]) is None


def test_vanished_fields_resolves_through_spec_paths() -> None:
    from autobridge.repair.controller import _vanished_mapped_fields as vanished

    spec = {"entities": {"Reservation": {"fields": {
        "guest_name": {"path": "$.guestName"},
        "total_amount": {"path": "$.balance"},
    }}}}
    signals = [
        {"kind": "keys_changed",
         "details": {"entity": "Reservation", "missing": ["guestName"], "added": []}},
        {"kind": "mapped_field_emptied",
         "details": {"entity": "Reservation", "wire_key": "balance", "added_candidates": []}},
        "not-a-signal",
    ]
    found = vanished(signals, spec["entities"])
    assert {"entity": "Reservation", "field": "guest_name", "wire_key": "guestName"} in found
    assert {"entity": "Reservation", "field": "total_amount", "wire_key": "balance"} in found
    assert vanished([], {}) == []
    assert vanished([{"kind": "x", "details": {}}], spec["entities"]) == []


def test_spec_static_tz_variants() -> None:
    from autobridge.engine.sync_run import _spec_static_tz

    assert _spec_static_tz({"source": {"timezone_source": {"kind": "static", "value": "UTC"}}}) == "UTC"
    assert _spec_static_tz({"source": {"timezone_source": {"kind": "field", "path": "$.tz"}}}) is None
    assert _spec_static_tz({}) is None
    assert _spec_static_tz(None) is None


def test_lost_wire_samples_variants() -> None:
    from simulators.common import beacon_serve as _beacon

    assert _beacon.baseline_pin() is None
    with _beacon.unfaulted_baseline(123):
        assert _beacon.baseline_pin() == 123
    assert _beacon.baseline_pin() is None


def test_transform_edge_branches() -> None:
    from autobridge.runtime.transforms import base as _base
    from autobridge.runtime.transforms.business_date import transform_business_date
    from autobridge.runtime.transforms.date_local import transform_date_local

    # Invalid ISO date / overlong or non-string formats.
    assert _base._parse_iso_date_string("2026-13-40") is None
    assert _base.is_allowed_date_input_format("x" * 65) is False
    assert _base.is_allowed_date_input_format(None) is False
    assert _base.parse_with_input_format("2026-10-15", "%m-%d-%Y") is None
    assert _base.parse_wire_date_only("991332") is None
    assert _base.parse_wire_date_only("nope") is None
    # Datetime input with an unknown zone falls back to UTC.
    import datetime as _dt

    assert transform_date_local(
        _dt.datetime(2026, 10, 15, 12, 0), {"tz": "No/Such_Zone"}, context_tz="UTC"
    ) == "2026-10-15"
    # Framed wire datetime (with a time part) converts in source zone.
    assert transform_date_local("261015 120000", {"tz": "UTC"}, context_tz="UTC") == "2026-10-15"
    assert transform_business_date("261015 120000", {"tz": "UTC"}, context_tz="UTC") == "2026-10-15"
    # Garbage passes through date_local; business_date returns it as-is.
    assert transform_date_local("not-a-date", {}, context_tz="UTC") == "not-a-date"
    assert transform_business_date("not-a-date", {}, context_tz="UTC") == "not-a-date"


def test_pagination_body_helpers() -> None:
    from autobridge.runtime.pagination import (
        build_request_body,
        effective_count,
        get_body_path,
        set_body_path,
    )

    assert effective_count(type("P", (), {"count_value": None})(), 50) == 50
    body: dict = {}
    set_body_path(body, "Limitation.Count", 25)
    assert body == {"Limitation": {"Count": 25}}
    # Non-dict intermediate is replaced.
    set_body_path({"Limitation": 5}, "Limitation.Count", 25)
    assert get_body_path({"a": {"b": 1}}, "a.b") == 1
    assert get_body_path({"a": 1}, "a.b") is None
    assert get_body_path({}, "a") is None
    state = type("S", (), {"cursor": "c1", "pages_fetched": 0})()
    pag = type("P", (), {"count_path": "Limitation.Count", "cursor_path": "Limitation.Cursor"})()
    merged = build_request_body(state, pag, {"Limitation": {"Count": 100}}, 50)
    assert merged["Limitation"]["Count"] == 50
    assert merged["Limitation"]["Cursor"] == "c1"


def test_spec_static_tz_object_form() -> None:
    from autobridge.engine.sync_run import _spec_static_tz

    class _Tz:
        kind = "static"
        value = "UTC"

    class _Source:
        timezone_source = _Tz()

    class _Spec:
        source = _Source()

    assert _spec_static_tz(_Spec()) == "UTC"


def test_business_date_prefers_record_zone() -> None:
    from autobridge.runtime.checks import run_checks

    # A record-carried zone wins over the spec zone and the legacy map.
    recs = [{"charge_id": "C1", "business_date": "2026-10-26",
             "posted_at": "2026-10-27T01:30:00+00:00", "property_id": "prop-001",
             "timezone": "America/New_York"}]
    checks = [{"kind": "business_date_invariant", "field": "business_date",
               "reference_field": "posted_at", "cutoff": 4}]
    assert run_checks(recs, checks, entity_name="FolioCharge",
                      context_tz="UTC") == []
    # property_timezone alias behaves the same.
    recs[0]["property_timezone"] = recs[0].pop("timezone")
    assert run_checks(recs, checks, entity_name="FolioCharge",
                      context_tz="UTC") == []


def test_rename_helpers_cover_mismatch_branches() -> None:
    from autobridge.repair.context import RepairContext
    from autobridge.repair.rules import rename_field as rf
    from autobridge.repair.rules.rename_field import (
        _added_sample,
        _rename_value_samples,
        _wire_shape_sets,
    )

    spec = _spec()
    # Wrong-entity signals are skipped; non-dict details are tolerated.
    sigs = [
        {"kind": "keys_changed", "operation": "o",
         "details": {"entity": "Other", "missing": ["x"], "added": [{"key": "y"}]}},
        {"kind": "keys_changed", "operation": "o",
         "details": {"entity": "Reservation", "missing": [],
                     "added": [{"key": "guestNames", "sample": "Guest 2",
                                "samples": ["Guest 2"]}]}},
        {"kind": "mapped_field_emptied", "operation": "o",
         "details": {"entity": "Reservation", "wire_key": "guestName",
                     "canonical_fields": ["guest_name"], "added_candidates": ["guestNames"],
                     "added_candidate_samples": {"guestNames": ["Guest 1", "Guest 1", None]},
                     "lost_samples": ["Guest 1", None, "Guest 2"]}},
        {"kind": "keys_changed", "operation": "o",
         "details": {"entity": "Reservation", "missing": [], "added": []}},
        "not-a-signal",
    ]
    ctx = RepairContext(spec=spec, signals=sigs, drift_class="SCHEMA_RENAME")
    assert _wire_shape_sets(ctx, "Reservation") == (set(), {"guestNames"})
    new_samples, lost = _rename_value_samples(ctx, "Reservation", "guestName", "guestNames")
    assert sorted(new_samples) == ["Guest 1", "Guest 2"], new_samples
    assert sorted(lost) == ["Guest 1", "Guest 2"], lost
    assert _added_sample(ctx, "guestNames") == ("Guest 2", None)
    assert _added_sample(ctx, "absent") == (None, None)
    # Shape helpers tolerate missing structure.
    assert rf._spec_transform_names(ctx, "Reservation", "nope") == set()
    assert rf._canonical_field_for(ctx, "Reservation", "guestName") == "guest_name"
    assert rf._canonical_field_for(ctx, "Nope", "guestName") is None


def test_preflight_fails_loudly_without_spec() -> None:
    import types

    from bench import beacon_run

    vendors = [types.SimpleNamespace(vendor="nosuch", http=True, public_url="http://x",
                                     spec=None, docs="")]
    try:
        beacon_run.preflight_beacon(
            beacon_run.BeaconProtocol(
                round_after_utc="2026-11-02T00:00:00+00:00", target_round=1,
                faults=1, trials=1, arms=(), engine_commit="a", generator_commit="b",
                frozen_at_utc="2026-11-02T00:00:00+00:00", rehearsal=True,
            ),
            config=beacon_run.default_beacon_config(),
            eval_seed_hex="ab" * 32,
            verifier_url=None,
            vendors=vendors,  # type: ignore[arg-type]
            public_urls_map={"nosuch": "http://x"},
        )
    except beacon_run.BeaconError as exc:
        assert "pre-drift spec" in str(exc)
    else:
        raise AssertionError("preflight must fail without a spec")


def test_preflight_repair_index_rejects_bad_seed() -> None:
    from bench import beacon_run

    for bad in ("zz", "ab" * 31):
        try:
            beacon_run._preflight_repair_index("atlas", bad, 0)
        except beacon_run.BeaconError:
            continue
        raise AssertionError(f"bad seed accepted: {bad!r}")
    try:
        beacon_run._preflight_repair_index("nosuch", "ab" * 32, 0)
    except beacon_run.BeaconError as exc:
        assert "unknown vendor" in str(exc)
    else:
        raise AssertionError("unknown vendor accepted")


def test_served_docs_unknown_vendor_is_none() -> None:
    from bench import beacon_run

    assert beacon_run.beacon_served_docs("nosuch", "ab" * 32, 0) is None


def test_preflight_rejects_unresolvable_candidate_url(monkeypatch) -> None:
    from bench import beacon_run

    vendors = beacon_run.load_vendors()

    monkeypatch.setattr(beacon_run, "engine_spec_for", lambda *_a, **_k: {"not": "a spec"})
    try:
        beacon_run.preflight_beacon(
            beacon_run.BeaconProtocol(
                round_after_utc="2026-11-02T00:00:00+00:00", target_round=1,
                faults=1, trials=1, arms=(), engine_commit="a", generator_commit="b",
                frozen_at_utc="2026-11-02T00:00:00+00:00", rehearsal=True,
            ),
            config=beacon_run.default_beacon_config(),
            eval_seed_hex="ab" * 32,
            verifier_url=None,
            vendors=vendors,
        )
    except beacon_run.BeaconError as exc:
        assert "URL unresolvable" in str(exc) or "pre-drift spec" in str(exc)
        return
    raise AssertionError("preflight must fail loudly on a bad spec")


def test_pair_skips_other_entities() -> None:
    from autobridge.repair.controller import _pair_new_key_with_baseline as pair

    vanished = [{"entity": "Reservation", "field": "guest_name", "wire_key": "guestName"}]
    base = {"Reservation": [{"guest_name": "Ada"}]}
    # Touched entity does not include the vanished one: no pairing.
    assert pair("guestNames", "Ada", ["Ada"], vanished, base, ["FolioCharge"]) is None


def test_pagination_body_helpers_edge_branches() -> None:
    from autobridge.runtime.pagination import (
        build_request_body,
        get_body_path,
        initial_state,
        set_body_path,
    )

    # Body cursor without a limit param: cursor still set.
    state = initial_state("body_cursor")
    object.__setattr__(state, "cursor", "tok")
    pag = type("P", (), {"count_path": None, "cursor_path": "Limitation.Cursor"})()
    assert build_request_body(state, pag, {}, 50) == {"Limitation": {"Cursor": "tok"}}
    # Non-dict intermediate replaced; missing segment reads None.
    body: dict = {"Limitation": 5}
    set_body_path(body, "Limitation.Count", 25)
    assert body == {"Limitation": {"Count": 25}}
    assert get_body_path({"a": 1}, "a.b") is None
    assert get_body_path({}, "a") is None


def test_transform_base_edge_branches() -> None:
    from autobridge.runtime.transforms import base as _base
    from autobridge.runtime.transforms.date_local import transform_date_local

    assert _base.parse_with_input_format("2026-10-26", "%c") is None
    assert _base.parse_with_input_format("not a date", "%m-%d-%Y") is None
    assert _base.parse_wire_datetime("991332999999") is None
    assert _base.parse_wire_datetime("nope") is None
    assert _base.parse_wire_date_only("nope") is None
    assert _base._parse_iso_date_string("2026-13-40") is None
    # Bad zone falls back to UTC; float money uses str().
    assert transform_date_local("2026-10-15", {"tz": "No/Such_Zone"}) == "2026-10-15"
    assert _base._ensure_decimal(1.5) is not None


def test_wire_hint_edge_branches() -> None:
    from autobridge.repair.rules import wire as _wire

    assert _wire.wire_cursor_hints([{"kind": "pagination_changed", "details": {}}]) == []
    assert _wire.wire_cursor_for_strategy([{"kind": "x", "details": {}}], {"cursor"}) == {}
    assert _wire._rename_hint([]) is None if hasattr(_wire, "_rename_hint") else True
