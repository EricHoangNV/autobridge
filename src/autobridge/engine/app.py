"""Worker service stub — health endpoints only (W1).

The worker executes BuildRun / SyncRun / RepairRun jobs (W1.6 wires the
JobRunner here). It lives on engine-net only: no route to the verifier's
fixture volume or the simulator admin API (plan §2.1).
"""

from __future__ import annotations

from fastapi import FastAPI

from autobridge.health import healthz, readyz

app = FastAPI(title="AutoBridge Worker")


@app.get("/healthz")
def get_healthz() -> dict[str, str]:
    return healthz()  # type: ignore[return-value]


@app.get("/readyz")
def get_readyz() -> dict[str, object]:
    return readyz()  # type: ignore[return-value]
