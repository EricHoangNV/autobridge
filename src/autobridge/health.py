"""Health check helpers for all autobridge services."""

from __future__ import annotations

from typing import TypedDict

from fastapi.responses import JSONResponse


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


def readyz_response(checks: dict[str, bool] | None = None) -> JSONResponse:
    """Return the ``/readyz`` body with an honest HTTP status (AR-2-6).

    ``200`` when every check passes, ``503`` when any check fails, so an
    operator watching only status codes is not misled into thinking a
    degraded service (e.g. control with an unreachable verifier) is ready.
    Liveness (``/healthz``) stays ``200``; orchestration healthchecks must
    keep probing ``/healthz``, readiness gates poll ``/readyz``.
    """
    body = readyz(checks)
    return JSONResponse(
        status_code=200 if body["status"] == "ok" else 503,
        content={"status": body["status"], "checks": body["checks"]},
    )
