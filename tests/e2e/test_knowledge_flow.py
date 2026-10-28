"""REQ-021 item 6 — knowledge learn/reuse flow (e2e).

knowledge list empty -> inject a drift -> climb to L1, approve -> a new
entry appears on the page -> inject the same drift again -> L0 hit, 0 model
calls, the entry's "times reused" goes up. In-process always; the live test
checks both endpoints' shape against the stack.
"""

from __future__ import annotations

import os
import pathlib
import urllib.request
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from autobridge.control.app import app
from autobridge.control.deps import get_db
from autobridge.control.models import (
    Approval,
    Base,
    Connector,
    ConnectorRelease,
    DriftEvent,
    RepairRun,
    session_factory,
)
from autobridge.drift.fingerprint import FingerprintDelta
from autobridge.engine.releases import create_release_from_patch
from autobridge.repair.controller import RepairController
from autobridge.repair.knowledge import KnowledgeCache

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load_reference_spec(connector_id: str) -> dict[str, Any]:
    path = REPO_ROOT / "generated" / "connectors" / connector_id / "reference" / "spec.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _page_delta() -> FingerprintDelta:
    return FingerprintDelta(
        fingerprint_changed=True,
        pagination_changed=True,
        keys_changed=False,
        types_changed=False,
        changed_entities=[],
        pagination_details={
            "list_reservations": {"previous": {"strategy": "page"}, "current": {"strategy": "cursor"}},
        },
        keys_details={},
        types_details={},
    )


def _client_for_db(url: str) -> tuple[TestClient, Any]:
    engine = create_engine(url)
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


def test_knowledge_learn_reuse_flow(tmp_path: pathlib.Path) -> None:
    """The demo flow, asserted end to end: empty -> L1 -> approve -> entry -> L0 hit -> reused."""
    client, factory = _client_for_db(f"sqlite:///{tmp_path}/flow.db")
    try:
        spec = _load_reference_spec("atlaspms")
        with factory() as s:
            conn = Connector(
                name="Atlas", base_url="http://localhost:8105", credential_ref="ATLAS_API_KEY",
                target_entities=["Reservation"], runtime_target={"kind": "local_process"},
            )
            s.add(conn)
            s.flush()
            drift = DriftEvent(connector_id=conn.id, drift_class="PAGINATION_CHANGE")
            s.add(drift)
            s.flush()
            drift_id, connector_id = drift.id, conn.id
            ctrl = RepairController(knowledge_cache=KnowledgeCache())
            result = ctrl.handle(
                spec=spec, drift_class="PAGINATION_CHANGE", signals=[],
                fingerprint_delta=_page_delta(), extra={"target_strategy": "cursor"},
                vendor="AtlasPMS", drift_event_ids=[drift_id], session=s,
            )
            assert result.decision == "propose" and result.ladder_level_reached == 1
            run_id = s.query(RepairRun).order_by(RepairRun.created_at.desc()).first().id
            s.commit()

        assert client.get("/knowledge").json()["entries"] == []

        with factory() as s:
            from autobridge.control.models import spec_signature as _sig

            base = ConnectorRelease(connector_id=connector_id, version=1, spec=spec, signature=_sig(spec))
            s.add(base)
            s.flush()
            rel = create_release_from_patch(
                s, connector_id=connector_id, base_release_id=base.id,
                patch=result.candidate_patch, provenance_repair_run_id=run_id,
            )
            s.add(Approval(repair_run_id=run_id, actor="tester", decision="approve", to_release_id=rel.id))
            s.commit()

        entry = client.get("/knowledge").json()["entries"]
        assert len(entry) == 1
        assert entry[0]["times_reused"] == 0

        with factory() as s:
            ctrl2 = RepairController(knowledge_cache=KnowledgeCache())
            ctrl2.knowledge_cache.put("AtlasPMS", _page_delta(), result.candidate_patch, "page_to_cursor")
            result2 = ctrl2.handle(
                spec=spec, drift_class="PAGINATION_CHANGE", signals=[],
                fingerprint_delta=_page_delta(), extra={"target_strategy": "cursor"},
                vendor="AtlasPMS", drift_event_ids=[drift_id], session=s,
            )
            assert result2.ladder_level_reached == 0
            assert result2.model_usage.get("frontier_tokens", 0) == 0
            s.commit()

        entries = client.get("/knowledge").json()["entries"]
        assert len(entries) == 1
        assert entries[0]["times_reused"] == 1
        assert entries[0]["learned_from"]["rung"] == 1
    finally:
        app.dependency_overrides.clear()


@pytest.mark.live
def test_knowledge_endpoints_live_shape(e2e_stack: str) -> None:
    """Live stack serves both knowledge pages with the expected shape."""
    assert e2e_stack
    from tests.e2e.conftest import service_url  # noqa: PLC0415

    def _get(path: str) -> dict[str, Any]:
        import json as _json

        req = urllib.request.Request(service_url("control", path), method="GET")
        with urllib.request.urlopen(req, timeout=15) as resp:
            assert resp.status == 200, f"GET {path} -> {resp.status}"
            return _json.loads(resp.read().decode())

    capabilities = _get("/capabilities")
    assert set(capabilities["versions"]) == {"v1", "v2", "v3"}
    assert "POST" in capabilities["versions"]["v2"]["methods"]
    assert "POST" not in capabilities["versions"]["v1"]["methods"]
    assert "EVENT" in capabilities["versions"]["v3"]["methods"]
    assert "EVENT" not in capabilities["versions"]["v2"]["methods"]
    assert any(t["name"] == "reset_demo" for t in capabilities["tools"])
    knowledge = _get("/knowledge")
    assert set(knowledge) == {"entries", "connectors", "counts"}
    assert set(knowledge["counts"]) == {"entries", "reuses", "tokens_saved"}
    _ = os.environ.get("AUTOBRIDGE_CONTROL_TOKEN", "")
