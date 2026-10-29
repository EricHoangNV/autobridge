# ruff: noqa: TC003
"""Capabilities + knowledge tests (REQ-021 item 6).

``GET /capabilities`` is derived from code (registries + spec Literals):
v1 GET-only without v2 constructs, v2 with POST/body_fields/body_cursor and
joins, the 9 transforms, checks, stop conditions, one L1 rule per known
fix, the v1 -> v2 diff, and an MCP tools/list-style agent surface.

``GET /knowledge`` derives learned entries from stored runs: a propose +
approve group appears with vendor, drift signature, patch, run/rung/tokens
and reuse counts; a second identical drift hits L0 with 0 model calls and
bumps times_reused. Connector profiles come from successful builds.
"""

from __future__ import annotations

import pathlib
from typing import Any

import yaml
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
    DriftEvent,
    ModelCall,
    RepairRun,
    session_factory,
)
from autobridge.drift.fingerprint import FingerprintDelta
from autobridge.engine.releases import create_release_from_patch
from autobridge.repair.controller import RepairController
from autobridge.repair.knowledge import KnowledgeCache
from autobridge.spec.patch import apply_patch

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load_reference_spec(connector_id: str) -> dict[str, Any]:
    path = REPO_ROOT / "generated" / "connectors" / connector_id / "reference" / "spec.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _client_for(tmp_path: pathlib.Path) -> tuple[TestClient, Any]:
    engine = create_engine(f"sqlite:///{tmp_path}/knowledge_test.db")
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


def test_capabilities_come_from_code() -> None:
    from autobridge.control.routes.capabilities import capabilities_payload

    payload = capabilities_payload()
    v1 = payload["versions"]["v1"]
    v2 = payload["versions"]["v2"]
    assert v1["methods"] == ["GET"]
    assert "POST" not in v1["methods"]
    assert "body_fields" not in v1["auth_types"]
    assert "body_cursor" not in v1["pagination_strategies"]
    assert v1["joins"] is False and v1["body_templates"] is False
    assert "POST" in v2["methods"]
    assert "body_fields" in v2["auth_types"]
    assert "body_cursor" in v2["pagination_strategies"]
    assert v2["joins"] is True and v2["body_templates"] is True
    assert len(payload["transforms"]) == 9 and "money" in payload["transforms"]
    assert "money_invariant" in payload["checks"]
    assert "repeated_cursor" in payload["stop_conditions"]
    by_id = {r["id"]: r for r in payload["rules"]}
    assert by_id["page_to_cursor"]["fixes"]
    assert by_id["rename_field"]["confidence"] > 0
    assert payload["v1_v2_diff"]
    assert payload["tools"]
    for tool in payload["tools"]:
        assert set(tool) == {"name", "description", "inputSchema"}
        assert tool["name"] and tool["description"] and isinstance(tool["inputSchema"], dict)


def test_knowledge_empty_initially(tmp_path: pathlib.Path) -> None:
    client, _factory = _client_for(tmp_path)
    try:
        r = client.get("/knowledge")
        assert r.status_code == 200, r.text
        assert r.json() == {"entries": [], "connectors": [], "counts": {"entries": 0, "reuses": 0, "tokens_saved": 0}}
        r = client.get("/capabilities")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["versions"]["v1"]["methods"] == ["GET"]
        assert any(t["name"] == "reset_demo" for t in body["tools"])
    finally:
        app.dependency_overrides.clear()


def test_knowledge_learn_reuse_flow(tmp_path: pathlib.Path) -> None:
    """Demo flow: empty -> drift -> L1 -> approve -> entry -> same drift -> L0 hit, reused +1."""
    client, factory = _client_for(tmp_path)
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
                spec=spec,
                drift_class="PAGINATION_CHANGE",
                signals=[],
                fingerprint_delta=_page_delta(),
                extra={"target_strategy": "cursor"},
                vendor="AtlasPMS",
                drift_event_ids=[drift_id],
                session=s,
            )
            assert result.decision == "propose" and result.rule_id == "page_to_cursor"
            assert result.ladder_level_reached == 1
            first_id = s.query(RepairRun).order_by(RepairRun.created_at.desc()).first().id
            s.commit()
            run_id = first_id

            # Before approval: learned nothing yet.
            s.commit()
        r = client.get("/knowledge")
        assert r.status_code == 200, r.text
        assert r.json()["entries"] == []

        with factory() as s:
            apply_patch(spec, result.candidate_patch)  # patch must apply cleanly
            from autobridge.control.models import spec_signature as _sig

            base = ConnectorRelease(connector_id=connector_id, version=1, spec=spec, signature=_sig(spec))
            s.add(base)
            s.flush()
            rel = create_release_from_patch(
                s, connector_id=connector_id, base_release_id=base.id, patch=result.candidate_patch,
                provenance_repair_run_id=run_id,
            )
            assert rel is not None
            approval = Approval(repair_run_id=run_id, actor="tester", decision="approve", to_release_id=rel.id)
            s.add(approval)
            s.commit()

        # After approval: one entry appears. The deterministic L1 repair
        # cost 0 tokens; the entry records that honestly.
        r = client.get("/knowledge")
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["entries"]) == 1
        entry = body["entries"][0]
        assert entry["vendor"] == "AtlasPMS"
        assert entry["rule_id"] == "page_to_cursor"
        assert entry["drift_class"] == "PAGINATION_CHANGE"
        assert entry["drift_signature"]
        assert entry["learned_from"]["run_id"] == run_id
        assert entry["learned_from"]["rung"] == 1
        assert entry["learned_from"]["tokens"] == 0
        assert entry["learned_from"]["cost"] == "0"
        assert entry["times_reused"] == 0
        assert entry["approvals"] == 1

        # Same drift again: L0 hit, 0 model calls, times_reused goes up.
        with factory() as s:
            ctrl2 = RepairController(knowledge_cache=KnowledgeCache())
            # First run populated a cache only in its own controller; emulate
            # the learned cache by seeding from the approved patch.
            ctrl2.knowledge_cache.put("AtlasPMS", _page_delta(), result.candidate_patch, "page_to_cursor")
            result2 = ctrl2.handle(
                spec=spec,
                drift_class="PAGINATION_CHANGE",
                signals=[],
                fingerprint_delta=_page_delta(),
                extra={"target_strategy": "cursor"},
                vendor="AtlasPMS",
                drift_event_ids=[drift_id],
                session=s,
            )
            assert result2.decision == "propose"
            assert result2.ladder_level_reached == 0
            assert result2.model_usage.get("frontier_tokens", 0) == 0
            s.commit()
            assert s.query(ModelCall).count() == 0  # deterministic path writes no ledger rows

        r = client.get("/knowledge")
        body = r.json()
        assert len(body["entries"]) == 1
        entry = body["entries"][0]
        assert entry["times_reused"] == 1
        assert entry["tokens_saved"] == 0
        assert body["counts"] == {"entries": 1, "reuses": 1, "tokens_saved": 0}
    finally:
        app.dependency_overrides.clear()


def test_knowledge_connector_refused_vocabulary(tmp_path: pathlib.Path) -> None:
    """Owner check 2026-10-29: a refused build shows as refused, the good
    build as the release in use (Meridian v1-refused / v2-in-use)."""
    client, factory = _client_for(tmp_path)
    try:
        with factory() as s:
            conn = Connector(
                name="meridianpms", base_url="http://localhost:8116", credential_ref="MERIDIAN_API_KEY",
                target_entities=["Reservation"], runtime_target={"kind": "local_process"},
            )
            s.add(conn)
            s.flush()
            s.add(BuildRun(
                connector_id=conn.id, state="succeeded",
                inputs={"spec_vocabulary": "v1", "docs_text": "x"},
                ladder_trace=[{"stage": "vocabulary_guard", "reason": "needs POST"}],
                model_usage={},
                result={"success": False, "degradation_reason": "needs POST"},
            ))
            s.add(BuildRun(
                connector_id=conn.id, state="succeeded",
                inputs={"spec_vocabulary": "v2", "docs_text": "x"},
                ladder_trace=[],
                model_usage={},
                result={"success": True},
            ))
            s.commit()
        r = client.get("/knowledge")
        assert r.status_code == 200, r.text
        connectors = r.json()["connectors"]
        assert len(connectors) == 1
        assert connectors[0]["name"] == "meridianpms"
        assert connectors[0]["vocabulary"] == "v2"
        assert connectors[0]["refused_vocabularies"] == ["v1"]
    finally:
        app.dependency_overrides.clear()


def test_knowledge_connector_profile(tmp_path: pathlib.Path) -> None:
    client, factory = _client_for(tmp_path)
    try:
        with factory() as s:
            conn = Connector(
                name="Harbor", base_url="http://localhost:8106", credential_ref="HARBOR_API_KEY",
                target_entities=["Reservation"], runtime_target={"kind": "local_process"},
            )
            s.add(conn)
            s.flush()
            build = BuildRun(
                connector_id=conn.id, state="succeeded",
                inputs={"spec_vocabulary": "v2", "docs_text": "x"},
                ladder_trace=[
                    {"stage": "resolve", "family": "pagination", "decision": "resolved", "hypothesis": "pagination:/reservations:cursor"},
                    {"stage": "compile", "connector_id": "harbor"},
                ],
                model_usage={},
                result={"success": True},
            )
            s.add(build)
            s.commit()
        r = client.get("/knowledge")
        assert r.status_code == 200, r.text
        connectors = r.json()["connectors"]
        assert len(connectors) == 1
        assert connectors[0]["name"] == "Harbor"
        assert connectors[0]["vocabulary"] == "v2"
        assert connectors[0]["confirmed_hypotheses"] == ["pagination:/reservations:cursor"]
    finally:
        app.dependency_overrides.clear()


def test_capability_rule_fixes_name_shown_once() -> None:
    """Owner check 2026-10-28: the L1 rule text must not repeat the rule id
    (the knowledge page already renders the id beside it)."""
    from autobridge.control.routes.capabilities import capabilities_payload

    payload = capabilities_payload()
    assert payload["rules"]
    for rule in payload["rules"]:
        assert rule["fixes"], rule["id"]
        assert not rule["fixes"].startswith(rule["id"]), rule
        assert "\n" not in rule["fixes"]


def test_knowledge_connector_vocabulary_falls_back_to_release(tmp_path: pathlib.Path) -> None:
    """Owner check 2026-10-28: profiles built before the pinned-vocabulary
    input show the latest release's spec schema version instead of '?'."""
    client, factory = _client_for(tmp_path)
    try:
        with factory() as s:
            conn = Connector(
                name="Harbor", base_url="http://localhost:8106", credential_ref="HARBOR_API_KEY",
                target_entities=["Reservation"], runtime_target={"kind": "local_process"},
            )
            s.add(conn)
            s.flush()
            build = BuildRun(
                connector_id=conn.id, state="succeeded",
                inputs={"docs_text": "x"},
                ladder_trace=[],
                model_usage={},
                result={"success": True},
            )
            s.add(build)
            s.flush()
            spec = dict(_load_reference_spec("harborpms"))
            spec["spec_version"] = 2
            s.add(ConnectorRelease(connector_id=conn.id, version=1, spec=spec, signature="0" * 64))
            s.commit()
        r = client.get("/knowledge")
        assert r.status_code == 200, r.text
        connectors = r.json()["connectors"]
        assert len(connectors) == 1
        assert connectors[0]["vocabulary"] == "v2"
    finally:
        app.dependency_overrides.clear()


def test_knowledge_connector_vocabulary_empty_without_evidence(tmp_path: pathlib.Path) -> None:
    """No builds and no releases: vocabulary stays empty (the page shows '?')."""
    client, factory = _client_for(tmp_path)
    try:
        with factory() as s:
            s.add(Connector(name="Bare", base_url="http://localhost:9", credential_ref="X"))
            s.commit()
        r = client.get("/knowledge")
        assert r.status_code == 200, r.text
        assert r.json()["connectors"][0]["vocabulary"] == ""
    finally:
        app.dependency_overrides.clear()
