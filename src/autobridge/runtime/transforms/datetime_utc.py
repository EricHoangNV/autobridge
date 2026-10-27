"""datetime_utc — local datetime → UTC ISO (P1.W3.4)."""

from __future__ import annotations

import datetime
from typing import Any
from zoneinfo import ZoneInfo

from autobridge.runtime.transforms.base import (
    _parse_iso_date_string,
    _parse_iso_datetime,
    _resolve_tz,
)


def transform_datetime_utc(
    value: Any,
    args: dict[str, Any],
    *,
    context_tz: str | None = None,
) -> Any:
    """Convert source-local datetime string to UTC aware ISO.

    - None → None
    - Already aware datetime → convert to UTC
    - Naive datetime string → assume source tz then to UTC
    - Pure date string → midnight in source tz → UTC
    - Returns ISO string with +00:00
    """
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        dt = value
        if dt.tzinfo is None:
            tz_name = _resolve_tz(args.get("tz"), context_tz)
            try:
                tz = ZoneInfo(tz_name)
            except Exception:
                tz = ZoneInfo("UTC")
            dt = dt.replace(tzinfo=tz)
        return dt.astimezone(datetime.UTC).isoformat()

    s = str(value).strip()
    pure = _parse_iso_date_string(s)
    if pure is not None:
        tz_name = _resolve_tz(args.get("tz"), context_tz)
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("UTC")
        dt = datetime.datetime(pure.year, pure.month, pure.day, tzinfo=tz)
        return dt.astimezone(datetime.UTC).isoformat()

    try:
        dt = _parse_iso_datetime(s)
    except Exception:
        return s

    if dt.tzinfo is None:
        tz_name = _resolve_tz(args.get("tz"), context_tz)
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("UTC")
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(datetime.UTC).isoformat()
