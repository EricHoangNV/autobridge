"""DB-backed JobRunner (P1.W1.6, plan §4 W1).

Executes ``BuildRun`` / ``SyncRun`` / ``RepairRun`` jobs with retries, and
persists every state transition to the ``jobs`` table so a worker killed
mid-job can be restarted and resume:

- ``submit_*`` inserts the domain row (build) or records its refs in the
  job payload (sync / repair) and inserts a ``queued`` job row.
- ``run_once`` atomically claims the oldest ``queued`` job (one
  ``UPDATE ... WHERE state='queued'`` so two workers cannot double-claim),
  commits the claim, then executes the registered handler in a fresh
  session. A failed attempt rolls back the handler's partial writes and
  either re-queues (``attempts < max_attempts``) or marks ``failed`` with
  ``last_error`` — failures are data on the row, never raised.
- ``reclaim_orphaned`` moves ``running`` rows left behind by a dead worker
  back to ``queued`` with ``attempts`` preserved; the restarted worker then
  re-runs them via ``run_until_empty``.

Standing rules: handlers receive the DB session, never credential values
(the job payload carries ids and refs only; ``credential_ref`` stays on
the ``Connector`` row and the secret is read from the environment at
runtime). The stub handlers below drive each run row to a terminal state
so the queue wiring is proven; W3/W4/W5 replace them with the real
runtime, repair controller and bootstrap.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, cast

from sqlalchemy import func, select, update

from autobridge.control.models import (
    JOB_KINDS,
    BuildRun,
    Connector,
    ConnectorRelease,
    Job,
    RepairRun,
    SyncRun,
    utcnow,
)

if TYPE_CHECKING:
    from sqlalchemy import CursorResult
    from sqlalchemy.orm import Session, sessionmaker

#: Default retry budget per job when the submit call does not say otherwise.
DEFAULT_MAX_ATTEMPTS = 3

#: BuildRun lifecycle owned by the runner (stored free-form on the row).
BUILD_QUEUED = "queued"
BUILD_RUNNING = "running"
BUILD_SUCCEEDED = "succeeded"
BUILD_FAILED = "failed"


class JobError(Exception):
    """Raised for bad submissions (unknown kind, missing row, bad budget)."""


@dataclass
class JobContext:
    """What a handler gets: identity, payload, attempt count and a session.

    ``run_id`` starts as the job's domain-row id (None for sync/repair
    jobs whose row does not exist yet). A handler that creates its domain
    row at execution time assigns ``ctx.run_id`` and the runner writes it
    back to the job row on success.
    """

    job_id: str
    kind: str
    run_id: str | None
    payload: dict[str, Any]
    attempt: int
    session: Session
    worker_id: str
    result: dict[str, Any] = field(default_factory=dict)


class JobHandler(Protocol):
    """One execution attempt of a job; returns the JSON-able result."""

    def __call__(self, ctx: JobContext) -> dict[str, Any]: ...


@dataclass
class _Claim:
    job_id: str
    kind: str
    run_id: str | None
    payload: dict[str, Any]
    attempt: int
    max_attempts: int


def _worker_id() -> str:
    return f"worker-{uuid.uuid4().hex[:8]}"


def run_build_stub(ctx: JobContext) -> dict[str, Any]:
    """W1 stub: drives the BuildRun row queued → running → succeeded."""
    run = ctx.session.get(BuildRun, ctx.run_id)
    if run is None:
        raise JobError(f"BuildRun {ctx.run_id!r} not found")
    if run.state != BUILD_QUEUED:
        raise JobError(f"BuildRun {run.id!r} is {run.state!r}, not 'queued'")
    run.state = BUILD_RUNNING
    ctx.session.flush()
    run.state = BUILD_SUCCEEDED
    run.result = {"stub": True, "attempt": ctx.attempt}
    return {"build_run_id": run.id, "state": BUILD_SUCCEEDED}


def run_sync_stub(ctx: JobContext) -> dict[str, Any]:
    """W1 stub: inserts a Blocked SyncRun (real runtime arrives in W3)."""
    release_id = ctx.payload.get("release_id")
    if not release_id:
        raise JobError("sync job payload needs 'release_id'")
    run = SyncRun(
        release_id=release_id,
        outcome="Blocked",
        unmet_criteria=["stub_executor_no_runtime"],
        records_observed={},
        records_emitted={},
        checkpoint=dict(ctx.payload.get("checkpoint", {})),
        invariant_results=[],
        data_readiness={"stub": True},
    )
    ctx.session.add(run)
    ctx.session.flush()
    ctx.run_id = run.id
    return {"sync_run_id": run.id, "outcome": "Blocked"}


def run_repair_stub(ctx: JobContext) -> dict[str, Any]:
    """W1 stub: inserts an escalating RepairRun (real ladder arrives in W4)."""
    drift_ids = ctx.payload.get("drift_event_ids", [])
    run = RepairRun(
        drift_event_ids=list(drift_ids),
        ladder_level_reached=0,
        decision="escalate",
        model_usage={},
    )
    ctx.session.add(run)
    ctx.session.flush()
    ctx.run_id = run.id
    return {"repair_run_id": run.id, "decision": "escalate"}


def default_handlers() -> dict[str, JobHandler]:
    """Stub executors per kind (W3/W4/W5 replace them with the real ones)."""
    return {
        "build": run_build_stub,
        "sync": run_sync_stub,
        "repair": run_repair_stub,
    }


class JobRunner:
    """Executes run jobs against a session factory with DB-backed retries."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        handlers: dict[str, JobHandler] | None = None,
        worker_id: str | None = None,
    ) -> None:
        self._sessions = sessions
        self._handlers: dict[str, JobHandler] = (
            dict(handlers) if handlers is not None else default_handlers()
        )
        self._worker_id = worker_id or _worker_id()

    @property
    def worker_id(self) -> str:
        return self._worker_id

    def register_handler(self, kind: str, handler: JobHandler) -> None:
        """Override the executor for one of ``build`` / ``sync`` / ``repair``."""
        if kind not in JOB_KINDS:
            raise JobError(f"unknown job kind {kind!r}")
        self._handlers[kind] = handler

    # -- submit --------------------------------------------------------

    def submit_build(
        self,
        connector_id: str,
        inputs: dict[str, Any] | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> str:
        """Queue a build: creates the BuildRun row (``queued``) + job row."""
        self._check_budget(max_attempts)
        with self._sessions() as session:
            if session.get(Connector, connector_id) is None:
                raise JobError(f"Connector {connector_id!r} not found")
            run = BuildRun(connector_id=connector_id, state=BUILD_QUEUED)
            if inputs:
                run.inputs = dict(inputs)
            session.add(run)
            session.flush()
            job = Job(
                kind="build",
                run_id=run.id,
                state="queued",
                max_attempts=max_attempts,
                payload={"connector_id": connector_id},
            )
            session.add(job)
            session.commit()
            return job.id

    def submit_sync(
        self,
        release_id: str,
        checkpoint: dict[str, Any] | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> str:
        """Queue a sync: the SyncRun row is created by the handler at run time."""
        self._check_budget(max_attempts)
        with self._sessions() as session:
            if session.get(ConnectorRelease, release_id) is None:
                raise JobError(f"ConnectorRelease {release_id!r} not found")
            job = Job(
                kind="sync",
                run_id=None,
                state="queued",
                max_attempts=max_attempts,
                payload={
                    "release_id": release_id,
                    "checkpoint": dict(checkpoint or {}),
                },
            )
            session.add(job)
            session.commit()
            return job.id

    def submit_repair(
        self,
        drift_event_ids: list[str],
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> str:
        """Queue a repair: the RepairRun row is created by the handler."""
        self._check_budget(max_attempts)
        with self._sessions() as session:
            job = Job(
                kind="repair",
                run_id=None,
                state="queued",
                max_attempts=max_attempts,
                payload={"drift_event_ids": list(drift_event_ids)},
            )
            session.add(job)
            session.commit()
            return job.id

    # -- run -----------------------------------------------------------

    def run_once(self) -> str | None:
        """Claim and execute one job; returns its id, or None when idle.

        Handler failures are recorded on the job row (re-queued or failed),
        never raised — except ``JobError`` for a missing handler, which is
        also recorded as a terminal failure, not raised.
        """
        for _ in range(3):  # tolerate a lost claim race, then stop
            with self._sessions() as session:
                claim = self._claim(session)
            if claim is not None:
                return self._execute(claim)
            if not self._has_queued():
                return None
        return None

    def run_until_empty(self) -> list[str]:
        """Run jobs until none are queued; returns processed job ids."""
        done: list[str] = []
        while (job_id := self.run_once()) is not None:
            done.append(job_id)
        return done

    def reclaim_orphaned(self, active_worker_ids: tuple[str, ...] = ()) -> int:
        """Re-queue ``running`` jobs owned by dead workers (restart path).

        ``attempts`` is preserved, so the resumed job keeps its retry
        budget. With no arguments every ``running`` job is reclaimed.
        """
        with self._sessions() as session:
            stmt = update(Job).where(Job.state == "running")
            if active_worker_ids:
                stmt = stmt.where(Job.worker_id.notin_(active_worker_ids))
            stmt = stmt.values(state="queued", worker_id=None, updated_at=utcnow())
            count = cast("CursorResult", session.execute(stmt)).rowcount
            session.commit()
            return count

    # -- internals -----------------------------------------------------

    @staticmethod
    def _check_budget(max_attempts: int) -> None:
        if max_attempts < 1:
            raise JobError(f"max_attempts must be >= 1, got {max_attempts}")

    def _claim(self, session: Session) -> _Claim | None:
        oldest = session.execute(
            select(Job.id)
            .where(Job.state == "queued")
            .order_by(Job.created_at)
            .limit(1)
        ).scalar_one_or_none()
        if oldest is None:
            return None
        claimed = cast(
            "CursorResult",
            session.execute(
                update(Job)
                .where(Job.id == oldest, Job.state == "queued")
                .values(
                    state="running",
                    worker_id=self._worker_id,
                    attempts=Job.attempts + 1,
                    updated_at=utcnow(),
                )
            ),
        ).rowcount
        if claimed != 1:
            session.rollback()
            return None  # lost a race with another worker
        session.commit()
        job = session.get(Job, oldest)
        assert job is not None
        payload = dict(job.payload) if isinstance(job.payload, dict) else {}
        return _Claim(
            job_id=job.id,
            kind=job.kind,
            run_id=job.run_id,
            payload=payload,
            attempt=job.attempts,
            max_attempts=job.max_attempts,
        )

    def _has_queued(self) -> bool:
        with self._sessions() as session:
            return (
                session.execute(
                    select(func.count()).select_from(Job).where(Job.state == "queued")
                ).scalar_one()
                > 0
            )

    def _execute(self, claim: _Claim) -> str:
        handler = self._handlers.get(claim.kind)
        with self._sessions() as session:
            job = session.get(Job, claim.job_id)
            assert job is not None
            try:
                if handler is None:
                    raise JobError(f"no handler registered for kind {claim.kind!r}")
                ctx = JobContext(
                    job_id=job.id,
                    kind=job.kind,
                    run_id=job.run_id,
                    payload=dict(claim.payload),
                    attempt=claim.attempt,
                    session=session,
                    worker_id=self._worker_id,
                )
                result = handler(ctx)
                if not isinstance(result, dict):
                    raise JobError(
                        f"handler for {claim.kind!r} must return a dict, "
                        f"got {type(result).__name__}"
                    )
                job.state = "succeeded"
                job.result = dict(result)
                job.run_id = ctx.run_id
                job.last_error = None
                job.worker_id = None
                job.updated_at = utcnow()
                session.commit()
            except Exception as exc:  # noqa: BLE001 — failures are row data
                session.rollback()
                with self._sessions() as retry_session:
                    job = retry_session.get(Job, claim.job_id)
                    assert job is not None
                    job.last_error = f"{type(exc).__name__}: {exc}"[:2000]
                    job.worker_id = None
                    job.updated_at = utcnow()
                    if claim.attempt < claim.max_attempts:
                        job.state = "queued"
                    else:
                        job.state = "failed"
                    retry_session.commit()
            return claim.job_id
