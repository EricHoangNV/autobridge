# ruff: noqa: B008, TC002, TC003, ARG001, SIM102, SIM105, SIM118, UP035, I001, UP006, UP007, RUF012, B027
"""Control plane — FastAPI (P1.W7.1, plan §2.11).

Every endpoint of plan §2.11 plus health contracts. OpenAPI is the contract
(schemathesis validates it). The UI client is generated from this schema via
``openapi-typescript`` (W7.3).

Trust boundaries: this module may call the verifier and simulator admin over
HTTP (it is on verifier-net + admin-net); it never imports ``verifier`` or
``simulators`` as Python packages.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from autobridge.control.deps import get_engine
from autobridge.control.routes.bench import router as bench_router
from autobridge.control.schemas import BAD_REQUEST_400, NOT_FOUND_404
from autobridge.control.routes.builds import router as builds_router
from autobridge.control.routes.capabilities import router as capabilities_router
from autobridge.control.routes.connectors import router as connectors_router
from autobridge.control.routes.demo import router as demo_router
from autobridge.control.routes.discovery import router as discovery_router
from autobridge.control.routes.drift import router as drift_router
from autobridge.control.routes.edge import router as edge_router
from autobridge.control.routes.internal import router as internal_router
from autobridge.control.routes.metrics import router as metrics_router
from autobridge.control.routes.repairs import router as repairs_router
from autobridge.control.routes.runs import router as runs_router


def reclaim_orphaned_jobs() -> int:
    """Re-queue ``running`` jobs left behind by a dead process (REQ-032).

    The scheduler skips busy connectors, so an orphaned ``running`` row
    would wedge its connector in "sync running" forever after a restart
    or crash. Returns the number of jobs moved back to ``queued``; the
    runner's retry budget (``attempts``/``max_attempts``) still applies.
    """
    from autobridge.control.runner import get_runner  # noqa: PLC0415

    return get_runner(None).reclaim_orphaned()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Migrate on startup (REQ-021 owner check): create_all never adds
    # columns, so a database file kept across runs goes stale and endpoints
    # 500. Upgrade-to-head handles fresh, stale and current databases;
    # failure is loud (no serving a broken schema).
    import asyncio  # noqa: PLC0415

    from autobridge.config import sync_interval_seconds  # noqa: PLC0415
    from autobridge.control.migrate import ensure_migrated  # noqa: PLC0415

    ensure_migrated(get_engine())
    # Re-queue jobs orphaned by a dead process (restart/crash path): a
    # ``running`` row with no living worker would otherwise wedge its
    # connector in "sync running" forever, because the scheduler skips
    # busy connectors. Best-effort: a broken jobs table must not stop
    # serving (migrations already ran above).
    try:
        reclaimed = reclaim_orphaned_jobs()
        if reclaimed:
            import logging  # noqa: PLC0415

            logging.getLogger("autobridge.control").warning(
                "reclaimed %d orphaned job(s) to queued", reclaimed
            )
    except Exception:
        pass
    scheduler_task: asyncio.Task[None] | None = None
    try:
        interval = sync_interval_seconds()
    except ValueError:
        # A typo'd interval must fail startup loudly, never silently run
        # unscheduled (or scheduled at a surprising cadence).
        raise
    if interval is not None:
        scheduler_task = asyncio.create_task(_scheduler_loop(interval))
    try:
        yield
    finally:
        if scheduler_task is not None:
            scheduler_task.cancel()
            try:
                await scheduler_task
            except asyncio.CancelledError:
                pass


def _run_scheduler_pass(factory: Any) -> None:
    """One scheduler pass on a worker thread (never on the event loop).

    Syncs do blocking HTTP with retry/backoff budgets; running them on the
    loop would stall readyz, SSE and the UI for the whole pass. A pass
    never raises: per-connector results are row data, and a pass-level
    failure rolls back and is forgotten (the next tick tries again).
    """
    try:
        with factory() as session:
            try:
                from autobridge.control.scheduler import run_scheduler_pass  # noqa: PLC0415

                run_scheduler_pass(session)
            except Exception:
                session.rollback()
    except Exception:
        pass


async def _scheduler_loop(interval_s: float) -> None:
    """Tick the sync scheduler until cancelled (REQ-030 item 1).

    One pass per interval: sync every idle active connector through the
    same path as POST /sync. Passes run on a worker thread
    (:func:`_run_scheduler_pass`); a tick whose predecessor is still
    running is skipped — overlapping passes would double-sync connectors
    the busy check is meant to serialize. Secrets never appear here:
    syncs read credential values from the environment at runtime, as always.
    """
    import asyncio  # noqa: PLC0415

    from autobridge.control.deps import get_session_factory  # noqa: PLC0415

    factory = get_session_factory()
    running = False

    def _done(_task: Any) -> None:
        nonlocal running
        running = False

    while True:
        if not running:
            running = True
            task = asyncio.create_task(asyncio.to_thread(_run_scheduler_pass, factory))
            task.add_done_callback(_done)
        try:
            await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            break


app = FastAPI(
    title="AutoBridge Control",
    description="Generic Connect + Heal engine — control plane (plan §2.11). Every repair is a JSON Patch; transforms are closed; money is Decimal; dates are property-local.",
    version="0.1.0",
    lifespan=lifespan,
    responses={**NOT_FOUND_404, **BAD_REQUEST_400},
)

# Register routers (order determines OpenAPI grouping)
app.include_router(connectors_router)
app.include_router(builds_router)
app.include_router(capabilities_router)
app.include_router(runs_router)
app.include_router(drift_router)
app.include_router(repairs_router)
app.include_router(demo_router)
app.include_router(discovery_router)
app.include_router(edge_router)
app.include_router(internal_router)
app.include_router(metrics_router)
app.include_router(bench_router)


@app.get("/healthz", tags=["health"])
def get_healthz() -> dict[str, str]:
    from autobridge.health import healthz

    return healthz()  # type: ignore[return-value]


@app.get(
    "/readyz",
    tags=["health"],
    responses={
        200: {"description": "Ready: every check passed."},
        503: {"description": "Degraded: at least one check failed (see body)."},
    },
)
def get_readyz() -> JSONResponse:
    from autobridge.health import readyz_response

    checks: dict[str, bool] = {}
    # DB check
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        checks["db"] = True
    except Exception:
        checks["db"] = False
    # Verifier reachability: readyz reports the dependency as it is
    # (plan §3) — unreachable means "fail"/degraded, never a forced pass.
    try:
        import os

        import httpx

        verifier_url = os.environ.get("VERIFIER_URL", "http://verifier:8000")
        with httpx.Client(timeout=2) as client:
            r = client.get(f"{verifier_url.rstrip('/')}/healthz")
            checks["verifier"] = r.status_code == 200
    except Exception:
        checks["verifier"] = False

    checks["default"] = all(checks.values())
    # REQ-017: report the runtime mode on /readyz (informational only —
    # it never flips the status; local mode still reports ok/degraded
    # honestly from the checks above).
    from autobridge.runtime.mode import network_isolation_enforced, runtime_mode

    return readyz_response(
        checks,
        info={
            "runtime_mode": runtime_mode(),
            "network_isolation": "enforced" if network_isolation_enforced() else "local mode: no network isolation",
        },
    )


@app.get("/", tags=["health"])
def root() -> dict[str, str]:
    return {"service": "autobridge-control", "title": app.title}
