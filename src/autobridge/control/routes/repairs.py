# ruff: noqa: B008, TC002, TC003, ARG001, SIM102, SIM105, SIM118, UP035, I001, UP006, UP007, RUF012, B027
"""Repair approve/reject and release rollback."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from autobridge.control.deps import get_db
from autobridge.control.models import ConnectorRelease, RepairRun
from autobridge.control.schemas import (
    ApprovalOut,
    ReleaseOut,
    RepairDecisionRequest,
    RollbackRequest,
    RollbackResponse,
)

router = APIRouter(tags=["repairs"])


def _approval_to_out(a) -> ApprovalOut:
    return ApprovalOut(
        id=a.id,
        repair_run_id=a.repair_run_id,
        actor=a.actor,
        decision=a.decision,
        from_release_id=a.from_release_id,
        to_release_id=a.to_release_id,
        rollback_of_release_id=a.rollback_of_release_id,
        created_at=a.created_at,
    )


def _release_to_out(r: ConnectorRelease) -> ReleaseOut:
    return ReleaseOut(
        id=r.id,
        connector_id=r.connector_id,
        version=r.version,
        spec=dict(r.spec),
        parent_release_id=r.parent_release_id,
        provenance_build_run_id=r.provenance_build_run_id,
        provenance_repair_run_id=r.provenance_repair_run_id,
        verification_report_id=r.verification_report_id,
        signature=r.signature,
        created_at=r.created_at,
    )


@router.post("/repairs/{repair_id}/approve", response_model=ApprovalOut)
def approve_repair(repair_id: str, body: RepairDecisionRequest, db: Session = Depends(get_db)) -> ApprovalOut:
    run = db.get(RepairRun, repair_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"RepairRun {repair_id!r} not found")
    if run.decision != "propose":
        raise HTTPException(status_code=422, detail=f"RepairRun {repair_id!r} decision is {run.decision!r}, not propose")

    # Determine to_release_id: need to find candidate release created from this repair
    # Look for ConnectorRelease with provenance_repair_run_id == repair_id
    from sqlalchemy import select

    from autobridge.control.models import ConnectorRelease as Rel

    rel = db.execute(select(Rel).where(Rel.provenance_repair_run_id == repair_id).order_by(Rel.version.desc()).limit(1)).scalars().first()
    to_release_id = body.to_release_id or (rel.id if rel else None)
    if not to_release_id:
        raise HTTPException(status_code=422, detail="no candidate release for this repair (to_release_id required)")

    from_release_id = body.from_release_id
    # If from not given, try to infer from run's drift -> connector -> active
    if not from_release_id and body.connector_id:
        from autobridge.control.models import Connector

        conn = db.get(Connector, body.connector_id)
        if conn:
            from_release_id = conn.active_release_id

    # Perform approval via engine
    try:
        from autobridge.engine.releases import approve_release  # noqa: PLC0415

        approval = approve_release(
            db,
            repair_run_id=repair_id,
            actor=body.actor,
            from_release_id=from_release_id,
            to_release_id=to_release_id,
            connector_id=body.connector_id,
        )
        db.commit()
        db.refresh(approval)
        return _approval_to_out(approval)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/repairs/{repair_id}/reject", response_model=ApprovalOut)
def reject_repair(repair_id: str, body: RepairDecisionRequest, db: Session = Depends(get_db)) -> ApprovalOut:
    run = db.get(RepairRun, repair_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"RepairRun {repair_id!r} not found")

    try:
        from autobridge.engine.releases import reject_release  # noqa: PLC0415

        approval = reject_release(
            db,
            repair_run_id=repair_id,
            actor=body.actor,
            from_release_id=body.from_release_id,
            to_release_id=body.to_release_id,
        )
        db.commit()
        db.refresh(approval)
        return _approval_to_out(approval)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/releases", response_model=list[ReleaseOut])
def list_releases(connector_id: str | None = None, db: Session = Depends(get_db)) -> list[ReleaseOut]:
    from sqlalchemy import select

    q = select(ConnectorRelease).order_by(ConnectorRelease.version.desc())
    if connector_id:
        q = q.where(ConnectorRelease.connector_id == connector_id)
    rows = db.execute(q).scalars().all()
    return [_release_to_out(r) for r in rows]


@router.get("/releases/{release_id}", response_model=ReleaseOut)
def get_release(release_id: str, db: Session = Depends(get_db)) -> ReleaseOut:
    r = db.get(ConnectorRelease, release_id)
    if r is None:
        raise HTTPException(status_code=404, detail=f"Release {release_id!r} not found")
    return _release_to_out(r)


@router.post("/releases/{release_id}/rollback", response_model=RollbackResponse)
def rollback_release_endpoint(release_id: str, body: RollbackRequest, db: Session = Depends(get_db)) -> RollbackResponse:
    # release_id in path is the currently active (or any) but we use body.target_release_id as target
    # The path param is for REST consistency; we validate it exists
    current = db.get(ConnectorRelease, release_id)
    if current is None:
        raise HTTPException(status_code=404, detail=f"Release {release_id!r} not found")

    # Find connector_id from current release
    connector_id = current.connector_id
    # Target is body.target_release_id
    target = db.get(ConnectorRelease, body.target_release_id)
    if target is None:
        raise HTTPException(status_code=404, detail=f"Target release {body.target_release_id!r} not found")

    try:
        from autobridge.engine.releases import rollback_release  # noqa: PLC0415

        new_release, approval = rollback_release(
            db,
            connector_id=connector_id,
            target_release_id=body.target_release_id,
            actor=body.actor,
            repair_run_id=body.repair_run_id,
        )
        db.commit()
        db.refresh(new_release)
        db.refresh(approval)
        return RollbackResponse(new_release=_release_to_out(new_release), approval=_approval_to_out(approval))
    except Exception as exc:
        db.rollback()
        # Distinguish not found vs validation
        msg = str(exc)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg) from exc
        raise HTTPException(status_code=422, detail=msg) from exc
