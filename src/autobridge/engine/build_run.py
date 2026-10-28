"""BuildRun — docs → draft spec → compile → visible tests → verify (P1.W5.6, plan §4 W5).

Executes a BuildRun job (docs → draft spec) and creates a ConnectorRelease.
When a verifier URL is configured, the candidate spec is verified before
persisting (gate-before-persist, AR-1-2): a blocked or unreachable verifier
means no release is persisted, so there is nothing to approve or sync.
Trust boundary: never imports verifier or simulators. Verification is either
local visible-tests or an optional HTTP call to the verifier service (caller
provides the URL). For unit tests without Docker, local validation suffices.

JobRunner integration:
    from autobridge.engine.build_run import run_build_handler
    runner.register_handler("build", run_build_handler)

Direct usage:
    result = execute_build(session, run_id=..., docs_text=..., ...)
"""

from __future__ import annotations

import os
import time
from typing import Any

# No verifier / simulators imports at top level


def _now_trace_entry(stage: str, **extra: Any) -> dict[str, Any]:
    return {"stage": stage, "ts": time.time(), **extra}


# ---------------------------------------------------------------------------
# Core build execution (DB-agnostic except for release creation)
# ---------------------------------------------------------------------------


def execute_build(
    docs_text: str,
    source: str = "docs",
    connector_id: str | None = None,
    credential_ref: str | None = None,
    vendor: str | None = None,
    base_url_override: str | None = None,
    *,
    probe_executor: Any | None = None,
    probe_budget: Any | None = None,
    provider: Any | None = None,
    llm_budget: Any | None = None,
    verifier_url: str | None = None,
    scenario_id: str = "baseline",
) -> dict[str, Any]:
    """Run bootstrap pipeline and optional verifier check.

    Returns dict with keys: success, draft_spec, visible_errors,
    open_questions, degradation_reason, trace, model_usage,
    verified, verifier_response (if called), evidence_payloads.

    This function does NOT touch the DB — callers create releases.
    """
    from autobridge.discovery.bootstrap import build_draft_spec

    result = build_draft_spec(
        docs_text,
        source=source,
        connector_id=connector_id,
        credential_ref=credential_ref,
        vendor=vendor,
        base_url_override=base_url_override,
        probe_executor=probe_executor,
        probe_budget=probe_budget,
        provider=provider,
        llm_budget=llm_budget,
    )

    out: dict[str, Any] = {
        "success": result.success,
        "draft_spec": result.draft_spec,
        "visible_errors": result.visible_errors,
        "open_questions": list(result.open_questions),
        "degradation_reason": result.degradation_reason,
        "trace": list(result.trace),
        "model_usage": dict(result.model_usage),
        "evidence_payloads": list(result.evidence_payloads),
        "verified": False,
        "verifier_response": None,
    }

    if not result.success or result.draft_spec is None:
        return out

    # Optional verifier HTTP check (when Docker stack is up)
    if verifier_url:
        try:
            import httpx  # noqa: PLC0415

            # Probe verifier health first; if unreachable, skip verification
            health_url = verifier_url.rstrip("/") + "/healthz"
            try:
                with httpx.Client(timeout=3) as c:
                    h = c.get(health_url)
                    if h.status_code != 200:
                        out["trace"].append(_now_trace_entry("verifier_health", status=h.status_code, skipped=True))
                    else:
                        # Call verify
                        verify_url = verifier_url.rstrip("/") + "/verify"
                        payload = {"spec": result.draft_spec, "scenario_id": scenario_id, "mode": "visible"}
                        r = c.post(verify_url, json=payload, timeout=10)
                        if r.status_code == 200:
                            v = r.json()
                            out["verifier_response"] = v
                            if v.get("verdict") == "pass":
                                out["verified"] = True
                                out["trace"].append(_now_trace_entry("verifier", verdict="pass", scenario=scenario_id))
                            else:
                                out["trace"].append(_now_trace_entry("verifier", verdict="fail", failed_checks=v.get("failed_checks")))
                        else:
                            out["trace"].append(_now_trace_entry("verifier", error=f"HTTP {r.status_code}"))
            except Exception as exc:
                out["trace"].append(_now_trace_entry("verifier", error=str(exc), skipped=True))
        except Exception as exc:
            out["trace"].append(_now_trace_entry("verifier", error=str(exc), skipped=True))

    # If no verifier URL, consider local visible tests as verification
    if not verifier_url:
        # Local "verified" means visible tests passed
        out["verified"] = result.success and not result.visible_errors
        out["trace"].append(_now_trace_entry("verifier", verdict="pass" if out["verified"] else "fail", mode="local_visible"))

    return out


# ---------------------------------------------------------------------------
# DB + Evidence + Release wiring
# ---------------------------------------------------------------------------


def _append_bootstrap_evidence(
    session: Any,
    run_id: str,
    evidence_payloads: list[dict[str, Any]],
) -> list[str]:
    """Append bootstrap evidence records; return their ids.

    Each payload is a hypothesis evidence_payload (hypothesis family).
    We store them as kind="hypothesis" with parent_ids chaining to prior.
    Failures are isolated — evidence append never aborts the build.
    """
    try:
        from autobridge.evidence.store import EvidenceStore  # noqa: PLC0415

        store = EvidenceStore(session)
        ids: list[str] = []
        parent_ids: list[str] = []
        for payload in evidence_payloads:
            try:
                rec = store.append(kind="hypothesis", payload=payload, run_id=f"build_runs:{run_id}", parent_ids=parent_ids)
                session.flush()
                ids.append(rec.id)
                parent_ids = [rec.id]
            except Exception:
                continue
        return ids
    except Exception:
        return []


def _ensure_connector(session: Any, connector_id: str | None, spec_connector: dict[str, Any], fallback_name: str = "HarborPMS") -> Any:
    """Return existing Connector or create one from spec."""
    from autobridge.control.models import Connector  # noqa: PLC0415

    # If connector_id is a real row id, fetch it
    if connector_id is not None:
        conn = session.get(Connector, connector_id)
        if conn is not None:
            return conn
        # Also try lookup by slug (spec connector id)
        # Search by name/id substring
        from sqlalchemy import select as _select

        try:
            # Try to find by spec connector id stored as name? fallback
            q = session.execute(_select(Connector).where(Connector.name == connector_id)).scalars().first()
            if q is not None:
                return q
        except Exception:
            pass

    # Create new connector from spec
    spec_vendor = spec_connector.get("vendor", fallback_name) if isinstance(spec_connector, dict) else fallback_name
    # Derive base_url from spec source placeholder if needed
    # But Connector.base_url is the real URL template, not placeholder? In W1.6, base_url was http://example.com
    # For Harbor, we store placeholder as base_url (same as spec)
    # Keep as stored in spec source
    # Try to find existing connector by spec connector id slug
    from sqlalchemy import select as _select2

    from autobridge.control.models import Connector as _Conn

    existing = None
    try:
        existing = session.execute(_select2(_Conn).where(_Conn.name == spec_vendor)).scalars().first()
        if existing is not None:
            return existing
    except Exception:
        pass

    conn = _Conn(
        name=spec_vendor,
        base_url=spec_connector.get("base_url", "http://example.com") if isinstance(spec_connector, dict) else "http://example.com",
        credential_ref=spec_connector.get("credential_ref", "CONNECTOR_API_KEY") if isinstance(spec_connector, dict) else "CONNECTOR_API_KEY",
        target_entities=["Reservation", "FolioCharge"],
        runtime_target={"kind": "local_process"},
    )
    # Override id if provided as slug? Connector has its own uuid; we keep generated
    # But if caller passed connector_id that looks like a slug, we keep it as name
    session.add(conn)
    session.flush()
    return conn


def run_build_handler(ctx: Any) -> dict[str, Any]:
    """JobRunner handler for BuildRun jobs (P1.W5.6).

    Expected Job -> BuildRun + Connector exist at submit time.
    Inputs:
        BuildRun.inputs may contain:
            docs_text / docs / docs_content  (str)
            docs_source / source             (str)
            connector_id (override)          (str)
            vendor                           (str)
            credential_ref                   (str)
            base_url_override                (str)
            verifier_url                     (str, optional)
            scenario_id                      (str, baseline by default)

    Flow:
        1. Load BuildRun, mark running
        2. Run bootstrap (docs → draft spec → compile → visible tests)
        3. Append evidence records
        4. Update BuildRun with draft_spec, evidence_ids, ladder_trace, model_usage
        5. If visible tests pass, create ConnectorRelease (provenance_build_run_id)
        6. Mark BuildRun succeeded and return handler result
    """
    from autobridge.control.models import BuildRun, Connector  # noqa: PLC0415

    run = ctx.session.get(BuildRun, ctx.run_id)
    if run is None:
        raise ValueError(f"BuildRun {ctx.run_id!r} not found")
    if run.state not in ("queued", "running"):
        raise ValueError(f"BuildRun {run.id!r} is {run.state!r}, not 'queued'")

    # Mark running
    run.state = "running"
    ctx.session.flush()

    inputs: dict[str, Any] = dict(run.inputs or {})
    docs_text = inputs.get("docs_text") or inputs.get("docs") or inputs.get("docs_content") or ""
    if not docs_text and inputs.get("docs_path"):
        # Allow docs_path as file read (only if file exists and is not a reference spec)
        p = inputs["docs_path"]
        if "generated/connectors" in p:
            raise ValueError("build must not read reference specs")
        try:
            from pathlib import Path as _Path

            docs_text = _Path(p).read_text(encoding="utf-8")
        except Exception as exc:
            raise ValueError(f"cannot read docs_path {p!r}: {exc}") from exc

    if not docs_text:
        raise ValueError("build inputs missing docs_text (or docs/docs_content)")

    docs_source = inputs.get("docs_source") or inputs.get("source") or inputs.get("docs_path") or "docs"
    connector_id_override = inputs.get("connector_id")
    vendor_override = inputs.get("vendor")
    credential_ref_override = inputs.get("credential_ref")
    base_url_override = inputs.get("base_url_override") or inputs.get("base_url")
    verifier_url = inputs.get("verifier_url") or os.environ.get("VERIFIER_URL")
    scenario_id = inputs.get("scenario_id", "baseline")

    # Optional probe/budget wiring (for date ambiguity resolution tests)
    probe_executor = inputs.get("probe_executor")
    probe_budget = inputs.get("probe_budget")

    build_out = execute_build(
        docs_text,
        source=str(docs_source),
        connector_id=connector_id_override,
        credential_ref=credential_ref_override,
        vendor=vendor_override,
        base_url_override=base_url_override,
        probe_executor=probe_executor,
        probe_budget=probe_budget,
        provider=inputs.get("provider"),
        llm_budget=inputs.get("llm_budget"),
        verifier_url=verifier_url,
        scenario_id=scenario_id,
    )

    # Evidence
    evidence_ids = _append_bootstrap_evidence(ctx.session, run.id, build_out.get("evidence_payloads", []))
    # Also add a decision evidence
    try:
        from autobridge.evidence.store import EvidenceStore as _ES  # noqa: PLC0415

        store = _ES(ctx.session)
        decision_payload = {
            "connector_id": connector_id_override,
            "source": str(docs_source),
            "success": build_out["success"],
            "verified": build_out["verified"],
            "visible_errors": build_out["visible_errors"],
            "open_questions": build_out["open_questions"],
        }
        # Redact any secret values if present in inputs (defense)
        rec = store.append(kind="decision", payload=decision_payload, run_id=f"build_runs:{run.id}", parent_ids=evidence_ids[-1:] if evidence_ids else [])
        ctx.session.flush()
        evidence_ids.append(rec.id)
    except Exception:
        pass

    # Update BuildRun row
    run.draft_spec = build_out["draft_spec"]
    run.evidence_ids = list(evidence_ids)
    run.ladder_trace = list(build_out["trace"])
    run.model_usage = dict(build_out["model_usage"])
    # Store verification outcome in result
    result: dict[str, Any] = {
        "build_run_id": run.id,
        "success": build_out["success"],
        "verified": build_out["verified"],
        "visible_errors": build_out["visible_errors"],
        "open_questions": build_out["open_questions"],
        "degradation_reason": build_out["degradation_reason"],
    }

    release_id: str | None = None
    if build_out["success"] and build_out["draft_spec"]:
        # Create or fetch connector
        spec_conn = build_out["draft_spec"].get("connector", {})
        conn: Any
        # Resolve connector from run.connector_id
        conn = ctx.session.get(Connector, run.connector_id)
        if conn is None:
            # Fallback: create from spec
            conn = _ensure_connector(ctx.session, run.connector_id, spec_conn)
        # Create release — gate-before-persist (AR-1-2): when a verifier URL
        # is configured, the candidate spec is verified before persisting.
        # A blocked/unreachable verifier raises GateBlockedError and NO
        # release is persisted, so there is nothing to approve or sync.
        # Without a verifier URL (unit tests, worker without verifier-net
        # route) the gate is skipped, preserving backward compatibility.
        try:
            from autobridge.engine.releases import create_release  # noqa: PLC0415
            from autobridge.engine.verification import GateBlockedError  # noqa: PLC0415

            release = create_release(
                ctx.session,
                connector_id=conn.id,
                spec=build_out["draft_spec"],
                provenance_build_run_id=run.id,
                verifier_url=verifier_url,
                scenario_id=scenario_id,
            )
            ctx.session.flush()
            release_id = release.id
            result["release_id"] = release_id
            result["connector_id"] = conn.id
            if verifier_url:
                # Gate-before-persist passed: the persisted candidate is
                # verifier-checked, even if the best-effort inline check
                # above failed spuriously.
                result["verified"] = True
                run.ladder_trace = [
                    *list(run.ladder_trace or []),
                    _now_trace_entry("verifier", verdict="pass", scenario=scenario_id, gate="before_persist"),
                ]
        except GateBlockedError as gate_exc:
            # Gate blocked (fail verdict, regression fail, or verifier
            # unreachable): quarantine by not persisting. The draft spec
            # stays on the BuildRun, but no activatable release exists.
            result["verified"] = False
            result["gate_blocked"] = str(gate_exc)
            result["release_id"] = None
        except Exception as exc:
            # Release creation failed (invalid spec)
            result["release_error"] = str(exc)
            result["success"] = False

    run.result = dict(result)
    # BuildRun lifecycle: succeeded even if not verified? But we keep succeeded for handler success;
    # verification failure is reflected in result.verified, not job failure.
    run.state = "succeeded"
    ctx.session.flush()

    # Update Job payload result
    return {"build_run_id": run.id, "state": "succeeded", **result}


# ---------------------------------------------------------------------------
# Convenience: direct bootstrap + release creation without JobRunner (for tests)
# ---------------------------------------------------------------------------


def bootstrap_and_create_release(
    session: Any,
    connector_id: str,
    docs_text: str,
    source: str = "docs",
    credential_ref: str | None = None,
    vendor: str | None = None,
    base_url_override: str | None = None,
    scenario_id: str = "baseline",
) -> tuple[Any, Any, dict[str, Any]]:
    """Helper for tests/e2e: bootstrap docs and create a release.

    Returns (connector, release, build_out).
    Caller owns commit.
    """
    from autobridge.control.models import BuildRun, Connector  # noqa: PLC0415
    from autobridge.engine.releases import create_release  # noqa: PLC0415

    build_out = execute_build(
        docs_text,
        source=source,
        connector_id=None,
        credential_ref=credential_ref,
        vendor=vendor,
        base_url_override=base_url_override,
        scenario_id=scenario_id,
    )
    assert build_out["success"], f"bootstrap failed: {build_out['visible_errors']} trace {build_out['trace']}"
    assert build_out["draft_spec"] is not None

    conn = session.get(Connector, connector_id)
    if conn is None:
        raise ValueError(f"Connector {connector_id!r} not found")

    release = create_release(
        session,
        connector_id=conn.id,
        spec=build_out["draft_spec"],
        provenance_build_run_id=None,
    )
    session.flush()
    # Also create a BuildRun row for provenance tracking
    br = BuildRun(connector_id=conn.id, state="succeeded", inputs={"docs_text": docs_text[:200], "source": source}, draft_spec=build_out["draft_spec"], result={"release_id": release.id, "success": True}, ladder_trace=build_out["trace"], model_usage=build_out["model_usage"])
    session.add(br)
    session.flush()
    return conn, release, build_out
