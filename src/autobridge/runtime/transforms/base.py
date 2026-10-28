"""Shared helpers for the closed transform set (P1.W3.4, plan §2.4)."""

from __future__ import annotations

import datetime
import re
from decimal import Decimal
from typing import Any


class TransformError(Exception):
    """Base for transform failures; subclasses distinguish Blocked vs Degraded."""

    pass


class TransformBlocked(TransformError):
    """Unknown enum value or other blocking condition (→ SyncRun Blocked)."""

    def __init__(self, message: str, field: str | None = None, value: Any | None = None) -> None:
        super().__init__(message)
        self.field = field
        self.value = value


def _parse_iso_datetime(value: str) -> datetime.datetime:
    """Parse ISO datetime, handling Z → +00:00."""
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    # datetime.fromisoformat handles offset variants
    return datetime.datetime.fromisoformat(s)


def _parse_iso_date_string(value: str) -> datetime.date | None:
    """Return date if value is YYYY-MM-DD, else None."""
    s = str(value).strip()
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        try:
            return datetime.date.fromisoformat(s)
        except ValueError:
            return None
    return None


#: FIAS-style wire date/time shapes (framed hotel protocols): a six-digit
#: property-local ``YYMMDD`` date, optionally followed by a six-digit
#: ``HHMMSS`` time (space-separated or run together). Years are 2000+YY.
_WIRE_DATE_RE = re.compile(r"^(\d{2})(\d{2})(\d{2})$")
_WIRE_DATETIME_RE = re.compile(r"^(\d{2})(\d{2})(\d{2})[ ]?(\d{2})(\d{2})(\d{2})$")


def parse_wire_datetime(value: str) -> datetime.datetime | None:
    """Parse a framed-protocol wire date/time, or None when not wire-shaped.

    Accepts ``YYMMDD`` (midnight) and ``YYMMDD HHMMSS`` / ``YYMMDDHHMMSS``
    (12 digits). Returns a *naive* datetime — callers attach the source
    timezone via :func:`apply_naive_tz` (wire timestamps are property-local,
    never UTC). Anything not matching the digit shapes returns None so
    callers fall through to their existing parsing; a shape match with an
    impossible calendar value (month 13) also returns None and the value
    keeps the caller's legacy lenient behavior.
    """
    s = str(value).strip()
    m = _WIRE_DATETIME_RE.match(s.replace("  ", " "))
    if m:
        yy, mo, dd, hh, mi, ss = (int(g) for g in m.groups())
        try:
            return datetime.datetime(2000 + yy, mo, dd, hh, mi, ss)
        except ValueError:
            return None
    m = _WIRE_DATE_RE.match(s)
    if m:
        yy, mo, dd = (int(g) for g in m.groups())
        try:
            return datetime.datetime(2000 + yy, mo, dd)
        except ValueError:
            return None
    return None


def apply_naive_tz(
    dt: datetime.datetime,
    args: dict[str, Any],
    context_tz: str | None,
    *,
    default: str = "utc",
) -> datetime.datetime:
    """Attach a timezone to a naive datetime per the ``naive_as`` arg.

    - ``naive_as: utc``: naive timestamps are UTC instants.
    - ``naive_as: source``: naive timestamps are property-local wall time in
      the spec's timezone (framed-protocol wire timestamps) and are
      interpreted there before any UTC conversion.

    ``default`` is the legacy assumption when ``naive_as`` is unset
    (``date_local``/``business_date`` assumed UTC; ``datetime_utc`` assumed
    the source timezone) — callers preserve their own legacy default.

    Aware datetimes pass through. Unknown ``naive_as`` raises
    ``TransformError`` (fail closed, never guessed).
    """
    if dt.tzinfo is not None:
        return dt
    naive_as = args.get("naive_as", default)
    if naive_as not in ("utc", "source"):
        raise TransformError(f"naive_as must be 'utc' or 'source', got {naive_as!r}")
    tz_name = "UTC" if naive_as == "utc" else _resolve_tz(args.get("tz"), context_tz)
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(tz_name)
    except Exception:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("UTC")
    return dt.replace(tzinfo=tz)


def _resolve_tz(tz_arg: str | None, context_tz: str | None) -> str:
    """Resolve tz argument: 'source' means context_tz, else explicit."""
    if tz_arg is None:
        return context_tz or "UTC"
    if tz_arg == "source":
        return context_tz or "UTC"
    return tz_arg


def _ensure_decimal(value: Any) -> Decimal:
    """Coerce value to Decimal without float precision loss."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return Decimal(value)
    # For float, use str() to avoid binary repr, but caller should avoid float
    return Decimal(str(value))
