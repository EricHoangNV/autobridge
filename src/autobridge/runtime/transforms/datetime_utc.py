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
    is_allowed_date_input_format,
    parse_wire_datetime,
    parse_with_input_format,
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
    # REQ-081 item 4: declarative input format (allow-listed only).
    _input_fmt = args.get("input_format") if isinstance(args, dict) else None
    if isinstance(_input_fmt, str) and _input_fmt and is_allowed_date_input_format(_input_fmt):
        _parsed_fmt = parse_with_input_format(s, _input_fmt)
        if _parsed_fmt is not None:
            return (
                apply_naive_tz(_parsed_fmt, args, context_tz, default="source")
                .astimezone(datetime.UTC)
                .isoformat()
            )
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
