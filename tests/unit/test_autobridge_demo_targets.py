"""REQ-022 owner check 2026-10-29: demo target registry lives in data, not code.

The control plane loads ``config/demo_targets.yaml`` at startup; engine
source names no simulator. These tests pin the loader (valid + fail-closed
on bad files, synthetic ids to prove no vendor knowledge is needed), the
one-registry cross-checks (compose/local-up ports must match the YAML),
and the generalization guard at unit level so ``make test`` catches a
regression without the stack.
"""

from __future__ import annotations

import pathlib
import re

import pytest
import yaml

from autobridge.control.demo_targets import (
    DemoTargetsError,
    default_targets_path,
    get_demo_targets,
    known_target_ids,
    load_demo_targets,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _write_registry(tmp_path: pathlib.Path, targets: list[dict]) -> pathlib.Path:
    path = tmp_path / "demo_targets.yaml"
    path.write_text(yaml.safe_dump({"targets": targets}), encoding="utf-8")
    return path


def _entry(id: str, port: int, aliases: list[str] | None = None, extra: list[dict] | None = None) -> dict:
    return {"id": id, "aliases": aliases or [], "host_admin_port": port, "extra_resets": extra or []}


# ---------------------------------------------------------------------------
# Loader: valid files, synthetic ids (no vendor knowledge needed)
# ---------------------------------------------------------------------------


def test_loader_reads_synthetic_registry(tmp_path: pathlib.Path) -> None:
    path = _write_registry(
        tmp_path,
        [
            _entry("sim-probe-a", 8301, ["probe-a", "ProbeA"],
                   [{"suffix": "link", "path": "/admin/link/reset"}]),
            _entry("sim-probe-b", 8302),
        ],
    )
    reg = load_demo_targets(path)
    assert reg.reset_targets == ("sim-probe-a", "sim-probe-b")
    assert reg.alias_map["probea"] == "sim-probe-a"
    assert reg.alias_map["sim-probe-b"] == "sim-probe-b"
    assert reg.host_admin_ports == {"sim-probe-a": 8301, "sim-probe-b": 8302}
    assert reg.extra_resets == {"sim-probe-a": (("link", "/admin/link/reset"),)}
    assert known_target_ids(reg)[:2] == ["sim-probe-a", "sim-probe-b"]


def test_loader_default_path_is_repo_config() -> None:
    assert default_targets_path() == REPO_ROOT / "config" / "demo_targets.yaml"
    reg = get_demo_targets()
    assert len(reg.reset_targets) == 5
    assert set(reg.reset_targets) == set(reg.host_admin_ports)


def test_loader_env_override(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_registry(tmp_path, [_entry("sim-only", 8401)])
    monkeypatch.setenv("DEMO_TARGETS_FILE", str(path))
    assert default_targets_path() == path
    assert load_demo_targets().reset_targets == ("sim-only",)


# ---------------------------------------------------------------------------
# Loader: fail closed on bad files
# ---------------------------------------------------------------------------


def test_loader_missing_file_fails_closed(tmp_path: pathlib.Path) -> None:
    with pytest.raises(DemoTargetsError, match="missing"):
        load_demo_targets(tmp_path / "nope.yaml")


@pytest.mark.parametrize(
    "targets",
    [
        [],
        [{"id": "  ", "aliases": [], "host_admin_port": 8109, "extra_resets": []}],
        [
            _entry("sim-dup", 8109, ["x"]),
            _entry("sim-dup", 8110),
        ],
        [
            _entry("sim-a", 8109, ["x"]),
            _entry("sim-b", 8110, ["X"]),
        ],
        [_entry("sim-a", 0)],
        [_entry("sim-a", 8109, extra=[{"suffix": "s"}])],
        [_entry("sim-a", 8109, extra=[{"suffix": "s", "path": "relative/path"}])],
        ["not-a-mapping"],
    ],
)
def test_loader_invalid_files_fail_closed(tmp_path: pathlib.Path, targets: list) -> None:
    path = _write_registry(tmp_path, targets)  # type: ignore[arg-type]
    with pytest.raises(DemoTargetsError):
        load_demo_targets(path)


# ---------------------------------------------------------------------------
# One registry: compose.yaml and local-up.sh must match the YAML ports
# ---------------------------------------------------------------------------


def _compose_admin_ports() -> dict[str, int]:
    compose = yaml.safe_load((REPO_ROOT / "compose.yaml").read_text(encoding="utf-8"))
    ports: dict[str, int] = {}
    for name, svc in compose["services"].items():
        if not isinstance(name, str) or not name.startswith("sim-"):
            continue
        for published in (svc or {}).get("ports") or []:
            m = re.fullmatch(r'"?127\.0\.0\.1:(\d+):8001"?', str(published).strip())
            if m:
                ports[name] = int(m.group(1))
    return ports


def _local_up_admin_ports() -> dict[str, int]:
    text = (REPO_ROOT / "scripts" / "local-up.sh").read_text(encoding="utf-8")
    ports: dict[str, int] = {}
    for svc in re.finditer(r"start_svc\s+(sim-[\w-]+)", text):
        head = text[: svc.start()]
        port = re.findall(r"SIM_ADMIN_PORT=(\d+)", head)
        if port:
            ports[svc.group(1)] = int(port[-1])
    return ports


def test_registry_ports_match_compose_and_local_up() -> None:
    """config/demo_targets.yaml is the single source: infra must agree."""
    reg = get_demo_targets()
    assert _compose_admin_ports() == dict(reg.host_admin_ports)
    assert _local_up_admin_ports() == dict(reg.host_admin_ports)


def _compose_public_ports() -> dict[str, int]:
    compose = yaml.safe_load((REPO_ROOT / "compose.yaml").read_text(encoding="utf-8"))
    ports: dict[str, int] = {}
    for name, svc in compose["services"].items():
        if not isinstance(name, str) or not name.startswith("sim-"):
            continue
        for published in (svc or {}).get("ports") or []:
            m = re.fullmatch(r'"?\$\{AUTOBRIDGE_BIND_HOST:-[^}]*\}:(\d+):8000"?', str(published).strip())
            if m:
                ports[name] = int(m.group(1))
    return ports


def _local_up_public_ports() -> dict[str, int]:
    text = (REPO_ROOT / "scripts" / "local-up.sh").read_text(encoding="utf-8")
    ports: dict[str, int] = {}
    for svc in re.finditer(r"start_svc\s+(sim-[\w-]+)", text):
        head = text[: svc.start()]
        port = re.findall(r"SIM_PUBLIC_PORT=(\d+)", head)
        if port:
            ports[svc.group(1)] = int(port[-1])
    return ports


def test_registry_public_ports_match_compose_and_local_up() -> None:
    """Display public ports are registry data too: infra must agree."""
    reg = get_demo_targets()
    expected = {t.id: t.public_port for t in reg.targets}
    assert _compose_public_ports() == expected
    assert _local_up_public_ports() == expected


def test_registry_display_metadata_present_for_every_target() -> None:
    """The simulators index renders from the registry: no blank display rows."""
    for target in get_demo_targets().targets:
        assert target.display_name, f"{target.id}: display_name missing"
        assert target.api_style, f"{target.id}: api_style missing"
        assert target.docs_source, f"{target.id}: docs_source missing"
        assert (REPO_ROOT / target.docs_source).exists(), f"{target.id}: {target.docs_source} missing"


def test_loader_display_fields_optional_but_validated(tmp_path: pathlib.Path) -> None:
    full = _entry("sim-full", 8301)
    full.update(
        {
            "display_name": "Probe",
            "api_style": "REST",
            "public_port": 8300,
            "docs_source": "simulators/docs/probe.md",
        }
    )
    reg = load_demo_targets(_write_registry(tmp_path, [full, _entry("sim-bare", 8302)]))
    assert reg.targets[0].display_name == "Probe"
    assert reg.targets[0].public_port == 8300
    assert reg.targets[1].display_name == ""
    assert reg.targets[1].public_port == 0
    bad = _entry("sim-bad", 8303)
    bad["public_port"] = "not-a-port"  # type: ignore[dict-item]
    with pytest.raises(DemoTargetsError):
        load_demo_targets(_write_registry(tmp_path, [bad]))


# ---------------------------------------------------------------------------
# Generalization guard at unit level (mirrors the e2e proof, no stack needed)
# ---------------------------------------------------------------------------


def test_no_vendor_literal_in_engine_source() -> None:
    """Same assertion as test_no_legacystay_specific_rule_or_engine_code,
    runnable under ``make test`` so a regression fails before e2e."""
    offenders = [
        str(p)
        for p in (REPO_ROOT / "src" / "autobridge").rglob("*.py")
        if "legacystay" in p.read_text(encoding="utf-8").lower()
        or "legacy-stay" in p.read_text(encoding="utf-8").lower()
        or "legacy_stay" in p.read_text(encoding="utf-8").lower()
    ]
    assert offenders == []
