"""P2.S4.1 — mock HotelTools V1/V2 + compatibility gateway (FastAPI).

Endpoints:
  GET  /healthz, /readyz
  GET  /v1/catalog, /v2/catalog, /catalog/diff
  POST /gateway/invoke   {"tool", "params"} → outcome envelope
  POST /gateway/validate {"mapping"} → 200 safe / 422 unsafe
  POST /replay/run       {"mode": "gateway" | "rediscovery"}
  POST /replay/compare   → both replays + verdict
  POST /reset            → reset posted charges (test isolation)

The mock carries no credentials and returns none (Directives §6);
every mutation-free read is deterministic from the frozen toy store.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from mcp_stretch import replay as replay_mod
from mcp_stretch.catalog import V1_CATALOG, V1_VERSION, V2_CATALOG, V2_VERSION, diff
from mcp_stretch.gateway import PINNED_MAPPING, Gateway, validate_mapping

app = FastAPI(title="mcp-hotel (P2.S4.1 stretch mock)")

_GATEWAY = Gateway()


class InvokeIn(BaseModel):
    tool: str
    params: dict[str, Any] = {}


class ValidateIn(BaseModel):
    mapping: dict[str, dict[str, Any]]


class ReplayIn(BaseModel):
    mode: str = "gateway"


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> dict[str, Any]:
    return {
        "status": "ok",
        "ready": True,
        "checks": {"catalog": "ok", "pinned_mapping": "ok"},
        "v1": V1_VERSION,
        "v2": V2_VERSION,
        "v1_tools": len(V1_CATALOG),
        "v2_tools": len(V2_CATALOG),
        "pinned_mappings": len(PINNED_MAPPING),
    }


@app.get("/v1/catalog")
def v1_catalog() -> dict[str, Any]:
    return {"version": V1_VERSION, "tools": V1_CATALOG}


@app.get("/v2/catalog")
def v2_catalog() -> dict[str, Any]:
    return {"version": V2_VERSION, "tools": V2_CATALOG}


@app.get("/catalog/diff")
def catalog_diff() -> dict[str, Any]:
    return diff(V1_CATALOG, V2_CATALOG)


@app.post("/gateway/invoke")
def gateway_invoke(body: InvokeIn) -> dict[str, Any]:
    return _GATEWAY.invoke(body.tool, body.params)


@app.post("/gateway/validate")
def gateway_validate(body: ValidateIn) -> dict[str, Any]:
    ok, reason = validate_mapping(body.mapping)
    if not ok:
        raise HTTPException(status_code=422, detail=reason)
    return {"valid": True, "reason": reason}


@app.post("/replay/run")
def replay_run(body: ReplayIn) -> dict[str, Any]:
    if body.mode == "gateway":
        _GATEWAY.reset()
        return replay_mod.replay(replay_mod.PINNED_WORKFLOW, replay_mod.gateway_backend(_GATEWAY))
    if body.mode == "rediscovery":
        _GATEWAY.reset()
        return replay_mod.replay(
            replay_mod.PINNED_WORKFLOW, replay_mod.plain_rediscovery_backend(_GATEWAY)
        )
    raise HTTPException(status_code=422, detail=f"unknown_mode:{body.mode}")


@app.post("/replay/compare")
def replay_compare() -> dict[str, Any]:
    return replay_mod.compare(_GATEWAY)


@app.post("/reset")
def reset() -> dict[str, str]:
    _GATEWAY.reset()
    return {"status": "reset"}
