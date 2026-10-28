# ruff: noqa: TC003
"""Full-reset tests (REQ-021 item 1).

``POST /demo/reset`` must return the system to a known first-run state:
simulators to baseline, the L0 knowledge cache emptied, demo control-plane
rows removed, and the model-usage ledger archived per demo session.
Idempotent, and repeatable: reset → same scenario → reset yields identical
counts.
"""

from __future__ import annotations

import pathlib
from typing import Any
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from autobridge.control.app import app
from autobridge.control.deps import get_db
from autobridge.control.models import (
    Approval,
    Base,
    BuildRun,
    Connector,
    ConnectorRelease,
    DependencyRegistration,
    DiscoveryRecord,
    DriftEvent,
    EvidenceRecord,
    Job,
    ModelCall,
    ModelCallArchive,
    RepairRun,
    SyncRun,
    session_factory,
)
from autobridge.llm.provider import Usage, record_model_call
from autobridge.repair.knowledge import get_default_cache, reset_default_cache


def _client_for(tmp_path: pathlib.Path) -> tuple[TestClient, Any]:
    engine = create_engine(f"sqlite:///{tmp_path}/reset_test.db")
    Base.metadata.create_all(engine)
    factory = session_factory(engine)

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


def _seed_state(factory: Any) -> dict[str, int]:
    """One connector with a release, runs, drift, repair, approval, evidence and a ledger row."""
    from autobridge.repair.knowledge import get_default_cache as _cache

    with factory() as s:
        c = Connector(
            name="Reset demo",
            base_url="http://localhost:8105",
            credential_ref="ATLAS_API_KEY",
            target_entities=["Reservation"],
            runtime_target={"kind": "local_process"},
        )
        s.add(c)
        s.flush()
        rel = ConnectorRelease(
            connector_id=c.id, version=1, spec={"connector": {"vendor": "atlas"}},
            signature="sig",
        )
        s.add(rel)
        s.flush()
        c.active_release_id = rel.id
        build = BuildRun(connector_id=c.id)
        sync = SyncRun(release_id=rel.id, outcome="Complete")
        drift = DriftEvent(connector_id=c.id, drift_class="PAGINATION_CHANGE")
        repair = RepairRun(drift_event_ids=[drift.id], decision="propose")
        s.add_all([build, sync, drift, repair])
        s.flush()
        approval = Approval(repair_run_id=repair.id, actor="tester", decision="approve")
        evidence = EvidenceRecord(kind="decision", payload={"ok": True}, run_id=repair.id)
        call = record_model_call(
            s, run_id=repair.id, provider="fake", model="fake-small",
            tier="small", usage=Usage(10, 20), purpose="ladder-l1",
        )
        dep = DependencyRegistration(
            consumer_id="ui", connector_id=c.id, entity="Reservation",
            policy="notify",
        )
        disc = DiscoveryRecord(
            discovery_id="disc-1", vendor="atlas", candidate_entities=["Reservation"],
        )
        s.add_all([approval, evidence, dep, disc])
        job = Job(kind="sync")
        s.add(job)
        s.commit()
        call_id = call.id
    _cache().put("atlas", {"k": "v"}, [{"op": "replace"}], "page_to_cursor")
    return {"model_call_id": call_id}


def _counts(factory: Any) -> dict[str, int]:
    with factory() as s:
        return {
            "connectors": s.query(Connector).count(),
            "releases": s.query(ConnectorRelease).count(),
            "builds": s.query(BuildRun).count(),
            "syncs": s.query(SyncRun).count(),
            "drifts": s.query(DriftEvent).count(),
            "repairs": s.query(RepairRun).count(),
            "approvals": s.query(Approval).count(),
            "evidence": s.query(EvidenceRecord).count(),
            "calls": s.query(ModelCall).count(),
            "archive": s.query(ModelCallArchive).count(),
            "deps": s.query(DependencyRegistration).count(),
            "discovery": s.query(DiscoveryRecord).count(),
            "jobs": s.query(Job).count(),
            "knowledge": get_default_cache().size(),
        }


def _ok_admin() -> Any:
    mock_resp = Mock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ok": True}
    mock_resp.content = b'{"ok": true}'
    return mock_resp


def _post_reset(client: TestClient) -> Any:
    with patch("httpx.Client") as mock_c:
        mock_c.return_value.__enter__.return_value.post.return_value = _ok_admin()
        r = client.post("/demo/reset")
        calls = mock_c.return_value.__enter__.return_value.post.call_args_list
    assert r.status_code == 200, r.text
    return r, calls


def test_full_reset_clears_state_and_archives_ledger(tmp_path: pathlib.Path) -> None:
    client, factory = _client_for(tmp_path)
    try:
        reset_default_cache()
        _seed_state(factory)
        before = _counts(factory)
        assert before["connectors"] == 1 and before["calls"] == 1 and before["knowledge"] == 1

        r, calls = _post_reset(client)

        # Four simulators reset (REQ-021 item 1: Atlas, Harbor, LegacyStay, Meridian).
        assert len(calls) == 4
        body = r.json()
        assert body["labelled"] is True and body["demo"] is True
        assert body["session_id"].startswith("demo-")
        targets = [e["target"] for e in body["resets"]]
        assert targets == ["sim-atlas", "sim-harbor", "sim-legacystay", "sim-meridian"]
        assert all(e["status"] == 200 for e in body["resets"])

        # L0 emptied.
        assert body["knowledge_cache"] == {"entries_before": 1, "entries_after": 0}

        # Ledger archived per session, live rows gone.
        assert body["model_usage"]["archived"] == 1
        assert body["model_usage"]["session_id"] == body["session_id"]

        after = _counts(factory)
        for table in ("connectors", "releases", "builds", "syncs", "drifts", "repairs",
                      "approvals", "evidence", "calls", "deps", "discovery", "jobs",
                      "knowledge"):
            assert after[table] == 0, table
        assert after["archive"] == 1
        with factory() as s:
            row = s.query(ModelCallArchive).one()
            assert row.session_id == body["session_id"]
            assert (row.input_tokens, row.output_tokens) == (10, 20)
            assert row.purpose == "ladder-l1"
    finally:
        app.dependency_overrides.clear()
        reset_default_cache()


def test_reset_is_idempotent(tmp_path: pathlib.Path) -> None:
    client, factory = _client_for(tmp_path)
    try:
        reset_default_cache()
        _seed_state(factory)
        first = _post_reset(client)[0].json()
        second = _post_reset(client)[0].json()
        assert second["session_id"] != first["session_id"]
        assert second["model_usage"]["archived"] == 0
        assert all(v == 0 for v in second["removed"].values())
        with factory() as s:
            # First session's archive survives the second reset.
            assert s.query(ModelCallArchive).count() == 1
            assert s.query(ModelCallArchive).one().session_id == first["session_id"]
    finally:
        app.dependency_overrides.clear()
        reset_default_cache()


def test_reset_repeatable_same_counts(tmp_path: pathlib.Path) -> None:
    """Run a scenario, reset, run it again: identical outcome and counts."""
    client, factory = _client_for(tmp_path)
    try:
        reset_default_cache()
        _seed_state(factory)
        run1 = _post_reset(client)[0].json()
        _seed_state(factory)
        run2 = _post_reset(client)[0].json()
        assert run1["removed"] == run2["removed"]
        assert run1["model_usage"]["archived"] == run2["model_usage"]["archived"] == 1
        assert run1["knowledge_cache"] == run2["knowledge_cache"]
        with factory() as s:
            sessions = {r.session_id for r in s.query(ModelCallArchive).all()}
            assert sessions == {run1["session_id"], run2["session_id"]}
    finally:
        app.dependency_overrides.clear()
        reset_default_cache()


def test_reset_targets_legacystay_alias(tmp_path: pathlib.Path) -> None:
    client, _factory = _client_for(tmp_path)
    try:
        with patch("httpx.Client") as mock_c:
            mock_c.return_value.__enter__.return_value.post.return_value = _ok_admin()
            r = client.post(
                "/demo/inject-fault",
                json={"target": "legacystay", "scenario_id": "baseline"},
            )
            assert r.status_code == 200, r.text
            assert r.json()["target"] == "sim-legacystay"
            r = client.post(
                "/demo/inject-fault", json={"target": "unknown", "scenario_id": "x"}
            )
            assert r.status_code == 422
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# demo-reset driver (Makefile target)
# ---------------------------------------------------------------------------


def test_demo_reset_driver_unreachable() -> None:
    from autobridge.bench.demo_reset import demo_reset

    body, code = demo_reset("http://localhost:9")
    assert code == 1
    assert "error" in body


def test_demo_reset_main_prints_summary(
    monkeypatch: Any, capsys: Any
) -> None:
    import autobridge.bench.demo_reset as driver

    body = {
        "session_id": "demo-20261028T000000Z-abcd1234",
        "resets": [{"target": "sim-atlas", "status": 200}],
        "knowledge_cache": {"entries_before": 1, "entries_after": 0},
        "model_usage": {"archived": 2, "session_id": "demo-20261028T000000Z-abcd1234"},
        "removed": {"connectors": 1, "model_calls": 0},
    }
    monkeypatch.setattr(driver, "demo_reset", lambda _url: (body, 0))
    assert driver.main() == 0
    out = capsys.readouterr().out
    assert "demo session: demo-20261028T000000Z-abcd1234" in out
    assert "sim sim-atlas: 200" in out
    assert "knowledge cache: 1 -> 0" in out


def test_demo_reset_main_failure_prints_body(
    monkeypatch: Any, capsys: Any
) -> None:
    import autobridge.bench.demo_reset as driver

    monkeypatch.setattr(driver, "demo_reset", lambda _url: ({"error": "down"}, 1))
    assert driver.main() == 1
    assert "down" in capsys.readouterr().out
