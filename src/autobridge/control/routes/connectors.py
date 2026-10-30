# ruff: noqa: B008, TC002, TC003, ARG001, SIM102, SIM105, SIM118, UP035, I001, UP006, UP007, RUF012, B027
"""Connector endpoints (P1.W7.1)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Security
from sqlalchemy import select
from sqlalchemy.orm import Session

from autobridge.control.demo_targets import normalize_connector_base_url
from autobridge.control.deps import approved_by_for_releases, get_db, get_operator_name, require_control_token
from autobridge.control.models import (
    Connector,
    ConnectorRelease,
    DependencyRegistration,
    DriftEvent,
    SyncRun,
)
from autobridge.control.schemas import (
    AUTH_401,
    CONFLICT_409,
    DOMAIN_ERROR_422,
    ConnectorCreate,
    ConnectorDetail,
    ConnectorOut,
    DependencyCreate,
    DependencyOut,
    DriftEventOut,
    ReferenceReleaseRequest,
    ReferenceReleaseResponse,
    ReleaseOut,
    SyncRunOut,
)

router = APIRouter(prefix="/connectors", tags=["connectors"])


def _connector_to_out(c: Connector, db: Session | None = None) -> ConnectorOut:
    # REQ-030 item 4: state/activity/last_sync_at are computed server-side
    # so every surface (list, detail, simulators index) agrees. Without a
    # session (unit callers) the fields stay at their defaults.
    state = "working"
    activity = ""
    last_sync_at = None
    if db is not None:
        try:
            from autobridge.control.connector_state import connector_state  # noqa: PLC0415

            computed = connector_state(db, c.id)
            state = str(computed.get("state", "working"))
            activity = str(computed.get("activity", ""))
            last_sync_at = computed.get("last_sync_at")
        except Exception:
            state, activity = "degrading", "state unavailable"
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
        state=state,
        activity=activity,
        last_sync_at=last_sync_at,
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


@router.post("", response_model=ConnectorOut, status_code=201, responses={**DOMAIN_ERROR_422, **AUTH_401})
def create_connector(body: ConnectorCreate, db: Session = Depends(get_db), _: None = Security(require_control_token)) -> ConnectorOut:
    rt = body.runtime_target or {"kind": "local_process"}
    # Validate runtime_target kind (edge shipped in P2.S2.1)
    kind = rt.get("kind", "local_process")
    if kind not in ("local_process", "docker_sandbox", "edge"):
        raise HTTPException(status_code=422, detail=f"invalid runtime_target.kind {kind!r}")

    # REQ-027 sub-step 1: a simulator address is stored in the form that
    # works in the mode this stack runs in (Compose service DNS vs host
    # loopback); real vendor hosts and ${VAR} placeholders pass through.
    stored_base_url = normalize_connector_base_url(body.base_url) or body.base_url
    conn = Connector(
        name=body.name,
        vendor_docs_ref=body.vendor_docs_ref,
        base_url=stored_base_url,
        credential_ref=body.credential_ref,
        target_entities=list(body.target_entities),
        runtime_target=dict(rt),
        health_status="healthy",
    )
    db.add(conn)
    db.flush()
    db.commit()
    db.refresh(conn)
    return _connector_to_out(conn, db)


@router.get("", response_model=list[ConnectorOut])
def list_connectors(db: Session = Depends(get_db)) -> list[ConnectorOut]:
    rows = db.execute(select(Connector).order_by(Connector.created_at)).scalars().all()
    return [_connector_to_out(c, db) for c in rows]


@router.get("/{connector_id}", response_model=ConnectorDetail)
def get_connector(connector_id: str, db: Session = Depends(get_db)) -> ConnectorDetail:
    conn = db.get(Connector, connector_id)
    if conn is None:
        raise HTTPException(status_code=404, detail=f"Connector {connector_id!r} not found")
    base = _connector_to_out(conn, db)    # Releases
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

    # REQ-050 item 4: every docs build attempt, refused ones included
    # (they persist no release but stay recorded on the BuildRun).
    from autobridge.control.models import BuildRun as _BuildRun  # noqa: PLC0415
    from autobridge.control.models import RefusedBuildAttempt as _Refused  # noqa: PLC0415
    from autobridge.control.schemas import BuildAttemptOut as _BuildAttemptOut  # noqa: PLC0415

    build_rows = db.execute(
        select(_BuildRun).where(_BuildRun.connector_id == connector_id).order_by(_BuildRun.created_at)
    ).scalars().all()
    attempt_outs = [
        _BuildAttemptOut(
            id=b.id,
            spec_vocabulary=str((b.inputs or {}).get("spec_vocabulary", "") or ""),
            success=bool(isinstance(b.result, dict) and b.result.get("success")),
            degradation_reason=(
                str(b.result.get("degradation_reason"))
                if isinstance(b.result, dict) and b.result.get("degradation_reason")
                else None
            ),
            release_id=(
                str(b.result.get("release_id"))
                if isinstance(b.result, dict) and b.result.get("release_id")
                else None
            ),
            created_at=b.created_at,
        )
        for b in build_rows
    ]
    # REQ-053 item 7: vocabulary refusals recorded before this connector
    # existed (no connector row then) join the same history by name, so
    # the page shows the earlier refusal (e.g. Meridian v1) beside the
    # build in use. Already-represented vocabularies are not duplicated.
    seen_vocabularies = {a.spec_vocabulary for a in attempt_outs if a.spec_vocabulary}
    try:
        refused_rows = (
            db.execute(
                select(_Refused)
                .where(_Refused.connector_name == conn.name)
                .order_by(_Refused.created_at)
            )
            .scalars()
            .all()
        )
    except Exception:
        refused_rows = []
    for refused in refused_rows:
        if refused.spec_vocabulary and refused.spec_vocabulary in seen_vocabularies:
            continue
        attempt_outs.append(
            _BuildAttemptOut(
                id=refused.id,
                spec_vocabulary=refused.spec_vocabulary or "",
                success=False,
                degradation_reason=refused.reason or None,
                release_id=None,
                created_at=refused.created_at,
            )
        )
        if refused.spec_vocabulary:
            seen_vocabularies.add(refused.spec_vocabulary)
    attempt_outs.sort(key=lambda a: str(a.created_at or ""))

    return ConnectorDetail(
        **base.model_dump(),
        releases=release_outs,
        builds=attempt_outs,
        last_sync=last_sync,
        open_drift=open_drift_out,
        dependencies=dep_outs,
    )


@router.post("/{connector_id}/dependencies", response_model=DependencyOut, status_code=201, responses={**CONFLICT_409, **AUTH_401})
def create_dependency(connector_id: str, body: DependencyCreate, db: Session = Depends(get_db), _: None = Security(require_control_token)) -> DependencyOut:
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


@router.post(
    "/{connector_id}/releases/{release_id}/activate",
    response_model=ConnectorOut,
    responses={**DOMAIN_ERROR_422, **AUTH_401},
)
def activate_connector_release(
    connector_id: str, release_id: str, db: Session = Depends(get_db), _: None = Security(require_control_token)
) -> ConnectorOut:
    """Activate a build candidate so it becomes runnable (REQ-013).

    The first build's release is auto-activated (see ``run_build_handler``);
    later build candidates need this explicit operator step. Repair
    candidates cannot be activated here — they go through repair
    approval — and gate-blocked or rejected releases stay unrunnable (422).
    The actor is the server-configured operator (REQ-001), never a client
    value.
    """
    conn = db.get(Connector, connector_id)
    if conn is None:
        raise HTTPException(status_code=404, detail=f"Connector {connector_id!r} not found")
    if db.get(ConnectorRelease, release_id) is None:
        raise HTTPException(status_code=404, detail=f"Release {release_id!r} not found")
    actor = get_operator_name()
    try:
        from autobridge.engine.releases import ReleaseError, activate_release  # noqa: PLC0415

        activate_release(db, connector_id=connector_id, release_id=release_id, actor=actor)
        db.commit()
    except ReleaseError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    refreshed = db.get(Connector, connector_id)
    if refreshed is None:  # pragma: no cover - connector cannot vanish mid-request
        raise HTTPException(status_code=404, detail=f"Connector {connector_id!r} not found")
    return _connector_to_out(refreshed, db)


#: Spec keys that must only ever name a credential reference, never carry
#: a value (DIRECTIVES §6). A reference-release registration POSTs a full
#: spec document, so the server rejects smuggled values fail-closed —
#: placeholders (``${ENV}``) and empty values stay allowed.
_SECRET_VALUE_KEYS = frozenset(
    {"api_key", "api_secret", "token", "access_token", "secret", "password", "private_key", "authorization"}
)


def _reject_secret_values(node: object, path: str = "$") -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}"
            if (
                isinstance(key, str)
                and key.lower() in _SECRET_VALUE_KEYS
                and isinstance(value, str)
                and value.strip()
                and not value.strip().startswith("${")
            ):
                raise HTTPException(
                    status_code=422,
                    detail=f"spec must carry credential_ref, never a credential value (DIRECTIVES §6): {child}",
                )
            _reject_secret_values(value, child)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            _reject_secret_values(value, f"{path}[{i}]")


@router.post(
    "/{connector_id}/reference-release",
    response_model=ReferenceReleaseResponse,
    responses={**DOMAIN_ERROR_422, **AUTH_401},
)
def register_reference_release(
    connector_id: str, body: ReferenceReleaseRequest, db: Session = Depends(get_db), _: None = Security(require_control_token)
) -> ReferenceReleaseResponse:
    """Register a pre-built reference ConnectorSpec as a release (REQ-031).

    The demo seeds HarborPMS from its reusable reference release (money
    invariant included) through the control API — the same API the UI
    uses — instead of compiling it from docs. The client sends the parsed
    spec (it read the file; the server never reads files, AR-2-2). The
    candidate is verifier-gated exactly like a docs build (gate-before-
    persist: blocked/unreachable → 422, nothing persisted). A build row
    records the registration (vocabulary, provenance label) so the
    knowledge page shows it like any build; the release activates when
    the connector has no active release yet (first-build rule, REQ-013).
    """
    import os  # noqa: PLC0415

    from autobridge.control.models import BuildRun  # noqa: PLC0415

    conn = db.get(Connector, connector_id)
    if conn is None:
        raise HTTPException(status_code=404, detail=f"Connector {connector_id!r} not found")
    if not isinstance(body.spec, dict) or not body.spec:
        raise HTTPException(status_code=422, detail="spec must be a non-empty ConnectorSpec document")
    _reject_secret_values(body.spec)

    vocabulary: str | None = body.spec_vocabulary
    if vocabulary is None:
        version = body.spec.get("spec_version") if isinstance(body.spec, dict) else None
        vocabulary = f"v{version}" if version in (1, 2, 3) else "v1"

    build_run = BuildRun(
        connector_id=conn.id,
        state="succeeded",
        inputs={
            "docs_source": body.docs_source or "reference",
            "spec_vocabulary": vocabulary,
            "connector_id": conn.id,
            "scenario_id": body.scenario_id,
            "reference_release": True,
        },
        ladder_trace=[],
        model_usage={"calls": 0, "small_tokens": 0, "frontier_tokens": 0},
        evidence_ids=[],
        draft_spec=dict(body.spec),
        result={"success": True, "verified": False, "registration": "reference"},
    )
    db.add(build_run)
    db.flush()

    verifier_url = body.verifier_url or os.environ.get("VERIFIER_URL")
    try:
        from autobridge.engine.releases import ReleaseError, create_release  # noqa: PLC0415
        from autobridge.engine.verification import GateBlockedError  # noqa: PLC0415

        release = create_release(
            db,
            connector_id=conn.id,
            spec=dict(body.spec),
            provenance_build_run_id=build_run.id,
            verifier_url=verifier_url,
            scenario_id=body.scenario_id or "baseline",
        )
        db.flush()
    except GateBlockedError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=f"reference release blocked by the verifier gate: {exc}") from exc
    except ReleaseError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    report_id = release.verification_report_id
    build_run.ladder_trace = [
        {
            "stage": "verifier",
            "verdict": "pass" if report_id else "skipped",
            "scenario": body.scenario_id or "baseline",
            "gate": "before_persist",
        }
    ]
    build_run.result = {
        "success": True,
        "verified": bool(report_id),
        "release_id": release.id,
        "registration": "reference",
    }
    db.flush()

    activated = False
    if conn.active_release_id is None:
        from autobridge.engine.releases import _activate  # noqa: PLC0415

        _activate(db, conn.id, release.id, actor="reference-release:first-build")
        db.flush()
        activated = True
    db.commit()
    return ReferenceReleaseResponse(
        release_id=release.id,
        build_run_id=build_run.id,
        connector_id=conn.id,
        version=release.version,
        activated=activated,
        active_release_id=conn.active_release_id,
        verified=bool(report_id),
        verification_report_id=report_id,
    )
