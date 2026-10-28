"""E2E harness — complete for P1.W9.1 (plan §4 W9, §3).

Brings up the Compose stack, waits on every service's ``/readyz``, resets
simulator state before and after each test, and tears down.

Usage:
  make e2e                          # host runs pytest against the stack
  E2E_SKIP_STACK=1 make e2e         # stack already up (CI after ``make up``)
  E2E_KEEP_STACK=1 make e2e         # keep containers after the run
  E2E_TIMEOUT=180 make e2e           # longer wait for slow CI

Markers (REQ-010): every test in this directory is auto-marked at
collection — ``live`` if it takes the ``e2e_stack`` fixture (runs against
the Compose stack), ``inprocess`` otherwise (engine code in-process
against fake transports, green without Docker). ``make e2e`` sets
``AUTOBRIDGE_E2E_REQUIRE_STACK=1`` so live tests fail, not skip, when
the stack is unreachable.

Design (complete):
  - Session fixture ``e2e_stack`` is the single place that touches Docker.
    If Docker is not available the fixture skips, so the suite is green
    on a laptop without Docker and informative in CI.
  - Function fixture ``reset_simulator_state`` (autouse) resets both
    simulators via ``POST /admin/reset`` on the admin ports before and
    after every test. Tests that use the live stack (``e2e_stack`` in
    their fixtures) get a *verified* reset: a failed reset fails the
    test instead of leaking state into the next one. Tests running on
    fakes get a best-effort reset that never fails.
  - Function fixture ``preserve_process_env`` (autouse) snapshots and
    restores ``os.environ`` around every test: several e2e files set
    ``*_BASE_URL`` / ``*_API_KEY`` directly, and without this a random
    order would leak values between tests.
  - Tests are independent: no shared DB state, no ordering, random-order
    safe (``pytest-randomly`` shuffles every run; ``make e2e`` passes
    under at least two different seeds before a workstream is done).
  - Suite runtime budget: full suite under 5 minutes
    (``SUITE_RUNTIME_BUDGET_SECONDS``); ``test_00`` alone under 30s once
    the stack is warm (``WARM_TEST00_BUDGET_SECONDS``). The budget is
    wall-clock on a warm stack, excluding the one-off image build.

Trust boundaries: this file lives in ``tests/e2e`` and never imports
``autobridge`` engine internals; it talks to services only over HTTP.
"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from typing import Final, NoReturn

import pytest

# Host ports mirror compose.yaml (plan §2.1).
SERVICE_PORTS: Final[dict[str, int]] = {
    "control": 8101,
    "worker": 8102,
    "runtime-sandbox": 8103,
    "verifier": 8104,
    "sim-atlas": 8105,
    "sim-harbor": 8106,
    "sim-legacystay": 8111,
    "sim-meridian": 8116,
    "sim-edgepos": 8113,
    "sim-fias-pabx": 8118,
    "edge-worker": 8114,
    "mcp-hotel": 8115,
    "ui": 8107,
    "bench": 8108,
}

# Simulator admin ports (separate listener on admin-net, W2.2).
# Public on 8105/8106/8111/8116 (8000), admin on 8109/8110/8112/8117 (8001);
# sim-fias-pabx on 8118 (8000), admin on 8119 (8001, REQ-022).
SIM_ADMIN_PORTS: Final[dict[str, int]] = {
    "sim-atlas": 8109,
    "sim-harbor": 8110,
    "sim-legacystay": 8112,
    "sim-meridian": 8117,
    "sim-fias-pabx": 8119,
}

# Simulators reset by the per-test fixture (plan §2.8: Atlas + Harbor; P2.S1.1: + LegacyStay; REQ-020: + Meridian; REQ-022: + FIAS PABX).
SIM_SERVICES: Final[tuple[str, str, str, str, str]] = ("sim-atlas", "sim-harbor", "sim-legacystay", "sim-meridian", "sim-fias-pabx")

# Suite runtime budget, wall-clock on a warm stack excluding image build
# (plan §4 W9 passing criteria; also documented in docs/architecture.md §7).
SUITE_RUNTIME_BUDGET_SECONDS: Final[int] = 300
WARM_TEST00_BUDGET_SECONDS: Final[int] = 30

# Simulator admin API (W2.2): ``POST /admin/reset`` restores the pristine
# dataset; ``POST /admin/scenario`` applies a catalog scenario. Both live
# on the admin ports and require the admin-net header (sent by
# ``_http_post_json(..., admin=True)``).


def service_url(service: str, path: str = "/readyz") -> str:
    """Return host URL for *service* and *path*."""
    port = SERVICE_PORTS[service]
    if not path.startswith("/"):
        path = "/" + path
    return f"http://localhost:{port}{path}"


def stack_unreachable(reason: str) -> NoReturn:
    """Skip, or fail when the stack is required (REQ-010).

    Live-marked tests call this whenever the Compose stack (or one of its
    services) is not reachable. Without ``AUTOBRIDGE_E2E_REQUIRE_STACK=1``
    the test skips so the suite stays green on a laptop without Docker;
    with it (``make e2e`` sets it, CI runs ``make up`` first) the test
    fails loudly instead of passing vacuously. Never returns.
    """
    if os.getenv("AUTOBRIDGE_E2E_REQUIRE_STACK") == "1":
        pytest.fail(f"stack required but unreachable: {reason}", pytrace=False)
    pytest.skip(f"{reason} — skipping live test")
    raise AssertionError("unreachable")  # pragma: no cover - skip/fail above always raises


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Auto-mark every ``tests/e2e`` test as ``live`` or ``inprocess`` (REQ-010).

    A test that takes the ``e2e_stack`` fixture runs against the live Compose
    stack → ``live``. Every other test runs engine code in-process against
    fake transports → ``inprocess``. Explicit ``@pytest.mark.live`` /
    ``@pytest.mark.inprocess`` decorators on individual tests agree with
    this rule and serve as documentation.
    """
    for item in items:
        names = getattr(item, "fixturenames", None)
        if not names and hasattr(item, "func"):
            code = getattr(item.func, "__code__", None)
            names = code.co_varnames[: code.co_argcount] if code is not None else ()
        if "e2e_stack" in (names or ()):
            item.add_marker(pytest.mark.live)
        else:
            item.add_marker(pytest.mark.inprocess)


def _http_get_json(url: str, timeout: float = 3.0) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            body = resp.read()
            return json.loads(body)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, json.JSONDecodeError):
        return None


def control_headers() -> dict[str, str]:
    """Headers for control-API calls (REQ-015).

    Forwards ``AUTOBRIDGE_CONTROL_TOKEN`` as ``Authorization: Bearer`` when
    set (non-empty); otherwise returns JSON headers only. Every live
    control-API call in ``tests/e2e`` must use this helper so ``make e2e``
    passes both with no tokens and with all tokens set.
    """
    headers = {"Content-Type": "application/json"}
    # REQ-012: send the control write token when set (server-side env only).
    token = os.environ.get("AUTOBRIDGE_CONTROL_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _http_post_json(url: str, payload: dict, timeout: float = 5.0, admin: bool = False) -> bool:
    try:
        data = json.dumps(payload).encode()
        if admin:
            headers = {"Content-Type": "application/json"}
            headers.update(admin_headers())
        else:
            headers = control_headers()
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def admin_headers() -> dict[str, str]:
    """Headers authorizing simulator admin calls (AR-1-1).

    ``X-Admin-Network`` is only a routing hint; the real credential is
    ``SIM_ADMIN_TOKEN`` (server-side env only, never logged). Sent only
    when configured so header-only dev stacks keep working.
    """
    headers = {"X-Admin-Network": "admin-net"}
    token = os.environ.get("SIM_ADMIN_TOKEN", "")
    if token:
        headers["X-Admin-Token"] = token
    return headers


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


# REQ-017: Docker-free local mode. ``make local-up`` touches
# ``.local/ACTIVE``; tests detect it so live assertions use loopback URLs
# (``sim-*`` Docker DNS names do not resolve without containers) and skip
# container-network probes instead of failing.
REPO_ROOT: Final[pathlib.Path] = pathlib.Path(__file__).resolve().parents[2]


def is_local_mode() -> bool:
    """Return True when the Docker-free local stack is active (REQ-017)."""
    if os.getenv("AUTOBRIDGE_RUNTIME", "").strip().lower() == "local":
        return True
    try:
        return (REPO_ROOT / ".local" / "ACTIVE").is_file()
    except Exception:
        return False


def edge_pos_url() -> str:
    """Public URL of the edge-only sim POS for the current mode (REQ-017).

    Docker DNS (``sim-edgepos:8000``) inside Compose; loopback (``:8113``)
    for ``make local-up``.
    """
    if is_local_mode():
        return "http://localhost:8113"
    return "http://sim-edgepos:8000"


def e2e_timeout_seconds(default: float = 120.0) -> float:
    """Readyz wait seconds for the e2e harness.

    An empty or blank ``E2E_TIMEOUT`` counts as unset and yields the
    default (REQ-014): an empty value from ``.env`` must not crash
    ``float('')`` at collection.
    """
    return float((os.getenv("E2E_TIMEOUT") or "").strip() or str(default))


@pytest.fixture(scope="session")
def e2e_stack() -> str:
    """Ensure the Compose (or local, REQ-017) stack is up and readyz green.

    Fails or skips the suite when no stack is available: skips by default
    so the suite is green on a laptop without Docker, fails when
    ``AUTOBRIDGE_E2E_REQUIRE_STACK=1`` (``make e2e`` sets it) so CI never
    passes vacuously (REQ-010). Respects:
      E2E_SKIP_STACK=1  — do not run ``compose up`` (CI already did ``make up``)
      E2E_KEEP_STACK=1  — do not run ``compose down`` on teardown
      E2E_TIMEOUT       — seconds to wait for readyz (default 120)

    When every service is already readyz green — Compose or ``make
    local-up`` — the fixture reuses it with no Docker involvement, so
    ``make e2e`` runs the live tests against local mode too (REQ-017).
    """
    timeout = e2e_timeout_seconds()

    # A green stack (Compose or local processes) is reused as-is: no Docker
    # needed, nothing torn down (the owner started it).
    if is_stack_up():
        yield "already_up"
        return

    if os.getenv("E2E_SKIP_STACK") == "1":
        # Caller promises the stack is already up; skip if it is not reachable.
        stack_unreachable("E2E_SKIP_STACK=1 but stack is not reachable")
        raise AssertionError("unreachable")  # pragma: no cover - stack_unreachable never returns

    if not _docker_available():
        stack_unreachable("docker not available (run `make up` on a Docker host, or `make local-up`)")

    already_up = is_stack_up()
    brought_up = False
    if not already_up:
        try:
            _compose_up(timeout=timeout)
        except Exception as exc:  # pragma: no cover - sandbox docker compose may fail even when version check passes
            stack_unreachable(f"docker compose up failed (run `make up` on a Docker host): {exc}")
        brought_up = True

    # Wait for every service to report readyz ok (covers UI start_period).
    try:
        wait_for_readyz(timeout=timeout)
    except Exception as exc:  # pragma: no cover - stack may be partially up in sandbox
        if brought_up:
            with contextlib.suppress(Exception):
                _compose_down()
        stack_unreachable(f"stack not readyz within {timeout}s: {exc}")

    yield "managed" if brought_up else "already_up"

    if brought_up and os.getenv("E2E_KEEP_STACK") != "1":
        _compose_down()


@pytest.fixture(autouse=True)
def preserve_process_env() -> None:
    """Snapshot and restore ``os.environ`` around every test.

    Several e2e files assign ``*_BASE_URL`` / ``*_API_KEY`` directly
    instead of via monkeypatch; without this fixture a random order
    leaks those values into unrelated tests. Defined before
    ``reset_simulator_state`` so the restore runs after the post-test
    reset on teardown.
    """
    snapshot = dict(os.environ)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(snapshot)


def _reset_simulators(*, enforce: bool) -> None:
    """Reset both simulators to their pristine dataset.

    Always tries the admin-port ``POST /admin/reset`` (W2.2) with the
    admin-net header, falling back to the public port (single-uvicorn
    mode), plus the control-plane ``POST /demo/inject-fault`` baseline
    path (best-effort). When *enforce* is true (the test runs against
    the live stack) a failed admin reset fails the test instead of
    leaking scenario state; otherwise failures are ignored so
    fake-transport tests stay green without Docker.
    """
    failures: list[str] = []
    for svc in SIM_SERVICES:
        ok = _http_post_json(admin_url(svc, "/admin/reset"), {}, admin=True)
        if not ok:
            ok = _http_post_json(service_url(svc, "/admin/reset"), {})
        if not ok:
            failures.append(svc)
    # Control-plane demo path (W7): best-effort, never enforced.
    for svc in SIM_SERVICES:
        _http_post_json(service_url("control", "/demo/inject-fault"), {"target": svc, "scenario": "baseline"})
    if enforce and failures:
        pytest.fail(f"simulator reset failed for: {', '.join(failures)} — refusing to leak state")


@pytest.fixture(autouse=True)
def reset_simulator_state(request: pytest.FixtureRequest) -> None:
    """Reset simulator state before and after each test.

    A test that requests the ``e2e_stack`` fixture runs against the
    live Compose stack (session fixtures are set up before function
    ones, so if the stack were down this test would already have been
    skipped): its reset is verified. All other tests run on fake
    transports and get a best-effort reset that never fails.
    """
    live = "e2e_stack" in (request.fixturenames or ())
    _reset_simulators(enforce=live)
    yield
    _reset_simulators(enforce=live)
