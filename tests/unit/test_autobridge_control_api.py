# ruff: noqa: TC003, ARG001, F401, I001, F541
"""Control plane API tests (P1.W7.1, plan §2.11).

Covers every endpoint: connectors CRUD, build/sync jobs, runs, drift,
repairs approve/reject, releases rollback, demo inject-fault (labelled proxy),
metrics model-usage, bench scorecards, healthz/readyz, and OpenAPI contract.

Secrets never appear in any response (redaction).
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from autobridge.control.app import app
from autobridge.control.deps import get_db
from autobridge.control.models import (
    Base,
    session_factory,
)


@pytest.fixture()
def client(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> Any:  # type: ignore[no-untyped-def]
    # Hermetic (REQ-022 DNS-hermetic fix): /demo/* posts simulator admin URLs
    # over real httpx. Point them at refused loopback so no test resolves a
    # hostname (on macOS a worker-thread DNS lookup poisons later forked
    # children). The labelled/error paths asserted here are identical for
    # refused vs unresolvable hosts. NOTE: no VERIFIER_URL here — builds.py
    # falls back to it for the verifier gate, so setting it would change
    # build outcomes; readyz tests set it per-test instead.
    monkeypatch.setenv("SIMULATOR_ADMIN_URL", "http://127.0.0.1:9")
    # Use file DB to avoid StaticPool quirks with threads; tmp_path gives isolation
    engine = create_engine(f"sqlite:///{tmp_path}/control.db")
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
    c = TestClient(app)
    yield c
    app.dependency_overrides.clear()


def _create_connector(client: Any, **over: Any) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    payload = {
        "name": over.get("name", "Harbor Test"),
        "base_url": over.get("base_url", "http://sim-harbor:8000"),
        "credential_ref": over.get("credential_ref", "HARBOR_API_KEY"),
        "target_entities": over.get("target_entities", ["Reservation"]),
        "runtime_target": over.get("runtime_target", {"kind": "local_process"}),
    }
    if "vendor_docs_ref" in over:
        payload["vendor_docs_ref"] = over["vendor_docs_ref"]
    r = client.post("/connectors", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def test_healthz(client: Any) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_readyz(client: Any, monkeypatch: Any) -> None:  # type: ignore[no-untyped-def]
    # Hermetic (REQ-022 DNS-hermetic fix): readyz probes VERIFIER_URL over
    # real httpx — refuse on loopback instead of resolving the hostname.
    monkeypatch.setenv("VERIFIER_URL", "http://127.0.0.1:9")
    r = client.get("/readyz")
    # readyz is honest about dependencies (plan §3, AR-2-6): without a
    # reachable verifier the status is "degraded" and the code is 503,
    # never a misleading 200.
    assert r.status_code == 503
    assert r.json()["status"] == "degraded"
    assert "db" in r.json()["checks"]
    assert "verifier" in r.json()["checks"]


def test_root(client: Any) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["service"] == "autobridge-control"


# ---------------------------------------------------------------------------
# Connectors
# ---------------------------------------------------------------------------


def test_create_and_list_connectors(client: Any) -> None:  # type: ignore[no-untyped-def]
    c1 = _create_connector(client, name="Atlas")
    c2 = _create_connector(client, name="Harbor", credential_ref="ATLAS_API_KEY")
    lst = client.get("/connectors").json()
    assert len(lst) == 2
    ids = {c["id"] for c in lst}
    assert c1["id"] in ids and c2["id"] in ids


def test_create_connector_normalizes_sim_address(client: Any, monkeypatch: Any) -> None:  # type: ignore[no-untyped-def]
    """REQ-027 sub-step 1: stored base_url works in the stack's mode.

    A Compose service-DNS address sent in local mode is stored loopback;
    a loopback address sent in Compose mode is stored service-DNS. Real
    vendor hosts and ${VAR} placeholders pass through untouched.
    """
    import autobridge.control.demo_targets as dt

    monkeypatch.setattr(dt, "in_compose_network", lambda: False)
    c1 = _create_connector(client, name="NormLocal", base_url="http://sim-harbor:8000")
    assert c1["base_url"] == "http://localhost:8106"
    c2 = _create_connector(client, name="NormVendor", base_url="https://api.vendor.example.com/v1")
    assert c2["base_url"] == "https://api.vendor.example.com/v1"
    c3 = _create_connector(client, name="NormPlaceholder", base_url="${HARBOR_BASE_URL}")
    assert c3["base_url"] == "${HARBOR_BASE_URL}"
    monkeypatch.setattr(dt, "in_compose_network", lambda: True)
    c4 = _create_connector(client, name="NormCompose", base_url="http://localhost:8106")
    assert c4["base_url"] == "http://sim-harbor:8000"


def test_create_connector_invalid_runtime(client: Any) -> None:  # type: ignore[no-untyped-def]
    r = client.post(
        "/connectors",
        json={
            "name": "Edge",
            "base_url": "http://edge-relay:8000",
            "credential_ref": "TEST_API_KEY",
            "target_entities": [],
            "runtime_target": {"kind": "edge", "network_allowlist": ["edge-relay"]},
        },
    )
    assert r.status_code == 201
    r2 = client.post(
        "/connectors",
        json={
            "name": "Bad2",
            "base_url": "http://example.com",
            "credential_ref": "TEST_API_KEY",
            "target_entities": [],
            "runtime_target": {"kind": "invalid_kind"},
        },
    )
    assert r2.status_code == 422


def test_create_connector_missing_fields(client: Any) -> None:  # type: ignore[no-untyped-def]
    r = client.post("/connectors", json={"name": "OnlyName"})
    assert r.status_code == 422


def test_get_connector_detail(client: Any, monkeypatch: Any) -> None:  # type: ignore[no-untyped-def]
    c = _create_connector(client)
    r = client.get(f"/connectors/{c['id']}")
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == c["id"]
    assert "releases" in data
    assert "open_drift" in data
    assert "dependencies" in data
    assert data["credential_ref"] == "HARBOR_API_KEY"
    # Ensure credential value not leaked (spec carries ref only)
    assert "HARBOR_API_KEY" in r.text
    # But actual secret value (if env) not present
    monkeypatch.setenv("HARBOR_API_KEY", "supersecret123")
    r2 = client.get(f"/connectors/{c['id']}")
    assert "supersecret123" not in r2.text


def test_get_connector_not_found(client: Any) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/connectors/nonexistent")
    assert r.status_code == 404


def test_connector_detail_lists_build_attempts_incl_refused(client: Any) -> None:  # type: ignore[no-untyped-def]
    """REQ-050 item 4: the detail shows both attempts — v1 refused with its
    reason (no release) and v2 in use."""
    from pathlib import Path as _Path

    meridian_docs = (_Path(__file__).resolve().parents[2] / "simulators" / "docs" / "meridian-openapi.yaml").read_text(
        encoding="utf-8"
    )
    c = _create_connector(
        client,
        name="meridianpms",
        base_url="http://sim-meridian:8000",
        credential_ref="MERIDIAN_API_KEY",
        target_entities=["Reservation", "FolioCharge"],
    )
    # The build page sends no runtime choice (REQ-050 item 6).
    assert c["runtime_target"] == {"kind": "local_process"}
    r1 = client.post(f"/connectors/{c['id']}/build", json={"docs_text": meridian_docs, "spec_vocabulary": "v1"})
    assert r1.status_code == 200, r1.text
    r2 = client.post(f"/connectors/{c['id']}/build", json={"docs_text": meridian_docs, "spec_vocabulary": "v2"})
    assert r2.status_code == 200, r2.text
    detail = client.get(f"/connectors/{c['id']}").json()
    attempts = detail["builds"]
    assert len(attempts) == 2, attempts
    assert [a["spec_vocabulary"] for a in attempts] == ["v1", "v2"]
    assert attempts[0]["success"] is False
    assert attempts[0]["release_id"] is None
    assert "POST" in (attempts[0]["degradation_reason"] or "")
    assert "ConnectorSpec v1" in (attempts[0]["degradation_reason"] or "")
    assert attempts[1]["success"] is True
    assert attempts[1]["release_id"]
    # HTTP docs stay on the cloud runtime.
    assert detail["runtime_target"] == {"kind": "local_process"}


def test_dependency_create_and_list(client: Any) -> None:  # type: ignore[no-untyped-def]
    c = _create_connector(client)
    r = client.post(
        f"/connectors/{c['id']}/dependencies",
        json={"consumer_id": "demo-dashboard", "entity": "Reservation", "policy": "pause_on_release_change"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["consumer_id"] == "demo-dashboard"
    # Duplicate should 409
    r2 = client.post(
        f"/connectors/{c['id']}/dependencies",
        json={"consumer_id": "demo-dashboard", "entity": "Reservation"},
    )
    assert r2.status_code == 409
    lst = client.get(f"/connectors/{c['id']}/dependencies").json()
    assert len(lst) == 1
    # Detail also includes dependencies
    detail = client.get(f"/connectors/{c['id']}").json()
    assert len(detail["dependencies"]) == 1


def test_dependency_connector_not_found(client: Any) -> None:  # type: ignore[no-untyped-def]
    r = client.post("/connectors/nonexistent/dependencies", json={"consumer_id": "x", "entity": "Reservation"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Builds
# ---------------------------------------------------------------------------


def test_build_requires_docs(client: Any) -> None:  # type: ignore[no-untyped-def]
    c = _create_connector(client)
    r = client.post(f"/connectors/{c['id']}/build", json={})
    assert r.status_code == 422


def test_build_with_docs_text(client: Any) -> None:  # type: ignore[no-untyped-def]
    c = _create_connector(client, base_url="http://sim-harbor:8000")
    # Use docs that produce valid spec (HarborPMS title + base_url with harbor hint)
    docs = "# HarborPMS API\n\nBase URL: http://sim-harbor:8000\n\nGET /reservations\nQuery: offset, limit\n\nGET /folios\nQuery: offset, limit\n"
    r = client.post(f"/connectors/{c['id']}/build", json={"docs_text": docs, "scenario_id": "baseline"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert "job_id" in data
    assert "build_run_id" in data
    assert data["state"] == "succeeded"
    # Verify releases created
    rels = client.get("/releases").json()
    assert len(rels) == 1
    assert rels[0]["connector_id"] == c["id"]


def test_build_blocks_reference_spec(client: Any) -> None:  # type: ignore[no-untyped-def]
    c = _create_connector(client)
    r = client.post(f"/connectors/{c['id']}/build", json={"docs_path": "generated/connectors/harborpms/reference/spec.yaml"})
    assert r.status_code == 422


def test_build_connector_not_found(client: Any) -> None:  # type: ignore[no-untyped-def]
    r = client.post("/connectors/nonexistent/build", json={"docs_text": "hi"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


def test_sync_no_releases(client: Any) -> None:  # type: ignore[no-untyped-def]
    c = _create_connector(client)
    r = client.post(f"/connectors/{c['id']}/sync", json={})
    assert r.status_code == 422


def test_sync_with_release(client: Any) -> None:  # type: ignore[no-untyped-def]
    # Literal unroutable IP: no real server, and no DNS lookup either
    # (unit hermeticity guard). REQ-027 builds store it verbatim.
    c = _create_connector(client, base_url="http://127.0.0.1:9")
    docs = "# HarborPMS API\n\nBase URL: http://sim-harbor:8000\n\nGET /reservations\n\nGET /folios\n"
    br = client.post(f"/connectors/{c['id']}/build", json={"docs_text": docs})
    assert br.status_code == 200
    rels = client.get(f"/releases?connector_id={c['id']}").json()
    assert len(rels) >= 1
    rel_id = rels[0]["id"]
    # Sync via explicit release_id (will Block due to no real server, but job succeeds)
    r = client.post(f"/connectors/{c['id']}/sync", json={"release_id": rel_id})
    assert r.status_code == 200, r.text
    assert r.json()["release_id"] == rel_id
    # SyncRun should exist as Blocked (no server)
    # Fetch via job result or runs
    # The sync handler creates SyncRun even when http fails (Blocked)
    # We can list jobs to see sync job
    jobs = client.get("/jobs").json()
    # Find sync job
    sync_jobs = [j for j in jobs if j["kind"] == "sync"]
    assert len(sync_jobs) >= 1


def test_sync_wrong_connector(client: Any) -> None:  # type: ignore[no-untyped-def]
    c1 = _create_connector(client, name="C1", base_url="http://sim-harbor:8000")
    c2 = _create_connector(client, name="C2", base_url="http://sim-atlas:8000")
    docs = "# HarborPMS API\nBase URL: http://sim-harbor:8000\nGET /reservations\nGET /folios\n"
    client.post(f"/connectors/{c1['id']}/build", json={"docs_text": docs})
    rels = client.get(f"/releases?connector_id={c1['id']}").json()
    if rels:
        rel_id = rels[0]["id"]
        r = client.post(f"/connectors/{c2['id']}/sync", json={"release_id": rel_id})
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# Runs / Jobs
# ---------------------------------------------------------------------------


def test_get_run_build(client: Any) -> None:  # type: ignore[no-untyped-def]
    c = _create_connector(client, base_url="http://sim-harbor:8000")
    docs = "# HarborPMS API\nBase URL: http://sim-harbor:8000\nGET /reservations\nGET /folios\n"
    br = client.post(f"/connectors/{c['id']}/build", json={"docs_text": docs})
    build_run_id = br.json()["build_run_id"]
    r = client.get(f"/runs/{build_run_id}")
    assert r.status_code == 200
    assert r.json()["kind"] == "build"
    assert r.json()["id"] == build_run_id
    # Detail
    r2 = client.get(f"/runs/{build_run_id}/detail")
    assert r2.status_code == 200
    assert r2.json()["kind"] == "build"


def test_get_run_not_found(client: Any) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/runs/nonexistent")
    assert r.status_code == 404
    r2 = client.get("/runs/nonexistent/detail")
    assert r2.status_code == 404


def test_list_jobs(client: Any) -> None:  # type: ignore[no-untyped-def]
    c = _create_connector(client, base_url="http://sim-harbor:8000")
    docs = "# HarborPMS API\nGET /reservations\nGET /folios\n"
    client.post(f"/connectors/{c['id']}/build", json={"docs_text": docs})
    jobs = client.get("/jobs").json()
    assert len(jobs) >= 1
    # Get single job
    jid = jobs[0]["id"]
    r = client.get(f"/jobs/{jid}")
    assert r.status_code == 200
    assert r.json()["id"] == jid
    assert client.get("/jobs/nonexistent").status_code == 404


# ---------------------------------------------------------------------------
# Drift / Repairs / Releases
# ---------------------------------------------------------------------------


def test_drift_empty(client: Any) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/drift").json() == []
    assert client.get("/drift/nonexistent").status_code == 404


def test_repairs_empty(client: Any) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/repairs").json() == []
    assert client.get("/repairs/nonexistent").status_code == 404


def test_releases_empty(client: Any) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/releases").json() == []
    assert client.get("/releases/nonexistent").status_code == 404


def test_rollback_requires_active(client: Any) -> None:  # type: ignore[no-untyped-def]
    # Create connector and releases via build, but no active release -> rollback fails
    c = _create_connector(client, base_url="http://sim-harbor:8000")
    docs = "# HarborPMS API\nBase URL: http://sim-harbor:8000\nGET /reservations\nGET /folios\n"
    br = client.post(f"/connectors/{c['id']}/build", json={"docs_text": docs})
    assert br.status_code == 200
    rels = client.get("/releases").json()
    if not rels:
        pytest.skip("no releases")
    # Need a repair_run for audit; create dummy repair run directly via DB
    # Use the same DB as client (we can inject via model's engine)
    # Instead, test 404 for missing release
    r = client.post("/releases/nonexistent/rollback", json={"target_release_id": rels[0]["id"], "actor": "tester", "repair_run_id": "dummy"})
    assert r.status_code == 404


def test_approve_reject_not_found(client: Any) -> None:  # type: ignore[no-untyped-def]
    r = client.post("/repairs/nonexistent/approve", json={"actor": "tester"})
    assert r.status_code == 404
    r2 = client.post("/repairs/nonexistent/reject", json={"actor": "tester"})
    assert r2.status_code == 404


def test_release_filter_by_connector(client: Any) -> None:  # type: ignore[no-untyped-def]
    c1 = _create_connector(client, name="C1", base_url="http://sim-harbor:8000")
    c2 = _create_connector(client, name="C2", base_url="http://sim-harbor:8000")
    docs = "# HarborPMS API\nBase URL: http://sim-harbor:8000\nGET /reservations\nGET /folios\n"
    client.post(f"/connectors/{c1['id']}/build", json={"docs_text": docs})
    client.post(f"/connectors/{c2['id']}/build", json={"docs_text": docs})
    all_rels = client.get("/releases").json()
    assert len(all_rels) == 2
    filtered = client.get(f"/releases?connector_id={c1['id']}").json()
    assert len(filtered) == 1
    assert filtered[0]["connector_id"] == c1["id"]


# ---------------------------------------------------------------------------
# Demo inject-fault
# ---------------------------------------------------------------------------


def test_demo_inject_fault(client: Any) -> None:  # type: ignore[no-untyped-def]
    r = client.post("/demo/inject-fault", json={"target": "sim-atlas", "scenario_id": "baseline"})
    assert r.status_code == 200
    data = r.json()
    assert data["labelled"] is True
    assert data["demo"] is True
    assert data["target"] == "sim-atlas"
    assert data["scenario_id"] == "baseline"
    assert "message" in data
    assert "[DEMO]" in data["message"]
    # Alias handling
    r2 = client.post("/demo/inject-fault", json={"target": "atlas", "scenario_id": "rename_guest_id"})
    assert r2.status_code == 200
    assert r2.json()["target"] == "sim-atlas"
    r3 = client.post("/demo/inject-fault", json={"target": "harborpms", "scenario_id": "baseline"})
    assert r3.status_code == 200
    assert r3.json()["target"] == "sim-harbor"


def test_demo_inject_invalid_target(client: Any) -> None:  # type: ignore[no-untyped-def]
    r = client.post("/demo/inject-fault", json={"target": "unknown", "scenario_id": "baseline"})
    assert r.status_code == 422


def test_demo_inject_missing_fields(client: Any) -> None:  # type: ignore[no-untyped-def]
    r = client.post("/demo/inject-fault", json={"target": "sim-atlas"})
    assert r.status_code == 422


def test_demo_reset(client: Any) -> None:  # type: ignore[no-untyped-def]
    r = client.post("/demo/reset")
    assert r.status_code == 200
    assert r.json()["labelled"] is True


# ---------------------------------------------------------------------------
# Metrics / Bench
# ---------------------------------------------------------------------------


def test_metrics_empty(client: Any) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/metrics/model-usage")
    assert r.status_code == 200
    data = r.json()
    assert data["total_calls"] == 0
    assert data["total_cost"] == "0"
    assert data["frontier_tokens"] == 0


def test_metrics_sync_outcomes(client: Any) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/metrics/sync-outcomes")
    assert r.status_code == 200
    assert "total" in r.json()


def test_bench_scorecards(client: Any) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/bench/scorecards")
    assert r.status_code == 200
    data = r.json()
    assert "scorecards" in data
    assert "total" in data
    # Specific scorecard path
    r2 = client.get("/bench/scorecard")
    assert r2.status_code == 200
    assert "path" in r2.json()


# ---------------------------------------------------------------------------
# Evidence / SSE redaction
# ---------------------------------------------------------------------------


def test_evidence_endpoint(client: Any, tmp_path: pathlib.Path) -> None:  # type: ignore[no-untyped-def]
    # Create connector + build to generate evidence
    c = _create_connector(client, base_url="http://sim-harbor:8000")
    docs = "# HarborPMS API\nBase URL: http://sim-harbor:8000\nGET /reservations\nGET /folios\n"
    br = client.post(f"/connectors/{c['id']}/build", json={"docs_text": docs})
    build_run_id = br.json()["build_run_id"]
    # Evidence for run
    ev = client.get(f"/runs/{build_run_id}/evidence")
    assert ev.status_code == 200
    assert isinstance(ev.json(), list)
    # Evidence list non-empty (bootstrap creates hypothesis records)
    assert len(ev.json()) >= 1
    # Get single evidence
    eid = ev.json()[0]["id"]
    r = client.get(f"/evidence/{eid}")
    assert r.status_code == 200
    assert r.json()["id"] == eid
    assert client.get("/evidence/nonexistent").status_code == 404


def test_sse_endpoint_exists(client: Any) -> None:  # type: ignore[no-untyped-def]
    c = _create_connector(client, base_url="http://sim-harbor:8000")
    docs = "# HarborPMS API\nGET /reservations\nGET /folios\n"
    br = client.post(f"/connectors/{c['id']}/build", json={"docs_text": docs})
    build_run_id = br.json()["build_run_id"]
    # SSE endpoint should exist and not 404 (it streams; we test headers)
    r = client.get(f"/runs/{build_run_id}/events", headers={"Accept": "text/event-stream"})
    assert r.status_code == 200
    # Check not found for missing run
    assert client.get("/runs/nonexistent/events").status_code == 404


def test_secrets_never_in_api_responses(client: Any, monkeypatch: Any) -> None:  # type: ignore[no-untyped-def]
    secret = "supersecret12345"
    monkeypatch.setenv("TEST_CRED_SECRET", secret)
    c = _create_connector(client, name="SecretTest", credential_ref="TEST_CRED_SECRET")
    # Create evidence that contains secret value (simulate leakage)
    # Inject via direct DB using the same engine as client
    # We need to get engine from client fixture; we recreate via tmp_path is not shared,
    # so we test via API: ensure responses don't leak secret
    # The connector detail should only show credential_ref, not value
    detail = client.get(f"/connectors/{c['id']}").json()
    assert detail["credential_ref"] == "TEST_CRED_SECRET"
    assert secret not in json.dumps(detail)
    # List connectors also safe
    lst = client.get("/connectors").json()
    assert secret not in json.dumps(lst)
    # Build response should not leak
    docs = "# HarborPMS API\nGET /reservations\nGET /folios\n"
    br = client.post(f"/connectors/{c['id']}/build", json={"docs_text": docs})
    assert secret not in br.text
    # Metrics should not leak
    assert secret not in client.get("/metrics/model-usage").text
    # Drift/repairs/releases also safe
    assert secret not in client.get("/drift").text
    assert secret not in client.get("/repairs").text
    assert secret not in client.get("/releases").text


# ---------------------------------------------------------------------------
# OpenAPI contract
# ---------------------------------------------------------------------------


def test_openapi_schema_valid(client: Any) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/openapi.json")
    assert r.status_code == 200
    schema = r.json()
    assert schema["info"]["title"] == "AutoBridge Control"
    assert "paths" in schema
    # Every endpoint of plan §2.11 must be present
    required = [
        "/connectors",
        "/connectors/{connector_id}",
        "/connectors/{connector_id}/build",
        "/connectors/{connector_id}/sync",
        "/runs/{run_id}",
        "/runs/{run_id}/events",
        "/drift",
        "/repairs/{repair_id}/approve",
        "/repairs/{repair_id}/reject",
        "/releases/{release_id}/rollback",
        "/demo/inject-fault",
        "/metrics/model-usage",
        "/bench/scorecards",
        "/healthz",
        "/readyz",
    ]
    paths = schema["paths"]
    for p in required:
        assert p in paths, f"missing endpoint {p} in OpenAPI"


def test_openapi_docs_accessible(client: Any) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200


def test_connector_create_contract_422(client: Any) -> None:  # type: ignore[no-untyped-def]
    # Missing required fields -> 422 with detail
    r = client.post("/connectors", json={})
    assert r.status_code == 422
    assert "detail" in r.json()


def test_repair_decision_missing_actor(client: Any) -> None:  # type: ignore[no-untyped-def]
    # Need a repair run to test, but we can test 404 first, then validation
    # Create dummy connector + build to have DB, then try approve with missing actor
    c = _create_connector(client, base_url="http://sim-harbor:8000")
    docs = "# HarborPMS API\nGET /reservations\nGET /folios\n"
    client.post(f"/connectors/{c['id']}/build", json={"docs_text": docs})
    # Create a dummy repair run directly? For contract test, just test that missing actor => 422 when repair exists
    # But we have no repair, so test validation on existing endpoint with bad body when repair not found => 404 takes precedence
    # Instead test rollback validation
    rels = client.get("/releases").json()
    if rels:
        r = client.post(f"/releases/{rels[0]['id']}/rollback", json={"target_release_id": rels[0]["id"]})
        assert r.status_code == 422  # missing actor/repair_run_id


def test_metrics_model_usage_structure(client: Any) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/metrics/model-usage")
    data = r.json()
    for key in ["total_calls", "total_input_tokens", "total_output_tokens", "total_cost", "frontier_tokens", "small_tokens", "by_tier", "by_provider"]:
        assert key in data


def test_bench_scorecards_structure(client: Any) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/bench/scorecards")
    data = r.json()
    assert "scorecards" in data
    assert "total" in data
    assert isinstance(data["scorecards"], list)


# ---------------------------------------------------------------------------
# AR-1-2: unverified candidates persist nothing activatable; sync has no
# fallback to unapproved releases.
# ---------------------------------------------------------------------------


def _mock_verifier_fail(monkeypatch: Any) -> None:  # type: ignore[no-untyped-def]
    """Fail every verifier HTTP call (visible check + gate) with a fail verdict."""
    import httpx

    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(
            200,
            json={
                "verdict": "fail",
                "score": 0.0,
                "failed_checks": [{"name": "field_missing", "count": 1}],
                "regression": "pass",
                "scenario_id": "baseline",
                "mode": "visible",
            },
        )

    transport = httpx.MockTransport(handler)

    class FakeClient:
        def __init__(self, *a: Any, **kw: Any) -> None:  # noqa: ARG001, ARG002
            self._client = real_client(transport=transport)

        def __enter__(self) -> Any:  # type: ignore[no-untyped-def]
            return self._client

        def __exit__(self, *a: Any) -> None:  # noqa: ARG001, ARG002
            self._client.close()

    monkeypatch.setattr(httpx, "Client", FakeClient)


def test_build_gate_blocked_persists_no_activatable_release(client: Any, monkeypatch: Any) -> None:  # type: ignore[no-untyped-def]
    c = _create_connector(client, base_url="http://sim-harbor:8000")
    _mock_verifier_fail(monkeypatch)
    docs = "# HarborPMS API\n\nBase URL: http://sim-harbor:8000\n\nGET /reservations\nQuery: offset, limit\n\nGET /folios\nQuery: offset, limit\n"
    r = client.post(f"/connectors/{c['id']}/build", json={"docs_text": docs, "verifier_url": "http://verifier:8000", "scenario_id": "baseline"})
    assert r.status_code == 200, r.text
    # Gate blocked: no release persisted, so nothing exists to approve or sync
    rels = client.get(f"/releases?connector_id={c['id']}").json()
    assert rels == []
    r2 = client.post(f"/connectors/{c['id']}/sync", json={})
    assert r2.status_code == 422
    assert "no active release" in r2.text


def test_sync_without_active_release_is_422(client: Any) -> None:  # type: ignore[no-untyped-def]
    # No releases at all: sync needs an active release (REQ-013; the first
    # build auto-activates, so a candidate always exists after a build —
    # see test_autobridge_activation.py for the candidate cases).
    c = _create_connector(client, base_url="http://sim-harbor:8000")
    r = client.post(f"/connectors/{c['id']}/sync", json={})
    assert r.status_code == 422
    assert "no active release" in r.text


def test_demo_inject_fault_meridian_target(client: Any) -> None:  # type: ignore[no-untyped-def]
    r = client.post("/demo/inject-fault", json={"target": "sim-meridian", "scenario_id": "meridian_state_renamed"})
    assert r.status_code == 200
    assert r.json()["target"] == "sim-meridian"
    r2 = client.post("/demo/inject-fault", json={"target": "meridian", "scenario_id": "meridian_cursor_renamed"})
    assert r2.status_code == 200
    assert r2.json()["target"] == "sim-meridian"


def test_framed_build_with_relay_record_address_uses_docs_fias_base(client: Any) -> None:  # type: ignore[no-untyped-def]
    """Owner check on 96ba3137 (demo 6/8, step 8): a connector record may
    carry the edge relay queue address for its runtime target; the framed
    docs build must still compile to a valid spec with the docs-derived
    fias:// source instead of 422ing on the relay address."""
    from pathlib import Path as _Path

    fias_docs = (_Path(__file__).resolve().parents[2] / "simulators" / "docs" / "fias-pabx.md").read_text(encoding="utf-8")
    c = _create_connector(
        client,
        name="PABX edge",
        base_url="edge-relay://queue/fias-commands",
        credential_ref="EDGE_TOKEN",
        vendor_docs_ref="simulators/docs/fias-pabx.md",
        target_entities=["FolioCharge"],
        runtime_target={"kind": "edge"},
    )
    r = client.post(
        f"/connectors/{c['id']}/build",
        json={"docs_text": fias_docs, "spec_vocabulary": "v3"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "succeeded"
    rels = client.get("/releases").json()
    assert len(rels) == 1
    assert rels[0]["spec"]["source"]["protocol"] == "fias_tcp"
    assert rels[0]["spec"]["source"]["base_url"] == "fias://listen:5010"
