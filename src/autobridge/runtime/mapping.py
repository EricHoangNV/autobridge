"""Entity mapping — raw API records → canonical via FieldMapping + transforms (P1.W3.4).

Applies per-entity field paths + closed transform pipeline, producing canonical
records that validate against the entity schemas.

Unknown enum → raises TransformBlocked which the sync layer maps to Blocked.
Money is Decimal end-to-end.
Dates are property-local business dates via date_local / business_date transforms.
"""

from __future__ import annotations

from typing import Any

from autobridge.runtime.transforms import TransformBlocked, apply_transform_pipeline

try:
    from jsonpath_ng import parse as jsonpath_parse
except ImportError:  # pragma: no cover
    jsonpath_parse = None  # type: ignore[assignment]


def _jsonpath_extract_one(data: Any, path: str) -> Any:
    """Extract single value via JSONPath subset; None if missing."""
    if jsonpath_parse is None:
        raise RuntimeError("jsonpath-ng required for JSONPath")
    try:
        expr = jsonpath_parse(path)
    except Exception:
        return None
    matches = expr.find(data)
    if not matches:
        return None
    return matches[0].value


def _resolve_timezone(source_spec: Any | None, record: dict[str, Any] | None = None) -> str | None:
    """Resolve timezone from spec.source.timezone_source.

    Returns IANA string or None (caller falls back to UTC).
    """
    if source_spec is None:
        return None
    tz_src = getattr(source_spec, "timezone_source", None)
    if tz_src is None and isinstance(source_spec, dict):
        tz_src = source_spec.get("timezone_source")
    if tz_src is None:
        return None

    kind = getattr(tz_src, "kind", None) if not isinstance(tz_src, dict) else tz_src.get("kind")
    if kind == "static":
        val = getattr(tz_src, "value", None) if not isinstance(tz_src, dict) else tz_src.get("value")
        return str(val) if val else None
    if kind == "field":
        path = getattr(tz_src, "path", None) if not isinstance(tz_src, dict) else tz_src.get("path")
        if path and record is not None:
            return _jsonpath_extract_one(record, path)
        return None
    return None


def map_record_to_entity(
    raw: dict[str, Any],
    entity_spec: Any,
    *,
    context_tz: str | None = None,
    entity_name: str | None = None,  # noqa: ARG001
) -> dict[str, Any]:
    """Map one raw record to one canonical entity dict via field mappings.

    Each field: path extraction → transform pipeline → value.
    Missing path → field omitted (unless const provides default).
    Transform pipeline may raise TransformBlocked for unknown enum.
    """
    out: dict[str, Any] = {}

    # Normalize fields access
    fields: dict[str, Any] | None = None
    if isinstance(entity_spec, dict):
        fields = entity_spec.get("fields")
    elif hasattr(entity_spec, "fields"):
        fields = entity_spec.fields

    if not fields:
        return out

    for field_name, fm in fields.items():
        path: str | None = None
        paths: list[str] | None = None
        transform: list[Any] | None = None
        if isinstance(fm, dict):
            path = fm.get("path")
            paths = fm.get("paths")
            transform = fm.get("transform")
        elif hasattr(fm, "path"):
            path = getattr(fm, "path", None)
            paths = getattr(fm, "paths", None)
            transform = getattr(fm, "transform", None)
        else:
            continue

        if path is None and not paths:
            # No input path: only a const-led pipeline can produce a value
            # (site facts stated outright); anything else maps nothing.
            first_step = None
            if transform:
                first_step = transform[0]
                if isinstance(first_step, dict):
                    first_step = first_step.get("name")
                elif hasattr(first_step, "name"):
                    first_step = first_step.name
            if first_step != "const":
                continue
            raw_val = None
        # Extract raw value: one path → scalar; paths (spec v3) → the
        # list of per-path values fed into the pipeline (e.g. concat).
        # All paths missing counts as a missing value (field skipped,
        # unless a const/default step provides one — same as scalar).
        elif paths:
            extracted = [_jsonpath_extract_one(raw, p) for p in paths]
            raw_val = None if all(v is None for v in extracted) else extracted
        else:
            assert path is not None
            raw_val = _jsonpath_extract_one(raw, path)

        # Normalize transform steps to list[dict]
        steps: list[dict[str, Any]] = []
        if transform:
            # fm.transform is already list[TransformStep] after validation
            # Convert to plain dicts for registry
            if isinstance(transform, list):
                for s in transform:
                    if isinstance(s, dict):
                        steps.append(s)
                    elif hasattr(s, "model_dump"):
                        steps.append(s.model_dump())
                    elif hasattr(s, "name"):
                        d: dict[str, Any] = {"name": s.name, "args": getattr(s, "args", {})}
                        steps.append(d)
            elif isinstance(transform, dict):
                steps.append(transform)

        # Apply pipeline; None input still goes through pipeline to allow const/defaults
        # But if steps empty and raw_val is None, omit field
        if not steps and raw_val is None:
            # Keep null if original record had None explicitly? We check if path existed
            # jsonpath find returns None for missing; we skip
            continue

        try:
            # Normalize steps to have args dict
            norm_steps: list[dict[str, Any]] = []
            for st in steps:
                n = st.get("name")
                a = st.get("args", {})
                if not isinstance(a, dict):
                    a = {}
                norm_steps.append({"name": n, "args": a})
            mapped = apply_transform_pipeline(raw_val, norm_steps, context_tz=context_tz, field_name=field_name)
        except TransformBlocked:
            # Propagate with field context
            raise
        except Exception:
            # Other transform errors are treated as missing field, not Blocked
            # For money format errors etc., we propagate so caller can Degrade?
            # For now, re-raise but sync will treat as Degraded
            raise

        # mapped may be None → omit if still None and not required?
        # For canonical entities, we keep explicit None for nullable fields
        # but spec doesn't mark nullable; engine keeps None to allow validation to catch
        if mapped is None and raw_val is None:
            # Keep None to satisfy nullable like guest_name
            # Check if field is known nullable? We'll include None
            out[field_name] = None
        elif mapped is not None:
            out[field_name] = mapped
        else:
            # mapped None but raw was not None? e.g., transform returned None
            out[field_name] = None

        # Ensure money Decimal stays Decimal; later JSON serializer must handle
        # We keep Decimal objects for verifier comparison

    return out


def map_records_to_entities(
    raw_records: list[dict[str, Any]],
    entity_spec: Any,
    source_spec: Any | None = None,
    *,
    entity_name: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Map many raw records to canonical, separating Blocked.

    Returns (canonical_records, blocked_records) where blocked_records are those
    that raised TransformBlocked (unknown enum). The caller maps blocked → Blocked outcome.
    """
    canonical: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []

    # Resolve static timezone once if source_spec static; field kind per-record
    static_tz: str | None = None
    if source_spec is not None:
        tz_src = getattr(source_spec, "timezone_source", None) if not isinstance(source_spec, dict) else source_spec.get("timezone_source")
        if tz_src is not None:
            kind = getattr(tz_src, "kind", None) if not isinstance(tz_src, dict) else tz_src.get("kind")
            if kind == "static":
                static_tz = getattr(tz_src, "value", None) if not isinstance(tz_src, dict) else tz_src.get("value")

    for raw in raw_records:
        # For field-kind timezone, resolve per record
        ctx_tz = static_tz
        if source_spec is not None:
            # Check if field kind needs per-record resolution
            tz_src2 = getattr(source_spec, "timezone_source", None) if not isinstance(source_spec, dict) else source_spec.get("timezone_source")
            if tz_src2 is not None:
                kind2 = getattr(tz_src2, "kind", None) if not isinstance(tz_src2, dict) else tz_src2.get("kind")
                if kind2 == "field":
                    path2 = getattr(tz_src2, "path", None) if not isinstance(tz_src2, dict) else tz_src2.get("path")
                    if path2:
                        ctx_tz = _jsonpath_extract_one(raw, path2)

        try:
            mapped = map_record_to_entity(raw, entity_spec, context_tz=ctx_tz, entity_name=entity_name)
            canonical.append(mapped)
        except TransformBlocked as e:
            # Record this as blocked with metadata
            blocked.append({"raw": raw, "field": e.field, "value": e.value, "error": str(e)})
        except Exception as e:
            # Other transform errors → treat as Degraded, but include partial
            # For now, skip record and note as Degraded candidate
            blocked.append({"raw": raw, "error": str(e), "blocked": False})

    return canonical, blocked
