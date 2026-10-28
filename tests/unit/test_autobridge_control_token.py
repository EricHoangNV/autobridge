# ruff: noqa: TC003, ARG001, F401, I001, F541
"""Control-plane write token tests (REQ-012).

``AUTOBRIDGE_CONTROL_TOKEN`` guards every state-changing route
(POST/PUT/PATCH/DELETE): unset/empty behaves exactly as before (open),
set requires ``Authorization: Bearer <token>`` (constant-time compare,
401 otherwise). Read-only GETs, ``/healthz``, ``/readyz`` and the SSE
stream stay open either way. The token is a credential: it never appears
in API responses and the SSE redaction covers it.
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from autobridge.control import sse as sse_mod
from autobridge.control.app import app
from autobridge.control.deps import get_db
from autobridge.control.models import (
    Base,
    session_factory,
)


@pytest.fixture()
def client(tmp_path: pathlib.Path) -> Any:  # type: ignore[no-untyped-def]
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

    app.dependency_overrides[get_db] = override_db
    c = TestClient(app)
    yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOBRIDGE_CONTROL_TOKEN", raising=False)


@pytest.fixture()
def with_token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("AUTOBRIDGE_CONTROL_TOKEN", "test-token-abc123")
    return "test-token-abc123"


def _connector_payload() -> dict[str, Any]:
    return {
        "name": "Token Test",
        "base_url": "http://sim-harbor:8000",
        "credential_ref": "HARBOR_API_KEY",
        "target_entities": ["Reservation"],
        "runtime_target": {"kind": "local_process"},
    }


def _discovery_payload() -> dict[str, Any]:
    return {
        "records": [
            {
                "discovery_id": "disc-token-001",
                "status": "discovered",
                "vendor": "TokenPMS",
                "base_url": "https://token.example.com",
                "candidate_entities": ["Reservation"],
                "evidence_refs": [],
            }
        ]
    }


# --- Unset: behavior exactly as before (open) ---


def test_unset_write_routes_open(client: Any, no_token: None) -> None:  # type: ignore[no-untyped-def]
    r = client.post("/connectors", json=_connector_payload())
    assert r.status_code == 201, r.text
    r = client.post("/discovery/import", json=_discovery_payload())
    assert r.status_code == 201, r.text
    r = client.post("/demo/reset", json={})
    assert r.status_code == 200, r.text


def test_empty_token_means_open(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("AUTOBRIDGE_CONTROL_TOKEN", "")
    r = client.post("/connectors", json=_connector_payload())
    assert r.status_code == 201, r.text


# --- Set: 401 without or with a wrong token, 2xx with the right one ---


def test_set_missing_token_401(client: Any, with_token: str) -> None:  # type: ignore[no-untyped-def]
    r = client.post("/connectors", json=_connector_payload())
    assert r.status_code == 401
    assert r.headers.get("WWW-Authenticate") == "Bearer"
    # The token value never leaks into the error body
    assert with_token not in r.text


def test_set_wrong_token_401(client: Any, with_token: str) -> None:  # type: ignore[no-untyped-def]
    r = client.post(
        "/connectors",
        json=_connector_payload(),
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert r.status_code == 401
    # Non-bearer scheme is also rejected
    r = client.post(
        "/connectors",
        json=_connector_payload(),
        headers={"Authorization": f"Token {with_token}"},
    )
    assert r.status_code == 401


def test_set_right_token_2xx(client: Any, with_token: str) -> None:  # type: ignore[no-untyped-def]
    auth = {"Authorization": f"Bearer {with_token}"}
    r = client.post("/connectors", json=_connector_payload(), headers=auth)
    assert r.status_code == 201, r.text
    # The token never appears in a success response
    assert with_token not in r.text
    r = client.post("/discovery/import", json=_discovery_payload(), headers=auth)
    assert r.status_code == 201, r.text
    assert with_token not in r.text
    r = client.post("/demo/reset", json={}, headers=auth)
    assert r.status_code == 200, r.text
    assert with_token not in r.text


# --- GETs, healthz, readyz and the SSE stream stay open ---


def test_gets_open_either_way(client: Any, with_token: str) -> None:  # type: ignore[no-untyped-def]
    auth = {"Authorization": f"Bearer {with_token}"}
    # Without credentials, with the token set
    assert client.get("/healthz").status_code == 200
    assert client.get("/connectors").status_code == 200
    assert client.get("/operator").status_code == 200
    # And with credentials (GETs accept but never require the token)
    assert client.get("/healthz", headers=auth).status_code == 200
    assert client.get("/connectors", headers=auth).status_code == 200


def test_gets_open_when_unset(client: Any, no_token: None) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/healthz").status_code == 200
    assert client.get("/connectors").status_code == 200


def test_sse_stream_not_gated(client: Any, with_token: str) -> None:  # type: ignore[no-untyped-def]
    # Unknown run: 404 proves the route ran its own lookup instead of a 401.
    r = client.get("/runs/does-not-exist/events")
    assert r.status_code == 404


# --- OpenAPI contract: security scheme on the write routes only ---


def test_openapi_write_routes_carry_bearer_scheme() -> None:
    schema = app.openapi()
    schemes = schema.get("components", {}).get("securitySchemes", {})
    assert "HTTPBearer" in schemes
    assert schemes["HTTPBearer"].get("type") == "http"
    assert schemes["HTTPBearer"].get("scheme") == "bearer"
    post_without = [
        path
        for path, ops in schema["paths"].items()
        if "post" in ops and "security" not in ops["post"]
    ]
    assert post_without == []
    get_with = [
        (method, path)
        for path, ops in schema["paths"].items()
        for method in ops
        if method == "get" and "security" in ops[method]
    ]
    assert get_with == []


# --- The token is a credential: SSE redaction covers it ---


def test_sse_redaction_covers_control_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOBRIDGE_CONTROL_TOKEN", "token-secret-xyz789")
    secrets = sse_mod.collect_secrets(None)
    assert "token-secret-xyz789" in secrets


# --- Caller helpers send the token only when set ---


def test_caller_helpers_emit_bearer_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    from autobridge.bench import demo as demo_mod
    from autobridge.bench import harness as harness_mod
    from autobridge import cli as cli_mod

    monkeypatch.delenv("AUTOBRIDGE_CONTROL_TOKEN", raising=False)
    assert demo_mod.control_headers() == {}
    assert harness_mod.control_headers() == {}
    assert cli_mod.auth_headers() == {}

    monkeypatch.setenv("AUTOBRIDGE_CONTROL_TOKEN", "helper-token-1")
    assert demo_mod.control_headers() == {"Authorization": "Bearer helper-token-1"}
    assert harness_mod.control_headers() == {"Authorization": "Bearer helper-token-1"}
    assert cli_mod.auth_headers() == {"Authorization": "Bearer helper-token-1"}
