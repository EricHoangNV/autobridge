"""Verifier service stub — health endpoints only (W1)."""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="AutoBridge Verifier")


@app.get("/healthz")
def get_healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def get_readyz() -> dict[str, object]:
    return {"status": "ok", "checks": {"default": "ok"}}
