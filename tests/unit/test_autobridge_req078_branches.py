"""REQ-078 branch coverage: new climb/grade/detect paths.

Covers the branches the first REQ-078 test file exercises only without a
session or through fakes: session-persisted model candidates, the L2
winner patch, the dry-run-failure climb, the generic deferred job branch,
in-run warm learning, paired stats, budget flags, wire/rename details,
FIAS baseline deltas, protocol budget lines, and the code_list /
token_expired answer-key acceptances.
"""

from __future__ import annotations

import json
from typing import Any

from autobridge.llm.provider import Completion, FakeProvider, Usage


def _usage() -> Usage:
    return Usage(input_tokens=100, output_tokens=50)


def _mem_factory() -> Any:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from autobridge.control.models import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _patch_spec() -> dict[str, Any]:
    return {
        "spec_version": 3,
        "connector": {"id": "t", "vendor": "test"},
        "source": {
            "base_url": "http://example.com",
            "auth": {"type": "bearer", "credential_ref": "TEST_TOKEN"},
            "timezone_source": {"kind": "static", "value": "UTC"},
        },
        "operations": {
            "list_reservations": {
                "method": "GET",
                "path": "/reservations",
                "pagination": {"strategy": "page", "page_param": "page", "items_path": "$.data", "stop_when": ["empty_page"],
                               # All strategy params present so a strategy-only
                               # L2 patch stays schema-valid (cursor and
                               # offset both validate from this base).
                               "cursor_param": "cursor", "next_path": "$.next", "offset_param": "offset"},
            }
        },
        "entities": {
            "Reservation": {
                "from": "list_reservations",
                "key": "$.id",
                "fields": {
                    "guest_name": {"path": "$.guestName"},
                    "status": {"path": "$.status"},
                },
                "checks": [],
            }
        },
        "capabilities": {"read": ["Reservation"]},
        "health": {"canary": {"every": "5m", "operation": "list_reservations"}},
    }


def test_model_candidate_persists_repair_row_with_session() -> None:
    """A verified model patch with a session persists the propose row
    (candidate_patch threaded, never re-fetched)."""
    from autobridge.repair.controller import RepairController

    patch = [{"op": "replace", "path": "/entities/Reservation/fields/guest_name/path", "value": "$.fullName"}]
    provider = FakeProvider(
        responses={"frontier": Completion(text=json.dumps({"patch": patch}), usage=_usage())}
    )
    factory = _mem_factory()
    with factory() as s:
        ctrl = RepairController()
        result = ctrl.handle(
            spec=_patch_spec(), drift_class="SCHEMA_RENAME", signals=[],
            fingerprint_delta=None, hypotheses=[], enable_rules=False,
            provider=provider, budget=None, validate_against=_patch_spec(), session=s,
        )
        s.commit()
        assert result.decision == "propose"
        assert result.repair_run_id is not None
        from autobridge.control.models import RepairRun

        row = s.get(RepairRun, result.repair_run_id)
        assert row is not None and row.decision == "propose"
        assert list(row.candidate_patch or []) == patch


def test_l2_confident_winner_becomes_patch() -> None:
    """A confident L2 winner is turned into a patch through the same
    pointer checks (never synthesised beyond the fragment)."""
    from autobridge.discovery.hypotheses import Hypothesis, Provenance
    from autobridge.repair.controller import RepairController

    prov = Provenance(source="test", line=1, snippet="t")
    def _mk(hid: str, frag: dict) -> Hypothesis:
        return Hypothesis(
            id=hid, family=str(frag.get("family", "date")), claim="c",
            candidate_mapping_fragment=dict(frag),
            prior=0.2, evidence_for=(prov,), field=frag.get("field"), strategy=frag.get("strategy"),
        )
    # h0 yields no patch (skipped), h1 yields a patch outside the allowed
    # pointers (skipped), h2 yields the adopted patch.
    empty = _mk("h0", {"family": "status", "field": "status"})
    dated = _mk("h1", {"family": "date", "field": "arrival_date"})
    paged = _mk("h2", {"family": "pagination", "field": "/reservations", "strategy": "offset"})
    small_text = json.dumps({"ranking": ["h0", "h1", "h2"], "confidence": 0.95, "rationale": "fits"})
    provider = FakeProvider(
        responses={"small": Completion(text=small_text, usage=_usage())}
    )
    factory = _mem_factory()
    with factory() as s:
        ctrl = RepairController()
        result = ctrl.handle(
            spec=_patch_spec(), drift_class="PAGINATION_CHANGE", signals=[],
            fingerprint_delta=None, hypotheses=[empty, dated, paged], enable_rules=False,
            provider=provider, budget=None, validate_against=_patch_spec(), session=s,
            evidence={"patch_target": {"allowed_pointers": ["/operations/list_reservations/pagination/strategy"]}},
        )
        s.commit()
    assert result.decision == "propose"
    assert result.candidate_patch == [
        {"op": "replace", "path": "/operations/list_reservations/pagination/strategy", "value": "offset"}
    ]
    assert result.ladder_level_reached == 2
    assert result.repair_run_id is not None


def test_dry_run_failure_climbs_with_failure_as_evidence() -> None:
    """An L1 rule patch that fails the dry-run climbs to the model with
    the failure as evidence instead of stopping at a question."""
    from autobridge.engine.detect_repair import detect_after_sync

    factory = _mem_factory()
    import tests.unit.test_autobridge_detect_repair as tmod

    spec = tmod._page_spec()
    conn_id, rel_id = tmod._seed_connector(factory, spec)
    with factory() as s:
        out = detect_after_sync(
            s, connector_id=conn_id, release_id=rel_id, spec_dict=spec,
            summary=tmod._cursor_summary(), sync_run_id="sync-dryfail",
        )
        s.commit()
    assert out["drift_found"] is True
    assert out["decision"] in ("propose", "escalate")


def test_generic_deferred_job_climbs_with_evidence() -> None:
    """The deferred job path serves every class: a non-semantic parcel
    carrying class-neutral evidence climbs with the provider."""
    from autobridge.control.models import Connector, ConnectorRelease, DriftEvent
    from autobridge.engine.releases import spec_signature
    from autobridge.repair.controller import RepairController

    factory = _mem_factory()
    spec = _patch_spec()
    with factory() as s:
        conn = Connector(name="c", base_url="http://x", credential_ref="K",
                         target_entities=["Reservation"], runtime_target={"kind": "local_process"})
        s.add(conn)
        s.flush()
        rel = ConnectorRelease(connector_id=conn.id, version=1, spec=spec, signature=spec_signature(spec))
        s.add(rel)
        s.flush()
        conn.active_release_id = rel.id
        drift = DriftEvent(connector_id=conn.id, drift_class="PAGINATION_CHANGE",
                           severity="high", source_sync_run_id=None, status="open")
        s.add(drift)
        s.flush()
        drift_id, release_id = drift.id, rel.id
        s.commit()
    from autobridge.llm.provider import FakeProvider as _FP

    provider = _FP(responses={"frontier": Completion(
        text=json.dumps({"escalate": "Which paging is right?", "rationale": "ambiguous"}),
        usage=_usage())})
    import autobridge.llm.provider as _pm

    orig = _pm.resolve_frontier_provider
    _pm.resolve_frontier_provider = lambda: (provider, "replay")
    try:
        from autobridge.repair.controller import _run_repair_climb

        class _Ctx:
            def __init__(self, session: Any) -> None:
                self.session = session
                self.payload: dict[str, Any] = {}
                self.run_id: str | None = None
                self.job_id = "job-1"

        with factory() as s:
            ctx = _Ctx(s)
            ctx.payload = {
                "drift_class": "UNKNOWN",
                "release_id": release_id,
                "l3_evidence": {
                    "patch_target": {"allowed_pointers": []},
                    "observed": {"drift_class": "UNKNOWN", "signals": ["something odd"]},
                },
                "dry_run_fault": None,
            }
            out = _run_repair_climb(
                ctx, controller=RepairController(), spec=dict(spec),
                drift_class="UNKNOWN", vendor="test",
                drift_ids=[drift_id], payload=ctx.payload,
                running_row_id=None, on_rung=None,
            )
        assert out["decision"] == "escalate"
        assert provider.calls, "the deferred non-semantic climb must reach the model"
    finally:
        _pm.resolve_frontier_provider = orig


def test_warm_learns_verified_fix_and_reports_reuse() -> None:
    """In-run warm learning: a verified fix is learned under the
    production key, and a later L0 hit on the same drift reports the
    source instance."""
    import bench.beacon_run as br
    from autobridge.repair.knowledge import KnowledgeCache, cache_key

    cache: Any = KnowledgeCache()
    patch = [{"op": "replace", "path": "/entities/Reservation/fields/guest_name/path", "value": "$.fullName"}]
    delta = {"fingerprint_changed": True, "keys_changed": True, "changed_entities": ["Reservation"]}
    spec = {
        "candidate_patch": patch, "decision": "propose",
        "ladder_level_reached": 0, "rule_id": "rename_field",
        "learn_inputs": {"vendor": "atlas", "drift_class": "SCHEMA_RENAME", "delta": delta},
    }
    grade = br.BeaconArmGrade(arm="autobridge_warm", instance_index=7, trial=0,
                              vendor="atlas", family="rename", verified_completion=1)
    br._beacon_warm_learn(cache, 7, type("R", (), {"spec": spec})(), grade)
    key = cache_key("atlas", "SCHEMA_RENAME", delta)
    assert key in getattr(cache, "beacon_learned_sources", {})
    grade2 = br.BeaconArmGrade(arm="autobridge_warm", instance_index=9, trial=0,
                               vendor="atlas", family="rename", verified_completion=1)
    br._beacon_warm_learn(cache, 9, type("R", (), {"spec": spec})(), grade2)
    assert grade2.warm_used_learned_from == 7
    # Cold never learns: no cache, no crash, no report.
    br._beacon_warm_learn(None, 9, type("R", (), {"spec": spec})(), grade2)


def test_summarize_per_instance_paired_and_family() -> None:
    """Scores average per instance first; paired diffs and per-family
    rows are present."""
    import bench.beacon_run as br

    grades: list[Any] = []
    for trial in range(3):
        grades.append(br.BeaconArmGrade(arm="autobridge_cold", instance_index=0, trial=trial,
                                        vendor="a", family="rename", verified_completion=1))
        grades.append(br.BeaconArmGrade(arm="autobridge_warm", instance_index=0, trial=trial,
                                        vendor="a", family="rename", verified_completion=1))
        grades.append(br.BeaconArmGrade(arm="autobridge_cold", instance_index=1, trial=trial,
                                        vendor="a", family="rename", verified_completion=0))
        grades.append(br.BeaconArmGrade(arm="autobridge_warm", instance_index=1, trial=trial,
                                        vendor="a", family="rename", verified_completion=1))
    summary = br.summarize_grades(grades)
    cold = summary["by_arm"]["autobridge_cold"]["verified_completion"]
    assert cold["n_instances"] == 2 and abs(cold["mean"] - 0.5) < 1e-9
    assert 0.0 <= cold["ci95_lo"] <= cold["ci95_hi"] <= 1.0
    assert "rename" in summary["by_family"]
    assert "autobridge_warm_minus_autobridge_cold" in summary["paired_vs_cold"]
    assert summary["paired_vs_cold"]["autobridge_warm_minus_autobridge_cold"]["mean"] > 0


def test_ladder_budget_flag_and_outcome_map() -> None:
    """Budget-exhausted climbs and over-ceiling agents map to Error."""
    import bench.beacon_run as br
    from bench import ablation as ab

    assert ab._ladder_hit_budget(type("R", (), {"ladder_trace": [{"budget_type": "token"}]})()) is True
    assert ab._ladder_hit_budget(type("R", (), {"ladder_trace": []})()) is False
    from types import SimpleNamespace as _NS

    rep = br._arm_outcome_for(_NS(ran=True, spec={"decision": "escalate", "budget_exhausted": True}), False)
    assert rep["outcome"] == "Error"
    assert br._arm_failed(_NS(ran=True, spec={"decision": "escalate", "budget_exhausted": True}), {"decision": "escalate", "budget_exhausted": True}) is True


def test_wire_hints_for_rename_details_and_emptied() -> None:
    """The FIAS rename pairing and the emptied added-candidate pair by value."""
    from autobridge.repair.rules.wire import wire_rename_hint

    sig_rename = type("S", (), {"kind": "keys_changed", "operation": "feed_ps",
                                "details": {"entity": "FolioCharge", "missing": ["TA"], "added": [],
                                            "rename": {"old_field": "TA", "new_field": "TM"}}})()
    assert wire_rename_hint([sig_rename]) == ("FolioCharge", "TA", "TM")
    sig_empty = type("S", (), {"kind": "mapped_field_emptied", "operation": "op",
                               "details": {"entity": "Reservation", "wire_key": "guestName",
                                           "added_candidates": ["fullName"]}})()
    assert wire_rename_hint([sig_empty]) == ("Reservation", "guestName", "fullName")
    sig_multi = type("S", (), {"kind": "mapped_field_emptied", "operation": "op",
                               "details": {"entity": "R", "wire_key": "a",
                                           "added_candidates": ["b", "c"]}})()
    assert wire_rename_hint([sig_multi]) is None


def test_rename_refuses_nested_object() -> None:
    """An added object holding the missing key is a path move: the rename
    rule refuses so it never proposes guest_name → $.localInfo."""
    from autobridge.repair.context import RepairContext
    from autobridge.repair.rules import rename_field

    spec = _patch_spec()
    sig = type("S", (), {"kind": "keys_changed", "operation": "op",
                         "details": {"entity": "Reservation", "missing": ["guestName"],
                                     "added": [{"key": "localInfo", "sample": {"guestName": "Ann"}}]}})()
    ctx = RepairContext(spec=spec, drift_class="SCHEMA_RENAME", signals=[sig],
                        fingerprint_delta=None, classification=None, extra={})
    info = rename_field._rename_info(ctx)
    assert info is not None
    assert rename_field.precondition(ctx) is False


def test_fias_baseline_delta_is_one_to_one() -> None:
    """The feed delta compares baseline-announced to current-announced ids."""
    import tests.unit.test_autobridge_drift_event_feeds as fmod

    report = fmod.detect_feed_field_rename(
        fmod._fias_like_spec(), "PS", ["P#", "RN", "TI", "DA", "SO", "TM", "DU", "DD", "CX"],
        fmod._v3_samples(), baseline_announced=fmod.BASELINE_PS,
    )
    assert report["drift_found"] is True
    prev = report["delta"].types_details["FolioCharge"]["previous"]
    assert set(prev) == set(fmod.BASELINE_PS)


def test_protocol_budget_lines_round_trip() -> None:
    """Frozen budgets parse (old protocols without them still parse)."""
    import bench.beacon_run as br

    proto = br.BeaconProtocol(
        round_after_utc="2030-01-01T00:00:00+00:00", target_round=1, faults=1,
        trials=1, arms=("autobridge_cold",), engine_commit="a",
        generator_commit="b", frozen_at_utc="2030-01-01T00:00:00+00:00",
        token_ceiling=200000, wall_clock_s=600.0,
    )
    assert proto.token_ceiling == 200000
    assert proto.wall_clock_s == 600.0


def test_code_list_repair_accepts_escalation_and_expired_token() -> None:
    """Owner acceptances: code_list repair + question passes; token_expired
    keys escalate."""
    from simulators.common import faultgen as fg
    from verifier.beacon import accepted_outcomes_for, grade_arm_outcome

    assert fg.expected_for("transport_auth", {"kind": "token_expired"}) == "no_patch"
    assert accepted_outcomes_for({"expected": "no_patch", "family": "transport_auth", "params": {"kind": "token_expired"}}) == {"no_patch", "escalate", "degraded_question"}
    assert "escalate" in accepted_outcomes_for({"expected": "repair", "family": "code_list"})
    assert "degraded_question" in accepted_outcomes_for({"expected": "repair", "family": "code_list"})
    arm = {"outcome": "Blocked", "patch_reached_release": False, "operator_question": True}
    passed, _ = grade_arm_outcome({"expected": "repair", "family": "code_list"}, arm)
    assert passed is True


def test_l0_hit_with_provider_returns_deterministic_propose() -> None:
    """L0 knew the drift: the model climb falls back to the deterministic
    path (0 model calls) instead of calling the provider."""
    from autobridge.drift.fingerprint import FingerprintDelta
    from autobridge.repair.controller import RepairController
    from autobridge.repair.knowledge import KnowledgeCache

    delta = FingerprintDelta(
        fingerprint_changed=True, pagination_changed=True,
        keys_changed=False, types_changed=False, changed_entities=[],
        pagination_details={"list_reservations": {
            "previous": {"strategy": "page"}, "current": {"strategy": "cursor"}}},
        keys_details={}, types_details={},
    )
    cache: Any = KnowledgeCache()
    patch = [
        {"op": "replace", "path": "/operations/list_reservations/pagination/strategy", "value": "cursor"},
        {"op": "add", "path": "/operations/list_reservations/pagination/cursor_param", "value": "cursor"},
        {"op": "add", "path": "/operations/list_reservations/pagination/next_path", "value": "$.next"},
    ]
    cache.put("test", delta, patch, "page_to_cursor", drift_class="PAGINATION_CHANGE")
    provider = FakeProvider(responses={"frontier": Completion(text="{}", usage=_usage())})
    ctrl = RepairController(knowledge_cache=cache)
    result = ctrl.handle(
        spec=_patch_spec(), drift_class="PAGINATION_CHANGE", signals=[],
        fingerprint_delta=delta, hypotheses=[], provider=provider, budget=None,
        validate_against=_patch_spec(),
    )
    assert result.decision == "propose"
    assert result.candidate_patch == patch
    assert provider.calls == [], "L0 hit must never reach the model"


def test_emptied_constant_default_counts_with_baseline() -> None:
    """A mapped field turned into a constant '0' while the baseline
    carried values counts as emptied (with populated siblings alive)."""
    from autobridge.drift.signals import signals_from_sync

    def _wire(guest: Any) -> dict[str, Any]:
        return {"guestName": guest, "status": "ok", "id": "R-1"}

    spec = {
        "operations": {"list_reservations": {"method": "GET", "path": "/reservations"}},
        "entities": {
            "Reservation": {
                "from": "list_reservations",
                "key": "$.id",
                "fields": {"guest_name": {"path": "$.guestName"}, "status": {"path": "$.status"}},
            }
        }
    }
    current = [_wire("0") for _ in range(10)]
    baseline = [_wire(f"Name-{i}") for i in range(10)]

    def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "outcome": "Complete", "unmet_criteria": [], "data_readiness": {},
            "fingerprint_id": "x",
            "operation_results": {"list_reservations": {"records": rows, "raw_pages": [{"data": rows}]}},
            "records_observed": {"Reservation": len(rows)}, "canonical_records": {},
            "blocked_records": {}, "invariant_results": [], "checkpoint_values": {},
        }

    sigs = signals_from_sync(
        _summary(current), spec=spec,
        baseline_summary={"operation_results": {"list_reservations": {"records": baseline, "raw_pages": [{"data": baseline}]}}},
    )
    kinds = [s.kind for s in sigs]
    assert "mapped_field_emptied" in kinds
    emptied = next(s for s in sigs if s.kind == "mapped_field_emptied")
    assert emptied.details.get("wire_key") == "guestName"
    assert "constant default" in emptied.message


def test_http_transient_unmet_criteria_map_to_transient() -> None:
    """http_error / transient_error unmet criteria are TRANSIENT-family
    signals, never a generic invariant failure."""
    from autobridge.drift.classifier import classify
    from autobridge.drift.signals import signals_from_sync

    def _summary(crit: str) -> dict[str, Any]:
        return {
            "outcome": "Blocked", "unmet_criteria": [crit], "data_readiness": {},
            "fingerprint_id": "x", "operation_results": {}, "records_observed": {},
            "canonical_records": {}, "blocked_records": {}, "invariant_results": [],
            "checkpoint_values": {},
        }

    sigs = signals_from_sync(_summary("http_error"))
    assert [s.kind for s in sigs if s.kind in ("http_error", "transient_error", "invariant_failure")] == ["http_error"]
    sigs2 = signals_from_sync(_summary("transient_error:timeout"))
    assert [s.kind for s in sigs2 if s.kind in ("http_error", "transient_error", "invariant_failure")] == ["transient_error"]
    assert classify(sigs2).drift_class == "TRANSIENT"
    """Constant date offsets and non-ISO values raise date_shift."""
    from autobridge.drift.signals import signals_from_sync

    def _rows(dates: list[str]) -> dict[str, list[dict[str, Any]]]:
        return {"Reservation": [
            {"reservation_id": f"R-{i}", "arrival_date": d} for i, d in enumerate(dates)
        ]}

    spec = {
        "entities": {
            "Reservation": {
                "key": "$.reservation_id",
                "fields": {"arrival_date": {"path": "$.arrivalDate", "transform": [{"name": "date_local"}]}},
            }
        }
    }
    base = _rows(["2026-10-26"] * 6)
    shifted = _rows(["2026-10-27"] * 6)
    sigs = signals_from_sync(
        {"outcome": "Complete", "unmet_criteria": [], "data_readiness": {},
         "fingerprint_id": "x", "operation_results": {}, "records_observed": {},
         "canonical_records": shifted, "blocked_records": {}, "invariant_results": [],
         "checkpoint_values": {}},
        spec=spec, baseline_summary={"canonical_records": base},
    )
    assert any(s.kind == "date_shift" for s in sigs)
    bad = _rows(["10-27-2026"] * 6)
    sigs2 = signals_from_sync(
        {"outcome": "Complete", "unmet_criteria": [], "data_readiness": {},
         "fingerprint_id": "x", "operation_results": {}, "records_observed": {},
         "canonical_records": bad, "blocked_records": {}, "invariant_results": [],
         "checkpoint_values": {}},
        spec=spec,
    )
    assert any(s.kind == "date_shift" for s in sigs2)


def test_model_escalation_without_question_uses_fallback() -> None:
    """An explicit escalation with an empty question resets the model flag
    and falls back to the class-neutral engine question (never blank)."""
    from autobridge.repair.controller import RepairController

    provider = FakeProvider(
        responses={"frontier": Completion(
            text=json.dumps({"escalate": "", "rationale": "ambiguous", "proposal": {
                "label": "", "effect": "",
                "patch": [{"op": "replace", "path": "/entities/Reservation/fields/status/path", "value": "$.s"}],
            }}),
            usage=_usage(),
        )}
    )
    ctrl = RepairController()
    result = ctrl.handle(
        spec=_patch_spec(), drift_class="SCHEMA_RENAME", signals=[],
        fingerprint_delta=None, hypotheses=[], enable_rules=False,
        provider=provider, budget=None, validate_against=_patch_spec(),
        evidence={"patch_target": {"allowed_pointers": ["/entities/Reservation/fields/status/path"]}},
    )
    assert result.decision == "escalate"
    assert result.candidate_patch is None
    bundle = result.escalation_bundle
    assert bundle is not None and bundle.question and bundle.question.endswith("?")
    assert "semantic" not in bundle.question
