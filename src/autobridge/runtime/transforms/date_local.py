"""date_local — timestamp → property-local YYYY-MM-DD date (P1.W3.4).

Args:
  tz: IANA zone or "source" (uses spec timezone_source).
  format: optional output format (currently only YYYY-MM-DD, default).
"""

from __future__ import annotations

import datetime
from typing import Any
from zoneinfo import ZoneInfo

from autobridge.runtime.transforms.base import (
    _parse_iso_date_string,
    _parse_iso_datetime,
    _resolve_tz,
    apply_naive_tz,
    parse_wire_datetime,
)


def transform_date_local(
    value: Any,
    args: dict[str, Any],
    *,
    context_tz: str | None = None,
) -> Any:
    """Convert timestamp string to local business calendar date.

    - None input → None (nullable field).
    - Pure YYYY-MM-DD input → returned as-is (already local date).
    - ISO datetime with offset → convert to target tz and return date part.
    - Naive datetime (no tz) → per ``naive_as`` (default ``utc``: assume
      UTC; ``source``: property-local wall time), then convert to target tz.
    - Framed-protocol wire date/time (``YYMMDD[ HHMMSS]``, e.g. FIAS) →
      property-local wall time via ``naive_as: source``.
    - Returns YYYY-MM-DD string.
    - Timestamp crossing midnight due to tz offset lands on correct local date.
    """
    if value is None:
        return None
    if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, datetime.datetime):
        dt = apply_naive_tz(value, args, context_tz)
        tz_name = _resolve_tz(args.get("tz"), context_tz)
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("UTC")
        local = dt.astimezone(tz)
        return local.date().isoformat()

    s = str(value).strip()
    # Pure date
    pure = _parse_iso_date_string(s)
    if pure is not None:
        return pure.isoformat()

    # Framed-protocol wire date/time (e.g. FIAS YYMMDD[ HHMMSS]):
    # property-local wall time, interpreted in the source timezone.
    wire = parse_wire_datetime(s)
    if wire is not None:
        wire = apply_naive_tz(wire, args, context_tz)
        tz_name = _resolve_tz(args.get("tz"), context_tz)
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("UTC")
        return wire.astimezone(tz).date().isoformat()

    # Datetime string
    try:
        dt = _parse_iso_datetime(s)
    except Exception:
        # Fallback: try date parse again
        pure2 = _parse_iso_date_string(s)
        if pure2 is not None:
            return pure2.isoformat()
        return s

    dt = apply_naive_tz(dt, args, context_tz)
    tz_name = _resolve_tz(args.get("tz"), context_tz)
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    local = dt.astimezone(tz)
    return local.date().isoformat()
