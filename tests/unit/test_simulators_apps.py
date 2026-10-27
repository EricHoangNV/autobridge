"""Simulator service app tests (P1.W1.2): /healthz + /readyz on both sims."""

from __future__ import annotations

import importlib

from fastapi.testclient import TestClient


def _check(app_module: str) -> None:
    mod = importlib.import_module(app_module)
    client = TestClient(mod.app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert "checks" in resp.json()


def test_atlas_app_health() -> None:
    _check("simulators.atlaspms.app")


def test_harbor_app_health() -> None:
    _check("simulators.harborpms.app")


def test_sim_apps_do_not_import_engine_or_verifier() -> None:
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    pattern = re.compile(r"^\s*(?:import|from)\s+(\S+)", re.MULTILINE)
    for rel in ("simulators/atlaspms/app.py", "simulators/harborpms/app.py"):
        text = (root / rel).read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            top = match.group(1).split(".")[0]
            assert top not in {"autobridge", "verifier"}, f"{rel} imports {top}"
