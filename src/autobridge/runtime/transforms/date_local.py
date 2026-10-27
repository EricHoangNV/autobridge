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
    - Naive datetime (no tz) → assume UTC then convert to target tz.
    - Returns YYYY-MM-DD string.
    - Timestamp crossing midnight due to tz offset lands on correct local date.
    """
    if value is None:
        return None
    if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, datetime.datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.UTC)
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

    # Datetime string
    try:
        dt = _parse_iso_datetime(s)
    except Exception:
        # Fallback: try date parse again
        pure2 = _parse_iso_date_string(s)
        if pure2 is not None:
            return pure2.isoformat()
        return s

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.UTC)
    tz_name = _resolve_tz(args.get("tz"), context_tz)
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    local = dt.astimezone(tz)
    return local.date().isoformat()
