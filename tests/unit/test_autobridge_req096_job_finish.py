"""REQ-096 item 2/3: the background repair job finishes like the inline sync.

Commit 6460036e added ``finish_climb`` (dry-run gate → candidate release →
verifier report) and calls it from both ``detect_and_repair_after_sync``
and the background repair job (``_run_repair_climb``). Before it, a
deferred L0/L1 proposal never got a candidate release or a verifier
report: the repair page waited forever and Approve had nothing to ship.

Each test below runs the real job handler against a real session with a
fake verifier and no live model call:

a. a deferred job whose climb resolves at L1 (``page_to_cursor``) ends with
   a candidate release, the repair row's ``verifier_report_id`` set, and
   ``candidate_release_id`` in the job result;
b. the same for an L0 knowledge hit (warm cache);
c. a deferred L1 proposal that fails the dry-run gate on the parcel's
   ``dry_run_fault`` becomes the question-only escalation with no
   candidate release and no patch offered, exactly as the inline path does;
d. parity: the same faulted sync summary through
   ``detect_and_repair_after_sync`` (inline) and through the job handler
   gives the same decision, rule, rung and candidate patch;
e. escalate and climbing results are not finalized (no candidate
   release), and a payload missing ``connector_id`` or ``release_id``
   skips finalize without raising.

Item 3 pins demo scene 3's wait: with the repair row landing after a
delay, the step's repair read returns the drift's terminal row (not ``{}``
and not an older row), using a stubbed control API.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from autobridge.control.jobs import JobContext
from autobridge.control.models import (
    Base,
    Connector,
    ConnectorRelease,
    DriftEvent,
    Job,
    RepairRun,
    spec_signature,
)
from autobridge.drift.signals import signals_from_sync
from autobridge.engine.detect_repair import (
    _submit_deferred_model_repair,
    build_detector_delta,
    detect_and_repair_after_sync,
    finish_climb,
)
from autobridge.repair.controller import prepare_climb_inputs, repair_job_handler
from autobridge.repair.dryrun import build_dry_run_fault
from autobridge.runtime.interpreter import OperationResult

_REAL_HTTPX_CLIENT = httpx.Client


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


def _no_live_provider(monkeypatch: Any) -> None:
    import autobridge.llm.provider as provider_mod

    monkeypatch.setattr(
        provider_mod, "resolve_frontier_provider", lambda: (None, "off")
    )


def _page_spec(with_money_check: bool = False) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    if with_money_check:
        checks = [
            {
                "kind": "money_invariant",
                "field": "total_amount",
                "compare_to": "$.gross_sum",
            }
        ]
    fields: dict[str, Any] = {
        "reservation_id": {"path": "$.reservation_id"},
        "guest_name": {"path": "$.guest_name"},
    }
    if with_money_check:
        fields = {
            "reservation_id": {"path": "$.reservation_id"},
            "total_amount": {"path": "$.total_amount"},
        }
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
                "fields": fields,
                "checks": checks,
            }
        },
        "capabilities": {"read": ["Reservation"]},
        "health": {"canary": {"every": "60s", "operation": "list_reservations"}},
    }


def _wire(i: int, money: bool = False) -> dict[str, Any]:
    if money:
        return {"reservation_id": f"R{i:04d}", "total_amount": "100.00"}
    return {"reservation_id": f"R{i:04d}", "guest_name": f"Guest {i}"}


def _summary(money: bool = False) -> dict[str, Any]:
    """A real sync shape: page spec, cursor on the wire, silent drift.

    With ``money=True`` the spec's money check fails on the faulted
    records (page total 300 vs ``gross_sum`` 9999), so the L1 proposal
    fails the dry-run gate; without it the gate passes vacuously.
    """
    wire = [_wire(i, money=money) for i in range(3)]
    raw_page: dict[str, Any] = {"success": True, "data": wire, "next_cursor": "NTA"}
    canonical: dict[str, list[dict[str, Any]]] = {}
    if money:
        raw_page["gross_sum"] = "9999.00"
        canonical = {
            "Reservation": [
                {"reservation_id": f"R{i:04d}", "total_amount": "100.00"}
                for i in range(3)
            ]
        }
    return {
        "outcome": "Complete",
        "unmet_criteria": [],
        "data_readiness": {},
        "fingerprint_id": "req096",
        "operation_results": {
            "list_reservations": OperationResult(
                operation="list_reservations",
                records=wire,
                pages_fetched=1,
                raw_pages=[raw_page],
                stop_reason="no_next_token",
            )
        },
        "records_observed": {"Reservation": 3},
        "canonical_records": canonical,
        "blocked_records": {},
        "invariant_results": [],
        "checkpoint_values": {},
    }


def _mem_factory() -> Any:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed(factory: Any, spec: dict[str, Any]) -> tuple[str, str]:
    with factory() as s:
        conn = Connector(
            name="atlaspms",
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


def _deferred_payload(
    factory: Any,
    spec: dict[str, Any],
    summary: dict[str, Any],
    conn_id: str,
    rel_id: str,
    sync_run_id: str,
) -> tuple[str, dict[str, Any]]:
    """Queue the deferred job exactly as the live sync does; return ids+payload."""
    sigs = signals_from_sync(summary, spec=spec)
    assert any(s.kind == "pagination_changed" for s in sigs), (
        "the test summary must read as a pagination drift"
    )
    delta = build_detector_delta(spec, sigs)
    prepared = prepare_climb_inputs(
        spec=spec,
        signals=list(sigs),
        summary=summary,
        sync_run_id=sync_run_id,
        drift_class="PAGINATION_CHANGE",
    )
    fault = build_dry_run_fault(summary)
    with factory() as s:
        drift = DriftEvent(
            connector_id=conn_id,
            drift_class="PAGINATION_CHANGE",
            severity="high",
            source_sync_run_id=sync_run_id,
            status="open",
        )
        s.add(drift)
        s.commit()
        drift_id = drift.id
    with factory() as s:
        job_id = _submit_deferred_model_repair(
            s,
            drift_id=drift_id,
            drift_class="PAGINATION_CHANGE",
            connector_id=conn_id,
            release_id=rel_id,
            prepared=prepared,
            signals=list(sigs),
            delta=delta,
            dry_run_fault=fault,
        )
        s.commit()
    with factory() as s:
        job = s.get(Job, job_id)
        assert job is not None
        # Strict: the stored parcel itself must round-trip (REQ-095).
        payload = json.loads(json.dumps(job.payload))
    return job_id, payload


def _run_job(factory: Any, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    with factory() as s:
        ctx = JobContext(
            job_id=job_id,
            kind="repair",
            run_id=None,
            payload=payload,
            attempt=1,
            session=s,
            worker_id="w-req096",
        )
        out = repair_job_handler(ctx)
        s.commit()
    return out


def test_a_l1_job_finishes_with_candidate_and_report(
    monkeypatch: Any,
) -> None:
    """Item 2a: deferred L1 climb ends with candidate + verifier report."""
    _mock_verifier_pass(monkeypatch)
    _no_live_provider(monkeypatch)
    monkeypatch.setenv("VERIFIER_URL", "http://verifier:8000")
    factory = _mem_factory()
    spec = _page_spec()
    conn_id, rel_id = _seed(factory, spec)
    summary = _summary()
    job_id, payload = _deferred_payload(factory, spec, summary, conn_id, rel_id, "s-a")
    out = _run_job(factory, job_id, payload)
    assert out["decision"] == "propose", out
    assert out["rule_id"] == "page_to_cursor", out
    assert out["ladder_level_reached"] == 1, out
    assert out["candidate_release_id"], "the job must ship a candidate release"
    assert out["gate_error"] is None, out
    with factory() as s:
        run = s.get(RepairRun, out["repair_run_id"])
        assert run is not None and run.decision == "propose"
        assert run.verifier_report_id, "the repair row carries the verdict"
        cand = s.get(ConnectorRelease, out["candidate_release_id"])
        assert cand is not None
        assert cand.provenance_repair_run_id == run.id
        assert cand.verification_report_id == run.verifier_report_id
        assert cand.spec["operations"]["list_reservations"]["pagination"]["strategy"] == "cursor"


def test_b_l0_job_finishes_with_candidate_and_report(
    monkeypatch: Any,
) -> None:
    """Item 2b: a warm cache takes the job path the same way (L0, 0 calls)."""
    _mock_verifier_pass(monkeypatch)
    _no_live_provider(monkeypatch)
    monkeypatch.setenv("VERIFIER_URL", "http://verifier:8000")
    from autobridge.engine.releases import approve_release

    factory = _mem_factory()
    spec = _page_spec()
    conn_id, rel_id = _seed(factory, spec)
    with factory() as s:
        first = detect_and_repair_after_sync(
            s,
            connector_id=conn_id,
            release_id=rel_id,
            spec_dict=spec,
            summary=_summary(),
            sync_run_id="s-warm",
            verifier_url="http://verifier:8000",
        )
        assert first["decision"] == "propose"
        approve_release(
            s,
            repair_run_id=first["repair_run_id"],
            actor="tester",
            from_release_id=rel_id,
            to_release_id=first["candidate_release_id"],
            connector_id=conn_id,
        )
        s.commit()
    job_id, payload = _deferred_payload(
        factory, spec, _summary(), conn_id, rel_id, "s-b"
    )
    out = _run_job(factory, job_id, payload)
    assert out["decision"] == "propose", out
    assert out["ladder_level_reached"] == 0, "the warmed fix must answer at L0"
    assert out["rule_id"] == "page_to_cursor", out
    assert out["candidate_release_id"], "an L0 proposal ships a candidate too"
    with factory() as s:
        run = s.get(RepairRun, out["repair_run_id"])
        assert run is not None and run.verifier_report_id


def test_c_failing_dryrun_becomes_question_only(
    monkeypatch: Any,
) -> None:
    """Item 2c: an L1 proposal failing the parcel's dry-run escalates.

    The money check fails on the break's records, so the page_to_cursor
    patch fixes nothing the gate judges: both the job (parcel fault) and
    the inline path (same summary) end as the question-only escalation
    with no candidate release and no patch offered.
    """
    _mock_verifier_pass(monkeypatch)
    _no_live_provider(monkeypatch)
    monkeypatch.setenv("VERIFIER_URL", "http://verifier:8000")
    factory = _mem_factory()
    spec = _page_spec(with_money_check=True)
    conn_id, rel_id = _seed(factory, spec)
    summary = _summary(money=True)
    job_id, payload = _deferred_payload(factory, spec, summary, conn_id, rel_id, "s-c")
    out = _run_job(factory, job_id, payload)
    assert out["decision"] == "escalate", out
    assert out.get("candidate_patch") is None, "no patch may be offered"
    assert out["candidate_release_id"] is None, out
    assert "still fail" in (out["gate_error"] or ""), out
    with factory() as s:
        run = s.get(RepairRun, out["repair_run_id"])
        assert run is not None and run.decision == "escalate"
        assert run.candidate_patch is None
        releases = s.query(ConnectorRelease).filter_by(connector_id=conn_id).all()
        assert len(releases) == 1, "no candidate release may persist"
    # The inline path over the same summary decides the same (fresh
    # session state: the job above left the drift open, which would
    # otherwise dedupe the inline detection onto it).
    inline_factory = _mem_factory()
    inline_conn, inline_rel = _seed(inline_factory, spec)
    with inline_factory() as s:
        inline = detect_and_repair_after_sync(
            s,
            connector_id=inline_conn,
            release_id=inline_rel,
            spec_dict=spec,
            summary=_summary(money=True),
            sync_run_id="s-c-inline",
            verifier_url="http://verifier:8000",
        )
        s.commit()
    assert inline["decision"] == "escalate"
    assert inline["candidate_release_id"] is None
    assert inline["gate_error"] == out["gate_error"]
    assert "money_invariant" in (inline["gate_error"] or "")


def test_d_parity_inline_vs_job(monkeypatch: Any) -> None:
    """Item 2d: the same summary decides the same through both paths."""
    _mock_verifier_pass(monkeypatch)
    _no_live_provider(monkeypatch)
    monkeypatch.setenv("VERIFIER_URL", "http://verifier:8000")
    factory = _mem_factory()
    spec = _page_spec()
    conn_id, rel_id = _seed(factory, spec)
    with factory() as s:
        inline = detect_and_repair_after_sync(
            s,
            connector_id=conn_id,
            release_id=rel_id,
            spec_dict=spec,
            summary=_summary(),
            sync_run_id="s-d",
            verifier_url="http://verifier:8000",
        )
        s.commit()
    job_id, payload = _deferred_payload(
        factory, spec, _summary(), conn_id, rel_id, "s-d"
    )
    out = _run_job(factory, job_id, payload)
    for key in ("decision", "rule_id", "ladder_level_reached"):
        assert out[key] == inline[key], (key, out, inline)
    # The inline result carries no patch in its dict (REQ-040 shape); the
    # patch lives on the repair row — that is what must agree.
    with factory() as s:
        inline_run = s.get(RepairRun, inline["repair_run_id"])
        assert inline_run is not None
        assert out["candidate_patch"] == inline_run.candidate_patch
    assert out["candidate_release_id"] and inline["candidate_release_id"]
    assert out["gate_error"] is None and inline["gate_error"] is None


def test_e_escalate_climbing_not_finalized_and_missing_ids_skip(
    monkeypatch: Any,
) -> None:
    """Item 2e: only a propose finalizes; missing ids skip without raising."""
    _mock_verifier_pass(monkeypatch)
    _no_live_provider(monkeypatch)
    monkeypatch.setenv("VERIFIER_URL", "http://verifier:8000")
    factory = _mem_factory()
    spec = _page_spec()
    conn_id, rel_id = _seed(factory, spec)
    # A real escalation (revoked auth: non-fixable, no rule, no patch)
    # finalizes to nothing through the shared post-climb steps.
    from autobridge.repair.controller import RepairController

    with factory() as s:
        ctrl = RepairController()
        res = ctrl.handle(
            spec=spec,
            drift_class="AUTH_REVOKED",
            signals=[],
            fingerprint_delta=None,
            vendor=None,
            drift_event_ids=[],
            session=s,
        )
        s.commit()
        assert res.decision == "escalate"
        assert res.repair_run_id is not None
        _, candidate_id, gate_error = finish_climb(
            s,
            spec_dict=spec,
            result=res,
            connector_id=conn_id,
            release_id=rel_id,
            summary=None,
            verifier_url="http://verifier:8000",
        )
        s.commit()
        assert candidate_id is None and gate_error is None
    # A climbing row is progress, not an answer: never finalized either.
    with factory() as s:
        climbing = SimpleNamespace(
            decision="climbing",
            candidate_patch=[{"op": "replace", "path": "/x", "value": 1}],
            repair_run_id="run-in-flight",
            evidence_ids=[],
            ladder_level_reached=1,
            rule_id=None,
        )
        _, candidate_id, _ = finish_climb(
            s,
            spec_dict=spec,
            result=climbing,  # type: ignore[arg-type]
            connector_id=conn_id,
            release_id=rel_id,
            summary=None,
            verifier_url="http://verifier:8000",
        )
        assert candidate_id is None
    # A payload missing connector_id/release_id skips finalize, no raise.
    summary = _summary()
    job_id, payload = _deferred_payload(factory, spec, summary, conn_id, rel_id, "s-e")
    payload = {k: v for k, v in payload.items() if k not in ("connector_id", "release_id")}
    out = _run_job(factory, job_id, payload)
    assert out["decision"] == "propose", out
    assert out["candidate_release_id"] is None
    assert out["candidate_patch"], "the patch is still derived, just not shipped"
    with factory() as s:
        assert (
            s.query(ConnectorRelease).filter_by(connector_id=conn_id).count() == 1
        ), "no candidate may persist without its release link"


def test_demo_scene3_wait_returns_drifts_terminal_row(
    monkeypatch: Any,
) -> None:
    """Item 3: scene 3 waits for the drift's terminal row.

    The repair lands after a delay, behind an older row for another drift
    and a climbing snapshot for this drift: the poll must return this
    drift's terminal row — never ``{}`` and never the older row.
    """
    import bench.demo as demo_mod

    older = {
        "id": "run-older",
        "drift_event_ids": ["drift-other"],
        "decision": "propose",
    }
    climbing = {
        "id": "run-climbing",
        "drift_event_ids": ["drift-1"],
        "decision": "climbing",
    }
    terminal = {
        "id": "run-terminal",
        "drift_event_ids": ["drift-1"],
        "decision": "propose",
        "rule_id": "page_to_cursor",
    }
    bodies = [[older], [older, climbing], [older, climbing, terminal]]
    calls: list[dict[str, Any]] = []

    def _fake_raw(base_url: str, path: str) -> Any:  # noqa: ARG001
        assert path == "/repairs?connector_id=conn-1"
        return list(bodies.pop(0)) if bodies else [older, climbing, terminal]

    def _fake_call(
        base_url: str,  # noqa: ARG001
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,  # noqa: ARG001
    ) -> dict[str, Any]:
        rec = {"method": method, "path": path}
        calls.append(rec)
        return rec

    monkeypatch.setattr(demo_mod, "_control_get_raw", _fake_raw)
    monkeypatch.setattr(demo_mod, "_control_call", _fake_call)
    out = demo_mod._poll_live_repair_for_drift(
        "http://control:8101", "conn-1", "drift-1", calls, tries=5, wait_s=0
    )
    assert out == terminal, out
    assert out != {} and out["id"] != "run-older"
    assert any(r.get("path") == "/repairs?connector_id=conn-1" for r in calls)
