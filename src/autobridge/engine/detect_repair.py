"""Detect drift from a sync summary and repair it (REQ-027 sub-step 3).

The live chain: a worker's (or ``POST /sync``) sync produces a summary via
:func:`autobridge.engine.sync_run.execute_sync`. This module derives drift
signals from that summary — observed responses only, never scenario ids,
fault YAML or answer keys — classifies them, persists a ``DriftEvent``,
runs :meth:`RepairController.handle_from_signals` (detector-derived inputs
only) and, when the ladder proposes a patch, persists a candidate release
via :func:`create_release_from_patch` (verifier-gated when ``VERIFIER_URL``
is configured, fail-closed).

Repairs here are data (a JSON Patch from the closed rule set, plan §1.1);
the deterministic ladder runs first and only it runs here — no model calls
(``frontier_tokens == 0``). Semantic drifts escalate with one operator
question; the frontier climb lives in the demo/bench, not in this path.
"""

from __future__ import annotations

import os
from typing import Any

from sqlalchemy import select as _select


def _summary_has_responses(summary: dict[str, Any]) -> bool:
    """Whether the sync observed any vendor response (raw page or records).

    REQ-027 owner check: a sync that never reached the vendor (connection
    refused, DNS, unset placeholder) has no evidence of drift. Transport
    failures that returned pages (429/503 bodies) still count as observed.
    """
    results = summary.get("operation_results")
    if not isinstance(results, dict):
        return False
    for res in results.values():
        if isinstance(res, dict):
            raw_pages = res.get("raw_pages")
            records = res.get("records")
        else:
            raw_pages = getattr(res, "raw_pages", None)
            records = getattr(res, "records", None)
        if isinstance(raw_pages, list) and raw_pages:
            return True
        if isinstance(records, list) and records:
            return True
    return False


def _triples(signals: list[Any]) -> list[tuple[str | None, str | None, dict[str, Any]]]:
    """Normalize signals (objects or dicts) to (kind, operation, details)."""
    out: list[tuple[str | None, str | None, dict[str, Any]]] = []
    for sig in signals or []:
        if isinstance(sig, dict):
            kind = sig.get("kind")
            operation = sig.get("operation")
            details = sig.get("details")
        else:
            kind = getattr(sig, "kind", None)
            operation = getattr(sig, "operation", None)
            details = getattr(sig, "details", None)
        out.append(
            (
                kind if isinstance(kind, str) else None,
                operation if isinstance(operation, str) else None,
                details if isinstance(details, dict) else {},
            )
        )
    return out


def _spec_pagination_subset(spec: dict[str, Any], op_name: str) -> dict[str, Any]:
    """The spec's pagination facet for one operation (the "previous" side)."""
    prev: dict[str, Any] = {}
    try:
        ops = spec.get("operations", {})
        if not isinstance(ops, dict):
            return prev
        op = ops.get(op_name)
        if not isinstance(op, dict):
            return prev
        pag = op.get("pagination")
        if not isinstance(pag, dict):
            return prev
        for key in (
            "strategy",
            "items_path",
            "next_path",
            "total_path",
            "page_param",
            "limit_param",
            "cursor_param",
            "offset_param",
        ):
            if pag.get(key) is not None:
                prev[key] = pag[key]
    except Exception:
        return {}
    return prev


def build_detector_delta(
    spec: dict[str, Any], signals: list[Any]
) -> dict[str, Any] | None:
    """Build a fingerprint-delta-shaped dict from spec + wire signals.

    ``{"pagination_details": {op: {"previous": ..., "current": ...}},
    "types_details": {entity: {"previous": {old: ...}, "current": {new: ...}}}}``
    where "previous" is the spec's declared shape and "current" is what the
    wire served. Only names and strategies go in (never samples or secrets),
    so the same drift re-syncs to the same L0 key while a different drift
    does not. Returns None when the signals carry no wire evidence — the
    caller then runs the ladder with ``fingerprint_delta=None`` (L0 skipped,
    rules decide from signals; never cached under a content-free key).
    """
    pagination_details: dict[str, Any] = {}
    types_details: dict[str, Any] = {}
    for kind, operation, details in _triples(signals):
        if kind == "pagination_changed" and isinstance(operation, str) and operation:
            prev = _spec_pagination_subset(spec, operation)
            if not prev:
                continue
            cur = dict(prev)
            hint = details.get("wire_strategy_hint")
            if isinstance(hint, str) and hint:
                cur["strategy"] = hint
            leaf = details.get("observed_leaf")
            if isinstance(leaf, str) and leaf:
                cur["next_path"] = f"$.{leaf}"
            candidates = details.get("array_candidates")
            if (
                isinstance(candidates, list)
                and len(candidates) == 1
                and isinstance(candidates[0], str)
            ):
                cur["items_path"] = candidates[0]
            if cur != prev and operation not in pagination_details:
                pagination_details[operation] = {"previous": prev, "current": cur}
        elif kind == "keys_changed":
            missing = details.get("missing")
            added = details.get("added")
            if (
                isinstance(missing, list)
                and len(missing) == 1
                and isinstance(missing[0], str)
                and isinstance(added, list)
                and len(added) == 1
                and isinstance(added[0], dict)
                and isinstance(added[0].get("key"), str)
            ):
                entity = details.get("entity")
                ent = entity if isinstance(entity, str) and entity else None
                if ent is None:
                    ent = next(iter(spec.get("entities", {}) or {}), "unknown")
                if ent not in types_details:
                    types_details[ent] = {
                        "previous": {missing[0]: "str"},
                        "current": {str(added[0]["key"]): "str"},
                    }
    delta: dict[str, Any] = {}
    if pagination_details:
        delta["pagination_details"] = pagination_details
    if types_details:
        delta["types_details"] = types_details
    return delta or None


def _resolve_verified_drifts(
    session: Any, connector_id: str, release_id: str, summary: dict[str, Any]
) -> list[str]:
    """Mark an approved repair's drifts repaired after its verifying sync.

    Only when the sync landed ``Complete`` on a release whose provenance is
    a repair run that has an approve ``Approval``: that is the operator's
    verified fix. Everything else (other releases, other outcomes,
    unapproved repairs) leaves drift rows untouched. Best-effort: detection
    failures are row data, never a sync failure.
    """
    try:
        from autobridge.control.models import Approval, ConnectorRelease, DriftEvent

        if (summary.get("outcome") if isinstance(summary, dict) else None) != "Complete":
            return []
        release = session.get(ConnectorRelease, release_id)
        if release is None:
            return []
        provenance_id = getattr(release, "provenance_repair_run_id", None)
        if not provenance_id:
            return []
        approved = (
            session.execute(
                _select(Approval).where(
                    Approval.repair_run_id == provenance_id,
                    Approval.decision == "approve",
                )
            )
            .scalars()
            .first()
        )
        if approved is None:
            return []
        from autobridge.control.models import RepairRun as _RepairRun

        repair = session.get(_RepairRun, provenance_id)
        if repair is None:
            return []
        resolved: list[str] = []
        for did in list(repair.drift_event_ids or []):
            drift = session.get(DriftEvent, did)
            if (
                drift is not None
                and drift.connector_id == connector_id
                and drift.status == "open"
            ):
                drift.status = "repaired"
                resolved.append(drift.id)
        if resolved:
            session.flush()
        return resolved
    except Exception:
        return []


def detect_and_repair_after_sync(
    session: Any,
    *,
    connector_id: str,
    release_id: str,
    spec_dict: dict[str, Any],
    summary: dict[str, Any],
    sync_run_id: str,
    verifier_url: str | None = None,
    knowledge_cache: Any | None = None,
) -> dict[str, Any]:
    """Detect drift from a sync *summary* and repair it, persisting everything.

    Creates a ``DriftEvent`` (status ``open``) and runs the deterministic
    ladder through :meth:`RepairController.handle_from_signals`, which
    persists the ``RepairRun`` (rung, rule id, zero model usage, ladder
    trace). A proposed patch becomes a candidate release (provenance-linked
    to the repair run) awaiting operator approval; the verifier gate runs
    first when *verifier_url* (default ``VERIFIER_URL`` env) is set.

    A sync with no signals is not drift: ``{"drift_found": False}`` and no
    rows are written. A ``Complete`` sync can still carry wire-contradiction
    signals (silent shape change with no invariant failure) — that is
    drift, and it is repaired here.
    """
    from autobridge.control.models import DriftEvent, RepairRun
    from autobridge.drift.classifier import classify
    from autobridge.drift.signals import signals_from_sync
    from autobridge.repair.controller import RepairController

    outcome = summary.get("outcome") if isinstance(summary, dict) else None
    if not _summary_has_responses(summary if isinstance(summary, dict) else {}):
        # No vendor response, no drift evidence: a Blocked connectivity
        # failure stays the sync's own outcome (recorded on the SyncRun),
        # never a DriftEvent, never a repair. Fail closed, loudly visible.
        return {"drift_found": False, "outcome": outcome, "reason": "no_vendor_response"}
    signals = signals_from_sync(
        summary, spec=spec_dict, source_run_id=sync_run_id
    )
    if not signals:
        # No drift on a sync that reached the vendor. When this sync ran
        # on a release created by an approved repair, it is the verifying
        # sync — landing Complete retires that repair's drift events (the
        # connector returns to Working). Any other outcome leaves them
        # open: the fix is not verified yet.
        resolved = _resolve_verified_drifts(session, connector_id, release_id, summary)
        return {"drift_found": False, "outcome": outcome, "drifts_resolved": resolved}

    classification = classify(signals)
    delta = build_detector_delta(spec_dict, signals)

    drift = DriftEvent(
        connector_id=connector_id,
        drift_class=classification.drift_class,
        severity=classification.severity,
        evidence_ids=list(classification.evidence_ids),
        source_sync_run_id=sync_run_id,
        status="open",
    )
    session.add(drift)
    session.flush()

    controller = RepairController(knowledge_cache=knowledge_cache) if knowledge_cache is not None else RepairController()
    result = controller.handle_from_signals(
        spec=spec_dict,
        signals=list(signals),
        fingerprint_delta=delta,
        drift_event_ids=[drift.id],
        session=session,
    )

    # The run handle() just persisted (same session, flushed): newest row.
    from sqlalchemy import select as _select

    repair_run = session.execute(
        _select(RepairRun).order_by(RepairRun.created_at.desc()).limit(1)
    ).scalars().first()

    candidate_release_id: str | None = None
    gate_error: str | None = None
    if result.decision == "propose" and result.candidate_patch and repair_run is not None:
        from autobridge.engine.releases import create_release_from_patch

        active_verifier = verifier_url if verifier_url is not None else os.environ.get("VERIFIER_URL")
        try:
            candidate = create_release_from_patch(
                session,
                connector_id=connector_id,
                base_release_id=release_id,
                patch=list(result.candidate_patch),
                provenance_repair_run_id=repair_run.id,
                verifier_url=active_verifier or None,
            )
            session.flush()
            candidate_release_id = candidate.id
            # REQ-030 item 3: the verifier verdict lives on the repair row
            # too (the candidate release carries the report id) — the
            # repair page reads verdicts from here, never recomputed.
            if candidate.verification_report_id:
                repair_run.verifier_report_id = candidate.verification_report_id
                session.flush()
        except Exception as exc:  # noqa: BLE001 — gate/patch failures are row data
            gate_error = f"{type(exc).__name__}: {exc}"[:500]

    return {
        "drift_found": True,
        "outcome": outcome,
        "drift_id": drift.id,
        "drift_class": classification.drift_class,
        "drift_reason": classification.reason,
        "repair_run_id": repair_run.id if repair_run is not None else None,
        "decision": result.decision,
        "rule_id": result.rule_id,
        "ladder_level_reached": result.ladder_level_reached,
        "model_usage": dict(result.model_usage),
        "candidate_release_id": candidate_release_id,
        "gate_error": gate_error,
    }
