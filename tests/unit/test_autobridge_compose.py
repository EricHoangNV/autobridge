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
    "ui",
    "bench",
}

EXPECTED_NETWORKS = {"engine-net", "vendor-net", "verifier-net", "admin-net"}

# Plan §2.1 attachment matrix.
EXPECTED_ATTACHMENT = {
    "control": {"engine-net", "verifier-net", "admin-net"},
    "worker": {"engine-net"},
    "runtime-sandbox": {"engine-net", "vendor-net"},
    "verifier": {"verifier-net"},
    "sim-atlas": {"vendor-net", "admin-net"},
    "sim-harbor": {"vendor-net", "admin-net"},
    "ui": {"engine-net"},
    "bench": {"engine-net", "verifier-net"},
}

# service -> in-container port (mirrors compose.yaml)
EXPECTED_PORTS = {
    "control": 8101,
    "worker": 8102,
    "runtime-sandbox": 8103,
    "verifier": 8104,
    "sim-atlas": 8105,
    "sim-harbor": 8106,
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


def test_host_ports_unique(compose: dict) -> None:
    seen: dict[str, str] = {}
    for svc, cfg in compose["services"].items():
        for mapping in cfg.get("ports", []):
            host = str(mapping).split(":")[0]
            assert host not in seen, f"host port {host} shared by {seen[host]} and {svc}"
            seen[host] = svc
    for svc, port in EXPECTED_PORTS.items():
        mappings = [str(m) for m in compose["services"][svc].get("ports", [])]
        assert any(m.startswith(f"{port}:") for m in mappings), f"{svc} missing host port {port}"


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
    for svc in ("sim-atlas", "sim-harbor"):
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
    assert resp.status_code == 200
    # Outside the Compose stack some dependencies are unreachable, so an
    # honest readyz may report "degraded" (plan §3); a forced "ok" is a bug.
    assert resp.json()["status"] in ("ok", "degraded")
    assert "checks" in resp.json()


def test_control_app_health() -> None:
    _check_health("autobridge.control.app")


def test_worker_app_health() -> None:
    _check_health("autobridge.engine.app")


def test_runtime_sandbox_app_health() -> None:
    _check_health("autobridge.runtime.app")


def test_bench_app_health() -> None:
    _check_health("autobridge.bench.app")


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
