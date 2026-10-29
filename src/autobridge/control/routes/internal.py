# ruff: noqa: B008, TC002, TC003, ARG001, SIM102, SIM105, SIM118, UP035, I001, UP006, UP007, RUF012, B027
"""Internal engine-net endpoints for the worker (REQ-029, AR-1-2 residual).

The worker has no verifier-net route, so it cannot call the verifier
directly. Control does (it sits on verifier-net), so it exposes
``POST /internal/verify``: a spec-verification proxy the worker's gate
uses before persisting any release. Fail-closed: no process persists or
activates a release without a passing verdict.

Trust boundary: this module calls the verifier over HTTP (control is on
verifier-net); it never imports ``verifier`` or ``simulators``.
"""

from __future__ import annotations

import os
from typing import Any, Literal

import httpx
from fastapi import APIRouter, HTTPException, Security
from pydantic import BaseModel, Field

from autobridge.control.deps import require_control_token
from autobridge.control.schemas import AUTH_401

router = APIRouter(tags=["internal"])


class InternalVerifyRequest(BaseModel):
    """Spec-verification proxy body (mirrors the verifier's /verify)."""

    spec: dict[str, Any] = Field(description="Candidate ConnectorSpec dict (credential_ref only, no secrets)")
    scenario_id: str = Field(default="baseline")
    mode: Literal["visible", "hidden"] = Field(default="visible")
    regression_suite: list[str] | None = Field(default=None)


@router.post("/internal/verify", responses=AUTH_401)
def internal_verify(
    body: InternalVerifyRequest, _: None = Security(require_control_token)
) -> dict[str, Any]:
    """Proxy a spec to the verifier and return its JSON verbatim.

    503 when control itself has no verifier configured (``VERIFIER_URL``
    unset); 502 when the verifier is unreachable or answers non-200
    (fail-closed: the worker treats both as gate-blocked and persists
    nothing). The response is the verifier's aggregate verdict only —
    never fixture rows (the verifier's own response-schema guarantee).
    """
    verifier_url = os.environ.get("VERIFIER_URL", "").strip()
    if not verifier_url:
        raise HTTPException(
            status_code=503,
            detail="verifier not configured on control (VERIFIER_URL unset): cannot gate this release",
        )
    payload: dict[str, Any] = {
        "spec": body.spec,
        "scenario_id": body.scenario_id,
        "mode": body.mode,
    }
    if body.regression_suite is not None:
        payload["regression_suite"] = list(body.regression_suite)
    base = verifier_url.rstrip("/")
    url = base if base.endswith("/verify") else base + "/verify"
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(url, json=payload)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"verifier unreachable from control at {url}: {type(exc).__name__}",
        ) from exc
    if resp.status_code != 200:
        detail = ""
        try:
            detail = str(resp.json().get("detail", resp.text)[:300])
        except Exception:
            detail = resp.text[:300]
        raise HTTPException(
            status_code=502,
            detail=f"verifier returned HTTP {resp.status_code} for {body.scenario_id}: {detail}",
        )
    try:
        return dict(resp.json())
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"verifier returned non-JSON for {body.scenario_id}: {type(exc).__name__}",
        ) from exc
