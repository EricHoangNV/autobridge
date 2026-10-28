"""Simulated edge worker (P2.S2, plan §5 S2).

Runs on the isolated ``edge-net`` Docker network and executes only signed
connector releases against the edge-only sim POS (``sim-edgepos``):

* ``POST /execute`` takes ``{spec, signature, operation}``, verifies
  ``signature`` against the spec (verify centrally, execute locally),
  enforces the edge allowlist, and interprets the operation. When
  ``EDGE_RELEASE_SECRET`` is set the signature is an HMAC only the secret
  holder can mint; otherwise it is an integrity checksum (local dev only).
* Unsigned (missing signature) and tampered (mismatch) releases are refused
  with 422/403 before any network or interpreter work.
* ``GET /healthz`` / ``GET /readyz`` mirror the other engine services.

Trust boundary: this module lives in the engine package (``src/autobridge``)
and never imports ``verifier`` or ``simulators``.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from autobridge.health import healthz, readyz
from autobridge.runtime.targets import (
    EDGE_POS_HOST,
    EdgeTarget,
    NetworkBlockedError,
    TargetConfigError,
    release_signature,
    verify_release_signature,
)

app = FastAPI(title="AutoBridge Edge Worker")


def _allowlist() -> list[str]:
    # REQ-014: an empty/blank EDGE_ALLOWLIST counts as unset (falls back
    # to the default host instead of yielding an empty allowlist).
    raw = (os.getenv("EDGE_ALLOWLIST") or "").strip() or EDGE_POS_HOST
    return [h.strip() for h in raw.split(",") if h.strip()]


def _worker() -> EdgeTarget:
    return EdgeTarget(
        endpoint=os.getenv("EDGE_WORKER_ENDPOINT"),
        network_allowlist=_allowlist(),
        # REQ-014: an empty EDGE_TIMEOUT_S counts as unset (default 60s).
        timeout_s=int((os.getenv("EDGE_TIMEOUT_S") or "").strip() or "60"),
    )


class ExecuteRequest(BaseModel):
    spec: dict[str, Any] = Field(description="Full ConnectorSpec document")
    signature: str | None = Field(
        default=None,
        description="HMAC-signed spec_signature(spec) from the release "
        "(EDGE_RELEASE_SECRET); integrity checksum when no key is configured",
    )
    operation: str = Field(min_length=1, description="Operation to execute")
    params: dict[str, Any] | None = Field(default=None)


@app.get("/healthz")
def get_healthz() -> dict[str, str]:
    return healthz()  # type: ignore[return-value]


def _check_edge() -> bool:
    """Edge factory + signed-only + allowlist intact."""
    try:
        worker = _worker()
        assert worker.kind == "edge"
        if worker.can_reach("http://verifier:8000/verify"):
            return False  # pragma: no cover - validate_allowlist rejects blocked hosts at construction
        if worker.can_reach(f"http://{EDGE_POS_HOST}:8001/admin/reset"):
            return False  # pragma: no cover - admin paths are always blocked; backstop
        probe: dict[str, Any] = {"spec_version": "v1"}
        sig = release_signature(probe)
        verify_release_signature(probe, sig)
        try:
            verify_release_signature(probe, "tampered")
            return False  # pragma: no cover - signature mismatch always raises; backstop
        except TargetConfigError:
            pass
        return worker.execute().status == "blocked"
    except Exception:
        return False


@app.get("/readyz")
def get_readyz() -> dict[str, object]:
    return readyz({"edge_target": _check_edge()})  # type: ignore[return-value]


@app.post("/execute")
def post_execute(body: ExecuteRequest) -> dict[str, Any]:
    """Execute a signed release operation on the edge worker."""
    worker = _worker()
    try:
        verify_release_signature(body.spec, body.signature)
    except TargetConfigError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    try:
        result = worker.execute_signed_release(
            body.spec, body.signature, body.operation, body.params
        )
    except TargetConfigError as exc:  # pragma: no cover - app pre-verified; worker double-checks
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except NetworkBlockedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if result.status == "error":
        raise HTTPException(status_code=502, detail=result.error or "edge execution failed")
    return {
        "status": result.status,
        "records": result.records,
        "output": result.output,
        "elapsed_ms": result.elapsed_ms,
    }
