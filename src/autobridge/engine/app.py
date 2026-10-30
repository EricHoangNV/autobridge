"""Worker service stub — health endpoints only (W1).

The worker executes BuildRun / SyncRun / RepairRun jobs (W1.6 wires the
JobRunner here). It lives on engine-net only: no route to the verifier's
fixture volume or the simulator admin API (plan §2.1).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI

from autobridge.health import healthz, readyz_response

if TYPE_CHECKING:
    from fastapi.responses import JSONResponse


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001 - lifespan signature is fixed by FastAPI
    # REQ-045: the worker executes the ladder, so it validates the engine
    # prompts at boot too — fail startup naming the file, never repair
    # with a broken prompt.
    from autobridge.llm.prompts import ensure_prompts_loaded  # noqa: PLC0415

    ensure_prompts_loaded()
    yield


app = FastAPI(title="AutoBridge Worker", lifespan=lifespan)


@app.get("/healthz")
def get_healthz() -> dict[str, str]:
    return healthz()  # type: ignore[return-value]


@app.get(
    "/readyz",
    responses={
        200: {"description": "Ready: the job store is reachable."},
        503: {"description": "Degraded: the job store is unreachable (see body)."},
    },
)
def get_readyz() -> JSONResponse:
    """Readiness is the job store (AR-2-6).

    The worker claims jobs from the same database the control plane
    writes them to; if it cannot open that store it is not ready to
    work, so the check is real (``SELECT 1``) rather than a fixed pass.
    """
    from autobridge.control.models import default_database_url, engine_from_url

    checks: dict[str, bool] = {}
    try:
        engine = engine_from_url(default_database_url())
        with engine.connect() as conn:
            from sqlalchemy import text

            conn.execute(text("SELECT 1"))
        checks["db"] = True
    except Exception:
        checks["db"] = False

    checks["default"] = all(checks.values())
    return readyz_response(checks)
