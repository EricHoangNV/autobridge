"""Shared helpers for simulator public APIs (W2.2).

Provides dataset loading, pagination, auth checks, and fault-aware
response building. Used by both AtlasPMS and HarborPMS.
"""

from __future__ import annotations

import base64
from typing import Any

from fastapi import HTTPException, Request

from simulators.common.faults import (
    is_auth_expired,
    is_auth_revoked,
    is_endpoint_removed,
    is_rate_limited,
    is_transient_error,
    should_repeat_cursor,
)
from simulators.common.generator import generate_dataset
from simulators.common.state import get_state


def get_dataset_for_current_state() -> dict[str, list[dict[str, Any]]]:
    """Generate dataset for current state's seed, with fault mutations applied."""
    from simulators.common.faults import apply_dataset_mutations

    state = get_state()
    seed = int(state["seed"])
    scenario_id = str(state["scenario_id"])
    # Generate baseline dataset
    dataset = generate_dataset(seed=seed)
    # REQ-025 beacon: an active beacon instance applies its record-shape
    # mutation on top of the scenario mutations (beacon and scenario modes
    # are exclusive in state, so at most one applies in practice).
    if state.get("beacon") is not None:
        from simulators.common import beacon_serve as _beacon

        dataset = _beacon.apply_active_to_dataset(dataset)
    # Apply mutations
    return apply_dataset_mutations(dataset, scenario_id)


def check_auth(
    scenario_id: str,
    authorization: str | None = None,
    require_bearer: bool = False,
) -> None:
    """Validate Authorization header, respecting fault-injected auth failures.

    Raises HTTPException 401/403 when auth fails. For test simplicity,
    any non-empty Bearer token is accepted unless a fault forces failure.
    When ``require_bearer`` is True (AtlasPMS clean spec), missing or
    malformed Authorization raises 401 unauthorized.
    """
    # Fault-injected auth failures always win
    if is_auth_expired(scenario_id):
        raise HTTPException(status_code=401, detail={"code": "token_expired", "message": "Token has expired"})
    if is_auth_revoked(scenario_id):
        raise HTTPException(status_code=403, detail={"code": "token_revoked", "message": "Token has been revoked"})

    if require_bearer:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=401, detail={"code": "unauthorized", "message": "Missing or invalid Authorization header"}
            )
        token = authorization.removeprefix("Bearer ").strip()
        if not token:
            raise HTTPException(
                status_code=401, detail={"code": "unauthorized", "message": "Missing or invalid Authorization header"}
            )
        return
    _ = authorization  # permissive for Harbor and other vendors


def check_rate_limit_and_transient(scenario_id: str, request_count: int) -> None:
    """Raise 429 or 503 if fault dictates, based on request_count."""
    if is_rate_limited(scenario_id, request_count):
        raise HTTPException(
            status_code=429,
            detail={"code": "rate_limit", "message": "Too many requests"},
            headers={"Retry-After": "1"},
        )
    if is_transient_error(scenario_id, request_count):
        raise HTTPException(
            status_code=503,
            detail={"code": "transient_error", "message": "Service temporarily unavailable"},
        )


def check_endpoint_removed(scenario_id: str, endpoint: str) -> None:
    if is_endpoint_removed(scenario_id) and endpoint in ("reservations", "folios"):
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": f"Endpoint {endpoint} not found"})


# ---------------------------------------------------------------------------
# Pagination helpers
# ---------------------------------------------------------------------------


def paginate_offset(
    data: list[dict[str, Any]],
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Offset pagination: slice data[ offset : offset+limit ]."""
    total = len(data)
    sliced = data[offset : offset + limit]
    # next_offset is None when no more data
    next_offset = offset + limit if offset + limit < total else None
    meta = {"total": total, "limit": limit, "offset": offset, "next_offset": next_offset}
    return sliced, meta


def encode_cursor(idx: int) -> str:
    return base64.urlsafe_b64encode(str(idx).encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    # Pad base64
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        return int(base64.urlsafe_b64decode(padded).decode())
    except Exception:
        return 0


def paginate_cursor(
    data: list[dict[str, Any]],
    cursor: str | None,
    limit: int,
    scenario_id: str,
    request_count: int = 0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Cursor pagination with optional repeated-cursor bug."""
    offset = decode_cursor(cursor)
    total = len(data)
    sliced = data[offset : offset + limit]
    next_idx = offset + limit
    if next_idx >= total:
        next_cursor = None
    else:
        # Bug: repeat previous cursor instead of advancing (every 2nd page)
        if should_repeat_cursor(scenario_id) and request_count > 0 and request_count % 2 == 0:
            # Return previous cursor (or current) to simulate bug
            next_cursor = cursor if cursor else encode_cursor(offset)
        else:
            next_cursor = encode_cursor(next_idx)
    meta = {"total": total, "limit": limit, "cursor": cursor, "next_cursor": next_cursor}
    return sliced, meta


def get_baseline_gross_sums() -> dict[str, str]:
    """Return baseline gross sums for current state's seed (for W6.2 money invariant).

    Computes sum of total_amount (reservations) and amount (folios) from baseline
    dataset (seed, no mutations). Used as independent reference for gross_to_net
    detection: list's actual sum (mutated net) will differ from this gross sum.
    """
    from decimal import Decimal

    state = get_state()
    seed = int(state["seed"])
    # Generate baseline (no mutations)
    baseline = generate_dataset(seed=seed)
    res_sum = sum(Decimal(str(r.get("total_amount", "0"))) for r in baseline.get("reservations", []))
    fol_sum = sum(Decimal(str(f.get("amount", "0"))) for f in baseline.get("folios", []))
    return {"reservations_gross_sum": format(res_sum, ".2f"), "folios_gross_sum": format(fol_sum, ".2f")}


def get_pagination_params(
    request: Request,
    default_limit: int = 10,
) -> tuple[int, int, str | None]:
    """Extract pagination params from query string, with defaults.

    REQ-025 beacon: when a pagination contract is active, only the
    contract's parameter names are read (baseline names fall back to
    defaults, so an unfixed client replays the default first page).
    Returns the canonical ``(limit, offset, cursor)`` triple the apps
    already consume.
    """
    from simulators.common import beacon_serve as _beacon

    qp = request.query_params
    contract = _beacon.pagination_contract()
    if contract is not None:
        strategy = str(contract.get("strategy", ""))
        if strategy == "offset":
            limit = _parse_int(qp.get(str(contract.get("limit_param", ""))), default_limit, 1, 100)
            offset = _parse_int(qp.get(str(contract.get("offset_param", ""))), 0, 0, 10**9)
            return limit, offset, None
        if strategy == "page":
            page = _parse_int(qp.get(str(contract.get("page_param", ""))), 1, 1, 10**9)
            per_page = _parse_int(qp.get(str(contract.get("per_page_param", ""))), default_limit, 1, 100)
            return per_page, (page - 1) * per_page, None
        if strategy in ("cursor", "body_cursor"):
            limit = _parse_int(qp.get(str(contract.get("limit_param", "limit"))), default_limit, 1, 100)
            cursor = qp.get(str(contract.get("cursor_param", "cursor")))
            return limit, 0, cursor if isinstance(cursor, str) else None
    limit_str = qp.get("limit")
    offset_str = qp.get("offset")
    cursor = qp.get("cursor")
    try:
        limit = int(limit_str) if limit_str is not None else default_limit
    except ValueError:
        limit = default_limit
    limit = max(1, min(limit, 100))
    try:
        offset = int(offset_str) if offset_str is not None else 0
    except ValueError:
        offset = 0
    offset = max(0, offset)
    return limit, offset, cursor


def _parse_int(raw: Any, default: int, minimum: int, maximum: int) -> int:
    """Clamp an int-ish query value (helper for the non-beacon path)."""
    try:
        value = int(raw) if raw is not None else default
    except (ValueError, TypeError):
        return default
    if isinstance(value, bool):
        return default
    return max(minimum, min(maximum, value))
