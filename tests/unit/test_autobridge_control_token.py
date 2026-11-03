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
def client(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> Any:  # type: ignore[no-untyped-def]
    # Hermetic (REQ-022 DNS-hermetic fix): /demo/reset posts every simulator
    # admin URL over real httpx; refuse on loopback instead of resolving
    # hostnames (macOS fork-safety: no real DNS in unit tests).
    monkeypatch.setenv("SIMULATOR_ADMIN_URL", "http://127.0.0.1:9")
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


# --- REQ-089 (AR-7-2): startup says whether writes are gated ---


def test_startup_warns_open_writes_when_token_empty(
    caplog: pytest.LogCaptureFixture, no_token: None
) -> None:
    """Empty token → one loud warning that writes are open (REQ-089 item 2)."""
    from autobridge.control.app import log_control_token_status

    with caplog.at_level("WARNING", logger="autobridge.control"):
        log_control_token_status()
    warnings = [r for r in caplog.records if r.name == "autobridge.control" and r.levelname == "WARNING"]
    assert warnings, "expected a startup warning when the control token is empty"
    assert any("OPEN" in r.getMessage() for r in warnings)
    assert any("AUTOBRIDGE_CONTROL_TOKEN" in r.getMessage() for r in warnings)


def test_startup_names_var_but_never_value_when_set(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Set token → the var name is logged, the value never is (REQ-089 item 2)."""
    from autobridge.control.app import log_control_token_status

    monkeypatch.setenv("AUTOBRIDGE_CONTROL_TOKEN", "req089-secret-value-xyz")
    with caplog.at_level("INFO", logger="autobridge.control"):
        log_control_token_status()
    assert "req089-secret-value-xyz" not in caplog.text
    assert "AUTOBRIDGE_CONTROL_TOKEN" in caplog.text


# --- REQ-089 (AR-7-2): the labelled demo default ships everywhere ---

DEMO_CONTROL_TOKEN = "control-demo-token"


def _repo_file(*parts: str) -> str:
    import pathlib

    return (pathlib.Path(__file__).resolve().parents[2] / pathlib.Path(*parts)).read_text(
        encoding="utf-8"
    )


def test_compose_all_control_services_default_demo_token() -> None:
    """control, worker, edge-relay and ui share one demo default (REQ-089 item 1)."""
    import re

    compose = _repo_file("compose.yaml")
    for service in ("control", "worker", "edge-relay", "ui"):
        block = re.search(
            rf"^  {service}:\n(?:.*\n)*?    environment:\n((?:      .*\n)+)",
            compose,
            re.MULTILINE,
        )
        assert block is not None, f"compose service {service} has no environment block"
        match = re.search(
            r"^\s*AUTOBRIDGE_CONTROL_TOKEN: \$\{AUTOBRIDGE_CONTROL_TOKEN:-(.*?)\}$",
            block.group(1),
            re.MULTILINE,
        )
        assert match is not None, f"compose {service} must default AUTOBRIDGE_CONTROL_TOKEN"
        assert match.group(1) == DEMO_CONTROL_TOKEN


def test_edge_token_chain_tracks_control_demo_default() -> None:
    """EDGE_TOKEN falls back to the same demo default (REQ-031 + REQ-089)."""
    compose = _repo_file("compose.yaml")
    assert "EDGE_TOKEN: ${EDGE_TOKEN:-${AUTOBRIDGE_CONTROL_TOKEN:-control-demo-token}}" in compose
    local_up = _repo_file("scripts", "local-up.sh")
    assert (
        'export EDGE_TOKEN="${EDGE_TOKEN:-${AUTOBRIDGE_CONTROL_TOKEN:-control-demo-token}}"'
        in local_up
    )


def test_local_up_and_env_example_ship_demo_default() -> None:
    """local-up.sh exports it; .env.example documents it (explicit wins)."""
    local_up = _repo_file("scripts", "local-up.sh")
    assert (
        'export AUTOBRIDGE_CONTROL_TOKEN="${AUTOBRIDGE_CONTROL_TOKEN:-control-demo-token}"'
        in local_up
    )
    values: dict[str, str] = {}
    for line in _repo_file(".env.example").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    assert values.get("AUTOBRIDGE_CONTROL_TOKEN") == DEMO_CONTROL_TOKEN


# --- Caller helpers send the token only when set ---


def test_caller_helpers_emit_bearer_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    from bench import demo as demo_mod
    from bench import harness as harness_mod
    from autobridge import cli as cli_mod

    monkeypatch.delenv("AUTOBRIDGE_CONTROL_TOKEN", raising=False)
    assert demo_mod.control_headers() == {}
    assert harness_mod.control_headers() == {}
    assert cli_mod.auth_headers() == {}

    monkeypatch.setenv("AUTOBRIDGE_CONTROL_TOKEN", "helper-token-1")
    assert demo_mod.control_headers() == {"Authorization": "Bearer helper-token-1"}
    assert harness_mod.control_headers() == {"Authorization": "Bearer helper-token-1"}
    assert cli_mod.auth_headers() == {"Authorization": "Bearer helper-token-1"}
