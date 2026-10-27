# ruff: noqa: B008, TC002, TC003, ARG001, SIM102, SIM105, SIM118, UP035, I001, UP006, UP007, RUF012, B027
"""Run endpoints (P1.W7.1, P1.W7.2 for SSE)."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from autobridge.control.deps import get_db
from autobridge.control.models import BuildRun, EvidenceRecord, Job, RepairRun, SyncRun
from autobridge.control.schemas import (
    BuildRunOut,
    EvidenceOut,
    JobOut,
    RepairRunOut,
    RunOut,
    SyncRunOut,
)
from autobridge.evidence.redact import redact_payload

router = APIRouter(tags=["runs"])


def _job_to_out(j: Job) -> JobOut:
    return JobOut(
        id=j.id,
        kind=j.kind,
        run_id=j.run_id,
        state=j.state,
        attempts=j.attempts,
        max_attempts=j.max_attempts,
        payload=dict(j.payload or {}),
        result=dict(j.result) if j.result else None,
        last_error=j.last_error,
        worker_id=j.worker_id,
        created_at=j.created_at,
        updated_at=j.updated_at,
    )


def _build_to_out(b: BuildRun) -> BuildRunOut:
    return BuildRunOut(
        id=b.id,
        connector_id=b.connector_id,
        state=b.state,
        inputs=dict(b.inputs or {}),
        ladder_trace=list(b.ladder_trace or []),
        model_usage=dict(b.model_usage or {}),
        evidence_ids=list(b.evidence_ids or []),
        draft_spec=dict(b.draft_spec) if b.draft_spec else None,
        result=dict(b.result) if b.result else None,
        created_at=b.created_at,
        updated_at=b.updated_at,
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


def _repair_to_out(r: RepairRun) -> RepairRunOut:
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


def _evidence_to_out(e: EvidenceRecord, secrets: list[str] | None = None) -> EvidenceOut:
    payload = e.payload
    if secrets:
        payload = redact_payload(payload, secrets)
    return EvidenceOut(
        id=e.id,
        kind=e.kind,
        payload=dict(payload or {}),
        parent_ids=list(e.parent_ids or []),
        run_id=e.run_id,
        created_at=e.created_at,
    )


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run(run_id: str, db: Session = Depends(get_db)) -> RunOut:
    # Try each run table by id
    b = db.get(BuildRun, run_id)
    if b is not None:
        return RunOut(
            id=b.id,
            kind="build",
            state=b.state,
            created_at=b.created_at,
            payload={
                "build_run": _build_to_out(b).model_dump(mode="json"),
                "job": None,
            },
        )
    s = db.get(SyncRun, run_id)
    if s is not None:
        return RunOut(
            id=s.id,
            kind="sync",
            outcome=s.outcome,
            created_at=s.started_at,
            payload={"sync_run": _sync_to_out(s).model_dump(mode="json")},
        )
    r = db.get(RepairRun, run_id)
    if r is not None:
        return RunOut(
            id=r.id,
            kind="repair",
            decision=r.decision,
            created_at=r.created_at,
            payload={"repair_run": _repair_to_out(r).model_dump(mode="json")},
        )
    j = db.get(Job, run_id)
    if j is not None:
        return RunOut(
            id=j.id,
            kind=f"job:{j.kind}",
            state=j.state,
            created_at=j.created_at,
            payload={"job": _job_to_out(j).model_dump(mode="json")},
        )
    raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")


@router.get("/runs/{run_id}/detail")
def get_run_detail(run_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Detailed run view returning typed object (for UI)."""
    b = db.get(BuildRun, run_id)
    if b is not None:
        return {"kind": "build", "run": _build_to_out(b).model_dump(mode="json")}
    s = db.get(SyncRun, run_id)
    if s is not None:
        return {"kind": "sync", "run": _sync_to_out(s).model_dump(mode="json")}
    r = db.get(RepairRun, run_id)
    if r is not None:
        return {"kind": "repair", "run": _repair_to_out(r).model_dump(mode="json")}
    j = db.get(Job, run_id)
    if j is not None:
        return {"kind": "job", "run": _job_to_out(j).model_dump(mode="json")}
    raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, db: Session = Depends(get_db)) -> JobOut:
    j = db.get(Job, job_id)
    if j is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    return _job_to_out(j)


@router.get("/jobs", response_model=list[JobOut])
def list_jobs(db: Session = Depends(get_db)) -> list[JobOut]:
    rows = db.execute(select(Job).order_by(Job.created_at.desc()).limit(100)).scalars().all()
    return [_job_to_out(j) for j in rows]


@router.get("/evidence/{evidence_id}", response_model=EvidenceOut)
def get_evidence(evidence_id: str, db: Session = Depends(get_db)) -> EvidenceOut:
    e = db.get(EvidenceRecord, evidence_id)
    if e is None:
        raise HTTPException(status_code=404, detail=f"Evidence {evidence_id!r} not found")
    return _evidence_to_out(e)


@router.get("/runs/{run_id}/evidence", response_model=list[EvidenceOut])
def get_run_evidence(run_id: str, db: Session = Depends(get_db)) -> list[EvidenceOut]:
    rows = db.execute(select(EvidenceRecord).order_by(EvidenceRecord.created_at)).scalars().all()
    matched = [r for r in rows if r.run_id.endswith(f":{run_id}") or r.run_id == run_id]
    secrets = _collect_secrets(db)
    return [_evidence_to_out(e, secrets) for e in matched]


def _collect_secrets(db: Session | None = None) -> list[str]:
    import os
    import re

    # Collect from DB credential_refs if available
    refs: list[str] = []
    if db is not None:
        try:
            from autobridge.control.models import Connector

            rows = db.execute(__import__("sqlalchemy").select(Connector.credential_ref)).scalars().all()
            refs.extend(rows)
        except Exception:
            pass
    # Add env-based refs (generic uppercase keys that look like credential refs)
    # Include any env var matching credential ref pattern and with value length >=6
    cred_re = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")
    skip = {"PATH", "HOME", "PWD", "OLDPWD", "SHELL", "USER", "LOGNAME", "LANG", "TERM", "DISPLAY", "DATABASE_URL", "VERIFIER_URL", "SIMULATOR_ADMIN_URL"}
    for k, v in os.environ.items():
        if k in skip:
            continue
        if not v or len(v) < 6:
            continue
        if cred_re.match(k) and k not in refs:
            refs.append(k)
    # Also include traditional suffixes
    for k in os.environ.keys():
        if k.endswith("_API_KEY") or k.endswith("_TOKEN") or k.endswith("_SECRET"):
            if k not in refs:
                refs.append(k)
    values: list[str] = []
    for ref in refs:
        val = os.environ.get(ref)
        if val:
            values.append(val)
    return values


@router.get("/runs/{run_id}/events")
async def get_run_events(run_id: str, request: Request, db: Session = Depends(get_db)):
    """SSE stream of evidence records for a run (W7.2).

    Streams stored EvidenceRecords as SSE events, redacting credentials.
    """
    # Verify run exists (any kind)
    exists = (
        db.get(BuildRun, run_id) is not None
        or db.get(SyncRun, run_id) is not None
        or db.get(RepairRun, run_id) is not None
        or db.get(Job, run_id) is not None
    )
    if not exists:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")

    # Collect evidence for this run
    rows = db.execute(select(EvidenceRecord).order_by(EvidenceRecord.created_at)).scalars().all()
    matched = [r for r in rows if r.run_id.endswith(f":{run_id}") or r.run_id == run_id]
    secrets = _collect_secrets(db)

    async def generator():
        for rec in matched:
            if await request.is_disconnected():
                break
            payload = rec.payload
            if secrets:
                payload = redact_payload(payload, secrets)
            data = {
                "id": rec.id,
                "kind": rec.kind,
                "payload": payload,
                "parent_ids": rec.parent_ids,
                "run_id": rec.run_id,
                "created_at": rec.created_at.isoformat() if rec.created_at else None,
            }
            yield {"event": rec.kind, "data": json.dumps(data, default=str)}
        # End-of-stream marker
        yield {"event": "done", "data": json.dumps({"run_id": run_id, "count": len(matched)})}

    return EventSourceResponse(generator())
