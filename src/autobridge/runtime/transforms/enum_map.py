"""enum_map — closed vocab mapping, unknown → Blocked (P1.W3.4)."""

from __future__ import annotations

from typing import Any

from autobridge.runtime.transforms.base import TransformBlocked


def transform_enum_map(
    value: Any,
    args: dict[str, Any],
    *,
    context_tz: str | None = None,  # noqa: ARG001
) -> Any:
    """Map vendor value via map dict; unknown → Blocked.

    Args:
      map: dict vendor_val → canonical
      on_unknown: must be "block" per spec validation
      field: optional field name for error reporting

    Returns mapped canonical value.
    Raises TransformBlocked when value not in map.
    """
    if value is None:
        return None

    mmap = args.get("map")
    if not isinstance(mmap, dict) or not mmap:
        raise ValueError("enum_map requires non-empty map")
    on_unknown = args.get("on_unknown", "block")
    field = args.get("field")

    # Direct lookup (stringified keys match)
    # Map keys may be strings, values canonical strings
    # Use exact match, fallback to str(value) match
    if value in mmap:
        return mmap[value]
    str_val = str(value)
    # Also check stringified map keys
    for k, v in mmap.items():
        if str(k) == str_val:
            return v

    if on_unknown == "block":
        raise TransformBlocked(
            f"unknown enum value {value!r} for field {field or 'unknown'}",
            field=field,
            value=value,
        )
    # Fallback never used per spec, but handle
    return value
