"""Persisted per-connector value baselines (REQ-075).

``money_scale_shift`` (``_observe_money_scale_shift`` in
:mod:`autobridge.drift.signals`) compares a sync's canonical money values
against a baseline: same record count, same keys, money moved by an exact
power of ten (minor/major units). The bench/beacon derive path always held
both observations, but the live path never supplied a baseline — so a live
vendor switching units synced ``Complete`` with no drift (the old
"history-blind" gap).

This module is the one shared mechanism both paths use:

* pure part — :func:`build_money_sketch` bounds a sync summary's canonical
  money values to a small JSON-safe sketch (join keys + money fields only,
  never credentials or raw payloads), and
  :func:`baseline_summary_from_sketch` re-expands a sketch to the
  ``baseline_summary`` shape :func:`signals_from_sync` reads;
* persisted part — :func:`store_connector_baseline` saves the sketch after
  a clean ``Complete`` sync, :func:`load_connector_baseline_summary`
  returns it on the next detection.

Bounding: at most :data:`MAX_BASELINE_ENTITIES` entities and
:data:`MAX_BASELINE_ROWS_PER_ENTITY` rows per entity. Money values are
stored as strings (``Decimal``-safe) and re-parsed by ``_decimal_of`` on
the detecting side, so sketch and live values compare exactly.

Release scoping: a sketch is reused only for the release that stored it.
A unit change shipped inside a new release (e.g. an approved net-total
fix) must never read as a scale shift against the old release's gross
baseline — the first sync on a new release stores fresh instead of
detecting, so at most one sync per release change stays silent.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

#: Hard bounds so one connector's baseline stays small (tens of KB).
MAX_BASELINE_ENTITIES = 32
MAX_BASELINE_ROWS_PER_ENTITY = 200

#: Join-key candidates, same order as ``_canonical_key_of`` in signals.
_KEY_FIELDS = ("reservation_id", "charge_id", "id", "key")


def _money_fields_of(spec: Any) -> dict[str, list[str]]:
    """Canonical money field names per entity (``money`` transform)."""
    from autobridge.drift.signals import _spec_money_fields  # noqa: PLC0415

    return _spec_money_fields(spec)


def _json_scalar(value: Any) -> Any | None:
    """JSON-safe scalar for a sketch cell (None when not a scalar)."""
    if value is None or isinstance(value, bool):
        return None if value is None else value
    if isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        return None  # money is Decimal, never float (DIRECTIVES §4)
    try:
        from decimal import Decimal as _Decimal  # noqa: PLC0415

        if isinstance(value, _Decimal):
            return str(value)
    except Exception:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    try:
        from datetime import date as _date  # noqa: PLC0415

        if isinstance(value, _date):
            return value.isoformat()
    except Exception:
        return None
    return None


def build_money_sketch(
    spec: Any,
    canonical_records: dict[str, list[dict[str, Any]]] | None,
) -> dict[str, list[dict[str, Any]]]:
    """Bound a sync's canonical money values to a JSON-safe sketch.

    Per entity (spec entities with a ``money`` transform only): up to
    ``MAX_BASELINE_ROWS_PER_ENTITY`` rows, each holding only the row's
    join-key fields and its money fields. Everything else — unmapped
    wire leftovers, nested payloads, and anything resembling a secret —
    is dropped by construction (credentials never flow through canonical
    records: the spec carries only ``credential_ref``).
    """
    sketch: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(spec, dict) or not isinstance(canonical_records, dict):
        return sketch
    money_fields = _money_fields_of(spec)
    if not money_fields:
        return sketch
    entities = spec.get("entities")
    entity_names = (
        list(entities)[:MAX_BASELINE_ENTITIES]
        if isinstance(entities, dict)
        else list(money_fields)[:MAX_BASELINE_ENTITIES]
    )
    for entity in entity_names:
        if not isinstance(entity, str):
            continue
        fields = money_fields.get(entity)
        if not fields:
            continue
        rows = canonical_records.get(entity)
        if not isinstance(rows, list) or not rows:
            continue
        kept: list[dict[str, Any]] = []
        for row in rows[:MAX_BASELINE_ROWS_PER_ENTITY]:
            if not isinstance(row, dict):
                continue
            cell: dict[str, Any] = {}
            for kf in _KEY_FIELDS:
                if kf in row and not isinstance(row[kf], (dict, list)):
                    scalar = _json_scalar(row[kf])
                    if scalar is not None:
                        cell[kf] = scalar
            for fname in fields:
                if fname in row:
                    scalar = _json_scalar(row.get(fname))
                    if scalar is not None:
                        cell[fname] = scalar
            if cell:
                kept.append(cell)
        if kept:
            sketch[entity] = kept
    return sketch


def sketch_to_canonical_records(
    sketch: dict[str, Any] | None,
) -> dict[str, list[dict[str, Any]]]:
    """Re-expand a sketch to ``canonical_records`` shape for detection."""
    out: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(sketch, dict):
        return out
    for entity, rows in sketch.items():
        if not isinstance(entity, str) or not isinstance(rows, list):
            continue
        out[entity] = [dict(r) for r in rows if isinstance(r, dict)]
    return out


def baseline_summary_from_sketch(sketch: dict[str, Any] | None) -> dict[str, Any]:
    """Wrap a sketch as the ``baseline_summary`` detection reads.

    The money observer reads ``baseline_summary["canonical_records"]``;
    the wire-shape observer reads ``["operation_results"]`` and keeps its
    existing live fallback (populated sibling) when that is empty — the
    persisted baseline carries money sketches only, never raw payloads.
    """
    return {
        "canonical_records": sketch_to_canonical_records(sketch),
        "operation_results": {},
    }


def _utcnow() -> datetime:
    try:
        from autobridge.control.models import utcnow as _model_utcnow  # noqa: PLC0415

        return _model_utcnow()
    except Exception:
        return datetime.now(UTC)


def store_connector_baseline(
    session: Any,
    *,
    connector_id: str,
    release_id: str,
    spec: Any,
    summary: dict[str, Any] | None,
    sync_run_id: str | None = None,
) -> bool:
    """Persist the sketch for a clean ``Complete`` sync (upsert, one row).

    Returns True when a sketch was stored. Skips (False): non-dict
    summaries, empty sketches (nothing money-shaped to compare later —
    e.g. an idle edge feed at zero records), and sessions whose schema
    predates the baselines table (fail-soft: detection still runs, the
    scale observer just stays silent as before).
    """
    if not isinstance(summary, dict) or not connector_id or not release_id:
        return False
    try:
        sketch = build_money_sketch(spec, summary.get("canonical_records"))
        if not sketch:
            return False
        from autobridge.control.models import ConnectorValueBaseline  # noqa: PLC0415

        row = session.get(ConnectorValueBaseline, connector_id)
        payload = {
            "connector_id": connector_id,
            "release_id": release_id,
            "sketch": sketch,
            "sync_run_id": sync_run_id or "",
            "updated_at": _utcnow(),
        }
        if row is None:
            session.add(ConnectorValueBaseline(**payload))
        else:
            row.release_id = release_id
            row.sketch = dict(sketch)
            row.sync_run_id = sync_run_id or ""
            row.updated_at = payload["updated_at"]
        session.flush()
        return True
    except Exception:
        return False


def load_connector_baseline_summary(
    session: Any,
    *,
    connector_id: str,
    release_id: str,
) -> dict[str, Any] | None:
    """Load the stored sketch as a detection ``baseline_summary``.

    Returns None (detection runs history-blind, as before) when no
    sketch is stored, the sketch belongs to another release, or the
    table is unavailable. Never raises.
    """
    if not connector_id or not release_id:
        return None
    try:
        from autobridge.control.models import ConnectorValueBaseline  # noqa: PLC0415

        row = session.get(ConnectorValueBaseline, connector_id)
        if row is None:
            return None
        if getattr(row, "release_id", None) != release_id:
            return None
        sketch = getattr(row, "sketch", None)
        if not isinstance(sketch, dict) or not sketch:
            return None
        return baseline_summary_from_sketch(sketch)
    except Exception:
        return None
