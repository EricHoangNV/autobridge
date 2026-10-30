"""REQ-042: a model call must never run inside a request or a held transaction.

A semantic drift with a live frontier provider used to climb inline in
``POST /sync`` (and the scheduler): the request outlived the demo's
client timeout while the sync's DB transaction stayed open across the
model call, wedging control. Now the sync records the sync run and the
drift, queues the repair as a job, and returns; the background drain
climbs, committing before and after each model call.

* a fake provider that sleeps 10 s: ``detect_after_sync`` (exactly what
  the sync handler runs) returns in < 2 s with ``repair_pending``; other
  queries answer meanwhile; the drained repair finishes and carries the
  call — plus the same through the scheduler pass.
* two deferred repairs finalize on their own rows (no cross-linking).
"""

from __future__ import annotations

import threading
import time
from typing import Any

from sqlalchemy import select

from autobridge.control.models import (
    Base,
    Connector,
    ConnectorRelease,
    DriftEvent,
    Job,
    ModelCall,
    RepairRun,
    session_factory,
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
        },
        "capabilities": {"read": ["Reservation"]},
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


_DECOY = (
    '[{"op": "replace", '
    '"path": "/entities/Reservation/fields/total_amount/path", '
    '"value": "$.net_amount"}]'
)


class _SleepProvider:
    """Frontier stand-in that blocks like a live model call (REQ-042)."""

    provider_name = "sleep-frontier"

    def __init__(self, seconds: float = 10.0, text: str = _DECOY) -> None:
        self.seconds = seconds
        self.text = text
        self.calls = 0

    def complete(  # noqa: ARG002 — test double mirrors the provider protocol
        self,
        _messages: list[dict[str, Any]],
        *_args: Any,
        **_kwargs: Any,
    ) -> Any:
        from autobridge.llm.provider import Completion, Usage  # noqa: PLC0415

        self.calls += 1
        time.sleep(self.seconds)
        return Completion(
            text=self.text,
            usage=Usage(2000, 115),
            replayed=False,
            captured_at="",
        )

    def model_for_tier(self, tier: str) -> str:
        return f"sleep-{tier}"


def _factory(tmp_path: Any) -> Any:
    from autobridge.control.models import engine_from_url  # noqa: PLC0415

    engine = engine_from_url(f"sqlite:///{tmp_path}/req042.db")
    Base.metadata.create_all(engine)
    return session_factory(engine)


def _seed(factory: Any, name: str = "harborpms") -> tuple[str, str, dict[str, Any]]:
    spec = _money_spec()
    with factory() as s:
        conn = Connector(
            name=name,
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
        return conn.id, rel.id, spec


def _live_sleep(monkeypatch: Any, seconds: float = 10.0) -> _SleepProvider:
    import autobridge.llm.provider as provider_mod  # noqa: PLC0415

    sleepy = _SleepProvider(seconds=seconds)
    monkeypatch.setattr(
        provider_mod, "resolve_frontier_provider", lambda: (sleepy, "live")
    )
    return sleepy


def test_deferred_sync_returns_fast_while_model_call_runs_behind(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """Sleep-10s provider: detect returns < 2 s; control answers meanwhile."""
    from autobridge.control.runner import get_runner  # noqa: PLC0415
    from autobridge.engine.detect_repair import detect_after_sync  # noqa: PLC0415

    sleepy = _live_sleep(monkeypatch, seconds=10.0)
    factory = _factory(tmp_path)
    conn_id, rel_id, spec = _seed(factory)

    with factory() as s:
        started = time.monotonic()
        out = detect_after_sync(
            s,
            connector_id=conn_id,
            release_id=rel_id,
            spec_dict=spec,
            summary=_net_summary(),
            sync_run_id="sync-req042-1",
        )
        elapsed = time.monotonic() - started
        s.commit()
    assert elapsed < 2.0, f"sync path held the caller {elapsed:.1f}s with a live model pending"
    assert out["drift_found"] is True
    assert out["repair_pending"] is True
    assert out["repair_job_id"], out
    assert out["repair_run_id"] is None
    assert out["decision"] is None

    with factory() as s:
        # The drift is recorded; no repair row and no model call yet.
        assert s.execute(select(DriftEvent)).scalars().all(), "drift must be recorded"
        assert s.execute(select(RepairRun)).scalars().all() == []
        assert s.execute(select(ModelCall)).scalars().all() == []
        job = s.get(Job, out["repair_job_id"])
        assert job is not None and job.state == "queued" and job.kind == "repair"

    # The background drain climbs while the caller keeps reading.
    with factory() as s:
        runner = get_runner(s)

    errors: list[str] = []

    def _drain() -> None:
        try:
            runner.run_next_of_kind("repair")
        except Exception as exc:  # noqa: BLE001 — surfaced below
            errors.append(f"{type(exc).__name__}: {exc}")

    worker = threading.Thread(target=_drain, daemon=True)
    worker.start()
    try:
        # Control stays responsive while the 10 s call is in flight:
        # connectors, drift and repairs all answer in < 2 s meanwhile.
        for _ in range(3):
            round_started = time.monotonic()
            with factory() as s:
                assert s.get(Connector, conn_id) is not None
                assert s.execute(select(DriftEvent)).scalars().all()
                s.execute(select(RepairRun)).scalars().all()
            round_elapsed = time.monotonic() - round_started
            assert round_elapsed < 2.0, f"control blocked {round_elapsed:.1f}s during the model call"
            time.sleep(1.0)
    finally:
        worker.join(timeout=30.0)
    assert not worker.is_alive(), "background repair did not finish"
    assert not errors, errors
    assert sleepy.calls == 1, "exactly one live model call"

    with factory() as s:
        runs = s.execute(select(RepairRun)).scalars().all()
        assert len(runs) == 1, "one repair for one break"
        run = runs[0]
        assert run.decision == "escalate", run.decision
        assert [t["level"] for t in run.ladder_trace] == [0, 1, 2, 3, 4]
        l3 = next(t for t in run.ladder_trace if t["level"] == 3)
        assert l3["action"] in ("frontier_generate", "proposal_rejected")
        calls = s.execute(select(ModelCall)).scalars().all()
        assert len(calls) == 1, "the sleep call is in the ledger"
        assert int(run.model_usage.get("frontier_tokens", 0)) > 0
        job = s.get(Job, out["repair_job_id"])
        assert job is not None and job.state == "succeeded", job.state if job else None
        assert job.result.get("repair_run_id") == run.id


def test_scheduler_pass_defers_live_climb_and_stays_responsive(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """Same shape through the scheduler: fast pass, responsive, then done."""
    import autobridge.engine.sync_run as sync_run_mod  # noqa: PLC0415
    from autobridge.control.runner import get_runner  # noqa: PLC0415
    from autobridge.control.scheduler import run_scheduler_pass  # noqa: PLC0415

    monkeypatch.setenv("EDGE_RELAY_URL", "http://127.0.0.1:9")
    sleepy = _live_sleep(monkeypatch, seconds=10.0)
    factory = _factory(tmp_path)
    conn_id, _rel_id, _spec = _seed(factory)
    # The schedulers' vendor HTTP is stubbed at the interpreter boundary:
    # the sync still runs the real detect/classify/queue path.
    monkeypatch.setattr(
        sync_run_mod, "execute_sync", lambda *a, **k: _net_summary()  # noqa: ARG005
    )

    with factory() as s:
        started = time.monotonic()
        report = run_scheduler_pass(s)
        elapsed = time.monotonic() - started
    assert elapsed < 2.0, f"scheduler pass held {elapsed:.1f}s with a live model pending"
    assert report["synced"] == 1, report
    assert report["results"][0].get("repair_pending") is True, report

    with factory() as s:
        runner = get_runner(s)

    worker = threading.Thread(
        target=lambda: runner.run_next_of_kind("repair"), daemon=True
    )
    worker.start()
    try:
        round_started = time.monotonic()
        with factory() as s:
            assert s.get(Connector, conn_id) is not None
        assert time.monotonic() - round_started < 2.0, "control blocked during the model call"
        time.sleep(1.0)
    finally:
        worker.join(timeout=30.0)
    assert not worker.is_alive()
    assert sleepy.calls == 1
    with factory() as s:
        runs = s.execute(select(RepairRun)).scalars().all()
        assert len(runs) == 1 and runs[0].decision == "escalate"


def test_two_deferred_repairs_finalize_on_their_own_rows(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """Concurrent deferred repairs never cross-link (REQ-040 shape)."""
    from autobridge.control.runner import get_runner  # noqa: PLC0415
    from autobridge.engine.detect_repair import detect_after_sync  # noqa: PLC0415

    sleepy = _live_sleep(monkeypatch, seconds=0.5)
    factory = _factory(tmp_path)
    conn_a, rel_a, spec_a = _seed(factory, name="harbor-a")
    conn_b, rel_b, spec_b = _seed(factory, name="harbor-b")

    with factory() as s:
        runner = get_runner(s)
    with factory() as s:
        out_a = detect_after_sync(
            s, connector_id=conn_a, release_id=rel_a, spec_dict=spec_a,
            summary=_net_summary(), sync_run_id="sync-a",
        )
        out_b = detect_after_sync(
            s, connector_id=conn_b, release_id=rel_b, spec_dict=spec_b,
            summary=_net_summary(), sync_run_id="sync-b",
        )
        s.commit()
    assert out_a["repair_job_id"] and out_b["repair_job_id"]
    assert out_a["repair_job_id"] != out_b["repair_job_id"]

    threads = [
        threading.Thread(target=lambda: runner.run_next_of_kind("repair"), daemon=True)
        for _ in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30.0)
    assert not any(t.is_alive() for t in threads)
    assert sleepy.calls == 2

    with factory() as s:
        runs = {r.id: r for r in s.execute(select(RepairRun)).scalars().all()}
        assert len(runs) == 2
        for out in (out_a, out_b):
            job = s.get(Job, out["repair_job_id"])
            assert job is not None and job.state == "succeeded", job.state if job else None
            run = runs[job.result["repair_run_id"]]
            assert set(run.drift_event_ids) == {out["drift_id"]}, (
                run.drift_event_ids, out["drift_id"]
            )
