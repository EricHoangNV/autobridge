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
import re
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


#: What each migration revision adds, oldest first (must mirror
#: ``alembic/versions/``). Used to detect the schema an unversioned database
#: actually has: stamp the newest satisfied prefix, then upgrade head.
#: Never stamp head blindly — a create_all-built database from an older
#: release has tables but lacks newer columns (owner check, REQ-021).
#: ``checks`` maps ``"<table>.<column>"`` to the CHECK's expected value set
#: (REQ-055 item 1): CHECK-only migrations (0011, 0013, 0015) are invisible
#: to tables/columns detection, and stamping over one silently skips its
#: DDL — the exact failure that left live databases rejecting 'approved'.
_REVISION_EFFECTS: tuple[tuple[str, dict[str, Any]], ...] = (
    (
        "0001_core_data_model",
        {
            "tables": [
                "connectors",
                "build_runs",
                "repair_runs",
                "connector_releases",
                "sync_runs",
                "drift_events",
                "approvals",
                "evidence_records",
                "model_calls",
                "dependency_registrations",
            ],
            "columns": [],
            "checks": {},
        },
    ),
    ("0002_job_queue", {"tables": ["jobs"], "columns": [], "checks": {}}),
    (
        "0003_escalation_bundle",
        {"tables": [], "columns": ["repair_runs.escalation_bundle"], "checks": {}},
    ),
    ("0004_discovery_records", {"tables": ["discovery_records"], "columns": [], "checks": {}}),
    ("0005_model_call_archive", {"tables": ["model_call_archive"], "columns": [], "checks": {}}),
    (
        "0006_repair_ladder_trace",
        {"tables": [], "columns": ["repair_runs.ladder_trace"], "checks": {}},
    ),
    (
        "0007_repair_knowledge_provenance",
        {
            "tables": [],
            "columns": ["repair_runs.vendor", "repair_runs.drift_signature"],
            "checks": {},
        },
    ),
    (
        "0008_model_call_reasoning_tokens",
        {
            "tables": [],
            "columns": ["model_calls.reasoning_tokens", "model_call_archive.reasoning_tokens"],
            "checks": {},
        },
    ),
    (
        "0009_req030_persistence",
        {
            "tables": ["pabx_link_events"],
            "columns": [
                "repair_runs.drift_class",
                "repair_runs.fingerprint_delta",
                "model_calls.mode",
                "model_calls.captured_at",
                "model_calls.outcome",
                "model_call_archive.mode",
                "model_call_archive.captured_at",
                "model_call_archive.outcome",
            ],
            "checks": {},
        },
    ),
    ("0010_scheduler_pause", {"tables": ["scheduler_state"], "columns": [], "checks": {}}),
    (
        # REQ-055 item 1: the CHECK contents are the detectable effect —
        # tables/columns alone always pass and let the stamp skip the DDL.
        "0011_repair_running_states",
        {
            "tables": [],
            "columns": [],
            "checks": {
                "repair_runs.decision": ["propose", "escalate", "reject", "climbing", "failed"],
            },
        },
    ),
    (
        "0012_model_call_prompt_ref",
        {
            "tables": [],
            "columns": [
                "model_calls.prompt_id",
                "model_calls.prompt_version",
                "model_calls.prompt_hash",
                "model_call_archive.prompt_id",
                "model_call_archive.prompt_version",
                "model_call_archive.prompt_hash",
            ],
            "checks": {},
        },
    ),
    (
        # REQ-055 item 1: same as 0011 — the terminal values must be present.
        "0013_repair_terminal_decisions",
        {
            "tables": [],
            "columns": [],
            "checks": {
                "repair_runs.decision": [
                    "propose",
                    "escalate",
                    "reject",
                    "climbing",
                    "failed",
                    "approved",
                    "rolled_back",
                ],
            },
        },
    ),
    (
        "0014_refused_build_attempts",
        {"tables": ["refused_build_attempts"], "columns": [], "checks": {}},
    ),
    (
        # REQ-055 item 1: re-enforces the 0013 value set on databases whose
        # stamp skipped 0013's DDL. Same detectable effect as 0013.
        "0015_repair_decision_check_repair",
        {
            "tables": [],
            "columns": [],
            "checks": {
                "repair_runs.decision": [
                    "propose",
                    "escalate",
                    "reject",
                    "climbing",
                    "failed",
                    "approved",
                    "rolled_back",
                ],
            },
        },
    ),
)


def _check_values(inspector: Any, table: str, column: str) -> set[str] | None:
    """Value set of the ``<column>`` membership CHECK on *table*.

    SQLite stores it as ``decision IN ('a', 'b', ...)``; Postgres reflects
    it as ``(decision)::text = ANY (ARRAY['a', ...])`` — both reduce to the
    quoted literals once the column is named. Returns ``None`` when CHECK
    reflection is unavailable or unparseable (fail-open: detection treats
    the check as satisfied rather than looping upgrades on an exotic
    dialect), the empty set when the table has no such CHECK.
    """
    try:
        checks = inspector.get_check_constraints(table)
    except Exception:
        return None
    for chk in checks or []:
        sql = str((chk or {}).get("sqltext") or "")
        if re.search(rf"\b{re.escape(column)}\b", sql) is None:
            continue
        vals = set(re.findall(r"'([^']*)'", sql))
        return vals if vals else None
    return set()


def _detect_revision(url: str) -> str | None:
    """Newest revision whose effects all exist in the database.

    ``"base"`` when even 0001's tables are missing (empty database), ``None``
    only when inspection itself fails.
    """
    from sqlalchemy import create_engine, inspect  # noqa: PLC0415

    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            insp = inspect(conn)
            tables = set(insp.get_table_names())
            columns: dict[str, set[str]] = {}
            for table in tables:
                try:
                    columns[table] = {c["name"] for c in insp.get_columns(table)}
                except Exception:
                    columns[table] = set()
            # REQ-055 item 1: CHECK contents are read while the connection
            # is open (an inspector outlives nothing after dispose).
            check_values: dict[str, set[str] | None] = {}
            for _revision, effects in _REVISION_EFFECTS:
                for dotted in effects.get("checks", {}):
                    if dotted not in check_values:
                        table, _, column = dotted.partition(".")
                        check_values[dotted] = _check_values(insp, table, column)
    except Exception:
        return None
    finally:
        engine.dispose()
    detected: str | None = "base"
    for revision, effects in _REVISION_EFFECTS:
        tables_ok = all(t in tables for t in effects.get("tables", []))
        cols_ok = True
        for dotted in effects.get("columns", []):
            table, _, column = dotted.partition(".")
            if column not in columns.get(table, set()):
                cols_ok = False
                break
        checks_ok = True
        for dotted, expected in effects.get("checks", {}).items():
            found = check_values.get(dotted)
            if found is not None and not set(expected) <= found:
                checks_ok = False
                break
        if not (tables_ok and cols_ok and checks_ok):
            break
        detected = revision
    return detected


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
    if current == head and _detect_revision(url) != head:
        # The version row lies (stamped head, schema older): fall through
        # and repair by detection instead of trusting it.
        current = None
    if current is not None and current != head:
        # The version row lags the schema: create_all ran with newer models
        # (dev/test databases), so the tables already carry a later
        # revision's effects. Trust the schema, stamp the detection —
        # upgrading would re-add existing columns and fail.
        detected = _detect_revision(url)
        order = [rev for rev, _ in _REVISION_EFFECTS]
        if (
            detected is not None
            and detected in order
            and current in order
            and order.index(detected) > order.index(current)
        ):
            command.stamp(cfg, detected)
            current = detected
    if current is None:
        # Unversioned database: detect the schema it actually has and stamp
        # that revision — never head blindly (a create_all-built database
        # from an older release lacks newer columns; stamping head would
        # skip their migrations and 500 later). Then upgrade applies the rest.
        detected = _detect_revision(url)
        if detected is None:
            raise RuntimeError(f"cannot inspect database schema at {url}")
        command.stamp(cfg, detected)
        current = _current_version(url)
    if current != head:
        command.upgrade(cfg, "head")
        current = _current_version(url)
    if current != head:
        raise RuntimeError(f"migration did not reach head {head} (now at {current})")
    _ensured[url] = head
    return head
