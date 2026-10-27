"""EdgePOS toy point-of-sale simulator (P2.S2, plan §5 S2).

A minimal vendor reachable ONLY on the isolated ``edge-net`` Docker network:
the ``edge-worker`` service can reach it, but the control plane, the cloud
``runtime-sandbox``, the UI and the worker have no route to it (negative
test in ``scripts/smoke.sh`` + ``tests/e2e/test_s2_edge_target.py``).

Shape: clean REST surface (offset pagination, plain-English statuses) with a
tiny static dataset.  Stateless by design — no admin API, no fault
switchboard — so the e2e ``reset_simulator_state`` fixture has nothing to do.

This module must never import autobridge or verifier (trust boundary).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Query

app = FastAPI(title="EdgePOS Simulator", openapi_url=None, docs_url=None, redoc_url=None)

RESERVATIONS: list[dict[str, Any]] = [
    {
        "reservation_id": "EDGE-1001",
        "property_id": "edge-001",
        "guest_name": "Grace Hopper",
        "arrival_date": "2026-10-27",
        "departure_date": "2026-10-29",
        "status": "checked_in",
        "total_amount": "199.99",
        "currency": "USD",
        "updated_at": "2026-10-27T14:05:00+00:00",
    },
    {
        "reservation_id": "EDGE-1002",
        "property_id": "edge-001",
        "guest_name": "Alan Turing",
        "arrival_date": "2026-10-28",
        "departure_date": "2026-10-30",
        "status": "booked",
        "total_amount": "349.50",
        "currency": "USD",
        "updated_at": "2026-10-27T15:05:00+00:00",
    },
]

FOLIO_CHARGES: list[dict[str, Any]] = [
    {
        "charge_id": "EDGE-CHG-9001",
        "reservation_id": "EDGE-1001",
        "property_id": "edge-001",
        "business_date": "2026-10-27",
        "amount": "24.50",
        "currency": "USD",
        "description": "Edge kiosk coffee",
        "posted_at": "2026-10-27T10:12:00+00:00",
    },
]


@app.get("/healthz")
def get_healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def get_readyz() -> dict[str, object]:
    return {"status": "ok", "checks": {"dataset": "ok"}}


def _paginate(data: list[dict[str, Any]], limit: int, offset: int) -> dict[str, Any]:
    sliced = data[offset : offset + limit]
    return {
        "data": sliced,
        "limit": limit,
        "offset": offset,
        "total": len(data),
    }


@app.get("/reservations")
def list_reservations(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return _paginate(RESERVATIONS, limit, offset)


@app.get("/folios")
def list_folios(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return _paginate(FOLIO_CHARGES, limit, offset)


@app.get("/reservations/{reservation_id}")
def get_reservation(reservation_id: str) -> dict[str, Any]:
    for rec in RESERVATIONS:
        if rec["reservation_id"] == reservation_id:
            return rec
    from fastapi import HTTPException

    raise HTTPException(status_code=404, detail={"code": "not_found", "message": reservation_id})
