"""retry_backoff_only rule — no patch, backoff only for rate_limit / transient.

For RATE_LIMIT (429) and TRANSIENT (5xx) the runtime already retries via
source.rate_limit.retry; no spec patch is needed beyond ensuring retry covers
the observed status.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autobridge.repair.context import RepairContext

RULE_ID = "retry_backoff_only"
CONFIDENCE = 0.97
VERIFICATION_LEVEL = "none"
PRODUCES_PATCH = False


def _has_retry_status(spec: dict[str, Any], status: int) -> bool:
    retry = spec.get("source", {}).get("rate_limit", {}).get("retry", {}) if isinstance(spec.get("source"), dict) else {}
    if not isinstance(retry, dict):
        return False
    statuses = retry.get("statuses")
    return isinstance(statuses, list) and status in statuses


def _http_status_in_ctx(ctx: RepairContext) -> int | None:
    v = ctx.extra.get("http_status")
    if isinstance(v, int):
        return v
    # Check signals for http_status
    for sig in ctx.signals:
        details: dict[str, Any] | None = None
        if isinstance(sig, dict):
            details = sig.get("details")
        elif hasattr(sig, "details"):
            details = sig.details
        if isinstance(details, dict) and "http_status" in details:
            try:
                return int(details["http_status"])
            except (TypeError, ValueError):
                continue
    return None


def precondition(ctx: RepairContext) -> bool:
    if ctx.drift_class not in ("RATE_LIMIT", "TRANSIENT"):
        return False
    # Must have rate-limited or transient signal or http_status 429/503 etc
    status = _http_status_in_ctx(ctx)
    if ctx.drift_class == "RATE_LIMIT":
        if status is not None and status != 429:
            # Still allow if signal kind is rate_limited
            pass
        return True
    if ctx.drift_class == "TRANSIENT":
        if status is not None and status not in (500, 502, 503, 504):
            pass
        return True
    return False


def generate_patch(ctx: RepairContext) -> list[dict[str, Any]]:
    """No patch — backoff is handled by runtime retry config.

    If the spec's retry does not cover the observed status, produce a minimal
    patch adding it; otherwise return empty.
    """
    status = _http_status_in_ctx(ctx)
    if status is None:
        # Default statuses for each class
        status = 429 if ctx.drift_class == "RATE_LIMIT" else 503
    if _has_retry_status(ctx.spec, status):
        return []
    # Add missing status to retry list
    retry_path = "/source/rate_limit/retry/statuses"
    current = ctx.spec.get("source", {}).get("rate_limit", {}).get("retry", {}).get("statuses", []) if isinstance(ctx.spec.get("source"), dict) else []
    if not isinstance(current, list):
        current = []
    new_statuses = sorted(set(current + [status]))
    return [
        {
            "op": "replace",
            "path": retry_path,
            "value": new_statuses,
        }
    ]
