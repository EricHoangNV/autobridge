"""REQ-022 B — edge-relay queue service: units (no Docker, scratch SQLite)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from autobridge.edge.relay import _Store, create_app


@pytest.fixture()
def relay(tmp_path: object) -> tuple[TestClient, _Store]:
    from pathlib import Path

    store = _Store(str(Path(str(tmp_path)) / "relay.db"))
    return TestClient(create_app(store)), store


def _enqueue(client: TestClient, **over: Any) -> dict[str, Any]:
    body = {"agent_id": "grand-plaza-pabx", "kind": "wakeup.set",
            "params": {"room": "101", "date": "261028", "time": "070000"}}
    body.update(over)
    r = client.post("/v1/enqueue", json=body)
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def test_healthz_readyz(relay: tuple[TestClient, _Store]) -> None:
    client, _ = relay
    assert client.get("/healthz").json() == {"status": "ok"}
    body = client.get("/readyz").json()
    assert body["status"] == "ok"
    assert body["checks"] == {"relay_store": "ok"}


def test_enqueue_poll_result_roundtrip(relay: tuple[TestClient, _Store]) -> None:
    client, _ = relay
    created = _enqueue(client, idempotency_key="k1")
    assert created["existed"] is False
    nxt = client.get("/v1/commands/next", params={"agent_id": "grand-plaza-pabx"})
    assert nxt.status_code == 200
    cmd = nxt.json()
    assert cmd["request_id"] == created["request_id"]
    assert cmd["params"] == {"room": "101", "date": "261028", "time": "070000"}
    # Nothing else queued: the delivered command is not re-polled immediately.
    assert client.get("/v1/commands/next", params={"agent_id": "grand-plaza-pabx"}).status_code == 204
    r = client.post(f"/v1/commands/{created['request_id']}/result",
                    json={"status": "sent", "detail": "link ACK"})
    assert r.json() == {"request_id": created["request_id"], "stored": True,
                        "status": "sent", "detail": "link ACK"}
    results = client.get("/v1/results", params={"agent_id": "grand-plaza-pabx"}).json()
    assert len(results["results"]) == 1
    assert results["results"][0]["result_json"] is not None


def test_enqueue_idempotent_on_key(relay: tuple[TestClient, _Store]) -> None:
    client, _ = relay
    first = _enqueue(client, idempotency_key="idem-1")
    second = _enqueue(client, idempotency_key="idem-1", kind="guest.out")
    assert second == {"request_id": first["request_id"], "existed": True}


def test_result_first_write_wins(relay: tuple[TestClient, _Store]) -> None:
    client, _ = relay
    created = _enqueue(client)
    client.get("/v1/commands/next", params={"agent_id": "grand-plaza-pabx"})
    client.post(f"/v1/commands/{created['request_id']}/result", json={"status": "sent"})
    again = client.post(f"/v1/commands/{created['request_id']}/result",
                        json={"status": "failed", "detail": "late"})
    assert again.json()["stored"] is False
    assert again.json()["status"] == "sent"


def test_result_unknown_request_is_404(relay: tuple[TestClient, _Store]) -> None:
    client, _ = relay
    r = client.post("/v1/commands/nope/result", json={"status": "sent"})
    assert r.status_code == 404


def test_result_unknown_status_is_422(relay: tuple[TestClient, _Store]) -> None:
    client, _ = relay
    created = _enqueue(client)
    client.get("/v1/commands/next", params={"agent_id": "grand-plaza-pabx"})
    r = client.post(f"/v1/commands/{created['request_id']}/result", json={"status": "maybe"})
    assert r.status_code == 422


def test_postings_dedup_on_key(relay: tuple[TestClient, _Store]) -> None:
    client, _ = relay
    body = {"agent_id": "grand-plaza-pabx", "idempotency_key": "PS:000001:101:x",
            "record_type": "PS", "fields": {"P#": "000001", "RN": "101"}}
    assert client.post("/v1/postings", json=body).json() == {"duplicate": False}
    assert client.post("/v1/postings", json=body).json() == {"duplicate": True}
    postings = client.get("/v1/postings", params={"agent_id": "grand-plaza-pabx"}).json()
    assert len(postings["postings"]) == 1


def test_heartbeat_and_agents_list(relay: tuple[TestClient, _Store]) -> None:
    client, _ = relay
    assert client.get("/v1/agents").json() == {"agents": []}
    r = client.post("/v1/heartbeats", json={"agent_id": "grand-plaza-pabx", "link_up": True,
                                            "link_detail": "accepted=1", "outbox_depth": 2,
                                            "inbox_depth": 3})
    assert r.json() == {"recorded": True}
    agents = client.get("/v1/agents").json()["agents"]
    assert len(agents) == 1
    assert agents[0]["link_up"] in (1, True)
    assert agents[0]["outbox_depth"] == 2


def test_redelivery_after_silence(tmp_path: object) -> None:
    """A delivered-but-unresulted command is re-delivered (at-least-once)."""
    from pathlib import Path

    store = _Store(str(Path(str(tmp_path)) / "relay.db"))
    try:
        request_id, _ = store.enqueue("a1", "wakeup.set", "{}", None, None)
        first = store.next_command("a1")
        assert first is not None and first["request_id"] == request_id
        assert store.next_command("a1") is None  # delivered, not due yet
        retry = store.next_command("a1", redeliver_after_s=-1)
        assert retry is not None and retry["request_id"] == request_id
    finally:
        store.close()


def test_agent_token_enforced(monkeypatch: Any, tmp_path: object) -> None:
    """EDGE_AGENT_TOKEN set: agent endpoints need it; operator side unaffected."""
    from pathlib import Path

    monkeypatch.setenv("EDGE_AGENT_TOKEN", "agent-secret")
    monkeypatch.delenv("AUTOBRIDGE_CONTROL_TOKEN", raising=False)
    store = _Store(str(Path(str(tmp_path)) / "relay.db"))
    try:
        client = TestClient(create_app(store))
        assert client.get("/v1/commands/next", params={"agent_id": "a"}).status_code == 401
        assert client.post("/v1/enqueue", json={"agent_id": "a", "kind": "k"}).status_code == 200
        authed = client.get("/v1/commands/next", params={"agent_id": "a"},
                            headers={"Authorization": "Bearer agent-secret"})
        assert authed.status_code == 200  # the enqueue above left one command
        assert authed.json()["kind"] == "k"
        wrong = client.get("/v1/commands/next", params={"agent_id": "a"},
                           headers={"Authorization": "Bearer wrong"})
        assert wrong.status_code == 401
    finally:
        store.close()


def test_operator_token_enforced(monkeypatch: Any, tmp_path: object) -> None:
    """AUTOBRIDGE_CONTROL_TOKEN set: operator endpoints need it; agent side open."""
    from pathlib import Path

    monkeypatch.delenv("EDGE_AGENT_TOKEN", raising=False)
    monkeypatch.setenv("AUTOBRIDGE_CONTROL_TOKEN", "op-secret")
    store = _Store(str(Path(str(tmp_path)) / "relay.db"))
    try:
        client = TestClient(create_app(store))
        assert client.post("/v1/enqueue", json={"agent_id": "a", "kind": "k"}).status_code == 401
        assert client.get("/v1/commands/next", params={"agent_id": "a"}).status_code == 204
        ok = client.post("/v1/enqueue", json={"agent_id": "a", "kind": "k"},
                         headers={"Authorization": "Bearer op-secret"})
        assert ok.status_code == 200
    finally:
        store.close()
