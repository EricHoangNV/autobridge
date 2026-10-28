"""Smoke tests for autobridge package — covers health, config, and control app."""

from __future__ import annotations

import importlib
import os

import pytest
from fastapi.testclient import TestClient


def test_package_version() -> None:
    import autobridge

    assert autobridge.__version__ == "0.1.0"


def test_healthz() -> None:
    from autobridge.health import healthz

    result = healthz()
    assert result["status"] == "ok"


def test_readyz_default() -> None:
    from autobridge.health import readyz

    result = readyz()
    assert result["status"] == "ok"
    assert result["checks"]["default"] == "ok"


def test_readyz_all_ok() -> None:
    from autobridge.health import readyz

    result = readyz({"db": True, "cache": True})
    assert result["status"] == "ok"
    assert result["checks"] == {"db": "ok", "cache": "ok"}


def test_readyz_one_fails() -> None:
    from autobridge.health import readyz

    result = readyz({"db": True, "cache": False})
    assert result["status"] == "degraded"
    assert result["checks"]["db"] == "ok"
    assert result["checks"]["cache"] == "fail"


def test_readyz_empty_dict() -> None:
    from autobridge.health import readyz

    result = readyz({})
    assert result["status"] == "ok"
    assert result["checks"]["default"] == "ok"


def test_config_get_env_default() -> None:
    from autobridge.config import get_env

    assert get_env("NONEXISTENT_KEY_XYZ_123", "fallback") == "fallback"
    assert get_env("NONEXISTENT_KEY_XYZ_123") == ""


def test_config_get_env_present(monkeypatch: pytest.MonkeyPatch) -> None:
    from autobridge.config import get_env

    monkeypatch.setenv("TEST_AUTOBRIDGE_KEY", "hello")
    assert get_env("TEST_AUTOBRIDGE_KEY") == "hello"


def test_config_require_env_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    from autobridge.config import require_env

    monkeypatch.setenv("TEST_REQUIRE_KEY", "value123")
    assert require_env("TEST_REQUIRE_KEY") == "value123"


def test_config_require_env_missing() -> None:
    from autobridge.config import require_env

    # Ensure key is absent
    os.environ.pop("MISSING_REQUIRE_KEY_XYZ", None)
    with pytest.raises(RuntimeError, match="Missing required env var"):
        require_env("MISSING_REQUIRE_KEY_XYZ")


def test_config_require_env_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    from autobridge.config import require_env

    monkeypatch.setenv("TEST_EMPTY_KEY", "")
    with pytest.raises(RuntimeError, match="Missing required env var"):
        require_env("TEST_EMPTY_KEY")


def test_control_app_healthz() -> None:
    from autobridge.control.app import app

    client = TestClient(app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_control_app_readyz() -> None:
    from autobridge.control.app import app

    client = TestClient(app)
    resp = client.get("/readyz")
    # Honest readyz (plan §3, AR-2-6): "degraded" with 503 when the verifier
    # is unreachable from the test host; "ok" with 200 only inside the
    # Compose stack.
    assert resp.status_code == 503
    assert resp.json()["status"] == "degraded"


def test_import_all_autobridge_subpackages() -> None:
    """Ensure every autobridge subpackage is importable (coverage)."""
    modules = [
        "autobridge",
        "autobridge.health",
        "autobridge.config",
        "autobridge.control",
        "autobridge.control.app",
        "autobridge.control.routes",
        "autobridge.spec",
        "autobridge.runtime",
        "autobridge.runtime.transforms",
        "autobridge.evidence",
        "autobridge.evidence.redact",
        "autobridge.llm",
        "autobridge.llm.provider",
        "autobridge.drift",
        "autobridge.repair",
        "autobridge.repair.rules",
        "autobridge.discovery",
        "autobridge.engine",
        "autobridge.bench",
    ]
    for mod in modules:
        importlib.import_module(mod)


def test_redact_no_secrets() -> None:
    from autobridge.evidence.redact import redact

    assert redact("hello world", []) == "hello world"
    assert redact("hello world", [""]) == "hello world"


def test_redact_replaces() -> None:
    from autobridge.evidence.redact import redact

    assert redact("my secret is s3cr3t", ["s3cr3t"]) == "my secret is ***"
    assert redact("aabbcc", ["bb"]) == "aa***cc"


def test_redact_multiple() -> None:
    from autobridge.evidence.redact import redact

    text = "token=abc123 and key=xyz789"
    result = redact(text, ["abc123", "xyz789"])
    assert result == "token=*** and key=***"
