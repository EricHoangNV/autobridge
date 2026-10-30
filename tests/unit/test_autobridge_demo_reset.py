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
from typing import TYPE_CHECKING, Any
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
)
from autobridge.control.routes.demo import expected_reset_targets

if TYPE_CHECKING:
    import pytest
from autobridge.llm.provider import Usage, record_model_call
from autobridge.repair.knowledge import get_default_cache, reset_default_cache


def _client_for(tmp_path: pathlib.Path) -> tuple[TestClient, Any]:
    engine = create_engine(f"sqlite:///{tmp_path}/reset_test.db")
    Base.metadata.create_all(engine)
    return _client_for_engine(engine)


def _client_for_db_url(url: str) -> tuple[TestClient, Any]:
    return _client_for_engine(create_engine(url))


def _client_for_engine(engine: Any) -> tuple[TestClient, Any]:
    from autobridge.control.models import session_factory as _session_factory

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

        # Simulators reset via the shared registry (REQ-022 owner check
        # 2026-10-29: expected entries derive from the same registry the
        # endpoint uses), plus the FIAS link-engine reset.
        assert len(calls) == 6
        body = r.json()
        assert body["labelled"] is True and body["demo"] is True
        assert body["session_id"].startswith("demo-")
        targets = [e["target"] for e in body["resets"]]
        assert targets == expected_reset_targets()
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
    from bench.demo_reset import demo_reset

    body, code = demo_reset("http://127.0.0.1:9")
    assert code == 1
    assert "error" in body


def test_demo_reset_main_prints_summary(
    monkeypatch: Any, capsys: Any
) -> None:
    import bench.demo_reset as driver

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
    import bench.demo_reset as driver

    monkeypatch.setattr(driver, "demo_reset", lambda _url: ({"error": "down"}, 1))
    assert driver.main() == 1
    assert "down" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Owner check: upgrade a previous-schema database, then serve /repairs
# ---------------------------------------------------------------------------


def _drop_effects(url: str, *, after: str) -> None:
    """Remove migration effects newer than *after* (simulates an old create_all).

    ``after="0005_..."`` drops the 0006 column; ``after="0001_..."`` drops
    everything newer (tables + columns). SQLite supports DROP COLUMN.
    """
    import sqlalchemy as sa

    from autobridge.control.migrate import _REVISION_EFFECTS

    drop_from = False
    stmts: list[str] = []
    for revision, effects in _REVISION_EFFECTS:
        if revision == after:
            drop_from = True
            continue
        if not drop_from:
            continue
        for table in effects["tables"]:
            stmts.append(f"DROP TABLE IF EXISTS {table}")
        for dotted in effects["columns"]:
            table, _, column = dotted.partition(".")
            stmts.append(f"ALTER TABLE {table} DROP COLUMN {column}")
    with sa.create_engine(url).connect() as conn:
        for stmt in stmts:
            conn.exec_driver_sql(stmt)
        conn.commit()


def _assert_current_schema(url: str) -> None:
    import sqlalchemy as sa

    with sa.create_engine(url).connect() as conn:
        insp = sa.inspect(conn)
        assert "model_call_archive" in insp.get_table_names()
        cols = [c["name"] for c in insp.get_columns("repair_runs")]
        assert "ladder_trace" in cols
        assert "escalation_bundle" in cols


def _reset_and_serve_repairs(url: str) -> None:
    client, _factory = _client_for_db_url(url)
    try:
        with patch("httpx.Client") as mock_c:
            mock_c.return_value.__enter__.return_value.post.return_value = _ok_admin()
            r = client.post("/demo/reset")
            assert r.status_code == 200, r.text
            assert r.json()["session_id"].startswith("demo-")
        _assert_current_schema(url)
        r = client.get("/repairs")
        assert r.status_code == 200, r.text
    finally:
        app.dependency_overrides.clear()
        reset_default_cache()


def test_reset_upgrades_previous_schema_then_serves_repairs(tmp_path: pathlib.Path) -> None:
    """A database file kept across runs (pre-0005/0006) resets and serves.

    Never require the operator to delete their database: /demo/reset
    migrates first, then the repair endpoints (which need ladder_trace)
    answer 200.
    """
    from alembic.config import Config

    from alembic import command

    db_file = tmp_path / "old_schema.db"
    url = f"sqlite:///{db_file}"
    root = pathlib.Path("alembic")
    assert root.is_dir(), "run from the repo root"
    cfg = Config()
    cfg.set_main_option("script_location", str(root.resolve()))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    # Simulate the kept-across-runs database: back at 0004, before the
    # archive table and the repair ladder_trace column existed.
    command.downgrade(cfg, "0004_discovery_records")
    import sqlalchemy as sa

    with sa.create_engine(url).connect() as conn:
        tables = sa.inspect(conn).get_table_names()
        assert "model_call_archive" not in tables
        cols = [c["name"] for c in sa.inspect(conn).get_columns("repair_runs")]
        assert "ladder_trace" not in cols
    _reset_and_serve_repairs(url)


def test_reset_upgrades_pre_alembic_schema(tmp_path: pathlib.Path) -> None:
    """A create_all-built database with no version row is detected, not blind-stamped."""
    from autobridge.control.models import Base as _Base

    url = f"sqlite:///{tmp_path}/pre_alembic.db"
    engine = create_engine(url)
    _Base.metadata.create_all(engine)
    engine.dispose()
    # Simulate an older release's create_all: everything except the newest column.
    _drop_effects(url, after="0005_model_call_archive")
    import sqlalchemy as sa

    with sa.create_engine(url).connect() as conn:
        assert "alembic_version" not in sa.inspect(conn).get_table_names()
    _reset_and_serve_repairs(url)


def test_reset_repairs_lying_head_stamp(tmp_path: pathlib.Path) -> None:
    """A database stamped at head without the columns is upgraded, not trusted."""
    from alembic.config import Config

    from alembic import command
    from autobridge.control.models import Base as _Base

    url = f"sqlite:///{tmp_path}/lying_stamp.db"
    engine = create_engine(url)
    _Base.metadata.create_all(engine)
    engine.dispose()
    _drop_effects(url, after="0005_model_call_archive")
    root = pathlib.Path("alembic")
    cfg = Config()
    cfg.set_main_option("script_location", str(root.resolve()))
    cfg.set_main_option("sqlalchemy.url", url)
    command.stamp(cfg, "head")
    _reset_and_serve_repairs(url)


def test_admin_url_compose_branch_and_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-055 gate: admin routing honors compose mode and env overrides."""
    import autobridge.control.routes.demo as _demo

    monkeypatch.setattr(_demo, "in_compose_network", lambda: True)
    assert _demo._admin_url_for_target("sim-atlas") == "http://sim-atlas:8001"
    monkeypatch.setattr(_demo, "in_compose_network", lambda: False)
    monkeypatch.setenv("SIMULATOR_ADMIN_URL", "http://127.0.0.1:9")
    assert _demo._admin_base_for_target("sim-atlas") == "http://127.0.0.1:9"
    monkeypatch.delenv("SIMULATOR_ADMIN_URL")
    monkeypatch.setenv("SIM_ATLAS_ADMIN_URL", "http://127.0.0.1:8")
    assert _demo._admin_base_for_target("sim-atlas") == "http://127.0.0.1:8"


def test_reset_wait_and_target_env_branches(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-055 gate: reset wait knob degrades; per-target env wins."""
    import autobridge.control.routes.demo as _demo

    monkeypatch.setenv("AUTOBRIDGE_RESET_WAIT_S", "bogus")
    assert _demo._reset_wait_s() == 90.0
    monkeypatch.setenv("AUTOBRIDGE_RESET_WAIT_S", "0")
    assert _demo._reset_wait_s() == 0.0
    client, _factory = _client_for(tmp_path)
    try:
        monkeypatch.setenv("SIM_ATLAS_ADMIN_URL", "http://127.0.0.1:9")
        with patch("httpx.Client") as mock_c:
            mock_c.return_value.__enter__.return_value.post.return_value = _ok_admin()
            r = client.post("/demo/reset", json={"targets": ["sim-atlas"]})
            assert r.status_code == 200, r.text
    finally:
        app.dependency_overrides.clear()


def test_inject_fault_reports_unparseable_admin_body(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-055 gate: a non-JSON admin reply is reported, not crashed on."""
    from unittest.mock import Mock as _Mock

    client, _factory = _client_for(tmp_path)
    try:
        monkeypatch.setenv("SIMULATOR_ADMIN_URL", "http://127.0.0.1:9")
        bad = _Mock()
        bad.status_code = 200
        bad.content = b"not json"
        bad.json.side_effect = ValueError("no json")
        bad.text = "not json"
        with patch("httpx.Client") as mock_c:
            mock_c.return_value.__enter__.return_value.post.return_value = bad
            r = client.post("/demo/inject-fault", json={"target": "sim-atlas", "scenario_id": "page_to_cursor"})
            assert r.status_code == 200, r.text
    finally:
        app.dependency_overrides.clear()


def test_simulators_probe_degrades_on_bad_admin(tmp_path: pathlib.Path) -> None:
    """REQ-055 gate: non-200 and non-JSON admin probes degrade to unknown."""
    from unittest.mock import Mock as _Mock

    client, _factory = _client_for(tmp_path)
    try:
        down = _Mock()
        down.status_code = 500
        with patch("httpx.Client") as mock_c:
            mock_c.return_value.__enter__.return_value.get.return_value = down
            r = client.get("/demo/simulators")
            assert r.status_code == 200, r.text
        bad = _Mock()
        bad.status_code = 200
        bad.json.side_effect = ValueError("no json")
        with patch("httpx.Client") as mock_c:
            mock_c.return_value.__enter__.return_value.get.return_value = bad
            r = client.get("/demo/simulators")
            assert r.status_code == 200, r.text
    finally:
        app.dependency_overrides.clear()


def test_demo_docs_public_url_failure_is_422(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-055 gate: an unresolvable public docs address is a 422."""
    import autobridge.control.routes.demo as _demo

    client, _factory = _client_for(tmp_path)
    try:
        monkeypatch.setattr(_demo, "public_base_url_for_target", lambda _r: (_ for _ in ()).throw(RuntimeError("x")))
        r = client.get("/demo/docs", params={"target": "sim-atlas"})
        assert r.status_code == 422, r.text
    finally:
        app.dependency_overrides.clear()


def test_record_climb_404_rationale_and_bad_cost(tmp_path: pathlib.Path) -> None:
    """REQ-055 gate: unknown connector 404s; rationale and cost degrade."""
    from autobridge.control.models import session_factory as _session_factory

    engine = create_engine(f"sqlite:///{tmp_path}/climb.db")
    Base.metadata.create_all(engine)
    client, factory = _client_for_db_url(f"sqlite:///{tmp_path}/climb.db")
    assert factory is not None
    _seed_state(_session_factory(engine))
    with factory() as s:
        from autobridge.control.models import Connector as _Connector

        conn = s.query(_Connector).first()
        assert conn is not None
        cid = conn.id
    try:
        body = {
            "connector_id": "missing",
            "drift_class": "SEMANTIC_VALUE_DRIFT",
            "escalation_question": "q?",
        }
        assert client.post("/demo/record-climb", json=body).status_code == 404
        body = {
            "connector_id": cid,
            "drift_class": "SEMANTIC_VALUE_DRIFT",
            "vendor": "harbor",
            "scenario": "gross_to_net",
            "escalation_question": "Net or gross?",
            "model_rationale": "Observed sums differ.",
            "model_calls": [
                {
                    "provider": "muse",
                    "model": "muse-spark-1.3",
                    "tier": "frontier",
                    "input_tokens": 1,
                    "output_tokens": 2,
                    "purpose": "l3",
                    "cost": "bogus",
                }
            ],
        }
        r = client.post("/demo/record-climb", json=body)
        assert r.status_code == 200, r.text
    finally:
        app.dependency_overrides.clear()
