"""Additional health coverage for autobridge."""

from __future__ import annotations

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
