"""REQ-072 — a stack restart mid-sync must not wedge a connector.

1. Startup fails orphaned sync/build jobs (no drain would ever claim them)
   with a reason; orphaned repairs are re-queued for the repair drain
   (consistent with ``fail_orphaned_repairs`` failing the old climbing row).
2. ``connector_busy_reason`` ignores a queued/running sync older than the
   stale bound, and ``run_scheduler_pass`` fails it so the tick syncs anyway.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import create_engine

from autobridge.control import models as m
from autobridge.control.jobs import JobRunner
from autobridge.control.models import BuildRun, Connector, ConnectorRelease, Job

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.orm import Session


@pytest.fixture()
def sessions(tmp_path: Path) -> Iterator[m.sessionmaker[Session]]:
    engine = create_engine(f"sqlite:///{tmp_path}/req072.db")
    m.init_db(engine)
    yield m.session_factory(engine)
    engine.dispose()


def _connector(sessions: m.sessionmaker[Session]) -> tuple[str, str]:
    with sessions() as s:
        conn = Connector(
            name="MeridianPMS",
            base_url="http://127.0.0.1:9",
            credential_ref="MERIDIAN_API_KEY",
        )
        s.add(conn)
        s.flush()
        rel = ConnectorRelease(
            connector_id=conn.id,
            version=1,
            spec={"spec_version": 1, "connector": {"id": "m", "vendor": "m"}},
            signature="sig",
        )
        s.add(rel)
        s.flush()
        conn.active_release_id = rel.id
        cid, rid = conn.id, rel.id
        s.commit()
        return cid, rid


def test_startup_fails_sync_and_build_requeues_repair(
    sessions: m.sessionmaker[Session],
) -> None:
    """Orphaned sync/build fail; orphaned repair is re-queued for the drain."""
    from autobridge.control.jobs import (
        ORPHANED_BUILD_FAILED_REASON,
        ORPHANED_SYNC_FAILED_REASON,
    )

    cid, rid = _connector(sessions)
    runner = JobRunner(sessions, worker_id="worker-new")
    with sessions() as s:
        # Sync orphans: one running (killed mid-sync), one queued (submitter died).
        s.add(Job(kind="sync", state="running", worker_id="w-old", attempts=1,
                  payload={"release_id": rid}))
        s.add(Job(kind="sync", state="queued", attempts=0,
                  payload={"release_id": rid}))
        # Build orphans with BuildRun rows left mid-flight.
        for state in ("running", "queued"):
            br = BuildRun(connector_id=cid, state=state)
            s.add(br)
            s.flush()
            s.add(Job(kind="build", run_id=br.id, state=state,
                      worker_id="w-old" if state == "running" else None,
                      attempts=1, payload={"connector_id": cid}))
        # Repair orphans: running (to re-queue) + queued (drain claims later).
        s.add(Job(kind="repair", state="running", worker_id="w-old", attempts=1,
                  payload={"drift_event_ids": ["d1"]}))
        s.add(Job(kind="repair", state="queued", attempts=0,
                  payload={"drift_event_ids": ["d2"]}))
        s.commit()

    failed = runner.fail_orphaned_sync_and_build_jobs()
    assert failed == {"sync": 2, "build": 2}

    with sessions() as s:
        syncs = s.query(Job).filter(Job.kind == "sync").all()
        assert all(j.state == "failed" for j in syncs)
        assert all((j.last_error or "") == ORPHANED_SYNC_FAILED_REASON for j in syncs)
        assert all(j.worker_id is None for j in syncs)
        builds = s.query(Job).filter(Job.kind == "build").all()
        assert all(j.state == "failed" for j in builds)
        assert all((j.last_error or "") == ORPHANED_BUILD_FAILED_REASON for j in builds)
        for b in builds:
            br = s.get(BuildRun, b.run_id)
            assert br is not None and br.state == "failed"
            assert br.result and br.result.get("orphaned") is True
        repairs = {j.state for j in s.query(Job).filter(Job.kind == "repair").all()}
        assert repairs == {"running", "queued"}  # untouched

    # The restart then re-queues only the running repair for the drain.
    assert runner.reclaim_orphaned() == 1
    with sessions() as s:
        assert {j.state for j in s.query(Job).filter(Job.kind == "repair").all()} == {"queued"}
        assert s.query(Job).filter(Job.kind == "sync", Job.state == "queued").count() == 0
        assert s.query(Job).filter(Job.kind == "build", Job.state == "queued").count() == 0


def test_startup_entrypoint_fails_sync_requeues_repair(
    sessions: m.sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``reclaim_orphaned_jobs`` (lifespan) never leaves a re-queued sync."""
    import autobridge.control.app as app_mod
    import autobridge.control.runner as runner_mod

    _, rid = _connector(sessions)
    runner = JobRunner(sessions, worker_id="worker-new")
    with sessions() as s:
        s.add(Job(kind="sync", state="running", worker_id="w-old", attempts=1,
                  payload={"release_id": rid}))
        s.add(Job(kind="repair", state="running", worker_id="w-old", attempts=1,
                  payload={"drift_event_ids": ["d1"]}))
        s.commit()
    monkeypatch.setattr(runner_mod, "get_runner", lambda _db: runner)
    assert app_mod.reclaim_orphaned_jobs() == 1  # the repair, re-queued
    with sessions() as s:
        sync = s.query(Job).filter(Job.kind == "sync").one()
        assert sync.state == "failed"
        assert "restart" in (sync.last_error or "")
        assert "next scheduled sync" in (sync.last_error or "")
        repair = s.query(Job).filter(Job.kind == "repair").one()
        assert repair.state == "queued"


def test_busy_reason_ignores_stale_sync_but_reports_fresh(
    sessions: m.sessionmaker[Session],
) -> None:
    """Fresh sync blocks; stale sync (no live worker) does not."""
    from autobridge.control.scheduler import connector_busy_reason

    cid, rid = _connector(sessions)
    now = datetime.now(UTC)
    with sessions() as s:
        stale = Job(kind="sync", state="queued", payload={"release_id": rid})
        s.add(stale)
        s.commit()
        stale_id = stale.id
    with sessions() as s:
        row = s.get(Job, stale_id)
        assert row is not None
        row.updated_at = now - timedelta(seconds=3600)
        s.commit()
    with sessions() as s:
        assert connector_busy_reason(s, cid, stale_after_s=60.0, now=now) is None
        # A fresh job still wedges (until the pass fails it if it goes stale).
        s.add(Job(kind="sync", state="queued", payload={"release_id": rid}))
        s.commit()
    with sessions() as s:
        reason = connector_busy_reason(s, cid, stale_after_s=60.0, now=now)
        assert reason is not None and "sync" in reason and "queued" in reason


def test_scheduler_pass_fails_stale_sync_and_syncs_anyway(
    sessions: m.sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stale row is failed with a reason; the tick syncs the connector."""
    from autobridge.control.scheduler import run_scheduler_pass

    monkeypatch.setenv("SIMULATOR_ADMIN_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("EDGE_RELAY_URL", "http://127.0.0.1:9")
    cid, rid = _connector(sessions)
    with sessions() as s:
        stale = Job(kind="sync", state="queued", payload={"release_id": rid})
        s.add(stale)
        s.commit()
        stale_id = stale.id
    with sessions() as s:
        row = s.get(Job, stale_id)
        assert row is not None
        row.updated_at = datetime.now(UTC) - timedelta(seconds=3600)
        s.commit()

    with sessions() as s:
        out = run_scheduler_pass(s, stale_after_s=60.0)
    assert out["stale_failed"] == 1, out
    assert out["synced"] == 1, out
    assert out["results"][0]["status"] == "synced"
    with sessions() as s:
        row = s.get(Job, stale_id)
        assert row is not None and row.state == "failed"
        assert "stale" in (row.last_error or "")


def test_run_job_claims_exact_job_not_oldest_queued(
    sessions: m.sessionmaker[Session],
) -> None:
    """``run_job`` (build/sync path, REQ-072) never claims another job."""
    seen: list[str] = []

    def repair_handler(_ctx: object) -> dict[str, object]:
        seen.append("repair-ran")
        return {"ok": True}

    runner = JobRunner(sessions, handlers={"repair": repair_handler}, worker_id="w")
    _, rid = _connector(sessions)
    with sessions() as s:
        first = Job(kind="repair", state="queued", payload={"drift_event_ids": ["d1"]})
        s.add(first)
        s.commit()
        first_id = first.id
    build_job = runner.submit_sync(rid)
    assert runner.run_job(build_job) is True  # stub sync handler runs; repair untouched
    assert seen == []
    with sessions() as s:
        assert s.get(Job, first_id).state == "queued"  # type: ignore[union-attr]
