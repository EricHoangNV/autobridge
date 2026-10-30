"""Blank-slate seed contract (REQ-059): reset → zero connectors, readiness checks.

Hermetic: the control plane is faked at ``_control_call`` /
``_control_get_raw`` level, so no stack is needed. The live stack path
is asserted by ``tests/e2e/test_09_demo_seed_break_heal.py``.
"""

from __future__ import annotations

from typing import Any

import bench.demo_seed as seed_mod
from bench.demo_seed import render_text, run_seed


def _clean_stack() -> dict[tuple[str, str], Any]:
    sims = {"simulators": [
        {"id": "sim-atlas", "live": True, "active_scenario": None},
        {"id": "sim-harbor", "live": True, "active_scenario": None},
        {"id": "sim-meridian", "live": True, "active_scenario": None},
        {"id": "sim-fias-pabx", "live": True, "active_scenario": None},
    ]}
    return {
        ("POST", "/demo/scheduler/pause"): {"status": 200, "body": {"paused": True}},
        ("POST", "/demo/reset"): {"status": 200, "body": {"labelled": True}},
        ("POST", "/demo/scheduler/resume"): {"status": 200, "body": {"resumed": True}},
        ("GET", "/connectors"): {"status": 200, "body": []},
        ("GET", "/drift"): {"status": 200, "body": []},
        ("GET", "/repairs"): {"status": 200, "body": []},
        ("GET", "/metrics/model-usage"): {
            "status": 200,
            "body": {"total_calls": 0, "frontier_tokens": 0, "small_tokens": 0},
        },
        ("GET", "/knowledge"): {
            "status": 200,
            "body": {"entries": [], "connectors": [], "decisions": []},
        },
        ("GET", "/demo/simulators"): {"status": 200, "body": sims},
    }


def _install_fake(monkeypatch: Any, responses: dict[tuple[str, str], Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_call(_base_url: str, method: str, path: str, _payload: Any = None) -> dict[str, Any]:
        rec = {"method": method, "path": path}
        hit = responses.get((method, path))
        if hit is None:
            rec["status"] = 404
            rec["body"] = {}
        else:
            rec["status"] = hit["status"]
            rec["body"] = hit["body"]
        calls.append(rec)
        return rec

    def fake_raw(_base_url: str, path: str) -> Any:
        hit = responses.get(("GET", path))
        return hit["body"] if hit is not None else {}

    def fake_knowledge(base_url: str, calls: list[dict[str, Any]]) -> dict[str, Any]:
        calls.append({"method": "GET", "path": "/knowledge", "status": 200,
                      "body": responses[("GET", "/knowledge")]["body"]})
        body = fake_raw(base_url, "/knowledge")
        return dict(body) if isinstance(body, dict) else {}

    monkeypatch.setattr(seed_mod, "_control_call", fake_call)
    monkeypatch.setattr(seed_mod, "_control_get_raw", fake_raw)
    monkeypatch.setattr(seed_mod, "_live_knowledge", fake_knowledge)
    return calls


def test_blank_seed_passes_on_clean_stack(monkeypatch: Any) -> None:
    _install_fake(monkeypatch, _clean_stack())
    report = run_seed("http://control:8101", 0)
    assert report["failed"] == 0, report["control_calls"]
    assert report["connectors"] == []
    end = report["end_state"]
    assert end["connectors"] == []
    assert end["waiting_repairs"] == 0
    assert end["model_usage"] == {"calls": 0, "small_tokens": 0, "frontier_tokens": 0}
    assert end["knowledge_entries"] == 0
    assert sorted(end["readiness"]["simulators"]) == sorted(
        ["sim-atlas", "sim-harbor", "sim-meridian", "sim-fias-pabx"]
    )
    text = render_text(report)
    assert "0 connectors · ready to build" in text


def test_blank_seed_fails_loud_on_leftover_connector(monkeypatch: Any) -> None:
    responses = _clean_stack()
    responses[("GET", "/connectors")] = {
        "status": 200, "body": [{"name": "atlaspms", "id": "c-1"}],
    }
    _install_fake(monkeypatch, responses)
    report = run_seed("http://control:8101", 0)
    assert report["failed"] > 0
    failures = [c for c in report["control_calls"] if c.get("status") == "assertion-failed"]
    assert any("zero connectors" in str(f.get("error", "")) or "zero connectors" in str(f.get("path", ""))
               for f in failures), failures
    assert "0 connectors · ready to build" not in render_text(report)


def test_blank_seed_fails_loud_on_faulted_simulator(monkeypatch: Any) -> None:
    responses = _clean_stack()
    sims = {"simulators": [
        {"id": "sim-atlas", "live": True, "active_scenario": "page_to_cursor"},
        {"id": "sim-harbor", "live": True, "active_scenario": None},
    ]}
    responses[("GET", "/demo/simulators")] = {"status": 200, "body": sims}
    _install_fake(monkeypatch, responses)
    report = run_seed("http://control:8101", 0)
    assert report["failed"] > 0
    failures = [c for c in report["control_calls"] if c.get("status") == "assertion-failed"]
    assert any("fault active" in str(f.get("error", "")) for f in failures), failures


def test_blank_seed_fails_loud_on_simulator_down(monkeypatch: Any) -> None:
    responses = _clean_stack()
    sims = {"simulators": [
        {"id": "sim-atlas", "live": False, "active_scenario": None},
    ]}
    responses[("GET", "/demo/simulators")] = {"status": 200, "body": sims}
    _install_fake(monkeypatch, responses)
    report = run_seed("http://control:8101", 0)
    assert report["failed"] > 0


def test_seed_builds_nothing_by_design() -> None:
    assert seed_mod.SEED_CONNECTORS == ()
    assert seed_mod.SEED_NAMES == ()
