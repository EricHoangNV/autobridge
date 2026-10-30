"""Shared job-runner + inline sync execution (REQ-030 item 1).

``POST /connectors/{id}/sync`` and the sync scheduler run the same code:
validate the connector/release, submit the sync job, execute exactly
that job inline (detect → classify → deterministic repair; a live-model
climb is queued as a background repair job, REQ-042), and return the
job result. One implementation, two callers — the scheduler must never
drift from what a manual sync does.
"""

from __future__ import annotations

import contextlib
from typing import Any

from sqlalchemy import select


class SyncError(ValueError):
    """A sync that cannot run; carries the HTTP status the route maps it to."""

    def __init__(self, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.status_code = status_code


def get_runner(db: Any = None) -> Any:
    """JobRunner bound to *db*'s engine (or the global session factory).

    Real handlers (build/sync/repair) are registered when importable;
    otherwise the W1.6 stubs stay (unit tests without the engine).
    """
    from sqlalchemy.orm import sessionmaker as _sm  # noqa: PLC0415

    from autobridge.control.deps import get_session_factory  # noqa: PLC0415
    from autobridge.control.jobs import JobRunner  # noqa: PLC0415

    session_factory = None
    if db is not None:
        try:
            bind = db.get_bind()
            if bind is not None:
                session_factory = _sm(bind=bind, expire_on_commit=False)
        except Exception:
            pass
    if session_factory is None:
        session_factory = get_session_factory()
    runner = JobRunner(session_factory)
    _register_real_handlers(runner)
    return runner


def _register_real_handlers(runner: Any) -> None:
    """Replace stub executors with the real engine handlers when available."""
    try:
        from autobridge.engine.build_run import run_build_handler  # noqa: PLC0415

        runner.register_handler("build", run_build_handler)
    except Exception:
        pass
    try:
        from autobridge.engine.sync_run import run_sync_handler  # noqa: PLC0415

        runner.register_handler("sync", run_sync_handler)
    except Exception:
        pass
    try:
        from autobridge.repair.controller import repair_job_handler  # noqa: PLC0415

        runner.register_handler("repair", repair_job_handler)
    except Exception:
        pass


def sync_connector_now(
    session: Any,
    connector_id: str,
    *,
    release_id: str | None = None,
    checkpoint: dict[str, Any] | None = None,
    spec_vocabulary: str | None = None,
) -> dict[str, Any]:
    """Validate, submit and inline-execute one sync; return the job result.

    Executes exactly the submitted sync job (``run_job``). The sync
    records its SyncRun row and any drift, and repairs deterministic
    drifts inline; a semantic drift needing a live frontier call is
    queued as a background repair job (REQ-042) — this call never runs
    a model call and never holds a transaction across one.

    Raises :class:`SyncError` (404 unknown connector/release, 422
    unrunnable release) or ``RuntimeError`` (500 job store failure) —
    the route maps these to HTTP responses; the scheduler records them
    as per-connector results.
    """
    from autobridge.control.models import (  # noqa: PLC0415
        Approval,
        BuildRun,
        Connector,
        ConnectorRelease,  # noqa: PLC0415
    )
    from autobridge.control.models import Job as JobModel  # noqa: PLC0415

    conn = session.get(Connector, connector_id)
    if conn is None:
        raise SyncError(f"Connector {connector_id!r} not found", status_code=404)

    active_id = release_id or conn.active_release_id
    if not active_id:
        # AR-1-2: no fallback to the latest release. The latest release may
        # be an unapproved (or gate-blocked) candidate that must never
        # execute. Sync requires an explicitly chosen or activated release.
        raise SyncError(
            "connector has no active release to sync "
            "(sync requires an approved, active release)"
        )

    rel_obj = session.get(ConnectorRelease, active_id)
    if rel_obj is None:
        raise SyncError(f"Release {active_id!r} not found", status_code=404)
    if rel_obj.connector_id != connector_id:
        raise SyncError("release belongs to a different connector")
    # REQ-013: an explicitly named release runs only when it is runnable —
    # the active release or an operator-accepted (approved/rollback) one.
    # Anything else (an unapproved candidate, a rejected release) is refused.
    # Gate-blocked builds persist no release (gate-before-persist), so they
    # can never be named here; a gate_blocked provenance flag is still
    # refused explicitly (fail closed).
    if rel_obj.provenance_build_run_id is not None:
        brun = session.get(BuildRun, rel_obj.provenance_build_run_id)
        if brun is not None and isinstance(brun.result, dict) and brun.result.get("gate_blocked"):
            raise SyncError(
                f"Release {active_id!r} was blocked by the verifier gate and cannot sync",
            )
    if active_id != conn.active_release_id:
        accepted = (
            session.execute(
                select(Approval).where(
                    Approval.to_release_id == active_id,
                    Approval.decision.in_(("approve", "rollback")),
                )
            )
            .scalars()
            .first()
        )
        if accepted is None:
            raise SyncError(
                f"Release {active_id!r} is not active or approved for connector "
                f"{connector_id!r} (sync needs the active release or an approved one; "
                "activate a build candidate or approve a repair candidate first)",
            )

    runner = get_runner(session)
    try:
        job_id = runner.submit_sync(
            release_id=active_id,
            checkpoint=checkpoint or {},
            spec_vocabulary=spec_vocabulary,
        )
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc

    with contextlib.suppress(Exception):
        # REQ-042: run exactly the sync job just submitted — never
        # run_once() (which would claim the oldest queued job, possibly a
        # repair whose model call would stall this request).
        runner.run_job(job_id)

    job = session.get(JobModel, job_id)
    if job is None:
        raise RuntimeError("job not found after submit")
    sync_run_id = job.run_id
    if job.result and isinstance(job.result, dict):
        sync_run_id = job.result.get("sync_run_id", sync_run_id)

    out: dict[str, Any] = {
        "job_id": job_id,
        "sync_run_id": sync_run_id,
        "state": job.state,
        "release_id": active_id,
    }
    if job.result and isinstance(job.result, dict):
        out.update(job.result)
    return out
