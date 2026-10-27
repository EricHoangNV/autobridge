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
