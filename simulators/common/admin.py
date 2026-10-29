"""Admin API for simulator fault injection (P1.W2.2).

Exposes ``POST /admin/scenario`` and ``POST /admin/reset`` on a separate
port (8001) and network (admin-net). Reachable from ``control`` (for
``POST /demo/inject-fault`` proxy) and from the host for e2e.

Trust boundary: this module never imports ``autobridge`` or ``verifier``.
"""

from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request
from pydantic import BaseModel

from simulators.common.faults import KNOWN_SCENARIO_IDS, validate_scenario_id
from simulators.common.state import get_state, reset_state, set_scenario

#: Env key naming the shared secret that authorizes simulator admin writes
#: (AR-1-1). The value is read at request time and never logged or echoed.
#: When unset/empty the admin API falls back to the header-only dev mode.
ADMIN_TOKEN_ENV_VAR = "SIM_ADMIN_TOKEN"

#: Header carrying the admin token (sent alongside the routing hint below).
ADMIN_TOKEN_HEADER = "x-admin-token"


class ScenarioRequest(BaseModel):
    id: str
    seed: int = 0


class ScenarioResponse(BaseModel):
    scenario_id: str
    seed: int
    request_count: int


class BeaconRequest(BaseModel):
    eval_seed_hex: str
    index: int


class BeaconResponse(BaseModel):
    index: int
    vendor: str
    family: str
    params: dict[str, Any]
    request_count: int


def _require_admin_network(request: Request) -> None:
    """Enforce simulator-admin authorization (AR-1-1).

    The ``X-Admin-Network: admin-net`` header is only a routing hint: its
    value is published in this source tree, so any vendor-net peer can send
    it. Real authorization is the shared secret in ``SIM_ADMIN_TOKEN``,
    presented as ``X-Admin-Token`` and compared in constant time. When the
    env var is unset/empty (local dev), the header-only check applies; when
    it is set, a missing or wrong token is 403 even with a correct header.
    The smoke probe (runtime → sim:8001) sends no token and therefore gets
    403, which the probe treats as "down" — preserving the negative check.
    Host-based e2e and the control proxy send both headers.
    """
    hdr = request.headers.get("x-admin-network")
    if hdr != "admin-net":
        raise HTTPException(status_code=403, detail="admin network required")
    expected = os.environ.get(ADMIN_TOKEN_ENV_VAR, "")
    if not expected:
        return
    presented = request.headers.get(ADMIN_TOKEN_HEADER, "")
    if not presented or not hmac.compare_digest(presented, expected):
        raise HTTPException(status_code=403, detail="admin token required")


def create_admin_app(extra_router: APIRouter | None = None) -> FastAPI:
    app = FastAPI(title="Simulator Admin API")

    if extra_router is not None:
        app.include_router(extra_router)

    @app.get("/healthz")
    def get_healthz(request: Request) -> dict[str, str]:
        # Smoke probes /healthz without headers — 403 preserves the
        # negative check, but hosts with both headers pass.
        _require_admin_network(request)
        return {"status": "ok"}

    @app.get("/readyz")
    def get_readyz(request: Request) -> dict[str, object]:
        _require_admin_network(request)
        return {"status": "ok", "checks": {"default": "ok"}}

    @app.get("/admin/scenario")
    def get_scenario(request: Request) -> dict[str, Any]:
        _require_admin_network(request)
        state = get_state()
        return {
            "scenario_id": state["scenario_id"],
            "seed": state["seed"],
            "request_count": state["request_count"],
            "known_scenarios": sorted(KNOWN_SCENARIO_IDS),
        }

    @app.post("/admin/scenario", response_model=ScenarioResponse)
    def post_scenario(body: ScenarioRequest, request: Request) -> dict[str, Any]:
        _require_admin_network(request)
        try:
            validate_scenario_id(body.id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not isinstance(body.seed, int) or body.seed < 0:
            raise HTTPException(status_code=400, detail="seed must be non-negative int")
        if body.seed > 1_000_000_000:
            raise HTTPException(status_code=400, detail="seed too large")
        state = set_scenario(body.id, body.seed)
        return {
            "scenario_id": state["scenario_id"],
            "seed": state["seed"],
            "request_count": state["request_count"],
        }

    @app.post("/admin/reset", response_model=ScenarioResponse)
    def post_reset(request: Request) -> dict[str, Any]:
        _require_admin_network(request)
        state = reset_state()
        return {
            "scenario_id": state["scenario_id"],
            "seed": state["seed"],
            "request_count": state["request_count"],
        }

    @app.post("/admin/beacon", response_model=BeaconResponse)
    def post_beacon(body: BeaconRequest, request: Request) -> dict[str, Any]:
        """Activate a REQ-025 beacon instance (eval seed + index).

        The simulator generates the instance internally (faultgen); the
        harness passes only the seed. Returns the public descriptor
        (index, vendor, family, params) — never the answer key. Fail
        closed (400) on bad hex, bad index, or generator rejection.
        """
        _require_admin_network(request)
        from simulators.common import beacon_serve as _beacon

        try:
            descriptor = _beacon.set_beacon(body.eval_seed_hex, body.index)
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=f"invalid beacon instance: {exc}") from exc
        state = get_state()
        return {
            "index": descriptor["index"],
            "vendor": descriptor["vendor"],
            "family": descriptor["family"],
            "params": descriptor["params"],
            "request_count": state["request_count"],
        }

    @app.get("/admin/beacon")
    def get_beacon(request: Request) -> dict[str, Any]:
        """Return the active beacon public descriptor, or ``{"beacon": None}``."""
        _require_admin_network(request)
        from simulators.common import beacon_serve as _beacon

        instance = _beacon.active_instance()
        if instance is None:
            return {"beacon": None}
        return {"beacon": _beacon.public_descriptor(instance)}

    @app.get("/admin/healthz")
    def admin_healthz(request: Request) -> dict[str, str]:
        _require_admin_network(request)
        return {"status": "ok"}

    return app


# Default admin app instance (used when running as separate uvicorn).
admin_app = create_admin_app()
