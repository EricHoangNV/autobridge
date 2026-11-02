"""date_local — timestamp → property-local YYYY-MM-DD date (P1.W3.4).

Args:
  tz: IANA zone or "source" (uses spec timezone_source).
  format: optional output format (currently only YYYY-MM-DD, default).
  input_format: optional declarative input format (REQ-081 item 4), e.g.
    "%m-%d-%Y". Fixed allow-list only (no free code); see
    base.ALLOWED_DATE_INPUT_DIRECTIVES.
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
    format_has_time_component,
    is_allowed_date_input_format,
    parse_wire_date_only,
    parse_wire_datetime,
    parse_with_input_format,
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
    - REQ-083 C13: a date-only input — from an ``input_format`` without
      ``%H``/``%M``/``%S``, or a date-only framed-protocol wire value
      (``YYMMDD``) — is a calendar date: returned unchanged, never
      timezone-converted.
    - Time-bearing ``input_format`` without an offset is vendor wall
      time (``naive_as`` defaults to ``source`` for that path).
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
    # REQ-081 item 4: declarative input format (allow-listed only), e.g.
    # "%m-%d-%Y" for the harbor dates/format fault.
    _input_fmt = args.get("input_format") if isinstance(args, dict) else None
    if isinstance(_input_fmt, str) and _input_fmt and is_allowed_date_input_format(_input_fmt):
        _parsed_fmt = parse_with_input_format(s, _input_fmt)
        if _parsed_fmt is not None:
            if not format_has_time_component(_input_fmt):
                # REQ-083 C13: a date-only value is a calendar date —
                # returned unchanged, never timezone-converted.
                return _parsed_fmt.date().isoformat()
            # Time-bearing without an offset is vendor wall time.
            _parsed_fmt = apply_naive_tz(_parsed_fmt, args, context_tz, default="source")
            tz_name = _resolve_tz(args.get("tz"), context_tz)
            try:
                tz = ZoneInfo(tz_name)
            except Exception:
                tz = ZoneInfo("UTC")
            return _parsed_fmt.astimezone(tz).date().isoformat()
    # Pure date
    pure = _parse_iso_date_string(s)
    if pure is not None:
        return pure.isoformat()

    # Framed-protocol wire date (YYMMDD, property-local calendar date):
    # returned unchanged (REQ-083 C13).
    wire_date = parse_wire_date_only(s)
    if wire_date is not None:
        return wire_date.isoformat()
    # Framed-protocol wire datetime (with a time part): property-local
    # wall time, interpreted in the source timezone.
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
