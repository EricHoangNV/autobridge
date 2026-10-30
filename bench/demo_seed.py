"""`make demo-seed` driver (REQ-059: the blank-slate opening frame).

From ``make demo-reset``, checks the stack is clean and ready and prints
the opening frame ("0 connectors · ready to build"):

- every simulator up and on its baseline scenario (no injected fault);
- no connectors, no open drift, no repairs;
- learned knowledge and recorded decisions empty;
- the model-usage ledger at zero.

Nothing is built here by design (owner decision 2026-10-30): deck 2
builds Atlas from its OpenAPI live in the UI (vocabulary v1), deck 4
builds Harbor from its wiki, deck 6 builds Meridian (v1 refused, then
v2), deck 7 builds the PABX (v2 refused, then v3). ``make demo``
(terminal-only) still runs stand-alone: every step that needs a
connector builds it itself, so the full 8/8 passes from this blank
seed with no prior build.

Fails loud (exit non-zero naming the unmet check); the transcript
records every control call, redacted like the demo's.

Secrets (DIRECTIVES §6): only ``credential_ref`` env-key names travel
here; no credential value is printed, logged or stored.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from bench.demo import (
    DEFAULT_CONTROL_URL,
    DEFAULT_SEED,
    _assert_live,
    _call_ok,
    _control_call,
    _control_get_raw,
    _live_knowledge,
    env_default,
)

#: No connector is seeded (REQ-059). Kept as the (empty) tuple so the
#: seed contract — "exactly these connectors, all working" — still reads
#: in one place: nothing. ``run_seed`` asserts the list endpoint agrees.
SEED_CONNECTORS: tuple[dict[str, Any], ...] = ()

SEED_NAMES: tuple[str, ...] = ()


def _simulator_readiness(
    base_url: str, calls: list[dict[str, Any]]
) -> dict[str, Any]:
    """Every registry simulator up and on baseline (no injected fault).

    Uses the server-probed ``GET /demo/simulators`` rows (``live`` +
    ``active_scenario``), never simulator admin directly: the harness
    stays engine-side of the trust boundary.
    """
    calls.append(_control_call(base_url, "GET", "/demo/simulators"))
    raw = _control_get_raw(base_url, "/demo/simulators")
    rows = (raw.get("simulators") if isinstance(raw, dict) else None) or []
    if not isinstance(rows, list):
        rows = []
    state: dict[str, Any] = {"simulators": [], "down": [], "faulted": []}
    for row in rows:
        if not isinstance(row, dict):
            continue
        sim_id = str(row.get("id") or "?")
        state["simulators"].append(sim_id)
        if row.get("live") is not True:
            state["down"].append(sim_id)
        if row.get("active_scenario") is not None:
            state["faulted"].append({sim_id: row.get("active_scenario")})
    _assert_live(
        calls, "every simulator up",
        isinstance(rows, list) and len(rows) > 0 and not state["down"],
        f"down or unlisted: {state['down']} (sims: {state['simulators']})",
    )
    _assert_live(
        calls, "every simulator on its baseline scenario (no injected fault)",
        not state["faulted"],
        f"fault active: {state['faulted']}",
    )
    return state


def verify_blank_state(
    base_url: str, calls: list[dict[str, Any]]
) -> dict[str, Any]:
    """Assert the blank opening frame: nothing built, nothing learned."""
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
    _assert_live(calls, "seed leaves zero connectors",
                 names == [],
                 f"connectors: {names}")
    state["connectors"] = names

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
    _assert_live(calls, "0 model tokens after seed (no builds, no syncs yet)",
                 total_calls == 0 and frontier == 0 and small == 0,
                 f"model usage: {usage_body}")
    state["model_usage"] = {"calls": total_calls, "small_tokens": small, "frontier_tokens": frontier}

    knowledge = _live_knowledge(base_url, calls)
    entries = knowledge.get("entries")
    _assert_live(calls, "knowledge starts empty (learning happens on approve, REQ-030 2b)",
                 isinstance(entries, list) and len(entries) == 0,
                 f"knowledge entries: {entries}")
    profiles = knowledge.get("connectors")
    _assert_live(calls, "no connector built on it yet (no vocabulary in use)",
                 profiles is None or (isinstance(profiles, list) and len(profiles) == 0),
                 f"knowledge connectors: {profiles}")
    decisions = knowledge.get("decisions")
    _assert_live(calls, "no recorded decisions after reset (they clear like the rest)",
                 decisions is None or (isinstance(decisions, list) and len(decisions) == 0),
                 f"knowledge decisions: {decisions}")
    state["knowledge_entries"] = 0 if isinstance(entries, list) else entries

    state["readiness"] = _simulator_readiness(base_url, calls)
    return state


def run_seed(base_url: str, seed: int) -> dict[str, Any]:
    """Reset, then verify the blank opening frame (REQ-059)."""
    calls: list[dict[str, Any]] = []
    # REQ-031 owner check: the scheduler pauses for the whole seed (an
    # explicit pause/resume the scheduler honours), not just inside reset
    # — a tick landing between reset and a check writes rows for a stack
    # that must read empty. The pause is owner-scoped: reset (called
    # below) keeps it, and the finally resumes exactly what this seed
    # paused.
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
    """Reset, then verify the blank opening frame (scheduler paused)."""
    reset_rec = _control_call(base_url, "POST", "/demo/reset", {})
    calls.append(reset_rec)
    if not _call_ok(reset_rec):
        calls.append({"method": "ASSERT", "path": "/demo/reset", "status": "assertion-failed",
                      "error": f"reset failed: {reset_rec.get('status')}: {reset_rec.get('body') or reset_rec.get('error')}"})
        failed = [c for c in calls if c.get("status") == "assertion-failed" or not _call_ok(c)]
        return {"mode": "live", "seed": seed, "connectors": [], "failed": len(failed),
                "control_calls": calls, "error": "demo-reset failed"}
    end_state: dict[str, Any] = verify_blank_state(base_url, calls)
    failed = [c for c in calls if c.get("status") == "assertion-failed" or not _call_ok(c)]
    return {
        "mode": "live",
        "seed": seed,
        "connectors": [],
        "end_state": end_state,
        "failed": len(failed),
        "control_calls": calls,
    }


def render_text(report: dict[str, Any]) -> str:
    """One block for the readiness check plus the opening frame."""
    lines = [f"demo-seed — blank slate (seed {report.get('seed')}) ({report.get('mode')})"]
    end = report.get("end_state") or {}
    readiness = end.get("readiness") or {}
    sims = readiness.get("simulators") or []
    if sims:
        lines.append(
            f"  simulators ready: {', '.join(sorted(str(s) for s in sims))} "
            f"({len(sims)} up, all on baseline)"
        )
    usage = end.get("model_usage") or {}
    lines.append(
        f"  connectors: 0 · drift: 0 · repairs: 0 · learned: "
        f"{end.get('knowledge_entries', '?')} · model calls: {usage.get('calls', '?')}"
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
        lines.append("0 connectors · ready to build")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for ``make demo-seed``."""
    import httpx  # noqa: PLC0415

    parser = argparse.ArgumentParser(description="Seed the blank-slate opening frame (REQ-059)")
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
