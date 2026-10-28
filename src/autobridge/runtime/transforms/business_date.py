"""business_date — posted_at UTC → property-local business date (P1.W3.4).

Maps a UTC instant to the property-local business date using the
cutoff hour (default 4). Late postings (00:00-03:59 local) count to prior
business date.
"""

from __future__ import annotations

import datetime
from typing import Any
from zoneinfo import ZoneInfo

from autobridge.runtime.transforms.base import (
    _parse_iso_datetime,
    _resolve_tz,
    apply_naive_tz,
    parse_wire_datetime,
)


def transform_business_date(
    value: Any,
    args: dict[str, Any],
    *,
    context_tz: str | None = None,
) -> Any:
    """Convert posted_at UTC instant to business_date YYYY-MM-DD.

    Args:
      tz: IANA zone or "source" (uses spec timezone_source)
      cutoff: hour int (0-23) default 4
      naive_as: 'utc' (default, legacy) or 'source' for naive inputs

    - None → None
    - date string YYYY-MM-DD → returned as-is (already business date)
    - datetime string → parse, convert to tz, apply cutoff
    - framed-protocol wire date/time (``YYMMDD[ HHMMSS]``) → property-local
      wall time (requires ``naive_as: source``), then cutoff
    """
    if value is None:
        return None
    if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, datetime.datetime):
        dt = apply_naive_tz(value, args, context_tz)
        tz_name = _resolve_tz(args.get("tz"), context_tz)
        cutoff = args.get("cutoff", args.get("cutoff_hour", 4))
        try:
            cutoff_hour = int(cutoff)
        except Exception:
            cutoff_hour = 4
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("UTC")
        local = dt.astimezone(tz)
        business = local.date()
        if local.hour < cutoff_hour:
            business = business - datetime.timedelta(days=1)
        return business.isoformat()

    s = str(value).strip()
    # Pure date already
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        try:
            datetime.date.fromisoformat(s)
            return s
        except ValueError:
            pass

    wire = parse_wire_datetime(s)
    if wire is not None:
        dt2 = apply_naive_tz(wire, args, context_tz)
    else:
        try:
            dt2 = _parse_iso_datetime(s)
        except Exception:
            return s
        dt2 = apply_naive_tz(dt2, args, context_tz)

    tz_name2 = _resolve_tz(args.get("tz"), context_tz)
    cutoff2 = args.get("cutoff", args.get("cutoff_hour", 4))
    try:
        cutoff_hour2 = int(cutoff2)
    except Exception:
        cutoff_hour2 = 4
    try:
        tz2 = ZoneInfo(tz_name2)
    except Exception:
        tz2 = ZoneInfo("UTC")
    local2 = dt2.astimezone(tz2)
    business2 = local2.date()
    if local2.hour < cutoff_hour2:
        business2 = business2 - datetime.timedelta(days=1)
    return business2.isoformat()
