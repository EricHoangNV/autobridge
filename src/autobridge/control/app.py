"""Control plane stub — health endpoints only (W1)."""

from __future__ import annotations

from fastapi import FastAPI

from autobridge.health import healthz, readyz

app = FastAPI(title="AutoBridge Control")


@app.get("/healthz")
def get_healthz() -> dict[str, str]:
    return healthz()  # type: ignore[return-value]


@app.get("/readyz")
def get_readyz() -> dict[str, object]:
    return readyz()  # type: ignore[return-value]
