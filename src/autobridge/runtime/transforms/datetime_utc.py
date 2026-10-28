"""datetime_utc — local datetime → UTC ISO (P1.W3.4)."""

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


def transform_datetime_utc(
    value: Any,
    args: dict[str, Any],
    *,
    context_tz: str | None = None,
) -> Any:
    """Convert source-local datetime string to UTC aware ISO.

    - None → None
    - Already aware datetime → convert to UTC
    - Naive datetime → per ``naive_as`` (default ``source``: property-local
      wall time in the source timezone — the legacy assumption; ``utc``:
      assume UTC)
    - Pure date string → midnight in source tz → UTC
    - Framed-protocol wire date/time (``YYMMDD[ HHMMSS]``) → naive
      property-local wall time, then per ``naive_as``
    - Returns ISO string with +00:00
    """
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        dt = apply_naive_tz(value, args, context_tz, default="source")
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

    wire = parse_wire_datetime(s)
    if wire is not None:
        return (
            apply_naive_tz(wire, args, context_tz, default="source")
            .astimezone(datetime.UTC)
            .isoformat()
        )

    try:
        dt = _parse_iso_datetime(s)
    except Exception:
        return s

    return (
        apply_naive_tz(dt, args, context_tz, default="source")
        .astimezone(datetime.UTC)
        .isoformat()
    )
