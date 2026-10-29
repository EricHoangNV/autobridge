"""Demo seed / break / heal journeys (REQ-031 items 1-3) against the live stack.

- seed: ``make demo-reset && make demo-seed`` leaves four baseline-synced
  ``working`` connectors, 0 model tokens, nothing waiting.
- break: ``make demo-break SCENARIO=<id>`` injects exactly one fault (the
  single-call property is unit-tested; here the simulator shows the fault
  active and the break prints its one line).
- heal: ``make demo-heal`` clears the fault while connectors, runs and the
  ledger stay intact.

Faults are always healed in a finally so no state leaks into the next test
(random-order safe). The seed test wipes the database first (it is the
seed's contract); every test creates what it needs.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any

import pytest


def _control(method: str, path: str, payload: Any = None) -> Any:
    from tests.e2e.conftest import control_headers, service_url  # noqa: PLC0415

    data = json.dumps(payload or {}).encode() if payload is not None or method != "GET" else None
    req = urllib.request.Request(
        service_url("control", path), data=data, method=method, headers=control_headers()
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        assert resp.status in (200, 201), f"{method} {path} -> {resp.status}"
        raw = resp.read().decode()
        return json.loads(raw) if raw else {}


def _simulators() -> list[dict[str, Any]]:
    body = _control("GET", "/demo/simulators")
    assert isinstance(body, dict)
    return body.get("simulators", [])


def _active_scenario(sim_id: str) -> str | None:
    for row in _simulators():
        if row.get("id") == sim_id:
            return row.get("active_scenario")
    raise AssertionError(f"simulator {sim_id} not listed")


@pytest.mark.live
@pytest.mark.timeout(600)
def test_seed_end_state(e2e_stack: str) -> None:
    """Seeded stack: four working connectors, 0 tokens, nothing waiting."""
    assert e2e_stack
    from bench.demo_seed import SEED_NAMES, run_seed  # noqa: PLC0415
    from tests.e2e.conftest import service_url  # noqa: PLC0415

    base_url = service_url("control", "")
    report = run_seed(base_url, 0)
    assert report["failed"] == 0, json.dumps(report["control_calls"][-3:], default=str)[:2000]
    assert sorted(c["name"] for c in report["connectors"]) == sorted(SEED_NAMES)
    for conn in report["connectors"]:
        assert conn["baseline_outcome"] == "Complete", conn

    connectors = _control("GET", "/connectors")
    rows = connectors if isinstance(connectors, list) else connectors.get("connectors", [])
    by_name = {r["name"]: r for r in rows}
    for name in SEED_NAMES:
        assert name in by_name, f"seed connector {name} missing: {sorted(by_name)}"
        assert by_name[name]["state"] == "working", by_name[name]
        assert by_name[name]["last_sync_at"], name
    assert _control("GET", "/drift") == []
    assert _control("GET", "/repairs") == []
    usage = _control("GET", "/metrics/model-usage")
    assert usage["total_calls"] == 0, usage
    knowledge = _control("GET", "/knowledge")
    assert knowledge["entries"] == []


@pytest.mark.live
def test_break_only_injects(e2e_stack: str) -> None:
    """demo-break arms one fault; the simulator shows it, nothing else ran."""
    assert e2e_stack
    from bench.demo_break import render_line, run_break  # noqa: PLC0415
    from tests.e2e.conftest import service_url  # noqa: PLC0415

    base_url = service_url("control", "")
    try:
        record = run_break(base_url, "page_to_cursor", None)
        assert record["ok"] is True, record
        assert render_line(record) == "broke AtlasPMS with page_to_cursor — watch Repairs"
        assert _active_scenario("sim-atlas") == "page_to_cursor"
    finally:
        from bench.demo_heal import run_heal  # noqa: PLC0415

        healed = run_heal(base_url, "sim-atlas")
        assert healed["ok"] is True, healed
    assert _active_scenario("sim-atlas") is None


@pytest.mark.live
def test_heal_clears_without_reset(e2e_stack: str) -> None:
    """demo-heal clears the fault; the connector and its rows survive."""
    assert e2e_stack
    from bench.demo_break import run_break  # noqa: PLC0415
    from bench.demo_heal import run_heal  # noqa: PLC0415
    from tests.e2e.conftest import service_url  # noqa: PLC0415

    base_url = service_url("control", "")
    created = _control("POST", "/connectors", {
        "name": "heal-probe",
        "vendor_docs_ref": "simulators/docs/atlas-openapi.yaml",
        "base_url": "http://sim-atlas:8000",
        "credential_ref": "ATLAS_API_KEY",
        "target_entities": ["Reservation"],
    })
    connector_id = created["id"]
    try:
        record = run_break(base_url, "gross_to_net", "harbor")
        assert record["ok"] is True, record
        assert record["target"] == "sim-harbor"
        assert _active_scenario("sim-harbor") == "gross_to_net"
        healed = run_heal(base_url, "sim-harbor")
        assert healed["ok"] is True, healed
        assert _active_scenario("sim-harbor") is None
        # The connector (and the database around it) survived the heal.
        detail = _control("GET", f"/connectors/{connector_id}")
        assert detail["name"] == "heal-probe"
    finally:
        run_heal(base_url, "sim-harbor")


@pytest.mark.live
def test_heal_clears_fias_link_faults(e2e_stack: str) -> None:
    """demo-heal returns the FIAS exchange to baseline (slot + link)."""
    assert e2e_stack
    from bench.demo_break import run_break  # noqa: PLC0415
    from bench.demo_heal import run_heal  # noqa: PLC0415
    from tests.e2e.conftest import (  # noqa: PLC0415
        admin_headers,
        admin_url,
        service_url,
    )

    base_url = service_url("control", "")
    try:
        record = run_break(base_url, "fias_record_layout_change", None)
        assert record["ok"] is True, record
        assert _active_scenario("sim-fias-pabx") == "fias_record_layout_change"
        healed = run_heal(base_url, "sim-fias-pabx")
        assert healed["ok"] is True, healed
        assert _active_scenario("sim-fias-pabx") is None

        # Link-engine faults are disarmed too, not just the shared slot.
        req = urllib.request.Request(
            admin_url("sim-fias-pabx", "/admin/fias/state"), method="GET", headers=admin_headers()
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            state = json.loads(resp.read().decode())
        faults = state.get("faults", state)
        assert faults.get("ps_layout", "baseline") == "baseline", faults
    finally:
        run_heal(base_url, "sim-fias-pabx")
