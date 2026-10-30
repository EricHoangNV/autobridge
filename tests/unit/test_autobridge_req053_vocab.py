"""REQ-053 items 3 + 7 + 8/9: terminal repair decisions, vocabulary pre-check.

* Approving / rejecting / rolling back a repair moves its decision terminal
  (``approved`` / ``reject`` / ``rolled_back``) — every "waiting" derives
  from the decision, one source of truth.
* ``POST /connectors/vocabulary-check`` runs before any connector is
  created: a refused vocabulary creates no connector (recorded by name);
  the later connector shows the refusal as history, and the knowledge
  profile lists it under refused vocabularies.
* ``GET /demo/simulators`` carries the same credential_ref and entities
  ``make demo`` sends, so the UI build path stops leaving form defaults.
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture()
def route_client(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    from autobridge.control.app import app  # noqa: PLC0415
    from autobridge.control.deps import get_db  # noqa: PLC0415
    from autobridge.control.models import Base as _Base  # noqa: PLC0415
    from autobridge.control.models import session_factory as _factory  # noqa: PLC0415

    monkeypatch.setenv("SIMULATOR_ADMIN_URL", "http://127.0.0.1:9")
    engine = create_engine(f"sqlite:///{tmp_path}/req053_vocab.db")
    _Base.metadata.create_all(engine)
    factory = _factory(engine)

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
    try:
        yield client, factory
    finally:
        app.dependency_overrides.clear()


def _meridian_docs() -> str:
    return (REPO_ROOT / "simulators" / "docs" / "meridian-openapi.yaml").read_text(encoding="utf-8")


def test_vocabulary_check_refuses_v1_without_creating_a_connector(route_client: Any) -> None:
    client, _factory = route_client
    rec = client.post(
        "/connectors/vocabulary-check",
        json={"name": "meridianpms", "docs_text": _meridian_docs(), "spec_vocabulary": "v1"},
    )
    assert rec.status_code == 200, rec.text
    body = rec.json()
    assert body["refused"] is True
    assert "POST" in (body["reason"] or "") and "ConnectorSpec v1" in (body["reason"] or "")
    assert body["vocabulary"] == "v1"
    assert body["suggested_vocabulary"] == "v2"
    # No connector was created.
    conns = client.get("/connectors").json()
    assert [c["name"] for c in conns] == []


def test_vocabulary_check_v2_passes_and_history_joins_the_connector(route_client: Any) -> None:
    from autobridge.control.models import RefusedBuildAttempt

    client, factory = route_client
    client.post(
        "/connectors/vocabulary-check",
        json={"name": "meridianpms", "docs_text": _meridian_docs(), "spec_vocabulary": "v1"},
    )
    rec = client.post(
        "/connectors/vocabulary-check",
        json={"name": "meridianpms", "docs_text": _meridian_docs(), "spec_vocabulary": "v2"},
    )
    assert rec.json()["refused"] is False
    created = client.post(
        "/connectors",
        json={
            "name": "meridianpms",
            "base_url": "http://sim-meridian:8000",
            "credential_ref": "MERIDIAN_API_KEY",
            "target_entities": ["Reservation", "FolioCharge"],
        },
    )
    assert created.status_code == 201, created.text
    cid = created.json()["id"]
    # The connector page shows the earlier v1 refusal as history.
    detail = client.get(f"/connectors/{cid}").json()
    vocabularies = [b.get("spec_vocabulary") for b in detail.get("builds", [])]
    assert vocabularies == ["v1"]
    v1 = detail["builds"][0]
    assert v1["success"] is False and v1["release_id"] is None
    assert "POST" in (v1["degradation_reason"] or "")
    # The knowledge profile lists v1 refused under the same name.
    knowledge = client.get("/knowledge").json()
    profiles = [c for c in knowledge.get("connectors", []) if c.get("name") == "meridianpms"]
    assert len(profiles) == 1
    assert "v1" in (profiles[0].get("refused_vocabularies") or [])
    with factory() as s:
        assert s.query(RefusedBuildAttempt).count() == 1


def test_vocabulary_check_rejects_docs_path_only(route_client: Any) -> None:
    client, _factory = route_client
    rec = client.post(
        "/connectors/vocabulary-check",
        json={"name": "x", "docs_path": "simulators/docs/meridian-openapi.yaml", "spec_vocabulary": "v1"},
    )
    assert rec.status_code == 422


def _seed_propose(factory: Any) -> dict[str, str]:
    from autobridge.control.models import (
        Connector,
        ConnectorRelease,
        DriftEvent,
        RepairRun,
        spec_signature,
    )

    spec = {
        "spec_version": 1,
        "connector": {"id": "c", "vendor": "v", "runtime_target": "cloud"},
        "source": {
            "base_url": "https://example.com",
            "auth": {"type": "bearer", "credential_ref": "TEST_TOKEN"},
            "timezone_source": {"kind": "static", "value": "UTC"},
        },
        "operations": {
            "list_reservations": {
                "method": "GET", "path": "/reservations", "params": {},
                "pagination": {"strategy": "page", "page_param": "page", "items_path": "$.data", "stop_when": ["empty_page"]},
            }
        },
        "entities": {
            "Reservation": {
                "from": "list_reservations", "key": "$.reservation_id",
                "fields": {"reservation_id": {"path": "$.reservation_id"}},
            }
        },
        "capabilities": {"read": ["Reservation"]},
        "health": {"canary": {"every": "60s", "operation": "list_reservations"}},
    }
    with factory() as s:
        conn = Connector(
            name="c", base_url="http://x", credential_ref="ATLAS_API_KEY",
            target_entities=["Reservation"], runtime_target={"kind": "local_process"},
        )
        s.add(conn)
        s.flush()
        rel = ConnectorRelease(
            connector_id=conn.id, version=1, spec=spec, signature=spec_signature(spec)
        )
        s.add(rel)
        s.flush()
        conn.active_release_id = rel.id
        drift = DriftEvent(
            connector_id=conn.id, drift_class="PAGINATION_CHANGE",
            source_sync_run_id="sync-1", status="open",
        )
        s.add(drift)
        s.flush()
        run = RepairRun(
            drift_event_ids=[drift.id], decision="propose", model_usage={},
            candidate_patch=[{"op": "replace", "path": "/a", "value": "b"}],
            rule_id="page_to_cursor", vendor="v", drift_class="PAGINATION_CHANGE",
        )
        s.add(run)
        s.flush()
        candidate = ConnectorRelease(
            connector_id=conn.id, version=2, spec=spec,
            parent_release_id=rel.id, signature=spec_signature(spec),
            provenance_repair_run_id=run.id,
        )
        s.add(candidate)
        s.flush()
        s.commit()
        return {"connector_id": conn.id, "release_id": rel.id, "repair_id": run.id,
                "candidate_id": candidate.id}


def test_approve_moves_the_repair_terminal(route_client: Any) -> None:
    client, factory = route_client
    ids = _seed_propose(factory)
    rec = client.post(f"/repairs/{ids['repair_id']}/approve", json={"connector_id": ids["connector_id"]})
    assert rec.status_code == 200, rec.text
    rep = client.get(f"/repairs/{ids['repair_id']}").json()
    assert rep["decision"] == "approved"
    assert rep["approval"] is not None and rep["approval"]["decision"] == "approve"
    # Approving again is rejected: the repair no longer waits.
    again = client.post(f"/repairs/{ids['repair_id']}/approve", json={"connector_id": ids["connector_id"]})
    assert again.status_code == 422


def test_reject_moves_the_repair_terminal(route_client: Any) -> None:
    client, _factory = route_client
    ids = _seed_propose(_factory)
    rec = client.post(f"/repairs/{ids['repair_id']}/reject", json={})
    assert rec.status_code == 200, rec.text
    rep = client.get(f"/repairs/{ids['repair_id']}").json()
    assert rep["decision"] == "reject"


def test_rollback_moves_the_repair_terminal_with_reopen(route_client: Any) -> None:
    from autobridge.engine.releases import rollback_release

    client, factory = route_client
    ids = _seed_propose(factory)
    client.post(f"/repairs/{ids['repair_id']}/approve", json={"connector_id": ids["connector_id"]})
    with factory() as s:
        _new, approval = rollback_release(
            s, connector_id=ids["connector_id"], target_release_id=ids["release_id"],
            actor="operator", repair_run_id=ids["repair_id"],
        )
        assert approval.decision == "rollback"
        s.commit()
    rep = client.get(f"/repairs/{ids['repair_id']}").json()
    assert rep["decision"] == "rolled_back"
    with factory() as s:
        _new2, _approval2 = rollback_release(
            s, connector_id=ids["connector_id"], target_release_id=ids["release_id"],
            actor="operator", repair_run_id=ids["repair_id"], reopen_as="escalate",
        )
        s.commit()
    rep2 = client.get(f"/repairs/{ids['repair_id']}").json()
    assert rep2["decision"] == "escalate"


def test_simulator_rows_carry_the_demo_build_defaults() -> None:
    from autobridge.control.demo_targets import get_demo_targets

    reg = get_demo_targets()
    by_id = {t.id: t for t in reg.targets}
    assert by_id["sim-meridian"].suggested_credential_ref == "MERIDIAN_API_KEY"
    assert list(by_id["sim-meridian"].suggested_target_entities) == ["Reservation", "FolioCharge"]
    assert by_id["sim-fias-pabx"].suggested_credential_ref == "EDGE_TOKEN"
    assert list(by_id["sim-fias-pabx"].suggested_target_entities) == ["FolioCharge"]
    assert by_id["sim-atlas"].suggested_credential_ref == "ATLAS_API_KEY"
    assert by_id["sim-harbor"].suggested_credential_ref == "HARBOR_API_KEY"
    assert by_id["sim-legacystay"].suggested_credential_ref == "LEGACYSTAY_API_KEY"


def test_simulators_endpoint_serves_build_defaults(route_client: Any) -> None:
    client, _factory = route_client
    body = client.get("/demo/simulators").json()
    by_id = {r["id"]: r for r in body.get("simulators", [])}
    meridian = by_id["sim-meridian"]
    assert meridian["suggested_credential_ref"] == "MERIDIAN_API_KEY"
    assert meridian["suggested_target_entities"] == ["Reservation", "FolioCharge"]
    fias = by_id["sim-fias-pabx"]
    assert fias["suggested_credential_ref"] == "EDGE_TOKEN"


def test_vocabulary_check_refusal_carries_capability_request_facts(route_client: Any) -> None:
    """REQ-058: a refused build's facts ride the check response (no new endpoint)."""
    client, _factory = route_client
    meridian = client.post(
        "/connectors/vocabulary-check",
        json={"name": "meridianpms", "docs_text": _meridian_docs(), "spec_vocabulary": "v1"},
    )
    assert meridian.status_code == 200, meridian.text
    mbody = meridian.json()
    assert mbody["refused"] is True
    assert mbody["refused_methods"] == ["POST"]
    assert mbody["refused_protocol"] is None
    assert mbody["doc_type"] == "openapi"
    assert mbody["auth_kind"] == "body_fields"
    assert mbody["operations"], "the vendor spec names its operations"
    assert all({"operation_id", "method", "path"} <= set(op) for op in mbody["operations"])
    assert mbody["has_body_fields"] is True


def test_vocabulary_check_event_refusal_carries_protocol_facts(route_client: Any) -> None:
    """REQ-058: the PABX-on-v2 refusal names the protocol, not methods."""
    client, _factory = route_client
    fias_docs = (REPO_ROOT / "simulators" / "docs" / "fias-pabx.md").read_text(encoding="utf-8")
    rec = client.post(
        "/connectors/vocabulary-check",
        json={"name": "pabx edge", "docs_text": fias_docs, "spec_vocabulary": "v2"},
    )
    assert rec.status_code == 200, rec.text
    body = rec.json()
    assert body["refused"] is True
    assert body["refused_protocol"] == "fias_tcp"
    assert body["refused_methods"] is None
    assert body["suggested_vocabulary"] == "v3"
    assert body["operations"], "framed docs describe event feeds"
    assert body["has_event_feeds"] is True
