"""Compose stack tests (P1.W1.2): services, networks and trust boundaries.

Parses compose.yaml statically (no Docker needed) and checks the live stub
apps' /healthz + /readyz via TestClient. The runtime negative checks
(sandbox -/-> verifier, sandbox -/-> sim admin, ui -/-> sims) run in
scripts/smoke.sh against the live stack; here we assert the static
precondition: the network attachments that make those checks pass.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "compose.yaml"

EXPECTED_SERVICES = {
    "control",
    "worker",
    "runtime-sandbox",
    "verifier",
    "sim-atlas",
    "sim-harbor",
    "sim-legacystay",
    "sim-edgepos",
    "edge-worker",
    "mcp-hotel",
    "ui",
    "bench",
}

EXPECTED_NETWORKS = {"engine-net", "vendor-net", "verifier-net", "admin-net", "edge-net"}

# Plan §2.1 attachment matrix (+ §5 S2 edge-net).
EXPECTED_ATTACHMENT = {
    "control": {"engine-net", "verifier-net", "admin-net"},
    "worker": {"engine-net"},
    "runtime-sandbox": {"engine-net", "vendor-net"},
    "verifier": {"verifier-net"},
    "sim-atlas": {"vendor-net", "admin-net"},
    "sim-harbor": {"vendor-net", "admin-net"},
    "sim-legacystay": {"vendor-net", "admin-net"},
    "sim-edgepos": {"edge-net"},
    "edge-worker": {"edge-net"},
    "mcp-hotel": {"engine-net"},
    "ui": {"engine-net"},
    "bench": {"engine-net", "verifier-net"},
}

# REQ-011: every published port binds loopback by default, with an opt-in
# override for LAN access. Container-to-container traffic is unchanged.
BIND_HOST_VAR = "${AUTOBRIDGE_BIND_HOST:-127.0.0.1}"


def _split_published_port(mapping: object) -> tuple[str, str, str]:
    """Split a compose short-syntax port mapping into (bind, host port, container port).

    Handles the loopback-bound form `"${AUTOBRIDGE_BIND_HOST:-127.0.0.1}:8101:8000"`,
    whose `${...}` interpolation itself contains a colon.
    """
    text = str(mapping)
    if text.startswith("${"):
        end = text.index("}")
        rest = text[end + 1 :]
        assert rest.startswith(":"), f"bad port mapping {text!r}"
        host_port, container_port = rest[1:].split(":")
        return text[: end + 1], host_port, container_port
    parts = text.split(":")
    assert len(parts) == 3, f"port mapping {text!r} must bind a host IP (bind:host:container)"
    host_ip, host_port, container_port = parts
    return host_ip, host_port, container_port


# service -> host port (mirrors compose.yaml)
EXPECTED_PORTS = {
    "control": 8101,
    "worker": 8102,
    "runtime-sandbox": 8103,
    "verifier": 8104,
    "sim-atlas": 8105,
    "sim-harbor": 8106,
    "sim-legacystay": 8111,
    "sim-edgepos": 8113,
    "edge-worker": 8114,
    "mcp-hotel": 8115,
    "ui": 8107,
    "bench": 8108,
}


@pytest.fixture(scope="module")
def compose() -> dict:
    with open(COMPOSE_FILE, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, dict)
    return data


def test_compose_file_exists() -> None:
    assert COMPOSE_FILE.is_file()


def test_all_services_present(compose: dict) -> None:
    assert set(compose["services"]) == EXPECTED_SERVICES


def test_all_networks_present(compose: dict) -> None:
    assert set(compose["networks"]) == EXPECTED_NETWORKS


def test_no_network_is_internal(compose: dict) -> None:
    # `internal: true` silently drops host-port publishing (and, on an
    # in-place internal->bridge conversion, service DNS aliases), so the
    # verifier's readyz would be unreachable from the host. Isolation is
    # by attachment only.
    for name, cfg in compose["networks"].items():
        assert not (cfg or {}).get("internal"), f"{name} must not be internal"


def test_network_attachments(compose: dict) -> None:
    for svc, want in EXPECTED_ATTACHMENT.items():
        got = set(compose["services"][svc]["networks"])
        assert got == want, f"{svc}: {got} != {want}"


def test_sandbox_has_no_verifier_or_admin_route(compose: dict) -> None:
    nets = set(compose["services"]["runtime-sandbox"]["networks"])
    assert nets.isdisjoint({"verifier-net", "admin-net"})


def test_ui_is_engine_net_only(compose: dict) -> None:
    assert set(compose["services"]["ui"]["networks"]) == {"engine-net"}


def test_worker_is_engine_net_only(compose: dict) -> None:
    assert set(compose["services"]["worker"]["networks"]) == {"engine-net"}


def test_edge_network_isolated(compose: dict) -> None:
    """P2.S2: sim-edgepos lives only on edge-net; no other service joins it."""
    assert set(compose["services"]["sim-edgepos"]["networks"]) == {"edge-net"}
    assert set(compose["services"]["edge-worker"]["networks"]) == {"edge-net"}
    for svc in ("control", "worker", "runtime-sandbox", "verifier", "ui", "bench", "mcp-hotel"):
        assert "edge-net" not in set(compose["services"][svc]["networks"]), svc
    for svc in ("sim-atlas", "sim-harbor", "sim-legacystay"):
        assert "edge-net" not in set(compose["services"][svc]["networks"]), svc


def test_host_ports_unique(compose: dict) -> None:
    seen: dict[str, str] = {}
    for svc, cfg in compose["services"].items():
        for mapping in cfg.get("ports", []):
            _, host_port, _ = _split_published_port(mapping)
            assert host_port not in seen, f"host port {host_port} shared by {seen[host_port]} and {svc}"
            seen[host_port] = svc
    for svc, port in EXPECTED_PORTS.items():
        mappings = [str(m) for m in compose["services"][svc].get("ports", [])]
        assert any(
            _split_published_port(m)[1] == str(port) for m in mappings
        ), f"{svc} missing host port {port}"


def test_published_ports_bind_loopback(compose: dict) -> None:
    """REQ-011: every published port binds loopback, never all interfaces.

    Fails if any mapping lacks a host IP (`"8101:8000"`) or binds `0.0.0.0`
    literally. The `${AUTOBRIDGE_BIND_HOST:-127.0.0.1}` override keeps LAN
    access opt-in; its default must stay loopback.
    """
    for svc, cfg in compose["services"].items():
        for mapping in cfg.get("ports", []):
            bind, _, _ = _split_published_port(mapping)
            assert "0.0.0.0" not in bind, f"{svc}: port {mapping!r} binds all interfaces"
            if bind.startswith("${"):
                assert bind == BIND_HOST_VAR, (
                    f"{svc}: port {mapping!r} must use {BIND_HOST_VAR}"
                )
            else:
                assert bind in ("127.0.0.1", "localhost"), (
                    f"{svc}: port {mapping!r} must bind loopback, got {bind!r}"
                )


def test_trust_boundary_ports_ignore_bind_override(compose: dict) -> None:
    """AR-1-4 residual: verifier + simulator admin ports never follow the
    AUTOBRIDGE_BIND_HOST override — they stay literal loopback even when
    the rest of the stack is LAN-exposed (e.g. AUTOBRIDGE_BIND_HOST=0.0.0.0).

    The verifier is the root of every acceptance decision and the admin
    APIs mutate simulator state: neither may be re-published to the LAN by
    the same switch that exposes the control API/UI for a demo.
    """
    pinned = {
        ("verifier", "8104"),
        ("sim-atlas", "8109"),
        ("sim-harbor", "8110"),
        ("sim-legacystay", "8112"),
    }
    for svc, port in sorted(pinned):
        mappings = [str(m) for m in compose["services"][svc].get("ports", [])]
        match = [m for m in mappings if _split_published_port(m)[1] == port]
        assert match, f"{svc} missing host port {port}"
        for m in match:
            bind, _, _ = _split_published_port(m)
            assert bind in ("127.0.0.1", "localhost"), (
                f"{svc}: port {m!r} must stay literal loopback and never use "
                f"{BIND_HOST_VAR}"
            )


def test_every_service_has_healthcheck(compose: dict) -> None:
    for svc, cfg in compose["services"].items():
        assert "healthcheck" in cfg, f"{svc} has no healthcheck"


def test_verifier_build_context_excludes_engine(compose: dict) -> None:
    # P1.W9.1: the verifier image builds from the repo root (it needs the
    # simulators package), but its Dockerfile must copy ONLY verifier/ and
    # simulators/ — src/autobridge (engine) physically cannot leak in.
    build = compose["services"]["verifier"]["build"]
    assert build["context"] in (".", "./")
    dockerfile = ROOT / build["dockerfile"]
    assert dockerfile.is_file(), f"missing {dockerfile}"
    text = dockerfile.read_text(encoding="utf-8")
    assert "COPY verifier/" in text, "verifier image must copy the verifier package"
    assert "COPY simulators/" in text, "verifier image must copy the simulators package"
    assert "COPY src" not in text, "verifier image must never copy engine src/"
    assert "COPY . " not in text and "COPY ./ " not in text, "verifier image must not blanket-copy the context"


def test_sim_build_context_is_simulators_only(compose: dict) -> None:
    for svc in ("sim-atlas", "sim-harbor", "sim-legacystay", "sim-edgepos"):
        build = compose["services"][svc]["build"]
        assert build["context"] in ("./simulators", "simulators")


def test_verifier_fixtures_mount_readonly(compose: dict) -> None:
    volumes = compose["services"]["verifier"].get("volumes", [])
    assert any("hidden_cases" in str(v) and str(v).endswith(":ro") for v in volumes)


def _check_health(app_module: str) -> None:
    import importlib

    mod = importlib.import_module(app_module)
    client = TestClient(mod.app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    resp = client.get("/readyz")
    # Outside the Compose stack some dependencies are unreachable, so an
    # honest readyz may report "degraded" (plan §3); a forced "ok" is a bug.
    # AR-2-6: the code is honest too — 200 iff ok, 503 iff degraded.
    assert resp.status_code in (200, 503)
    assert resp.json()["status"] in ("ok", "degraded")
    assert (resp.status_code == 200) == (resp.json()["status"] == "ok")
    assert "checks" in resp.json()


def test_control_app_health() -> None:
    _check_health("autobridge.control.app")


def test_worker_app_health() -> None:
    _check_health("autobridge.engine.app")


def test_runtime_sandbox_app_health() -> None:
    _check_health("autobridge.runtime.app")


def test_bench_app_health() -> None:
    _check_health("autobridge.bench.app")


def test_edge_worker_app_health() -> None:
    _check_health("autobridge.edge.app")


def test_mcp_hotel_app_health() -> None:
    """P2.S4.1: the MCP mock serves the same /healthz + /readyz contract."""
    _check_health("mcp_stretch.app")


def test_sim_edgepos_app_health() -> None:
    _check_health("simulators.edgepos.app")


def test_verifier_app_health() -> None:
    _check_health("verifier.app")


def test_smoke_and_wait_scripts_exist_and_executable() -> None:
    for name in ("scripts/smoke.sh", "scripts/wait_readyz.sh"):
        path = ROOT / name
        assert path.is_file(), f"missing {name}"
        assert path.stat().st_mode & 0o111, f"{name} is not executable"


def test_makefile_up_waits_on_readyz() -> None:
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "wait_readyz" in text


def test_makefile_smoke_runs_stack_smoke() -> None:
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "smoke.sh" in text


def test_control_has_default_verifier_url(compose: dict) -> None:
    """AR-1-2: control verifies builds before persisting (has verifier-net route)."""
    env = compose["services"]["control"].get("environment") or {}
    assert env.get("VERIFIER_URL") == "${VERIFIER_URL:-http://verifier:8000}"


def test_worker_has_no_verifier_url(compose: dict) -> None:
    """AR-1-2: worker is engine-net only with no verifier route, so it must
    not be configured with a verifier URL that could never resolve."""
    env = compose["services"]["worker"].get("environment") or {}
    assert "VERIFIER_URL" not in env


@pytest.mark.parametrize("service", ["control", "worker", "edge-worker"])
def test_edge_release_secret_shared(compose: dict, service: str) -> None:
    """AR-2-4: signer (control/worker) and verifier (edge-worker) read the
    same HMAC key; empty in local dev (unkeyed integrity checksums)."""
    env = compose["services"][service].get("environment") or {}
    assert env.get("EDGE_RELEASE_SECRET") == "${EDGE_RELEASE_SECRET:-}"
