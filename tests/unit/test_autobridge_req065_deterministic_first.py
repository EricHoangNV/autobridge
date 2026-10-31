"""REQ-065: L3 runs for semantic drift / UNKNOWN only (REQ-064 B2 removed).

"Deterministic first, models last": a detected non-semantic class with no
fitting rule escalates at L4 with its question and 0 model tokens. The
bench engine arms never call the provider for such a class, and the live
sync path never defers it to a background job (only SEMANTIC_VALUE_DRIFT
defers when a live call is wanted). Silent-corruption detection (B1:
``mapped_field_emptied``/``money_scale_shift``) is unchanged — those
signals still classify, and a detected class with no rule escalates with
its question. Every test uses FakeProvider (no network, no key).
"""

from __future__ import annotations

from typing import Any

from autobridge.llm.provider import FakeProvider


def test_bench_climb_gate_is_semantic_only() -> None:
    """The bench phase-2 gate climbs only for semantic ambiguity."""
    from bench.ablation import _MODEL_AMBIGUITY_CLASSES

    assert frozenset({"SEMANTIC_VALUE_DRIFT"}) == _MODEL_AMBIGUITY_CLASSES


def test_engine_arm_no_l3_for_detected_non_semantic_class() -> None:
    """status_vocab_change detects ENUM_NEW_VALUE with no fitting rule: the
    arm escalates without calling the provider (0 frontier tokens)."""
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
    assert provider.calls == [], "non-semantic class must not reach L3"
    assert res.spec is not None and res.spec["decision"] == "escalate"
    assert res.frontier_tokens == 0


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


def test_live_non_semantic_drift_escalates_inline_when_live_wanted(
    monkeypatch: Any,
) -> None:
    """Even with a live provider wanted, a non-semantic drift with no
    fitting rule escalates inline (no background job, no repair_pending) —
    only SEMANTIC_VALUE_DRIFT defers."""
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
    assert not out.get("repair_pending"), out
    assert out["decision"] == "escalate", out
    assert out["drift_class"] == "ENUM_NEW_VALUE", out
    with factory() as s:
        assert s.query(RepairRun).count() == 1
        assert s.query(Job).count() == 0
