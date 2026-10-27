# ruff: noqa: TC003, F401, I001, SIM105, F841
"""Extended control coverage (P1.W7.1) — hit remaining branches."""

from __future__ import annotations

import json
import pathlib

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from autobridge.control.app import app
from autobridge.control.deps import get_db, get_engine, get_session_factory, reset_engine_cache
from autobridge.control.models import (
    Base,
    Connector,
    DriftEvent,
    ModelCall,
    RepairRun,
    session_factory,
)


def test_deps_engine_cache(tmp_path: pathlib.Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Ensure cache reset works
    reset_engine_cache()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/engine_test.db")
    e1 = get_engine()
    e2 = get_engine()
    assert e1 is e2
    f1 = get_session_factory()
    f2 = get_session_factory()
    assert f1 is f2
    # Test get_db generator commits
    Base.metadata.create_all(e1)
    gen = get_db()
    sess = next(gen)
    # Insert connector via sess
    c = Connector(name="DepTest", base_url="http://example.com", credential_ref="TEST_API_KEY", target_entities=[], runtime_target={"kind": "local_process"})
    sess.add(c)
    sess.flush()
    # Commit via generator exit
    try:
        next(gen)
    except StopIteration:
        pass
    # Verify persisted
    factory = get_session_factory()
    with factory() as s:
        rows = s.query(Connector).all()
        assert len(rows) >= 1
    reset_engine_cache()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    reset_engine_cache()


def test_build_docs_url_and_path(tmp_path: pathlib.Path) -> None:
    from unittest.mock import Mock, patch

    from autobridge.control.models import Base

    engine = create_engine(f"sqlite:///{tmp_path}/build_test.db")
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
    client = TestClient(app)
    # Create connector
    r = client.post("/connectors", json={"name": "BuildTest", "base_url": "http://sim-harbor:8000", "credential_ref": "HARBOUR_API_KEY", "target_entities": []})
    cid = r.json()["id"]

    # Mock docs_url fetch
    mock_resp = Mock()
    mock_resp.text = "# HarborPMS API\nBase URL: http://sim-harbor:8000\nGET /reservations\nGET /folios\n"
    mock_resp.raise_for_status = Mock()
    with patch("httpx.Client") as mock_client:
        mock_client.return_value.__enter__.return_value.get.return_value = mock_resp
        r2 = client.post(f"/connectors/{cid}/build", json={"docs_url": "http://example.com/docs.md"})
        assert r2.status_code == 200, r2.text
        assert r2.json()["state"] == "succeeded"

    # Test docs_path with actual file
    docs_file = tmp_path / "docs.md"
    docs_file.write_text("# HarborPMS API\nBase URL: http://sim-harbor:8000\nGET /reservations\nGET /folios\n")
    r3 = client.post(f"/connectors/{cid}/build", json={"docs_path": str(docs_file)})
    assert r3.status_code == 200, r3.text

    # Test docs_path non-existing synthesizes
    r4 = client.post(f"/connectors/{cid}/build", json={"docs_path": "/tmp/nonexistent_docs_xyz.md"})
    assert r4.status_code == 200

    app.dependency_overrides.clear()


def test_repairs_approve_reject_flow(tmp_path: pathlib.Path) -> None:
    from fastapi.testclient import TestClient

    from autobridge.control.app import app
    from autobridge.control.models import Base

    engine = create_engine(f"sqlite:///{tmp_path}/repairs.db")
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
    client = TestClient(app)

    # Create connector and releases
    c = client.post("/connectors", json={"name": "RepairTest", "base_url": "http://sim-harbor:8000", "credential_ref": "TEST_API_KEY", "target_entities": []}).json()
    cid = c["id"]
    docs = "# HarborPMS API\nBase URL: http://sim-harbor:8000\nGET /reservations\nGET /folios\n"
    br = client.post(f"/connectors/{cid}/build", json={"docs_text": docs})
    assert br.status_code == 200
    rels = client.get("/releases").json()
    assert len(rels) == 1
    rel_id = rels[0]["id"]

    # Manually create drift and repair run that proposes
    with factory() as s:
        drift = DriftEvent(connector_id=cid, drift_class="FIELD_RENAMED", severity="medium", evidence_ids=[], status="open")
        s.add(drift)
        s.flush()
        repair = RepairRun(drift_event_ids=[drift.id], ladder_level_reached=1, candidate_patch=[{"op": "replace", "path": "/source/base_url", "value": "http://new.example.com"}], rule_id="rename_field", decision="propose", model_usage={"frontier_tokens": 0, "frontier_calls": 0})
        s.add(repair)
        s.flush()
        # Also create candidate release via create_release_from_patch
        from autobridge.engine.releases import create_release_from_patch
        new_rel = create_release_from_patch(s, connector_id=cid, base_release_id=rel_id, patch=repair.candidate_patch, provenance_repair_run_id=repair.id)
        s.commit()
        repair_id = repair.id
        new_rel_id = new_rel.id
        drift_id = drift.id

    # Approve
    r = client.post(f"/repairs/{repair_id}/approve", json={"actor": "tester", "from_release_id": rel_id, "to_release_id": new_rel_id, "connector_id": cid})
    assert r.status_code == 200, r.text
    assert r.json()["decision"] == "approve"

    # Verify active_release flipped
    detail = client.get(f"/connectors/{cid}").json()
    assert detail["active_release_id"] == new_rel_id

    # Reject another repair
    with factory() as s:
        drift2 = DriftEvent(connector_id=cid, drift_class="PAGINATION_CHANGED", evidence_ids=[], status="open")
        s.add(drift2)
        s.flush()
        repair2 = RepairRun(drift_event_ids=[drift2.id], ladder_level_reached=1, decision="propose", model_usage={})
        s.add(repair2)
        s.flush()
        repair2_id = repair2.id
        s.commit()

    r2 = client.post(f"/repairs/{repair2_id}/reject", json={"actor": "tester2", "from_release_id": rel_id, "to_release_id": new_rel_id})
    assert r2.status_code == 200
    assert r2.json()["decision"] == "reject"

    # List repairs filtered
    filtered = client.get(f"/repairs?connector_id={cid}").json()
    assert len(filtered) >= 2

    # Get single repair
    assert client.get(f"/repairs/{repair_id}").json()["id"] == repair_id

    # Drift filtered
    assert len(client.get(f"/drift?connector_id={cid}").json()) >= 1
    assert len(client.get("/drift?status=open").json()) >= 1

    app.dependency_overrides.clear()


def test_rollback_flow(tmp_path: pathlib.Path) -> None:
    from autobridge.control.models import Base

    engine = create_engine(f"sqlite:///{tmp_path}/rollback.db")
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
    client = TestClient(app)

    c = client.post("/connectors", json={"name": "RollbackTest", "base_url": "http://sim-harbor:8000", "credential_ref": "TEST_API_KEY", "target_entities": []}).json()
    cid = c["id"]
    docs = "# HarborPMS API\nBase URL: http://sim-harbor:8000\nGET /reservations\nGET /folios\n"
    br = client.post(f"/connectors/{cid}/build", json={"docs_text": docs})
    assert br.status_code == 200
    rels = client.get("/releases").json()
    assert len(rels) == 1
    rel_id = rels[0]["id"]

    # Need a repair run for rollback audit
    with factory() as s:
        from autobridge.control.models import RepairRun as RR
        rr = RR(drift_event_ids=[], ladder_level_reached=0, decision="escalate", model_usage={})
        s.add(rr)
        s.flush()
        rr_id = rr.id
        # Activate release via approve (need repair propose)
        from autobridge.control.models import RepairRun as RR2
        rr2 = RR2(drift_event_ids=[], ladder_level_reached=1, decision="propose", model_usage={})
        s.add(rr2)
        s.flush()
        from autobridge.engine.releases import create_release_from_patch
        new_rel = create_release_from_patch(s, connector_id=cid, base_release_id=rel_id, patch=[{"op": "replace", "path": "/source/base_url", "value": "http://sim-harbor:8000"}], provenance_repair_run_id=rr2.id)
        s.flush()
        new_rel_id = new_rel.id
        # Approve to activate
        from autobridge.engine.releases import approve_release
        approve_release(s, repair_run_id=rr2.id, actor="tester", from_release_id=rel_id, to_release_id=new_rel_id, connector_id=cid)
        s.commit()

    # Now active is new_rel_id, rollback to original rel_id
    r = client.post(f"/releases/{new_rel_id}/rollback", json={"target_release_id": rel_id, "actor": "rollback-tester", "repair_run_id": rr_id})
    assert r.status_code == 200, r.text
    assert r.json()["new_release"]["spec"] == rels[0]["spec"]

    app.dependency_overrides.clear()


def test_metrics_with_data(tmp_path: pathlib.Path) -> None:
    from decimal import Decimal

    from autobridge.control.models import Base

    engine = create_engine(f"sqlite:///{tmp_path}/metrics.db")
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
    client = TestClient(app)

    # Insert model calls
    with factory() as s:
        mc = ModelCall(run_id="build_runs:abc", provider="anthropic", model="claude-sonnet-4-5", tier="frontier", input_tokens=100, output_tokens=200, cost_estimate=Decimal("0.01"), purpose="rank")
        s.add(mc)
        mc2 = ModelCall(run_id="build_runs:def", provider="anthropic", model="claude-haiku-4-5", tier="small", input_tokens=50, output_tokens=50, cost_estimate=Decimal("0.002"), purpose="probe")
        s.add(mc2)
        s.commit()

    r = client.get("/metrics/model-usage")
    assert r.status_code == 200
    data = r.json()
    assert data["total_calls"] == 2
    assert data["frontier_tokens"] == 300
    assert data["small_tokens"] == 100
    assert len(data["entries"]) == 2
    assert data["by_provider"]["anthropic"] == 2
    assert data["by_purpose"]["rank"] == 1

    r2 = client.get("/metrics/sync-outcomes")
    assert r2.json()["total"] >= 0

    app.dependency_overrides.clear()


def test_bench_scorecard_with_file(tmp_path: pathlib.Path, monkeypatch) -> None:
    # Create experiments/scorecard.md temp and test bench endpoints

    # Create a fake experiments dir
    exp_dir = tmp_path / "experiments"
    exp_dir.mkdir()
    (exp_dir / "scorecard.md").write_text("# Scorecard\n\n| Scenario | Verdict |\n|---|---|")
    (exp_dir / "benchmark_runs").mkdir()
    (exp_dir / "benchmark_runs" / "run.json").write_text('{"scenario": "baseline"}')

    # Monkeypatch cwd for bench route which uses pathlib.Path("experiments")
    monkeypatch.chdir(tmp_path)

    from autobridge.control.models import Base

    engine = create_engine(f"sqlite:///{tmp_path}/bench.db")
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
    client = TestClient(app)

    r = client.get("/bench/scorecards")
    assert r.status_code == 200
    assert r.json()["total"] >= 2

    r2 = client.get("/bench/scorecard")
    assert r2.json()["content"] is not None

    r3 = client.get(f"/bench/scorecard?path={exp_dir / 'scorecard.md'}")
    assert r3.status_code == 200
    assert "Scorecard" in r3.json()["content"]

    r4 = client.get("/bench/scorecard?path=/tmp/nonexistent.md")
    assert "error" in r4.json()

    app.dependency_overrides.clear()


def test_demo_inject_with_env_override(tmp_path: pathlib.Path, monkeypatch) -> None:
    from unittest.mock import Mock, patch

    monkeypatch.setenv("SIMULATOR_ADMIN_URL", "http://mocked:9999")
    from autobridge.control.models import Base

    engine = create_engine(f"sqlite:///{tmp_path}/demo.db")
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
    client = TestClient(app)

    mock_resp = Mock()
    mock_resp.status_code = 200
    mock_resp.content = b'{"ok": true}'
    mock_resp.json.return_value = {"ok": True}
    mock_resp.text = '{"ok": true}'
    with patch("httpx.Client") as mock_c:
        mock_c.return_value.__enter__.return_value.post.return_value = mock_resp
        r = client.post("/demo/inject-fault", json={"target": "sim-atlas", "scenario_id": "baseline", "seed": 42, "extra": "value"})
        assert r.status_code == 200
        assert r.json()["seed"] == 42
        # Check that mocked URL was used (via env override)
        called_url = mock_c.return_value.__enter__.return_value.post.call_args[0][0]
        assert "mocked:9999" in called_url

    app.dependency_overrides.clear()
    monkeypatch.delenv("SIMULATOR_ADMIN_URL", raising=False)


def test_evidence_redaction_in_sse(tmp_path: pathlib.Path, monkeypatch) -> None:
    secret = "supersecret999"
    monkeypatch.setenv("REDACT_TEST_KEY", secret)
    from autobridge.control.models import Base, EvidenceRecord

    engine = create_engine(f"sqlite:///{tmp_path}/sse.db")
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
    client = TestClient(app)

    # Create connector and build to have run_id
    c = client.post("/connectors", json={"name": "SSETest", "base_url": "http://example.com", "credential_ref": "REDACT_TEST_KEY", "target_entities": []}).json()
    cid = c["id"]
    docs = "# HarborPMS API\nBase URL: http://sim-harbor:8000\nGET /reservations\nGET /folios\n"
    br = client.post(f"/connectors/{cid}/build", json={"docs_text": docs})
    build_run_id = br.json()["build_run_id"]

    # Insert evidence with secret value via direct DB (simulating leaked payload)
    with factory() as s:
        rec = EvidenceRecord(kind="probe", payload={"auth_header": f"Bearer {secret}", "note": secret}, parent_ids=[], run_id=f"build_runs:{build_run_id}")
        s.add(rec)
        s.commit()
        rec_id = rec.id

    # SSE should redact secret
    r = client.get(f"/runs/{build_run_id}/events", headers={"Accept": "text/event-stream"})
    assert r.status_code == 200
    assert secret not in r.text
    assert "***" in r.text or "REDACTED" in r.text or secret not in r.text

    # Evidence endpoint also redacted via secrets_for_refs
    ev = client.get(f"/runs/{build_run_id}/evidence").json()
    # Find our leaked record
    leaked = [e for e in ev if e["id"] == rec_id]
    if leaked:
        assert secret not in json.dumps(leaked[0]["payload"])

    app.dependency_overrides.clear()
    monkeypatch.delenv("REDACT_TEST_KEY", raising=False)


def test_operator_actor_from_env(tmp_path: pathlib.Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """REQ-001: Approval.actor comes from AUTOBRIDGE_OPERATOR; body actor ignored."""
    from autobridge.control.models import Base

    monkeypatch.setenv("AUTOBRIDGE_OPERATOR", "alice")
    engine = create_engine(f"sqlite:///{tmp_path}/operator.db")
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
    try:
        client = TestClient(app)

        # GET /operator exposes the server-configured name
        assert client.get("/operator").json() == {"name": "alice"}

        c = client.post("/connectors", json={"name": "OpTest", "base_url": "http://sim-harbor:8000", "credential_ref": "TEST_API_KEY", "target_entities": []}).json()
        cid = c["id"]
        docs = "# HarborPMS API\nBase URL: http://sim-harbor:8000\nGET /reservations\nGET /folios\n"
        assert client.post(f"/connectors/{cid}/build", json={"docs_text": docs}).status_code == 200
        rel_id = client.get("/releases").json()[0]["id"]

        with factory() as s:
            drift = DriftEvent(connector_id=cid, drift_class="FIELD_RENAMED", severity="medium", evidence_ids=[], status="open")
            s.add(drift)
            s.flush()
            repair = RepairRun(drift_event_ids=[drift.id], ladder_level_reached=1, candidate_patch=[{"op": "replace", "path": "/source/base_url", "value": "http://new.example.com"}], rule_id="rename_field", decision="propose", model_usage={})
            s.add(repair)
            s.flush()
            from autobridge.engine.releases import create_release_from_patch
            new_rel = create_release_from_patch(s, connector_id=cid, base_release_id=rel_id, patch=repair.candidate_patch, provenance_repair_run_id=repair.id)
            s.commit()
            repair_id, new_rel_id = repair.id, new_rel.id

        # Client-sent actor is ignored: approval records the env operator
        r = client.post(f"/repairs/{repair_id}/approve", json={"actor": "mallory", "from_release_id": rel_id, "to_release_id": new_rel_id, "connector_id": cid})
        assert r.status_code == 200, r.text
        assert r.json()["actor"] == "alice"

        # ReleaseOut carries who approved it (list, single, and connector detail)
        assert client.get("/releases").json()[0]["approved_by"] == "alice"
        assert client.get(f"/releases/{new_rel_id}").json()["approved_by"] == "alice"
        detail = client.get(f"/connectors/{cid}").json()
        assert [x for x in detail["releases"] if x["id"] == new_rel_id][0]["approved_by"] == "alice"

        # Reject also records the env operator, ignoring the body actor
        with factory() as s:
            drift2 = DriftEvent(connector_id=cid, drift_class="PAGINATION_CHANGED", evidence_ids=[], status="open")
            s.add(drift2)
            s.flush()
            repair2 = RepairRun(drift_event_ids=[drift2.id], ladder_level_reached=1, decision="propose", model_usage={})
            s.add(repair2)
            s.flush()
            s.commit()
            repair2_id = repair2.id
        r2 = client.post(f"/repairs/{repair2_id}/reject", json={"actor": "mallory"})
        assert r2.status_code == 200, r2.text
        assert r2.json()["actor"] == "alice"

        # Rollback records the env operator too, even with no actor in the body
        rb = client.post(f"/releases/{new_rel_id}/rollback", json={"target_release_id": rel_id, "repair_run_id": repair_id})
        assert rb.status_code == 200, rb.text
        assert rb.json()["approval"]["actor"] == "alice"
        assert rb.json()["new_release"]["approved_by"] == "alice"
    finally:
        app.dependency_overrides.clear()
        monkeypatch.delenv("AUTOBRIDGE_OPERATOR", raising=False)


def test_operator_default_name(tmp_path: pathlib.Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """REQ-001: GET /operator defaults to 'operator' when the env var is unset."""
    from autobridge.control.models import Base

    monkeypatch.delenv("AUTOBRIDGE_OPERATOR", raising=False)
    engine = create_engine(f"sqlite:///{tmp_path}/operator_default.db")
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
    try:
        client = TestClient(app)
        assert client.get("/operator").json() == {"name": "operator"}
    finally:
        app.dependency_overrides.clear()
