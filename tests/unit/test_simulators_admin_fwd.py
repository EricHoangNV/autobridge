"""Unit tests for the FIAS admin-port forwarder (REQ-034).

The sidecar is a dumb TCP pipe; these tests run it on loopback against a
local echo target. No Docker, no stack needed.
"""

from __future__ import annotations

import contextlib
import socket
import threading
import time
from typing import TYPE_CHECKING

from simulators.admin_fwd import _self_dial_target, serve

if TYPE_CHECKING:
    import pytest


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _echo_server(port: int, stop: threading.Event) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", port))
        srv.listen(8)
        srv.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except TimeoutError:
                continue
            with conn:
                conn.settimeout(2.0)
                with contextlib.suppress(OSError):
                    while True:
                        data = conn.recv(65536)
                        if not data:
                            break
                        conn.sendall(data)


def _run_pipe(target_port: int) -> int:
    listen = _free_port()
    t = threading.Thread(
        target=serve, args=(listen, "127.0.0.1", target_port), daemon=True
    )
    t.start()
    deadline = time.time() + 5.0
    while time.time() < deadline:
        with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", listen), timeout=0.5):
            return listen
        time.sleep(0.05)
    raise AssertionError("forwarder did not start listening")


def test_pipe_forwards_both_directions() -> None:
    stop = threading.Event()
    target = _free_port()
    echo = threading.Thread(target=_echo_server, args=(target, stop), daemon=True)
    echo.start()
    try:
        listen = _run_pipe(target)
        with socket.create_connection(("127.0.0.1", listen), timeout=5) as c:
            c.settimeout(5.0)
            c.sendall(b"hello-fias-admin")
            assert c.recv(65536) == b"hello-fias-admin"
            c.sendall(b"second-message")
            assert c.recv(65536) == b"second-message"
    finally:
        stop.set()


def test_pipe_forwards_large_payload() -> None:
    stop = threading.Event()
    target = _free_port()
    echo = threading.Thread(target=_echo_server, args=(target, stop), daemon=True)
    echo.start()
    try:
        listen = _run_pipe(target)
        payload = bytes(range(256)) * 1024  # 256 KiB, several chunks
        with socket.create_connection(("127.0.0.1", listen), timeout=10) as c:
            c.settimeout(10.0)
            c.sendall(payload)
            received = bytearray()
            while len(received) < len(payload):
                chunk = c.recv(65536)
                if not chunk:
                    break
                received += chunk
            assert bytes(received) == payload
    finally:
        stop.set()


def test_unreachable_target_closes_client_cleanly() -> None:
    listen = _run_pipe(_free_port())  # nobody listens on the target
    with socket.create_connection(("127.0.0.1", listen), timeout=5) as c:
        c.settimeout(5.0)
        with contextlib.suppress(OSError):
            c.sendall(b"ping")
        # The pipe must not hang: EOF (b"") or reset both prove closure.
        with contextlib.suppress(ConnectionResetError, BrokenPipeError):
            assert c.recv(65536) == b""



def test_self_dial_guard_same_ip_and_port() -> None:
    # Loopback to our own port is always self-dial, whatever the port.
    assert _self_dial_target("127.0.0.1", 18001, 18001) is True


def test_self_dial_guard_allows_peer_port() -> None:
    # Same host, different port: a peer process, not a loop.
    assert _self_dial_target("127.0.0.1", 18001, 18002) is False


def test_self_dial_guard_unresolvable_is_not_self(monkeypatch: pytest.MonkeyPatch) -> None:
    # Hermetic: simulate DNS failure instead of resolving a real name
    # (unit tests never touch the network — tests/unit/conftest.py).
    def _fail(*_args: object, **_kwargs: object) -> object:
        raise OSError("mocked DNS failure")

    monkeypatch.setattr(socket, "getaddrinfo", _fail)
    assert _self_dial_target("unresolvable.invalid", 8001, 8001) is False


def test_serve_refuses_self_target_without_storm() -> None:
    # Target == our own listener: every connection must be refused fast
    # (EOF), never forwarded into ourselves. Five quick connections prove
    # there is no accept-loop amplification.
    listen = _free_port()
    t = threading.Thread(
        target=serve, args=(listen, "127.0.0.1", listen), daemon=True
    )
    t.start()
    deadline = time.time() + 5.0
    while time.time() < deadline:
        with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", listen), timeout=0.5):
            break
        time.sleep(0.05)
    else:
        raise AssertionError("forwarder did not start listening")
    start = time.time()
    for _ in range(5):
        with socket.create_connection(("127.0.0.1", listen), timeout=5) as c:
            c.settimeout(5.0)
            assert c.recv(65536) == b""
    assert time.time() - start < 10.0
