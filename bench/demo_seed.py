"""`make demo-seed` driver (REQ-031 item 1, REQ-049 item 3).

From `make demo-reset`, creates the recording's connectors **through the
control API only** (the calls the UI makes), each baseline-synced
``Complete``:

- atlaspms — docs-built release, pinned vocabulary v1;
- meridianpms — the refused v1 build recorded with its reason, v2 release
  in use;
- PABX edge — the Grand Plaza PABX edge connector, docs-built v3 release,
  link up, postings flowing.

Harbor is NOT seeded (REQ-049): deck 4 builds it from its wiki in the UI
(or `make demo` step 4 builds it from docs itself), so the totals check
it carries comes from the docs, not from a reference release.

End state: three connectors ``working``, 0 model tokens, nothing waiting.
Clean names, real addresses, no leftover or example.com rows. Fails loud
(exit non-zero naming the unmet step); the transcript records every
control call, redacted like the demo's.

Secrets (DIRECTIVES §6): only ``credential_ref`` env-key names travel here;
no credential value is printed, logged or stored.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from bench.demo import (
    DEFAULT_CONTROL_URL,
    DEFAULT_SEED,
    REPO_ROOT,
    _assert_live,
    _call_ok,
    _check_connector_address,
    _connector_id_from_calls,
    _control_call,
    _control_get_raw,
    _live_build_and_poll,
    _live_ensure_connector,
    _live_knowledge,
    env_default,
)

#: The recording's connectors, in seed order. Names match the demo steps'
#: ensure-by-name payloads so `make demo` after `make demo-seed` reuses
#: them instead of leaving duplicates. Base URLs are the canonical
#: service-DNS form — valid input in both modes; the server normalizes them
#: to the address that works where the engine runs (REQ-027 sub-step 1).
SEED_CONNECTORS: tuple[dict[str, Any], ...] = (
    {
        "name": "atlaspms",
        "connector": {
            "name": "atlaspms",
            "vendor_docs_ref": "simulators/docs/atlas-openapi.yaml",
            "base_url": "http://sim-atlas:8000",
            "credential_ref": "ATLAS_API_KEY",
            "target_entities": ["Reservation", "FolioCharge"],
        },
        "docs_file": "simulators/docs/atlas-openapi.yaml",
        "spec_vocabulary": "v1",
    },
    {
        "name": "meridianpms",
        "connector": {
            "name": "meridianpms",
            "vendor_docs_ref": "simulators/docs/meridian-openapi.yaml",
            "base_url": "http://sim-meridian:8000",
            "credential_ref": "MERIDIAN_API_KEY",
            "target_entities": ["Reservation", "FolioCharge"],
        },
        "docs_file": "simulators/docs/meridian-openapi.yaml",
        "spec_vocabulary": "v2",
        "refused_vocabulary": "v1",
    },
    {
        "name": "PABX edge",
        "connector": {
            "name": "PABX edge",
            "vendor_docs_ref": "simulators/docs/fias-pabx.md",
            "base_url": "edge-relay://queue/fias-commands",
            "credential_ref": "EDGE_TOKEN",
            "target_entities": ["FolioCharge"],
            "runtime_target": {"kind": "edge"},
        },
        "docs_file": "simulators/docs/fias-pabx.md",
        "spec_vocabulary": "v3",
    },
)

SEED_NAMES: tuple[str, ...] = tuple(s["name"] for s in SEED_CONNECTORS)


def _sync_terminal(base_url: str, connector_id: str, calls: list[dict[str, Any]]) -> dict[str, Any]:
    """POST a sync and poll the job; returns the job result (REQ-031: baseline Complete)."""
    import time as _time  # noqa: PLC0415

    sync_rec = _control_call(base_url, "POST", f"/connectors/{connector_id}/sync", {})
    calls.append(sync_rec)
    sync_body = sync_rec.get("body") if isinstance(sync_rec.get("body"), dict) else {}
    job_id = sync_body.get("job_id") if isinstance(sync_body, dict) else None
    if not job_id:
        return {}
    for _ in range(30):
        rec = _control_call(base_url, "GET", f"/jobs/{job_id}")
        calls.append(rec)
        body = rec.get("body")
        state = body.get("state") if isinstance(body, dict) else None
        if state in ("succeeded", "failed"):
            result = body.get("result") if isinstance(body, dict) else None
            return dict(result) if isinstance(result, dict) else {}
        _time.sleep(2.0)
    calls.append(
        {
            "method": "ASSERT",
            "path": f"/jobs/{job_id} terminal",
            "status": "assertion-failed",
            "error": f"sync job {job_id} not terminal after 30 polls",
        }
    )
    return {}


def seed_connector(base_url: str, spec: dict[str, Any], calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Ensure, build, activate and baseline-sync one seed connector; returns its record."""
    name = str(spec["name"])
    payload = dict(spec["connector"])
    ensure_calls = _live_ensure_connector(base_url, payload)
    calls.extend(ensure_calls)
    connector_id = _connector_id_from_calls(ensure_calls, name)
    _assert_live(calls, f"connector {name} id", connector_id is not None, "no connector id from ensure calls")
    if connector_id is None:
        return {"name": name, "connector_id": None}
    # The server normalizes simulator addresses per stack mode; a leaked
    # Compose hostname (or an example.com placeholder) fails the seed.
    detail = _control_call(base_url, "GET", f"/connectors/{connector_id}")
    calls.append(detail)
    stored: Any = None
    if isinstance(detail.get("body"), dict):
        stored = detail["body"].get("base_url")
    addr_error = _check_connector_address(str(payload.get("base_url", "")), stored)
    _assert_live(calls, f"connector {name} address fits this stack mode", addr_error is None, addr_error or "")
    if isinstance(stored, str) and "example.com" in stored:
        _assert_live(calls, f"connector {name} real address", False, f"placeholder address stored: {stored!r}")

    docs_text = (REPO_ROOT / str(spec["docs_file"])).read_text(encoding="utf-8")
    refused_vocab = spec.get("refused_vocabulary")
    if refused_vocab:
        refused = _live_build_and_poll(
            base_url, connector_id,
            {"docs_text": docs_text, "spec_vocabulary": refused_vocab},
            calls,
        )
        _assert_live(
            calls, f"{name} {refused_vocab} docs build refused (recorded, no release)",
            refused.get("success") is False and not refused.get("release_id"),
            f"{refused_vocab} build result: {refused}",
        )
        reason = str(refused.get("degradation_reason") or "")
        _assert_live(
            calls, f"{name} {refused_vocab} refusal names POST and ConnectorSpec v1",
            "POST" in reason and "ConnectorSpec v1" in reason,
            f"{refused_vocab} degradation_reason: {reason!r}",
        )
    build_result: dict[str, Any] = _live_build_and_poll(
        base_url, connector_id,
        {"docs_text": docs_text, "spec_vocabulary": str(spec["spec_vocabulary"])},
        calls,
    )
    _assert_live(
        calls, f"{name} docs build with {spec['spec_vocabulary']} succeeds",
        build_result.get("success") is True,
        f"build result: {build_result}",
    )
    release_id = build_result.get("release_id")
    _assert_live(
        calls, f"{name} build persisted a release",
        isinstance(release_id, str) and bool(release_id),
        f"build result: {build_result}",
    )
    if isinstance(release_id, str) and release_id:
        detail2 = _control_call(base_url, "GET", f"/connectors/{connector_id}")
        calls.append(detail2)
        active: Any = None
        if isinstance(detail2.get("body"), dict):
            active = detail2["body"].get("active_release_id")
        if active != release_id:
            calls.append(
                _control_call(
                    base_url, "POST",
                    f"/connectors/{connector_id}/releases/{release_id}/activate", {},
                )
            )
    sync_result = _sync_terminal(base_url, connector_id, calls)
    _assert_live(
        calls, f"{name} baseline sync Complete",
        sync_result.get("outcome") == "Complete",
        f"baseline sync outcome {sync_result.get('outcome')}: {sync_result.get('unmet_criteria')}",
    )
    return {
        "name": name,
        "connector_id": connector_id,
        "release_id": release_id if isinstance(release_id, str) else None,
        "spec_vocabulary": spec["spec_vocabulary"],
        "refused_vocabulary": refused_vocab,
        "baseline_outcome": sync_result.get("outcome"),
    }


def verify_end_state(base_url: str, calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Assert the seeded end state: 4 working, 0 tokens, nothing waiting."""
    state: dict[str, Any] = {}
    connectors_rec = _control_call(base_url, "GET", "/connectors")
    calls.append(connectors_rec)
    rows: list[Any] = []
    body = connectors_rec.get("body")
    if isinstance(body, list):
        rows = body
    elif isinstance(body, dict) and isinstance(body.get("connectors"), list):
        rows = body["connectors"]
    names = sorted(str(r.get("name")) for r in rows if isinstance(r, dict))
    _assert_live(calls, "seed connectors are exactly the three recording names",
                 names == sorted(SEED_NAMES), f"connectors: {names}")
    states = {str(r.get("name")): str(r.get("state")) for r in rows if isinstance(r, dict)}
    _assert_live(calls, "every seed connector working",
                 bool(rows) and all(s == "working" for s in states.values()),
                 f"connector states: {states}")
    synced = (
        bool(rows)
        and all(bool(r.get("last_sync_at")) for r in rows if isinstance(r, dict))
    )
    missing_sync = sorted(
        str(r.get("name")) for r in rows if isinstance(r, dict) and not r.get("last_sync_at")
    )
    _assert_live(calls, "every seed connector synced at least once",
                 synced, f"never synced: {missing_sync}")
    state["connector_states"] = states

    drift_rec = _control_call(base_url, "GET", "/drift")
    calls.append(drift_rec)
    drift_rows = drift_rec.get("body")
    _assert_live(calls, "no open drift after seed",
                 isinstance(drift_rows, list) and len(drift_rows) == 0,
                 f"drift rows: {drift_rows}")
    repairs_rec = _control_call(base_url, "GET", "/repairs")
    calls.append(repairs_rec)
    repair_rows = repairs_rec.get("body")
    _assert_live(calls, "nothing waiting after seed (no repair rows)",
                 isinstance(repair_rows, list) and len(repair_rows) == 0,
                 f"repair rows: {repair_rows}")
    state["waiting_repairs"] = 0 if isinstance(repair_rows, list) else repair_rows

    usage_rec = _control_call(base_url, "GET", "/metrics/model-usage")
    calls.append(usage_rec)
    usage_body = usage_rec.get("body") if isinstance(usage_rec.get("body"), dict) else {}
    total_calls = int(usage_body.get("total_calls", -1) or 0) if isinstance(usage_body, dict) else -1
    frontier = int(usage_body.get("frontier_tokens", -1) or 0) if isinstance(usage_body, dict) else -1
    small = int(usage_body.get("small_tokens", -1) or 0) if isinstance(usage_body, dict) else -1
    _assert_live(calls, "0 model tokens after seed (deterministic builds+syncs only)",
                 total_calls == 0 and frontier == 0 and small == 0,
                 f"model usage: {usage_body}")
    state["model_usage"] = {"calls": total_calls, "small_tokens": small, "frontier_tokens": frontier}

    knowledge = _live_knowledge(base_url, calls)
    entries = knowledge.get("entries")
    _assert_live(calls, "knowledge starts empty (learning happens on approve, REQ-030 2b)",
                 isinstance(entries, list) and len(entries) == 0,
                 f"knowledge entries: {entries}")
    state["knowledge_entries"] = 0 if isinstance(entries, list) else entries
    return state


def run_seed(base_url: str, seed: int) -> dict[str, Any]:
    """Reset, seed the three connectors, verify the end state."""
    calls: list[dict[str, Any]] = []
    # REQ-031 owner check: the scheduler pauses for the whole seed (an
    # explicit pause/resume the scheduler honours), not just inside reset
    # — a tick landing between reset and a build (or mid-build) writes
    # rows for half-seeded connectors and races the seed's own syncs.
    # The pause is owner-scoped: reset (called below) keeps it, and the
    # finally resumes exactly what this seed paused.
    pause_rec = _control_call(
        base_url, "POST", "/demo/scheduler/pause",
        {"owner": "demo-seed", "reason": "demo-seed in progress"},
    )
    calls.append(pause_rec)
    pause_ok = pause_rec.get("status") == 200
    if not pause_ok:
        calls.append({"method": "ASSERT", "path": "/demo/scheduler/pause", "status": "assertion-failed",
                      "error": f"scheduler pause failed: {pause_rec.get('status')}: {pause_rec.get('body') or pause_rec.get('error')}"})
        failed = [c for c in calls if c.get("status") == "assertion-failed" or not _call_ok(c)]
        return {"mode": "live", "seed": seed, "connectors": [], "failed": len(failed),
                "control_calls": calls, "error": "scheduler pause failed"}
    try:
        report = _run_seed_paused(base_url, seed, calls)
    finally:
        resume_rec = _control_call(
            base_url, "POST", "/demo/scheduler/resume", {"owner": "demo-seed"},
        )
        calls.append(resume_rec)
    # Recount after the resume lands: a failed resume fails the seed (the
    # scheduler must not stay paused behind a green seed).
    failed = [c for c in calls if c.get("status") == "assertion-failed" or not _call_ok(c)]
    report["failed"] = len(failed)
    report["control_calls"] = calls
    return report


def _run_seed_paused(base_url: str, seed: int, calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Reset, seed the three connectors, verify the end state (scheduler paused)."""
    reset_rec = _control_call(base_url, "POST", "/demo/reset", {})
    calls.append(reset_rec)
    if not _call_ok(reset_rec):
        calls.append({"method": "ASSERT", "path": "/demo/reset", "status": "assertion-failed",
                      "error": f"reset failed: {reset_rec.get('status')}: {reset_rec.get('body') or reset_rec.get('error')}"})
        failed = [c for c in calls if c.get("status") == "assertion-failed" or not _call_ok(c)]
        return {"mode": "live", "seed": seed, "connectors": [], "failed": len(failed),
                "control_calls": calls, "error": "demo-reset failed"}
    connectors: list[dict[str, Any]] = []
    for spec in SEED_CONNECTORS:
        record = seed_connector(base_url, dict(spec), calls)
        connectors.append(record)
        if record.get("connector_id") is None or record.get("baseline_outcome") != "Complete":
            break
    end_state: dict[str, Any] = {}
    if len(connectors) == len(SEED_CONNECTORS) and all(c.get("baseline_outcome") == "Complete" for c in connectors):
        end_state = verify_end_state(base_url, calls)
    failed = [c for c in calls if c.get("status") == "assertion-failed" or not _call_ok(c)]
    return {
        "mode": "live",
        "seed": seed,
        "connectors": connectors,
        "end_state": end_state,
        "failed": len(failed),
        "control_calls": calls,
    }


def render_text(report: dict[str, Any]) -> str:
    """One block per connector plus the end-state line."""
    lines = [f"demo-seed — seed {report.get('seed')} ({report.get('mode')})"]
    for conn in report.get("connectors", []):
        extra = ""
        if conn.get("refused_vocabulary"):
            extra = f" ({conn['refused_vocabulary']} refused, recorded)"
        lines.append(
            f"  {conn.get('name')}: release {conn.get('release_id')} "
            f"· v{conn.get('spec_vocabulary')}{extra} · baseline {conn.get('baseline_outcome')}"
        )
    end = report.get("end_state") or {}
    if end:
        states = end.get("connector_states", {})
        usage = end.get("model_usage", {})
        lines.append(
            "seeded: " + ", ".join(f"{name} {state}" for name, state in sorted(states.items()))
            + f" · {usage.get('calls', '?')} model calls · nothing waiting"
        )
    if report.get("failed"):
        lines.append(f"seed FAILED: {report['failed']} failing call(s)")
        for call in report.get("control_calls", []):
            if call.get("status") == "assertion-failed" or not _call_ok(call):
                detail = call.get("error") or call.get("body")
                try:
                    detail_text = json.dumps(detail, default=str)[:300]
                except Exception:
                    detail_text = str(detail)[:300]
                lines.append(f"    FAILED {call.get('method')} {call.get('path')}: {detail_text}")
    else:
        lines.append("demo-seed complete: three connectors working, 0 model tokens, nothing waiting")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for ``make demo-seed``."""
    import httpx  # noqa: PLC0415

    parser = argparse.ArgumentParser(description="Seed the recording's connectors (REQ-031 item 1)")
    parser.add_argument("--control-url", default=env_default("CONTROL_URL", DEFAULT_CONTROL_URL))
    parser.add_argument("--seed", type=int, default=int(env_default("DEMO_SEED", str(DEFAULT_SEED))))
    args = parser.parse_args(argv)
    try:
        with httpx.Client(timeout=3) as client:
            client.get(args.control_url.rstrip("/") + "/healthz").raise_for_status()
    except Exception as exc:
        print(f"demo-seed: control plane unreachable at {args.control_url}: {exc}")
        return 1
    report = run_seed(args.control_url, args.seed)
    print(render_text(report), flush=True)
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
