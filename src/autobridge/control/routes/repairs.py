# ruff: noqa: B008, TC002, TC003, ARG001, SIM102, SIM105, SIM118, UP035, I001, UP006, UP007, RUF012, B027
"""Repair approve/reject and release rollback."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Security
from sqlalchemy.orm import Session

from autobridge.control.deps import (
    approval_for_repair,
    approved_by_for_releases,
    get_db,
    get_operator_name,
    require_control_token,
)
from autobridge.control.models import ConnectorRelease, RepairRun
from autobridge.control.schemas import (
    AUTH_401,
    ApprovalOut,
    ApprovePreviewOut,
    OperatorOut,
    ReleaseOut,
    RepairDecisionRequest,
    RollbackRequest,
    RollbackResponse,
)

router = APIRouter(tags=["repairs"])


@router.get("/operator", response_model=OperatorOut)
def get_operator() -> OperatorOut:
    """Return the server-configured operator name (REQ-001)."""
    return OperatorOut(name=get_operator_name())


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


@router.post("/repairs/{repair_id}/approve", response_model=ApprovalOut, responses=AUTH_401)
def approve_repair(repair_id: str, body: RepairDecisionRequest, db: Session = Depends(get_db), _: None = Security(require_control_token)) -> ApprovalOut:
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
    # AR-1-3: approval is bound to this repair run's candidate release. A
    # client-supplied to_release_id is a consistency assertion, never a
    # selector: without this, any caller could activate an arbitrary
    # (stale, rolled-back, unverified) release under an unrelated repair's
    # audit cover.
    if rel is None:
        raise HTTPException(status_code=422, detail=f"no candidate release for repair {repair_id!r}")
    if body.to_release_id is not None and body.to_release_id != rel.id:
        raise HTTPException(status_code=422, detail=f"to_release_id {body.to_release_id!r} is not the candidate release of repair {repair_id!r}")
    to_release_id = rel.id

    from_release_id = body.from_release_id
    # If from not given, try to infer from run's drift -> connector -> active
    if not from_release_id and body.connector_id:
        from autobridge.control.models import Connector

        conn = db.get(Connector, body.connector_id)
        if conn:
            from_release_id = conn.active_release_id

    # The server sets the actor from AUTOBRIDGE_OPERATOR (REQ-001);
    # any client-sent actor is ignored.
    actor = get_operator_name()
    # Perform approval via engine
    try:
        from autobridge.engine.releases import approve_release  # noqa: PLC0415

        approval = approve_release(
            db,
            repair_run_id=repair_id,
            actor=actor,
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


@router.post("/repairs/{repair_id}/reject", response_model=ApprovalOut, responses=AUTH_401)
def reject_repair(repair_id: str, body: RepairDecisionRequest, db: Session = Depends(get_db), _: None = Security(require_control_token)) -> ApprovalOut:
    run = db.get(RepairRun, repair_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"RepairRun {repair_id!r} not found")

    try:
        from autobridge.engine.releases import reject_release  # noqa: PLC0415

        approval = reject_release(
            db,
            repair_run_id=repair_id,
            actor=get_operator_name(),
            from_release_id=body.from_release_id,
            to_release_id=body.to_release_id,
        )
        db.commit()
        db.refresh(approval)
        return _approval_to_out(approval)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/repairs/{repair_id}/approve-proposal", response_model=ApprovalOut, responses=AUTH_401)
def approve_proposal(repair_id: str, body: RepairDecisionRequest, db: Session = Depends(get_db), _: None = Security(require_control_token)) -> ApprovalOut:
    """Approve the model's proposed change on an escalated repair (REQ-049).

    The repair must be ``decision == "escalate"`` with a proposal in its
    escalation bundle. The server applies the proposal to the active
    release, verifier-gates the candidate (visible baseline), activates it
    and records the approval — a business decision by the operator, not a
    verified fix, and never learned into L0 knowledge. Fail-closed: a
    proposal that does not apply or fails the gate answers 422 and the
    repair stays escalated.
    """
    import os as _os  # noqa: PLC0415

    run = db.get(RepairRun, repair_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"RepairRun {repair_id!r} not found")

    actor = get_operator_name()
    try:
        from autobridge.engine.releases import approve_business_decision  # noqa: PLC0415

        _candidate, approval = approve_business_decision(
            db,
            repair_run_id=repair_id,
            actor=actor,
            connector_id=body.connector_id,
            verifier_url=_os.environ.get("VERIFIER_URL") or None,
        )
        db.commit()
        db.refresh(approval)
        return _approval_to_out(approval)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/repairs/{repair_id}/approve-preview", response_model=ApprovePreviewOut)
def approve_preview(repair_id: str, db: Session = Depends(get_db)) -> ApprovePreviewOut:
    """Preview of what Approve/Reject will do, from the real path (REQ-032 §4).

    Read-only: derives every line from the same rows ``POST
    .../approve`` acts on (candidate release, active release, dependents,
    scheduler interval, learn-on-approve rule), so the UI text follows the
    server even when the behaviour changes. Never approves, rejects,
    activates or writes knowledge.
    """
    from sqlalchemy import select

    from autobridge.control.models import Connector, DependencyRegistration

    run = db.get(RepairRun, repair_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"RepairRun {repair_id!r} not found")
    approval = approval_for_repair(db, repair_id)

    rel = (
        db.execute(
            select(ConnectorRelease)
            .where(ConnectorRelease.provenance_repair_run_id == repair_id)
            .order_by(ConnectorRelease.version.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    base_facts: dict[str, object] = {
        "repair_run_id": repair_id,
        "decision": run.decision,
        "vendor": run.vendor,
        "drift_class": run.drift_class,
        "rule_id": run.rule_id,
        "approval": approval.model_dump(mode="json") if approval is not None else None,
    }
    if run.decision == "escalate":
        # REQ-049 item 5: an escalation carrying the model's proposed
        # change can be approved as a business decision (or denied to keep
        # everything as-is). A plain escalation has nothing to approve.
        bundle = run.escalation_bundle if isinstance(run.escalation_bundle, dict) else {}
        proposal = bundle.get("proposal") if isinstance(bundle, dict) else None
        if not isinstance(proposal, dict) or not proposal.get("patch"):
            return ApprovePreviewOut(
                repair_run_id=repair_id,
                approvable=False,
                reason=f"Repair decision is {run.decision!r}, not 'propose': nothing to approve.",
                facts=base_facts,
            )
        from autobridge.control.models import Connector as _Conn
        from autobridge.control.models import DependencyRegistration as _Dep
        from autobridge.control.models import DriftEvent as _Drift

        if approval is not None and approval.decision == "approve":
            return ApprovePreviewOut(
                repair_run_id=repair_id,
                approvable=False,
                reason=f"Already approved as a business decision by {approval.actor}: approving again would mint a duplicate release.",
                facts=base_facts,
            )
        connector_id: str | None = None
        for did in list(run.drift_event_ids or []):
            drift = db.get(_Drift, did)
            if drift is not None and drift.connector_id:
                connector_id = drift.connector_id
                break
        conn = db.get(_Conn, connector_id) if connector_id else None
        active_id = conn.active_release_id if conn is not None else None
        active_rel = db.get(ConnectorRelease, active_id) if active_id else None
        if conn is None or active_rel is None:
            return ApprovePreviewOut(
                repair_run_id=repair_id,
                approvable=False,
                reason="No active release to apply the proposal to: Approve would fail the same way.",
                facts=base_facts,
            )
        from autobridge.engine.releases import next_version as _next_version  # noqa: PLC0415

        to_version = _next_version(db, conn.id)
        dependents = (
            db.execute(
                select(_Dep).where(
                    _Dep.connector_id == conn.id,
                    _Dep.policy == "pause_on_release_change",
                )
            )
            .scalars()
            .all()
        )
        try:
            from autobridge.config import sync_interval_seconds as _interval  # noqa: PLC0415

            interval_s: float | None = _interval()
        except Exception:
            interval_s = None
        label = str(proposal.get("label") or "Approve")
        effect = str(proposal.get("effect") or "")
        proposal_lines = ["Approve: " + label + (f" — {effect}" if effect else "")]
        proposal_lines.append(
            f"Creates release r{to_version} from r{active_rel.version} with the model's proposed change."
        )
        proposal_lines.append(f"Activates r{to_version}.")
        if dependents:
            n = len(dependents)
            proposal_lines.append(f"Pauses {n} dependent consumer{'s' if n != 1 else ''} until the next sync.")
        else:
            proposal_lines.append("No dependent consumers to pause.")
        if interval_s is not None:
            proposal_lines.append(f"The next scheduled sync (in ~{interval_s:g} s) runs on r{to_version}.")
        else:
            proposal_lines.append(f"The next sync runs on r{to_version} (scheduler off).")
        proposal_lines.append(
            "Recorded as a business decision by the operator — not a verified fix: "
            "the verifier confirms the new release syncs and its checks pass, "
            "but makes no claim the new meaning is correct."
        )
        proposal_lines.append(
            "This decision is not kept as learned knowledge: the same drift next time asks again."
        )
        proposal_lines.append(f"Rollback to r{active_rel.version} stays available.")
        deny_lines = [
            "Deny: keep everything as-is, contact the vendor.",
            "No release is created or activated; the connector stays in error and the drift stays open.",
        ]
        return ApprovePreviewOut(
            repair_run_id=repair_id,
            approvable=True,
            reason="",
            approve_lines=proposal_lines,
            reject_lines=deny_lines,
            facts={
                **base_facts,
                "business_decision": True,
                "proposal": {
                    "label": label,
                    "effect": effect,
                    "patch": list(proposal.get("patch") or []),
                },
                "from_release_id": active_id,
                "from_version": active_rel.version,
                "to_version": to_version,
                "active_release_id": active_id,
                "connector_id": conn.id,
                "connector_name": conn.name,
                "dependent_pause_count": len(dependents),
                "sync_interval_s": interval_s,
                "learnable": False,
                "learn_why": "business decisions are never learned",
            },
        )
    if run.decision != "propose":
        return ApprovePreviewOut(
            repair_run_id=repair_id,
            approvable=False,
            reason=f"Repair decision is {run.decision!r}, not 'propose': nothing to approve.",
            facts=base_facts,
        )
    if rel is None:
        return ApprovePreviewOut(
            repair_run_id=repair_id,
            approvable=False,
            reason="No candidate release for this repair: Approve would fail the same way.",
            facts=base_facts,
        )

    conn = db.get(Connector, rel.connector_id)
    active_id = conn.active_release_id if conn is not None else None
    active_rel = db.get(ConnectorRelease, active_id) if active_id else None
    from_version = active_rel.version if active_rel is not None else None
    to_version = rel.version

    dependents = (
        db.execute(
            select(DependencyRegistration).where(
                DependencyRegistration.connector_id == rel.connector_id,
                DependencyRegistration.policy == "pause_on_release_change",
            )
        )
        .scalars()
        .all()
    )
    try:
        from autobridge.config import sync_interval_seconds  # noqa: PLC0415

        interval_s = sync_interval_seconds()
    except Exception:
        interval_s = None

    # Learn-on-approve: the same condition approve_release learns under
    # (REQ-030 2b) — proposing caches nothing, approving writes the entry.
    try:
        from autobridge.repair.knowledge import NO_LEARN_CLASSES, delta_has_content  # noqa: PLC0415

        drift_class = run.drift_class or ""
        if not run.candidate_patch:
            learnable, learn_why = False, "the repair carries no patch"
        elif drift_class in NO_LEARN_CLASSES:
            learnable, learn_why = False, f"{drift_class or 'unknown'} drifts are never learned"
        elif not delta_has_content(run.fingerprint_delta):
            learnable, learn_why = False, "the wire delta carries no change"
        else:
            learnable, learn_why = True, ""
    except Exception:
        learnable, learn_why = False, "knowledge check unavailable"

    vendor = run.vendor or "unknown vendor"
    drift_label = run.drift_class or "this change"
    if from_version is None:
        approve_lines = [f"Creates the first release r{to_version} with this change."]
    else:
        approve_lines = [f"Creates release r{to_version} from r{from_version} with this change."]
    approve_lines.append(f"Activates r{to_version}.")
    if dependents:
        n = len(dependents)
        approve_lines.append(f"Pauses {n} dependent consumer{'s' if n != 1 else ''} until the next sync.")
    else:
        approve_lines.append("No dependent consumers to pause.")
    if interval_s is not None:
        approve_lines.append(f"The next scheduled sync (in ~{interval_s:g} s) runs on r{to_version}.")
    else:
        approve_lines.append(f"The next sync runs on r{to_version} (scheduler off).")
    if learnable:
        approve_lines.append(
            f"This fix is kept as known for {vendor} + {drift_label}: the same drift next time resolves at L0."
        )
    else:
        approve_lines.append(f"This fix is not kept as learned knowledge ({learn_why}).")
    if from_version is not None:
        approve_lines.append(f"Rollback to r{from_version} stays available.")
    else:
        approve_lines.append("No earlier release to roll back to.")
    if approval is not None and approval.decision == "reject":
        approve_lines.append(f"Previously rejected by {approval.actor}: approving now still activates r{to_version}.")

    reject_lines = [
        "Discards the candidate release: no release is created or activated.",
        "The fix is not kept as learned knowledge.",
        "The drift stays open.",
    ]
    facts: dict[str, object] = {
        **base_facts,
        "candidate_release_id": rel.id,
        "candidate_version": to_version,
        "from_release_id": active_id,
        "from_version": from_version,
        "active_release_id": active_id,
        "connector_id": rel.connector_id,
        "connector_name": conn.name if conn is not None else rel.connector_id,
        "dependent_pause_count": len(dependents),
        "sync_interval_s": interval_s,
        "learnable": learnable,
        "learn_why": learn_why,
    }
    return ApprovePreviewOut(
        repair_run_id=repair_id,
        approvable=True,
        reason="",
        approve_lines=approve_lines,
        reject_lines=reject_lines,
        facts=facts,
    )


@router.get("/releases", response_model=list[ReleaseOut])
def list_releases(connector_id: str | None = None, db: Session = Depends(get_db)) -> list[ReleaseOut]:
    from sqlalchemy import select

    q = select(ConnectorRelease).order_by(ConnectorRelease.version.desc())
    if connector_id:
        q = q.where(ConnectorRelease.connector_id == connector_id)
    rows = db.execute(q).scalars().all()
    by_release = approved_by_for_releases(db, [r.id for r in rows])
    return [_release_to_out(r, approved_by=by_release.get(r.id)) for r in rows]


@router.get("/releases/{release_id}", response_model=ReleaseOut)
def get_release(release_id: str, db: Session = Depends(get_db)) -> ReleaseOut:
    r = db.get(ConnectorRelease, release_id)
    if r is None:
        raise HTTPException(status_code=404, detail=f"Release {release_id!r} not found")
    by_release = approved_by_for_releases(db, [r.id])
    return _release_to_out(r, approved_by=by_release.get(r.id))


@router.post("/releases/{release_id}/rollback", response_model=RollbackResponse, responses=AUTH_401)
def rollback_release_endpoint(release_id: str, body: RollbackRequest, db: Session = Depends(get_db), _: None = Security(require_control_token)) -> RollbackResponse:
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
            actor=get_operator_name(),
            repair_run_id=body.repair_run_id,
        )
        db.commit()
        db.refresh(new_release)
        db.refresh(approval)
        return RollbackResponse(new_release=_release_to_out(new_release, approved_by=approval.actor), approval=_approval_to_out(approval))
    except Exception as exc:
        db.rollback()
        # Distinguish not found vs validation
        msg = str(exc)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg) from exc
        raise HTTPException(status_code=422, detail=msg) from exc
