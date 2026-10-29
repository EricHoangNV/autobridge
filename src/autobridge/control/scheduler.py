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

from typing import Any

from sqlalchemy import select


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
