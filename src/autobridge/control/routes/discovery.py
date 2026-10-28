# ruff: noqa: B008, TC002, TC003, ARG001, SIM102, SIM105, SIM118, UP035, I001, UP006, UP007, RUF012, B027
"""Discovery static import endpoints (P2.S3.1, plan §5 S3).

Reserved ``DiscoveryResult`` (``schemas/discovery-result.v0.json``) rows are
imported statically via ``POST /discovery/import`` and listed via
``GET /discovery``. Status moves only through the explicit transition
endpoints below; any other transition is a 422. The v1 engine neither
produces nor consumes these rows.

Trust boundary: stdlib + SQLAlchemy + pydantic only; never imports
``verifier`` or ``simulators``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Security
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from autobridge.control.deps import get_db, require_control_token
from autobridge.control.models import (
    DISCOVERY_STATUSES,
    DISCOVERY_TRANSITIONS,
    DiscoveryRecord,
)
from autobridge.control.schemas import (
    AUTH_401,
    CONFLICT_409,
    DOMAIN_ERROR_422,
    DiscoveryImportRequest,
    DiscoveryImportResponse,
    DiscoveryRecordOut,
)
from autobridge.spec.entities import DiscoveryResult

router = APIRouter(prefix="/discovery", tags=["discovery"])


def _to_out(r: DiscoveryRecord) -> DiscoveryRecordOut:
    return DiscoveryRecordOut(
        id=r.id,
        discovery_id=r.discovery_id,
        vendor=r.vendor,
        base_url=r.base_url,
        candidate_entities=list(r.candidate_entities or []),
        evidence_refs=list(r.evidence_refs or []),
        status=r.status,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


def _validate_record(payload: dict[str, object]) -> DiscoveryResult:
    """Validate one record against the reserved DiscoveryResult contract.

    The pydantic model mirrors ``schemas/discovery-result.v0.json`` field
    for field (unit + e2e tests assert file parity); the request path uses
    the model only so validation is filesystem-independent inside the
    container image, which ships ``src/`` without ``schemas/``.
    """
    try:
        return DiscoveryResult.model_validate(payload)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"invalid DiscoveryResult: {exc}") from exc


@router.post(
    "/import",
    response_model=DiscoveryImportResponse,
    status_code=201,
    responses={**DOMAIN_ERROR_422, **CONFLICT_409, **AUTH_401},
)
def import_discoveries(
    body: DiscoveryImportRequest, db: Session = Depends(get_db), _: None = Security(require_control_token)
) -> DiscoveryImportResponse:
    seen: set[str] = set()
    imported: list[DiscoveryRecord] = []
    for item in body.records:
        payload: dict[str, object] = item.model_dump(mode="json", exclude_none=False)
        # Drop explicit None created_at so both validators see the same shape
        # the schema file accepts (absent beats null for format: date-time).
        if payload.get("created_at") is None:
            payload.pop("created_at", None)
        if item.status not in DISCOVERY_STATUSES:
            raise HTTPException(
                status_code=422,
                detail=f"unknown discovery status {item.status!r}; must be one of {sorted(DISCOVERY_STATUSES)}",
            )

        if item.discovery_id in seen:
            raise HTTPException(
                status_code=422,
                detail=f"duplicate discovery_id {item.discovery_id!r} in import batch",
            )
        seen.add(item.discovery_id)
        _validate_record(payload)
        existing = db.execute(
            select(DiscoveryRecord).where(
                DiscoveryRecord.discovery_id == item.discovery_id
            )
        ).scalars().first()
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail=f"discovery_id {item.discovery_id!r} already imported",
            )
        row = DiscoveryRecord(
            discovery_id=item.discovery_id,
            vendor=item.vendor,
            base_url=item.base_url,
            candidate_entities=list(item.candidate_entities or []),
            evidence_refs=list(item.evidence_refs or []),
            status=item.status,
        )
        db.add(row)
        imported.append(row)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="discovery_id already imported") from exc
    for row in imported:
        db.refresh(row)
    outs = [_to_out(r) for r in imported]
    return DiscoveryImportResponse(imported=outs, total=len(outs))


@router.get("", response_model=list[DiscoveryRecordOut])
def list_discoveries(db: Session = Depends(get_db)) -> list[DiscoveryRecordOut]:
    rows = db.execute(
        select(DiscoveryRecord).order_by(DiscoveryRecord.created_at)
    ).scalars().all()
    return [_to_out(r) for r in rows]


@router.get("/{discovery_id}", response_model=DiscoveryRecordOut)
def get_discovery(discovery_id: str, db: Session = Depends(get_db)) -> DiscoveryRecordOut:
    row = db.execute(
        select(DiscoveryRecord).where(DiscoveryRecord.discovery_id == discovery_id)
    ).scalars().first()
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"Discovery {discovery_id!r} not found"
        )
    return _to_out(row)


def _transition(
    db: Session, discovery_id: str, action: str
) -> DiscoveryRecordOut:
    row = db.execute(
        select(DiscoveryRecord).where(DiscoveryRecord.discovery_id == discovery_id)
    ).scalars().first()
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"Discovery {discovery_id!r} not found"
        )
    allowed = DISCOVERY_TRANSITIONS.get(row.status, {})
    if action not in allowed:
        raise HTTPException(
            status_code=422,
            detail=(
                f"illegal transition: status {row.status!r} cannot {action!r}; "
                f"allowed from here: {sorted(allowed.values()) or ['none']}"
            ),
        )
    row.status = allowed[action]
    db.commit()
    db.refresh(row)
    return _to_out(row)


@router.post(
    "/{discovery_id}/request-confirmation",
    response_model=DiscoveryRecordOut,
    responses={**DOMAIN_ERROR_422, **AUTH_401},
)
def request_confirmation(discovery_id: str, db: Session = Depends(get_db), _: None = Security(require_control_token)) -> DiscoveryRecordOut:
    return _transition(db, discovery_id, "request-confirmation")


@router.post(
    "/{discovery_id}/authorize",
    response_model=DiscoveryRecordOut,
    responses={**DOMAIN_ERROR_422, **AUTH_401},
)
def authorize_discovery(discovery_id: str, db: Session = Depends(get_db), _: None = Security(require_control_token)) -> DiscoveryRecordOut:
    return _transition(db, discovery_id, "authorize")


@router.post(
    "/{discovery_id}/connect",
    response_model=DiscoveryRecordOut,
    responses={**DOMAIN_ERROR_422, **AUTH_401},
)
def connect_discovery(discovery_id: str, db: Session = Depends(get_db), _: None = Security(require_control_token)) -> DiscoveryRecordOut:
    return _transition(db, discovery_id, "connect")


@router.post(
    "/{discovery_id}/mark-healing",
    response_model=DiscoveryRecordOut,
    responses={**DOMAIN_ERROR_422, **AUTH_401},
)
def mark_healing(discovery_id: str, db: Session = Depends(get_db), _: None = Security(require_control_token)) -> DiscoveryRecordOut:
    return _transition(db, discovery_id, "mark-healing")


@router.post(
    "/{discovery_id}/resolve",
    response_model=DiscoveryRecordOut,
    responses={**DOMAIN_ERROR_422, **AUTH_401},
)
def resolve_healing(discovery_id: str, db: Session = Depends(get_db), _: None = Security(require_control_token)) -> DiscoveryRecordOut:
    return _transition(db, discovery_id, "resolve")
