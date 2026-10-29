"""Sync scheduler (REQ-030 item 1).

Ticks every ``AUTOBRIDGE_SYNC_INTERVAL_S`` seconds (off when unset) and
syncs every active connector through the same path as ``POST /sync``
(submit + inline handlers: detect → classify → ladder → verifier gate).
The scheduler never approves: a proposed patch becomes a candidate
release awaiting an operator decision, exactly like a manual sync.

A connector is skipped while it is busy:

* a ``sync`` / ``build`` / ``repair`` job for it is ``queued`` or
  ``running`` (sync, build, repair in flight), or
* its latest repair run has no approval decision yet (a verified fix
  waiting, or an escalation needing the operator — re-syncing would pile
  another repair on the undecided one). The next scheduled sync after an
  approval is the verifying sync; landing ``Complete`` retires the drift
  (see :func:`autobridge.engine.detect_repair.detect_and_repair_after_sync`).

Corrupt scheduler state is per-connector row data, never a tick failure:
one connector's error is recorded in its result and the pass continues.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select


def _pause_fresh(paused_at: Any) -> bool:
    """A pause is honoured only while fresh (REQ-031: stale owners die)."""
    from autobridge.control.models import SCHEDULER_PAUSE_STALE_AFTER_S

    if paused_at is None:
        return False
    try:
        # SQLite returns naive datetimes; control stores UTC throughout.
        if getattr(paused_at, "tzinfo", None) is None:
            paused_at = paused_at.replace(tzinfo=UTC)
        age_s = (datetime.now(UTC) - paused_at).total_seconds()
    except Exception:
        return True
    return age_s < SCHEDULER_PAUSE_STALE_AFTER_S


def pause_scheduler(session: Any, owner: str, reason: str = "") -> dict[str, Any]:
    """Pause the sync scheduler (idempotent, owner-scoped).

    ``POST /demo/reset``, ``POST /demo/scheduler/pause`` (the seed
    script) and sims-only heal pause while they run; the scheduler
    honours a fresh pause by skipping whole passes. A resume only clears
    its owner's pause, so a nested reset inside a paused seed cannot
    resume early — and a stale pause (owner crashed) expires instead of
    wedging the scheduler.
    """
    from autobridge.control.models import SchedulerState

    row = session.get(SchedulerState, "scheduler")
    if row is None:
        row = SchedulerState(id="scheduler", paused=True, owner=owner, reason=reason)
        session.add(row)
    else:
        row.paused = True
        row.owner = owner
        row.reason = reason
    session.flush()
    return {"paused": True, "owner": owner, "reason": reason}


def resume_scheduler(session: Any, owner: str) -> dict[str, Any]:
    """Clear a pause owned by *owner*; a foreign owner's pause is kept.

    Returns ``{"resumed": True}`` when this call cleared the pause,
    ``{"resumed": False, "owner": <current>}`` when someone else owns it
    (nested reset inside a paused seed: the seed resumes, not the reset).
    """
    from autobridge.control.models import SchedulerState

    row = session.get(SchedulerState, "scheduler")
    if row is None or not row.paused:
        return {"resumed": True, "owner": owner, "reason": "not paused"}
    if row.owner and row.owner != owner:
        return {"resumed": False, "owner": row.owner, "reason": row.reason}
    row.paused = False
    row.owner = ""
    row.reason = ""
    session.flush()
    return {"resumed": True, "owner": owner, "reason": ""}


def scheduler_pause_status(session: Any) -> dict[str, Any]:
    """Fresh pauses are honoured; stale ones read as cleared (never wedged)."""
    from autobridge.control.models import SchedulerState

    row = session.get(SchedulerState, "scheduler")
    if row is None or not row.paused:
        return {"paused": False, "owner": "", "reason": ""}
    if not _pause_fresh(row.updated_at):
        return {
            "paused": False,
            "owner": row.owner,
            "reason": row.reason,
            "stale": True,
        }
    return {"paused": True, "owner": row.owner, "reason": row.reason}


def wait_for_quiescent(
    session: Any, *, timeout_s: float = 90.0, poll_s: float = 1.0
) -> dict[str, Any]:
    """Block until no sync/build/repair job is still running.

    ``POST /demo/reset`` calls this after pausing: the reset must start
    from a known state, never mid-sync (a sync that outlives the reset
    writes rows for deleted connectors, or its writes collide with the
    seed's). Only ``running`` jobs block — those are in flight on the
    worker or on an inline ``POST /sync``. ``queued`` rows are demo state
    the reset itself deletes (nothing has started them; pausing first
    guarantees the scheduler submits nothing new while waiting).
    Returns ``{"quiescent": True, "waited_s": ...}``; on timeout raises
    :class:`TimeoutError` naming the stuck jobs — reset fails loud (409)
    instead of starting dirty.
    """
    import time

    from autobridge.control.models import Job

    deadline = time.monotonic() + max(0.0, timeout_s)
    waited_s = 0.0
    while True:
        stuck = (
            session.execute(
                select(Job).where(
                    Job.kind.in_(("sync", "build", "repair")),
                    Job.state.in_(("running",)),
                )
            )
            .scalars()
            .all()
        )
        if not stuck:
            return {"quiescent": True, "waited_s": round(waited_s, 1)}
        if time.monotonic() >= deadline:
            def _describe(j: Any) -> str:
                payload = j.payload if isinstance(j.payload, dict) else {}
                target = (
                    payload.get("release_id")
                    or payload.get("connector_id")
                    or (payload.get("drift_event_ids") or [None])[0]
                    or "?"
                )
                return f"{j.kind}/{j.id} {j.state} (worker {j.worker_id or '?'}, target {target})"

            raise TimeoutError(
                "reset refused: jobs still in flight after "
                f"{timeout_s:g}s: " + ", ".join(_describe(j) for j in stuck[:10])
            )
        session.expire_all()
        time.sleep(poll_s)
        waited_s += poll_s


def connector_busy_reason(session: Any, connector_id: str) -> str | None:
    """Why the scheduler must skip *connector_id*, or ``None`` when syncable.

    Checks, in order: in-flight jobs (sync/build/repair), then repairs
    awaiting an operator decision (propose or escalate with no approval,
    reject or rollback row referencing them).
    """
    from autobridge.control.models import (
        Approval,
        Connector,
        ConnectorRelease,
        DriftEvent,
        Job,
        RepairRun,
    )

    conn = session.get(Connector, connector_id)
    if conn is None:
        return "connector not found"

    # -- in-flight jobs touching this connector -------------------------
    release_ids = set(
        session.execute(
            select(ConnectorRelease.id).where(
                ConnectorRelease.connector_id == connector_id
            )
        ).scalars().all()
    )
    drift_ids = set(
        session.execute(
            select(DriftEvent.id).where(DriftEvent.connector_id == connector_id)
        ).scalars().all()
    )
    for job in (
        session.execute(
            select(Job).where(Job.state.in_(("queued", "running")))
        )
        .scalars()
        .all()
    ):
        payload = job.payload if isinstance(job.payload, dict) else {}
        if job.kind == "build" and payload.get("connector_id") == connector_id:
            return f"{job.kind} {job.state}"
        if job.kind == "sync" and payload.get("release_id") in release_ids:
            return f"{job.kind} {job.state}"
        if job.kind == "repair" and (
            set(payload.get("drift_event_ids") or []) & drift_ids
        ):
            return f"{job.kind} {job.state}"

    # -- repairs awaiting an operator decision --------------------------
    if drift_ids:
        for run in session.execute(select(RepairRun)).scalars().all():
            if not set(run.drift_event_ids or []) & drift_ids:
                continue
            if run.decision not in ("propose", "escalate"):
                continue
            decided = (
                session.execute(
                    select(Approval).where(Approval.repair_run_id == run.id)
                )
                .scalars()
                .first()
            )
            if decided is None:
                return f"repair {run.decision} awaiting decision"
    return None


def snapshot_edge_links(session: Any) -> int:
    """Persist one link snapshot per edge connector (REQ-030 item 3).

    Best-effort read of the relay ``/v1/agents`` view: link state, depths
    and heartbeat per agent at this tick. Never fails the pass — a dead
    relay is a missed snapshot, not an error (the relay proxy endpoint
    reports the outage itself).
    """
    from autobridge.control.models import Connector, PabxLinkEvent

    recorded = 0
    try:
        from autobridge.control.routes.edge import _relay_get  # noqa: PLC0415

        view = _relay_get("/v1/agents", None)
    except Exception:
        return 0
    agents = view.get("agents", []) if isinstance(view, dict) else []
    by_id = {a.get("agent_id"): a for a in agents if isinstance(a, dict)}
    try:
        connectors = session.execute(select(Connector)).scalars().all()
    except Exception:
        return 0
    for conn in connectors:
        try:
            kind = (conn.runtime_target or {}).get("kind", "local_process")
        except Exception:
            kind = "local_process"
        if kind != "edge":
            continue
        match = by_id.get(conn.id) or by_id.get(conn.name)
        try:
            session.add(
                PabxLinkEvent(
                    connector_id=conn.id,
                    kind="link",
                    payload={
                        "link_up": bool((match or {}).get("link_up", False)),
                        "link_detail": str((match or {}).get("link_detail", "")),
                        "outbox_depth": int((match or {}).get("outbox_depth", 0) or 0),
                        "inbox_depth": int((match or {}).get("inbox_depth", 0) or 0),
                        "agent_seen": match is not None,
                    },
                )
            )
            session.flush()
            recorded += 1
        except Exception:
            continue
    return recorded


def run_scheduler_pass(session: Any) -> dict[str, Any]:
    """Sync every idle active connector once; return per-connector results.

    Connectors without an active release have nothing runnable and are
    reported ``skipped`` (no release). Busy connectors are reported
    ``skipped`` with the reason. Each sync runs the same handlers as
    ``POST /sync``; per-connector failures are recorded, never raised.
    """
    from autobridge.control.models import Connector
    from autobridge.control.runner import sync_connector_now

    results: list[dict[str, Any]] = []
    # REQ-031: reset/seed/heal pause the scheduler while they run — a pass
    # that starts paused syncs nothing (half-reset connectors, doubled
    # drift rows and SQLite lock races all come from here).
    pause = scheduler_pause_status(session)
    if pause.get("paused"):
        return {
            "connectors": 0,
            "results": results,
            "synced": 0,
            "skipped": 0,
            "errors": 0,
            "link_snapshots": 0,
            "paused": True,
            "pause_owner": pause.get("owner"),
        }
    # Snapshots commit on their own: holding one write transaction open
    # across every connector's sync would serialize API writes behind the
    # pass (SQLite), even in WAL mode. The syncs below run in the runner's
    # own sessions; this session only re-checks busyness per connector.
    link_snapshots = snapshot_edge_links(session)
    session.commit()
    connectors = (
        session.execute(select(Connector).order_by(Connector.created_at))
        .scalars()
        .all()
    )
    for conn in connectors:
        if not conn.active_release_id:
            results.append(
                {"connector_id": conn.id, "status": "skipped", "reason": "no active release"}
            )
            continue
        busy = connector_busy_reason(session, conn.id)
        if busy is not None:
            results.append(
                {"connector_id": conn.id, "status": "skipped", "reason": busy}
            )
            continue
        try:
            out = sync_connector_now(session, conn.id)
            results.append(
                {
                    "connector_id": conn.id,
                    "status": "synced",
                    "sync_run_id": out.get("sync_run_id"),
                    "outcome": out.get("outcome"),
                    "drift_found": out.get("drift_found", False),
                }
            )
        except Exception as exc:  # noqa: BLE001 — one connector never stops the pass
            results.append(
                {
                    "connector_id": conn.id,
                    "status": "error",
                    "reason": f"{type(exc).__name__}: {exc}"[:500],
                }
            )
    session.commit()
    return {
        "connectors": len(connectors),
        "results": results,
        "synced": sum(1 for r in results if r["status"] == "synced"),
        "skipped": sum(1 for r in results if r["status"] == "skipped"),
        "errors": sum(1 for r in results if r["status"] == "error"),
        "link_snapshots": link_snapshots,
    }
