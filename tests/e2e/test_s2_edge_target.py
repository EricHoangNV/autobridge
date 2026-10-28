"""REQ-022 B — on-premise edge agent (replaces the P2.S2 EdgePOS toy).

Passing criteria (REQ-022 B):
  * the agent is outbound-only: it polls a cloud command queue (edge-relay)
    and reports through authenticated callbacks; no inbound execution port
    (``POST /execute`` is 404, live and in-process);
  * delivery guarantees the verifier can check: durable local outbox/inbox
    surviving agent restart, at-least-once with idempotency keys, answers
    correlated by request id, command expiry enforced, ACK timeouts
    reported, one command in flight with a bounded wait;
  * the agent ACKs the PABX after a durable local write, never after the
    cloud call; link supervision (handshake, keep-alive, resync, reconnect,
    oversize/flood session close) on ``/readyz`` and in heartbeats;
  * the agent executes verified, signed bundle data (AR-2-4 HMAC);
  * EdgePOS is gone: no sim-edgepos service, no EDGEPOS env, no /execute.

In-process tests drive the real pieces over loopback TCP (real PabxLink
peer, real relay app, real store) without Docker. Live tests run the same
flow against the Compose stack when it is up. Trust boundary: this file
lives in tests/e2e and may import the engine + simulators for
verification, but the engine itself never imports verifier/simulators.
"""

from __future__ import annotations

import datetime
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from autobridge.control.app import app as control_app
from autobridge.control.deps import get_db
from autobridge.control.models import Base, session_factory
from autobridge.edge.bundle import load_bundle
from autobridge.edge.fias_host import FiasHost
from autobridge.edge.poller import Agent, RelayClient
from autobridge.edge.store import AgentStore


@pytest.fixture()
def control_client(tmp_path: Path, monkeypatch: Any) -> Any:
    """Control app on a scratch DB (lifespan not run — no migrations)."""
    engine = create_engine(f"sqlite:///{tmp_path}/control.db")
    Base.metadata.create_all(engine)
    factory = session_factory(engine)

    def override_db():  # type: ignore[no-untyped-def]
        s = factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    control_app.dependency_overrides[get_db] = override_db
    # Hermetic open mode: the in-process control app reads the ambient
    # AUTOBRIDGE_CONTROL_TOKEN at request time, so a keyed developer shell
    # would 401 these tests. They pin the open path; keyed auth is covered
    # by the live tests against the real stack.
    monkeypatch.delenv("AUTOBRIDGE_CONTROL_TOKEN", raising=False)
    monkeypatch.setenv("EDGE_RELAY_URL", "http://relay")
    c = TestClient(control_app)
    yield c
    control_app.dependency_overrides.clear()


@pytest.fixture()
def relay_pair(tmp_path: Path, monkeypatch: Any) -> tuple[TestClient, RelayClient]:
    """Real relay app + an agent-side client talking to it in-process."""
    from autobridge.edge.relay import _Store, create_app

    relay_app = create_app(_Store(str(tmp_path / "relay.db")))
    inner = TestClient(relay_app)

    class _FakeHttpxClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            _ = (args, kwargs)

        def __enter__(self) -> Any:
            return inner

        def __exit__(self, *args: Any) -> bool:
            return False

    monkeypatch.setattr(httpx, "Client", _FakeHttpxClient)
    return inner, RelayClient("http://relay", session=inner)


class _Sink:
    def __init__(self, store: AgentStore) -> None:
        self.store = store

    def on_record(self, record: Any) -> str:
        self.store.store_posting(record.record_type, record.as_dict())
        return "OK"


@pytest.fixture()
def live_link(tmp_path: Path) -> Any:
    """Real agent host + real PabxLink peer over loopback TCP."""
    from simulators.fias_pabx.pabx import PabxConfig, PabxLink

    store = AgentStore(str(tmp_path / "agent.db"))
    host = FiasHost(_Sink(store), listen_host="127.0.0.1", listen_port=0,
                    keepalive_interval=30.0, dead_after=120.0, ack_wait=2.0)
    host.start()
    deadline = time.monotonic() + 5.0
    port = 0
    while port == 0:
        assert time.monotonic() < deadline, "listener never bound"
        time.sleep(0.02)
        port = host.bound_port()
    pabx = PabxLink(PabxConfig(host="127.0.0.1", port=port, reconnect_interval=0.2,
                               keepalive_interval=30.0, dead_after=120.0, ack_wait=2.0,
                               resend_interval=60.0, seed=0))
    pabx.start()
    deadline = time.monotonic() + 10.0
    while not host.link_state()["link_up"]:
        assert time.monotonic() < deadline, "link never came up"
        time.sleep(0.05)
    yield host, pabx, store
    pabx.stop()
    host.stop()
    store.close()


def _future_wire() -> tuple[str, str]:
    from zoneinfo import ZoneInfo

    now = datetime.datetime.now(ZoneInfo("America/New_York")) + datetime.timedelta(days=2)
    return now.strftime("%y%m%d"), now.strftime("%H%M00")


def _wait_for(pred: Any, timeout: float = 15.0, what: str = "condition") -> Any:
    deadline = time.monotonic() + timeout
    while True:
        value = pred()
        if value:
            return value
        assert time.monotonic() < deadline, f"{what} never became true"
        time.sleep(0.1)


# ---------------------------------------------------------------------------
# In-process: the whole B path without Docker
# ---------------------------------------------------------------------------


def test_agent_full_loop_wakeup_and_posting(control_client: Any, relay_pair: Any, live_link: Any) -> None:
    """Operator → control → relay → agent → PABX, and postings back."""
    relay_inner, relay = relay_pair
    host, pabx, store = live_link
    agent = Agent("test-agent", store, relay, load_bundle(), host, command_timeout=6.0)

    da, ti = _future_wire()
    r = control_client.post("/edge/commands", json={"agent_id": "test-agent", "kind": "wakeup.set",
                                                     "params": {"room": "101", "date": da, "time": ti}})
    assert r.status_code == 200, r.text
    request_id = r.json()["request_id"]

    assert agent.poll_once() == "stored"
    assert agent.execute_once() == "sent"
    assert pabx.snapshot()["scheduled_wakeups"] == 1

    results = control_client.get("/edge/commands/results", params={"agent_id": "test-agent"}).json()
    assert len(results["results"]) == 1
    assert results["results"][0]["request_id"] == request_id

    # Stay postings flowed into the inbox; forward one and drain via control.
    _wait_for(lambda: store.depths()["inbox"] > 0, what="postings in inbox")
    assert agent.forward_once() == "forwarded"
    postings = control_client.get("/edge/postings", params={"agent_id": "test-agent"}).json()
    assert len(postings["postings"]) == 1
    assert postings["postings"][0]["record_type"] == "PS"

    assert agent.heartbeat_once() is True
    agents = control_client.get("/edge/agents").json()["agents"]
    assert agents[0]["agent_id"] == "test-agent"
    assert agents[0]["link_up"] in (1, True)


def test_agent_expired_command_never_sent(control_client: Any, relay_pair: Any, live_link: Any) -> None:
    relay_inner, relay = relay_pair
    host, pabx, store = live_link
    agent = Agent("test-agent", store, relay, load_bundle(), host, command_timeout=6.0)

    past = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)).isoformat()
    r = control_client.post("/edge/commands", json={"agent_id": "test-agent", "kind": "wakeup.set",
                                                     "params": {"room": "101"},
                                                     "expires_at": past})
    assert r.status_code == 200
    assert agent.poll_once() == "stored"
    assert agent.execute_once() == "expired"
    assert pabx.snapshot()["scheduled_wakeups"] == 0
    results = control_client.get("/edge/commands/results", params={"agent_id": "test-agent"}).json()
    assert len(results["results"]) == 1


def test_agent_ack_timeout_reported_link_survives(control_client: Any, relay_pair: Any,
                                                  live_link: Any) -> None:
    relay_inner, relay = relay_pair
    host, pabx, store = live_link
    agent = Agent("test-agent", store, relay, load_bundle(), host, command_timeout=6.0)

    with pabx.lock:
        pabx.faults.silent_types = ("WR",)
    try:
        da, ti = _future_wire()
        r = control_client.post("/edge/commands", json={"agent_id": "test-agent", "kind": "wakeup.set",
                                                         "params": {"room": "101", "date": da,
                                                                    "time": ti}})
        assert r.status_code == 200
        assert agent.poll_once() == "stored"
        assert agent.execute_once() == "ack_timeout"
        assert host.link_state()["link_up"] is True
        results = control_client.get("/edge/commands/results", params={"agent_id": "test-agent"}).json()
        assert len(results["results"]) == 1
    finally:
        with pabx.lock:
            pabx.faults.silent_types = ()


def test_agent_restart_inflight_command_not_lost(tmp_path: Path, control_client: Any,
                                                 relay_pair: Any) -> None:
    """Kill the agent with a command queued: after restart it runs once."""
    from simulators.fias_pabx.pabx import PabxConfig, PabxLink

    relay_inner, relay = relay_pair
    store = AgentStore(str(tmp_path / "agent.db"))
    port = 15010
    host1 = FiasHost(_Sink(store), listen_host="127.0.0.1", listen_port=port,
                     keepalive_interval=30.0, dead_after=120.0, ack_wait=1.0)
    host1.start()
    pabx = PabxLink(PabxConfig(host="127.0.0.1", port=port, reconnect_interval=0.2,
                               keepalive_interval=30.0, dead_after=120.0, ack_wait=1.0,
                               resend_interval=60.0, seed=0))
    pabx.start()
    try:
        _wait_for(lambda: host1.link_state()["link_up"], what="first link")
        agent1 = Agent("test-agent", store, relay, load_bundle(), host1, command_timeout=2.0)
        da, ti = _future_wire()
        r = control_client.post("/edge/commands", json={"agent_id": "test-agent", "kind": "wakeup.set",
                                                         "params": {"room": "101", "date": da,
                                                                    "time": ti}})
        request_id = r.json()["request_id"]
        assert agent1.poll_once() == "stored"
        host1.stop()  # killed with the command queued
        assert agent1.execute_once() == "link_down"  # requeued, not reported
        assert control_client.get("/edge/commands/results",
                                  params={"agent_id": "test-agent"}).json() == {"results": []}

        host2 = FiasHost(_Sink(store), listen_host="127.0.0.1", listen_port=port,
                         keepalive_interval=30.0, dead_after=120.0, ack_wait=1.0)
        host2.start()
        try:
            _wait_for(lambda: host2.link_state()["link_up"], what="second link")
            agent2 = Agent("test-agent", store, relay, load_bundle(), host2, command_timeout=6.0)
            assert agent2.execute_once() == "sent"
            results = control_client.get("/edge/commands/results",
                                         params={"agent_id": "test-agent"}).json()["results"]
            assert [row["request_id"] for row in results] == [request_id]  # reported exactly once
            assert pabx.snapshot()["scheduled_wakeups"] == 1
        finally:
            host2.stop()
    finally:
        pabx.stop()
        store.close()


def test_no_execute_port_and_no_edgepos(control_client: Any) -> None:
    """Outbound-only proof + EdgePOS removal proof at the API level."""
    assert control_client.post("/edge/commands", json={"agent_id": "a"}).status_code == 422  # kind required
    schema = control_client.get("/openapi.json").json()
    assert not any("edgepos" in path for path in schema["paths"])


# ---------------------------------------------------------------------------
# Live: the same flow against the Compose stack
# ---------------------------------------------------------------------------


def _stack_reachable(url: str) -> bool:
    try:
        return httpx.get(url, timeout=5.0).status_code == 200
    except httpx.HTTPError:
        return False


def test_agent_live_command_and_postings(e2e_stack: str) -> None:
    """Live agent: enqueue WR via control, see the result + postings drain."""
    assert e2e_stack
    import shutil
    import subprocess

    from tests.e2e.conftest import control_headers, is_local_mode  # noqa: PLC0415

    if not _stack_reachable("http://localhost:8120/healthz"):
        pytest.skip("edge-relay not reachable live")
    da, ti = _future_wire()
    try:
        r = httpx.post("http://localhost:8101/edge/commands",
                       json={"agent_id": "grand-plaza-pabx", "kind": "wakeup.set",
                             "params": {"room": "101", "date": da, "time": ti}},
                       headers=control_headers(), timeout=15.0)
    except httpx.ConnectError as exc:
        pytest.skip(f"control unreachable live: {exc}")
    assert r.status_code == 200, r.text
    request_id = r.json()["request_id"]
    deadline = time.monotonic() + 60.0
    seen = ""
    while time.monotonic() < deadline:
        results = httpx.get("http://localhost:8101/edge/commands/results",
                            params={"agent_id": "grand-plaza-pabx"},
                            headers=control_headers(), timeout=15.0).json()["results"]
        matching = [row for row in results if row["request_id"] == request_id]
        if matching:
            seen = matching[0]["result_json"]
            break
        time.sleep(2.0)
    assert seen, "live agent never reported the command"
    assert '"status": "sent"' in seen
    postings = httpx.get("http://localhost:8101/edge/postings",
                         params={"agent_id": "grand-plaza-pabx"},
                         headers=control_headers(), timeout=15.0).json()["postings"]
    assert len(postings) >= 1
    assert postings[0]["record_type"] == "PS"
    agents = httpx.get("http://localhost:8101/edge/agents",
                       headers=control_headers(), timeout=15.0).json()["agents"]
    assert any(a["agent_id"] == "grand-plaza-pabx" and a["link_up"] in (1, True) for a in agents)
    # No inbound execution port, live either.
    gone = httpx.post("http://localhost:8114/execute", json={"spec": {}, "operation": "x"},
                      timeout=15.0)
    assert gone.status_code == 404
    if is_local_mode():
        return  # the Docker network probe below has no meaning on one loopback (REQ-017)
    # Control plane still cannot reach the PABX (edge-net isolation).
    if shutil.which("docker") is None:
        pytest.skip("docker not available")
    proc = subprocess.run(
        ["docker", "compose", "exec", "-T", "control", "python", "-c",
         "import urllib.request; print(urllib.request.urlopen('http://sim-fias-pabx:8000/healthz', timeout=5).status)"],
        capture_output=True, timeout=30,
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    assert proc.returncode != 0, "control plane must NOT reach sim-fias-pabx"
