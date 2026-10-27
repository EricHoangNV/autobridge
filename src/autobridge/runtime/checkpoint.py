"""Checkpoint extraction (P1.W3.4, plan §2.4).

Checkpoint is the high-water mark for incremental syncs:
  checkpoint: { field: $.updated_at, mode: max }

The runtime extracts it via JSONPath over canonical records, supporting
lexicographic max/min for ISO timestamps (lexicographic == chronological for
ISO 8601) and numeric for numbers. Mixed types are stringified.
"""

from __future__ import annotations

from typing import Any


def _extract_jsonpath_values(records: list[dict[str, Any]], field_path: str) -> list[Any]:
    """Extract values for checkpoint field across records via JSONPath subset."""
    # We delegate to jsonpath_ng, but keep fallback to direct key lookup
    # for simple $.field paths without needing parse.
    # For checkpoint, field is like $.updated_at or $.meta.updated_at
    # We'll use jsonpath_ng parse if available.
    try:
        from jsonpath_ng import parse as jsonpath_parse
    except ImportError:  # pragma: no cover
        jsonpath_parse = None  # type: ignore[assignment]

    values: list[Any] = []
    if jsonpath_parse is None:
        # Fallback simple dotted path: strip $.
        key = field_path.lstrip("$").lstrip(".")
        for rec in records:
            v = rec.get(key)
            if v is not None:
                values.append(v)
        return values

    try:
        expr = jsonpath_parse(field_path)
    except Exception:
        # Fallback to simple key
        key = field_path.lstrip("$").lstrip(".")
        for rec in records:
            v = rec.get(key)
            if v is not None:
                values.append(v)
        return values

    for rec in records:
        matches = expr.find(rec)
        for m in matches:
            if m.value is not None:
                values.append(m.value)
    return values


def compute_checkpoint(
    records: list[dict[str, Any]],
    field: str,
    mode: str = "max",
) -> Any | None:
    """Compute checkpoint value from records.

    Returns max/min over extracted values, or None if no values.
    Handles mixed types via stringify comparison fallback.
    """
    if not records:
        return None
    values = _extract_jsonpath_values(records, field)
    if not values:
        return None
    try:
        if mode == "max":
            return max(values)
        return min(values)
    except TypeError:
        # Mixed types: stringify
        str_vals = [str(v) for v in values]
        return max(str_vals) if mode == "max" else min(str_vals)


def compute_checkpoint_for_operation(
    records: list[dict[str, Any]],
    checkpoint_spec: Any | None,
) -> Any | None:
    """Extract checkpoint value given OperationSpec.checkpoint or dict.

    Supports both dict {"field": "...", "mode": "max"} and pydantic model.
    """
    if checkpoint_spec is None:
        return None
    field: str | None = None
    mode: str = "max"
    if isinstance(checkpoint_spec, dict):
        field = checkpoint_spec.get("field")
        mode = checkpoint_spec.get("mode", "max")
    elif hasattr(checkpoint_spec, "field"):
        field = checkpoint_spec.field
        mode = getattr(checkpoint_spec, "mode", "max")
    else:
        return None
    if not field:
        return None
    return compute_checkpoint(records, field, mode)


def next_checkpoint_value(
    existing: Any | None,
    new_value: Any | None,
    mode: str = "max",
) -> Any | None:
    """Merge existing checkpoint with new_value for incremental state.

    For max mode, returns greater of the two; for min, lesser.
    """
    if existing is None:
        return new_value
    if new_value is None:
        return existing
    try:
        if mode == "max":
            return max(existing, new_value)
        return min(existing, new_value)
    except TypeError:
        # Mixed stringify
        es = str(existing)
        ns = str(new_value)
        if mode == "max":
            return existing if es >= ns else new_value
        return existing if es <= ns else new_value
