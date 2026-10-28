"""Unit tests for Docker-free local mode (REQ-017, plan §2.1).

Covers ``autobridge.runtime.mode`` (mode detection, target resolution),
the ``/readyz`` mode reporting on control + runtime-sandbox, and the
simulator launcher port overrides. Trust boundary: no Docker needed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pytest


def _reload_mode(monkeypatch: pytest.MonkeyPatch, **env: str) -> Any:
    import autobridge.runtime.mode as mode

    for key in ("AUTOBRIDGE_RUNTIME", "DOCKER_HOST"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return mode


def _patch_which(monkeypatch: pytest.MonkeyPatch, path: str | None) -> None:
    import autobridge.runtime.mode as mode

    monkeypatch.setattr(mode.shutil, "which", lambda _cmd: path)


def _patch_run(monkeypatch: pytest.MonkeyPatch, behavior: Any) -> None:
    import autobridge.runtime.mode as mode

    monkeypatch.setattr(mode.subprocess, "run", behavior)


def test_runtime_mode_explicit_local(monkeypatch: pytest.MonkeyPatch) -> None:
    mode = _reload_mode(monkeypatch, AUTOBRIDGE_RUNTIME="local")
    assert mode.runtime_mode() == "local"
    assert not mode.network_isolation_enforced()


def test_runtime_mode_explicit_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    mode = _reload_mode(monkeypatch, AUTOBRIDGE_RUNTIME="docker")
    assert mode.runtime_mode() == "docker"
    assert mode.network_isolation_enforced()


def test_runtime_mode_auto_no_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    mode = _reload_mode(monkeypatch)
    monkeypatch.setattr(mode, "docker_available", lambda: False)
    assert mode.runtime_mode() == "local"
    assert not mode.network_isolation_enforced()


def test_runtime_mode_auto_with_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    mode = _reload_mode(monkeypatch)
    monkeypatch.setattr(mode, "docker_available", lambda: True)
    assert mode.runtime_mode() == "docker"
    assert mode.network_isolation_enforced()


def test_runtime_mode_case_and_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    mode = _reload_mode(monkeypatch, AUTOBRIDGE_RUNTIME="  LOCAL ")
    assert mode.runtime_mode() == "local"


def test_docker_available_no_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    import autobridge.runtime.mode as mode

    _patch_which(monkeypatch, None)
    assert mode.docker_available() is False


def test_docker_available_daemon_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    import autobridge.runtime.mode as mode

    class _Proc:
        returncode = 1

    _patch_which(monkeypatch, "/usr/bin/docker")
    _patch_run(monkeypatch, lambda *_args, **_kwargs: _Proc())
    assert mode.docker_available() is False


def test_docker_available_timeout_means_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    import autobridge.runtime.mode as mode

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise TimeoutError("no daemon")

    _patch_which(monkeypatch, "/usr/bin/docker")
    _patch_run(monkeypatch, _boom)
    assert mode.docker_available() is False


def test_docker_available_healthy_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    import autobridge.runtime.mode as mode

    class _Proc:
        returncode = 0

    _patch_which(monkeypatch, "/usr/bin/docker")
    _patch_run(monkeypatch, lambda *_args, **_kwargs: _Proc())
    assert mode.docker_available() is True


def test_active_target_kind_passthrough_in_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    mode = _reload_mode(monkeypatch, AUTOBRIDGE_RUNTIME="docker")
    assert mode.active_target_kind("docker_sandbox") == "docker_sandbox"
    assert mode.active_target_kind("local_process") == "local_process"
    assert mode.active_target_kind("edge") == "edge"


def test_active_target_kind_local_maps_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    mode = _reload_mode(monkeypatch, AUTOBRIDGE_RUNTIME="local")
    assert mode.active_target_kind("docker_sandbox") == "local_process"
    assert mode.active_target_kind("local_process") == "local_process"
    assert mode.active_target_kind("edge") == "edge"


def test_resolve_target_local_mode_uses_local_process(monkeypatch: pytest.MonkeyPatch) -> None:
    mode = _reload_mode(monkeypatch, AUTOBRIDGE_RUNTIME="local")
    target = mode.resolve_target({"kind": "docker_sandbox", "timeout_s": 30})
    assert target.kind == "local_process"
    assert target.timeout_s == 30


def test_resolve_target_docker_mode_keeps_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    mode = _reload_mode(monkeypatch, AUTOBRIDGE_RUNTIME="docker")
    target = mode.resolve_target({"kind": "docker_sandbox", "timeout_s": 30})
    assert target.kind == "docker_sandbox"


def test_resolve_target_edge_untouched_in_local_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    mode = _reload_mode(monkeypatch, AUTOBRIDGE_RUNTIME="local")
    target = mode.resolve_target({"kind": "edge"})
    assert target.kind == "edge"


def test_readyz_response_carries_info_without_changing_status() -> None:
    import json

    from autobridge.health import readyz_response

    resp = readyz_response({"db": True}, info={"runtime_mode": "local"})
    assert resp.status_code == 200
    assert json.loads(resp.body) == {
        "status": "ok",
        "checks": {"db": "ok"},
        "runtime_mode": "local",
    }
    degraded = readyz_response({"db": False}, info={"runtime_mode": "local"})
    assert degraded.status_code == 503
    assert json.loads(degraded.body)["runtime_mode"] == "local"


def test_control_readyz_reports_local_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx
    from fastapi.testclient import TestClient

    from autobridge.control.app import app

    monkeypatch.setenv("AUTOBRIDGE_RUNTIME", "local")

    class _OkClient:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def __enter__(self) -> _OkClient:
            return self

        def __exit__(self, *_args: Any) -> bool:
            return False

        def get(self, *_args: Any, **_kwargs: Any) -> Any:
            return httpx.Response(200, json={"status": "ok"})

    monkeypatch.setattr(httpx, "Client", _OkClient)
    client = TestClient(app)
    resp = client.get("/readyz")
    data = resp.json()
    assert data["runtime_mode"] == "local"
    assert "no network isolation" in data["network_isolation"]


def test_runtime_sandbox_readyz_reports_active_target(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from autobridge.runtime.app import app as runtime_app

    monkeypatch.setenv("AUTOBRIDGE_RUNTIME", "local")
    client = TestClient(runtime_app)
    resp = client.get("/readyz")
    data = resp.json()
    assert data["runtime_mode"] == "local"
    assert data["active_target"] == "local_process"
    assert "no network isolation" in data["network_isolation"]
