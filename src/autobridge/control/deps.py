# ruff: noqa: B008, TC002, TC003, ARG001, SIM102, SIM105, SIM118, UP035, I001, UP006, UP007, RUF012, B027
"""DB dependency for control plane (P1.W7.1)."""

from __future__ import annotations

import hmac
import os
from collections.abc import Generator

from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from autobridge.control.models import (
    default_database_url,
    engine_from_url,
    init_db,
    session_factory,
)

#: Env var naming the human rung of the ladder (REQ-001). The server sets
#: Approval.actor from this value; the request body actor is never trusted.
OPERATOR_ENV_VAR = "AUTOBRIDGE_OPERATOR"
DEFAULT_OPERATOR = "operator"

#: Optional bearer token guarding state-changing routes (REQ-012). When unset
#: or empty, write routes behave exactly as before (open). When set, every
#: POST/PUT/PATCH/DELETE route requires ``Authorization: Bearer <token>``
#: (constant-time compare) and answers 401 without it. Read-only GETs,
#: ``/healthz``, ``/readyz`` and the SSE stream stay open.
CONTROL_TOKEN_ENV_VAR = "AUTOBRIDGE_CONTROL_TOKEN"

bearer_scheme = HTTPBearer(
    auto_error=False,
    description="Optional control-plane write token (AUTOBRIDGE_CONTROL_TOKEN).",
)


def get_operator_name() -> str:
    """Return the server-configured operator name (never empty)."""
    import os

    return os.environ.get(OPERATOR_ENV_VAR, "").strip() or DEFAULT_OPERATOR


def get_control_token() -> str:
    """Return the configured control write token (empty = auth disabled)."""
    return os.environ.get(CONTROL_TOKEN_ENV_VAR, "")


def require_control_token(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> None:
    """FastAPI dependency guarding state-changing routes (REQ-012).

    Open when ``AUTOBRIDGE_CONTROL_TOKEN`` is unset/empty; otherwise requires
    ``Authorization: Bearer <token>`` compared with ``hmac.compare_digest``.
    The token value is never logged or echoed — 401 details name only the
    missing/invalid header.
    """
    expected = get_control_token()
    if not expected:
        return
    presented = credentials.credentials if credentials is not None else ""
    if not presented or not hmac.compare_digest(presented, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid control token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return


def approval_for_repair(db: Session, repair_run_id: str):
    """Latest approval row on a repair as an ``ApprovalSummaryOut`` (REQ-032).

    Returns ``None`` when no operator has decided on the repair yet. Used
    to populate ``RepairRunOut.approval`` so the repairs list and the
    repair page show approved/applied without extra round trips.
    """
    from sqlalchemy import select

    from autobridge.control.models import Approval
    from autobridge.control.schemas import ApprovalSummaryOut

    row = (
        db.execute(
            select(Approval)
            .where(Approval.repair_run_id == repair_run_id)
            .order_by(Approval.created_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    if row is None:
        return None
    return ApprovalSummaryOut(
        id=row.id,
        actor=row.actor,
        decision=row.decision,
        from_release_id=row.from_release_id,
        to_release_id=row.to_release_id,
        created_at=row.created_at,
    )


def approved_by_for_releases(db: Session, release_ids: list[str]) -> dict[str, str]:
    """Map release id -> actor of its latest approval (REQ-001).

    Used to populate ``ReleaseOut.approved_by`` so the UI can show who
    approved each release. Releases without any approval are absent.
    """
    from sqlalchemy import select

    from autobridge.control.models import Approval

    if not release_ids:
        return {}
    rows = (
        db.execute(
            select(Approval)
            .where(Approval.to_release_id.in_(release_ids))
            .order_by(Approval.created_at.desc())
        )
        .scalars()
        .all()
    )
    out: dict[str, str] = {}
    for a in rows:
        if a.to_release_id is not None and a.to_release_id not in out:
            out[a.to_release_id] = a.actor
    return out

_engine = None
_SessionFactory = None


def get_engine():
    global _engine
    if _engine is None:
        url = default_database_url()
        _engine = engine_from_url(url)
        # Ensure tables exist (Alembic does prod, but local/dev needs init)
        try:
            init_db(_engine)
        except Exception:
            pass
    return _engine


def get_session_factory():
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = session_factory(get_engine())
    return _SessionFactory


def get_db() -> Generator[Session, None, None]:
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine_cache() -> None:
    global _engine, _SessionFactory
    _engine = None
    _SessionFactory = None
