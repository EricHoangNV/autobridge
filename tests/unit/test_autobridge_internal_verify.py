"""REQ-029: control POST /internal/verify proxy (worker gate path)."""

from __future__ import annotations

from typing import TYPE_CHECKING

# Save the real client before any monkeypatching (FakeClient below must not
# recurse into itself once httpx.Client is patched).
import httpx as _httpx
from fastapi.testclient import TestClient

from autobridge.control.app import app

if TYPE_CHECKING:
    import pytest

_REAL_HTTPX_CLIENT = _httpx.Client


def _verifier_ok(monkeypatch: pytest.MonkeyPatch, seen: dict) -> None:
    """Fake httpx.Client so the proxy gets a verifier-style pass."""
    import json as _json

    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        try:
            body = _json.loads(request.content.decode())
        except Exception:
            body = {}
        seen.update(body)
        return httpx.Response(
            200,
            json={
                "verdict": "pass",
                "score": 1.0,
                "failed_checks": [],
                "regression": "pass",
                "scenario_id": body.get("scenario_id", "baseline"),
                "mode": body.get("mode", "visible"),
            },
        )

    transport = httpx.MockTransport(handler)

    class FakeClient:
        def __init__(self, *a: object, **kw: object) -> None:  # noqa: ARG001, ARG002
            self._client = _REAL_HTTPX_CLIENT(transport=transport)

        def __enter__(self) -> httpx.Client:
            return self._client

        def __exit__(self, *a: object) -> None:  # noqa: ARG001, ARG002
            self._client.close()

    monkeypatch.setattr(httpx, "Client", FakeClient)


def test_internal_verify_no_verifier_configured_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without VERIFIER_URL control names the gap (fail-closed for the worker)."""
    monkeypatch.delenv("VERIFIER_URL", raising=False)
    monkeypatch.delenv("AUTOBRIDGE_CONTROL_TOKEN", raising=False)
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post("/internal/verify", json={"spec": {"a": 1}, "scenario_id": "baseline"})
    assert r.status_code == 503, r.text
    assert "VERIFIER_URL" in r.json()["detail"]


def test_internal_verify_proxies_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    """With a verifier, the proxy returns its JSON verbatim."""
    monkeypatch.setenv("VERIFIER_URL", "http://verifier:8000")
    monkeypatch.delenv("AUTOBRIDGE_CONTROL_TOKEN", raising=False)
    seen: dict = {}
    _verifier_ok(monkeypatch, seen)
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post(
        "/internal/verify",
        json={"spec": {"a": 1}, "scenario_id": "rename_guest_id", "regression_suite": ["baseline"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verdict"] == "pass"
    assert body["scenario_id"] == "rename_guest_id"
    # The verifier saw the spec and the regression suite.
    assert seen["spec"] == {"a": 1}
    assert seen["regression_suite"] == ["baseline"]


def test_internal_verify_verifier_error_is_502(monkeypatch: pytest.MonkeyPatch) -> None:
    """A verifier non-200 becomes 502 (the worker gate-blocks on it)."""
    import httpx

    monkeypatch.setenv("VERIFIER_URL", "http://verifier:8000")
    monkeypatch.delenv("AUTOBRIDGE_CONTROL_TOKEN", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(500, json={"detail": "boom"})

    transport = httpx.MockTransport(handler)

    class FakeClient:
        def __init__(self, *a: object, **kw: object) -> None:  # noqa: ARG001, ARG002
            self._client = httpx.Client(transport=transport)

        def __enter__(self) -> httpx.Client:
            return self._client

        def __exit__(self, *a: object) -> None:  # noqa: ARG001, ARG002
            self._client.close()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post("/internal/verify", json={"spec": {"a": 1}})
    assert r.status_code == 502, r.text


def test_internal_verify_unreachable_is_502(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unreachable verifier becomes 502 (fail-closed, never a pass)."""
    import httpx

    monkeypatch.setenv("VERIFIER_URL", "http://verifier:8000")
    monkeypatch.delenv("AUTOBRIDGE_CONTROL_TOKEN", raising=False)

    class FakeClient:
        def __init__(self, *a: object, **kw: object) -> None:  # noqa: ARG001, ARG002
            raise httpx.ConnectError("refused")

        def __enter__(self) -> object:  # pragma: no cover
            raise AssertionError("unreachable")

        def __exit__(self, *a: object) -> None:  # pragma: no cover  # noqa: ARG002
            pass

    monkeypatch.setattr(httpx, "Client", FakeClient)
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post("/internal/verify", json={"spec": {"a": 1}})
    assert r.status_code == 502, r.text


def test_internal_verify_requires_token_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """With AUTOBRIDGE_CONTROL_TOKEN set, the proxy needs the bearer token."""
    monkeypatch.setenv("VERIFIER_URL", "http://verifier:8000")
    monkeypatch.setenv("AUTOBRIDGE_CONTROL_TOKEN", "tok-123")
    seen: dict = {}
    _verifier_ok(monkeypatch, seen)
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post("/internal/verify", json={"spec": {"a": 1}})
    assert r.status_code == 401, r.text
    r2 = client.post(
        "/internal/verify",
        json={"spec": {"a": 1}},
        headers={"Authorization": "Bearer tok-123"},
    )
    assert r2.status_code == 200, r2.text
