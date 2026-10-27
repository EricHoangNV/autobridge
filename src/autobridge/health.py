"""Health check helpers for all autobridge services."""

from __future__ import annotations

from typing import TypedDict


class HealthStatus(TypedDict):
    status: str


class ReadyStatus(TypedDict):
    status: str
    checks: dict[str, str]


def healthz() -> HealthStatus:
    """Return liveness status."""
    return {"status": "ok"}


def readyz(checks: dict[str, bool] | None = None) -> ReadyStatus:
    """Return readiness status.

    If *checks* is None, all checks are considered passing.
    """
    raw = checks if checks is not None else {}
    mapped: dict[str, str] = {}
    for name, ok in raw.items():
        mapped[name] = "ok" if ok else "fail"
    if not mapped:
        mapped["default"] = "ok"
    all_ok = all(v == "ok" for v in mapped.values())
    return {"status": "ok" if all_ok else "degraded", "checks": mapped}
