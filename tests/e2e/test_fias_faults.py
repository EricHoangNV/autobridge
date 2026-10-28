"""REQ-022 sub-step D — FIAS fault scenarios (YAML + verifier check + e2e).

Each scenario in ``simulators/scenarios/fias_*.yaml`` (+ ``agent_*``,
``cloud_slow``, ``session_*``, ``stale_*``) is graded here against the
real pieces over loopback TCP (real ``PabxLink`` peer, real ``FiasHost``,
real relay app, real agent store) and, when the stack is up, against the
live services. The link-layer guarantees are graded by
``verifier/fias_checks.py`` (the HTTP oracle fails these scenarios closed
by design); the layout-change repair additionally climbs the real ladder
at L0/L1 with 0 frontier tokens.

Trust boundary: this file lives in tests/e2e and may import the engine +
simulators for verification, but the engine itself never imports
verifier/simulators.
"""

from __future__ import annotations

import datetime
import json
import time
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient

from autobridge.edge.bundle import load_bundle
from autobridge.edge.fias_host import FiasHost
from autobridge.edge.poller import Agent, RelayClient
from autobridge.edge.store import AgentStore

if TYPE_CHECKING:
    from pathlib import Path

RELAY_URL = "http://relay"


# ---------------------------------------------------------------------------
# Fixtures (real pieces, loopback TCP, scratch SQLite)
# ---------------------------------------------------------------------------


class _Sink:
    """Durable-write boundary: store the record, then the host ACKs."""

    def __init__(self, store: AgentStore) -> None:
        self.store = store
        self.records: list[Any] = []

    def on_record(self, record: Any) -> str:
        self.records.append(record)
        self.store.store_posting(record.record_type, record.as_dict())
        return "OK"


@pytest.fixture()
def relay_pair(tmp_path: Path, monkeypatch: Any) -> tuple[TestClient, RelayClient]:
    """Real relay app + an agent-side client talking to it in-process."""
    import httpx

    from autobridge.edge.relay import _Store, create_app
    from tests.e2e.conftest import agent_token  # noqa: PLC0415

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
    return inner, RelayClient(RELAY_URL, agent_token=agent_token(), session=inner)


@pytest.fixture()
def link_env(tmp_path: Path) -> Any:
    """Real agent host + real PabxLink peer over loopback TCP."""
    import httpx  # noqa: F401  (kept for symmetry with relay_pair)

    from simulators.fias_pabx.pabx import PabxConfig, PabxLink

    store = AgentStore(str(tmp_path / "agent.db"))
    sink = _Sink(store)
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


def _wait_for(pred: Any, timeout: float = 15.0, what: str = "condition") -> Any:
    deadline = time.monotonic() + timeout
    while True:
        value = pred()
        if value:
            return value
        assert time.monotonic() < deadline, f"{what} never became true"
        time.sleep(0.1)


def _future_wire() -> tuple[str, str]:
    from zoneinfo import ZoneInfo

    now = datetime.datetime.now(ZoneInfo("America/New_York")) + datetime.timedelta(days=2)
    return now.strftime("%y%m%d"), now.strftime("%H%M00")


def _results_for(inner: TestClient, agent_id: str) -> list[dict[str, Any]]:
    from tests.e2e.conftest import control_headers  # noqa: PLC0415

    return inner.get("/v1/results", params={"agent_id": agent_id},
                     headers=control_headers()).json()["results"]


def _result_statuses(inner: TestClient, agent_id: str, request_id: str) -> list[str]:
    return [
        json.loads(r["result_json"])["status"]
        for r in _results_for(inner, agent_id)
        if r["request_id"] == request_id
    ]


def _relay_postings(inner: TestClient, agent_id: str) -> list[dict[str, Any]]:
    from tests.e2e.conftest import control_headers  # noqa: PLC0415

    return inner.get("/v1/postings", params={"agent_id": agent_id},
                     headers=control_headers()).json()["postings"]


def _drain_forward(agent: Agent, limit: int = 500) -> int:
    """Forward until idle; returns forwarded count (bounded)."""
    count = 0
    for _ in range(limit):
        if agent.forward_once() != "forwarded":
            break
        count += 1
    return count


def _enqueue(inner: TestClient, agent_id: str, kind: str = "wakeup.set",
             params: dict[str, Any] | None = None,
             expires_at: str | None = None) -> str:
    from tests.e2e.conftest import control_headers  # noqa: PLC0415

    body: dict[str, Any] = {"agent_id": agent_id, "kind": kind, "params": params or {}}
    if expires_at is not None:
        body["expires_at"] = expires_at
    r = inner.post("/v1/enqueue", json=body, headers=control_headers())
    assert r.status_code == 200, r.text
    return r.json()["request_id"]


# ---------------------------------------------------------------------------
# Layout change: detect from the handshake, repair through the ladder
# ---------------------------------------------------------------------------


def _discovered_spec_dict() -> dict[str, Any]:
    import pathlib

    from autobridge.discovery.bootstrap import build_draft_spec

    repo = pathlib.Path(__file__).resolve().parents[2]
    md = (repo / "simulators" / "docs" / "fias-pabx.md").read_text(encoding="utf-8")
    result = build_draft_spec(md, source="simulators/docs/fias-pabx.md",
                              connector_id="fiaspabx", spec_vocabulary="v3")
    assert result.success, f"visible_errors={result.visible_errors}"
    return dict(result.draft_spec)


def _feed_records(layout: list[str], tm: bool = False) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """One PS shaped to *layout* (dict order irrelevant) plus a baseline RE."""
    base = {"RN": "101", "P#": "000417", "DA": "261028", "TI": "231504",
            "TA": "1250", "TM": "1250", "DD": "004930", "DU": "000312",
            "SO": "TEL", "CX": "VOICE"}
    if tm:
        base.pop("TA")
    ps = [{fid: base[fid] for fid in layout if fid in base}]
    re_ = [{"RN": "101", "RS": "3", "DA": "261028", "TI": "180000"}]
    return ps, re_


def _mixed_v3_samples(layout: list[str]) -> list[dict[str, Any]]:
    """Call + minibar postings in the v3 shape (a real inbox mixes both).

    The mix matters: dialled-digits/duration are digit strings on calls but
    empty on minibar postings, so only the renamed amount id is
    money-shaped across every sample.
    """
    call = {"RN": "101", "P#": "000417", "DA": "261028", "TI": "231504",
            "TM": "1250", "DD": "004930", "DU": "000312", "SO": "TEL", "CX": "VOICE"}
    mini = {"RN": "102", "P#": "000418", "DA": "261028", "TI": "232000",
            "TM": "450", "DD": "", "DU": "", "SO": "MINI", "CX": "SNACK"}
    return [{fid: rec[fid] for fid in layout if fid in rec} for rec in (call, mini)]


class _ScriptFeed:
    def __init__(self, ps: list[dict[str, Any]], re_: list[dict[str, Any]] | None = None) -> None:
        self._by_record = {"PS": ps, "RE": re_ or []}

    def read(self, feed: Any, operation_name: str) -> list[dict[str, Any]]:
        rec = feed["record"] if isinstance(feed, dict) else feed.record
        records = self._by_record.get(rec)
        if records is None:
            raise KeyError(f"unknown feed {rec!r} for {operation_name!r}")
        return [dict(r) for r in records]


def test_layout_change_repair_zero_frontier(link_env: Any) -> None:
    """fias_record_layout_change: handshake LR → ladder L1 → Complete, 0 calls."""
    from autobridge.drift.classifier import classify  # noqa: PLC0415
    from autobridge.drift.event_feeds import detect_feed_field_rename  # noqa: PLC0415
    from autobridge.drift.signals import DriftSignal  # noqa: PLC0415
    from autobridge.engine.sync_run import execute_sync  # noqa: PLC0415
    from autobridge.repair.controller import RepairController  # noqa: PLC0415
    from autobridge.spec.patch import apply_patch  # noqa: PLC0415
    from simulators.fias_pabx.pabx import LAYOUT_V2_PS, LAYOUT_V3_PS  # noqa: PLC0415

    host, pabx, sink, store = link_env

    # Baseline: the announced layout matches the discovered mapping.
    announced_before = list(host.link_state()["peer_layouts"]["PS"])
    assert len(announced_before) == 8
    spec = _discovered_spec_dict()
    out = execute_sync(spec, None, feed_reader=_ScriptFeed(*_feed_records(announced_before)))
    assert out["outcome"] == "Complete", out.get("unmet_criteria")
    amount_path = spec["entities"]["FolioCharge"]["fields"]["amount"]["path"]
    old_wire = str(amount_path).removeprefix("$.")

    # Firmware update on the wire: the PABX reconnects announcing layout v3.
    with pabx.lock:
        pabx.faults.ps_layout = "layout_v3"
        pabx.faults.drop_link = True
    _wait_for(lambda: host.link_state()["links_accepted"] >= 2,
              timeout=15.0, what="PABX redial after layout change")
    announced = list(host.link_state()["peer_layouts"]["PS"])
    assert announced == list(LAYOUT_V3_PS), announced
    assert announced != list(LAYOUT_V2_PS)

    # The new shape does NOT fail the run — it corrupts it silently: the
    # amount path reads nothing, so charges land worth None while the run
    # reports Complete. That is exactly why the drift must be detected from
    # the handshake announcement, not from the sync outcome.
    out = execute_sync(spec, None, feed_reader=_ScriptFeed(*_feed_records(announced, tm=True)))
    assert out["outcome"] == "Complete", out.get("unmet_criteria")
    assert out["canonical_records"]["FolioCharge"][0]["amount"] is None

    # Detect from the handshake announcement (real data, not the YAML).
    samples = _mixed_v3_samples(announced)
    drift = detect_feed_field_rename(spec, "PS", announced, samples)
    assert drift["drift_found"] is True
    assert drift["missing_wire"] == [old_wire]
    assert drift["rename"] is not None and drift["rename"]["new_path"] == "$.TM"
    assert drift["delta"] is not None

    signal = DriftSignal(kind="types_changed", entity="FolioCharge", severity="medium",
                         message="PS layout drift from handshake LR",
                         details={"missing_wire": drift["missing_wire"],
                                  "added_wire": drift["added_wire"],
                                  "announced_layout": drift["announced_ids"]},
                         fingerprint_delta=drift["delta"])
    classification = classify([signal])
    assert classification.drift_class == "SCHEMA_RENAME", classification

    controller = RepairController()
    result = controller.handle(spec=spec, drift_class=classification.drift_class,
                               signals=[signal], fingerprint_delta=drift["delta"],
                               extra=drift["extra"])
    assert result.decision == "propose", result.escalation_reason
    assert result.rule_id == "rename_field"
    assert result.candidate_patch == [
        {"op": "replace", "path": "/entities/FolioCharge/fields/amount/path", "value": "$.TM"}
    ]
    assert [t["level"] for t in result.ladder_trace] == [0, 1]
    assert result.model_usage.get("frontier_tokens", 0) == 0
    assert result.model_usage.get("small_tokens", 0) == 0
    assert result.model_usage.get("calls", 0) == 0

    # The repaired mapping syncs the new shape Complete, amounts exact.
    new_spec = apply_patch(spec, result.candidate_patch)
    out = execute_sync(new_spec, None, feed_reader=_ScriptFeed(*_feed_records(announced, tm=True)))
    assert out["outcome"] == "Complete", out.get("unmet_criteria")
    from decimal import Decimal  # noqa: PLC0415

    assert out["canonical_records"]["FolioCharge"][0]["amount"] == Decimal("12.50")

    # The verifier's link-layer layout check is clean after the repair.
    from verifier.fias_checks import grade_fias_scenario  # noqa: PLC0415

    mapped = [p.removeprefix("$.") for p in
              [f["path"] for f in new_spec["entities"]["FolioCharge"]["fields"].values()
               if isinstance(f, dict) and isinstance(f.get("path"), str)]]
    assert grade_fias_scenario("fias_record_layout_change",
                               {"mapped_wire_ids": mapped, "announced_ids": announced}) == []


# ---------------------------------------------------------------------------
# In-process: reliability faults over the real link
# ---------------------------------------------------------------------------


def _unique_ps_pnos(sink: _Sink) -> set[str]:
    return {r.field("P#") for r in sink.records if r.record_type == "PS"}


def _drain_and_grade(inner: TestClient, agent: Agent, sink: _Sink, agent_id: str,
                     scenario_id: str) -> list[dict[str, Any]]:
    """Forward everything to the relay and grade delivery (no loss, no dupes)."""
    from autobridge.edge.store import posting_key  # noqa: PLC0415
    from verifier.fias_checks import grade_fias_scenario  # noqa: PLC0415

    _drain_forward(agent)
    produced = [p["idempotency_key"] for p in _relay_postings(inner, agent_id)]
    expected = [posting_key("PS", r.as_dict()) for r in sink.records
                if r.record_type == "PS"]
    # expected collapses to unique keys (each charge once); produced must match.
    return grade_fias_scenario(scenario_id, {"expected_keys": sorted(set(expected)),
                                             "produced_keys": produced})


def _wait_quiet(pabx: Any, timeout: float = 20.0) -> None:
    """Wait until the PABX has nothing left to send or resend."""
    _wait_for(lambda: (pabx.snapshot()["pending_postings"] == 0
                       and pabx.snapshot()["unacked"] == []),
              timeout=timeout, what="link to quiesce")


def test_link_resync_no_loss(relay_pair: Any, link_env: Any) -> None:
    """fias_link_resync: zeros mid-session → re-handshake, nothing lost."""
    inner, relay = relay_pair
    host, pabx, sink, store = link_env
    agent = Agent("test-agent", store, relay, load_bundle(), host, command_timeout=6.0)

    _wait_for(lambda: len(sink.records) >= 6, what="stay postings in sink")
    with pabx.lock:
        pabx.faults.send_zeros_then_drop = True
    _wait_for(lambda: host.link_state()["links_accepted"] >= 2,
              timeout=15.0, what="re-handshake after zeros")
    assert host.link_state()["counters"]["resyncs"] >= 1
    _wait_quiet(pabx)
    assert _drain_and_grade(inner, agent, sink, "test-agent", "fias_link_resync") == []


def test_ack_timeout_reported_link_survives(relay_pair: Any, link_env: Any) -> None:
    """fias_ack_timeout: silent WR → cloud told, link up, nothing retried blindly."""
    from verifier.fias_checks import grade_fias_scenario  # noqa: PLC0415

    inner, relay = relay_pair
    host, pabx, sink, store = link_env
    agent = Agent("test-agent", store, relay, load_bundle(), host, command_timeout=6.0)

    with pabx.lock:
        pabx.faults.silent_types = ("WR",)
    try:
        da, ti = _future_wire()
        request_id = _enqueue(inner, "test-agent",
                              params={"room": "101", "date": da, "time": ti})
        assert agent.poll_once() == "stored"
        assert agent.execute_once() == "ack_timeout"
        assert host.link_state()["link_up"] is True
        assert _result_statuses(inner, "test-agent", request_id) == ["ack_timeout"]
        assert grade_fias_scenario(
            "fias_ack_timeout",
            {"reported_outcomes": ["ack_timeout"], "expected_outcome": "ack_timeout",
             "sent_to_pabx": False, "may_send": False}) == []
    finally:
        with pabx.lock:
            pabx.faults.silent_types = ()


def test_restart_inflight_no_loss_no_dupes(relay_pair: Any, tmp_path: Path) -> None:
    """agent_restart_inflight: killed mid-flight → restart runs once, drains all."""
    from simulators.fias_pabx.pabx import PabxConfig, PabxLink  # noqa: PLC0415

    inner, relay = relay_pair
    store = AgentStore(str(tmp_path / "agent.db"))
    port = 15021
    sink1 = _Sink(store)
    host1 = FiasHost(sink1, listen_host="127.0.0.1", listen_port=port,
                     keepalive_interval=30.0, dead_after=120.0, ack_wait=2.0)
    host1.start()
    pabx = PabxLink(PabxConfig(host="127.0.0.1", port=port, reconnect_interval=0.2,
                               keepalive_interval=30.0, dead_after=120.0, ack_wait=2.0,
                               resend_interval=60.0, seed=0))
    pabx.start()
    try:
        _wait_for(lambda: host1.link_state()["link_up"], what="first link")
        agent1 = Agent("test-agent", store, relay, load_bundle(), host1, command_timeout=6.0)
        da, ti = _future_wire()
        request_id = _enqueue(inner, "test-agent", params={"room": "101", "date": da, "time": ti})
        assert agent1.poll_once() == "stored"
        _wait_for(lambda: len(sink1.records) >= 2, what="postings in inbox")
        host1.stop()  # killed with the command queued and postings unforwarded
        assert agent1.execute_once() == "link_down"  # requeued, not reported
        assert _results_for(inner, "test-agent") == []

        sink2 = _Sink(store)
        host2 = FiasHost(sink2, listen_host="127.0.0.1", listen_port=port,
                         keepalive_interval=30.0, dead_after=120.0, ack_wait=2.0)
        host2.start()
        try:
            _wait_for(lambda: host2.link_state()["link_up"], what="second link")
            agent2 = Agent("test-agent", store, relay, load_bundle(), host2, command_timeout=6.0)
            assert agent2.execute_once() == "sent"
            assert _result_statuses(inner, "test-agent", request_id) == ["sent"]
            assert pabx.snapshot()["scheduled_wakeups"] == 1
            merged = _Sink(store)
            merged.records = list(sink1.records) + list(sink2.records)
            _drain_forward(agent2)
            from autobridge.edge.store import posting_key  # noqa: PLC0415
            from verifier.fias_checks import grade_fias_scenario  # noqa: PLC0415

            produced = [p["idempotency_key"] for p in _relay_postings(inner, "test-agent")]
            expected = sorted({posting_key("PS", r.as_dict()) for r in merged.records
                               if r.record_type == "PS"})
            assert grade_fias_scenario("agent_restart_inflight",
                                       {"expected_keys": expected,
                                        "produced_keys": produced}) == []
        finally:
            host2.stop()
    finally:
        pabx.stop()
        store.close()


def test_cloud_slow_link_keeps_ack(link_env: Any, relay_pair: Any, monkeypatch: Any) -> None:
    """cloud_slow: callbacks stall; the PABX keeps getting ACKs, outbox drains later."""
    inner, relay = relay_pair
    host, pabx, sink, store = link_env
    agent = Agent("test-agent", store, relay, load_bundle(), host, command_timeout=6.0)

    _wait_for(lambda: len(sink.records) >= 6, what="stay postings in sink")
    orig = relay.forward_posting
    slow_calls: list[str] = []

    def _slow(agent_id: str, key: str, record_type: str,
              fields: dict[str, str], occurred_at: str | None) -> bool:
        slow_calls.append(key)
        time.sleep(1.5)  # scaled cloud stall (tens of seconds in production)
        return orig(agent_id, key, record_type, fields, occurred_at)

    monkeypatch.setattr(relay, "forward_posting", _slow)
    # Drain in the background while the link must stay up throughout.
    import threading  # noqa: PLC0415

    done: list[int] = []

    def _drain() -> None:
        done.append(_drain_forward(agent))

    worker = threading.Thread(target=_drain, daemon=True)
    worker.start()
    try:
        for _ in range(6):
            assert host.link_state()["link_up"] is True
            time.sleep(1.0)
    finally:
        worker.join(timeout=60.0)
    assert not worker.is_alive()
    assert len(slow_calls) >= 6  # every posting paid the stall, none skipped
    assert _drain_and_grade(inner, agent, sink, "test-agent", "cloud_slow") == []


def test_duplicate_records_land_once(relay_pair: Any, link_env: Any) -> None:
    """fias_duplicate_records: PABX replays sent postings; each charge lands once."""
    inner, relay = relay_pair
    host, pabx, sink, store = link_env
    agent = Agent("test-agent", store, relay, load_bundle(), host, command_timeout=6.0)

    _wait_quiet(pabx)
    frames_before = len(sink.records)
    assert frames_before >= 6
    with pabx.lock:
        pabx.faults.replay_postings = 3
    _wait_for(lambda: len(sink.records) >= frames_before + 3,
              timeout=15.0, what="replayed duplicates in sink")
    dupes = [r for r in sink.records[frames_before:] if r.record_type == "PS"]
    assert {r.field("P#") for r in dupes} <= _unique_ps_pnos(sink)  # same P#s, nothing invented
    assert _drain_and_grade(inner, agent, sink, "test-agent", "fias_duplicate_records") == []


def test_oversize_session_closes_and_redials(link_env: Any) -> None:
    """fias_oversize_frame: host closes the bad session; redial is clean, no crash."""
    host, pabx, sink, store = link_env
    _ = (sink, store)
    accepted_before = host.link_state()["links_accepted"]
    with pabx.lock:
        pabx.faults.send_oversize = True
    _wait_for(lambda: host.link_state()["counters"]["oversize_closed"] >= 1,
              timeout=15.0, what="host oversize close")
    _wait_for(lambda: host.link_state()["links_accepted"] >= accepted_before + 1,
              timeout=15.0, what="PABX redial")
    _wait_for(lambda: host.link_state()["link_up"], timeout=15.0, what="link back up")
    assert pabx.links_established >= 2
    assert host.is_alive() if hasattr(host, "is_alive") else True
    # The new link carries business traffic again.
    with pabx.lock:
        pabx.faults.burst_postings = 2
    _wait_for(lambda: pabx.snapshot()["counters"]["postings_sent"] >= 8,
              timeout=15.0, what="postings resume")


def test_flood_session_closes_and_redials(link_env: Any) -> None:
    """fias_record_flood: identical-record storm closes the session; redial holds."""
    host, pabx, sink, store = link_env
    _ = (sink, store)
    accepted_before = host.link_state()["links_accepted"]
    with pabx.lock:
        pabx.faults.flood_records = 150
    _wait_for(lambda: host.link_state()["counters"]["flood_closed"] >= 1,
              timeout=30.0, what="host flood close")
    _wait_for(lambda: host.link_state()["links_accepted"] >= accepted_before + 1,
              timeout=15.0, what="PABX redial")
    _wait_for(lambda: host.link_state()["link_up"], timeout=15.0, what="link back up")
    # No close-loop: the redialed session survives past the leftover repeats.
    time.sleep(4.0)
    assert host.link_state()["links_accepted"] == accepted_before + 1
    assert host.link_state()["link_up"] is True


def test_session_lost_command_retried_or_reported(relay_pair: Any, link_env: Any) -> None:
    """session_lost_with_command: drop mid-wait → retried on the new session, once."""
    from verifier.fias_checks import grade_fias_scenario  # noqa: PLC0415

    inner, relay = relay_pair
    host, pabx, sink, store = link_env
    agent = Agent("test-agent", store, relay, load_bundle(), host, command_timeout=6.0)

    da, ti = _future_wire()
    request_id = _enqueue(inner, "test-agent", params={"room": "103", "date": da, "time": ti})
    assert agent.poll_once() == "stored"
    # The session is lost while the command waits in the outbox: drop,
    # redial, and only then execute — the retry must run on the new
    # session, never broadcast, and report exactly once.
    with pabx.lock:
        pabx.faults.drop_link = True
    _wait_for(lambda: host.link_state()["links_accepted"] >= 2,
              timeout=15.0, what="redial after drop")
    _wait_for(lambda: host.link_state()["link_up"], timeout=15.0, what="link back up")
    assert _results_for(inner, "test-agent") == []  # never half-reported
    assert agent.execute_once() == "sent"
    assert _result_statuses(inner, "test-agent", request_id) == ["sent"]
    assert pabx.snapshot()["scheduled_wakeups"] == 1  # one wake-up, never broadcast twice
    assert grade_fias_scenario(
        "session_lost_with_command",
        {"reported_outcomes": ["sent"], "expected_outcome": "sent",
         "sent_to_pabx": True, "may_send": True}) == []


def test_stale_command_never_sent(relay_pair: Any, link_env: Any) -> None:
    """stale_command: an expired wake-up is reported, never transmitted."""
    from verifier.fias_checks import grade_fias_scenario  # noqa: PLC0415

    inner, relay = relay_pair
    host, pabx, sink, store = link_env
    agent = Agent("test-agent", store, relay, load_bundle(), host, command_timeout=6.0)

    past = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)).isoformat()
    request_id = _enqueue(inner, "test-agent", params={"room": "104"}, expires_at=past)
    assert agent.poll_once() == "stored"
    assert agent.execute_once() == "expired"
    assert host.link_state()["counters"]["commands_sent"] == 0
    assert pabx.snapshot()["scheduled_wakeups"] == 0
    assert _result_statuses(inner, "test-agent", request_id) == ["expired"]
    assert grade_fias_scenario(
        "stale_command",
        {"reported_outcomes": ["expired"], "expected_outcome": "expired",
         "sent_to_pabx": False, "may_send": False}) == []


# ---------------------------------------------------------------------------
# Live: fault injection + recovery against the stack
# ---------------------------------------------------------------------------


def _live_post(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None,
               timeout: float = 15.0) -> tuple[int, dict[str, Any]]:
    import urllib.request  # noqa: PLC0415

    data = json.dumps(payload).encode()
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=req_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return resp.status, json.loads(body) if body else {}
    except Exception as exc:  # urllib.error.HTTPError carries the status
        status = getattr(exc, "code", 0)
        try:
            return status, json.loads(exc.read() or b"{}")
        except Exception:
            return status, {}


def _live_get(url: str, headers: dict[str, str] | None = None,
              timeout: float = 15.0) -> tuple[int, dict[str, Any]]:
    import urllib.request  # noqa: PLC0415

    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return resp.status, json.loads(body) if body else {}
    except Exception as exc:
        return getattr(exc, "code", 0), {}


LIVE_SCENARIOS = [
    "fias_record_layout_change",
    "fias_link_resync",
    "fias_ack_timeout",
    "agent_restart_inflight",
    "cloud_slow",
    "fias_duplicate_records",
    "fias_oversize_frame",
    "fias_record_flood",
    "session_lost_with_command",
    "stale_command",
]


def _wait_agent_link_live(timeout: float = 120.0) -> None:
    """Wait until the cloud heartbeat reports the agent link up (settle)."""
    import httpx  # noqa: PLC0415

    from tests.e2e.conftest import control_headers  # noqa: PLC0415

    _wait_for(
        lambda: any(
            a.get("agent_id") == "grand-plaza-pabx" and a.get("link_up") in (1, True)
            for a in httpx.get("http://localhost:8101/edge/agents",
                               headers=control_headers(), timeout=15.0).json()["agents"]
        ),
        timeout=timeout, what="agent link settled up",
    )


def _wait_edge_readyz_link_up(timeout: float = 120.0) -> None:
    """Wait until the agent's own /readyz reports the PABX link up.

    Called in every live fault test's teardown after the scenario reset:
    the next file must inherit a connected link, never a redial in
    progress (REQ-022 owner check 2026-10-29 — not a flake to retry).
    """
    import httpx  # noqa: PLC0415

    def _up() -> bool:
        try:
            body = httpx.get("http://localhost:8114/readyz", timeout=10.0).json()
        except Exception:
            return False
        if not isinstance(body, dict):
            return False
        link = body.get("link", {})
        return bool(isinstance(link, dict) and link.get("link_up") is True)

    _wait_for(_up, timeout=timeout, what="edge-worker readyz link_up")


def test_live_scenario_route_arms_every_fault(e2e_stack: str) -> None:
    """Live: /admin/scenario arms each D fault; reset returns to baseline."""
    from simulators.fias_pabx.admin import SCENARIO_LINK_FAULTS  # noqa: PLC0415
    from tests.e2e.conftest import admin_headers, admin_url  # noqa: PLC0415

    assert e2e_stack
    for sid in LIVE_SCENARIOS:
        status, body = _live_post(admin_url("sim-fias-pabx", "/admin/scenario"),
                                  {"id": sid, "seed": 42}, headers=admin_headers())
        assert status == 200, (sid, status, body)
        assert body.get("scenario_id") == sid, (sid, body)
        want = {k: (list(v) if isinstance(v, tuple) else v)
                for k, v in SCENARIO_LINK_FAULTS[sid].items()}
        assert body.get("armed_link_faults") == want, (sid, body)
    status, body = _live_post(admin_url("sim-fias-pabx", "/admin/fias/reset"),
                              {}, headers=admin_headers())
    assert status == 200, body
    assert body.get("ps_layout") == "baseline"
    assert body.get("faults", {}).get("silent_types") == []
    _wait_agent_link_live()  # leave the link settled for the next test
    _wait_edge_readyz_link_up()


def test_live_layout_change_visible_in_agent_heartbeat(e2e_stack: str) -> None:
    """Live: the v3 announcement shows up in the cloud heartbeat, then resets."""
    import httpx  # noqa: PLC0415

    from tests.e2e.conftest import admin_headers, admin_url, control_headers  # noqa: PLC0415

    assert e2e_stack
    status, _ = _live_post(admin_url("sim-fias-pabx", "/admin/scenario"),
                           {"id": "fias_record_layout_change", "seed": 42},
                           headers=admin_headers())
    assert status == 200
    try:
        detail = _wait_for(
            lambda: next((
                a.get("link_detail", "")
                for a in httpx.get("http://localhost:8101/edge/agents",
                                   headers=control_headers(), timeout=15.0).json()["agents"]
                if a.get("agent_id") == "grand-plaza-pabx"
                and '"TM"' in a.get("link_detail", "")
            ), ""),
            timeout=120.0, what="v3 layout in agent heartbeat",
        )
        assert '"TM"' in detail
    finally:
        _live_post(admin_url("sim-fias-pabx", "/admin/fias/reset"), {},
                   headers=admin_headers())
    _wait_for(
        lambda: _live_get(admin_url("sim-fias-pabx", "/admin/fias/state"),
                          headers=admin_headers())[1].get("ps_layout") == "baseline",
        timeout=60.0, what="layout back to baseline",
    )
    _wait_agent_link_live()  # leave the link settled for the next test
    _wait_edge_readyz_link_up()


def test_live_inject_fault_proxies_to_fias(e2e_stack: str) -> None:
    """Live: /demo/inject-fault labels a FIAS fault like an HTTP one."""
    from tests.e2e.conftest import admin_headers, admin_url, control_headers  # noqa: PLC0415

    assert e2e_stack
    try:
        status, body = _live_post(
            "http://localhost:8101/demo/inject-fault",
            {"target": "fias", "scenario_id": "fias_link_resync"},
            headers=control_headers(), timeout=20.0)
        assert status == 200, body
        assert body.get("labelled") is True and body.get("demo") is True
        assert body.get("target") == "sim-fias-pabx"
        admin = body.get("admin_response", {})
        assert admin.get("_http_status") == 200, admin
        assert admin.get("scenario_id") == "fias_link_resync", admin
    finally:
        _live_post(admin_url("sim-fias-pabx", "/admin/fias/reset"), {},
                   headers=admin_headers())
        _wait_agent_link_live()  # leave the link settled for the next test
        _wait_edge_readyz_link_up()
