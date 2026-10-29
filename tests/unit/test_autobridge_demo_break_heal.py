"""Break/heal drivers + reset sims-only + docs bridge (REQ-031 items 2-4).

- ``make demo-break`` injects exactly one fault (single control call, no
  sync/detect/repair/approve) and prints one line; TARGET resolves through
  the registry, scenarios default per catalogue vendor.
- ``make demo-heal`` clears simulator fault state without touching
  connectors, runs, drift, repairs, knowledge or the ledger.
- ``GET /demo/docs`` serves simulator developers-portal text through the
  demo bridge (registry-derived URL, never caller-supplied).
- The UI drawer's scenario/target lists stay identical to what
  ``make demo-break`` accepts.
"""

from __future__ import annotations

import pathlib
import re
from typing import Any
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from autobridge.control.app import app
from autobridge.control.deps import get_db
from autobridge.control.models import Base, Connector, ModelCall, RepairRun
from autobridge.control.models import session_factory as _session_factory


def _client_for(tmp_path: pathlib.Path) -> tuple[TestClient, Any]:
    engine = create_engine(f"sqlite:///{tmp_path}/break_heal.db")
    Base.metadata.create_all(engine)
    factory = _session_factory(engine)

    def override_db():  # type: ignore[no-untyped-def]
        s = factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_db
    return TestClient(app), factory


# ---------------------------------------------------------------------------
# demo-break: pure request planning
# ---------------------------------------------------------------------------


def test_break_supported_scenarios_equal_catalogue() -> None:
    from bench.demo import CATALOGUE_SCENARIOS
    from bench.demo_break import supported_scenarios

    assert set(supported_scenarios()) == set(CATALOGUE_SCENARIOS)
    assert len(supported_scenarios()) > 20


def test_break_defaults_per_vendor() -> None:
    from bench.demo_break import default_target_for

    assert default_target_for("page_to_cursor") == "sim-atlas"
    assert default_target_for("offset_to_cursor") == "sim-atlas"
    assert default_target_for("meridian_cursor_renamed") == "sim-meridian"
    assert default_target_for("fias_record_layout_change") == "sim-fias-pabx"
    # The semantic recording runs on HarborPMS even though the catalogue
    # vendor is "both".
    assert default_target_for("gross_to_net") == "sim-harbor"
    assert default_target_for("invalid_patch_trap") == "sim-harbor"


def test_break_target_aliases_resolve() -> None:
    from bench.demo_break import resolve_break_target

    assert resolve_break_target("page_to_cursor", "atlas") == "sim-atlas"
    assert resolve_break_target("page_to_cursor", "sim-atlas") == "sim-atlas"
    assert resolve_break_target("gross_to_net", "harbor") == "sim-harbor"
    assert resolve_break_target("page_to_cursor", None) == "sim-atlas"


def test_break_unknown_scenario_or_target_fails_loud() -> None:
    import pytest

    from bench.demo_break import BreakError, resolve_break_target

    with pytest.raises(BreakError, match="unknown SCENARIO"):
        resolve_break_target("no_such_fault", None)
    with pytest.raises(BreakError, match="unknown TARGET"):
        resolve_break_target("page_to_cursor", "no-such-sim")


def test_break_posts_exactly_one_inject_call(monkeypatch) -> None:
    """demo-break never syncs, detects, repairs or approves: one POST, then print."""
    from bench.demo_break import render_line, run_break

    calls: list[tuple[str, str, Any]] = []

    def fake_control_call(_base_url: str, method: str, path: str, payload: Any = None) -> dict[str, Any]:
        calls.append((method, path, payload))
        assert path == "/demo/inject-fault"
        return {"method": method, "path": path, "status": 200,
                "body": {"admin_response": {"scenario_id": payload["scenario_id"], "_http_status": 200}}}

    monkeypatch.setattr("bench.demo_break._control_call", fake_control_call)
    record = run_break("http://localhost:8101", "page_to_cursor", None)
    assert record["ok"] is True
    assert record["target"] == "sim-atlas"
    assert len(calls) == 1, f"demo-break must make exactly one control call, made {len(calls)}"
    assert calls[0][2]["scenario_id"] == "page_to_cursor"
    assert "target" not in calls[0][2] or calls[0][2]["target"] == "sim-atlas"
    line = render_line(record)
    assert line == "broke AtlasPMS with page_to_cursor — watch Repairs"


def test_break_failed_inject_fails_loud(monkeypatch) -> None:
    from bench.demo_break import run_break

    def fake_control_call(_base_url: str, method: str, path: str, _payload: Any = None) -> dict[str, Any]:
        return {"method": method, "path": path, "status": 200,
                "body": {"admin_response": {"error": "[Errno -5] no address"}}}

    monkeypatch.setattr("bench.demo_break._control_call", fake_control_call)
    record = run_break("http://localhost:8101", "page_to_cursor", None)
    assert record["ok"] is False
    assert "FAILED" in __import__("bench.demo_break", fromlist=["render_line"]).render_line(record)


# ---------------------------------------------------------------------------
# demo-heal: sims-only reset
# ---------------------------------------------------------------------------


def _seed_db(factory: Any) -> str:
    with factory() as s:
        c = Connector(
            name="heal-kept",
            vendor_docs_ref="simulators/docs/atlas-openapi.yaml",
            base_url="http://localhost:8105",
            credential_ref="ATLAS_API_KEY",
            target_entities=["Reservation"],
        )
        s.add(c)
        s.flush()
        s.add(ModelCall(run_id="r1", provider="p", model="m", tier="small",
                        input_tokens=1, output_tokens=1, purpose="probe"))
        s.add(RepairRun(drift_event_ids=[], ladder_level_reached=1, vendor="atlaspms",
                        drift_class="PAGINATION_CHANGE", rule_id="page_to_cursor",
                        decision="propose", model_usage={}))
        s.commit()
        return c.id


def _mock_admin_ok() -> Any:
    mock_resp = Mock()
    mock_resp.status_code = 200
    mock_resp.content = b'{"ok": true}'
    mock_resp.json.return_value = {"ok": True}
    mock_resp.text = '{"ok": true}'
    return mock_resp


def test_heal_resolve_targets() -> None:
    from bench.demo_heal import resolve_heal_targets

    assert resolve_heal_targets(None) is None
    assert resolve_heal_targets("") is None
    assert resolve_heal_targets("harbor") == ["sim-harbor"]
    assert resolve_heal_targets("sim-fias-pabx") == ["sim-fias-pabx"]
    import pytest

    with pytest.raises(ValueError, match="unknown TARGET"):
        resolve_heal_targets("no-such-sim")


def test_reset_sims_only_keeps_everything_but_sims(tmp_path: pathlib.Path, monkeypatch) -> None:
    """POST /demo/reset {"sims_only": true} clears sim slots, keeps DB rows."""
    monkeypatch.setenv("SIMULATOR_ADMIN_URL", "http://mocked:9999")
    client, factory = _client_for(tmp_path)
    connector_id = _seed_db(factory)
    with patch("httpx.Client") as mock_c:
        mock_c.return_value.__enter__.return_value.post.return_value = _mock_admin_ok()
        r = client.post("/demo/reset", json={"sims_only": True, "targets": ["sim-atlas"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sims_only"] is True
    assert [e["target"] for e in body["resets"]] == ["sim-atlas"]
    # Nothing in the database was touched.
    with factory() as s:
        assert s.get(Connector, connector_id) is not None
        assert s.query(ModelCall).count() == 1
        assert s.query(RepairRun).count() == 1
    app.dependency_overrides.clear()
    monkeypatch.delenv("SIMULATOR_ADMIN_URL", raising=False)


def test_reset_sims_only_unknown_target_422(tmp_path: pathlib.Path, monkeypatch) -> None:
    monkeypatch.setenv("SIMULATOR_ADMIN_URL", "http://mocked:9999")
    client, _ = _client_for(tmp_path)
    r = client.post("/demo/reset", json={"sims_only": True, "targets": ["no-such-sim"]})
    assert r.status_code == 422
    app.dependency_overrides.clear()
    monkeypatch.delenv("SIMULATOR_ADMIN_URL", raising=False)


def test_full_reset_still_wipes_db_by_default(tmp_path: pathlib.Path, monkeypatch) -> None:
    """Empty body keeps the historical full reset (backwards compatible)."""
    monkeypatch.setenv("SIMULATOR_ADMIN_URL", "http://mocked:9999")
    client, factory = _client_for(tmp_path)
    connector_id = _seed_db(factory)
    with patch("httpx.Client") as mock_c:
        mock_c.return_value.__enter__.return_value.post.return_value = _mock_admin_ok()
        r = client.post("/demo/reset", json={})
    assert r.status_code == 200, r.text
    assert r.json().get("sims_only", False) is False
    with factory() as s:
        assert s.get(Connector, connector_id) is None
        assert s.query(ModelCall).count() == 0
    app.dependency_overrides.clear()
    monkeypatch.delenv("SIMULATOR_ADMIN_URL", raising=False)


# ---------------------------------------------------------------------------
# GET /demo/docs bridge
# ---------------------------------------------------------------------------


def test_demo_docs_serves_registry_target(tmp_path: pathlib.Path) -> None:
    client, _ = _client_for(tmp_path)

    class GetResp:
        status_code = 200
        text = "# HarborPMS API"

        def raise_for_status(self) -> None:
            return None

    with patch("httpx.Client") as mock_c:
        mock_c.return_value.__enter__.return_value.get.return_value = GetResp()
        r = client.get("/demo/docs", params={"target": "harbor"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["target"] == "sim-harbor"
    assert body["docs_text"] == "# HarborPMS API"
    assert body["characters"] == len("# HarborPMS API")
    assert body["docs_source"] == "simulators/docs/harbor.md"
    app.dependency_overrides.clear()


def test_demo_docs_unknown_target_404(tmp_path: pathlib.Path) -> None:
    client, _ = _client_for(tmp_path)
    r = client.get("/demo/docs", params={"target": "no-such-sim"})
    assert r.status_code == 404
    app.dependency_overrides.clear()


def test_demo_docs_unreachable_sim_502(tmp_path: pathlib.Path) -> None:
    client, _ = _client_for(tmp_path)
    with patch("httpx.Client") as mock_c:
        mock_c.return_value.__enter__.return_value.get.side_effect = RuntimeError("down")
        r = client.get("/demo/docs", params={"target": "sim-atlas"})
    assert r.status_code == 502
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Drawer parity: the UI offers exactly the break list
# ---------------------------------------------------------------------------


def _drawer_options() -> tuple[list[str], list[str]]:
    text = (pathlib.Path(__file__).resolve().parents[2] / "ui" / "components" / "InjectFaultDrawer.tsx").read_text(encoding="utf-8")
    scenarios = re.search(r"const SCENARIO_OPTIONS = \[(.*?)\];", text, re.DOTALL)
    targets = re.search(r"const TARGET_OPTIONS = \[(.*?)\];", text, re.DOTALL)
    assert scenarios and targets, "drawer option lists not found"
    scen = re.findall(r'"([a-z0-9_]+)"', scenarios.group(1))
    targs = re.findall(r'value: "([^"]+)"', targets.group(1))
    return scen, targs


def test_drawer_scenarios_match_break_list() -> None:
    from bench.demo import CATALOGUE_SCENARIOS

    scen, _ = _drawer_options()
    assert "baseline" in scen, "drawer keeps the baseline clear-slot entry"
    assert set(s for s in scen if s != "baseline") == set(CATALOGUE_SCENARIOS), (
        set(s for s in scen if s != "baseline") ^ set(CATALOGUE_SCENARIOS)
    )


def test_drawer_targets_are_canonical_registry_ids() -> None:
    import yaml

    _, targs = _drawer_options()
    doc = yaml.safe_load(
        (pathlib.Path(__file__).resolve().parents[2] / "config" / "demo_targets.yaml").read_text(encoding="utf-8")
    )
    canonical = [t["id"] for t in doc["targets"]]
    assert targs == canonical
