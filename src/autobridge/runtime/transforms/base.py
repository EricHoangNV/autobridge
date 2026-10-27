"""Shared helpers for the closed transform set (P1.W3.4, plan §2.4)."""

from __future__ import annotations

import datetime
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
