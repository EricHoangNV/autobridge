"""Bench service stub — health endpoints only (W1).

The bench harness (W8) runs AutoBridge and the coding-agent baseline under
matched budgets. Stub only until then.
"""

from __future__ import annotations

from fastapi import FastAPI

from autobridge.health import healthz, readyz

app = FastAPI(title="AutoBridge Bench")


@app.get("/healthz")
def get_healthz() -> dict[str, str]:
    return {"status": healthz()["status"]}


@app.get("/readyz")
def get_readyz() -> dict[str, object]:
    return dict(readyz())
