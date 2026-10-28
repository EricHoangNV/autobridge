"""REQ-022 B — control /edge/* proxy: units (fake relay HTTP, scratch DB)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from autobridge.control.app import app
from autobridge.control.deps import get_db
from autobridge.control.models import Base, session_factory


@pytest.fixture()
def client(tmp_path: object) -> Any:
    from pathlib import Path

    engine = create_engine(f"sqlite:///{Path(str(tmp_path))}/control.db")
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

    app.dependency_overrides[get_db] = override_db
    c = TestClient(app)
    yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def relay_client(tmp_path: object, monkeypatch: Any) -> TestClient:
    """A real relay app, served to the proxy through a faked httpx.Client."""
    from pathlib import Path

    from autobridge.edge.relay import _Store, create_app

    relay_app = create_app(_Store(str(Path(str(tmp_path)) / "relay.db")))
    inner = TestClient(relay_app)

    class _FakeHttpxClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            _ = (args, kwargs)

        def __enter__(self) -> Any:
            return inner

        def __exit__(self, *args: Any) -> bool:
            return False

    monkeypatch.setattr(httpx, "Client", _FakeHttpxClient)
    monkeypatch.setenv("EDGE_RELAY_URL", "http://relay")
    return inner


def test_edge_enqueue_and_drain(client: TestClient, relay_client: TestClient) -> None:
    r = client.post("/edge/commands", json={"agent_id": "a1", "kind": "wakeup.set",
                                             "params": {"room": "101"}})
    assert r.status_code == 200, r.text
    request_id = r.json()["request_id"]
    assert r.json()["existed"] is False
    # Idempotent re-enqueue returns the same request.
    r2 = client.post("/edge/commands", json={"agent_id": "a1", "kind": "wakeup.set",
                                              "idempotency_key": "k-1"})
    assert r2.status_code == 200
    assert r2.json()["existed"] is False
    r3 = client.post("/edge/commands", json={"agent_id": "a1", "kind": "other",
                                              "idempotency_key": "k-1"})
    assert r3.json() == {"request_id": r2.json()["request_id"], "existed": True}
    # Poll + result through the relay, drained through the proxy.
    nxt = relay_client.get("/v1/commands/next", params={"agent_id": "a1"})
    assert nxt.json()["request_id"] == request_id
    relay_client.post(f"/v1/commands/{request_id}/result", json={"status": "sent"})
    results = client.get("/edge/commands/results", params={"agent_id": "a1"}).json()
    assert len(results["results"]) == 1
    assert results["results"][0]["request_id"] == request_id
    postings = client.get("/edge/postings", params={"agent_id": "a1"}).json()
    assert postings == {"postings": []}
    agents = client.get("/edge/agents").json()
    assert agents == {"agents": []}


def test_edge_post_requires_control_token(client: TestClient, relay_client: TestClient,  # noqa: ARG001
                                          monkeypatch: Any) -> None:
    monkeypatch.setenv("AUTOBRIDGE_CONTROL_TOKEN", "op-secret")
    r = client.post("/edge/commands", json={"agent_id": "a1", "kind": "wakeup.set"})
    assert r.status_code == 401
    ok = client.post("/edge/commands", json={"agent_id": "a1", "kind": "wakeup.set"},
                     headers={"Authorization": "Bearer op-secret"})
    assert ok.status_code == 200
    # GETs stay open (REQ-012).
    assert client.get("/edge/commands/results").status_code == 200
    assert client.get("/edge/postings").status_code == 200
    assert client.get("/edge/agents").status_code == 200


def test_edge_relay_down_is_502(client: TestClient, monkeypatch: Any) -> None:
    monkeypatch.setenv("EDGE_RELAY_URL", "http://127.0.0.1:9")  # refused fast
    r = client.post("/edge/commands", json={"agent_id": "a1", "kind": "wakeup.set"})
    assert r.status_code == 502
    assert "relay" in r.json()["detail"]


def test_edge_openapi_has_edge_routes(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert "/edge/commands" in schema["paths"]
    assert "/edge/commands/results" in schema["paths"]
    assert "/edge/postings" in schema["paths"]
    assert "/edge/agents" in schema["paths"]
