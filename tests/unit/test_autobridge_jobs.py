"""JobRunner tests (P1.W1.6, plan §4 W1).

Proves the queue executes BuildRun / SyncRun / RepairRun jobs, retries
with a budget, and persists state across a worker restart: a worker
"killed" mid-job leaves a ``running`` row that a fresh runner reclaims
and completes.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy import create_engine

from alembic import command
from autobridge.control import jobs as j
from autobridge.control import models as m
from autobridge.control.jobs import JobContext, JobError, JobRunner
from autobridge.control.models import (
    BuildRun,
    Connector,
    ConnectorRelease,
    Job,
    RepairRun,
    SyncRun,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2]

SPEC: dict[str, Any] = {
    "spec_version": 1,
    "connector": {"id": "harborpms", "vendor": "HarborPMS"},
}


@pytest.fixture()
def sessions(tmp_path: Path) -> Iterator[m.sessionmaker[Session]]:
    engine = create_engine(f"sqlite:///{tmp_path}/jobs.db")
    m.init_db(engine)
    yield m.session_factory(engine)
    engine.dispose()


def _connector_id(sessions: m.sessionmaker[Session]) -> str:
    with sessions() as s:
        row = Connector(
            name="HarborPMS",
            base_url="http://sim-harbor:8000",
            credential_ref="HARBOR_API_KEY",
            target_entities=["Reservation"],
            runtime_target={"kind": "local_process"},
        )
        s.add(row)
        s.commit()
        return row.id


def _release_id(sessions: m.sessionmaker[Session], connector_id: str) -> str:
    with sessions() as s:
        row = ConnectorRelease(
            connector_id=connector_id,
            version=1,
            spec=dict(SPEC),
            signature=m.spec_signature(SPEC),
        )
        s.add(row)
        s.commit()
        return row.id


def _job(sessions: m.sessionmaker[Session], job_id: str) -> Job:
    with sessions() as s:
        row = s.get(Job, job_id)
        assert row is not None
        s.expunge(row)
        return row


def test_build_runs_to_succeeded(sessions: m.sessionmaker[Session]) -> None:
    runner = JobRunner(sessions, worker_id="worker-a")
    job_id = runner.submit_build(_connector_id(sessions), inputs={"docs": "ref"})
    assert runner.run_until_empty() == [job_id]
    job = _job(sessions, job_id)
    assert job.state == "succeeded"
    assert job.attempts == 1
    assert job.worker_id is None
    assert job.result == {"build_run_id": job.run_id, "state": "succeeded"}
    with sessions() as s:
        run = s.get(BuildRun, job.run_id)
        assert run is not None
        assert run.state == "succeeded"
        assert run.result == {"stub": True, "attempt": 1}


def test_retry_then_success(sessions: m.sessionmaker[Session]) -> None:
    calls = {"n": 0}

    def flaky(ctx: JobContext) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError(f"boom {calls['n']}")
        return j.run_build_stub(ctx)

    runner = JobRunner(
        sessions, handlers={"build": flaky}, worker_id="worker-a"
    )
    job_id = runner.submit_build(_connector_id(sessions), max_attempts=3)
    assert runner.run_until_empty() == [job_id, job_id, job_id]
    job = _job(sessions, job_id)
    assert job.state == "succeeded"
    assert job.attempts == 3
    assert job.last_error is None
    assert calls["n"] == 3


def test_exhaustion_marks_failed_and_rolls_back(
    sessions: m.sessionmaker[Session],
) -> None:
    def always_fails(ctx: JobContext) -> dict[str, Any]:
        ctx.session.get(BuildRun, ctx.run_id)
        raise RuntimeError("permanent")

    runner = JobRunner(sessions, handlers={"build": always_fails})
    job_id = runner.submit_build(_connector_id(sessions), max_attempts=2)
    assert runner.run_until_empty() == [job_id, job_id]
    job = _job(sessions, job_id)
    assert job.state == "failed"
    assert job.attempts == 2
    assert "RuntimeError: permanent" in (job.last_error or "")
    with sessions() as s:
        run = s.get(BuildRun, job.run_id)
        assert run is not None
        assert run.state == "queued"  # failed attempts left no partial writes


def test_worker_restart_mid_job(sessions: m.sessionmaker[Session]) -> None:
    """A kill between claim and completion leaves a running row; a fresh
    worker reclaims it and finishes exactly once."""
    real_calls = {"n": 0}
    inner = j.run_build_stub

    def counting(ctx: JobContext) -> dict[str, Any]:
        real_calls["n"] += 1
        return inner(ctx)

    dead = JobRunner(
        sessions, handlers={"build": counting}, worker_id="worker-dead"
    )
    job_id = dead.submit_build(_connector_id(sessions))

    def killed_mid_job(_ctx: JobContext) -> dict[str, Any]:
        raise SystemExit("kill -9")

    dead.register_handler("build", killed_mid_job)
    with pytest.raises(SystemExit, match="kill -9"):
        dead.run_once()

    # The "killed" worker is gone; the claim survived in the DB.
    job = _job(sessions, job_id)
    assert job.state == "running"
    assert job.worker_id == "worker-dead"
    assert job.attempts == 1
    with sessions() as s:
        assert s.get(BuildRun, job.run_id) is not None
        assert s.get(BuildRun, job.run_id).state == "queued"  # type: ignore[union-attr]

    restarted = JobRunner(
        sessions, handlers={"build": counting}, worker_id="worker-alive"
    )
    assert restarted.reclaim_orphaned() == 1
    assert restarted.run_until_empty() == [job_id]
    job = _job(sessions, job_id)
    assert job.state == "succeeded"
    assert job.attempts == 2  # one lost claim + one real execution
    assert real_calls["n"] == 1
    with sessions() as s:
        assert s.get(BuildRun, job.run_id).state == "succeeded"  # type: ignore[union-attr]


def test_reclaim_respects_live_workers(sessions: m.sessionmaker[Session]) -> None:
    runner = JobRunner(sessions, worker_id="worker-a")
    job_id = runner.submit_build(_connector_id(sessions))
    with sessions() as s:
        row = s.get(Job, job_id)
        assert row is not None
        row.state = "running"
        row.worker_id = "worker-a"
        row.attempts = 1
        s.commit()
    assert runner.reclaim_orphaned(active_worker_ids=("worker-a",)) == 0
    assert _job(sessions, job_id).state == "running"
    assert runner.reclaim_orphaned(active_worker_ids=("worker-b",)) == 1
    assert _job(sessions, job_id).state == "queued"


def test_sync_and_repair_stubs(sessions: m.sessionmaker[Session]) -> None:
    runner = JobRunner(sessions)
    connector_id = _connector_id(sessions)
    release_id = _release_id(sessions, connector_id)
    sync_id = runner.submit_sync(release_id, checkpoint={"cursor": "abc"})
    repair_id = runner.submit_repair(["drift-1", "drift-2"])
    assert runner.run_until_empty() == [sync_id, repair_id]

    sync_job = _job(sessions, sync_id)
    assert sync_job.state == "succeeded"
    assert sync_job.run_id is not None  # backfilled by the handler
    with sessions() as s:
        run = s.get(SyncRun, sync_job.run_id)
        assert run is not None
        assert run.outcome == "Blocked"
        assert run.unmet_criteria == ["stub_executor_no_runtime"]
        assert run.checkpoint == {"cursor": "abc"}

    repair_job = _job(sessions, repair_id)
    assert repair_job.state == "succeeded"
    with sessions() as s:
        run = s.get(RepairRun, repair_job.run_id)
        assert run is not None
        assert run.decision == "escalate"
        assert run.drift_event_ids == ["drift-1", "drift-2"]


def test_stub_guards_fail_loudly(sessions: m.sessionmaker[Session]) -> None:
    runner = JobRunner(sessions, worker_id="worker-a")
    assert runner.worker_id == "worker-a"

    # BuildRun row deleted between submit and run.
    job_id = runner.submit_build(_connector_id(sessions), max_attempts=1)
    with sessions() as s:
        run = s.get(BuildRun, _job(sessions, job_id).run_id)
        assert run is not None
        s.delete(run)
        s.commit()
    runner.run_once()
    job = _job(sessions, job_id)
    assert job.state == "failed"  # attempts (1) exhausted max (1)
    assert "not found" in (job.last_error or "")

    # BuildRun row tampered out of 'queued'.
    job_id2 = runner.submit_build(_connector_id(sessions), max_attempts=1)
    with sessions() as s:
        run = s.get(BuildRun, _job(sessions, job_id2).run_id)
        assert run is not None
        run.state = "running"
        s.commit()
    runner.run_once()
    job2 = _job(sessions, job_id2)
    assert job2.state == "failed"
    assert "not 'queued'" in (job2.last_error or "")

    # Sync job with no release ref in the payload.
    with sessions() as s:
        bad = Job(kind="sync", state="queued", max_attempts=1, payload={})
        s.add(bad)
        s.commit()
        bad_id = bad.id
    runner.run_once()
    assert "needs 'release_id'" in (_job(sessions, bad_id).last_error or "")


def test_run_once_idle_returns_none(sessions: m.sessionmaker[Session]) -> None:
    assert JobRunner(sessions).run_once() is None
    assert JobRunner(sessions).run_until_empty() == []


def test_submit_validation(sessions: m.sessionmaker[Session]) -> None:
    runner = JobRunner(sessions)
    with pytest.raises(JobError, match="Connector 'nope' not found"):
        runner.submit_build("nope")
    with pytest.raises(JobError, match="ConnectorRelease 'nope' not found"):
        runner.submit_sync("nope")
    with pytest.raises(JobError, match="max_attempts"):
        runner.submit_build(_connector_id(sessions), max_attempts=0)
    with pytest.raises(JobError, match="unknown job kind"):
        runner.register_handler("teleport", j.run_build_stub)


def test_missing_handler_fails_job(sessions: m.sessionmaker[Session]) -> None:
    runner = JobRunner(sessions, handlers={})
    job_id = runner.submit_build(_connector_id(sessions), max_attempts=1)
    assert runner.run_once() == job_id
    job = _job(sessions, job_id)
    assert job.state == "failed"
    assert "no handler registered for kind 'build'" in (job.last_error or "")


def test_non_dict_result_fails_job(sessions: m.sessionmaker[Session]) -> None:
    def bad(_ctx: JobContext) -> dict[str, Any]:
        return ["not", "a", "dict"]  # type: ignore[return-value]

    runner = JobRunner(sessions, handlers={"build": bad}, worker_id="w")
    job_id = runner.submit_build(_connector_id(sessions), max_attempts=1)
    runner.run_once()
    job = _job(sessions, job_id)
    assert job.state == "failed"
    assert "must return a dict" in (job.last_error or "")


def test_payload_carries_refs_not_secrets(
    sessions: m.sessionmaker[Session],
) -> None:
    """The job row names the credential env key at most; never its value."""
    import os

    secret = "super-secret-value"
    os.environ["HARBOR_API_KEY"] = secret
    try:
        runner = JobRunner(sessions)
        job_id = runner.submit_build(_connector_id(sessions))
        runner.run_until_empty()
        job = _job(sessions, job_id)
        assert secret not in repr(job.payload)
        assert secret not in repr(job.result)
    finally:
        del os.environ["HARBOR_API_KEY"]


def test_job_vocab_rejects_unknowns() -> None:
    with pytest.raises(ValueError, match="kind"):
        Job(kind="teleport", state="queued")
    with pytest.raises(ValueError, match="state"):
        Job(kind="build", state="melting")


def test_migration_0002_job_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "jobs-mig.db"
    monkeypatch.setenv(m.DATABASE_URL_ENV, f"sqlite:///{db}")
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(cfg, "head")
    engine = create_engine(f"sqlite:///{db}")
    try:
        with engine.connect() as conn:
            assert "jobs" in sa.inspect(conn).get_table_names()
            checks = [
                str(c["sqltext"])
                for c in sa.inspect(conn).get_check_constraints("jobs")
            ]
        assert any("kind" in c for c in checks)
        assert any("state" in c for c in checks)
        with m.session_factory(engine)() as s:
            s.add(Job(kind="build", state="queued", max_attempts=3))
            s.commit()
            assert s.query(Job).count() == 1
        command.downgrade(cfg, "0001_core_data_model")
        with engine.connect() as conn:
            assert "jobs" not in sa.inspect(conn).get_table_names()
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            assert "jobs" in sa.inspect(conn).get_table_names()
    finally:
        engine.dispose()
