"""Runtime-sandbox service stub — health endpoints only (W1).

The sandbox interprets ConnectorSpecs (W3 wires the interpreter here). It
is the only service allowed to reach the simulators' public API
(vendor-net); it has no route to the verifier or the simulator admin API.
"""

from __future__ import annotations

from fastapi import FastAPI

from autobridge.health import healthz, readyz

app = FastAPI(title="AutoBridge Runtime Sandbox")


@app.get("/healthz")
def get_healthz() -> dict[str, str]:
    return healthz()  # type: ignore[return-value]


@app.get("/readyz")
def get_readyz() -> dict[str, object]:
    return readyz()  # type: ignore[return-value]
