# ruff: noqa: B008, TC002, TC003, ARG001, SIM102, SIM105, SIM118, UP035, I001, UP006, UP007, RUF012, B027
"""Drift endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from autobridge.control.deps import get_db
from autobridge.control.models import DriftEvent, RepairRun
from autobridge.control.schemas import DriftEventOut, RepairRunOut

router = APIRouter(tags=["drift"])


def _drift_to_out(d: DriftEvent) -> DriftEventOut:
    return DriftEventOut(
        id=d.id,
        connector_id=d.connector_id,
        drift_class=d.drift_class,
        severity=d.severity,
        evidence_ids=list(d.evidence_ids or []),
        source_sync_run_id=d.source_sync_run_id,
        status=d.status,
        created_at=d.created_at,
        updated_at=d.updated_at,
    )


@router.get("/drift", response_model=list[DriftEventOut])
def list_drift(connector_id: str | None = None, status: str | None = None, db: Session = Depends(get_db)) -> list[DriftEventOut]:
    q = select(DriftEvent).order_by(DriftEvent.created_at.desc())
    if connector_id:
        q = q.where(DriftEvent.connector_id == connector_id)
    if status:
        q = q.where(DriftEvent.status == status)
    rows = db.execute(q).scalars().all()
    return [_drift_to_out(d) for d in rows]


@router.get("/drift/{drift_id}", response_model=DriftEventOut)
def get_drift(drift_id: str, db: Session = Depends(get_db)) -> DriftEventOut:
    d = db.get(DriftEvent, drift_id)
    if d is None:
        raise HTTPException(status_code=404, detail=f"DriftEvent {drift_id!r} not found")
    return _drift_to_out(d)


@router.get("/repairs", response_model=list[RepairRunOut])
def list_repairs(connector_id: str | None = None, db: Session = Depends(get_db)) -> list[RepairRunOut]:
    # Repairs are linked via drift_event_ids -> drift events -> connector
    # Simple: return all repairs, optionally filter by connector via drift

    rows = db.execute(select(RepairRun).order_by(RepairRun.created_at.desc()).limit(100)).scalars().all()
    if connector_id:
        # Filter to repairs where any drift belongs to connector
        filtered = []
        for r in rows:
            for drift_id in (r.drift_event_ids or []):
                d = db.get(DriftEvent, drift_id)
                if d is not None and d.connector_id == connector_id:
                    filtered.append(r)
                    break
        rows = filtered
    return [
        RepairRunOut(
            id=r.id,
            drift_event_ids=list(r.drift_event_ids or []),
            ladder_level_reached=r.ladder_level_reached,
            candidate_patch=list(r.candidate_patch) if r.candidate_patch else None,
            rule_id=r.rule_id,
            hypothesis_ids=list(r.hypothesis_ids or []),
            visible_test_results=list(r.visible_test_results or []),
            verifier_report_id=r.verifier_report_id,
            decision=r.decision,
            model_usage=dict(r.model_usage or {}),
            escalation_bundle=dict(r.escalation_bundle) if r.escalation_bundle else None,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.get("/repairs/{repair_id}", response_model=RepairRunOut)
def get_repair(repair_id: str, db: Session = Depends(get_db)) -> RepairRunOut:
    r = db.get(RepairRun, repair_id)
    if r is None:
        raise HTTPException(status_code=404, detail=f"RepairRun {repair_id!r} not found")
    return RepairRunOut(
        id=r.id,
        drift_event_ids=list(r.drift_event_ids or []),
        ladder_level_reached=r.ladder_level_reached,
        candidate_patch=list(r.candidate_patch) if r.candidate_patch else None,
        rule_id=r.rule_id,
        hypothesis_ids=list(r.hypothesis_ids or []),
        visible_test_results=list(r.visible_test_results or []),
        verifier_report_id=r.verifier_report_id,
        decision=r.decision,
        model_usage=dict(r.model_usage or {}),
        escalation_bundle=dict(r.escalation_bundle) if r.escalation_bundle else None,
        created_at=r.created_at,
    )
