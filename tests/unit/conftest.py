"""Unit-test hermeticity: no real DNS lookups (REQ-022 macOS fork-safety fix).

On macOS, a ``socket.getaddrinfo`` call made from a worker thread (e.g. the
docs-URL guard's ``ThreadPoolExecutor`` in
``src/autobridge/control/routes/builds.py``) initialises the system
resolver / CoreFoundation machinery off the main thread; every later
``subprocess``-spawned (``fork`` + ``exec``) child then dies with
``returncode=-11`` before ``exec``.  The minimal repro is
``test_main_full_pipeline_writes_scorecard`` (real ``httpx`` verify call)
+ ``test_validate_docs_url_unit`` (real DNS in a worker thread) followed by
any test that forks (``test_autobridge_dotenv_loader``,
``test_autobridge_ui_client``, ``test_autobridge_edge_sign``).

Unit tests must therefore never perform real hostname resolution: they run
offline and hermetic.  This autouse fixture replaces ``socket.getaddrinfo``
for every test under ``tests/unit/``.  Literal IP hosts (``127.0.0.1``,
``::1`` — used by local TCP tests) and ``None`` (server bind) still
resolve for real; anything else raises immediately *and* is recorded, so
worker-thread trips swallowed by fail-open handlers (``except Exception:
return False``) still fail the test at teardown.

To fix a trip: mock the resolver (e.g.
``autobridge.control.routes.builds._docs_url_resolves_internal``) or the
HTTP layer above it, or use a literal ``127.0.0.1`` URL instead of
``localhost``.  Never set ``OBJC_DISABLE_INITIALIZE_FORK_SAFETY``.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any

import pytest


def _is_literal_host(host: Any) -> bool:
    """True for ``None`` (bind) and numeric IP literals (no DNS needed)."""
    if host is None:
        return True
    if isinstance(host, bytes):
        try:
            host = host.decode("ascii")
        except UnicodeDecodeError:
            return False
    if not isinstance(host, str) or not host:
        return False
    try:
        ipaddress.ip_address(host.strip().strip("[]"))
    except ValueError:
        return False
    return True


@pytest.fixture(autouse=True)
def _no_real_dns(monkeypatch: pytest.MonkeyPatch) -> Any:
    # REQ-029: the verifier gate reads VERIFIER_URL / CONTROL_URL /
    # AUTOBRIDGE_REQUIRE_VERIFIER_GATE from the environment. Scrub them so
    # unit tests are hermetic regardless of the developer's shell (a stale
    # CONTROL_URL would otherwise route the gate at a phantom proxy).
    # Tests that need them set it explicitly via monkeypatch.
    for _var in ("VERIFIER_URL", "CONTROL_URL", "AUTOBRIDGE_REQUIRE_VERIFIER_GATE", "AUTOBRIDGE_CONTROL_TOKEN"):
        monkeypatch.delenv(_var, raising=False)
    trips: list[str] = []
    real_getaddrinfo = socket.getaddrinfo

    def _guarded(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
        if _is_literal_host(host):
            return real_getaddrinfo(host, port, *args, **kwargs)
        label = host.decode("ascii", "replace") if isinstance(host, bytes) else str(host)
        trips.append(label)
        raise AssertionError(
            f"unit test performed real DNS for host {label!r}; "
            "unit tests are offline — mock the resolver "
            "(e.g. autobridge.control.routes.builds._docs_url_resolves_internal) "
            "or the HTTP layer, or use a literal 127.0.0.1 URL"
        )

    monkeypatch.setattr(socket, "getaddrinfo", _guarded)
    yield
    assert not trips, (
        "unit test(s) performed real DNS lookups (macOS fork-safety hazard): "
        + ", ".join(repr(t) for t in trips)
    )
