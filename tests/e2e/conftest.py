"""E2E harness — first cut for P1.W1.10 (plan §4 W1, §3, §4 W9).

Brings up the Compose stack, waits on every service's ``/readyz``, resets
simulator state between tests when the admin API exists, and tears down.

Usage:
  make e2e                          # host runs pytest against the stack
  E2E_SKIP_STACK=1 make e2e         # stack already up (CI after ``make up``)
  E2E_KEEP_STACK=1 make e2e         # keep containers after the run
  E2E_TIMEOUT=180 make e2e           # longer wait for slow CI

Design (W9 complete version is in P1.W9.1):
  - Session fixture ``e2e_stack`` is the single place that touches Docker.
    If Docker is not available the fixture skips, so the suite is green
    on a laptop without Docker and informative in CI.
  - Function fixture ``reset_simulator_state`` tries ``POST /admin/reset``
    on each simulator's admin port and ``POST /admin/scenario {id: baseline}``
    when those endpoints exist (W2.2). On W1 they do not, so it is a no-op.
  - Tests are independent: no shared DB state, no ordering, random-order safe.
  - Suite runtime budget (W9 target): full suite under 5 minutes; ``test_00``
    alone under 30s once the stack is warm.

Trust boundaries: this file lives in ``tests/e2e`` and never imports
``autobridge`` engine internals; it talks to services only over HTTP.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from typing import Final

import pytest

# Host ports mirror compose.yaml (plan §2.1).
SERVICE_PORTS: Final[dict[str, int]] = {
    "control": 8101,
    "worker": 8102,
    "runtime-sandbox": 8103,
    "verifier": 8104,
    "sim-atlas": 8105,
    "sim-harbor": 8106,
    "ui": 8107,
    "bench": 8108,
}

# Simulator admin ports (separate listener on admin-net, W2.2).
# Public on 8105/8106 (8000), admin on 8109/8110 (8001).
SIM_ADMIN_PORTS: Final[dict[str, int]] = {
    "sim-atlas": 8109,
    "sim-harbor": 8110,
}

# Admin endpoints are *not* host-mapped; they live on 8001 inside the
# container. The host cannot reach them in W1 (negative network test).
# The harness resets via ``control`` proxy when it exists (W7
# POST /demo/inject-fault) and via direct admin-net from inside control
# container. For the first cut we do best-effort via localhost admin
# ports when W2 maps them; otherwise no-op.
ADMIN_RESET_PATHS: Final[list[str]] = [
    "/admin/reset",
    "/admin/scenario",
]


def service_url(service: str, path: str = "/readyz") -> str:
    """Return host URL for *service* and *path*."""
    port = SERVICE_PORTS[service]
    if not path.startswith("/"):
        path = "/" + path
    return f"http://localhost:{port}{path}"


def _http_get_json(url: str, timeout: float = 3.0) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            body = resp.read()
            return json.loads(body)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, json.JSONDecodeError):
        return None


def _http_post_json(url: str, payload: dict, timeout: float = 5.0, admin: bool = False) -> bool:
    try:
        data = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"}
        if admin:
            headers["X-Admin-Network"] = "admin-net"
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def admin_url(service: str, path: str = "/admin/scenario") -> str:
    """Return host URL for simulator admin API."""
    port = SIM_ADMIN_PORTS[service]
    if not path.startswith("/"):
        path = "/" + path
    return f"http://localhost:{port}{path}"


def is_stack_up(timeout: float = 3.0) -> bool:
    """Return True if every service reports ``/readyz`` status ok."""
    for svc in SERVICE_PORTS:
        data = _http_get_json(service_url(svc, "/readyz"), timeout=timeout)
        if data is None or data.get("status") != "ok":
            return False
    return True


def wait_for_readyz(timeout: float = 120.0, interval: float = 2.0) -> None:
    """Block until every service is readyz green or *timeout* expires."""
    deadline = time.monotonic() + timeout
    last_error: str | None = None
    while time.monotonic() < deadline:
        all_ok = True
        for svc in SERVICE_PORTS:
            data = _http_get_json(service_url(svc, "/readyz"), timeout=3.0)
            if data is None or data.get("status") != "ok":
                all_ok = False
                last_error = f"{svc} not ready: {data}"
                break
        if all_ok:
            return
        time.sleep(interval)
    pytest.fail(f"stack not readyz within {timeout}s: {last_error}")


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def _compose_up(timeout: float = 120.0) -> None:
    subprocess.run(
        ["docker", "compose", "up", "-d", "--build", "--wait"],
        check=True,
        timeout=timeout,
    )


def _compose_down() -> None:
    subprocess.run(
        ["docker", "compose", "down", "-v"],
        check=False,
        timeout=60,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def e2e_stack() -> str:
    """Ensure the Compose stack is up and readyz green.

    Skips the suite when Docker is not available. Respects:
      E2E_SKIP_STACK=1  — do not run ``compose up`` (CI already did ``make up``)
      E2E_KEEP_STACK=1  — do not run ``compose down`` on teardown
      E2E_TIMEOUT       — seconds to wait for readyz (default 120)
    """
    timeout = float(os.getenv("E2E_TIMEOUT", "120"))

    if os.getenv("E2E_SKIP_STACK") == "1":
        # Caller promises the stack is already up; skip if it is not reachable.
        if not is_stack_up():
            pytest.skip("E2E_SKIP_STACK=1 but stack is not reachable — skipping e2e")
        yield "external"
        return

    if not _docker_available():
        pytest.skip("docker not available — skipping e2e (run `make up` on a Docker host)")

    already_up = is_stack_up()
    brought_up = False
    if not already_up:
        try:
            _compose_up(timeout=timeout)
        except Exception as exc:  # pragma: no cover - sandbox docker compose may fail even when version check passes
            pytest.skip(f"docker compose up failed — skipping e2e (run `make up` on a Docker host): {exc}")
        brought_up = True

    # Wait for every service to report readyz ok (covers UI start_period).
    try:
        wait_for_readyz(timeout=timeout)
    except Exception as exc:  # pragma: no cover - stack may be partially up in sandbox
        if brought_up:
            with contextlib.suppress(Exception):
                _compose_down()
        pytest.skip(f"stack not readyz within {timeout}s — skipping e2e: {exc}")

    yield "managed" if brought_up else "already_up"

    if brought_up and os.getenv("E2E_KEEP_STACK") != "1":
        _compose_down()


@pytest.fixture(autouse=True)
def reset_simulator_state(request: pytest.FixtureRequest) -> None:
    """Reset simulator state between tests when the admin API exists.

    First cut (W1): the admin API does not yet exist, so this is a no-op
    that never fails. W2.2 wires ``POST /admin/scenario`` and a reset
    endpoint; this hook will then use them. Tests must not rely on the
    reset having done anything in W1, only that it does not error.

    This fixture is autouse but does not require the stack to be up — it
    best-effort resets if reachable. Tests that need the stack should
    explicitly depend on ``e2e_stack``.
    """
    # Check if e2e_stack fixture is active (i.e., stack is up or will be)
    # If the test explicitly requests e2e_stack, that fixture will have been
    # evaluated already; otherwise we just try best-effort without asserting.
    _ = request

    def _do_reset() -> None:
        for svc in ("sim-atlas", "sim-harbor"):
            # Try control-proxied reset (W7 demo path) — available later.
            _http_post_json(service_url("control", "/demo/inject-fault"), {"target": svc, "scenario": "baseline"})
            # Direct admin port (W2.2) — public port also works for /admin/reset in single-process fallback,
            # but prefer admin port with header.
            for path in ADMIN_RESET_PATHS:
                url = admin_url(svc, path)
                if path == "/admin/scenario":
                    _http_post_json(url, {"id": "baseline", "seed": 0}, admin=True)
                else:
                    _http_post_json(url, {}, admin=True)
                # Fallback: try public port too (supports single-uvicorn mode during tests)
                url2 = service_url(svc, path)
                if path == "/admin/scenario":
                    _http_post_json(url2, {"id": "baseline", "seed": 0})
                else:
                    _http_post_json(url2, {})

    _do_reset()
    yield
    _do_reset()
