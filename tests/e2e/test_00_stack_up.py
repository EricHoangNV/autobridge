"""Stack smoke via the live Compose stack — P1.W1.10.

Every service must expose ``GET /healthz`` (liveness) and ``GET /readyz``
(readiness) per plan §3. ``test_00`` is the workstream-W1 acceptance test
(plan §4 W1 Service confirmation): ``make up`` → all readyz green,
``make smoke`` passes, and this file passes.

When Docker is not available the ``e2e_stack`` fixture skips the suite.
When the stack is already up (``E2E_SKIP_STACK=1`` or CI that ran
``make up`` beforehand) the fixture only waits for readyz.

Tests are independent and order-safe; the suite must pass in random order
(plan §4 W9). No test writes state that another test depends on.
"""

from __future__ import annotations

import json
import pathlib
import urllib.request

import yaml

from tests.e2e.conftest import SERVICE_PORTS, service_url


def _get_json(url: str, timeout: float = 5.0) -> tuple[int, dict]:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        body = json.loads(resp.read())
        assert isinstance(body, dict)
        return resp.status, body


# ---------------------------------------------------------------------------
# Health contracts (plan §3)
# ---------------------------------------------------------------------------


def test_all_services_have_healthz(e2e_stack: str) -> None:
    assert e2e_stack
    for svc in SERVICE_PORTS:
        status, body = _get_json(service_url(svc, "/healthz"))
        assert status == 200, f"{svc} /healthz -> {status}"
        assert body.get("status") == "ok", f"{svc} /healthz body: {body}"


def test_all_services_have_readyz(e2e_stack: str) -> None:
    assert e2e_stack
    for svc in SERVICE_PORTS:
        status, body = _get_json(service_url(svc, "/readyz"))
        assert status == 200, f"{svc} /readyz -> {status}"
        # readyz must be {"status": "ok"|"degraded", "checks": {...}}
        assert body.get("status") in ("ok", "degraded"), f"{svc} /readyz body: {body}"
        assert "checks" in body, f"{svc} /readyz missing checks: {body}"


def test_all_services_readyz_green(e2e_stack: str) -> None:
    """Service confirmation: every service's readyz status is ok (not degraded)."""
    assert e2e_stack
    failures: list[str] = []
    for svc in SERVICE_PORTS:
        _, body = _get_json(service_url(svc, "/readyz"))
        if body.get("status") != "ok":
            failures.append(f"{svc}: {body}")
    assert not failures, "readyz not green:\n" + "\n".join(failures)


def test_readyz_checks_are_all_ok(e2e_stack: str) -> None:
    """Every individual check inside readyz must be ok."""
    assert e2e_stack
    failures: list[str] = []
    for svc in SERVICE_PORTS:
        _, body = _get_json(service_url(svc, "/readyz"))
        checks = body.get("checks", {})
        for name, value in checks.items():
            if value != "ok":
                failures.append(f"{svc} check {name}={value}")
    assert not failures, "failing readyz checks:\n" + "\n".join(failures)


def test_healthz_content_type_is_json(e2e_stack: str) -> None:
    assert e2e_stack
    for svc in SERVICE_PORTS:
        with urllib.request.urlopen(service_url(svc, "/healthz"), timeout=5) as resp:
            ctype = resp.headers.get("Content-Type", "")
            assert "application/json" in ctype, f"{svc} /healthz Content-Type={ctype!r}"


def test_readyz_content_type_is_json(e2e_stack: str) -> None:
    assert e2e_stack
    for svc in SERVICE_PORTS:
        with urllib.request.urlopen(service_url(svc, "/readyz"), timeout=5) as resp:
            ctype = resp.headers.get("Content-Type", "")
            assert "application/json" in ctype, f"{svc} /readyz Content-Type={ctype!r}"


def test_control_readyz_includes_dependency_checks(e2e_stack: str) -> None:
    """Control is the hub; its readyz should report dependency sub-checks.

    First cut: the stub returns {"default": "ok"}; later (W7) it will
    report db/verifier/sandbox/sim reachability. This test asserts the
    response is a valid readyz shape regardless of which checks exist.
    """
    assert e2e_stack
    _, body = _get_json(service_url("control", "/readyz"))
    checks = body["checks"]
    assert isinstance(checks, dict)
    assert len(checks) >= 1
    for key, val in checks.items():
        assert isinstance(key, str) and key
        assert val in ("ok", "fail", "degraded"), f"unexpected check value {key}={val!r}"


def test_service_count_matches_compose(e2e_stack: str) -> None:
    """Guard against a service being added to compose.yaml but not to the harness."""
    assert e2e_stack
    compose_file = pathlib.Path(__file__).resolve().parents[2] / "compose.yaml"
    with open(compose_file, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    expected_services = set(data["services"].keys())
    assert set(SERVICE_PORTS.keys()) == expected_services, (
        f"harness SERVICE_PORTS {set(SERVICE_PORTS)} != compose services {expected_services}"
    )
