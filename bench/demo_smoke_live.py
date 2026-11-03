"""Keyed live-product-path smoke (REQ-095 item 2).

``make demo-smoke-live``: the only automated run of the live-key product
path — control API → job queue → background drain → ladder — the path
that dropped every deferred repair on Decimal evidence with no gate
noticing (unit/e2e/smoke run keyless, so repairs run inline with no job
parcel; the beacon and ablations call ``execute_sync`` +
``RepairController`` in-process, never the control service or job queue).

Optional by design: without ``FRONTIER_API_KEY`` it prints one skip line
and exits 0 — a missing key never fails a test or a gate. With the key
it runs one scene (Atlas ``page_to_cursor``, rule-level, a handful of
model calls at most): seed, inject the fault, POST the sync exactly as
the UI does, wait for the background drain, assert a repair row with a
rule-level proposal exists, approve it, and assert the resync reaches
a release. Documented as the owner's pre-recording check
(``docs/demo.md`` "Recording from the UI").

Secrets (DIRECTIVES §6): only ``credential_ref`` env-key names travel
here; no credential value is printed, logged or stored.
"""

from __future__ import annotations

import argparse
import os
from typing import Any

from bench.demo import (
    DEFAULT_CONTROL_URL,
    DEFAULT_SEED,
    _connector_id_from_calls,
    _control_call,
    _control_get_raw,
    _live_approve_repair,
    _live_candidate_for_repair,
    _live_ensure_connector,
    _live_latest_drift,
    _poll_live_repair_for_drift,
    _step_live_ensure_active_release,
    _step_live_sync_terminal,
    env_default,
)

#: Printed when the key is absent; the run passes without doing anything.
SKIP_LINE = (
    "demo-smoke-live: SKIP — FRONTIER_API_KEY is not set "
    "(keyed check; nothing ran)"
)

SCENARIO = "page_to_cursor"
TARGET = "sim-atlas"


def _fail(reason: str) -> tuple[dict[str, Any], int]:
    return {"status": "failed", "error": reason}, 1


def run_smoke(control_url: str, seed: int) -> tuple[dict[str, Any], int]:
    """One keyed scene; returns (report, exit code). Never raises."""
    calls: list[dict[str, Any]] = []
    calls.append(_control_call(control_url, "POST", "/demo/reset", {}))

    ensure_calls = _live_ensure_connector(
        control_url,
        {
            "name": "atlaspms",
            "vendor_docs_ref": "simulators/docs/atlas-openapi.yaml",
            "base_url": "http://sim-atlas:8000",
            "credential_ref": "ATLAS_API_KEY",
            "target_entities": ["Reservation", "FolioCharge"],
        },
    )
    calls.extend(ensure_calls)
    connector_id = _connector_id_from_calls(ensure_calls, "atlaspms")
    if connector_id is None:
        return _fail("no connector id from ensure calls")
    _step_live_ensure_active_release(
        control_url,
        connector_id,
        "simulators/docs/atlas-openapi.yaml",
        calls,
        spec_vocabulary="v1",
    )

    sync_result = _step_live_sync_terminal(control_url, connector_id, calls)
    if sync_result.get("outcome") != "Complete":
        return _fail(
            "baseline sync not Complete: "
            f"{sync_result.get('outcome')}: {sync_result.get('unmet_criteria')}"
        )

    calls.append(
        _control_call(
            control_url,
            "POST",
            "/demo/inject-fault",
            {"target": TARGET, "scenario_id": SCENARIO, "seed": seed},
        )
    )
    # Exactly as the UI does: POST the sync; with a live key the climb
    # runs in the background drain and the request returns at once.
    _step_live_sync_terminal(control_url, connector_id, calls)
    drift_row = _live_latest_drift(control_url, connector_id, calls)
    if drift_row.get("drift_class") != "PAGINATION_CHANGE":
        return _fail(f"no PAGINATION_CHANGE drift row: {drift_row}")
    if not drift_row.get("id"):
        return _fail("drift row has no id")
    repair_row = _poll_live_repair_for_drift(
        control_url, connector_id, str(drift_row["id"]), calls
    )
    if repair_row.get("decision") != "propose":
        return _fail(f"no rule-level proposal for the drift: {repair_row}")
    if repair_row.get("rule_id") != "page_to_cursor":
        return _fail(f"proposal is not the page_to_cursor rule: {repair_row}")
    usage = repair_row.get("model_usage")
    usage = usage if isinstance(usage, dict) else {}
    model_calls = int(usage.get("frontier_tokens", 0) or 0) + int(
        usage.get("small_tokens", 0) or 0
    )

    repair_run_id = str(repair_row.get("id"))
    candidate_id = _live_candidate_for_repair(
        control_url, connector_id, repair_run_id, calls
    )
    if not candidate_id:
        return _fail(f"run {repair_run_id}: no candidate release found")
    _live_approve_repair(control_url, connector_id, repair_run_id, candidate_id, calls)
    # Approve lands: the connector's active release is the candidate and
    # the verifying sync is Complete.
    detail = _control_get_raw(control_url, f"/connectors/{connector_id}")
    active_id = detail.get("active_release_id") if isinstance(detail, dict) else None
    if active_id != candidate_id:
        return _fail(
            f"approve did not activate the candidate: active={active_id} "
            f"candidate={candidate_id}"
        )
    resync = _step_live_sync_terminal(control_url, connector_id, calls)
    if resync.get("outcome") != "Complete":
        return _fail(
            "verifying sync not Complete after approve: "
            f"{resync.get('outcome')}: {resync.get('unmet_criteria')}"
        )
    return (
        {
            "status": "ok",
            "connector_id": connector_id,
            "drift_id": drift_row.get("id"),
            "repair_run_id": repair_run_id,
            "rule_id": repair_row.get("rule_id"),
            "model_tokens": model_calls,
            "control_calls": len(calls),
        },
        0,
    )


def main(argv: list[str] | None = None) -> int:
    """Entry point: skip without the key, else run the keyed scene."""
    parser = argparse.ArgumentParser(
        description="Keyed live-product-path smoke (REQ-095 item 2)"
    )
    parser.add_argument(
        "--control-url", default=env_default("CONTROL_URL", DEFAULT_CONTROL_URL)
    )
    parser.add_argument("--seed", type=int, default=int(env_default("DEMO_SEED", str(DEFAULT_SEED))))
    args = parser.parse_args(argv)

    if not (os.environ.get("FRONTIER_API_KEY") or "").strip():
        print(SKIP_LINE)
        return 0
    report, code = run_smoke(args.control_url, args.seed)
    if code == 0:
        print(
            "demo-smoke-live: PASS — page_to_cursor proposed at rule level, "
            f"approved to a release, resync Complete "
            f"(repair {report.get('repair_run_id')}, "
            f"{report.get('model_tokens')} model tokens, "
            f"{report.get('control_calls')} control calls)"
        )
    else:
        print(f"demo-smoke-live: FAIL — {report.get('error')}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
