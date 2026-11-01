"""REQ-078 item 1 (supersedes REQ-065): every repairable drift climbs past L1.

"Deterministic first, models last" stays: an L1 rule hit never reaches
the model, and the true non-fixables (CAPABILITY_REMOVED, AUTH_REVOKED)
plus backoff/transient never climb. But there is no class list gating
the climb anywhere: any other drift class that L0/L1 cannot repair
reaches L2/L3 with a fake provider, and the deferred live-model job
path applies to every repairable class, not only SEMANTIC_VALUE_DRIFT.
Silent-corruption detection (B1: ``mapped_field_emptied`` /
``money_scale_shift``) is unchanged. Every test uses FakeProvider (no
network, no key).
"""

from __future__ import annotations

from typing import Any

from autobridge.llm.provider import FakeProvider


def test_bench_climb_gate_has_no_class_list() -> None:
    """No hard-coded class list gates the model climb (REQ-078 item 1)."""
    from bench.ablation import _MODEL_AMBIGUITY_CLASSES

    assert frozenset() == _MODEL_AMBIGUITY_CLASSES


def test_engine_arm_climbs_for_detected_non_semantic_class() -> None:
    """status_vocab_change detects ENUM_NEW_VALUE with no fitting rule: the
    arm climbs to the model (FakeProvider answers the escalation) instead
    of stopping at a deterministic escalation."""
    import json

    from autobridge.llm.provider import Completion, Usage
    from bench.ablation import ablation_case_for_scenario, run_engine_arm
    from bench.harness import HarnessConfig, make_trial_inputs

    inputs = make_trial_inputs(HarnessConfig(), "status_vocab_change", 0)
    case = ablation_case_for_scenario("status_vocab_change")
    provider = FakeProvider(
        responses={
            "frontier": Completion(
                text=json.dumps(
                    {
                        "escalate": "Which canonical status does the new value mean?",
                        "rationale": "no rule fits this enum value",
                    }
                ),
                usage=Usage(input_tokens=100, output_tokens=50),
            )
        }
    )
    res = run_engine_arm(inputs, case, ablation="autobridge_cold", provider=provider)
    assert provider.calls != [], "non-semantic class with no rule must reach the model"
    assert res.spec is not None and res.spec["decision"] == "escalate"
    assert res.frontier_tokens > 0


def test_l1_fix_never_calls_provider() -> None:
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
    without evidence): the ladder climbs to the model, then escalates."""
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


def test_live_non_semantic_drift_defers_when_live_wanted(
    monkeypatch: Any,
) -> None:
    """With a live provider wanted, a non-semantic drift also defers to a
    background repair job (REQ-078 item 1) — the deferred path is not
    semantic-only."""
    from autobridge.control.models import Job, RepairRun
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
    assert out.get("repair_pending"), out
    assert out["drift_class"] == "ENUM_NEW_VALUE", out
    with factory() as s:
        assert s.query(RepairRun).count() == 0
        assert s.query(Job).count() == 1
