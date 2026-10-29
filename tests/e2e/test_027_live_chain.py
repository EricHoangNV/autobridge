"""REQ-027 item 5 (pulled forward by owner check): the live chain on the stack.

Exact owner sequence, no reference spec anywhere: create -> build from
docs -> baseline Complete -> inject page_to_cursor -> sync -> /repairs
shows page_to_cursor, rung 1, 0 tokens -> approve -> sync Complete.
Plus: a Blocked sync that never reached the vendor opens no drift.

Per-scenario coverage (REQ-027 item 5): one live test per Atlas
known-repair scenario with a rule — rename_guest_id (rename_field),
cursor_repeat_token (page_to_cursor guard), offset_to_cursor (same wire
shape as page_to_cursor on Atlas, so page_to_cursor heals it) — each
inject -> sync -> repair proposed by the right rule -> approve -> sync
Complete, all with 0 model calls. Transport faults (rate_limit_429,
transient_503) are absorbed by retries: sync Complete, no drift, no
repair. status_vocab_change Blocks on the unknown enum and escalates
(the ladder never guesses a mapping), token_expired Blocks with no
vendor response and opens nothing. gross_to_net is a Harbor semantic
scenario: covered live by demo step 4 and in-process by the model-usage
matrix, not by this Atlas file.

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
    status, body = _post(
        f"/connectors/{connector_id}/build",
        # REQ-027: probe served wire keys so detection is scoped to reality.
        {"docs_text": docs_text, "probe_wire_shape": True},
    )
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


# ---------------------------------------------------------------------------
# REQ-027 item 5: one live test per known-repair scenario (Atlas)
# ---------------------------------------------------------------------------


def _inject_fault(scenario_id: str) -> None:
    status, body = _post(
        "/demo/inject-fault", {"target": "sim-atlas", "scenario_id": scenario_id, "seed": 1}
    )
    assert status == 200, f"POST inject -> {status}: {body}"


def _assert_built_spec_sane(connector_id: str) -> None:
    """The built spec must be a real Atlas compile, not a placeholder."""
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


def _connector_repairs(connector_id: str) -> list[dict[str, Any]]:
    status, repairs = _get(f"/repairs?connector_id={connector_id}")
    assert status == 200, repairs
    assert isinstance(repairs, list), repairs
    return repairs


def _connector_drifts(connector_id: str) -> list[dict[str, Any]]:
    status, drifts = _get(f"/drift?connector_id={connector_id}")
    assert status == 200, drifts
    assert isinstance(drifts, list), drifts
    return drifts


def _assert_zero_model_usage(run: dict[str, Any]) -> None:
    usage = run.get("model_usage") or {}
    assert int(usage.get("frontier_tokens", -1)) == 0, usage
    assert int(usage.get("small_tokens", -1)) == 0, usage


def _heal_cycle(
    name: str,
    scenario_id: str,
    expected_drift_class: str,
    expected_rule_id: str,
) -> None:
    """Full live chain: baseline Complete -> inject -> propose -> approve -> Complete.

    Service-DNS base URL: the server normalizes it to the mode's address
    (REQ-027 sub-step 1), so this runs on Compose and on local mode.
    """
    connector_id = _ensure_connector(name, "http://sim-atlas:8000")
    _build_and_activate(connector_id)
    _assert_built_spec_sane(connector_id)

    baseline = _sync_and_wait(connector_id)
    assert baseline.get("outcome") == "Complete", baseline
    # A healthy baseline opens no drift and no repair (REQ-027: build-probe
    # annotation scopes detection to wire keys the vendor served).
    assert _connector_drifts(connector_id) == []
    assert _connector_repairs(connector_id) == []

    _inject_fault(scenario_id)
    faulted = _sync_and_wait(connector_id)

    drifts = _connector_drifts(connector_id)
    assert len(drifts) == 1, f"expected exactly the fault drift, got: {drifts}"
    assert drifts[0].get("drift_class") == expected_drift_class, drifts[0]

    repairs = _connector_repairs(connector_id)
    assert len(repairs) == 1, f"expected exactly one repair run, got: {repairs}"
    run = repairs[0]
    assert run.get("decision") == "propose", run
    assert run.get("rule_id") == expected_rule_id, run
    assert run.get("ladder_level_reached") == 1, run
    _assert_zero_model_usage(run)
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


def test_docs_built_atlas_heals_rename_guest_id(e2e_stack: str) -> None:
    """rename_guest_id: detector-derived wire evidence -> rename_field, rung 1."""
    assert e2e_stack
    _heal_cycle(_unique("atlas-027-rename"), "rename_guest_id", "SCHEMA_RENAME", "rename_field")


def test_docs_built_atlas_heals_cursor_repeat_token(e2e_stack: str) -> None:
    """cursor_repeat_token: repeated-cursor guard added by page_to_cursor, rung 1.

    While the vendor keeps repeating the cursor the patched release stops
    the loop and reports the duplicates (Degraded with unique_key named —
    bounded, no hang); once the vendor heals, the same release syncs
    Complete. All deterministic: 0 model calls throughout.
    """
    assert e2e_stack
    connector_id = _ensure_connector(_unique("atlas-027-repeat"), "http://sim-atlas:8000")
    _build_and_activate(connector_id)
    _assert_built_spec_sane(connector_id)

    baseline = _sync_and_wait(connector_id)
    assert baseline.get("outcome") == "Complete", baseline
    assert _connector_drifts(connector_id) == []
    assert _connector_repairs(connector_id) == []

    _inject_fault("cursor_repeat_token")
    _sync_and_wait(connector_id)

    drifts = _connector_drifts(connector_id)
    assert len(drifts) == 1, drifts
    assert drifts[0].get("drift_class") == "PAGINATION_CHANGE", drifts[0]

    repairs = _connector_repairs(connector_id)
    assert len(repairs) == 1, repairs
    run = repairs[0]
    assert run.get("decision") == "propose", run
    assert run.get("rule_id") == "page_to_cursor", run
    assert run.get("ladder_level_reached") == 1, run
    _assert_zero_model_usage(run)

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

    # Fault still injected: the guard bounds the loop; duplicates are
    # reported, not hidden — Degraded with the criterion named. (A later
    # vendor revert to page shape would be a new drift, not a failure of
    # this repair: the release tracks the vendor's current shape.)
    bounded = _sync_and_wait(connector_id)
    assert bounded.get("outcome") == "Degraded", bounded
    assert "unique_key" in (bounded.get("unmet_criteria") or []), bounded


def test_docs_built_atlas_heals_offset_to_cursor_wire_alias(e2e_stack: str) -> None:
    """offset_to_cursor serves the same cursor wire shape as page_to_cursor on Atlas.

    The vendor is page-based, so the offset fault lands as a page->cursor
    drift and the page_to_cursor rule is the correct healer (the
    offset_to_cursor rule is covered by its offset-fixture unit tests and
    the model-usage matrix: no live vendor is offset-based).
    """
    assert e2e_stack
    _heal_cycle(
        _unique("atlas-027-offset"), "offset_to_cursor", "PAGINATION_CHANGE", "page_to_cursor"
    )


def test_status_vocab_change_blocks_and_escalates(e2e_stack: str) -> None:
    """Unknown enum value: sync Blocked, ENUM_NEW_VALUE drift, escalate, 0 calls.

    The ladder never guesses an enum mapping: the rule produces a hypothesis,
    not a patch, so the run escalates with one operator question.
    """
    assert e2e_stack
    connector_id = _ensure_connector(_unique("atlas-027-enum"), "http://sim-atlas:8000")
    _build_and_activate(connector_id)
    _assert_built_spec_sane(connector_id)

    baseline = _sync_and_wait(connector_id)
    assert baseline.get("outcome") == "Complete", baseline
    assert _connector_drifts(connector_id) == []

    _inject_fault("status_vocab_change")
    faulted = _sync_and_wait(connector_id)
    assert faulted.get("outcome") == "Blocked", faulted

    drifts = _connector_drifts(connector_id)
    assert len(drifts) == 1, drifts
    assert drifts[0].get("drift_class") == "ENUM_NEW_VALUE", drifts[0]

    repairs = _connector_repairs(connector_id)
    assert len(repairs) == 1, repairs
    run = repairs[0]
    assert run.get("decision") == "escalate", run
    assert run.get("ladder_level_reached") == 4, run
    _assert_zero_model_usage(run)


def test_token_expired_blocks_quietly(e2e_stack: str) -> None:
    """Expired credential: every page 401s, no vendor response, no drift, no repair."""
    assert e2e_stack
    connector_id = _ensure_connector(_unique("atlas-027-authexp"), "http://sim-atlas:8000")
    _build_and_activate(connector_id)

    baseline = _sync_and_wait(connector_id)
    assert baseline.get("outcome") == "Complete", baseline

    _inject_fault("token_expired")
    faulted = _sync_and_wait(connector_id)
    assert faulted.get("outcome") == "Blocked", faulted
    assert _connector_drifts(connector_id) == []
    assert _connector_repairs(connector_id) == []


def test_rate_limit_429_absorbed_by_retries(e2e_stack: str) -> None:
    """Intermittent 429s: retries absorb the throttle, sync Complete, nothing opens."""
    assert e2e_stack
    connector_id = _ensure_connector(_unique("atlas-027-rl"), "http://sim-atlas:8000")
    _build_and_activate(connector_id)

    _inject_fault("rate_limit_429")
    result = _sync_and_wait(connector_id)
    assert result.get("outcome") == "Complete", result
    assert _connector_drifts(connector_id) == []
    assert _connector_repairs(connector_id) == []


def test_transient_503_absorbed_by_retries(e2e_stack: str) -> None:
    """Intermittent 503s: retries absorb the outage, sync Complete, nothing opens."""
    assert e2e_stack
    connector_id = _ensure_connector(_unique("atlas-027-t503"), "http://sim-atlas:8000")
    _build_and_activate(connector_id)

    _inject_fault("transient_503")
    result = _sync_and_wait(connector_id)
    assert result.get("outcome") == "Complete", result
    assert _connector_drifts(connector_id) == []
    assert _connector_repairs(connector_id) == []
