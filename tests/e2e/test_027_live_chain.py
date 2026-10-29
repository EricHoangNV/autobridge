"""REQ-027 item 5 (pulled forward by owner check): the live chain on the stack.

Exact owner sequence, no reference spec anywhere: create -> build from
docs -> baseline Complete -> inject page_to_cursor -> sync -> /repairs
shows page_to_cursor, rung 1, 0 tokens -> approve -> sync Complete.
Plus: a Blocked sync that never reached the vendor opens no drift.

Takes the ``e2e_stack`` fixture: skips without Docker, fails in CI when
the stack is required but unreachable (REQ-010).
"""

from __future__ import annotations

import json
import pathlib
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

from tests.e2e.conftest import control_headers, service_url

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ATLAS_DOCS = REPO_ROOT / "simulators" / "docs" / "atlas-openapi.yaml"


def _post(path: str, payload: dict[str, Any]) -> tuple[int, Any]:
    url = service_url("control", path)
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers=control_headers(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode() or "null")
        except Exception:
            return exc.code, {}


def _get(path: str) -> tuple[int, Any]:
    url = service_url("control", path)
    req = urllib.request.Request(url, headers=control_headers(), method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, json.loads(resp.read().decode() or "null")


def _poll_job(job_id: str, tries: int = 60) -> dict[str, Any]:
    last: dict[str, Any] = {}
    for _ in range(tries):
        status, body = _get(f"/jobs/{job_id}")
        assert status == 200, f"GET /jobs/{job_id} -> {status}: {body}"
        assert isinstance(body, dict), body
        last = body
        if body.get("state") in ("succeeded", "failed"):
            return body
        time.sleep(2.0)
    raise AssertionError(f"job {job_id} not terminal after {tries} polls: {last}")


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _ensure_connector(name: str, base_url: str) -> str:
    status, body = _get("/connectors")
    assert status == 200, body
    rows = body if isinstance(body, list) else body.get("connectors", [])
    for conn in rows if isinstance(rows, list) else []:
        if isinstance(conn, dict) and conn.get("name") == name:
            assert conn.get("id"), conn
            return str(conn["id"])
    status, body = _post(
        "/connectors",
        {
            "name": name,
            "vendor_docs_ref": "simulators/docs/atlas-openapi.yaml",
            "base_url": base_url,
            "credential_ref": "ATLAS_API_KEY",
            "target_entities": ["Reservation", "FolioCharge"],
        },
    )
    assert status in (200, 201), f"POST /connectors -> {status}: {body}"
    assert isinstance(body, dict) and body.get("id"), body
    return str(body["id"])


def _build_and_activate(connector_id: str) -> str:
    docs_text = ATLAS_DOCS.read_text(encoding="utf-8")
    status, body = _post(f"/connectors/{connector_id}/build", {"docs_text": docs_text})
    assert status == 200, f"POST build -> {status}: {body}"
    assert isinstance(body, dict) and body.get("job_id"), body
    job = _poll_job(str(body["job_id"]))
    assert job.get("state") == "succeeded", f"build job failed: {job.get('last_error')}"
    status, detail = _get(f"/connectors/{connector_id}")
    assert status == 200, detail
    assert isinstance(detail, dict), detail
    releases = detail.get("releases") or []
    assert releases, f"build produced no releases: {detail}"
    release_id = str(releases[-1]["id"])
    if detail.get("active_release_id") != release_id:
        status, body = _post(
            f"/connectors/{connector_id}/releases/{release_id}/activate", {}
        )
        assert status == 200, f"POST activate -> {status}: {body}"
    return release_id


def _sync_and_wait(connector_id: str) -> dict[str, Any]:
    status, body = _post(f"/connectors/{connector_id}/sync", {})
    assert status == 200, f"POST sync -> {status}: {body}"
    assert isinstance(body, dict) and body.get("job_id"), body
    job = _poll_job(str(body["job_id"]))
    assert job.get("state") == "succeeded", f"sync job failed: {job.get('last_error')}"
    result = job.get("result") or {}
    assert isinstance(result, dict), job
    return result


def test_docs_built_atlas_heals_page_to_cursor(e2e_stack: str) -> None:
    """Owner sequence: docs-built Atlas syncs, heals, resyncs Complete."""
    assert e2e_stack
    name = _unique("atlas-027")
    # Service-DNS form: the server normalizes to the mode's address
    # (REQ-027 sub-step 1); never localhost here so Compose resolves it.
    connector_id = _ensure_connector(name, "http://sim-atlas:8000")
    _build_and_activate(connector_id)

    # The built spec must be a real Atlas compile, not a placeholder:
    # versioned method paths, page pagination, concrete base URL.
    status, detail = _get(f"/connectors/{connector_id}")
    assert status == 200, detail
    assert isinstance(detail, dict), detail
    releases = detail.get("releases") or []
    built = next((r for r in releases if r.get("id") == detail.get("active_release_id")), releases[-1])
    spec = built.get("spec") or {}
    ops = spec.get("operations") or {}
    assert ops, "built release has no operations"
    assert any("/api/v1.3/" in str(op.get("path")) for op in ops.values()), ops
    assert "${" not in str((spec.get("source") or {}).get("base_url")), spec.get("source")

    baseline = _sync_and_wait(connector_id)
    assert baseline.get("outcome") == "Complete", baseline

    status, body = _post(
        "/demo/inject-fault", {"target": "sim-atlas", "scenario_id": "page_to_cursor", "seed": 1}
    )
    assert status == 200, f"POST inject -> {status}: {body}"
    faulted = _sync_and_wait(connector_id)

    status, drifts = _get(f"/drift?connector_id={connector_id}")
    assert status == 200, drifts
    assert isinstance(drifts, list) and drifts, "no drift rows after faulted sync"
    assert drifts[0].get("drift_class") == "PAGINATION_CHANGE", drifts[0]

    status, repairs = _get(f"/repairs?connector_id={connector_id}")
    assert status == 200, repairs
    assert isinstance(repairs, list) and repairs, "no repair rows after faulted sync"
    run = repairs[0]
    assert run.get("decision") == "propose", run
    assert run.get("rule_id") == "page_to_cursor", run
    assert run.get("ladder_level_reached") == 1, run
    usage = run.get("model_usage") or {}
    assert int(usage.get("frontier_tokens", -1)) == 0, usage
    assert int(usage.get("small_tokens", -1)) == 0, usage
    _ = faulted

    status, detail = _get(f"/connectors/{connector_id}")
    assert status == 200, detail
    assert isinstance(detail, dict), detail
    candidate = next(
        (
            r
            for r in (detail.get("releases") or [])
            if isinstance(r, dict) and r.get("provenance_repair_run_id") == run.get("id")
        ),
        None,
    )
    assert candidate is not None, f"no candidate release for repair {run.get('id')}"
    status, body = _post(
        f"/repairs/{run['id']}/approve",
        {"to_release_id": candidate["id"], "connector_id": connector_id},
    )
    assert status == 200, f"POST approve -> {status}: {body}"

    resync = _sync_and_wait(connector_id)
    assert resync.get("outcome") == "Complete", resync


def test_blocked_sync_opens_no_drift(e2e_stack: str) -> None:
    """A sync that never reaches the vendor opens no drift and no repair."""
    assert e2e_stack
    name = _unique("atlas-027-unreachable")
    connector_id = _ensure_connector(name, "http://127.0.0.1:9")
    _build_and_activate(connector_id)

    result = _sync_and_wait(connector_id)
    assert result.get("outcome") == "Blocked", result

    status, drifts = _get(f"/drift?connector_id={connector_id}")
    assert status == 200, drifts
    assert drifts == [], f"blocked sync opened drift: {drifts}"
    status, repairs = _get(f"/repairs?connector_id={connector_id}")
    assert status == 200, repairs
    assert repairs == [], f"blocked sync opened repair: {repairs}"
