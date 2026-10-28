"""FIAS PABX simulator public app (REQ-022 sub-step A).

Serves, on the container's port 8000 (edge-net only — never published past
loopback; the cloud control plane has no route here):

* ``/healthz`` / ``/readyz`` — process readiness. A missing host peer is
  reported in the checks (``fias_link: down``) but is not a readiness
  failure: the simulator is ready to serve; the *peer* is absent. The
  nightly-restart rule (§5) means the link flaps by design.
* ``/developers`` — the hotel's uploaded interface specification, viewed
  as a document (``simulators/devportal/fias.html`` renders
  ``simulators/docs/fias-pabx.md`` at load time; the ``.md`` is the single
  source, as with the other three portals).
* ``/docs`` — the raw ``simulators/docs/`` mount AutoBridge's docs reader uses.

The FIAS link engine (``PabxLink``) runs in a background thread started by
the lifespan: it dials the integration host (``FIAS_HOST:FIAS_PORT``,
default ``edge-worker:5010`` per the site settings) and reconnects while
no link is up. ``FIAS_NO_LINK=1`` disables the dial-out (unit tests).

This module must never import autobridge or verifier (trust boundary).
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from simulators.common.devportal import mount_devportal

from . import admin as fias_admin
from .pabx import PabxConfig, PabxLink

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"


def _config_from_env() -> PabxConfig:
    def _float(key: str, default: float) -> float:
        try:
            return float(os.environ.get(key, "").strip() or default)
        except ValueError:
            return default

    def _int(key: str, default: int) -> int:
        try:
            return int(os.environ.get(key, "").strip() or default)
        except ValueError:
            return default

    return PabxConfig(
        host=os.environ.get("FIAS_HOST", "edge-worker"),
        port=_int("FIAS_PORT", 5010),
        reconnect_interval=_float("FIAS_RECONNECT_INTERVAL", 10.0),
        keepalive_interval=_float("FIAS_KEEPALIVE_INTERVAL", 5.0),
        dead_after=_float("FIAS_DEAD_AFTER", 30.0),
        ack_wait=_float("FIAS_ACK_WAIT", 5.0),
        resend_interval=_float("FIAS_RESEND_INTERVAL", 60.0),
        seed=_int("FIAS_SEED", 0),
    )


def create_app() -> FastAPI:
    app = FastAPI(title="FIAS PABX Simulator", openapi_url=None, docs_url=None, redoc_url=None)

    if DOCS_DIR.is_dir():
        app.mount("/docs", StaticFiles(directory=str(DOCS_DIR)), name="docs")
    mount_devportal(app, page="fias.html", source="fias-pabx.md")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):  # type: ignore[no-untyped-def]
        link: PabxLink | None = None
        if os.environ.get("FIAS_NO_LINK") != "1":
            link = PabxLink(_config_from_env())
            link.start()
        fias_admin.set_link(link)
        try:
            yield
        finally:
            if link is not None:
                link.stop()

    app.router.lifespan_context = lifespan

    @app.get("/healthz")
    def get_healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def get_readyz() -> dict[str, Any]:
        link = fias_admin.get_link_or_none()
        if link is None:
            fias_link = "disabled (FIAS_NO_LINK=1)"
        elif link.link_up:
            fias_link = "up"
        else:
            fias_link = f"down: dialling {link.config.host}:{link.config.port}"
        return {"status": "ok", "checks": {"admin": "ok", "docs": "ok", "fias_link": fias_link}}

    return app


app = create_app()
