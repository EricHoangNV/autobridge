"""Additional health coverage for autobridge."""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient


def test_control_healthz_content_type() -> None:
    from autobridge.control.app import app

    client = TestClient(app)
    resp = client.get("/healthz")
    assert resp.headers["content-type"].startswith("application/json")


def test_control_readyz_has_checks() -> None:
    from autobridge.control.app import app

    client = TestClient(app)
    resp = client.get("/readyz")
    data = resp.json()
    assert "checks" in data
    assert "status" in data


def test_readyz_response_ok_is_200() -> None:
    from autobridge.health import readyz_response

    resp = readyz_response({"db": True, "verifier": True})
    assert resp.status_code == 200
    assert json.loads(resp.body) == {
        "status": "ok",
        "checks": {"db": "ok", "verifier": "ok"},
    }


def test_readyz_response_degraded_is_503() -> None:
    """AR-2-6: a failed check must surface as HTTP 503, not 200."""
    from autobridge.health import readyz_response

    resp = readyz_response({"db": True, "verifier": False})
    assert resp.status_code == 503
    assert json.loads(resp.body) == {
        "status": "degraded",
        "checks": {"db": "ok", "verifier": "fail"},
    }


class _RefusedClient:
    """Stand-in for httpx.Client whose requests always fail (AR-2-6)."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def __enter__(self) -> _RefusedClient:
        return self

    def __exit__(self, *_args: Any) -> bool:
        return False

    def get(self, *_args: Any, **_kwargs: Any) -> Any:
        import httpx

        raise httpx.ConnectError("connection refused (test fake)")


class _OkClient(_RefusedClient):
    """Stand-in for httpx.Client whose GETs return HTTP 200 (AR-2-6)."""

    def get(self, *_args: Any, **_kwargs: Any) -> Any:
        import httpx

        return httpx.Response(200, json={"status": "ok"})


def test_control_readyz_503_when_verifier_unreachable(monkeypatch: Any) -> None:
    """AR-2-6: control /readyz is 503 while the verifier dependency is down."""
    import httpx

    from autobridge.control.app import app

    monkeypatch.setattr(httpx, "Client", _RefusedClient)
    client = TestClient(app)
    resp = client.get("/readyz")
    assert resp.status_code == 503
    data = resp.json()
    assert data["status"] == "degraded"
    assert data["checks"]["verifier"] == "fail"


def test_control_readyz_200_when_all_checks_pass(monkeypatch: Any) -> None:
    """AR-2-6: control /readyz stays 200 when every check passes."""
    import httpx

    from autobridge.control.app import app

    monkeypatch.setattr(httpx, "Client", _OkClient)
    client = TestClient(app)
    resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_worker_readyz_200_with_reachable_db(monkeypatch: Any, tmp_path: Any) -> None:
    """AR-2-6: worker /readyz is 200 when the job store opens."""
    from autobridge.engine.app import app

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/worker.db")
    client = TestClient(app)
    resp = client.get("/readyz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["checks"]["db"] == "ok"


def test_worker_readyz_503_when_db_unreachable(monkeypatch: Any) -> None:
    """AR-2-6: worker /readyz is 503 when the job store cannot be opened."""
    from autobridge.engine.app import app

    monkeypatch.setenv("DATABASE_URL", "sqlite:////proc/definitely-not-here/w.db")
    client = TestClient(app)
    resp = client.get("/readyz")
    assert resp.status_code == 503
    data = resp.json()
    assert data["status"] == "degraded"
    assert data["checks"]["db"] == "fail"
