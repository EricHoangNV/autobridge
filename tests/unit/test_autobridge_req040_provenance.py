"""REQ-040: repair provenance is threaded, never re-fetched as "the newest row".

The sync→repair path used to re-fetch the repair row it had just persisted
with ``select(RepairRun).order_by(created_at.desc()).limit(1)`` — no
connector, drift-event or run-id filter. Two connectors repairing at once
could attach a candidate release (``provenance_repair_run_id``), its
verifier report id and the returned ``repair_run_id`` to the *other*
connector's repair row.

The fix threads the id the controller persisted (``LadderResult`` carries
``repair_run_id``; ``_finalize_proposal`` links by it). This test runs two
concurrent ``detect_and_repair_after_sync`` calls on different connectors
and asserts each repair links its own drift, its own candidate release and
its own verifier report — the shape that fails if the unscoped re-fetch
ever comes back. A ``created_at`` tie-break would not fix it, so none is
used here either.
"""

from __future__ import annotations

import threading
from typing import Any

import httpx
from sqlalchemy import select

from autobridge.control.models import (
    Base,
    Connector,
    ConnectorRelease,
    DriftEvent,
    RepairRun,
    engine_from_url,
    session_factory,
    spec_signature,
)
from autobridge.drift.signals import signals_from_sync
from autobridge.engine.detect_repair import (
    _finalize_proposal,
    build_detector_delta,
    detect_and_repair_after_sync,
)
from autobridge.repair.controller import RepairController
from autobridge.runtime.interpreter import OperationResult

_REAL_HTTPX_CLIENT = httpx.Client


def _page_spec(name: str) -> dict[str, Any]:
    """Atlas-like page-paginated spec (pageNumber/pageSize + total)."""
    return {
        "spec_version": 1,
        "connector": {"id": name, "vendor": "atlas"},
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


def _cursor_summary() -> dict[str, Any]:
    """What a real sync sees when the vendor flips page -> cursor silently."""
    wire = [
        {"reservation_id": f"R{i:04d}", "guest_name": f"Guest {i}"}
        for i in range(3)
    ]
    return {
        "outcome": "Complete",
        "unmet_criteria": [],
        "data_readiness": {"freshness": "current", "completeness": "complete"},
        "fingerprint_id": "req040-chain",
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


def _mock_verifier_pass(monkeypatch: Any) -> None:
    """httpx.Client mock returning a passing verifier verdict + regression."""

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(
            200,
            json={
                "verdict": "pass",
                "score": 1.0,
                "failed_checks": [],
                "regression": "pass",
                "scenario_id": "baseline",
                "mode": "visible",
            },
        )

    transport = httpx.MockTransport(handler)

    class FakeClient:
        def __init__(self, *a: Any, **kw: Any) -> None:  # noqa: ARG001, ARG002
            self._client = _REAL_HTTPX_CLIENT(transport=transport)

        def __enter__(self) -> httpx.Client:
            return self._client

        def __exit__(self, *a: Any) -> None:  # noqa: ARG001, ARG002
            self._client.close()

        def post(self, *a: Any, **kw: Any) -> httpx.Response:
            return self._client.post(*a, **kw)

    monkeypatch.setattr(httpx, "Client", FakeClient)


def _factory(tmp_path: Any) -> Any:
    engine = engine_from_url(f"sqlite:///{tmp_path}/req040.db")
    Base.metadata.create_all(engine)
    return session_factory(engine)


def _seed(factory: Any, name: str) -> tuple[str, str, dict[str, Any]]:
    spec = _page_spec(name)
    with factory() as s:
        conn = Connector(
            name=name,
            base_url="http://localhost:8105",
            credential_ref="ATLAS_API_KEY",
            target_entities=["Reservation"],
            runtime_target={"kind": "local_process"},
        )
        s.add(conn)
        s.flush()
        rel = ConnectorRelease(
            connector_id=conn.id,
            version=1,
            spec=spec,
            signature=spec_signature(spec),
        )
        s.add(rel)
        s.flush()
        conn.active_release_id = rel.id
        s.commit()
        return conn.id, rel.id, spec


def test_two_concurrent_repairs_link_own_candidate_and_report(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """Concurrent sync repairs never cross-link provenance (REQ-040).

    Two threads run the full ``detect_and_repair_after_sync`` path on
    different connectors at the same time, verifier gate passing. Each
    returned ``repair_run_id`` must name the row carrying that thread's
    drift; each candidate release's ``provenance_repair_run_id`` and each
    repair row's ``verifier_report_id`` must point at the run's own
    candidate — never at the other connector's rows.
    """
    _mock_verifier_pass(monkeypatch)
    factory = _factory(tmp_path)
    conn_a, rel_a, spec_a = _seed(factory, name="atlas-a")
    conn_b, rel_b, spec_b = _seed(factory, name="atlas-b")

    gate = threading.Barrier(2)
    outs: dict[str, Any] = {}
    errors: list[str] = []

    def _run(
        key: str, conn_id: str, rel_id: str, spec: dict[str, Any], sync_id: str
    ) -> None:
        try:
            gate.wait(timeout=30.0)
            with factory() as s:
                outs[key] = detect_and_repair_after_sync(
                    s,
                    connector_id=conn_id,
                    release_id=rel_id,
                    spec_dict=spec,
                    summary=_cursor_summary(),
                    sync_run_id=sync_id,
                    verifier_url="http://verifier:8000",
                )
                s.commit()
        except Exception as exc:  # noqa: BLE001 — surfaced below
            errors.append(f"{key}: {type(exc).__name__}: {exc}")

    threads = [
        threading.Thread(
            target=_run, args=("a", conn_a, rel_a, spec_a, "sync-a"), daemon=True
        ),
        threading.Thread(
            target=_run, args=("b", conn_b, rel_b, spec_b, "sync-b"), daemon=True
        ),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60.0)
    assert not any(t.is_alive() for t in threads), "repair threads did not finish"
    assert not errors, errors
    out_a, out_b = outs["a"], outs["b"]
    for out in (out_a, out_b):
        assert out["drift_found"] is True
        assert out["decision"] == "propose"
        assert out["rule_id"] == "page_to_cursor"
        assert out["candidate_release_id"], "propose must persist a candidate"
        assert out["gate_error"] is None
    assert out_a["repair_run_id"] != out_b["repair_run_id"]
    assert out_a["candidate_release_id"] != out_b["candidate_release_id"]
    assert out_a["drift_id"] != out_b["drift_id"]

    with factory() as s:
        run_a = s.get(RepairRun, out_a["repair_run_id"])
        run_b = s.get(RepairRun, out_b["repair_run_id"])
        assert run_a is not None and run_b is not None
        # Each repair row carries its own drift — never the other's.
        assert run_a.drift_event_ids == [out_a["drift_id"]]
        assert run_b.drift_event_ids == [out_b["drift_id"]]
        # Each candidate release links its own repair run.
        cand_a = s.get(ConnectorRelease, out_a["candidate_release_id"])
        cand_b = s.get(ConnectorRelease, out_b["candidate_release_id"])
        assert cand_a is not None and cand_b is not None
        assert cand_a.connector_id == conn_a
        assert cand_b.connector_id == conn_b
        assert cand_a.provenance_repair_run_id == run_a.id
        assert cand_b.provenance_repair_run_id == run_b.id
        # Each repair row carries its own candidate's verifier report.
        assert cand_a.verification_report_id, "gate passed: report expected"
        assert cand_b.verification_report_id
        assert run_a.verifier_report_id == cand_a.verification_report_id
        assert run_b.verifier_report_id == cand_b.verification_report_id
        # The drift rows belong to their own connectors.
        drift_a = s.get(DriftEvent, out_a["drift_id"])
        drift_b = s.get(DriftEvent, out_b["drift_id"])
        assert drift_a is not None and drift_a.connector_id == conn_a
        assert drift_b is not None and drift_b.connector_id == conn_b
        # No stray repairs: exactly the two rows, one per connector.
        all_runs = s.execute(select(RepairRun)).scalars().all()
        assert len(all_runs) == 2


def test_interleaved_persists_finalize_on_own_rows(tmp_path: Any, monkeypatch: Any) -> None:
    """Deterministic twin of the race: persist both repairs, then link.

    Both ``RepairRun`` rows exist (flushed, one session) before either
    candidate release is linked — the exact state a concurrent sync lands
    in. Linking by threaded id gives each candidate its own repair; the
    old unscoped "newest row" fetch returns the *same* row for both calls
    (no ``RepairRun`` write happens between them), so one candidate would
    cross-link. No ``created_at`` tie-break is involved either way.
    """
    _mock_verifier_pass(monkeypatch)
    factory = _factory(tmp_path)
    conn_a, rel_a, spec_a = _seed(factory, name="atlas-a")
    conn_b, rel_b, spec_b = _seed(factory, name="atlas-b")

    with factory() as s:
        controller = RepairController()

        def _climb(
            conn_id: str, spec: dict[str, Any], sync_id: str
        ) -> tuple[Any, str]:
            signals = signals_from_sync(
                _cursor_summary(), spec=spec, source_run_id=sync_id
            )
            assert signals, "cursor wire must yield signals"
            delta = build_detector_delta(spec, signals)
            drift = DriftEvent(
                connector_id=conn_id,
                drift_class="PAGINATION_CHANGE",
                status="open",
            )
            s.add(drift)
            s.flush()
            result = controller.handle_from_signals(
                spec=spec,
                signals=list(signals),
                fingerprint_delta=delta,
                drift_event_ids=[drift.id],
                session=s,
            )
            assert result.decision == "propose", result.escalation_reason
            assert result.repair_run_id, "controller must thread the persisted id"
            return result, drift.id

        # Both repairs persist before either candidate links — the race.
        result_a, drift_a_id = _climb(conn_a, spec_a, "sync-a")
        result_b, drift_b_id = _climb(conn_b, spec_b, "sync-b")
        assert result_a.repair_run_id != result_b.repair_run_id

        cand_a_id, err_a = _finalize_proposal(
            s,
            connector_id=conn_a,
            release_id=rel_a,
            repair_run_id=result_a.repair_run_id,
            result=result_a,
            verifier_url="http://verifier:8000",
        )
        cand_b_id, err_b = _finalize_proposal(
            s,
            connector_id=conn_b,
            release_id=rel_b,
            repair_run_id=result_b.repair_run_id,
            result=result_b,
            verifier_url="http://verifier:8000",
        )
        assert err_a is None and err_b is None
        assert cand_a_id and cand_b_id and cand_a_id != cand_b_id
        s.commit()

        run_a = s.get(RepairRun, result_a.repair_run_id)
        run_b = s.get(RepairRun, result_b.repair_run_id)
        cand_a = s.get(ConnectorRelease, cand_a_id)
        cand_b = s.get(ConnectorRelease, cand_b_id)
        assert run_a is not None and run_b is not None
        assert cand_a is not None and cand_b is not None
        assert run_a.drift_event_ids == [drift_a_id]
        assert run_b.drift_event_ids == [drift_b_id]
        assert cand_a.provenance_repair_run_id == run_a.id
        assert cand_b.provenance_repair_run_id == run_b.id
        assert cand_a.verification_report_id
        assert cand_b.verification_report_id
        assert run_a.verifier_report_id == cand_a.verification_report_id
        assert run_b.verifier_report_id == cand_b.verification_report_id
