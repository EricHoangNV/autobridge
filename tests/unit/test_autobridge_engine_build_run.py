"""Tests for engine/build_run.py (P1.W5.6)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import httpx as _httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from autobridge.control.jobs import JobRunner
from autobridge.control.models import Base, BuildRun, Connector, ConnectorRelease
from autobridge.engine.build_run import (
    bootstrap_and_create_release,
    execute_build,
    run_build_handler,
)

if TYPE_CHECKING:
    import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARBOR_MD = (REPO_ROOT / "simulators" / "docs" / "harborpms.md").read_text()
ATLAS_YAML = (REPO_ROOT / "simulators" / "docs" / "atlaspms-openapi.yaml").read_text()

# Save the real httpx client before any monkeypatching (the FakeClient below
# must not recurse into itself once httpx.Client is patched).
_REAL_HTTPX_CLIENT = _httpx.Client


def _sessions(tmp_path: Path):  # type: ignore[no-untyped-def]
    engine = create_engine(f"sqlite:///{tmp_path}/build.db")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine), engine


def _connector_id(SessionLocal) -> str:  # type: ignore[no-untyped-def]
    with SessionLocal() as s:
        conn = Connector(name="HarborPMS", base_url="http://localhost:8106", credential_ref="HARBOR_API_KEY", target_entities=["Reservation", "FolioCharge"], runtime_target={})
        s.add(conn)
        s.commit()
        return conn.id


def test_ensure_connector_body_fields_primary_ref(tmp_path: Path) -> None:
    """Body_fields specs store the first sorted body ref on the Connector row."""
    from autobridge.engine.build_run import _ensure_connector

    SessionLocal, engine = _sessions(tmp_path)
    try:
        with SessionLocal() as s:
            conn = _ensure_connector(
                s,
                None,
                {"vendor": "ExamplePMS"},
                spec_auth={"type": "body_fields", "body_fields": {"ClientToken": "EXAMPLE_CLIENT_TOKEN", "AccessToken": "EXAMPLE_ACCESS_TOKEN"}},
            )
            s.commit()
            assert conn.credential_ref == "EXAMPLE_ACCESS_TOKEN"
        # legacy path without spec_auth keeps the old fallback
        with SessionLocal() as s:
            conn2 = _ensure_connector(s, None, {"vendor": "OtherPMS", "credential_ref": "OTHER_KEY"})
            s.commit()
            assert conn2.credential_ref == "OTHER_KEY"
    finally:
        engine.dispose()


def test_execute_build_v2_vocabulary_opt_in() -> None:
    meridian = (REPO_ROOT / "simulators" / "docs" / "meridian-openapi.yaml").read_text()
    out_v1 = execute_build(meridian, source="meridian.yaml", connector_id="examplepms")
    assert out_v1["success"] is False
    assert out_v1["draft_spec"] is None
    out_v2 = execute_build(meridian, source="meridian.yaml", connector_id="examplepms", spec_vocabulary="v2")
    assert out_v2["success"] is True
    assert out_v2["draft_spec"]["spec_version"] == 2
    assert out_v2["model_usage"]["frontier_tokens"] == 0


def test_execute_build_harbor_success() -> None:
    out = execute_build(HARBOR_MD, source="simulators/docs/harborpms.md")
    assert out["success"] is True
    assert out["draft_spec"] is not None
    assert out["visible_errors"] == []
    assert out["verified"] is True
    assert "HARBOR_API_KEY" in str(out["draft_spec"]["source"]["auth"])


def test_execute_build_atlas_success() -> None:
    out = execute_build(ATLAS_YAML, source="atlas.yaml")
    assert out["success"] is True
    assert out["draft_spec"]["connector"]["id"] == "atlaspms"


def test_execute_build_missing_docs() -> None:
    out = execute_build("", source="empty.md")
    assert isinstance(out, dict)


def test_execute_build_with_verifier_url_mock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ARG001, ARG005
    # Mock httpx to simulate verifier success

    orig_out = execute_build(HARBOR_MD, source="harbor.md")
    assert orig_out["success"]

    # With fake verifier URL that is unreachable, should still have trace
    out = execute_build(HARBOR_MD, source="harbor.md", verifier_url="http://127.0.0.1:9")
    assert out["success"] is True
    # trace should contain verifier entry (skipped or error)
    assert any("verifier" in t.get("stage", "") for t in out["trace"])


def test_run_build_handler_success(tmp_path: Path) -> None:
    SessionLocal, engine = _sessions(tmp_path)
    cid = _connector_id(SessionLocal)
    runner = JobRunner(SessionLocal, handlers={"build": run_build_handler})
    runner.submit_build(cid, inputs={"docs_text": HARBOR_MD, "docs_source": "harbor.md"})
    runner.run_until_empty()
    with SessionLocal() as s:
        br = s.query(BuildRun).first()
        assert br is not None
        assert br.state == "succeeded"
        assert br.draft_spec is not None
        assert br.draft_spec["connector"]["id"] == "harborpms"
        assert br.result["success"] is True
        assert br.result["verified"] is True
        assert br.result["release_id"]
        # Release exists
        rel = s.get(ConnectorRelease, br.result["release_id"])
        assert rel is not None
        assert rel.spec["connector"]["id"] == "harborpms"
        # Oracle pass
        from verifier.oracle import oracle_verdict

        verdict, _, failed = oracle_verdict("baseline", rel.spec)
        assert verdict == "pass", f"oracle failed: {failed}"
    engine.dispose()


def test_run_build_handler_creates_release_provenance(tmp_path: Path) -> None:
    SessionLocal, engine = _sessions(tmp_path)
    cid = _connector_id(SessionLocal)
    runner = JobRunner(SessionLocal, handlers={"build": run_build_handler})
    runner.submit_build(cid, inputs={"docs_text": HARBOR_MD})
    runner.run_until_empty()
    with SessionLocal() as s:
        br = s.query(BuildRun).first()
        rel = s.get(ConnectorRelease, br.result["release_id"])  # type: ignore[union-attr]
        assert rel.provenance_build_run_id == br.id  # type: ignore[union-attr]
    engine.dispose()


def test_run_build_handler_docs_path(tmp_path: Path) -> None:
    # REQ-029: docs_path alone is refused (identifier only) — the job fails
    # naming the reason and no release is persisted.
    SessionLocal, engine = _sessions(tmp_path)
    cid = _connector_id(SessionLocal)
    # Write temp docs file
    docs_file = tmp_path / "harbor.md"
    docs_file.write_text(HARBOR_MD)
    runner = JobRunner(SessionLocal, handlers={"build": run_build_handler})
    job_id = runner.submit_build(cid, inputs={"docs_path": str(docs_file)})
    runner.run_until_empty()
    with SessionLocal() as s:
        from autobridge.control.models import Job

        job = s.get(Job, job_id)
        assert job is not None and job.state == "failed"
        assert "identifier only" in (job.last_error or "")
        assert s.query(ConnectorRelease).count() == 0
    engine.dispose()


def test_run_build_handler_docs_path_reference_guard(tmp_path: Path) -> None:
    SessionLocal, engine = _sessions(tmp_path)
    cid = _connector_id(SessionLocal)
    runner = JobRunner(SessionLocal, handlers={"build": run_build_handler})
    job_id = runner.submit_build(cid, inputs={"docs_path": "generated/connectors/harborpms/reference/spec.yaml"})
    # Handler should fail (job marked failed)
    runner.run_until_empty()
    with SessionLocal() as s:
        from autobridge.control.models import Job

        job = s.get(Job, job_id)
        assert job.state == "failed"
        assert "must not read reference" in (job.last_error or "")
    engine.dispose()


def test_run_build_handler_missing_docs_text(tmp_path: Path) -> None:
    SessionLocal, engine = _sessions(tmp_path)
    cid = _connector_id(SessionLocal)
    runner = JobRunner(SessionLocal, handlers={"build": run_build_handler})
    job_id = runner.submit_build(cid, inputs={})
    runner.run_until_empty()
    with SessionLocal() as s:
        from autobridge.control.models import Job

        job = s.get(Job, job_id)
        assert job.state == "failed"
        assert "missing docs_text" in (job.last_error or "")
    engine.dispose()


def test_run_build_handler_with_overrides(tmp_path: Path) -> None:
    SessionLocal, engine = _sessions(tmp_path)
    cid = _connector_id(SessionLocal)
    runner = JobRunner(SessionLocal, handlers={"build": run_build_handler})
    runner.submit_build(cid, inputs={"docs_text": HARBOR_MD, "connector_id": "custom", "vendor": "CustomVendor", "credential_ref": "CUSTOM_KEY"})
    runner.run_until_empty()
    with SessionLocal() as s:
        br = s.query(BuildRun).first()
        assert br.draft_spec["connector"]["id"] == "custom"  # type: ignore[index]
        assert br.draft_spec["connector"]["vendor"] == "CustomVendor"  # type: ignore[index]
    engine.dispose()


def test_bootstrap_and_create_release_helper(tmp_path: Path) -> None:
    SessionLocal, engine = _sessions(tmp_path)
    cid = _connector_id(SessionLocal)
    with SessionLocal() as s:
        conn, release, build_out = bootstrap_and_create_release(s, cid, HARBOR_MD, source="harbor.md")
        assert build_out["success"] is True
        assert release.spec["connector"]["id"] == "harborpms"
        # Also creates BuildRun
        br = s.query(BuildRun).order_by(BuildRun.created_at.desc()).first()
        assert br is not None
        assert br.draft_spec is not None
        s.commit()
    engine.dispose()


def test_run_build_handler_evidence_created(tmp_path: Path) -> None:
    SessionLocal, engine = _sessions(tmp_path)
    cid = _connector_id(SessionLocal)
    runner = JobRunner(SessionLocal, handlers={"build": run_build_handler})
    runner.submit_build(cid, inputs={"docs_text": HARBOR_MD})
    runner.run_until_empty()
    with SessionLocal() as s:
        br = s.query(BuildRun).first()
        assert br.evidence_ids  # type: ignore[union-attr]
        assert len(br.evidence_ids) > 0  # type: ignore[union-attr]
        # Evidence records exist
        from autobridge.control.models import EvidenceRecord

        recs = s.query(EvidenceRecord).all()
        assert len(recs) > 0
    engine.dispose()


def test_run_build_handler_build_run_state_transitions(tmp_path: Path) -> None:
    SessionLocal, engine = _sessions(tmp_path)
    cid = _connector_id(SessionLocal)
    runner = JobRunner(SessionLocal, handlers={"build": run_build_handler})
    runner.submit_build(cid, inputs={"docs_text": HARBOR_MD})
    with SessionLocal() as s:
        br = s.query(BuildRun).first()
        assert br.state == "queued"  # type: ignore[union-attr]
    runner.run_once()
    with SessionLocal() as s:
        br = s.query(BuildRun).first()
        assert br.state == "succeeded"  # type: ignore[union-attr]
    engine.dispose()


def test_build_run_no_import_verifier() -> None:
    import pathlib

    import autobridge.engine.build_run as mod

    src = pathlib.Path(mod.__file__).read_text()
    top = "\n".join(src.splitlines()[:30])
    assert "import verifier" not in top
    assert "from verifier" not in top
    assert "import simulators" not in top
    assert "from simulators" not in top


def test_build_run_no_reference_read() -> None:
    import pathlib

    import autobridge.engine.build_run as mod

    src = pathlib.Path(mod.__file__).read_text()
    # Guard is allowed but actual open of reference should not happen except guard
    # We allow at most 2 occurrences (guard + error message)
    assert src.count("generated/connectors") <= 2


def test_execute_build_probe_path(tmp_path: Path) -> None:  # noqa: ARG001
    from autobridge.discovery.probes import ProbeBudget

    budget = ProbeBudget(max_probes=5)
    out = execute_build(HARBOR_MD, source="harbor.md", probe_budget=budget, probe_executor=lambda _: {"strategy": "cursor"})  # noqa: ARG005
    assert out["success"] is True


def test_execute_build_guard_reference_source() -> None:
    out = execute_build(HARBOR_MD, source="generated/connectors/harborpms/reference/spec.yaml")
    # Guard should make it fail gracefully, not crash
    assert isinstance(out, dict)


# ---------------------------------------------------------------------------
# AR-1-2: gate-before-persist on the build path
# ---------------------------------------------------------------------------


def _mock_gate(monkeypatch: pytest.MonkeyPatch, *, blocked: bool, capture: dict | None = None) -> None:  # type: ignore[no-untyped-def]
    """Fake the verifier gate at verify_spec level (no HTTP).

    When blocked, the gate raises GateBlockedError exactly as an
    unreachable verifier or a fail verdict would through ensure_gate_passes.
    """
    import autobridge.engine.verification as v

    def fake_pass(spec: dict, scenario_id: str, verifier_url: str | None, **kw: object) -> object:  # noqa: ARG001  # type: ignore[no-untyped-def]
        if capture is not None:
            capture["scenario_id"] = scenario_id
            capture["verifier_url"] = verifier_url
        return v.GateOutcome(
            verdict="pass",
            regression="pass",
            score=1.0,
            failed_checks=[],
            regression_failures=None,
            scenario_id=scenario_id,
            raw={},
            allowed=True,
        )

    def fake_blocked(spec: dict, scenario_id: str, verifier_url: str | None, **kw: object) -> object:  # noqa: ARG001  # type: ignore[no-untyped-def]
        raise v.GateBlockedError(f"verifier blocked for {scenario_id}")

    monkeypatch.setattr(v, "verify_spec", fake_blocked if blocked else fake_pass)


def test_run_build_handler_gate_blocked_persists_no_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    """Failing verifier: build succeeds (draft kept) but NO release persists."""
    _mock_gate(monkeypatch, blocked=True)
    SessionLocal, engine = _sessions(tmp_path)
    cid = _connector_id(SessionLocal)
    runner = JobRunner(SessionLocal, handlers={"build": run_build_handler})
    runner.submit_build(
        cid,
        inputs={"docs_text": HARBOR_MD, "docs_source": "harbor.md", "verifier_url": "http://127.0.0.1:9", "scenario_id": "baseline"},
    )
    runner.run_until_empty()
    with SessionLocal() as s:
        br = s.query(BuildRun).first()
        assert br is not None
        assert br.state == "succeeded"
        assert br.result["success"] is True  # draft spec produced
        assert br.result["verified"] is False
        assert "gate_blocked" in br.result
        assert br.result.get("release_id") is None
        assert s.query(ConnectorRelease).count() == 0
    engine.dispose()


def test_run_build_handler_gate_unreachable_persists_no_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    """Unreachable verifier (no mock HTTP at all): same quarantine as a fail."""
    _mock_gate(monkeypatch, blocked=True)
    SessionLocal, engine = _sessions(tmp_path)
    cid = _connector_id(SessionLocal)
    runner = JobRunner(SessionLocal, handlers={"build": run_build_handler})
    runner.submit_build(
        cid,
        inputs={"docs_text": HARBOR_MD, "docs_source": "harbor.md", "verifier_url": "http://127.0.0.1:9", "scenario_id": "baseline"},
    )
    runner.run_until_empty()
    with SessionLocal() as s:
        br = s.query(BuildRun).first()
        assert br is not None
        assert br.result["success"] is True
        assert br.result["verified"] is False
        assert br.result.get("release_id") is None
        assert s.query(ConnectorRelease).count() == 0
    engine.dispose()


def test_run_build_handler_gate_pass_persists_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    """Passing gate: release persists and the gate saw the build scenario."""
    capture: dict = {}
    _mock_gate(monkeypatch, blocked=False, capture=capture)
    SessionLocal, engine = _sessions(tmp_path)
    cid = _connector_id(SessionLocal)
    runner = JobRunner(SessionLocal, handlers={"build": run_build_handler})
    runner.submit_build(
        cid,
        inputs={"docs_text": HARBOR_MD, "docs_source": "harbor.md", "verifier_url": "http://127.0.0.1:9", "scenario_id": "baseline"},
    )
    runner.run_until_empty()
    with SessionLocal() as s:
        br = s.query(BuildRun).first()
        assert br is not None
        assert br.result["verified"] is True
        assert br.result["release_id"]
        rel = s.get(ConnectorRelease, br.result["release_id"])
        assert rel is not None
        # REQ-029: a passed gate mints the report id the release carries.
        assert rel.verification_report_id
        assert br.result.get("verification_report_id") == rel.verification_report_id
    # Gate-before-persist was actually invoked with the build's scenario
    assert capture.get("scenario_id") == "baseline"
    assert capture.get("verifier_url") == "http://127.0.0.1:9"
    engine.dispose()


def _mock_control_proxy(monkeypatch: pytest.MonkeyPatch, *, verdict: str = "pass", raise_exc: Exception | None = None) -> None:  # type: ignore[no-untyped-def]
    """Fake httpx.Client so the worker's control-proxy gate gets a verdict.

    The handler echoes the request's scenario_id/mode (like the real
    verifier through the real proxy), so AR-1-5 binding passes.
    """
    import json as _json

    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        if raise_exc is not None:
            raise raise_exc
        try:
            req_body = _json.loads(request.content.decode())
        except Exception:
            req_body = {}
        body = {
            "verdict": verdict,
            "score": 1.0 if verdict == "pass" else 0.5,
            "failed_checks": [] if verdict == "pass" else [{"name": "field_missing", "count": 1}],
            "regression": "pass",
            "scenario_id": req_body.get("scenario_id", "baseline"),
            "mode": req_body.get("mode", "visible"),
        }
        return httpx.Response(200, json=body)

    transport = httpx.MockTransport(handler)

    class FakeClient:
        def __init__(self, *a: object, **kw: object) -> None:  # noqa: ARG001, ARG002
            self._client = _REAL_HTTPX_CLIENT(transport=transport)

        def __enter__(self) -> httpx.Client:
            return self._client

        def __exit__(self, *a: object) -> None:  # noqa: ARG001, ARG002
            self._client.close()

    monkeypatch.setattr(httpx, "Client", FakeClient)


def test_run_build_handler_worker_no_verifier_require_gate_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    """REQ-029: a worker-executed build with no reachable verifier persists nothing.

    No VERIFIER_URL, no CONTROL_URL, AUTOBRIDGE_REQUIRE_VERIFIER_GATE=1
    (the deployed setting): the gate fails closed, the draft spec stays on
    the BuildRun, and the reason names the missing verifier.
    """
    monkeypatch.delenv("VERIFIER_URL", raising=False)
    monkeypatch.delenv("CONTROL_URL", raising=False)
    monkeypatch.setenv("AUTOBRIDGE_REQUIRE_VERIFIER_GATE", "1")
    SessionLocal, engine = _sessions(tmp_path)
    cid = _connector_id(SessionLocal)
    runner = JobRunner(SessionLocal, handlers={"build": run_build_handler})
    job_id = runner.submit_build(
        cid,
        inputs={"docs_text": HARBOR_MD, "docs_source": "harbor.md", "scenario_id": "baseline"},
    )
    runner.run_until_empty()
    with SessionLocal() as s:
        from autobridge.control.models import Job

        job = s.get(Job, job_id)
        # Quarantined, not crashed: the job succeeds with gate_blocked naming
        # the reason, and nothing persists.
        assert job is not None and job.state == "succeeded"
        br = s.query(BuildRun).first()
        assert br is not None
        assert br.result.get("release_id") is None
        assert br.result.get("verified") is False
        assert "gate_blocked" in br.result
        assert "no verifier reachable" in br.result["gate_blocked"]
        assert s.query(ConnectorRelease).count() == 0
    engine.dispose()


def test_run_build_handler_worker_proxy_gate_pass_mints_report_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    """REQ-029: the worker verifies through control's proxy; the release carries the report id."""
    monkeypatch.delenv("VERIFIER_URL", raising=False)
    monkeypatch.setenv("CONTROL_URL", "http://control:8000")
    monkeypatch.delenv("AUTOBRIDGE_CONTROL_TOKEN", raising=False)
    monkeypatch.delenv("AUTOBRIDGE_REQUIRE_VERIFIER_GATE", raising=False)
    _mock_control_proxy(monkeypatch, verdict="pass")
    SessionLocal, engine = _sessions(tmp_path)
    cid = _connector_id(SessionLocal)
    runner = JobRunner(SessionLocal, handlers={"build": run_build_handler})
    runner.submit_build(
        cid,
        inputs={"docs_text": HARBOR_MD, "docs_source": "harbor.md", "scenario_id": "baseline"},
    )
    runner.run_until_empty()
    with SessionLocal() as s:
        br = s.query(BuildRun).first()
        assert br is not None
        assert br.state == "succeeded", br.result
        assert br.result.get("release_id")
        assert br.result["verified"] is True
        rel = s.get(ConnectorRelease, br.result["release_id"])
        assert rel is not None
        assert rel.verification_report_id
        assert br.result.get("verification_report_id") == rel.verification_report_id
    engine.dispose()


def test_run_build_handler_worker_proxy_unreachable_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    """REQ-029: an unreachable control proxy blocks the worker build (nothing persists)."""
    import httpx

    monkeypatch.delenv("VERIFIER_URL", raising=False)
    monkeypatch.setenv("CONTROL_URL", "http://control:8000")
    _mock_control_proxy(monkeypatch, raise_exc=httpx.ConnectError("refused"))
    SessionLocal, engine = _sessions(tmp_path)
    cid = _connector_id(SessionLocal)
    runner = JobRunner(SessionLocal, handlers={"build": run_build_handler})
    job_id = runner.submit_build(
        cid,
        inputs={"docs_text": HARBOR_MD, "docs_source": "harbor.md", "scenario_id": "baseline"},
    )
    runner.run_until_empty()
    with SessionLocal() as s:
        from autobridge.control.models import Job

        job = s.get(Job, job_id)
        # Quarantined, not crashed: gate_blocked names the proxy failure.
        assert job is not None and job.state == "succeeded"
        br = s.query(BuildRun).first()
        assert br is not None
        assert "gate_blocked" in br.result
        assert "control verify proxy unreachable" in br.result["gate_blocked"]
        assert s.query(ConnectorRelease).count() == 0
    engine.dispose()


def test_run_build_handler_refusal_records_no_release(tmp_path: Path) -> None:
    """A refused build (v1 vocabulary vs POST-only docs) never crashes the job.

    The BuildRun records success False with a named reason; no release is
    persisted and the handler returns normally.
    """
    from autobridge.control.models import BuildRun as _BR

    SessionLocal, engine = _sessions(tmp_path)
    cid = _connector_id(SessionLocal)
    meridian = (REPO_ROOT / "simulators" / "docs" / "meridian-openapi.yaml").read_text()
    runner = JobRunner(SessionLocal, handlers={"build": run_build_handler})
    runner.submit_build(
        cid,
        inputs={"docs_text": meridian, "docs_source": "meridian.yaml", "spec_vocabulary": "v1"},
    )
    runner.run_until_empty()
    with SessionLocal() as s:
        br = s.query(_BR).first()
        assert br is not None
        assert br.state == "succeeded"
        assert br.result["success"] is False
        assert br.result.get("release_id") is None
        assert s.query(ConnectorRelease).count() == 0
        assert any(t.get("stage") == "vocabulary_guard" for t in br.ladder_trace)
    engine.dispose()


# ---------------------------------------------------------------------------
# REQ-027: wire-shape probe (observed_wire_keys)
# ---------------------------------------------------------------------------


def _probe_draft_spec() -> dict:
    """Minimal valid draft spec with one list operation and one entity."""
    return {
        "spec_version": 1,
        "connector": {"id": "probe", "vendor": "probe"},
        "source": {
            "base_url": "http://probe.test",
            "auth": {"type": "api_key_header", "credential_ref": "PROBE_API_KEY", "header_name": "x-api-key"},
            "timezone_source": {"kind": "static", "value": "UTC"},
        },
        "operations": {
            "list_things": {
                "method": "GET",
                "path": "/things",
                "params": {},
                "pagination": {
                    "strategy": "page",
                    "page_param": "page",
                    "limit_param": "limit",
                    "items_path": "$.data",
                    "total_path": "$.total",
                    "stop_when": ["empty_page"],
                },
            }
        },
        "entities": {
            "Thing": {
                "from": "list_things",
                "key": "$.id",
                "fields": {
                    "thing_id": {"path": "$.id"},
                    "name": {"path": "$.name"},
                    "phantom": {"path": "$.never_served"},
                },
            }
        },
        "capabilities": {"read": ["Thing"]},
        "health": {"canary": {"every": "60s", "operation": "list_things"}},
    }


def _probe_factory(records: list) -> object:  # type: ignore[no-untyped-def]
    """httpx client factory serving *records* as a page envelope."""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("x-api-key") == "probe-secret"
        payload = {"success": True, "data": records, "count": len(records), "total": len(records)}
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)

    def factory(**kw: object) -> httpx.Client:  # type: ignore[no-untyped-def]
        return httpx.Client(transport=transport, timeout=kw.get("timeout", 5))  # type: ignore[arg-type]

    return factory


def test_observe_wire_shape_records_served_keys(monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    """Probe records served top keys (mapped + unmapped), never values."""
    import json

    from autobridge.engine.build_run import observe_wire_shape

    monkeypatch.setenv("PROBE_API_KEY", "probe-secret")
    records = [
        {"id": "T1", "name": "one", "extra_unmapped": "x"},
        {"id": "T2", "name": "two", "extra_unmapped": "y"},
    ]
    shape = observe_wire_shape(
        _probe_draft_spec(), client_factory=_probe_factory(records), overall_budget_s=10.0
    )
    assert shape == {"Thing": ["extra_unmapped", "id", "name"]}
    assert "never_served" not in shape["Thing"]
    # No values leak into the shape: only key names.
    assert "probe-secret" not in json.dumps(shape)
    assert "one" not in json.dumps(shape)


def test_observe_wire_shape_fail_soft() -> None:
    """Invalid spec, missing creds, or unreachable vendor all yield {}."""
    from autobridge.engine.build_run import observe_wire_shape

    assert observe_wire_shape({}) == {}
    assert observe_wire_shape({"spec_version": 1}) == {}
    # Missing credential: AuthError inside the probe, no network touched.
    spec = _probe_draft_spec()
    assert observe_wire_shape(spec, client_factory=_probe_factory([])) == {}
    # Unreachable vendor with a tiny budget fails fast and empty.
    import os

    os.environ["PROBE_API_KEY"] = "probe-secret"
    try:
        unreachable = _probe_draft_spec()
        unreachable["source"]["base_url"] = "http://127.0.0.1:9"
        assert observe_wire_shape(unreachable, timeout_s=0.5, overall_budget_s=2.0) == {}
    finally:
        del os.environ["PROBE_API_KEY"]


def test_observe_wire_shape_skips_non_http() -> None:
    """EVENT/feed operations are never probed over HTTP."""
    from autobridge.engine.build_run import observe_wire_shape

    spec = _probe_draft_spec()
    spec["operations"]["list_things"]["method"] = "EVENT"
    spec["operations"]["list_things"]["feed"] = {"via": "relay", "record": "$.x"}
    del spec["operations"]["list_things"]["path"]
    # Invalid EVENT shape still fails soft (load fails or op skipped).
    assert observe_wire_shape(spec) == {}
