"""Smoke tests for verifier package."""

from __future__ import annotations

import importlib

from fastapi.testclient import TestClient


def test_verifier_version() -> None:
    import verifier

    assert verifier.__version__ == "0.1.0"


def test_verifier_healthz() -> None:
    from verifier.app import app

    client = TestClient(app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_verifier_readyz() -> None:
    from verifier.app import app

    client = TestClient(app)
    resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert "checks" in resp.json()


def test_verifier_imports() -> None:
    for mod in ["verifier", "verifier.app"]:
        importlib.import_module(mod)
