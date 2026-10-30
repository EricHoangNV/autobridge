"""REQ-039: the live repair path climbs semantic drift (funnel + L3 + judge).

A scheduler/worker sync that degrades on a money invariant must not take
the deterministic 0-call escalation: the live path enumerates the
engine's own amount readings from the wire signals and the spec, filters
them, climbs L0 -> L1 -> L2(skip, one explanation) -> L3 (provider call
or explicit skip), judges the proposal against the verifier, and
escalates with one operator question. All from observed sync data — no
scenario ids, no fault YAML, no answer keys.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from autobridge.control.models import (
    Base,
    Connector,
    ConnectorRelease,
    EvidenceRecord,
    ModelCall,
    RepairRun,
    spec_signature,
)


def _money_spec() -> dict[str, Any]:
    return {
        "spec_version": 3,
        "connector": {"id": "harbor", "vendor": "harbor"},
        "source": {
            "base_url": "http://harbor:8000",
            "auth": {"type": "bearer", "credential_ref": "HARBOR_API_KEY"},
            "timezone_source": {"kind": "static", "value": "UTC"},
        },
        "operations": {
            "list_reservations": {
                "method": "GET",
                "path": "/reservations",
                "params": {},
                "pagination": {
                    "strategy": "page",
                    "page_param": "page",
                    "limit_param": "limit",
                    "items_path": "$.data",
                    "stop_when": ["empty_page"],
                },
            },
            "list_folios": {
                "method": "GET",
                "path": "/folios",
                "params": {},
                "pagination": {
                    "strategy": "page",
                    "page_param": "page",
                    "limit_param": "limit",
                    "items_path": "$.data",
                    "stop_when": ["empty_page"],
                },
            },
        },
        "entities": {
            "Reservation": {
                "from": "list_reservations",
                "key": "$.reservation_id",
                "fields": {
                    "reservation_id": {"path": "$.reservation_id"},
                    "total_amount": {"path": "$.total_amount"},
                },
                "checks": [
                    {
                        "kind": "money_invariant",
                        "field": "total_amount",
                        "compare_to": "$.gross_sum",
                    }
                ],
            },
            "FolioCharge": {
                "from": "list_folios",
                "key": "$.charge_id",
                "fields": {
                    "charge_id": {"path": "$.charge_id"},
                    "amount": {"path": "$.amount"},
                },
                "checks": [
                    {"kind": "money_invariant", "field": "amount", "compare_to": "$.gross_sum"}
                ],
            },
        },
        "capabilities": {"read": ["Reservation", "FolioCharge"]},
        "health": {"canary": {"every": "60s", "operation": "list_reservations"}},
    }


def _net_summary() -> dict[str, Any]:
    """What a real sync sees when totals read net instead of gross."""
    return {
        "outcome": "Degraded",
        "unmet_criteria": ["money_invariant"],
        "data_readiness": {"freshness": "current", "completeness": "complete"},
        "fingerprint_id": "semantic-live",
        "operation_results": {
            "list_reservations": {
                "records": [
                    {"reservation_id": "r1", "total_amount": "90.00"},
                    {"reservation_id": "r2", "total_amount": "180.00"},
                ],
                "pages_fetched": 1,
                "raw_pages": [{"gross_sum": "300.00", "data": []}],
                "stop_reason": "total_reached",
            }
        },
        "records_observed": {"Reservation": 2},
        "canonical_records": {
            "Reservation": [
                {"reservation_id": "r1", "total_amount": "90.00"},
                {"reservation_id": "r2", "total_amount": "180.00"},
            ],
        },
        "blocked_records": {},
        "invariant_results": [{"check": "money_invariant", "status": "failed"}],
        "checkpoint_values": {},
    }


def _mem_factory() -> Any:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed(factory: Any, spec: dict[str, Any]) -> tuple[str, str]:
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


_DECOY = (
    '[{"op": "replace", '
    '"path": "/entities/Reservation/fields/total_amount/path", '
    '"value": "$.net_amount"}]'
)


def _live_provider(text: str = _DECOY) -> Any:
    from autobridge.llm.provider import Completion, FakeProvider, Usage

    return FakeProvider(
        responses={
            "frontier": Completion(text=text, usage=Usage(2000, 115)),
            "small": Completion(text="[]", usage=Usage(96, 12)),
        },
        frontier_model="fake-frontier",
        small_model="fake-small",
    )


def _hypothesis_evidence(session: Any, run_id: str) -> dict[str, Any]:
    rows = (
        session.execute(
            select(EvidenceRecord).where(EvidenceRecord.run_id == f"repair_runs:{run_id}")
        )
        .scalars()
        .all()
    )
    hyps = [r for r in rows if r.kind == "hypothesis"]
    assert len(hyps) == 1, [r.kind for r in rows]
    return dict(hyps[0].payload)


def test_live_semantic_break_climbs_with_funnel_and_l3_attempt(monkeypatch: Any) -> None:
    """Break -> Degraded -> the repair carries the funnel + L3 attempt(s).

    REQ-053 item 6: the live model's hallucinated wire field
    (``$.net_amount`` is not in the response) fails the dry-run on the
    break's records: one retry with the exact error, then the proposal is
    dropped and the repair stands on the question alone — never offered.
    """
    import autobridge.llm.provider as provider_mod
    from autobridge.engine.detect_repair import detect_and_repair_after_sync
    from autobridge.repair.knowledge import KnowledgeCache

    monkeypatch.setattr(
        provider_mod, "resolve_frontier_provider", lambda: (_live_provider(), "live")
    )
    factory = _mem_factory()
    spec = _money_spec()
    conn_id, rel_id = _seed(factory, spec)
    with factory() as s:
        out = detect_and_repair_after_sync(
            s,
            connector_id=conn_id,
            release_id=rel_id,
            spec_dict=spec,
            summary=_net_summary(),
            sync_run_id="sync-sem-1",
            verifier_url="http://127.0.0.1:9",
            knowledge_cache=KnowledgeCache(),
        )
        assert out["drift_found"] is True
        assert out["drift_class"] == "SEMANTIC_VALUE_DRIFT"
        assert out["decision"] == "escalate"
        assert out["provider_mode"] == "live"
        assert out["l3_status"] == "attempt"
        run = s.get(RepairRun, out["repair_run_id"])
        assert run is not None
        levels = [t["level"] for t in run.ladder_trace]
        assert levels == [0, 1, 2, 3, 4], run.ladder_trace
        by_level = {t["level"]: t for t in run.ladder_trace}
        assert by_level[2]["action"] == "l3_skipped" or by_level[2]["action"] == "l2_skipped"
        l3 = by_level[3]
        assert l3["action"] == "proposal_rejected"
        assert l3.get("proposal") is None
        # The hallucinated wire field makes the re-mapped records fail the
        # money check: dropped after the one retry, never offered.
        assert "dry-run" in ((l3.get("proposal_dropped") or "") + (l3.get("reason") or ""))
        assert "money_invariant" in ((l3.get("proposal_dropped") or "") + (l3.get("reason") or ""))
        # The exact prompt rides on the rung: make record-frontier replays
        # these messages live to capture the recording replay mode needs.
        assert isinstance(l3.get("messages"), list) and len(l3["messages"]) == 2
        # The funnel derivation persists as hypothesis evidence.
        stages = _hypothesis_evidence(s, run.id)["stages"]
        assert [st["name"] for st in stages] == [
            "generated",
            "amount",
            "wire_consistent",
            "distinct",
        ]
        assert stages[0]["count"] == 6
        assert stages[2]["ids"] == ["amount:amount:net", "amount:total_amount:net"]
        assert stages[3]["ids"] == ["amount:total_amount:net"]
        # Both L3 calls land in the ModelCall ledger (initial + one retry).
        calls = (
            s.execute(select(ModelCall).where(ModelCall.run_id == run.id))
            .scalars()
            .all()
        )
        assert len(calls) == 2
        assert {c.tier for c in calls} == {"frontier"}
        assert {c.mode for c in calls} == {"live"}
        # Initial attempt and retry both ended rejected (dry-run).
        assert {c.outcome for c in calls} == {"proposal_rejected"}
        # The dry-run drop is recorded as evidence, never raised — the
        # escalation stands with its operator question.
        verifs = [
            r
            for r in s.execute(
                select(EvidenceRecord).where(
                    EvidenceRecord.run_id == f"repair_runs:{run.id}"
                )
            )
            .scalars()
            .all()
            if r.kind == "verification"
        ]
        assert len(verifs) == 1
        assert "dry-run" in str(verifs[0].payload["verdict"]["reason"])
        assert run.escalation_bundle is not None
        s.commit()


def test_live_semantic_break_without_recording_records_explicit_skip(
    monkeypatch: Any, tmp_path: Any
) -> None:
    """No key and no recording: L3 is skipped explicitly, never invented."""
    from autobridge.engine.detect_repair import detect_and_repair_after_sync
    from autobridge.repair.knowledge import KnowledgeCache

    monkeypatch.delenv("FRONTIER_API_KEY", raising=False)
    monkeypatch.setenv("FRONTIER_MODE", "auto")
    monkeypatch.setenv("REPLAY_DIR", str(tmp_path / "empty"))
    factory = _mem_factory()
    spec = _money_spec()
    conn_id, rel_id = _seed(factory, spec)
    with factory() as s:
        out = detect_and_repair_after_sync(
            s,
            connector_id=conn_id,
            release_id=rel_id,
            spec_dict=spec,
            summary=_net_summary(),
            sync_run_id="sync-sem-2",
            knowledge_cache=KnowledgeCache(),
        )
        assert out["decision"] == "escalate"
        assert out["provider_mode"] == "replay"
        assert str(out["l3_status"]).startswith("skip:")
        assert "no replay recording" in str(out["l3_status"])
        run = s.get(RepairRun, out["repair_run_id"])
        levels = [t["level"] for t in run.ladder_trace]
        assert levels == [0, 1, 2, 3, 4], run.ladder_trace
        by_level = {t["level"]: t for t in run.ladder_trace}
        assert by_level[3]["action"] == "l3_skipped"
        assert "make record-frontier" in by_level[3]["reason"]
        # The skipped prompt is still recorded: the capture replays it.
        assert isinstance(by_level[3].get("messages"), list)
        stages = _hypothesis_evidence(s, run.id)["stages"]
        assert [st["name"] for st in stages] == [
            "generated",
            "amount",
            "wire_consistent",
            "distinct",
        ]
        calls = (
            s.execute(select(ModelCall).where(ModelCall.run_id == run.id))
            .scalars()
            .all()
        )
        assert calls == []
        s.commit()


def test_repair_job_handler_semantic_branch(monkeypatch: Any) -> None:
    """The worker repair-job path climbs too, never deterministically."""
    import autobridge.llm.provider as provider_mod
    from autobridge.repair.controller import repair_job_handler

    monkeypatch.setattr(
        provider_mod, "resolve_frontier_provider", lambda: (_live_provider(), "live")
    )
    factory = _mem_factory()
    spec = _money_spec()
    with factory() as s:
        conn = Connector(
            name="harborpms",
            base_url="http://localhost:8106",
            credential_ref="HARBOR_API_KEY",
            target_entities=["Reservation"],
            runtime_target={"kind": "local_process"},
        )
        s.add(conn)
        s.commit()
        ctx = SimpleNamespace(
            payload={
                "drift_event_ids": [],
                "spec": spec,
                "drift_class": "SEMANTIC_VALUE_DRIFT",
                "signals": [],
                "summary": _net_summary(),
                "sync_run_id": "sync-job-1",
                "vendor": "harbor",
            },
            session=s,
            run_id=None,
        )
        out = repair_job_handler(ctx)
        assert out["decision"] == "escalate"
        assert out["provider_mode"] == "live"
        assert out["l3_status"] == "attempt"
        assert out["ladder_level_reached"] == 4
        s.commit()
