"""On-premise edge agent (REQ-022 sub-step B; replaces the P2.S2 toy worker).

Outbound only: no inbound port from the cloud. The agent

* listens on TCP ``:5010`` for the PABX dial-out (host-side FIAS link —
  :mod:`autobridge.edge.fias_host`),
* polls the cloud command queue (the ``edge-relay`` service) and reports
  through authenticated callbacks (client-credentials style
  ``EDGE_AGENT_TOKEN``, never logged),
* ACKs the PABX after a durable local write (SQLite outbox/inbox in
  ``EDGE_STORE_PATH`` — survives agent restart), never after the cloud
  call, so a slow cloud cannot stall the link.

Delivery: at-least-once with idempotency keys (``P#`` + room + time for
``PS``), answers correlated by request id, command expiry enforced (a
stale wake-up is never sent), ACK timeouts reported to the cloud, one
command in flight per link with a bounded wait. What the agent executes
is declarative spec data (``agent_bundle.json``) verified against a
pinned signature (``EDGE_BUNDLE_SIGNATURE``, HMAC when
``EDGE_RELEASE_SECRET`` is set — AR-2-4).

``GET /healthz`` / ``GET /readyz`` stay for orchestration: ``readyz``
fails only when the agent itself is broken (store unwritable, bundle
invalid, link thread dead). A missing PABX peer is reported in the info
block but never fails readiness — the nightly-restart rule flaps links
by design.

Trust boundary: this module lives in the engine package
(``src/autobridge``) and never imports ``verifier`` or ``simulators``.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from autobridge.edge.bundle import BundleError, is_pinned, verify_bundle
from autobridge.edge.fias_host import FiasHost, Record
from autobridge.edge.poller import (
    Agent,
    agent_id_from_env,
    max_attempts_from_env,
    relay_client_from_env,
    relay_url_from_env,
)
from autobridge.edge.store import AgentStore
from autobridge.health import healthz, readyz_response

log = logging.getLogger("autobridge.edge.app")

app = FastAPI(title="AutoBridge Edge Agent")


def _store_path() -> str:
    return (os.environ.get("EDGE_STORE_PATH", "") or "").strip() or "./edge_agent.db"


def _fias_host() -> str:
    return (os.environ.get("FIAS_LISTEN_HOST", "") or "").strip() or "0.0.0.0"


def _fias_port() -> int:
    try:
        return int((os.environ.get("FIAS_LISTEN_PORT", "") or "").strip() or "5010")
    except ValueError:
        return 5010


class _AgentState:
    """Process-wide agent pieces (built once in the lifespan)."""

    def __init__(self) -> None:
        self.agent_id = agent_id_from_env()
        self.bundle: dict[str, Any] = {}
        self.bundle_ok = False
        self.bundle_detail = ""
        self.store: AgentStore | None = None
        self.link: FiasHost | None = None
        self.agent: Agent | None = None

    def build(self) -> None:
        try:
            self.bundle = verify_bundle()
            self.bundle_ok = True
            self.bundle_detail = "pinned" if is_pinned() else "dev mode (unsigned)"
        except BundleError as exc:
            self.bundle_ok = False
            self.bundle_detail = str(exc)
            log.error("command bundle rejected: %s", exc)
            return
        self.store = AgentStore(_store_path())

        class _Sink:
            def __init__(self, store: AgentStore) -> None:
                self._store = store

            def on_record(self, record: Record) -> str:
                # Durable local write; the link ACK goes out only after
                # this returns. "OK" = accepted into the agent inbox (the
                # cloud reconciliation happens on the forward path).
                self._store.store_posting(record.record_type, record.as_dict())
                return "OK"

        self.link = FiasHost(
            _Sink(self.store),
            listen_host=_fias_host(),
            listen_port=_fias_port(),
            timezone=(os.environ.get("EDGE_PROPERTY_TZ", "") or "").strip() or "America/New_York",
        )
        self.agent = Agent(
            self.agent_id,
            self.store,
            relay_client_from_env(),
            self.bundle,
            self.link,
            max_attempts=max_attempts_from_env(),
        )
        if os.environ.get("EDGE_AGENT_DISABLE_LINK") != "1":
            self.link.start()
        if os.environ.get("EDGE_AGENT_DISABLE_CLOUD") != "1":
            self.agent.start()

    def shutdown(self) -> None:
        if self.agent is not None:
            self.agent.stop()
        if self.link is not None:
            self.link.stop()
        if self.store is not None:
            self.store.close()


_STATE = _AgentState()


def startup() -> _AgentState:
    """Build the process-wide agent state (lifespan + tests)."""
    _STATE.build()
    return _STATE


def shutdown() -> None:
    """Stop threads and close the store (lifespan + tests)."""
    _STATE.shutdown()


def reset() -> _AgentState:
    """Close and drop all built state (tests only; the lifespan never calls it)."""
    global _STATE
    shutdown()
    _STATE = _AgentState()
    return _STATE


@asynccontextmanager
async def lifespan(_app: FastAPI):
    startup()
    try:
        yield
    finally:
        shutdown()


app.router.lifespan_context = lifespan


@app.get("/healthz")
def get_healthz() -> dict[str, str]:
    return healthz()  # type: ignore[return-value]


def _checks() -> tuple[dict[str, bool], dict[str, Any]]:
    checks = {
        "store": _STATE.store.ping() if _STATE.store is not None else False,
        "bundle": _STATE.bundle_ok,
        "link_thread": True,
    }
    info: dict[str, Any] = {
        "agent_id": _STATE.agent_id,
        "bundle": _STATE.bundle_detail,
        "relay_url": relay_url_from_env(),
    }
    link = _STATE.link
    if link is None:
        checks["link_thread"] = False
        info["link"] = "not built (bundle rejected)"
    else:
        state = link.link_state()
        info["link"] = state
        info["relay_contact_ok"] = _STATE.agent.relay.last_contact_ok if _STATE.agent else False
        if _STATE.store is not None:
            info["queue"] = _STATE.store.depths()
    if _STATE.agent is not None:
        info["relay_contact_ok"] = _STATE.agent.relay.last_contact_ok
    return checks, info


@app.get("/readyz")
def get_readyz() -> Any:
    checks, info = _checks()
    return readyz_response(checks, info=info)
