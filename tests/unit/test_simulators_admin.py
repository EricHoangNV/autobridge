"""Simulator admin authorization tests (AR-1-1).

The ``X-Admin-Network: admin-net`` header is a published routing hint, not
a credential. When ``SIM_ADMIN_TOKEN`` is set, admin writes additionally
require ``X-Admin-Token`` (constant-time compare); a vendor-net peer that
only replays the header gets 403. When the token is unset the API falls
back to header-only dev mode.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from simulators.common.admin import ADMIN_TOKEN_ENV_VAR, create_admin_app

TOKEN = "test-admin-token-ar-1-1"
HEADERS = {"X-Admin-Network": "admin-net"}


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_admin_app())


def _authed(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = dict(HEADERS)
    if extra:
        headers.update(extra)
    return headers


def test_healthz_requires_header_without_token(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Legacy dev mode: header gates, no token involved."""
    monkeypatch.delenv(ADMIN_TOKEN_ENV_VAR, raising=False)
    assert client.get("/healthz").status_code == 403
    assert client.get("/healthz", headers=_authed()).status_code == 200
    assert client.get("/readyz").status_code == 403
    assert client.get("/readyz", headers=_authed()).status_code == 200


def test_header_only_write_succeeds_without_token(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Legacy dev mode keeps header-only stacks (and old e2e) working."""
    monkeypatch.delenv(ADMIN_TOKEN_ENV_VAR, raising=False)
    r = client.post("/admin/reset", headers=_authed())
    assert r.status_code == 200


def test_header_only_write_rejected_when_token_set(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """AR-1-1 core: the published header alone must NOT authorize a write."""
    monkeypatch.setenv(ADMIN_TOKEN_ENV_VAR, TOKEN)
    assert client.post("/admin/reset", headers=_authed()).status_code == 403
    assert client.post("/admin/scenario", json={"id": "baseline", "seed": 0}, headers=_authed()).status_code == 403
    assert client.get("/admin/scenario", headers=_authed()).status_code == 403
    assert client.get("/healthz", headers=_authed()).status_code == 403


def test_missing_header_rejected_when_token_set(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ADMIN_TOKEN_ENV_VAR, TOKEN)
    assert client.post("/admin/reset").status_code == 403
    assert client.post("/admin/reset", headers={"X-Admin-Token": TOKEN}).status_code == 403


def test_wrong_token_rejected(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ADMIN_TOKEN_ENV_VAR, TOKEN)
    assert client.post("/admin/reset", headers=_authed({"X-Admin-Token": "wrong"})).status_code == 403


def test_correct_token_authorizes_write_and_round_trips(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ADMIN_TOKEN_ENV_VAR, TOKEN)
    auth = _authed({"X-Admin-Token": TOKEN})
    r = client.post("/admin/scenario", json={"id": "offset_to_cursor", "seed": 7}, headers=auth)
    assert r.status_code == 200
    assert r.json()["scenario_id"] == "offset_to_cursor"
    g = client.get("/admin/scenario", headers=auth)
    assert g.status_code == 200
    assert g.json()["scenario_id"] == "offset_to_cursor"
    # Leave global sim state clean for other tests.
    reset = client.post("/admin/reset", headers=auth)
    assert reset.status_code == 200
    assert reset.json()["scenario_id"] == "baseline"


def test_rejection_never_echoes_token(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Secrets rule: 403 details name the missing header, never the value."""
    monkeypatch.setenv(ADMIN_TOKEN_ENV_VAR, TOKEN)
    for resp in (
        client.post("/admin/reset", headers=_authed()),
        client.post("/admin/reset", headers=_authed({"X-Admin-Token": "wrong"})),
        client.get("/healthz", headers=_authed()),
    ):
        assert resp.status_code == 403
        assert TOKEN not in resp.text
