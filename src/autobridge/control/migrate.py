"""Alembic migrations at startup (REQ-021 owner check after sub-step D).

``Base.metadata.create_all`` adds tables but never adds columns, so a
database file kept across runs (``.local/autobridge.db``, a Docker volume)
goes stale: new columns like ``repair_runs.ladder_trace`` are missing and
endpoints answer 500. Every process that serves the control schema runs
:func:`ensure_migrated` on startup (control lifespan covers Docker and
local mode — same app module), and ``POST /demo/reset`` runs it before
wiping (an old database must reset cleanly, never be deleted by hand).

Loud, not silent: migration failure raises — starting without a current
schema would only move the 500s later.
"""

from __future__ import annotations

import os
import pathlib
from typing import Any

#: Alembic script location override (tests point it at the repo ``alembic/``).
ALEMBIC_DIR_ENV = "ALEMBIC_DIR"

_ensured: dict[str, str] = {}


def script_dir() -> pathlib.Path:
    """Locate the ``alembic/`` script directory.

    ``ALEMBIC_DIR`` wins; otherwise the ``alembic/`` sibling of the repo
    root (three parents above this file: control -> autobridge -> src).
    The Docker engine image copies ``alembic/`` next to ``src/`` the same way.
    """
    override = (os.environ.get(ALEMBIC_DIR_ENV, "") or "").strip()
    if override:
        return pathlib.Path(override)
    return pathlib.Path(__file__).resolve().parents[3] / "alembic"


def _current_version(url: str) -> str | None:
    from sqlalchemy import create_engine, text  # noqa: PLC0415

    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            try:
                row = conn.execute(text("SELECT version_num FROM alembic_version")).fetchone()
            except Exception:
                return None  # no version table: pre-alembic or empty database
            return str(row[0]) if row else None
    finally:
        engine.dispose()


def _has_full_schema(url: str) -> bool:
    from sqlalchemy import create_engine, inspect  # noqa: PLC0415

    from autobridge.control.models import Base  # noqa: PLC0415

    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            present = set(inspect(conn).get_table_names())
    finally:
        engine.dispose()
    expected = {t for t in Base.metadata.tables if t != "alembic_version"}
    return expected <= present
    from sqlalchemy import create_engine, text  # noqa: PLC0415

    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            try:
                row = conn.execute(text("SELECT version_num FROM alembic_version")).fetchone()
            except Exception:
                return None  # no version table: pre-alembic or empty database
            return str(row[0]) if row else None
    finally:
        engine.dispose()


def ensure_migrated(engine_or_url: Any = None) -> str:
    """Upgrade the control database to the Alembic head; return the version.

    * ``None`` resolves like the app (``DATABASE_URL`` env, else the local
      default file).
    * ``:memory:`` SQLite is skipped (tests build it from current metadata
      with ``create_all``; a second connection would see an empty database).
    * Already-current databases cost one ``SELECT`` and no migration run.
    * Failures raise — callers must not serve a stale schema quietly.
    """
    from alembic.config import Config  # noqa: PLC0415
    from alembic.script import ScriptDirectory  # noqa: PLC0415

    from alembic import command  # noqa: PLC0415
    from autobridge.control.models import default_database_url  # noqa: PLC0415

    if engine_or_url is None:
        url = default_database_url()
    elif isinstance(engine_or_url, str):
        url = engine_or_url
    else:
        url = str(engine_or_url.url)
    if ":memory:" in url:
        return "memory-skipped"

    scripts = script_dir()
    if not (scripts / "env.py").is_file():
        raise RuntimeError(f"alembic script directory missing: {scripts}")
    cfg = Config()
    cfg.set_main_option("script_location", str(scripts))
    cfg.set_main_option("sqlalchemy.url", url)
    heads = ScriptDirectory.from_config(cfg).get_heads()
    head = sorted(heads)[-1] if len(heads) > 1 else (heads[0] if heads else "head")
    if _ensured.get(url) == head and _current_version(url) == head:
        return head
    current = _current_version(url)
    if current is None and _has_full_schema(url):
        # Adopted database (create_all-built, no version row): stamp head
        # instead of re-creating tables.
        command.stamp(cfg, head)
        current = _current_version(url)
    if current != head:
        command.upgrade(cfg, "head")
        current = _current_version(url)
    if current != head:
        raise RuntimeError(f"migration did not reach head {head} (now at {current})")
    _ensured[url] = head
    return head
