# ruff: noqa: TC003
"""Pinned vocabulary tests (REQ-021 item 2).

A per-build ``spec_vocabulary`` (request field, else ``SPEC_VOCABULARY``
env, else ``v1``) limits discovery and the runtime: the v1 build of
POST/body-auth docs refuses deterministically at 0 model calls, the same
docs under v2 compile, and a v2 release synced under a v1 pin ends
``Blocked`` with a named reason before any HTTP. The UI and ``make demo``
show which vocabulary each build used.
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from autobridge import config as config_mod
from autobridge.control.app import app
from autobridge.control.deps import get_db
from autobridge.control.models import (
    Base,
    BuildRun,
    Connector,
    ConnectorRelease,
    SyncRun,
    session_factory,
)
from autobridge.discovery.bootstrap import build_draft_spec
from autobridge.engine.build_run import execute_build
from autobridge.engine.sync_run import resolve_sync_vocabulary
from autobridge.llm.provider import FakeProvider

MINI_POST_DOCS = """
openapi: 3.0.0
info:
  title: MiniPost
  version: 1.0.0
servers:
  - url: http://localhost:8000
paths:
  /api/things/getAll:
    post:
      operationId: getAllThings
      requestBody:
        content:
          application/json:
            schema:
              type: object
      responses:
        "200":
          description: ok
"""


# ---------------------------------------------------------------------------
# Config: env default, fail loudly on garbage
# ---------------------------------------------------------------------------


def _clear_vocab_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPEC_VOCABULARY", raising=False)


def test_spec_vocabulary_default_is_v1(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_vocab_env(monkeypatch)
    assert config_mod.spec_vocabulary_default() == "v1"


def test_spec_vocabulary_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPEC_VOCABULARY", "v2")
    assert config_mod.spec_vocabulary_default() == "v2"


def test_spec_vocabulary_env_invalid_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPEC_VOCABULARY", "v3")
    with pytest.raises(ValueError, match="SPEC_VOCABULARY"):
        config_mod.spec_vocabulary_default()


def test_runtime_max_vocabulary() -> None:
    assert config_mod.runtime_max_vocabulary() == "v2"


def test_build_rejects_unknown_vocabulary_value() -> None:
    with pytest.raises(ValueError, match="spec_vocabulary"):
        build_draft_spec("docs", source="x.md", spec_vocabulary="v3")


def test_none_resolves_to_env_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPEC_VOCABULARY", "v2")
    result = build_draft_spec(
        MINI_POST_DOCS, source="mini-post.yaml", connector_id="mini", spec_vocabulary=None
    )
    assert result.draft_spec is not None


# ---------------------------------------------------------------------------
# The refusal itself is deterministic: 0 frontier and 0 small-model tokens
# ---------------------------------------------------------------------------


def test_v1_refusal_costs_zero_model_calls() -> None:
    fake = FakeProvider()
    out = execute_build(
        MINI_POST_DOCS,
        source="mini-post.yaml",
        connector_id="mini",
        provider=fake,
        spec_vocabulary="v1",
    )
    assert out["success"] is False
    assert out["draft_spec"] is None
    assert fake.calls == []
    assert out["model_usage"].get("calls", 0) == 0
    assert out["model_usage"].get("frontier_tokens", 0) == 0
    assert out["model_usage"].get("small_tokens", 0) == 0
    assert any(t.get("stage") == "vocabulary_guard" for t in out["trace"])


def test_v2_compiles_same_docs() -> None:
    fake = FakeProvider()
    out = execute_build(
        MINI_POST_DOCS,
        source="mini-post.yaml",
        connector_id="mini",
        provider=fake,
        spec_vocabulary="v2",
    )
    assert out["draft_spec"] is not None
    assert out["draft_spec"]["spec_version"] == 2
    assert fake.calls == []


# ---------------------------------------------------------------------------
# Sync pin resolution: request > provenance > runtime max
# ---------------------------------------------------------------------------


def test_resolve_sync_vocabulary_prefers_request(tmp_path: pathlib.Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path}/vocab.db")
    Base.metadata.create_all(engine)
    factory = session_factory(engine)
    with factory() as s:
        assert resolve_sync_vocabulary(s, release=None, requested="v1") == "v1"
        assert resolve_sync_vocabulary(s, release=None, requested="v2") == "v2"
        # No provenance: the runtime default constrains nothing.
        assert resolve_sync_vocabulary(s, release=None, requested=None) == "v2"


def test_resolve_sync_vocabulary_prefers_provenance(tmp_path: pathlib.Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path}/vocab2.db")
    Base.metadata.create_all(engine)
    factory = session_factory(engine)
    with factory() as s:
        conn = Connector(
            name="V",
            base_url="http://example.com",
            credential_ref="K",
            target_entities=["Reservation"],
            runtime_target={"kind": "local_process"},
        )
        s.add(conn)
        s.flush()
        build = BuildRun(connector_id=conn.id, state="succeeded", inputs={"spec_vocabulary": "v1"})
        s.add(build)
        s.flush()
        rel = ConnectorRelease(connector_id=conn.id, version=1, spec={"spec_version": 1}, signature="s")
        rel.provenance_build_run_id = build.id
        s.add(rel)
        s.flush()
        assert resolve_sync_vocabulary(s, rel, requested=None) == "v1"
        assert resolve_sync_vocabulary(s, rel, requested="v2") == "v2"


# ---------------------------------------------------------------------------
# Sync gate via handler: v2 release under a v1 pin ends Blocked, no HTTP
# ---------------------------------------------------------------------------


def _v2_release(
    factory: Any,
    pin: str | None = None,
    connector_id: str | None = None,
    spec: dict[str, Any] | None = None,
) -> str:
    with factory() as s:
        if connector_id is None:
            conn = Connector(
                name="V2",
                base_url="https://example.com",
                credential_ref="K",
                target_entities=["Reservation"],
                runtime_target={"kind": "local_process"},
            )
            s.add(conn)
            s.flush()
            connector_id = conn.id
        build_vocab: dict[str, Any] = {"spec_vocabulary": pin} if pin else {}
        build = BuildRun(connector_id=connector_id, state="succeeded", inputs=build_vocab)
        s.add(build)
        s.flush()
        rel = ConnectorRelease(
            connector_id=connector_id,
            version=1,
            spec=spec if spec is not None else {"spec_version": 2, "connector": {"id": "v2", "vendor": "V"}},
            signature="s",
        )
        rel.provenance_build_run_id = build.id
        s.add(rel)
        s.flush()
        conn = s.get(Connector, connector_id)
        assert conn is not None
        conn.active_release_id = rel.id
        s.commit()
        return rel.id


def _handler_ctx(factory: Any, payload: dict[str, Any]) -> Any:
    from autobridge.control.jobs import JobContext

    s = factory()
    return JobContext(
        job_id="job1", kind="sync", run_id=None, payload=payload, attempt=1, session=s, worker_id="w1"
    )


def test_sync_gate_blocks_v2_under_v1_pin(tmp_path: pathlib.Path) -> None:
    from autobridge.engine.sync_run import run_sync_handler

    engine = create_engine(f"sqlite:///{tmp_path}/vocab3.db")
    Base.metadata.create_all(engine)
    factory = session_factory(engine)
    release_id = _v2_release(factory)

    def _boom(_spec: dict) -> Any:
        raise AssertionError("no HTTP client may be built under a v1 pin")

    ctx = _handler_ctx(factory, {"release_id": release_id, "spec_vocabulary": "v1"})
    try:
        result = run_sync_handler(ctx, http_client_factory=_boom)
        ctx.session.commit()
    finally:
        ctx.session.close()
    assert result["outcome"] == "Blocked"
    assert result["unmet_criteria"] == ["vocabulary_v1_cannot_execute_spec_v2"]
    with factory() as s:
        row = s.get(SyncRun, result["sync_run_id"])
        assert row is not None and row.outcome == "Blocked"


def test_sync_gate_blocks_v2_under_provenance_v1(tmp_path: pathlib.Path) -> None:
    from autobridge.engine.sync_run import run_sync_handler

    engine = create_engine(f"sqlite:///{tmp_path}/vocab4.db")
    Base.metadata.create_all(engine)
    factory = session_factory(engine)
    release_id = _v2_release(factory, pin="v1")

    def _boom(_spec: dict) -> Any:
        raise AssertionError("no HTTP client may be built under a v1 pin")

    ctx = _handler_ctx(factory, {"release_id": release_id})
    try:
        result = run_sync_handler(ctx, http_client_factory=_boom)
        ctx.session.commit()
    finally:
        ctx.session.close()
    assert result["outcome"] == "Blocked"
    assert result["unmet_criteria"] == ["vocabulary_v1_cannot_execute_spec_v2"]


def test_sync_gate_unknown_spec_version_fails_closed(tmp_path: pathlib.Path) -> None:
    from autobridge.control.jobs import JobContext
    from autobridge.engine.sync_run import run_sync_handler

    engine = create_engine(f"sqlite:///{tmp_path}/vocab5.db")
    Base.metadata.create_all(engine)
    factory = session_factory(engine)
    release_id = _v2_release(factory, spec={"connector": {"id": "noversion"}})
    ctx = JobContext(
        job_id="job1", kind="sync", run_id=None, payload={"release_id": release_id},
        attempt=1, session=factory(), worker_id="w1",
    )
    try:
        result = run_sync_handler(ctx)
        ctx.session.commit()
    finally:
        ctx.session.close()
    assert result["outcome"] == "Blocked"
    assert result["unmet_criteria"] == ["unknown_spec_version"]


# ---------------------------------------------------------------------------
# API: request field, env default, refusal end to end, sync Blocked
# ---------------------------------------------------------------------------


def _api_client(tmp_path: pathlib.Path) -> tuple[TestClient, Any]:
    engine = create_engine(f"sqlite:///{tmp_path}/vocab_api.db")
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


def _create_connector(client: TestClient) -> str:
    r = client.post(
        "/connectors",
        json={
            "name": "Vocab Test",
            "base_url": "http://localhost:8105",
            "credential_ref": "ATLAS_API_KEY",
            "target_entities": ["Reservation"],
            "runtime_target": {"kind": "local_process"},
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_build_api_records_vocabulary_and_refuses_v1(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, factory = _api_client(tmp_path)
    try:
        monkeypatch.delenv("SPEC_VOCABULARY", raising=False)
        connector_id = _create_connector(client)
        r = client.post(
            f"/connectors/{connector_id}/build",
            json={"docs_text": MINI_POST_DOCS, "spec_vocabulary": "v2"},
        )
        assert r.status_code == 200, r.text
        with factory() as s:
            run = s.get(BuildRun, r.json()["build_run_id"])
            assert run is not None
            assert run.inputs.get("spec_vocabulary") == "v2"

        # Default pin (v1): the same docs refuse — no release, named reason.
        r = client.post(f"/connectors/{connector_id}/build", json={"docs_text": MINI_POST_DOCS})
        assert r.status_code == 200, r.text
        with factory() as s:
            run = s.get(BuildRun, r.json()["build_run_id"])
            assert run is not None
            assert run.inputs.get("spec_vocabulary") == "v1"
            assert run.result is not None and run.result.get("success") is False
            assert any(t.get("stage") == "vocabulary_guard" for t in run.ladder_trace or [])
            assert (run.model_usage or {}).get("calls", 0) == 0
            assert s.query(ConnectorRelease).count() == 0
    finally:
        app.dependency_overrides.clear()


def test_build_api_rejects_bad_vocabulary(tmp_path: pathlib.Path) -> None:
    client, _factory = _api_client(tmp_path)
    try:
        connector_id = _create_connector(client)
        r = client.post(
            f"/connectors/{connector_id}/build",
            json={"docs_text": "docs", "spec_vocabulary": "v3"},
        )
        assert r.status_code == 422, r.text
    finally:
        app.dependency_overrides.clear()


def test_build_api_env_default(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, factory = _api_client(tmp_path)
    try:
        monkeypatch.setenv("SPEC_VOCABULARY", "v2")
        connector_id = _create_connector(client)
        r = client.post(f"/connectors/{connector_id}/build", json={"docs_text": "docs"})
        assert r.status_code == 200, r.text
        with factory() as s:
            run = s.get(BuildRun, r.json()["build_run_id"])
            assert run is not None
            assert run.inputs.get("spec_vocabulary") == "v2"
    finally:
        app.dependency_overrides.clear()


def test_sync_api_blocked_under_v1_pin(tmp_path: pathlib.Path) -> None:
    client, factory = _api_client(tmp_path)
    try:
        connector_id = _create_connector(client)
        release_id = _v2_release(factory, connector_id=connector_id)
        r = client.post(
            f"/connectors/{connector_id}/sync",
            json={"release_id": release_id, "spec_vocabulary": "v1"},
        )
        assert r.status_code == 200, r.text
        with factory() as s:
            runs = s.query(SyncRun).all()
            assert len(runs) == 1
            assert runs[0].outcome == "Blocked"
    finally:
        app.dependency_overrides.clear()
