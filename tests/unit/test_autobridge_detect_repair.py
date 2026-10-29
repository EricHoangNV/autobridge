"""REQ-027 sub-step 3 — the live chain: sync detects, repairs, approves.

Every drift below is observed the way a real sync observes it (an
``operation_results`` entry holding ``raw_pages``/``records`` plus the
interpreter outcome) — no scenario id, no fault YAML, no answer key.
The chain under test:

  sync summary -> signals_from_sync -> classify -> DriftEvent ->
  RepairController.handle_from_signals -> RepairRun + candidate release ->
  approve -> next sync Complete.

Plus: a ``Complete`` sync with wire-contradiction signals is still drift
(silent shape change), repeats hit L0, transport faults escalate with no
patch, and a None delta is never cached (no generic-key poisoning).
"""

from __future__ import annotations

import pathlib
from typing import Any

import yaml
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from autobridge.control.models import (
    Base,
    Connector,
    ConnectorRelease,
    DriftEvent,
    RepairRun,
    spec_signature,
)
from autobridge.drift.classifier import classify
from autobridge.drift.signals import signals_from_sync
from autobridge.engine.detect_repair import (
    build_detector_delta,
    detect_and_repair_after_sync,
)
from autobridge.repair.controller import RepairController
from autobridge.repair.knowledge import KnowledgeCache
from autobridge.runtime.interpreter import OperationResult


def _page_spec() -> dict[str, Any]:
    """Atlas-like page-paginated spec (pageNumber/pageSize + total)."""
    return {
        "spec_version": 1,
        "connector": {"id": "atlas", "vendor": "atlas"},
        "source": {
            "base_url": "http://atlas:8000",
            "auth": {"type": "bearer", "credential_ref": "ATLAS_API_KEY"},
            "timezone_source": {"kind": "static", "value": "UTC"},
        },
        "operations": {
            "list_reservations": {
                "method": "GET",
                "path": "/reservations",
                "params": {},
                "pagination": {
                    "strategy": "page",
                    "cursor_param": None,
                    "offset_param": None,
                    "page_param": "pageNumber",
                    "limit_param": "pageSize",
                    "next_path": None,
                    "items_path": "$.data",
                    "total_path": "$.total",
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
                    "guest_name": {"path": "$.guest_name"},
                },
            }
        },
        "capabilities": {"read": ["Reservation"]},
        "health": {"canary": {"every": "60s", "operation": "list_reservations"}},
    }


def _cursor_wire(i: int) -> dict[str, Any]:
    return {"reservation_id": f"R{i:04d}", "guest_name": f"Guest {i}"}


def _cursor_summary(outcome: str = "Complete") -> dict[str, Any]:
    """What a real sync sees when the vendor flips page -> cursor silently.

    The spec's ``total_path`` reads nothing and its ``items_path`` still
    extracts (the envelope keeps a ``data`` array), but a transmitted
    cursor leaf with no total contradicts the page strategy. Outcome stays
    Complete — the drift is silent, and the detector must still catch it.
    """
    wire = [_cursor_wire(i) for i in range(3)]
    return {
        "outcome": outcome,
        "unmet_criteria": [],
        "data_readiness": {"freshness": "current", "completeness": "complete"},
        "fingerprint_id": "chain-test",
        "operation_results": {
            "list_reservations": OperationResult(
                operation="list_reservations",
                records=wire,
                pages_fetched=1,
                raw_pages=[
                    {"success": True, "data": wire, "next_cursor": "NTA"}
                ],
                stop_reason="no_next_token",
            )
        },
        "records_observed": {"Reservation": 3},
        "canonical_records": {},
        "blocked_records": {},
        "invariant_results": [],
        "checkpoint_values": {},
    }


def _baseline_summary() -> dict[str, Any]:
    wire = [_cursor_wire(i) for i in range(3)]
    return {
        "outcome": "Complete",
        "unmet_criteria": [],
        "data_readiness": {"freshness": "current", "completeness": "complete"},
        "fingerprint_id": "chain-baseline",
        "operation_results": {
            "list_reservations": OperationResult(
                operation="list_reservations",
                records=wire,
                pages_fetched=1,
                raw_pages=[
                    {"success": True, "data": wire, "total": 3}
                ],
                stop_reason="total_reached",
            )
        },
        "records_observed": {"Reservation": 3},
        "canonical_records": {},
        "blocked_records": {},
        "invariant_results": [],
        "checkpoint_values": {},
    }


def _mem_factory() -> Any:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_connector(factory: Any, spec: dict[str, Any]) -> tuple[str, str]:
    with factory() as s:
        conn = Connector(
            name="Atlas",
            base_url="http://localhost:8105",
            credential_ref="ATLAS_API_KEY",
            target_entities=["Reservation"],
            runtime_target={"kind": "local_process"},
        )
        s.add(conn)
        s.flush()
        rel = ConnectorRelease(
            connector_id=conn.id, version=1, spec=spec,
            signature=spec_signature(spec),
        )
        s.add(rel)
        s.flush()
        conn.active_release_id = rel.id
        s.commit()
        return conn.id, rel.id


def _zero_usage(result: Any) -> None:
    assert result.model_usage["frontier_tokens"] == 0
    assert result.model_usage["small_tokens"] == 0


# ---------------------------------------------------------------------------
# No drift: healthy baseline sync writes no rows
# ---------------------------------------------------------------------------


def test_healthy_baseline_sync_detects_no_drift() -> None:
    factory = _mem_factory()
    spec = _page_spec()
    conn_id, rel_id = _seed_connector(factory, spec)
    with factory() as s:
        out = detect_and_repair_after_sync(
            s, connector_id=conn_id, release_id=rel_id,
            spec_dict=spec, summary=_baseline_summary(), sync_run_id="sync-ok",
        )
        assert out == {"drift_found": False, "outcome": "Complete"}
        assert s.query(DriftEvent).count() == 0
        assert s.query(RepairRun).count() == 0


# ---------------------------------------------------------------------------
# The live chain: detect -> propose (L1) -> candidate -> approve
# ---------------------------------------------------------------------------


def test_live_chain_page_to_cursor_propose_approve() -> None:
    factory = _mem_factory()
    spec = _page_spec()
    conn_id, rel_id = _seed_connector(factory, spec)

    with factory() as s:
        # A Complete sync that silently changed shape is still drift.
        out = detect_and_repair_after_sync(
            s, connector_id=conn_id, release_id=rel_id,
            spec_dict=spec, summary=_cursor_summary(), sync_run_id="sync-1",
            knowledge_cache=KnowledgeCache(),
        )
        assert out["drift_found"] is True
        assert out["drift_class"] == "PAGINATION_CHANGE"
        assert out["decision"] == "propose"
        assert out["rule_id"] == "page_to_cursor"
        assert out["ladder_level_reached"] == 1
        assert out["model_usage"]["frontier_tokens"] == 0
        assert out["model_usage"]["small_tokens"] == 0
        assert out["candidate_release_id"], "propose must persist a candidate release"
        assert out["gate_error"] is None
        drift_id = out["drift_id"]
        run_id = out["repair_run_id"]
        candidate_id = out["candidate_release_id"]
        s.commit()

    with factory() as s:
        drift = s.get(DriftEvent, drift_id)
        assert drift is not None and drift.status == "open"
        assert drift.source_sync_run_id == "sync-1"
        run = s.get(RepairRun, run_id)
        assert run is not None
        assert run.decision == "propose"
        assert run.rule_id == "page_to_cursor"
        assert run.ladder_level_reached == 1
        assert run.drift_event_ids == [drift_id]
        assert run.model_usage["frontier_tokens"] == 0
        assert len(run.ladder_trace) >= 2  # L0 miss + L1 rule, each with a reason
        candidate = s.get(ConnectorRelease, candidate_id)
        assert candidate is not None
        assert candidate.provenance_repair_run_id == run_id
        assert candidate.spec["operations"]["list_reservations"]["pagination"]["strategy"] == "cursor"

        # Approve the candidate: the repair becomes the active release.
        from autobridge.engine.releases import approve_release

        approval = approve_release(
            s, repair_run_id=run_id, actor="tester",
            from_release_id=rel_id, to_release_id=candidate_id,
            connector_id=conn_id,
        )
        s.commit()
        assert approval.decision == "approve"
        conn = s.get(Connector, conn_id)
        assert conn is not None and conn.active_release_id == candidate_id


# ---------------------------------------------------------------------------
# Repeats hit L0 (the derived delta is stable and specific)
# ---------------------------------------------------------------------------


def test_repeat_same_drift_hits_l0() -> None:
    factory = _mem_factory()
    spec = _page_spec()
    conn_id, rel_id = _seed_connector(factory, spec)
    cache = KnowledgeCache()
    with factory() as s:
        first = detect_and_repair_after_sync(
            s, connector_id=conn_id, release_id=rel_id,
            spec_dict=spec, summary=_cursor_summary(), sync_run_id="sync-1",
            knowledge_cache=cache,
        )
        s.commit()
        second = detect_and_repair_after_sync(
            s, connector_id=conn_id, release_id=rel_id,
            spec_dict=spec, summary=_cursor_summary(), sync_run_id="sync-2",
            knowledge_cache=cache,
        )
    assert first["ladder_level_reached"] == 1
    assert second["decision"] == "propose"
    assert second["ladder_level_reached"] == 0, "repeat must be an L0 hit"
    assert second["rule_id"] == "page_to_cursor"


def test_derived_delta_stable_and_specific() -> None:
    spec = _page_spec()
    signals = signals_from_sync(_cursor_summary(), spec=spec)
    d1 = build_detector_delta(spec, signals)
    d2 = build_detector_delta(spec, signals_from_sync(_cursor_summary(), spec=spec))
    assert d1 and d1 == d2, "same drift must re-derive the same delta"
    assert "pagination_details" in d1
    assert d1["pagination_details"]["list_reservations"]["previous"]["strategy"] == "page"
    assert d1["pagination_details"]["list_reservations"]["current"]["strategy"] == "cursor"
    assert build_detector_delta(spec, []) is None
    assert build_detector_delta(spec, signals_from_sync(_baseline_summary(), spec=spec)) is None


# ---------------------------------------------------------------------------
# Transport faults escalate with no patch and no candidate
# ---------------------------------------------------------------------------


def test_rate_limit_escalates_with_no_patch() -> None:
    factory = _mem_factory()
    spec = _page_spec()
    conn_id, rel_id = _seed_connector(factory, spec)
    summary = dict(_baseline_summary())
    summary["outcome"] = "Blocked"
    summary["unmet_criteria"] = ["rate_limited"]
    with factory() as s:
        out = detect_and_repair_after_sync(
            s, connector_id=conn_id, release_id=rel_id,
            spec_dict=spec, summary=summary, sync_run_id="sync-rl",
            knowledge_cache=KnowledgeCache(),
        )
        assert out["drift_found"] is True
        assert out["decision"] == "escalate"
        assert out["candidate_release_id"] is None
        assert out["model_usage"]["frontier_tokens"] == 0
        run = s.get(RepairRun, out["repair_run_id"])
        assert run is not None and run.candidate_patch is None


# ---------------------------------------------------------------------------
# A None delta is never cached (no generic-key poisoning across drift types)
# ---------------------------------------------------------------------------


def test_none_delta_never_cached() -> None:
    cache = KnowledgeCache()
    ctrl = RepairController(knowledge_cache=cache)
    spec = _page_spec()
    first = ctrl.handle(
        spec=spec,
        drift_class="PAGINATION_CHANGE",
        signals=[],
        fingerprint_delta=None,
        extra={"target_strategy": "cursor"},
        vendor="atlas",
        drift_event_ids=[],
        session=None,
    )
    assert first.decision == "propose" and first.rule_id == "page_to_cursor"
    assert cache.lookup("atlas", None) is None, "None delta must not populate L0"

    # A later, different drift must not L0-hit the unrelated cached patch.
    wire = [
        {"reservation_id": "R0001", "primaryOccupant": "Guest 1"},
    ]
    summary = {
        "outcome": "Complete",
        "unmet_criteria": [],
        "data_readiness": {},
        "fingerprint_id": "x",
        "operation_results": {
            "list_reservations": OperationResult(
                operation="list_reservations",
                records=wire,
                pages_fetched=1,
                raw_pages=[{"success": True, "data": wire, "total": 1}],
                stop_reason="total_reached",
            )
        },
        "records_observed": {"Reservation": 1},
        "canonical_records": {},
        "blocked_records": {},
        "invariant_results": [],
        "checkpoint_values": {},
    }
    signals = signals_from_sync(summary, spec=spec)
    assert classify(signals).drift_class == "SCHEMA_RENAME"
    second = ctrl.handle_from_signals(spec=spec, signals=signals, fingerprint_delta=None, session=None)
    assert second.decision == "propose" and second.rule_id == "rename_field"
    assert second.ladder_level_reached == 1, "must reach L1, not a poisoned L0 hit"


# ---------------------------------------------------------------------------
# run_sync_handler (worker + POST /sync path) detects and repairs inline
# ---------------------------------------------------------------------------


def test_sync_handler_detects_and_repairs_inline() -> None:
    from autobridge.control.jobs import JobContext
    from autobridge.engine.sync_run import run_sync_handler
    from bench.faults import fake_env, fault_client_for

    spec = yaml.safe_load(pathlib.Path("generated/connectors/atlaspms/reference/spec.yaml").read_text(encoding="utf-8"))
    factory = _mem_factory()
    conn_id, rel_id = _seed_connector(factory, spec)

    def _fault_client(_spec_dict: dict[str, Any]) -> Any:
        return fault_client_for("page_to_cursor", seed=1, connector_id="atlaspms")

    ctx = JobContext(
        job_id="job1", kind="sync", run_id=None,
        payload={"release_id": rel_id}, attempt=1,
        session=factory(), worker_id="w1",
    )
    try:
        with fake_env():
            result = run_sync_handler(ctx, http_client_factory=_fault_client)
        ctx.session.commit()
    finally:
        ctx.session.close()
    assert result["drift_found"] is True, result
    assert result["drift_class"] == "PAGINATION_CHANGE"
    assert result["decision"] == "propose"
    assert result["rule_id"] == "page_to_cursor"
    assert result["candidate_release_id"]
    with factory() as s:
        assert s.query(DriftEvent).count() == 1
        assert s.query(RepairRun).count() == 1


# ---------------------------------------------------------------------------
# Real faulted wire (served fault, not hand-built): the honest path
# ---------------------------------------------------------------------------


def test_real_served_fault_end_to_end() -> None:
    """Atlas reference spec against the served page_to_cursor fault.

    Uses the simulator fault-serving stack (the same path the bench arms
    use), so the wire evidence is genuinely observed, not constructed.
    """
    from autobridge.engine.sync_run import execute_sync
    from bench.faults import fake_env, fault_client_for

    spec = yaml.safe_load(pathlib.Path("generated/connectors/atlaspms/reference/spec.yaml").read_text(encoding="utf-8"))
    factory = _mem_factory()
    conn_id, rel_id = _seed_connector(factory, spec)
    with fake_env():
        faulted = execute_sync(spec, fault_client_for("page_to_cursor", seed=1, connector_id="atlaspms"))
    with factory() as s:
        out = detect_and_repair_after_sync(
            s, connector_id=conn_id, release_id=rel_id,
            spec_dict=spec, summary=faulted, sync_run_id="sync-live",
            knowledge_cache=KnowledgeCache(),
        )
        assert out["drift_found"] is True
        assert out["drift_class"] == "PAGINATION_CHANGE", out
        assert out["decision"] == "propose" and out["rule_id"] == "page_to_cursor"
        assert out["ladder_level_reached"] == 1
        assert out["model_usage"]["frontier_tokens"] == 0
        assert out["candidate_release_id"]


# ---------------------------------------------------------------------------
# REQ-027 owner check: no drift from a sync that got no response
# ---------------------------------------------------------------------------


def test_blocked_no_response_opens_no_drift() -> None:
    """A Blocked sync with zero observed responses is a connectivity
    failure, not drift: no DriftEvent, no RepairRun, outcome preserved."""
    from autobridge.engine.sync_run import execute_sync

    spec = yaml.safe_load(pathlib.Path("generated/connectors/atlaspms/reference/spec.yaml").read_text(encoding="utf-8"))
    factory = _mem_factory()
    conn_id, rel_id = _seed_connector(factory, spec)
    import httpx as _httpx

    def _refused(_spec_dict: dict[str, Any]) -> Any:
        from autobridge.runtime.http import HttpClient
        from autobridge.spec.models import RetryPolicy

        def factory_fn(**_kw: Any) -> _httpx.Client:
            def handler(_request: _httpx.Request) -> _httpx.Response:
                raise _httpx.ConnectError("connection refused")
            return _httpx.Client(transport=_httpx.MockTransport(handler))

        return HttpClient(
            "http://127.0.0.1:9",
            retry=RetryPolicy(statuses=[429, 503], backoff="exponential", max_attempts=1),
            client_factory=factory_fn,
            sleep_fn=lambda _: None,
        )

    blocked = execute_sync(spec, _refused(spec))
    assert blocked["outcome"] == "Blocked", blocked
    assert not (blocked.get("operation_results") or {}), blocked.get("operation_results")
    with factory() as s:
        out = detect_and_repair_after_sync(
            s, connector_id=conn_id, release_id=rel_id,
            spec_dict=spec, summary=blocked, sync_run_id="sync-refused",
            knowledge_cache=KnowledgeCache(),
        )
        assert out == {"drift_found": False, "outcome": "Blocked", "reason": "no_vendor_response"}, out
        assert s.query(DriftEvent).count() == 0
        assert s.query(RepairRun).count() == 0


def test_blocked_with_error_pages_still_detects() -> None:
    """Transport failures that returned pages (429 bodies) still detect:
    the guard is about zero observed responses, not the Blocked outcome."""
    spec = _page_spec()
    factory = _mem_factory()
    conn_id, rel_id = _seed_connector(factory, spec)
    summary = {
        "outcome": "Blocked",
        "unmet_criteria": ["http_error"],
        "data_readiness": {"freshness": "unknown", "completeness": "none"},
        "fingerprint_id": "chain-blocked",
        "operation_results": {
            "list_reservations": OperationResult(
                operation="list_reservations",
                records=[],
                pages_fetched=1,
                raw_pages=[{"error": "http_error", "status": 503}],
                stop_reason="http_error",
            )
        },
        "records_observed": {"Reservation": 0},
        "canonical_records": {},
        "blocked_records": {},
        "invariant_results": [],
        "checkpoint_values": {},
    }
    with factory() as s:
        out = detect_and_repair_after_sync(
            s, connector_id=conn_id, release_id=rel_id,
            spec_dict=spec, summary=summary, sync_run_id="sync-503",
            knowledge_cache=KnowledgeCache(),
        )
        assert out["drift_found"] is True, out
        assert s.query(DriftEvent).count() == 1
