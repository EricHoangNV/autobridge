# ruff: noqa: B008, TC002, TC003, ARG001, SIM102, SIM105, SIM118, UP035, I001, UP006, UP007, RUF012, B027
"""Demo inject-fault endpoint (P1.W7.1) — proxies to simulator admin."""

from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Security

from autobridge.control.deps import require_control_token
from autobridge.control.schemas import AUTH_401, DOMAIN_ERROR_422, InjectFaultRequest, InjectFaultResponse

router = APIRouter(prefix="/demo", tags=["demo"])

# Normalize target aliases to service hostnames and admin paths
TARGET_MAP = {
    "sim-atlas": "sim-atlas",
    "sim-harbor": "sim-harbor",
    "atlas": "sim-atlas",
    "harbor": "sim-harbor",
    "atlaspms": "sim-atlas",
    "harborpms": "sim-harbor",
    "atlas-pms": "sim-atlas",
    "harbor-pms": "sim-harbor",
}


def _resolve_target(target: str) -> str:
    key = target.strip().lower()
    resolved = TARGET_MAP.get(key)
    if not resolved:
        raise HTTPException(status_code=422, detail=f"unknown demo target {target!r}; use sim-atlas or sim-harbor")
    return resolved


def _admin_headers() -> dict[str, str]:
    """Headers proving admin authorization to the simulator admin API.

    ``X-Admin-Network`` is only a routing hint (AR-1-1); the real credential
    is ``SIM_ADMIN_TOKEN`` (server-side env only, never logged or echoed).
    Sent only when configured so header-only dev stacks keep working.
    """
    headers = {"X-Admin-Network": "admin-net"}
    token = os.environ.get("SIM_ADMIN_TOKEN", "")
    if token:
        headers["X-Admin-Token"] = token
    return headers


def _admin_url_for_target(resolved: str) -> str:
    # Inside Docker Compose, admin API is at http://sim-atlas:8001 and http://sim-harbor:8001
    # From host (when not in Docker), we use localhost ports 8109/8110
    # Detect via env: if ADMIN_NET_HOST is set, use Docker hostnames; else try host ports
    if os.environ.get("ADMIN_NET_HOST") or os.path.exists("/.dockerenv"):
        # In container: use service DNS
        return f"http://{resolved}:8001"
    # Host mode: map to host ports
    port_map = {"sim-atlas": 8109, "sim-harbor": 8110}
    port = port_map.get(resolved, 8109)
    return f"http://localhost:{port}"


@router.post("/inject-fault", response_model=InjectFaultResponse, responses={**DOMAIN_ERROR_422, **AUTH_401})
def inject_fault(body: InjectFaultRequest, _: None = Security(require_control_token)) -> InjectFaultResponse:
    resolved = _resolve_target(body.target)
    base = _admin_url_for_target(resolved)

    # Also consider env overrides for admin URLs (tests may set SIM_ADMIN_URL)
    env_key = f"{resolved.upper().replace('-', '_')}_ADMIN_URL"
    if os.environ.get(env_key):
        base = os.environ[env_key]
    # Generic override
    if os.environ.get("SIMULATOR_ADMIN_URL"):
        base = os.environ["SIMULATOR_ADMIN_URL"]

    admin_response: dict[str, Any] | None = None
    # Try to POST to /admin/scenario
    url = f"{base.rstrip('/')}/admin/scenario"
    payload: dict[str, Any] = {"id": body.scenario_id}
    if body.seed is not None:
        payload["seed"] = body.seed
    # Pass through extra fields
    if body.model_extra:
        for k, v in body.model_extra.items():
            if k not in payload:
                payload[k] = v

    try:
        with httpx.Client(timeout=5) as client:
            # The simulator admin API requires proof of admin authorization
            # (simulators/common/admin.py): header-only writes get 403 when
            # SIM_ADMIN_TOKEN is set, and the labelled fault never lands.
            r = client.post(url, json=payload, headers=_admin_headers())
            try:
                admin_response = r.json() if r.content else {"status_code": r.status_code}
            except Exception:
                admin_response = {"status_code": r.status_code, "text": r.text[:500]}
            # Also include status for transparency
            if isinstance(admin_response, dict):
                admin_response["_http_status"] = r.status_code
                admin_response["_url"] = url
    except Exception as exc:
        # Do not fail the demo endpoint if admin is unreachable — return labelled response with error
        admin_response = {"error": str(exc), "_url": url}

    return InjectFaultResponse(
        labelled=True,
        demo=True,
        target=resolved,
        scenario_id=body.scenario_id,
        seed=body.seed,
        admin_response=admin_response,
        message=f"[DEMO] Injected fault {body.scenario_id!r} into {resolved} (labelled demo action)",
    )


@router.post("/reset", response_model=dict, responses=AUTH_401)
def demo_reset(_: None = Security(require_control_token)) -> dict[str, Any]:
    """Demo helper: reset both simulators to baseline."""
    results: dict[str, Any] = {"labelled": True, "demo": True, "resets": []}
    for target in ("sim-atlas", "sim-harbor"):
        base = _admin_url_for_target(target)
        # Same env overrides as inject-fault (tests point these at scratch sims).
        env_key = f"{target.upper().replace('-', '_')}_ADMIN_URL"
        if os.environ.get(env_key):
            base = os.environ[env_key]
        if os.environ.get("SIMULATOR_ADMIN_URL"):
            base = os.environ["SIMULATOR_ADMIN_URL"]
        url = f"{base.rstrip('/')}/admin/reset"
        try:
            with httpx.Client(timeout=5) as client:
                # Admin writes require admin authorization (see inject-fault).
                r = client.post(url, json={}, headers=_admin_headers())
                results["resets"].append({"target": target, "status": r.status_code})
        except Exception as exc:
            results["resets"].append({"target": target, "error": str(exc)})
    return results
