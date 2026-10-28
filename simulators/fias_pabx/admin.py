"""FIAS link admin routes: link state and fault hooks (REQ-022 A).

Mounted on the shared simulator admin API (port 8001, admin-net) via the
``ADMIN_EXTRA_ROUTER`` hook in ``simulators/run.py``. Same authorization
as every admin write (``X-Admin-Network`` + ``SIM_ADMIN_TOKEN`` —
``simulators/common/admin.py``); the edge-net host side can never reach
these knobs.

Endpoints:

* ``GET /admin/fias/state`` — link snapshot (rooms, un-PA'd postings,
  current ``PS`` layout, counters). No secrets, no key material.
* ``POST /admin/fias/faults`` — set fault hooks (layout change, zeros
  resync, ACK-timeout silence, drop, DB-swap on reconnect, bursts).
* ``POST /admin/fias/reset`` — baseline faults, fresh P# counter and
  reseeded rooms (the generic ``/admin/reset`` only clears the shared
  scenario slot, which the FIAS engine does not consume in step A).

This module must never import autobridge or verifier (trust boundary).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from simulators.common.admin import _require_admin_network

if TYPE_CHECKING:
    from .pabx import PabxLink

router = APIRouter()

_link: PabxLink | None = None


def set_link(link: PabxLink | None) -> None:
    """Register the running link engine (called by the app lifespan)."""
    global _link
    _link = link


def get_link() -> PabxLink:
    link = get_link_or_none()
    if link is None:  # pragma: no cover - lifespan always sets it in service
        raise HTTPException(status_code=503, detail="FIAS link engine is not running")
    return link


def get_link_or_none() -> PabxLink | None:
    """Return the running link engine, or None when dial-out is disabled."""
    return _link


class FaultsRequest(BaseModel):
    ps_layout: str | None = None
    send_zeros_then_drop: bool | None = None
    drop_link: bool | None = None
    silent_types: list[str] | None = None
    resync_on_reconnect: bool | None = None
    burst_postings: int | None = None


@router.get("/admin/fias/state")
def get_fias_state(request: Request) -> dict[str, Any]:
    _require_admin_network(request)
    return get_link().snapshot()  # type: ignore[no-any-return]


@router.post("/admin/fias/faults")
def post_fias_faults(body: FaultsRequest, request: Request) -> dict[str, Any]:
    _require_admin_network(request)
    link = get_link()
    if body.ps_layout is not None and body.ps_layout not in ("baseline", "layout_v2"):
        raise HTTPException(status_code=400, detail="ps_layout must be baseline|layout_v2")
    if body.silent_types is not None:
        for rtype in body.silent_types:
            if len(rtype) != 2:
                raise HTTPException(status_code=400, detail=f"bad record type {rtype!r}")
    if body.burst_postings is not None and not 0 <= body.burst_postings <= 200:
        raise HTTPException(status_code=400, detail="burst_postings must be 0..200")
    with link.lock:
        if body.ps_layout is not None:
            link.faults.ps_layout = body.ps_layout
        if body.send_zeros_then_drop is not None:
            link.faults.send_zeros_then_drop = body.send_zeros_then_drop
        if body.drop_link is not None:
            link.faults.drop_link = body.drop_link
        if body.silent_types is not None:
            link.faults.silent_types = tuple(body.silent_types)
        if body.resync_on_reconnect is not None:
            link.faults.resync_on_reconnect = body.resync_on_reconnect
        if body.burst_postings is not None:
            link.faults.burst_postings = body.burst_postings
    return get_link().snapshot()  # type: ignore[no-any-return]


@router.post("/admin/fias/reset")
def post_fias_reset(request: Request) -> dict[str, Any]:
    _require_admin_network(request)
    link = get_link()
    link.reset_faults()
    with link.lock:
        link.faults.drop_link = True  # fresh handshake picks up baseline
    return get_link().snapshot()  # type: ignore[no-any-return]
