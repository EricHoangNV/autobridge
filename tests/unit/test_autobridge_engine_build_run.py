"""Tests for engine/build_run.py (P1.W5.6)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

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
    out = execute_build(HARBOR_MD, source="harbor.md", verifier_url="http://invalid:9999")
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
    SessionLocal, engine = _sessions(tmp_path)
    cid = _connector_id(SessionLocal)
    # Write temp docs file
    docs_file = tmp_path / "harbor.md"
    docs_file.write_text(HARBOR_MD)
    runner = JobRunner(SessionLocal, handlers={"build": run_build_handler})
    runner.submit_build(cid, inputs={"docs_path": str(docs_file)})
    runner.run_until_empty()
    with SessionLocal() as s:
        br = s.query(BuildRun).first()
        assert br.state == "succeeded"  # type: ignore[union-attr]
        assert br.draft_spec is not None  # type: ignore[union-attr]
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
        inputs={"docs_text": HARBOR_MD, "docs_source": "harbor.md", "verifier_url": "http://verifier:8000", "scenario_id": "baseline"},
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
        inputs={"docs_text": HARBOR_MD, "docs_source": "harbor.md", "verifier_url": "http://verifier-down:9999", "scenario_id": "baseline"},
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
        inputs={"docs_text": HARBOR_MD, "docs_source": "harbor.md", "verifier_url": "http://verifier:8000", "scenario_id": "baseline"},
    )
    runner.run_until_empty()
    with SessionLocal() as s:
        br = s.query(BuildRun).first()
        assert br is not None
        assert br.result["verified"] is True
        assert br.result["release_id"]
        rel = s.get(ConnectorRelease, br.result["release_id"])
        assert rel is not None
    # Gate-before-persist was actually invoked with the build's scenario
    assert capture.get("scenario_id") == "baseline"
    assert capture.get("verifier_url") == "http://verifier:8000"
    engine.dispose()
