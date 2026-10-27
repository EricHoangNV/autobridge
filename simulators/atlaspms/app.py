"""AtlasPMS simulator public API stub — health endpoints only (W1).

W2 wires the seeded dataset, bearer auth and offset pagination here. This
module must never import autobridge or verifier (trust boundary).
"""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="AtlasPMS Simulator")


@app.get("/healthz")
def get_healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def get_readyz() -> dict[str, object]:
    return {"status": "ok", "checks": {"default": "ok"}}
