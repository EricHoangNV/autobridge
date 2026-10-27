# ruff: noqa: B008, TC002, TC003, ARG001, SIM102, SIM105, SIM118, UP035, I001, UP006, UP007, RUF012, B027
"""Connector endpoints (P1.W7.1)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from autobridge.control.deps import approved_by_for_releases, get_db
from autobridge.control.models import (
    Connector,
    ConnectorRelease,
    DependencyRegistration,
    DriftEvent,
    SyncRun,
)
from autobridge.control.schemas import (
    ConnectorCreate,
    ConnectorDetail,
    ConnectorOut,
    DependencyCreate,
    DependencyOut,
    DriftEventOut,
    ReleaseOut,
    SyncRunOut,
)

router = APIRouter(prefix="/connectors", tags=["connectors"])


def _connector_to_out(c: Connector) -> ConnectorOut:
    return ConnectorOut(
        id=c.id,
        name=c.name,
        vendor_docs_ref=c.vendor_docs_ref,
        base_url=c.base_url,
        credential_ref=c.credential_ref,
        target_entities=list(c.target_entities or []),
        runtime_target=dict(c.runtime_target or {}),
        active_release_id=c.active_release_id,
        health_status=c.health_status,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


def _release_to_out(r: ConnectorRelease, approved_by: str | None = None) -> ReleaseOut:
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
        approved_by=approved_by,
        created_at=r.created_at,
    )


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


def _sync_to_out(s: SyncRun) -> SyncRunOut:
    return SyncRunOut(
        id=s.id,
        release_id=s.release_id,
        started_at=s.started_at,
        finished_at=s.finished_at,
        records_observed=dict(s.records_observed or {}),
        records_emitted=dict(s.records_emitted or {}),
        checkpoint=dict(s.checkpoint or {}),
        invariant_results=list(s.invariant_results or []),
        outcome=s.outcome,
        unmet_criteria=list(s.unmet_criteria or []),
        data_readiness=dict(s.data_readiness or {}),
        fingerprint_id=s.fingerprint_id,
        kind="sync",
    )


def _dep_to_out(d: DependencyRegistration) -> DependencyOut:
    return DependencyOut(
        id=d.id,
        consumer_id=d.consumer_id,
        connector_id=d.connector_id,
        entity=d.entity,
        policy=d.policy,
        paused=d.paused,
        created_at=d.created_at,
    )


@router.post("", response_model=ConnectorOut, status_code=201)
def create_connector(body: ConnectorCreate, db: Session = Depends(get_db)) -> ConnectorOut:
    rt = body.runtime_target or {"kind": "local_process"}
    # Validate runtime_target kind
    kind = rt.get("kind", "local_process")
    if kind not in ("local_process", "docker_sandbox", "edge"):
        raise HTTPException(status_code=422, detail=f"invalid runtime_target.kind {kind!r}")
    if kind == "edge":
        raise HTTPException(status_code=422, detail="RuntimeTarget.edge not yet available (P2.S2.1)")

    conn = Connector(
        name=body.name,
        vendor_docs_ref=body.vendor_docs_ref,
        base_url=body.base_url,
        credential_ref=body.credential_ref,
        target_entities=list(body.target_entities),
        runtime_target=dict(rt),
        health_status="healthy",
    )
    db.add(conn)
    db.flush()
    db.commit()
    db.refresh(conn)
    return _connector_to_out(conn)


@router.get("", response_model=list[ConnectorOut])
def list_connectors(db: Session = Depends(get_db)) -> list[ConnectorOut]:
    rows = db.execute(select(Connector).order_by(Connector.created_at)).scalars().all()
    return [_connector_to_out(c) for c in rows]


@router.get("/{connector_id}", response_model=ConnectorDetail)
def get_connector(connector_id: str, db: Session = Depends(get_db)) -> ConnectorDetail:
    conn = db.get(Connector, connector_id)
    if conn is None:
        raise HTTPException(status_code=404, detail=f"Connector {connector_id!r} not found")
    base = _connector_to_out(conn)
    # Releases
    releases = db.execute(
        select(ConnectorRelease).where(ConnectorRelease.connector_id == connector_id).order_by(ConnectorRelease.version)
    ).scalars().all()
    by_release = approved_by_for_releases(db, [r.id for r in releases])
    release_outs = [_release_to_out(r, approved_by=by_release.get(r.id)) for r in releases]

    # Last sync via active release or latest release
    last_sync: SyncRunOut | None = None
    if conn.active_release_id:
        last_sync_row = db.execute(
            select(SyncRun).where(SyncRun.release_id == conn.active_release_id).order_by(SyncRun.started_at.desc()).limit(1)
        ).scalars().first()
        if last_sync_row is not None:
            last_sync = _sync_to_out(last_sync_row)
    elif releases:
        # Try any release's sync
        for rel in reversed(releases):
            s = db.execute(select(SyncRun).where(SyncRun.release_id == rel.id).order_by(SyncRun.started_at.desc()).limit(1)).scalars().first()
            if s is not None:
                last_sync = _sync_to_out(s)
                break

    open_drift = db.execute(
        select(DriftEvent).where(DriftEvent.connector_id == connector_id, DriftEvent.status == "open").order_by(DriftEvent.created_at.desc())
    ).scalars().all()
    open_drift_out = [_drift_to_out(d) for d in open_drift]

    deps = db.execute(select(DependencyRegistration).where(DependencyRegistration.connector_id == connector_id)).scalars().all()
    dep_outs = [_dep_to_out(d) for d in deps]

    return ConnectorDetail(
        **base.model_dump(),
        releases=release_outs,
        last_sync=last_sync,
        open_drift=open_drift_out,
        dependencies=dep_outs,
    )


@router.post("/{connector_id}/dependencies", response_model=DependencyOut, status_code=201)
def create_dependency(connector_id: str, body: DependencyCreate, db: Session = Depends(get_db)) -> DependencyOut:
    conn = db.get(Connector, connector_id)
    if conn is None:
        raise HTTPException(status_code=404, detail=f"Connector {connector_id!r} not found")
    # Check duplicate
    existing = db.execute(
        select(DependencyRegistration).where(
            DependencyRegistration.connector_id == connector_id,
            DependencyRegistration.consumer_id == body.consumer_id,
            DependencyRegistration.entity == body.entity,
        )
    ).scalars().first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="dependency already exists")
    dep = DependencyRegistration(
        consumer_id=body.consumer_id,
        connector_id=connector_id,
        entity=body.entity,
        policy=body.policy,
        paused=False,
    )
    db.add(dep)
    db.flush()
    db.commit()
    db.refresh(dep)
    return _dep_to_out(dep)


@router.get("/{connector_id}/sync-runs", response_model=list[SyncRunOut])
def list_sync_runs(connector_id: str, db: Session = Depends(get_db)) -> list[SyncRunOut]:
    conn = db.get(Connector, connector_id)
    if conn is None:
        raise HTTPException(status_code=404, detail=f"Connector {connector_id!r} not found")
    release_ids = db.execute(
        select(ConnectorRelease.id).where(ConnectorRelease.connector_id == connector_id)
    ).scalars().all()
    if not release_ids:
        return []
    rows = db.execute(
        select(SyncRun).where(SyncRun.release_id.in_(release_ids)).order_by(SyncRun.started_at.desc()).limit(100)
    ).scalars().all()
    return [_sync_to_out(s) for s in rows]


@router.get("/{connector_id}/dependencies", response_model=list[DependencyOut])
def list_dependencies(connector_id: str, db: Session = Depends(get_db)) -> list[DependencyOut]:
    conn = db.get(Connector, connector_id)
    if conn is None:
        raise HTTPException(status_code=404, detail=f"Connector {connector_id!r} not found")
    rows = db.execute(select(DependencyRegistration).where(DependencyRegistration.connector_id == connector_id)).scalars().all()
    return [_dep_to_out(d) for d in rows]
