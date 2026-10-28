"""REQ-022 owner check after sub-step B: keyed local-up and thread safety.

1. A keyed `.env` without `EDGE_BUNDLE_SIGNATURE` must fail fast with the
   `make edge-sign` hint (not a readyz timeout): `scripts/check-edge-key.sh`
   wired into `make up` and `scripts/local-up.sh`; `make edge-sign` mints
   and pins the signature without printing secrets.
2. Link threads never raise (unhandled thread exceptions poison forked
   macOS children): `FiasHost._serve` contains `LinkDropped`/errors,
   `PabxLink.stop()` joins, HTTP clients use `trust_env=False`, and the
   suite errors on `PytestUnhandledThreadExceptionWarning`.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHECK = ROOT / "scripts" / "check-edge-key.sh"
SIGN = ROOT / "scripts" / "edge-sign.sh"


def _run(script: Path, dotenv: Path, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["DOTENV_PATH"] = str(dotenv)
    # Isolate from the developer's shell for determinism.
    for var in ("EDGE_RELEASE_SECRET", "EDGE_BUNDLE_SIGNATURE"):
        env.pop(var, None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(script)], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120
    )


# ---------------------------------------------------------------------------
# check-edge-key.sh: fail fast only when keyed and unsigned
# ---------------------------------------------------------------------------


def test_check_keyed_without_signature_fails_with_hint(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("EDGE_RELEASE_SECRET=s3cret\n", encoding="utf-8")
    out = _run(CHECK, dotenv)
    assert out.returncode == 1
    assert "make edge-sign" in out.stderr


def test_check_keyed_with_signature_passes(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("EDGE_RELEASE_SECRET=s3cret\nEDGE_BUNDLE_SIGNATURE=abc123\n", encoding="utf-8")
    out = _run(CHECK, dotenv)
    assert out.returncode == 0, out.stderr


def test_check_open_mode_passes(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("", encoding="utf-8")
    out = _run(CHECK, dotenv)
    assert out.returncode == 0, out.stderr


def test_check_explicit_env_wins_over_dotenv(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("EDGE_RELEASE_SECRET=s3cret\n", encoding="utf-8")
    out = _run(CHECK, dotenv, extra_env={"EDGE_BUNDLE_SIGNATURE": "pinned-in-env"})
    assert out.returncode == 0, out.stderr


def test_up_and_local_up_wire_the_check() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "up: edge-check" in makefile
    local_up = (ROOT / "scripts" / "local-up.sh").read_text(encoding="utf-8")
    assert "scripts/check-edge-key.sh" in local_up


# ---------------------------------------------------------------------------
# edge-sign.sh: mint, pin, never print secrets
# ---------------------------------------------------------------------------


def test_edge_sign_creates_dotenv_and_pins_signature(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    out = _run(SIGN, dotenv)
    assert out.returncode == 0, out.stderr
    text = dotenv.read_text(encoding="utf-8")
    m = re.search(r"^EDGE_BUNDLE_SIGNATURE=([0-9a-f]{64})$", text, re.M)
    assert m, text
    # The minted value is never printed (it lives in .env, git-ignored).
    assert m.group(1) not in out.stdout + out.stderr


def test_edge_sign_upserts_and_preserves_other_lines(tmp_path: Path, monkeypatch: Any) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "EDGE_RELEASE_SECRET=s3cret\nEDGE_BUNDLE_SIGNATURE=deadbeef\nOTHER=keepme\n",
        encoding="utf-8",
    )
    out = _run(SIGN, dotenv)
    assert out.returncode == 0, out.stderr
    lines = dotenv.read_text(encoding="utf-8").splitlines()
    assert "OTHER=keepme" in lines
    assert "EDGE_RELEASE_SECRET=s3cret" in lines
    sigs = [ln.split("=", 1)[1] for ln in lines if ln.startswith("EDGE_BUNDLE_SIGNATURE=")]
    assert len(sigs) == 1 and re.fullmatch(r"[0-9a-f]{64}", sigs[0])
    assert "s3cret" not in out.stdout + out.stderr
    assert "keepme" not in out.stdout + out.stderr
    # The pinned signature verifies the bundle it was minted over
    # (with the same secret the script minted with).
    from autobridge.edge.bundle import load_bundle, verify_bundle

    monkeypatch.setenv("EDGE_RELEASE_SECRET", "s3cret")
    verify_bundle(load_bundle(), signature=sigs[0])


def test_edge_sign_mints_with_the_effective_secret(tmp_path: Path, monkeypatch: Any) -> None:
    """A keyed worker must accept the pin: mint with the same secret.

    Regression for the keyed local-up dead end: edge-sign ran unkeyed
    (empty env) while the worker verified keyed, so every link reported
    "signature mismatch". The script sources .env before minting.
    """
    from autobridge.edge.bundle import load_bundle, verify_bundle

    dotenv = tmp_path / ".env"
    dotenv.write_text("EDGE_RELEASE_SECRET=s3cret-keyed\n", encoding="utf-8")
    out = _run(SIGN, dotenv)
    assert out.returncode == 0, out.stderr
    sigs = [
        ln.split("=", 1)[1]
        for ln in dotenv.read_text(encoding="utf-8").splitlines()
        if ln.startswith("EDGE_BUNDLE_SIGNATURE=")
    ]
    assert len(sigs) == 1
    monkeypatch.setenv("EDGE_RELEASE_SECRET", "s3cret-keyed")
    verify_bundle(load_bundle(), signature=sigs[0])
    # ... and the same pin fails under a different secret (fail-closed).
    monkeypatch.setenv("EDGE_RELEASE_SECRET", "another-secret")
    with pytest.raises(Exception, match="mismatch"):
        verify_bundle(load_bundle(), signature=sigs[0])


# ---------------------------------------------------------------------------
# Threads never raise; HTTP clients never consult system proxies
# ---------------------------------------------------------------------------


def test_serve_contains_link_dropped() -> None:
    """A session that ends mid-serve returns; nothing escapes the thread."""
    import time as _time

    from autobridge.edge.fias_host import FiasHost, FrameSplitter, encode_frame
    from autobridge.edge.store import AgentStore

    store = AgentStore(":memory:")
    host = FiasHost(_RecordingSink(store), listen_host="127.0.0.1", listen_port=0,
                    keepalive_interval=100.0, dead_after=1000.0)
    a, b = socket.socketpair()
    try:
        a.settimeout(2.0)
        b.settimeout(2.0)
        thread = threading.Thread(target=host._serve, args=(a, FrameSplitter(65536)), daemon=True)
        thread.start()
        _time.sleep(0.2)
        # All-zero bytes mid-session: resync, i.e. LinkDropped inside the thread.
        b.sendall(b"\x00\x00\x00" + encode_frame("LA", [("DA", "261028")]))
        thread.join(timeout=5.0)
        assert not thread.is_alive()
        assert host.counters["resyncs"] == 1
    finally:
        a.close()
        b.close()
        store.close()


def test_pabx_stop_joins_the_link_thread() -> None:
    """stop() returns only after the link thread ended (no leaked thread)."""
    import time as _time

    from simulators.fias_pabx.pabx import PabxConfig, PabxLink

    link = PabxLink(PabxConfig(host="127.0.0.1", port=1, reconnect_interval=0.05))
    link.start()
    _time.sleep(0.3)  # let it fail connects / redial a few times
    link.stop()
    assert not link.is_alive()


def test_agent_http_client_ignores_system_proxies() -> None:
    from autobridge.edge.poller import RelayClient

    client = RelayClient("http://localhost:8120")._client()
    try:
        assert client.trust_env is False
    finally:
        client.close()


def test_feed_reader_ignores_system_proxies(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from autobridge.runtime.feeds import RelayFeedReader

    seen: dict[str, Any] = {}

    class _Client:
        def __init__(self, *_args: Any, **kwargs: Any) -> None:
            seen.update(kwargs)

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def get(self, _url: str, **_kwargs: Any) -> Any:
            raise AssertionError("no network in this test")

    monkeypatch.setattr(httpx, "Client", _Client)
    reader = RelayFeedReader("http://localhost:8120", "tok")
    with pytest.raises(Exception, match="no network"):
        reader.read({"record": "PS", "via": "edge_relay"}, "feed_ps")
    assert seen.get("trust_env") is False


def test_vendor_http_client_ignores_system_proxies_by_default() -> None:
    from autobridge.runtime.http import HttpClient

    client = HttpClient("http://localhost:8105").client_factory(timeout=1)
    try:
        assert client.trust_env is False
    finally:
        client.close()


class _RecordingSink:
    def __init__(self, store: Any) -> None:
        self.store = store

    def on_record(self, _record: Any) -> str:
        return "OK"
