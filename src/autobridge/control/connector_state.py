"""Server-side connector state (REQ-030 item 4).

``GET /connectors`` and ``GET /connectors/{id}`` report, per connector:

* ``state`` — ``working`` (last sync ``Complete``, no open drift) ·
  ``degrading`` (drift detected; repair running; verified fix waiting;
  verifying sync after approval; or never synced yet) · ``error``
  (a waiting repair used a model or escalated; last sync ``Degraded``
  or ``Blocked`` with nothing else explaining it).
* ``activity`` — one line naming what is happening and how long ago.
* ``last_sync_at`` — ISO timestamp of the latest sync, or ``None``.

Colour/shape/word rendering is the UI's job (REQ-032); the server owns
the classification so every surface agrees. Rules are evaluated in
precedence order (error first): in-flight work beats waiting decisions,
waiting decisions beat the last sync outcome.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        ts = value
    else:
        try:
            ts = datetime.fromisoformat(str(value))
        except Exception:
            return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts


def human_ago(ts: datetime | None, *, now: datetime | None = None) -> str:
    """Short relative age (``"4 s ago"``, ``"3 m ago"``, ``"2 h ago"``)."""
    if ts is None:
        return "never"
    now = now or datetime.now(UTC)
    delta = (now - ts).total_seconds()
    if delta < 0:
        return "just now"
    if delta < 60:
        return f"{int(delta)} s ago"
    if delta < 3600:
        return f"{int(delta // 60)} m ago"
    if delta < 86400:
        return f"{int(delta // 3600)} h ago"
    return f"{int(delta // 86400)} d ago"


def _model_tokens_used(model_usage: Any) -> int:
    if not isinstance(model_usage, dict):
        return 0
    return int(model_usage.get("frontier_tokens", 0) or 0) + int(
        model_usage.get("small_tokens", 0) or 0
    )


def connector_state(session: Any, connector_id: str) -> dict[str, Any]:
    """Compute ``state`` / ``activity`` / ``last_sync_at`` for a connector."""
    from autobridge.control.models import (
        Approval,
        Connector,
        ConnectorRelease,
        DriftEvent,
        RepairRun,
        SyncRun,
    )
    from autobridge.control.scheduler import connector_busy_reason

    conn = session.get(Connector, connector_id)
    if conn is None:
        raise KeyError(f"Connector {connector_id!r} not found")

    release_ids: list[str] = list(
        session.execute(
            select(ConnectorRelease.id).where(
                ConnectorRelease.connector_id == connector_id
            )
        ).scalars().all()
    )
    last_sync: Any | None = None
    if release_ids:
        last_sync = (
            session.execute(
                select(SyncRun)
                .where(SyncRun.release_id.in_(release_ids))
                .order_by(SyncRun.started_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
    last_sync_at = getattr(last_sync, "started_at", None)
    last_outcome = getattr(last_sync, "outcome", None)
    last_unmet = list(getattr(last_sync, "unmet_criteria", None) or [])

    open_drift: list[Any] = list(
        session.execute(
            select(DriftEvent)
            .where(
                DriftEvent.connector_id == connector_id,
                DriftEvent.status == "open",
            )
            .order_by(DriftEvent.created_at.desc())
        )
        .scalars()
        .all()
    )

    # Repairs awaiting an operator decision (propose/escalate, no approval).
    awaiting: list[Any] = []
    drift_ids = set(
        session.execute(
            select(DriftEvent.id).where(DriftEvent.connector_id == connector_id)
        ).scalars().all()
    )
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
                awaiting.append(run)
    awaiting.sort(key=lambda r: str(getattr(r, "created_at", "")))

    busy = connector_busy_reason(session, connector_id)
    in_flight = busy is not None and ("queued" in busy or "running" in busy)

    # -- running repairs (REQ-043): a climb in flight for this connector.
    # The row is created at job start, so a connector whose drift is being
    # repaired names the rung it is on — the Repairs list and the linked
    # repair page show the rest.
    climbing: list[Any] = []
    if drift_ids:
        for run in session.execute(select(RepairRun)).scalars().all():
            if run.decision != "climbing":
                continue
            if set(run.drift_event_ids or []) & drift_ids:
                climbing.append(run)
    climbing.sort(key=lambda r: str(getattr(r, "created_at", "")))
    climbing_rung = 0
    if climbing:
        try:
            from autobridge.repair.controller import current_rung_level  # noqa: PLC0415

            climbing_rung = 0
            for run in climbing:
                rung = current_rung_level(list(run.ladder_trace or []))
                rung = max(rung, int(run.ladder_level_reached or 0))
                climbing_rung = max(climbing_rung, rung)
        except Exception:
            climbing_rung = 0

    # -- error ---------------------------------------------------------
    for run in awaiting:
        if run.decision == "escalate" or _model_tokens_used(run.model_usage) > 0:
            return {
                "state": "error",
                "activity": "needs your decision",
                "last_sync_at": last_sync_at,
            }
    if not open_drift and not awaiting and last_outcome in ("Degraded", "Blocked"):
        reason = last_unmet[0] if last_unmet else last_outcome
        return {
            "state": "error",
            "activity": f"{last_outcome}: {reason}",
            "last_sync_at": last_sync_at,
        }

    # -- degrading ------------------------------------------------------
    if in_flight and open_drift:
        if climbing:
            return {
                "state": "degrading",
                "activity": (
                    f"drift detected {human_ago(_parse_ts(open_drift[0].created_at))} · repairing at L{climbing_rung}"
                ),
                "last_sync_at": last_sync_at,
            }
        return {
            "state": "degrading",
            "activity": (
                f"drift detected {human_ago(_parse_ts(open_drift[0].created_at))} · repairing"
            ),
            "last_sync_at": last_sync_at,
        }
    if in_flight:
        return {
            "state": "degrading",
            "activity": "sync running",
            "last_sync_at": last_sync_at,
        }
    if awaiting:
        run = awaiting[-1]
        if _model_tokens_used(run.model_usage) == 0:
            return {
                "state": "degrading",
                "activity": "fix ready · verified · 0 model calls",
                "last_sync_at": last_sync_at,
            }
        return {
            "state": "degrading",
            "activity": "fix waiting for review",
            "last_sync_at": last_sync_at,
        }
    if open_drift:
        # Open drift but nothing waiting and nothing running: the repair was
        # decided (approved/rejected) and the verifying sync has not run
        # yet — the connector is still converging.
        return {
            "state": "degrading",
            "activity": (
                f"drift detected {human_ago(_parse_ts(open_drift[0].created_at))} · verifying"
            ),
            "last_sync_at": last_sync_at,
        }

    # -- working ---------------------------------------------------------
    if last_outcome == "Complete":
        return {
            "state": "working",
            "activity": f"in sync · Complete {human_ago(_parse_ts(last_sync_at))}",
            "last_sync_at": last_sync_at,
        }
    return {
        "state": "degrading",
        "activity": "waiting for first sync",
        "last_sync_at": last_sync_at,
    }
