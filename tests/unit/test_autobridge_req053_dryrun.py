"""REQ-053 item 6: dry-run every proposal, auto-rollback on failed verifying sync.

* A proposal that references a wire field the vendor never sends (deck 5:
  ``$.net_sum``) is rejected with the exact error, retried once, then
  dropped to question-only — never offered for Approve.
* A proposal whose checks pass on the break's records is offered with the
  dry-run result shown ("tested on the current records: the checks pass").
* A verifying sync that still fails after an approval rolls back to the
  previous release automatically; the original repair stays the one open
  item — never a second escalation for the same open drift.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from autobridge.control.models import (
    Approval,
    Base,
    Connector,
    ConnectorRelease,
    DriftEvent,
    RepairRun,
    spec_signature,
)
from autobridge.engine.sync_run import execute_sync
from autobridge.repair.dryrun import build_dry_run_fault, dry_run_proposal

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _mem_factory() -> Any:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _harbor_spec() -> dict[str, Any]:
    from autobridge.discovery.bootstrap import build_draft_spec

    docs = (REPO_ROOT / "simulators" / "docs" / "harbor.md").read_text(encoding="utf-8")
    res = build_draft_spec(
        docs,
        source="simulators/docs/harbor.md",
        connector_id="x",
        vendor="harborpms",
        spec_vocabulary="v1",
        credential_ref="HARBOR_API_KEY",
        base_url_override="http://localhost:8106",
    )
    assert res.success, res.degradation_reason
    return json.loads(json.dumps(res.draft_spec, default=str))


def _harbor_fault(spec: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    from bench.faults import fake_env, fault_client_for

    with fake_env():
        summary = execute_sync(
            spec, fault_client_for("gross_to_net", seed=1, connector_id="harborpms")
        )
    assert summary["outcome"] == "Degraded", summary
    fault = build_dry_run_fault(summary)
    assert fault is not None
    return summary, fault


def _net_sum_patch() -> list[dict[str, Any]]:
    # Deck 5's decoy: a field Harbor never sends.
    return [
        {"op": "replace", "path": "/entities/Reservation/checks/3/compare_to", "value": "$.net_sum"}
    ]


def _resolvable_patch() -> list[dict[str, Any]]:
    # A pointer the response actually carries (the faulted page has `total`).
    return [
        {"op": "replace", "path": "/entities/Reservation/checks/3/compare_to", "value": "$.total"}
    ]


def test_dry_run_rejects_wire_field_the_vendor_never_sends() -> None:
    spec = _harbor_spec()
    _summary, fault = _harbor_fault(spec)
    ok, detail = dry_run_proposal(spec, _net_sum_patch(), fault)
    assert ok is False
    assert detail == (
        "`$.net_sum` is not a field in the response; available: "
        + ", ".join(
            sorted(fault["raw_first_pages"]["list_reservations"].keys())
        )
    )


def test_dry_run_passes_a_resolvable_pointer() -> None:
    spec = _harbor_spec()
    _summary, fault = _harbor_fault(spec)
    ok, detail = dry_run_proposal(spec, _resolvable_patch(), fault)
    assert ok is True, detail
    assert detail == "the checks pass on the current records"


def test_dry_run_passes_patches_that_touch_no_entity() -> None:
    spec = _harbor_spec()
    _summary, fault = _harbor_fault(spec)
    pagination_patch = [
        {"op": "replace", "path": "/operations/list_reservations/pagination/strategy", "value": "cursor"}
    ]
    ok, _ = dry_run_proposal(spec, pagination_patch, fault)
    assert ok is True


def test_dry_run_without_fault_records_skips() -> None:
    spec = _harbor_spec()
    ok, detail = dry_run_proposal(spec, _net_sum_patch(), None)
    assert ok is True
    assert "skipped" in detail


def _scripted_frontier(texts: list[str]) -> Any:
    from autobridge.llm.provider import Completion, FakeProvider, Usage

    script = list(texts)

    def _next(*_args: Any, **_kwargs: Any) -> Completion:
        text = script.pop(0) if len(script) > 1 else script[0]
        return Completion(text=text, usage=Usage(10, 10))

    return FakeProvider(
        responses={
            "small": Completion(
                text='["gross_is_canonical", "net_is_canonical"]', usage=Usage(4, 4)
            ),
            "frontier": _next,
        }
    )


def _harbor_semantic_inputs() -> dict[str, Any]:
    from autobridge.discovery.docs_reader import Provenance
    from autobridge.discovery.hypotheses import Hypothesis

    prov = (Provenance(source="demo:gross_to_net", line=1, snippet="total_amount reads net"),)
    return {
        "hypotheses": [
            Hypothesis(
                id="gross_is_canonical",
                family="amount",
                claim="total_amount must stay gross",
                candidate_mapping_fragment={"family": "amount", "field": "total_amount"},
                prior=0.41,
                evidence_for=prov,
                field="total_amount",
            ),
            Hypothesis(
                id="net_is_canonical",
                family="amount",
                claim="total_amount reads net now",
                candidate_mapping_fragment={"family": "amount", "field": "total_amount"},
                prior=0.39,
                evidence_for=prov,
                field="total_amount",
            ),
        ]
    }


def test_climb_retries_with_the_exact_dry_run_error_then_drops() -> None:
    """Bad field first: one retry with the exact error, then question-only."""
    from autobridge.llm.provider import Budget
    from autobridge.repair.ladder import run_frontier_climb
    from autobridge.repair.semantic import allowed_l3_pointers

    spec = _harbor_spec()
    _summary, fault = _harbor_fault(spec)
    evidence = {
        "patch_target": {
            "document": "connector spec",
            "entity": "Reservation",
            "allowed_ops": ["replace", "add"],
            "allowed_pointers": allowed_l3_pointers(spec, "Reservation"),
        }
    }
    bad = json.dumps(_net_sum_patch())
    climb = run_frontier_climb(
        spec=spec,
        drift_class="SEMANTIC_VALUE_DRIFT",
        hypotheses=_harbor_semantic_inputs()["hypotheses"],
        enumerator_yield_count=1,
        provider=_scripted_frontier([bad, bad]),
        budget=Budget(),
        vendor="harborpms",
        scenario="gross_to_net",
        validate_against=spec,
        evidence=evidence,
        dry_run_fault=fault,
    )
    assert climb["proposal"] is None
    l3 = next(a for a in climb["attempts"] if a["rung"] == 3)
    assert "dry-run" in (l3.get("retry_error") or l3.get("reason") or "")
    assert "`$.net_sum` is not a field in the response" in (
        l3.get("retry_error") or l3.get("reason") or ""
    )
    assert climb["usage"]["frontier_calls"] == 2


def test_climb_offers_the_retried_proposal_with_the_dry_run_result() -> None:
    """Bad field, then a resolvable one: the proposal is offered once, with
    the dry-run result shown beside it."""
    from autobridge.llm.provider import Budget
    from autobridge.repair.ladder import run_frontier_climb
    from autobridge.repair.semantic import allowed_l3_pointers

    spec = _harbor_spec()
    _summary, fault = _harbor_fault(spec)
    evidence = {
        "patch_target": {
            "document": "connector spec",
            "entity": "Reservation",
            "allowed_ops": ["replace", "add"],
            "allowed_pointers": allowed_l3_pointers(spec, "Reservation"),
        }
    }
    climb = run_frontier_climb(
        spec=spec,
        drift_class="SEMANTIC_VALUE_DRIFT",
        hypotheses=_harbor_semantic_inputs()["hypotheses"],
        enumerator_yield_count=1,
        provider=_scripted_frontier([json.dumps(_net_sum_patch()), json.dumps(_resolvable_patch())]),
        budget=Budget(),
        vendor="harborpms",
        scenario="gross_to_net",
        validate_against=spec,
        evidence=evidence,
        dry_run_fault=fault,
    )
    assert climb["proposal"] == _resolvable_patch()
    assert climb["dry_run"] == "tested on the current records: the checks pass"
    l3 = next(a for a in climb["attempts"] if a["rung"] == 3)
    assert l3.get("dry_run") == "tested on the current records: the checks pass"


def _seed_harbor(factory: Any, spec: dict[str, Any]) -> tuple[str, str]:
    with factory() as s:
        conn = Connector(
            name="harborpms",
            base_url="http://localhost:8106",
            credential_ref="HARBOR_API_KEY",
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


def test_gate_records_the_pass_on_the_proposing_rung() -> None:
    """A passing proposal keeps its candidate and stamps the dry-run on
    the proposing rung (shown with the patch on the repair page)."""
    from autobridge.engine.detect_repair import _gate_proposal_on_fault_records
    from autobridge.repair.ladder import LadderResult

    factory = _mem_factory()
    spec = _harbor_spec()
    conn_id, _rel_id = _seed_harbor(factory, spec)
    _summary, _fault = _harbor_fault(spec)
    with factory() as s:
        drift = DriftEvent(
            connector_id=conn_id, drift_class="SEMANTIC_VALUE_DRIFT",
            source_sync_run_id="sync-x", status="open",
        )
        s.add(drift)
        s.flush()
        run = RepairRun(
            drift_event_ids=[drift.id], decision="propose", model_usage={},
            candidate_patch=_resolvable_patch(), rule_id="test_rule",
            vendor="harborpms", drift_class="SEMANTIC_VALUE_DRIFT",
            ladder_trace=[{"level": 1, "action": "rule", "reason": "matched"}],
            ladder_level_reached=1,
        )
        s.add(run)
        s.flush()
        result = LadderResult(
            decision="propose", ladder_level_reached=1,
            candidate_patch=_resolvable_patch(), rule_id="test_rule",
            verification_level=None, model_usage={},
            repair_run_id=run.id,
        )
        rewritten, dry_error = _gate_proposal_on_fault_records(
            s, spec_dict=spec, summary=_summary, result=result
        )
        assert dry_error is None
        assert rewritten.decision == "propose"
        assert rewritten.candidate_patch == _resolvable_patch()
        s.commit()
        row = s.get(RepairRun, run.id)
        assert row is not None
        rung = next(e for e in (row.ladder_trace or []) if e.get("level") == 1)
        assert rung.get("dry_run") == "tested on the current records: the checks pass"


def test_gate_rewrites_a_bad_deterministic_proposal_to_escalate() -> None:
    """The detect-layer gate drops a failing patch: escalation, no candidate."""
    from autobridge.engine.detect_repair import _gate_proposal_on_fault_records
    from autobridge.repair.ladder import LadderResult

    factory = _mem_factory()
    spec = _harbor_spec()
    conn_id, rel_id = _seed_harbor(factory, spec)
    _summary, _fault = _harbor_fault(spec)
    with factory() as s:
        drift = DriftEvent(
            connector_id=conn_id, drift_class="SEMANTIC_VALUE_DRIFT",
            source_sync_run_id="sync-x", status="open",
        )
        s.add(drift)
        s.flush()
        run = RepairRun(
            drift_event_ids=[drift.id], decision="propose", model_usage={},
            candidate_patch=_net_sum_patch(), rule_id="test_rule",
            vendor="harborpms", drift_class="SEMANTIC_VALUE_DRIFT",
        )
        s.add(run)
        s.flush()
        result = LadderResult(
            decision="propose", ladder_level_reached=1,
            candidate_patch=_net_sum_patch(), rule_id="test_rule",
            verification_level=None, model_usage={},
            repair_run_id=run.id,
        )
        rewritten, dry_error = _gate_proposal_on_fault_records(
            s, spec_dict=spec, summary=_summary, result=result
        )
        assert dry_error is not None and "$.net_sum" in dry_error
        assert rewritten.decision == "escalate"
        assert rewritten.candidate_patch is None
        s.commit()
        row = s.get(RepairRun, run.id)
        assert row is not None
        assert row.decision == "escalate"
        assert not row.candidate_patch
        assert "dry-run" in (row.escalation_reason or "")


def test_failed_verifying_sync_rolls_back_and_reopens_without_a_second_repair() -> None:
    """Approve a bad patch → verifying sync Degraded → automatic rollback;
    the original escalation stays open, no second repair is created."""
    from autobridge.engine.detect_repair import detect_after_sync
    from autobridge.engine.releases import approve_release

    factory = _mem_factory()
    spec = _harbor_spec()
    conn_id, r1_id = _seed_harbor(factory, spec)

    with factory() as s:
        drift = DriftEvent(
            connector_id=conn_id, drift_class="SEMANTIC_VALUE_DRIFT",
            source_sync_run_id="sync-break", status="open",
        )
        s.add(drift)
        s.flush()
        run = RepairRun(
            drift_event_ids=[drift.id], decision="propose", model_usage={},
            candidate_patch=_net_sum_patch(), rule_id="test_rule",
            vendor="harborpms", drift_class="SEMANTIC_VALUE_DRIFT",
        )
        s.add(run)
        s.flush()
        candidate = ConnectorRelease(
            connector_id=conn_id, version=2, spec=spec,
            parent_release_id=r1_id, signature=spec_signature(spec),
            provenance_repair_run_id=run.id,
        )
        s.add(candidate)
        s.flush()
        approve_release(
            s, repair_run_id=run.id, actor="operator",
            from_release_id=r1_id, to_release_id=candidate.id,
            connector_id=conn_id,
        )
        assert run.decision == "approved"
        s.commit()
        candidate_id = candidate.id

    # The verifying sync runs on the approved release and still fails.
    _summary, _fault = _harbor_fault(spec)
    with factory() as s:
        out = detect_after_sync(
            s, connector_id=conn_id, release_id=candidate_id,
            spec_dict=spec, summary=_summary, sync_run_id="sync-verify",
        )
        s.commit()
    assert out.get("rolled_back", {}).get("rolled_back") is True, out
    assert out["drift_found"] is False
    with factory() as s:
        assert s.query(DriftEvent).filter(DriftEvent.status == "open").count() == 1
        # Still exactly one repair: the original, re-opened — never a second.
        assert s.query(RepairRun).count() == 1
        row = s.query(RepairRun).one()
        assert row.decision == "escalate"
        assert not row.candidate_patch
        bundle = row.escalation_bundle or {}
        assert bundle.get("proposal") is None
        assert "automatic rollback" in (bundle.get("reason") or "")
        # Two approvals: the approve and the automatic rollback.
        approvals = (
            s.query(Approval).filter(Approval.repair_run_id == row.id)
            .order_by(Approval.created_at).all()
        )
        assert [a.decision for a in approvals] == ["approve", "rollback"]
        conn = s.get(Connector, conn_id)
        assert conn is not None
        active = s.get(ConnectorRelease, conn.active_release_id)
        assert active is not None and active.version == 3
        # The rollback release restores the pre-approval spec: the bad
        # compare_to is gone, the gross_sum check stands.
        checks = active.spec["entities"]["Reservation"]["checks"]
        money = [c for c in checks if c.get("kind") == "money_invariant"]
        assert money and money[0].get("compare_to") == "$.gross_sum"

    # A later sync on the rolled-back release dedupes onto the original
    # repair instead of opening a second escalation.
    with factory() as s:
        conn = s.get(Connector, conn_id)
        assert conn is not None
        out2 = detect_after_sync(
            s, connector_id=conn_id, release_id=str(conn.active_release_id),
            spec_dict=spec, summary=_summary, sync_run_id="sync-again",
        )
        assert out2.get("deduped") is True, out2
        assert out2.get("repair_run_id") == row.id
        assert s.query(RepairRun).count() == 1


def test_verifying_sync_complete_needs_no_rollback() -> None:
    from autobridge.engine.detect_repair import _maybe_rollback_verifying_sync
    from autobridge.engine.releases import approve_release

    factory = _mem_factory()
    spec = _harbor_spec()
    conn_id, r1_id = _seed_harbor(factory, spec)
    with factory() as s:
        run = RepairRun(drift_event_ids=[], decision="propose", model_usage={},
                        candidate_patch=_resolvable_patch())
        s.add(run)
        s.flush()
        candidate = ConnectorRelease(
            connector_id=conn_id, version=2, spec=spec,
            parent_release_id=r1_id, signature=spec_signature(spec),
            provenance_repair_run_id=run.id,
        )
        s.add(candidate)
        s.flush()
        approve_release(
            s, repair_run_id=run.id, actor="operator",
            from_release_id=r1_id, to_release_id=candidate.id,
            connector_id=conn_id,
        )
        s.commit()
        assert _maybe_rollback_verifying_sync(
            s, connector_id=conn_id, release_id=candidate.id,
            summary={"outcome": "Complete", "operation_results": {"op": {"records": [{}]}}},
        ) is None
