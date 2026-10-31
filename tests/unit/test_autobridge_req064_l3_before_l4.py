"""REQ-064 B2: the ladder tries L3 before escalating when no rule fits.

When a drift class is detected and no L0/L1 rule produces a patch (L2
included where it runs today), the provider-wired climb calls L3 with
the same repair context and prompt shape as today
(``config/prompts/l3_patch.md``: patch or escalate with one question),
within the existing budget/ceiling, before escalating. The verifier gate
is unchanged: an L3 patch reaches a release only if it passes; otherwise
the run escalates with the question. Every test uses FakeProvider (no
network, no key).
"""

from __future__ import annotations

import json
from typing import Any

from autobridge.drift.signals import DriftSignal
from autobridge.llm.provider import Budget, Completion, FakeProvider, Usage
from autobridge.repair import semantic as sem
from autobridge.repair.controller import RepairController


def _folio_spec() -> dict[str, Any]:
    """Atlas reference spec (valid: the L3 apply-check validates)."""
    import pathlib

    import yaml

    return yaml.safe_load(
        pathlib.Path("generated/connectors/atlaspms/reference/spec.yaml").read_text(
            encoding="utf-8"
        )
    )


def _summary() -> dict[str, Any]:
    rows = [
        {"transactionID": f"CHG-{i:04d}", "businessDate": "", "amount": "10.00"}
        for i in range(12)
    ]
    return {
        "outcome": "Complete",
        "unmet_criteria": [],
        "operation_results": {
            "list_folios": {
                "records": rows,
                "raw_pages": [{"data": rows[:2]}],
                "pages_fetched": 1,
            }
        },
        "records_observed": {"FolioCharge": 12},
        "canonical_records": {
            "FolioCharge": [
                {"charge_id": f"CHG-{i:04d}", "business_date": "", "amount": "10.00"}
                for i in range(12)
            ]
        },
        "blocked_records": {},
        "invariant_results": [],
        "checkpoint_values": {},
    }


def _emptied_signal() -> DriftSignal:
    return DriftSignal(
        kind="mapped_field_emptied",
        operation="list_folios",
        entity="FolioCharge",
        severity="medium",
        message="Mapped field businessDate went dark for FolioCharge",
        details={
            "entity": "FolioCharge",
            "wire_key": "businessDate",
            "canonical_fields": ["business_date"],
            "empty_count": 12,
            "total_count": 12,
        },
    )


def _evidence() -> dict[str, Any]:
    return sem.build_detected_l3_evidence(_folio_spec(), _summary(), [_emptied_signal()])


def _patch_completion() -> Completion:
    return Completion(
        text=json.dumps(
            {
                "patch": [
                    {
                        "op": "replace",
                        "path": "/entities/FolioCharge/fields/business_date/path",
                        "value": "$.serviceDate",
                    }
                ],
                "rationale": "the vendor renamed the folio date wire field",
            }
        ),
        usage=Usage(input_tokens=100, output_tokens=50),
    )


def _escalate_completion() -> Completion:
    return Completion(
        text=json.dumps(
            {
                "escalate": "Should business_date track the new service date field?",
                "rationale": "the old wire field went dark with no stated replacement",
            }
        ),
        usage=Usage(input_tokens=100, output_tokens=50),
    )


def _handle(provider: FakeProvider, **over: Any) -> Any:
    ctrl = RepairController()
    params: dict[str, Any] = {
        "spec": _folio_spec(),
        "drift_class": "SCHEMA_RENAME",
        "signals": [_emptied_signal()],
        "fingerprint_delta": None,
        "session": None,
        "hypotheses": [],
        "enumerator_yield_count": 0,
        "provider": provider,
        "budget": Budget(),
        "evidence": _evidence(),
        "validate_against": _folio_spec(),
    }
    params.update(over)
    return ctrl.handle(**params)


def test_l3_called_for_detected_class_with_no_rule() -> None:
    """No L0/L1 rule fits the emptied field (no missing+added pair), so the
    climb reaches L3: the fake is called and its tokens land on the row."""
    provider = FakeProvider(responses={"frontier": _patch_completion()})
    result = _handle(provider)
    assert provider.calls, "L3 must be attempted before escalating"
    assert result.decision == "propose"
    assert result.rule_id == "l3_frontier"
    assert result.ladder_level_reached == 3
    assert result.model_usage.get("frontier_tokens", 0) > 0
    assert result.candidate_patch == [
        {
            "op": "replace",
            "path": "/entities/FolioCharge/fields/business_date/path",
            "value": "$.serviceDate",
        }
    ]


def test_l3_proposal_becomes_candidate_only_for_structural_classes() -> None:
    """A valid L3 proposal for SCHEMA_RENAME is a candidate (verifier gate
    and approval decide); for SEMANTIC_VALUE_DRIFT the same proposal is
    never released — the run escalates with the question instead."""
    provider = FakeProvider(responses={"frontier": _patch_completion()})
    structural = _handle(provider, drift_class="SCHEMA_RENAME")
    assert structural.decision == "propose" and structural.candidate_patch

    from autobridge.drift.signals import DriftSignal as _S

    semantic = _handle(
        FakeProvider(responses={"frontier": _patch_completion()}),
        drift_class="SEMANTIC_VALUE_DRIFT",
        signals=[
            _S(
                kind="money_scale_shift",
                entity="FolioCharge",
                details={"entity": "FolioCharge", "money_field": "amount", "factor": "100"},
            )
        ],
    )
    assert semantic.decision == "escalate"
    assert not semantic.candidate_patch
    rung3 = next(e for e in semantic.ladder_trace if e.get("level") == 3)
    assert rung3.get("proposal"), "the tested proposal rides the rung, never released"

    unknown = _handle(
        FakeProvider(responses={"frontier": _patch_completion()}),
        drift_class="UNKNOWN",
    )
    assert unknown.decision == "escalate" and not unknown.candidate_patch


def test_l3_escalation_carries_model_question() -> None:
    """The model asks instead of patching: the run escalates with the
    model's question (no invented text) and no candidate."""
    provider = FakeProvider(responses={"frontier": _escalate_completion()})
    result = _handle(provider)
    assert result.decision == "escalate"
    assert not result.candidate_patch
    assert result.escalation_bundle is not None
    assert (
        result.escalation_bundle.question
        == "Should business_date track the new service date field?"
    )


def test_l3_not_called_when_no_provider() -> None:
    """Without a provider the ladder keeps its deterministic behaviour:
    escalate at L4 with 0 model calls (unkeyed bench and tests)."""
    ctrl = RepairController()
    result = ctrl.handle(
        spec=_folio_spec(),
        drift_class="SCHEMA_RENAME",
        signals=[_emptied_signal()],
        fingerprint_delta=None,
        session=None,
        hypotheses=None,
        enumerator_yield_count=None,
        provider=None,
        budget=Budget(),
    )
    assert result.decision == "escalate"
    assert result.model_usage.get("frontier_tokens", 0) == 0
    assert result.model_usage.get("calls", 0) == 0


def test_l3_not_called_when_l1_fixes_it() -> None:
    """An L1 rule hit never reaches L3: the engine arm for a rule-fixed
    scenario proposes at L1 and the provider is never called."""
    from bench.ablation import ablation_case_for_scenario, run_engine_arm
    from bench.harness import HarnessConfig, make_trial_inputs

    inputs = make_trial_inputs(HarnessConfig(), "page_to_cursor", 0)
    case = ablation_case_for_scenario("page_to_cursor")
    provider = FakeProvider()
    res = run_engine_arm(inputs, case, ablation="autobridge_cold", provider=provider)
    assert res.spec is not None and res.spec["decision"] == "propose"
    assert res.spec["rule_id"] == "page_to_cursor"
    assert provider.calls == [], "L1 fixed it — L3 must not run"


def test_engine_arm_attempts_l3_for_detected_no_rule_class() -> None:
    """B2 at arm level: status_vocab_change detects ENUM_NEW_VALUE, no rule
    fits, so the arm attempts L3 (fake provider) and escalates with the
    model's question — recorded on the row with its tokens."""
    from bench.ablation import ablation_case_for_scenario, run_engine_arm
    from bench.harness import HarnessConfig, make_trial_inputs

    inputs = make_trial_inputs(HarnessConfig(), "status_vocab_change", 0)
    case = ablation_case_for_scenario("status_vocab_change")
    provider = FakeProvider(responses={"frontier": _escalate_completion()})
    res = run_engine_arm(inputs, case, ablation="autobridge_cold", provider=provider)
    assert provider.calls, "L3 must be attempted for a detected class with no rule"
    assert res.spec is not None and res.spec["decision"] == "escalate"
    assert res.frontier_tokens > 0


def _mem_factory() -> Any:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from autobridge.control.models import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_atlas(factory: Any, spec: dict[str, Any]) -> tuple[str, str]:
    from autobridge.control.models import Connector, ConnectorRelease
    from autobridge.engine.releases import spec_signature

    with factory() as s:
        conn = Connector(
            name="atlas",
            base_url="http://localhost:8105",
            credential_ref="ATLAS_API_KEY",
            target_entities=["Reservation"],
            runtime_target={"kind": "local_process"},
        )
        s.add(conn)
        s.flush()
        rel = ConnectorRelease(
            connector_id=conn.id, version=1, spec=spec, signature=spec_signature(spec)
        )
        s.add(rel)
        s.flush()
        conn.active_release_id = rel.id
        s.commit()
        return conn.id, rel.id


def _enum_blocked_summary() -> dict[str, Any]:
    """Blocked sync on an unknown enum value (ENUM_NEW_VALUE, no rule fits
    without evidence): the ladder escalates with no patch."""
    return {
        "outcome": "Blocked",
        "unmet_criteria": ["unknown_enum:status"],
        "data_readiness": {"freshness": "stale", "completeness": "incomplete"},
        "fingerprint_id": "x",
        "operation_results": {
            "list_reservations": {
                "records": [{"reservationID": "R-1", "status": "due_in"}],
                "raw_pages": [{"data": [{"reservationID": "R-1", "status": "due_in"}]}],
                "pages_fetched": 1,
            }
        },
        "records_observed": {"Reservation": 1},
        "canonical_records": {"Reservation": []},
        "blocked_records": {
            "Reservation": [{"field": "status", "value": "due_in"}]
        },
        "invariant_results": [{"check": "unknown_enum:status", "status": "failed"}],
        "checkpoint_values": {},
    }


def test_live_defers_detected_no_rule_drift_to_background_job(
    monkeypatch: Any,
) -> None:
    """REQ-064 B2 live path: ENUM_NEW_VALUE with no fitting rule + live
    provider wanted → repair_pending (no RepairRun yet, REQ-042 holds);
    the background job climbs with the provider."""

    from autobridge.control.models import DriftEvent, Job, RepairRun
    from autobridge.engine.detect_repair import detect_after_sync

    monkeypatch.setenv("FRONTIER_MODE", "live")
    monkeypatch.setenv("FRONTIER_API_KEY", "test-key-never-called")
    factory = _mem_factory()
    import pathlib

    import yaml

    spec = yaml.safe_load(
        pathlib.Path("generated/connectors/atlaspms/reference/spec.yaml").read_text(
            encoding="utf-8"
        )
    )
    conn_id, rel_id = _seed_atlas(factory, spec)
    with factory() as s:
        out = detect_after_sync(
            s,
            connector_id=conn_id,
            release_id=rel_id,
            spec_dict=spec,
            summary=_enum_blocked_summary(),
            sync_run_id="sync-enum",
        )
        s.commit()
    assert out["drift_found"] is True, out
    assert out.get("repair_pending") is True, out
    assert out["drift_class"] == "ENUM_NEW_VALUE", out
    assert out.get("repair_job_id"), out
    with factory() as s:
        assert s.query(DriftEvent).filter(DriftEvent.status == "open").count() == 1
        assert s.query(RepairRun).count() == 0, "deferred: no repair row until the job climbs"
        job = s.get(Job, out["repair_job_id"])
        assert job is not None and job.state == "queued"
        assert job.payload.get("drift_class") == "ENUM_NEW_VALUE"
        assert job.payload.get("hypotheses_json") == []


def test_offline_detected_no_rule_drift_escalates_inline(
    monkeypatch: Any,
) -> None:
    """Without a live provider the same drift escalates inline (0 model
    calls) — unkeyed behaviour is unchanged."""
    from autobridge.control.models import Job, RepairRun
    from autobridge.engine.detect_repair import detect_after_sync

    monkeypatch.setenv("FRONTIER_MODE", "off")
    monkeypatch.delenv("FRONTIER_API_KEY", raising=False)
    factory = _mem_factory()
    import pathlib

    import yaml

    spec = yaml.safe_load(
        pathlib.Path("generated/connectors/atlaspms/reference/spec.yaml").read_text(
            encoding="utf-8"
        )
    )
    conn_id, rel_id = _seed_atlas(factory, spec)
    with factory() as s:
        out = detect_after_sync(
            s,
            connector_id=conn_id,
            release_id=rel_id,
            spec_dict=spec,
            summary=_enum_blocked_summary(),
            sync_run_id="sync-enum",
        )
        s.commit()
    assert out["drift_found"] is True, out
    assert not out.get("repair_pending"), out
    assert out["decision"] == "escalate", out
    with factory() as s:
        assert s.query(RepairRun).count() == 1
        assert s.query(Job).count() == 0


def test_l3_proposal_persists_repair_row_with_session() -> None:
    """A proposed L3 patch persists its RepairRun row (rung 3 + usage)."""
    factory = _mem_factory()
    provider = FakeProvider(responses={"frontier": _patch_completion()})
    with factory() as s:
        from autobridge.control.models import RepairRun

        result = _handle(provider, session=s)
        s.commit()
        assert result.decision == "propose"
        assert result.repair_run_id is not None
        row = s.get(RepairRun, result.repair_run_id)
        assert row is not None and row.decision == "propose"
        assert row.ladder_level_reached == 3
        assert (row.model_usage or {}).get("frontier_tokens", 0) > 0
        levels = [t.get("level") for t in (row.ladder_trace or [])]
        assert 3 in levels


def test_deferred_detected_job_climbs_without_live_provider(monkeypatch: Any) -> None:
    """Job-side detected climb with no provider: parcelled signals rebuild,
    the deterministic ladder runs, and the row records the attempt."""
    import types

    from autobridge.control.models import Connector, ConnectorRelease, DriftEvent, RepairRun
    from autobridge.drift.signals import signal_to_json
    from autobridge.engine.releases import spec_signature
    from autobridge.repair.controller import _run_repair_climb

    monkeypatch.setenv("FRONTIER_MODE", "off")
    monkeypatch.delenv("FRONTIER_API_KEY", raising=False)
    factory = _mem_factory()
    spec = _folio_spec()
    with factory() as s:
        conn = Connector(
            name="atlas", base_url="http://localhost:8105",
            credential_ref="ATLAS_API_KEY", target_entities=["FolioCharge"],
            runtime_target={"kind": "local_process"},
        )
        s.add(conn)
        s.flush()
        rel = ConnectorRelease(
            connector_id=conn.id, version=1, spec=spec, signature=spec_signature(spec)
        )
        s.add(rel)
        s.flush()
        conn.active_release_id = rel.id
        drift = DriftEvent(
            connector_id=conn.id, drift_class="SCHEMA_RENAME",
            source_sync_run_id="sync-x", status="open",
        )
        s.add(drift)
        s.commit()
        drift_id, release_id = drift.id, rel.id
    with factory() as s:
        from autobridge.repair.controller import RepairController

        payload = {
            "drift_class": "SCHEMA_RENAME",
            "connector_id": None,
            "release_id": release_id,
            "signals_json": [signal_to_json(_emptied_signal())],
            "fingerprint_delta": None,
            "hypotheses_json": [],
            "hypothesis_stages": [{"name": "detected", "count": 1, "ids": ["mapped_field_emptied"], "source": "t"}],
            "l3_evidence": _evidence(),
            "dry_run_fault": None,
        }
        ctx = types.SimpleNamespace(session=s, payload=payload, run_id=None)
        out = _run_repair_climb(
            ctx,
            controller=RepairController(),
            spec=spec,
            drift_class="SCHEMA_RENAME",
            vendor="atlas",
            drift_ids=[drift_id],
            payload=payload,
            running_row_id=None,
            on_rung=None,
        )
        s.commit()
        assert out["decision"] == "escalate", out
        assert out.get("repair_run_id"), out
        row = s.get(RepairRun, out["repair_run_id"])
        assert row is not None
        levels = [t.get("level") for t in (row.ladder_trace or []) if isinstance(t, dict)]
        assert 0 in levels and 1 in levels


def test_deferred_detected_job_climbs_with_replay_provider(monkeypatch: Any, tmp_path: Any) -> None:
    """Job-side detected climb with a replay (non-live) provider: L3 is
    attempted, the miss is recorded as a skip, and the run escalates."""
    import types

    from autobridge.control.models import Connector, ConnectorRelease, DriftEvent
    from autobridge.drift.signals import signal_to_json
    from autobridge.engine.releases import spec_signature
    from autobridge.repair.controller import _run_repair_climb

    monkeypatch.setenv("FRONTIER_MODE", "replay")
    monkeypatch.setenv("REPLAY_DIR", str(tmp_path))
    factory = _mem_factory()
    spec = _folio_spec()
    with factory() as s:
        conn = Connector(
            name="atlas", base_url="http://localhost:8105",
            credential_ref="ATLAS_API_KEY", target_entities=["FolioCharge"],
            runtime_target={"kind": "local_process"},
        )
        s.add(conn)
        s.flush()
        rel = ConnectorRelease(
            connector_id=conn.id, version=1, spec=spec, signature=spec_signature(spec)
        )
        s.add(rel)
        s.flush()
        conn.active_release_id = rel.id
        drift = DriftEvent(
            connector_id=conn.id, drift_class="ENUM_NEW_VALUE",
            source_sync_run_id="sync-x", status="open",
        )
        s.add(drift)
        s.commit()
        drift_id = drift.id
    with factory() as s:
        from autobridge.repair.controller import RepairController

        payload = {
            "drift_class": "ENUM_NEW_VALUE",
            "connector_id": None,
            "release_id": None,
            "signals_json": [
                signal_to_json(
                    __import__("autobridge.drift.signals", fromlist=["DriftSignal"]).DriftSignal(
                        kind="unknown_enum",
                        entity="status",
                        details={"entity": "Reservation", "field": "status"},
                    )
                )
            ],
            "fingerprint_delta": None,
            "hypotheses_json": [],
            "hypothesis_stages": [],
            "l3_evidence": {},
            "dry_run_fault": None,
        }
        ctx = types.SimpleNamespace(session=s, payload=payload, run_id=None)
        out = _run_repair_climb(
            ctx,
            controller=RepairController(),
            spec=spec,
            drift_class="ENUM_NEW_VALUE",
            vendor="atlas",
            drift_ids=[drift_id],
            payload=payload,
            running_row_id=None,
            on_rung=None,
        )
        s.commit()
        assert out["decision"] == "escalate", out
        assert out.get("provider_mode") == "replay", out
        assert "l3_status" in out, out


def test_detected_evidence_entity_field_fallbacks() -> None:
    """_detected_entity_field prefers silent-corruption signals, then any
    entity, then nothing (the L3 prompt still runs on the drift class)."""
    from autobridge.drift.signals import DriftSignal as _S
    from autobridge.repair import semantic as _sem

    assert _sem._detected_entity_field([]) == (None, None)
    assert _sem._detected_entity_field([_S(kind="outcome_blocked")]) == (None, None)
    assert _sem.build_detected_l3_evidence({}, {}, []) == {}
    assert _sem.build_detected_l3_evidence(_folio_spec(), {}, []) == {}
    ent, field = _sem._detected_entity_field(
        [_S(kind="unknown_enum", entity="status", details={"entity": "Reservation", "field": "status"})]
    )
    assert (ent, field) == ("Reservation", "status")
    ent, field = _sem._detected_entity_field([_S(kind="keys_changed", details={"entity": "FolioCharge"})])
    assert ent == "FolioCharge" and field is None
