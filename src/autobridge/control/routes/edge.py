"""On-premise edge queue proxy (REQ-022 sub-step B).

The cloud command queue lives on the ``edge-relay`` service (edge-net +
engine-net); the agent polls it outbound and the control plane enqueues
through these ``/edge/*`` endpoints, so the UI, the demo and operators
only ever talk to control:

* ``POST /edge/commands`` — enqueue a declarative command for an agent
  (``require_control_token``; idempotent on ``idempotency_key``).
* ``GET /edge/commands/results`` — drain command outcomes.
* ``GET /edge/postings`` — drain PABX-originated records the agents
  forwarded (idempotent keys; duplicates never land twice).
* ``GET /edge/agents`` — last heartbeat per agent (link state, depths).

The proxy forwards ``AUTOBRIDGE_CONTROL_TOKEN`` as the relay bearer when
set (same secret, engine-net only — never logged, never echoed). A relay
that is down or answers 5xx surfaces as 502 with the status only, never
a silent empty list (an empty queue and a dead relay must not look alike).
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Security

from autobridge.control.deps import get_control_token, require_control_token
from autobridge.control.schemas import (
    AUTH_401,
    EDGE_RELAY_502,
    EdgeAgentsResponse,
    EdgeCommandRequest,
    EdgeCommandResponse,
    EdgePostingsResponse,
    EdgeResultsResponse,
)

router = APIRouter(prefix="/edge", tags=["edge"])

RELAY_URL_ENV_VAR = "EDGE_RELAY_URL"
RELAY_TIMEOUT_S = 5.0


def _relay_base() -> str:
    return ((os.environ.get(RELAY_URL_ENV_VAR, "") or "").strip() or "http://edge-relay:8000").rstrip("/")


def _relay_headers() -> dict[str, str]:
    token = get_control_token()
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def _relay_get(path: str, params: dict[str, Any] | None = None) -> Any:
    try:
        with httpx.Client(timeout=RELAY_TIMEOUT_S) as client:
            resp = client.get(f"{_relay_base()}{path}", params=params, headers=_relay_headers())
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"edge relay unreachable: {type(exc).__name__}") from exc
    if resp.status_code >= 500:
        raise HTTPException(status_code=502, detail=f"edge relay error: status {resp.status_code}")
    return resp.json()


def _relay_post(path: str, payload: dict[str, Any]) -> Any:
    try:
        with httpx.Client(timeout=RELAY_TIMEOUT_S) as client:
            resp = client.post(f"{_relay_base()}{path}", json=payload, headers=_relay_headers())
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"edge relay unreachable: {type(exc).__name__}") from exc
    if resp.status_code == 401:
        raise HTTPException(status_code=502, detail="edge relay refused the control credential")
    if resp.status_code >= 500:
        raise HTTPException(status_code=502, detail=f"edge relay error: status {resp.status_code}")
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text[:500])
    return resp.json()


@router.post("/commands", response_model=EdgeCommandResponse,
           responses={**AUTH_401, **EDGE_RELAY_502})
def enqueue_command(body: EdgeCommandRequest, _: None = Security(require_control_token)) -> EdgeCommandResponse:
    data = _relay_post("/v1/enqueue", body.model_dump(exclude_none=True))
    return EdgeCommandResponse(request_id=data["request_id"], existed=bool(data.get("existed", False)))


@router.get("/commands/results", response_model=EdgeResultsResponse,
              responses={**EDGE_RELAY_502})
def command_results(agent_id: str | None = Query(default=None)) -> EdgeResultsResponse:
    data = _relay_get("/v1/results", {"agent_id": agent_id} if agent_id else None)
    return EdgeResultsResponse.model_validate(data)


@router.get("/postings", response_model=EdgePostingsResponse,
              responses={**EDGE_RELAY_502})
def postings(
    agent_id: str | None = Query(default=None),
    since: str | None = Query(default=None),
) -> EdgePostingsResponse:
    params = {k: v for k, v in {"agent_id": agent_id, "since": since}.items() if v is not None}
    data = _relay_get("/v1/postings", params or None)
    return EdgePostingsResponse.model_validate(data)


@router.get("/agents", response_model=EdgeAgentsResponse,
              responses={**EDGE_RELAY_502})
def agents() -> EdgeAgentsResponse:
    data = _relay_get("/v1/agents")
    return EdgeAgentsResponse.model_validate(data)
