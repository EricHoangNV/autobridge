# ruff: noqa: B008, TC002, TC003, ARG001, SIM102, SIM105, SIM118, UP035, I001, UP006, UP007, RUF012, B027
"""DB dependency for control plane (P1.W7.1)."""

from __future__ import annotations

from collections.abc import Generator

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


def get_operator_name() -> str:
    """Return the server-configured operator name (never empty)."""
    import os

    return os.environ.get(OPERATOR_ENV_VAR, "").strip() or DEFAULT_OPERATOR


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
