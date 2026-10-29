"""Unit tests for the shared stack-mode detector (REQ-037)."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING

import pytest

import bench.demo as demo_mod
import bench.stack_mode as stack_mode_mod
from bench.stack_mode import (
    StackMode,
    detect_stack_mode,
    is_local_stack,
    pid_alive,
    probe_runtime_mode,
    recorded_pids,
)

if TYPE_CHECKING:
    import pathlib


@pytest.fixture(autouse=True)
def _clean_stack_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Host env must not leak into mode detection."""
    monkeypatch.delenv("AUTOBRIDGE_RUNTIME", raising=False)
    monkeypatch.delenv("CONTROL_URL", raising=False)
    stack_mode_mod._warned_stale_roots.clear()


def _make_root(tmp_path: pathlib.Path, *, with_marker: bool) -> pathlib.Path:
    if with_marker:
        local = tmp_path / ".local"
        (local / "pids").mkdir(parents=True)
        (local / "ACTIVE").write_text("2026-10-29T00:00:00Z\n", encoding="utf-8")
    return tmp_path


def test_no_marker_is_compose(tmp_path: pathlib.Path) -> None:
    det = detect_stack_mode(root=tmp_path)
    assert det == StackMode(mode="compose", stale_marker=False, detail=det.detail)
    assert "no .local/ACTIVE" in det.detail
    assert is_local_stack(root=tmp_path) is False


def test_str_root_accepted(tmp_path: pathlib.Path) -> None:
    assert detect_stack_mode(root=str(tmp_path)).mode == "compose"
    assert is_local_stack(root=str(tmp_path)) is False


def test_explicit_env_wins_over_stale_marker(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_root(tmp_path, with_marker=True)
    (root / ".local" / "pids" / "control.pid").write_text("999999999\n", encoding="utf-8")
    monkeypatch.setattr(stack_mode_mod, "probe_runtime_mode", lambda *_a, **_k: None)
    monkeypatch.setenv("AUTOBRIDGE_RUNTIME", "local")
    assert is_local_stack(root=root) is True


def test_marker_with_live_pid_is_local_without_probe(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_root(tmp_path, with_marker=True)
    (root / ".local" / "pids" / "control.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")

    def _boom(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("probe must not run while pids are alive")

    monkeypatch.setattr(stack_mode_mod, "probe_runtime_mode", _boom)
    det = detect_stack_mode(root=root)
    assert det.mode == "local" and det.stale_marker is False


def test_marker_with_dead_pids_and_no_probe_is_stale(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _make_root(tmp_path, with_marker=True)
    (root / ".local" / "pids" / "control.pid").write_text("999999999\n", encoding="utf-8")
    monkeypatch.setattr(stack_mode_mod, "probe_runtime_mode", lambda *_a, **_k: None)
    det = detect_stack_mode(root=root)
    assert det.mode == "compose" and det.stale_marker is True
    assert ".local/ACTIVE" in det.detail
    assert is_local_stack(root=root) is False
    assert ".local/ACTIVE" in capsys.readouterr().err
    # Warned once per process: the second call stays silent.
    assert is_local_stack(root=root) is False
    assert capsys.readouterr().err == ""


def test_marker_with_dead_pids_but_local_readyz_is_local(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_root(tmp_path, with_marker=True)
    (root / ".local" / "pids" / "control.pid").write_text("999999999\n", encoding="utf-8")
    monkeypatch.setattr(stack_mode_mod, "probe_runtime_mode", lambda *_a, **_k: "local")
    assert is_local_stack(root=root) is True


def test_marker_with_dead_pids_and_docker_readyz_is_stale(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_root(tmp_path, with_marker=True)
    (root / ".local" / "pids" / "control.pid").write_text("999999999\n", encoding="utf-8")
    monkeypatch.setattr(stack_mode_mod, "probe_runtime_mode", lambda *_a, **_k: "docker")
    det = detect_stack_mode(root=root)
    assert det.mode == "compose" and det.stale_marker is True
    assert "docker" in det.detail


def test_recorded_pids_skips_garbage(tmp_path: pathlib.Path) -> None:
    pids = tmp_path / ".local" / "pids"
    pids.mkdir(parents=True)
    (pids / "a.pid").write_text("not-a-pid\n", encoding="utf-8")
    (pids / "b.pid").write_text("\n", encoding="utf-8")
    (pids / "c.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
    assert recorded_pids(tmp_path) == [os.getpid()]
    assert recorded_pids(tmp_path / "missing") == []


def test_pid_alive_self_and_reaped_child() -> None:
    assert pid_alive(os.getpid()) is True
    child = subprocess.Popen(["true"])  # noqa: S603,S607
    child.wait()
    assert pid_alive(child.pid) is False
    assert pid_alive(999999999) is False


class _ReadyzHandler(BaseHTTPRequestHandler):
    payload: bytes = b"{}"

    def do_GET(self) -> None:  # noqa: N802
        data = type(self).payload
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture()
def readyz_server() -> HTTPServer:
    server = HTTPServer(("127.0.0.1", 0), _ReadyzHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def _server_url(server: HTTPServer) -> str:
    return f"http://127.0.0.1:{server.server_port}"


@pytest.mark.parametrize("runtime_mode", ["local", "docker"])
def test_probe_reads_runtime_mode(readyz_server: HTTPServer, runtime_mode: str) -> None:
    _ReadyzHandler.payload = json.dumps(
        {"status": "ok", "checks": {}, "runtime_mode": runtime_mode}
    ).encode()
    assert probe_runtime_mode(_server_url(readyz_server), timeout_s=2.0) == runtime_mode


@pytest.mark.parametrize(
    "payload",
    [b"{}", b"[1,2]", b"not json", b'{"runtime_mode": " to the moon "}'],
)
def test_probe_rejects_unexpected_bodies(readyz_server: HTTPServer, payload: bytes) -> None:
    _ReadyzHandler.payload = payload
    assert probe_runtime_mode(_server_url(readyz_server), timeout_s=2.0) is None


def test_probe_unreachable_is_none() -> None:
    assert probe_runtime_mode("http://127.0.0.1:1", timeout_s=1.0) is None


def test_demo_stack_is_local_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(demo_mod, "is_local_stack", lambda **_kwargs: True)
    assert demo_mod._stack_is_local() is True
    monkeypatch.setattr(demo_mod, "is_local_stack", lambda **_kwargs: False)
    assert demo_mod._stack_is_local() is False


def test_conftest_is_local_mode_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    conftest_mod = pytest.importorskip("tests.e2e.conftest")
    monkeypatch.setattr(conftest_mod, "is_local_stack", lambda **_kwargs: True)
    assert conftest_mod.is_local_mode() is True
    monkeypatch.setattr(conftest_mod, "is_local_stack", lambda **_kwargs: False)
    assert conftest_mod.is_local_mode() is False


def test_cli_mode_env_local(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AUTOBRIDGE_RUNTIME", "local")
    assert stack_mode_mod.main(["--mode"]) == 0
    assert capsys.readouterr().out == "local\n"


def test_cli_mode_stale_warns(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _make_root(tmp_path, with_marker=True)
    monkeypatch.setattr(
        stack_mode_mod,
        "detect_stack_mode",
        lambda **_kwargs: StackMode("compose", True, f"stale {root}/.local/ACTIVE: test"),
    )
    assert stack_mode_mod.main(["--mode"]) == 0
    out = capsys.readouterr()
    assert out.out == "compose\n"
    assert ".local/ACTIVE" in out.err


def test_cli_no_args_usage() -> None:
    assert stack_mode_mod.main([]) == 2
