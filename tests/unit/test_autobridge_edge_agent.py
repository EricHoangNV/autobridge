"""REQ-022 B — edge agent units: codec, store, bundle, poller steps.

Link tests run the host engine against the real ``PabxLink`` simulator over
loopback TCP (allowed in tests; the engine itself never imports it).
"""

from __future__ import annotations

import datetime
import threading
import time
from typing import Any

import pytest

from autobridge.edge.bundle import BundleError, load_bundle, render_command, verify_bundle
from autobridge.edge.fias_host import (
    ETX,
    STX,
    FiasHost,
    FrameSplitter,
    OversizeFrameError,
    Record,
    _is_expired,
    encode_frame,
    parse_frame,
)
from autobridge.edge.store import AgentStore, posting_key

# ---------------------------------------------------------------------------
# Codec
# ---------------------------------------------------------------------------


def test_codec_roundtrip() -> None:
    frame = encode_frame("PS", [("RN", "101"), ("P#", "000417"), ("TA", "1250")])
    assert frame[0] == STX and frame[-1] == ETX
    rec = parse_frame(frame[1:-1])
    assert rec == Record("PS", (("RN", "101"), ("P#", "000417"), ("TA", "1250")))
    assert rec.field("RN") == "101"
    assert rec.as_dict()["TA"] == "1250"


def test_codec_rejects_wire_unsafe() -> None:
    import pytest as _pytest

    with _pytest.raises(ValueError):
        encode_frame("PSS", [("RN", "101")])
    with _pytest.raises(ValueError):
        encode_frame("PS", [("RNN", "101")])
    with _pytest.raises(ValueError):
        encode_frame("PS", [("RN", "10|1")])
    with _pytest.raises(ValueError):
        parse_frame(b"no-separator")
    with _pytest.raises(ValueError):
        parse_frame(b"TOOLONG|RN101|")


def test_splitter_coalesced_split_and_zeros() -> None:
    splitter = FrameSplitter()
    f1 = encode_frame("LA", [("DA", "261028")])
    f2 = encode_frame("LS", [("DA", "261028")])
    items = splitter.feed(f1 + f2)
    assert [i.kind for i in items] == ["frame", "frame"]
    assert items[0].record is not None and items[0].record.record_type == "LA"
    # Split across feeds.
    splitter2 = FrameSplitter()
    assert splitter2.feed(f1[:4]) == []
    items = splitter2.feed(f1[4:])
    assert len(items) == 1 and items[0].kind == "frame"
    # Zero run becomes one item once non-zero bytes end it.
    splitter3 = FrameSplitter()
    assert splitter3.feed(b"\x00\x00\x00") == []
    items = splitter3.feed(b"\x00" + f1)
    assert items[0].kind == "zeros"
    assert items[1].kind == "frame"
    # Trailing zeros flush at end of data.
    splitter4 = FrameSplitter()
    assert splitter4.feed(b"\x00\x00") == []
    assert splitter4.flush()[0].kind == "zeros"


def test_splitter_oversize_and_bad() -> None:
    splitter = FrameSplitter(max_frame_len=8)
    with pytest.raises(OversizeFrameError):
        splitter.feed(bytes((STX,)) + b"1" * 16 + bytes((ETX,)))
    splitter2 = FrameSplitter()
    items = splitter2.feed(bytes((STX,)) + b"???" + bytes((ETX,)))
    assert items and items[0].kind == "bad"


def test_is_expired() -> None:
    assert _is_expired(None) is False
    assert _is_expired("") is False
    assert _is_expired("not-a-date") is False
    past = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)).isoformat()
    future = (datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1)).isoformat()
    assert _is_expired(past) is True
    assert _is_expired(future) is False


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: object) -> Any:
    from pathlib import Path

    db = AgentStore(str(Path(str(tmp_path)) / "agent.db"))
    yield db
    db.close()


def _cmd(request_id: str = "r1", kind: str = "wakeup.set") -> dict[str, Any]:
    return {"request_id": request_id, "kind": kind, "params": {"room": "101"},
            "idempotency_key": f"k-{request_id}", "expires_at": None}


def test_store_outbox_claim_finish(store: AgentStore) -> None:
    cmd = _cmd()
    assert store.enqueue_command(**cmd) is True
    assert store.enqueue_command(**cmd) is False  # re-delivery ignored
    claimed = store.next_command()
    assert claimed is not None and claimed["request_id"] == "r1"
    assert claimed["attempts"] == 1
    assert store.next_command() is None
    store.finish_command("r1", "sent", "link ACK")
    assert store.command_result("r1") == {"status": "sent", "detail": "link ACK"}
    assert store.depths() == {"outbox": 0, "inbox": 0}


def test_store_requeue_and_restart_recovery(tmp_path: object) -> None:
    from pathlib import Path

    path = str(Path(str(tmp_path)) / "agent.db")
    db = AgentStore(path)
    db.enqueue_command(**_cmd())
    assert db.next_command() is not None  # claimed, no result
    db.close()
    db2 = AgentStore(path)  # restart: claimed returns to queued
    try:
        assert db2.next_command() is not None
    finally:
        db2.close()


def test_store_requeue_command(store: AgentStore) -> None:
    store.enqueue_command(**_cmd())
    assert store.next_command() is not None
    store.requeue_command("r1")
    assert store.next_command() is not None


def test_store_inbox_dedup_and_forward(store: AgentStore) -> None:
    key, fresh = store.store_posting("PS", {"P#": "000001", "RN": "101", "DA": "d", "TI": "t"})
    assert fresh is True
    key2, fresh2 = store.store_posting("PS", {"P#": "000001", "RN": "101", "DA": "d", "TI": "t"})
    assert (key2, fresh2) == (key, False)
    assert posting_key("PS", {"P#": "1", "RN": "2", "DA": "3", "TI": "4"}) == "PS:1:2:3:4"
    assert posting_key("RE", {"RN": "101"}) != posting_key("RE", {"RN": "102"})
    row = store.next_unforwarded()
    assert row is not None and row["idempotency_key"] == key
    store.mark_forwarded(key)
    assert store.next_unforwarded() is None
    assert store.depths() == {"outbox": 0, "inbox": 0}


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------


def test_bundle_loads_and_renders_wakeup() -> None:
    bundle = load_bundle()
    assert bundle["version"] == 1
    record_type, fields, expect = render_command(
        bundle, "wakeup.set", {"room": "101", "date": "261028", "time": "070000"})
    assert record_type == "WR"
    assert ("RN", "101") in fields and expect == "ack"


def test_bundle_now_and_defaults_fill() -> None:
    bundle = load_bundle()
    record_type, fields, _ = render_command(bundle, "guest.in", {
        "room": "101", "reservation": "R1", "guest": "Doe,Jane",
        "arrival": "261028", "departure": "261030"})
    assert record_type == "GI"
    by_id = dict(fields)
    assert by_id["GL"] == "en" and by_id["CS"] == "3"  # defaults
    assert len(by_id["DA"]) == 6 and len(by_id["TI"]) == 6  # now fill


def test_bundle_rejects() -> None:
    bundle = load_bundle()
    with pytest.raises(BundleError):
        render_command(bundle, "nope", {})
    with pytest.raises(BundleError):  # missing placeholder
        render_command(bundle, "wakeup.set", {"room": "101", "date": "261028"})
    with pytest.raises(BundleError):  # wire-unsafe value
        render_command(bundle, "wakeup.set", {"room": "10|1", "date": "261028", "time": "070000"})
    with pytest.raises(BundleError):  # unrendered brace
        render_command(bundle, "wakeup.set", {"room": "10{1}", "date": "261028", "time": "070000"})


def test_bundle_verify_dev_and_pinned(monkeypatch: Any) -> None:
    bundle = load_bundle()
    monkeypatch.delenv("EDGE_RELEASE_SECRET", raising=False)
    monkeypatch.delenv("EDGE_BUNDLE_SIGNATURE", raising=False)
    assert verify_bundle(bundle) == bundle  # dev mode: unsigned accepted
    monkeypatch.setenv("EDGE_RELEASE_SECRET", "test-secret")
    with pytest.raises(BundleError, match="unsigned"):
        verify_bundle(bundle)  # keyed deployment must pin
    import json

    from autobridge.runtime.targets import release_signature

    sig = release_signature(json.loads(json.dumps(bundle)))
    monkeypatch.setenv("EDGE_BUNDLE_SIGNATURE", sig)
    assert verify_bundle(bundle) == bundle
    monkeypatch.setenv("EDGE_BUNDLE_SIGNATURE", "bogus")
    with pytest.raises(BundleError, match="mismatch"):
        verify_bundle(bundle)


# ---------------------------------------------------------------------------
# Poller steps (real relay over ASGI, fake link)
# ---------------------------------------------------------------------------


class _FakeLink:
    """Scripted link: {(record_type): (outcome, answer)}."""

    def __init__(self, script: dict[str, tuple[str, Any]] | None = None) -> None:
        self.script = script or {}
        self.sent: list[tuple[str, list[tuple[str, str]], str]] = []
        self.up = True

    def send_command(self, record_type: str, fields: list[tuple[str, str]],
                     expect: str = "ack", timeout: float | None = None,
                     expires_at: str | None = None) -> tuple[str, Any]:
        _ = (timeout, expires_at)
        self.sent.append((record_type, fields, expect))
        return self.script.get(record_type, ("sent", None))

    def link_state(self) -> dict[str, Any]:
        return {"link_up": self.up, "links_accepted": 1, "links_dropped": 0}


def _agent_with_relay(tmp_path: object, script: dict[str, tuple[str, Any]] | None = None,
                      **kw: Any) -> Any:
    """Build (Agent, store, relay TestClient) sharing scratch SQLite files."""
    from pathlib import Path

    from fastapi.testclient import TestClient

    from autobridge.edge.poller import Agent, RelayClient
    from autobridge.edge.relay import _Store, create_app

    store = AgentStore(str(Path(str(tmp_path)) / "agent.db"))
    relay_app = create_app(_Store(str(Path(str(tmp_path)) / "relay.db")))
    relay_test = TestClient(relay_app)
    relay = RelayClient("http://relay", session=relay_test)
    agent = Agent("test-agent", store, relay, load_bundle(), _FakeLink(script),
                  max_attempts=kw.get("max_attempts", 2))
    return agent, store, relay_test


def test_poller_execute_sent_roundtrip(tmp_path: object) -> None:
    agent, store, client = _agent_with_relay(tmp_path)
    # Enqueue on the relay directly, then drive the agent steps.
    r = client.post("/v1/enqueue", json={"agent_id": "test-agent", "kind": "wakeup.set",
                                         "params": {"room": "101", "date": "261028", "time": "070000"}})
    request_id = r.json()["request_id"]
    assert agent.poll_once() == "stored"
    assert agent.execute_once() == "sent"
    assert store.command_result(request_id) == {"status": "sent", "detail": "link ACK"}
    results = client.get("/v1/results", params={"agent_id": "test-agent"}).json()["results"]
    assert len(results) == 1
    assert agent.link.sent[0][0] == "WR"  # type: ignore[attr-defined]
    store.close()


def test_poller_expired_never_sent(tmp_path: object) -> None:
    agent, store, client = _agent_with_relay(tmp_path)
    import datetime

    past = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)).isoformat()
    r = client.post("/v1/enqueue", json={"agent_id": "test-agent", "kind": "wakeup.set",
                                         "params": {"room": "101", "date": "261028", "time": "070000"},
                                         "expires_at": past})
    request_id = r.json()["request_id"]
    assert agent.poll_once() == "stored"
    assert agent.execute_once() == "expired"
    assert agent.link.sent == []  # type: ignore[attr-defined]
    assert store.command_result(request_id)["status"] == "expired"
    store.close()


def test_poller_rejected_unknown_kind(tmp_path: object) -> None:
    agent, store, client = _agent_with_relay(tmp_path)
    r = client.post("/v1/enqueue", json={"agent_id": "test-agent", "kind": "nope", "params": {}})
    request_id = r.json()["request_id"]
    assert agent.poll_once() == "stored"
    assert agent.execute_once() == "rejected"
    assert agent.link.sent == []  # type: ignore[attr-defined]
    assert store.command_result(request_id)["status"] == "rejected"
    store.close()


def test_poller_link_down_requeues_then_fails(tmp_path: object) -> None:
    agent, store, client = _agent_with_relay(tmp_path, {"WR": ("link_down", None)}, max_attempts=1)
    client.post("/v1/enqueue", json={"agent_id": "test-agent", "kind": "wakeup.set",
                                     "params": {"room": "101", "date": "261028", "time": "070000"}})
    assert agent.poll_once() == "stored"
    assert agent.execute_once() == "link_down"  # attempt 1: requeued
    assert agent.execute_once() == "failed"  # attempt 2 exceeds max: reported failed
    import json

    results = client.get("/v1/results", params={"agent_id": "test-agent"}).json()["results"]
    assert [json.loads(r["result_json"])["status"] for r in results] == ["failed"]
    store.close()


def test_poller_forward_and_heartbeat(tmp_path: object) -> None:
    agent, store, client = _agent_with_relay(tmp_path)
    assert agent.forward_once() == "idle"
    key, _ = store.store_posting("PS", {"P#": "1", "RN": "101", "DA": "d", "TI": "t"})
    assert agent.forward_once() == "forwarded"
    assert agent.forward_once() == "idle"
    postings = client.get("/v1/postings", params={"agent_id": "test-agent"}).json()["postings"]
    assert [p["idempotency_key"] for p in postings] == [key]
    assert agent.heartbeat_once() is True
    agents = client.get("/v1/agents").json()["agents"]
    assert agents[0]["agent_id"] == "test-agent"
    store.close()


def test_poller_empty_and_relay_down() -> None:
    from autobridge.edge.poller import RelayClient

    relay = RelayClient("http://127.0.0.1:9", timeout=0.5)  # nothing listens: refused fast
    assert relay.next_command("a") is None
    assert relay.last_contact_ok is False


# ---------------------------------------------------------------------------
# Link engine against the real PABX simulator (loopback TCP)
# ---------------------------------------------------------------------------


class _RecordingSink:
    def __init__(self, store: AgentStore) -> None:
        self.store = store
        self.records: list[Record] = []

    def on_record(self, record: Record) -> str:
        self.records.append(record)
        self.store.store_posting(record.record_type, record.as_dict())
        return "OK"


@pytest.fixture()
def linked(tmp_path: object) -> Any:
    """Agent host + real PabxLink over loopback; yields (host, pabx, sink, store)."""
    from pathlib import Path

    from simulators.fias_pabx.pabx import PabxConfig, PabxLink

    store = AgentStore(str(Path(str(tmp_path)) / "agent.db"))
    sink = _RecordingSink(store)
    host = FiasHost(sink, listen_host="127.0.0.1", listen_port=0,
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
    yield host, pabx, sink, store
    pabx.stop()
    host.stop()
    store.close()


def test_link_handshake_announces_layouts(linked: Any) -> None:
    host, pabx, _, _ = linked
    state = host.link_state()
    assert state["link_up"] is True
    assert state["peer_layouts"]["PS"] == ["RN", "P#", "DA", "TI", "TA", "DD", "DU", "SO"]
    assert pabx.links_established >= 1


def test_link_second_connection_refused_while_up(linked: Any) -> None:
    import socket as _socket

    host, _, _, _ = linked
    port = host.bound_port()
    probe = _socket.create_connection(("127.0.0.1", port), timeout=5.0)
    try:
        probe.settimeout(5.0)
        assert probe.recv(16) == b""  # closed immediately: one link at a time
    finally:
        probe.close()


def test_link_postings_flow_to_sink(linked: Any) -> None:
    host, pabx, sink, store = linked
    deadline = time.monotonic() + 10.0
    while True:
        snap = pabx.snapshot()
        if snap["counters"]["pa_ok"] >= 6:
            break
        assert time.monotonic() < deadline, f"postings never PA'd: {snap['counters']}"
        time.sleep(0.05)
    assert len([r for r in sink.records if r.record_type == "PS"]) >= 6
    assert store.depths()["inbox"] >= 6
    keys = [k for k, r in ((posting_key(r.record_type, r.as_dict()), r) for r in sink.records)]
    assert len(set(keys)) == len(keys)  # every posting has a distinct idempotency key


def _future_wire() -> tuple[str, str]:
    from zoneinfo import ZoneInfo

    now = datetime.datetime.now(ZoneInfo("America/New_York")) + datetime.timedelta(days=2)
    return now.strftime("%y%m%d"), now.strftime("%H%M00")


def test_link_command_wr_sent(linked: Any) -> None:
    host, pabx, _, _ = linked
    da, ti = _future_wire()
    outcome, _ = host.send_command("WR", [("RN", "101"), ("DA", da), ("TI", ti)], "ack", timeout=10.0)
    assert outcome == "sent"
    assert pabx.snapshot()["scheduled_wakeups"] == 1


def test_link_command_kr_answered_ka(linked: Any) -> None:
    host, _, _, _ = linked
    da, ti = _future_wire()
    outcome, answer = host.send_command(
        "KR",
        [("KC", "01"), ("RN", "101"), ("K#", "1"), ("GA", da), ("GD", da),
         ("G#", "RES-1"), ("DA", da), ("TI", ti)],
        "KA", timeout=10.0)
    assert outcome == "sent"
    assert answer is not None and answer.record_type == "KA"


def test_link_command_ack_timeout_link_stays_up(linked: Any) -> None:
    host, pabx, _, _ = linked
    with pabx.lock:
        pabx.faults.silent_types = ("WR",)
    try:
        da, ti = _future_wire()
        # The outer wait (10 s) exceeds the link ACK wait (2 s), so the
        # link itself reports the timeout and the session survives it.
        outcome, _ = host.send_command("WR", [("RN", "101"), ("DA", da), ("TI", ti)],
                                       "ack", timeout=10.0)
        assert outcome == "ack_timeout"
        assert host.link_state()["link_up"] is True  # silence != dead link
    finally:
        with pabx.lock:
            pabx.faults.silent_types = ()


def test_link_command_expired_never_sent(linked: Any) -> None:
    host, pabx, _, _ = linked
    past = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)).isoformat()
    before = pabx.snapshot()["scheduled_wakeups"]
    outcome, _ = host.send_command("WR", [("RN", "101"), ("DA", "261028"), ("TI", "070000")],
                                   "ack", timeout=10.0, expires_at=past)
    assert outcome == "expired"
    assert pabx.snapshot()["scheduled_wakeups"] == before


def _run_serve(host: FiasHost, sock: Any) -> threading.Thread:
    from autobridge.edge.fias_host import FrameSplitter as _Splitter

    thread = threading.Thread(target=host._serve, args=(sock, _Splitter(host._max_frame_len)),
                              daemon=True)
    thread.start()
    return thread


def _drainable_sink() -> _RecordingSink:
    import tempfile

    return _RecordingSink(AgentStore(tempfile.mktemp(suffix=".db")))


def test_serve_closes_oversize_session() -> None:
    import socket as _socket

    sink = _drainable_sink()
    host = FiasHost(sink, listen_host="127.0.0.1", listen_port=0, max_frame_len=64,
                    keepalive_interval=100.0, dead_after=1000.0)
    a, b = _socket.socketpair()
    try:
        thread = _run_serve(host, a)
        b.sendall(bytes((STX,)) + b"X" * 200 + bytes((ETX,)))
        thread.join(timeout=5.0)
        assert not thread.is_alive()
        assert host.counters["oversize_closed"] == 1
    finally:
        a.close()
        b.close()
        sink.store.close()


def test_serve_closes_identical_flood() -> None:
    import socket as _socket

    sink = _drainable_sink()
    host = FiasHost(sink, listen_host="127.0.0.1", listen_port=0, max_identical=3,
                    keepalive_interval=100.0, dead_after=1000.0)
    a, b = _socket.socketpair()
    try:
        thread = _run_serve(host, a)
        frame = encode_frame("LA", [("DA", "261028"), ("TI", "120000")])
        for _ in range(5):
            b.sendall(frame)
            time.sleep(0.05)
        thread.join(timeout=5.0)
        assert not thread.is_alive()
        assert host.counters["flood_closed"] == 1
    finally:
        a.close()
        b.close()
        sink.store.close()


def test_serve_zeros_resync_and_le_close() -> None:
    import socket as _socket

    sink = _drainable_sink()
    host = FiasHost(sink, listen_host="127.0.0.1", listen_port=0,
                    keepalive_interval=100.0, dead_after=1000.0)
    a, b = _socket.socketpair()
    try:
        thread = _run_serve(host, a)
        b.sendall(b"\x00\x00\x00" + encode_frame("LA", [("DA", "261028")]))
        thread.join(timeout=5.0)
        assert not thread.is_alive()
        assert host.counters["resyncs"] == 1
    finally:
        a.close()
        b.close()
    # A fresh session: LE from the peer closes cleanly (nightly restart).
    a2, b2 = _socket.socketpair()
    try:
        thread2 = _run_serve(host, a2)
        b2.sendall(encode_frame("LE", [("DA", "261028")]))
        thread2.join(timeout=5.0)
        assert not thread2.is_alive()
    finally:
        a2.close()
        b2.close()
        sink.store.close()


def test_host_send_and_wait_ack_survives_idle_poll_tick() -> None:
    """Host wait retries on an idle recv tick (never a premature resend)."""
    import socket as _socket
    import threading

    from autobridge.edge.fias_host import ACK, FrameSplitter

    sink = _drainable_sink()
    host = FiasHost(sink, listen_host="127.0.0.1", listen_port=0,
                    keepalive_interval=100.0, dead_after=1000.0)
    a, b = _socket.socketpair()
    try:
        a.settimeout(0.2)
        b.settimeout(5.0)

        def _late_ack() -> None:
            data = b.recv(65536)
            assert data.startswith(bytes((STX,)))
            time.sleep(0.45)  # past one 0.2 s recv tick, inside ack_wait
            b.sendall(bytes((ACK,)))

        peer = threading.Thread(target=_late_ack, daemon=True)
        peer.start()
        before = host.counters["frames_out"]
        from collections import deque

        host._send_and_wait_ack(a, FrameSplitter(), deque(), "LA", [("DA", "261028")])
        peer.join(timeout=5.0)
        assert not peer.is_alive()
        assert host.counters["frames_out"] == before + 1  # sent once, never resent
        assert host.counters["acks_in"] == 1
    finally:
        a.close()
        b.close()
        sink.store.close()
