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
  resync, ACK-timeout silence, drop, DB-swap on reconnect, bursts,
  replays, oversize, flood).
* ``POST /admin/fias/reset`` — baseline faults, fresh P# counter and
  reseeded rooms (the generic ``/admin/reset`` only clears the shared
  scenario slot, which the FIAS engine does not consume in step A).
* ``POST /admin/scenario`` — named-scenario injection shadowing the
  shared slot route on this sim only (REQ-022 D): sets the shared slot
  and arms the scenario's link faults, so ``/demo/inject-fault`` labels
  FIAS faults the same way as HTTP ones.

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
    replay_postings: int | None = None
    send_oversize: bool | None = None
    flood_records: int | None = None


#: Named-scenario → link-fault arming (REQ-022 D). ``POST /demo/inject-fault``
#: targets this sim with a scenario id; this table is what the id *means* on
#: the wire. Scenarios with no PABX-side fault (agent killed by the test,
#: cloud slowed by the test seam, expired commands) map to ``{}``: the
#: shared slot records the label, the link is untouched.
SCENARIO_LINK_FAULTS: dict[str, dict[str, Any]] = {
    "fias_record_layout_change": {"ps_layout": "layout_v3", "drop_link": True},
    # REQ-067 item 8: the link drop alone leaves nothing new to forward
    # (stay postings were planned once and already PA'd), so deck 7 shows
    # a frozen count. Five burst postings queue at the reconnect with
    # fresh P#s: postings visibly resume, and lost/duplicated stay 0.
    "fias_link_resync": {"send_zeros_then_drop": True, "burst_postings": 5},
    "fias_ack_timeout": {"silent_types": ("WR",)},
    "agent_restart_inflight": {},
    "cloud_slow": {},
    "fias_duplicate_records": {"replay_postings": 3},
    "fias_oversize_frame": {"send_oversize": True},
    "fias_record_flood": {"flood_records": 150},
    "session_lost_with_command": {"drop_link": True},
    "stale_command": {},
}


@router.get("/admin/fias/state")
def get_fias_state(request: Request) -> dict[str, Any]:
    _require_admin_network(request)
    return get_link().snapshot()  # type: ignore[no-any-return]


@router.post("/admin/fias/faults")
def post_fias_faults(body: FaultsRequest, request: Request) -> dict[str, Any]:
    _require_admin_network(request)
    link = get_link()
    if body.ps_layout is not None and body.ps_layout not in ("baseline", "layout_v2", "layout_v3"):
        raise HTTPException(status_code=400, detail="ps_layout must be baseline|layout_v2|layout_v3")
    if body.silent_types is not None:
        for rtype in body.silent_types:
            if len(rtype) != 2:
                raise HTTPException(status_code=400, detail=f"bad record type {rtype!r}")
    if body.burst_postings is not None and not 0 <= body.burst_postings <= 200:
        raise HTTPException(status_code=400, detail="burst_postings must be 0..200")
    if body.replay_postings is not None and not 0 <= body.replay_postings <= 50:
        raise HTTPException(status_code=400, detail="replay_postings must be 0..50")
    if body.flood_records is not None and not 0 <= body.flood_records <= 300:
        raise HTTPException(status_code=400, detail="flood_records must be 0..300")
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
        if body.replay_postings is not None:
            link.faults.replay_postings = body.replay_postings
        if body.send_oversize is not None:
            link.faults.send_oversize = body.send_oversize
        if body.flood_records is not None:
            link.faults.flood_records = body.flood_records
    return get_link().snapshot()  # type: ignore[no-any-return]


@router.post("/admin/fias/reset")
def post_fias_reset(request: Request) -> dict[str, Any]:
    _require_admin_network(request)
    link = get_link()
    link.reset_faults()
    with link.lock:
        link.faults.drop_link = True  # fresh handshake picks up baseline
    return get_link().snapshot()  # type: ignore[no-any-return]


@router.post("/admin/fias/beacon")
def post_fias_beacon(body: FiasBeaconRequest, request: Request) -> dict[str, Any]:
    """Activate a REQ-025 beacon instance on the FIAS exchange.

    The harness passes only the eval seed + index; the instance is
    generated PABX-side. ``fias_layout`` arms a dynamic PS wire layout
    (announced in LR, records shaped at send time); ``fias_link`` arms
    the matching link behavior (resync or ACK-timeout silence, mirroring
    the named-scenario precedents). Any other family/vendor is 400.
    Returns the public descriptor (never the answer key).
    """
    _require_admin_network(request)
    import simulators.common.faultgen as _faultgen  # noqa: PLC0415 - simulators-internal
    from simulators.common import beacon_serve as _beacon  # noqa: PLC0415 - simulators-internal

    try:
        descriptor = _beacon.set_beacon(body.eval_seed_hex, body.index)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid beacon instance: {exc}") from exc
    if descriptor["vendor"] != "fias-pabx":
        _beacon.clear_beacon()
        raise HTTPException(status_code=400, detail=f"beacon index {body.index} targets {descriptor['vendor']}, not fias-pabx")
    instance = _faultgen.generate_instance(bytes.fromhex(body.eval_seed_hex.strip()), body.index)
    behavior = _faultgen.behavior_for_instance(instance)
    if behavior is None:
        _beacon.clear_beacon()
        raise HTTPException(status_code=400, detail=f"family {descriptor['family']} has no FIAS behavior")
    link = get_link()
    with link.lock:
        # Baseline the layout knobs first: a beacon instance fully
        # describes the wire shape (stale aliases must not leak across runs).
        link.faults.ps_layout = "baseline"
        link.faults.ps_layout_fields = None
        link.faults.ps_field_aliases = None
        link.faults.resync_on_reconnect = False
        link.faults.send_zeros_then_drop = False
        link.faults.silent_types = ()
        family = str(behavior.get("family"))
        if family == "fias_layout":
            link.faults.ps_layout_fields = [str(f) for f in behavior["announced_fields"]]
            link.faults.ps_field_aliases = {str(new): str(old) for old, new in dict(behavior.get("renamed", {})).items()}
            link.faults.drop_link = True  # fresh handshake announces the layout
        elif family == "fias_link":
            kind = str(behavior.get("kind"))
            if kind == "resync":
                link.faults.resync_on_reconnect = True
                link.faults.send_zeros_then_drop = True
                link.faults.drop_link = True
            elif kind == "ack_timeout":
                link.faults.silent_types = ("WR",)
            else:  # pragma: no cover - faultgen constrains kinds
                raise HTTPException(status_code=400, detail=f"unknown fias_link kind: {kind}")
        else:  # pragma: no cover - vendor gate above constrains families
            raise HTTPException(status_code=400, detail=f"family {family} is not a FIAS family")
    return {"beacon": descriptor}


class FiasBeaconRequest(BaseModel):
    eval_seed_hex: str
    index: int


class ScenarioRequest(BaseModel):
    id: str
    seed: int = 0


@router.post("/admin/scenario")
def post_fias_scenario(body: ScenarioRequest, request: Request) -> dict[str, Any]:
    """Named-scenario injection for the FIAS exchange (REQ-022 D).

    This router mounts before the shared admin router, so this shadows the
    generic ``POST /admin/scenario`` on the FIAS sim only: the shared slot
    is set exactly like elsewhere (``/demo/inject-fault`` labels the same
    way), and a scenario with PABX-side meaning additionally arms the link
    faults from :data:`SCENARIO_LINK_FAULTS`. Scenarios without link
    meaning (agent killed or cloud slowed by the test itself) record the
    label and touch nothing.
    """
    _require_admin_network(request)
    from simulators.common.faults import validate_scenario_id  # noqa: PLC0415 - simulators-internal
    from simulators.common.state import set_scenario  # noqa: PLC0415 - simulators-internal

    try:
        validate_scenario_id(body.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if not isinstance(body.seed, int) or body.seed < 0:
        raise HTTPException(status_code=400, detail="seed must be non-negative int")
    if body.seed > 1_000_000_000:
        raise HTTPException(status_code=400, detail="seed too large")
    state = set_scenario(body.id, body.seed)
    armed: dict[str, Any] = dict(SCENARIO_LINK_FAULTS.get(body.id, {}))
    if armed:
        link = get_link()
        with link.lock:
            if "ps_layout" in armed:
                link.faults.ps_layout = str(armed["ps_layout"])
            if armed.get("send_zeros_then_drop"):
                link.faults.send_zeros_then_drop = True
            if armed.get("drop_link"):
                link.faults.drop_link = True
            if "silent_types" in armed:
                link.faults.silent_types = tuple(armed["silent_types"])
            if armed.get("replay_postings"):
                link.faults.replay_postings = int(armed["replay_postings"])
            if armed.get("burst_postings"):
                link.faults.burst_postings = int(armed["burst_postings"])
            if armed.get("send_oversize"):
                link.faults.send_oversize = True
            if armed.get("flood_records"):
                link.faults.flood_records = int(armed["flood_records"])
    return {
        "scenario_id": state["scenario_id"],
        "seed": state["seed"],
        "request_count": state["request_count"],
        "armed_link_faults": armed,
    }
