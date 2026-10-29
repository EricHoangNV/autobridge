"""Detect drift from a sync summary and repair it (REQ-027 sub-step 3).

The live chain: a worker's (or ``POST /sync``) sync produces a summary via
:func:`autobridge.engine.sync_run.execute_sync`. This module derives drift
signals from that summary — observed responses only, never scenario ids,
fault YAML or answer keys — classifies them, persists a ``DriftEvent``,
and repairs: structural drifts run the deterministic ladder through
:meth:`RepairController.handle_from_signals` (detector-derived inputs
only) and, when the ladder proposes a patch, a candidate release is
persisted via :func:`create_release_from_patch` (verifier-gated when
``VERIFIER_URL`` is configured, fail-closed). A ``SEMANTIC_VALUE_DRIFT``
runs the same semantic climb as the instrumented path
(:func:`autobridge.repair.controller.run_semantic_repair` — engine
enumeration from the wire signals and the spec, wire filter, L2 when
>= 2, L3 through the frontier provider and budget when < 2, verifier
judge on the proposal, escalation question), so the live repair row
carries the funnel, the model call(s) and the rejected proposal.

Repairs here are data (a JSON Patch from the closed rule set, plan §1.1);
a semantic drift is never auto-patched: the climb always ends escalated
with one operator question.
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


def _find_undecided_open_drift(
    session: Any, connector_id: str, drift_class: str
) -> tuple[Any | None, Any | None]:
    """Newest open engine-derived drift of this class still awaiting a decision.

    Returns ``(drift, repair)`` when an ``open`` :class:`DriftEvent` for
    *(connector_id, drift_class)* exists whose newest repair has no
    :class:`Approval` row yet (no approve/reject/rollback decision), else
    ``(None, None)``.

    Scope is engine-derived drifts only: rows with
    ``source_sync_run_id`` set (a real ``POST /sync`` or scheduler sync
    wrote them). Labelled demo rows (``record-climb``) have no source sync
    and never match — a live break always opens its own drift.

    REQ-031 owner check (reset/seed-vs-scheduler race): without this, every
    sync on an undecided fault opens another drift and another repair —
    the scheduler tick and a driven sync (or two ticks) double the rows
    ("scar" drifts). One fault keeps exactly one drift and one repair
    until the operator decides.
    """
    from autobridge.control.models import Approval, DriftEvent, RepairRun

    drift = (
        session.execute(
            _select(DriftEvent)
            .where(
                DriftEvent.connector_id == connector_id,
                DriftEvent.drift_class == drift_class,
                DriftEvent.status == "open",
                DriftEvent.source_sync_run_id.is_not(None),
            )
            .order_by(DriftEvent.created_at.desc())
        )
        .scalars()
        .first()
    )
    if drift is None:
        return None, None
    repair: Any | None = None
    for run in (
        session.execute(
            _select(RepairRun)
            .where(RepairRun.drift_class == drift_class)
            .order_by(RepairRun.created_at.desc())
            .limit(50)
        )
        .scalars()
        .all()
    ):
        if drift.id in (run.drift_event_ids or []):
            repair = run
            break
    if repair is None:
        # An open drift with no repair is not the undecided state this
        # dedupes (something deleted the repair); fall through and repair
        # it instead of silently attaching to a repair-less drift.
        return None, None
    decided = (
        session.execute(
            _select(Approval).where(Approval.repair_run_id == repair.id)
        )
        .scalars()
        .first()
    )
    if decided is not None:
        # Approved/rejected/rolled back: the operator decided, so a new
        # detection is a new incident (e.g. the inject-again leg after an
        # approval) — never dedupe onto a decided repair.
        return None, None
    return drift, repair


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
    if all(s.kind == "zero_records" for s in signals):
        # An empty entity on a healthy sync is not drift (REQ-031): the
        # PABX edge connector idles at zero RoomStatus records between
        # postings bursts, and a quiet hotel day is not an incident.
        # Blocked/Degraded outcomes always add their own signals, so this
        # only quiets Complete syncs — the endpoint_removed pattern (zero
        # records + Blocked) still fires through capability_removed.
        resolved = _resolve_verified_drifts(session, connector_id, release_id, summary)
        return {"drift_found": False, "outcome": outcome, "drifts_resolved": resolved, "reason": "empty_entities_on_healthy_sync"}

    classification = classify(signals)
    delta = build_detector_delta(spec_dict, signals)

    # REQ-031: one fault keeps one drift and one repair until the operator
    # decides. A sync that re-detects an already-open, still-undecided
    # drift of the same class returns the existing rows — no second drift,
    # no second repair, no second candidate release. The sync itself still
    # lands (its SyncRun row and outcome are written by the caller); only
    # drift/repair creation dedupes.
    existing_drift, existing_repair = _find_undecided_open_drift(
        session, connector_id, classification.drift_class
    )
    if existing_drift is not None and existing_repair is not None:
        dedupe_candidate_id: str | None = None
        try:
            from autobridge.control.models import ConnectorRelease

            candidate = (
                session.execute(
                    _select(ConnectorRelease)
                    .where(
                        ConnectorRelease.connector_id == connector_id,
                        ConnectorRelease.provenance_repair_run_id
                        == existing_repair.id,
                    )
                    .order_by(ConnectorRelease.version.desc())
                )
                .scalars()
                .first()
            )
            if candidate is not None:
                dedupe_candidate_id = candidate.id
        except Exception:
            dedupe_candidate_id = None
        return {
            "drift_found": True,
            "deduped": True,
            "outcome": outcome,
            "drift_id": existing_drift.id,
            "drift_class": classification.drift_class,
            "drift_reason": classification.reason,
            "repair_run_id": existing_repair.id,
            "decision": existing_repair.decision,
            "rule_id": existing_repair.rule_id,
            "ladder_level_reached": existing_repair.ladder_level_reached,
            "model_usage": dict(existing_repair.model_usage or {}),
            "candidate_release_id": dedupe_candidate_id,
            "gate_error": None,
        }

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
    semantic_info: dict[str, Any] = {}
    if classification.drift_class == "SEMANTIC_VALUE_DRIFT":
        # REQ-039: the live repair path runs the same semantic climb as
        # the instrumented one — engine enumeration from the wire signals
        # and the spec, wire filter, L2 when >= 2, L3 through the frontier
        # provider and budget when < 2, verifier judge on the proposal,
        # escalation question. Never the deterministic 0-call escalation.
        from autobridge.repair.controller import run_semantic_repair  # noqa: PLC0415

        active_verifier = verifier_url if verifier_url is not None else os.environ.get("VERIFIER_URL")
        result, semantic_info = run_semantic_repair(
            spec=spec_dict,
            drift_class=classification.drift_class,
            signals=list(signals),
            fingerprint_delta=delta,
            summary=summary if isinstance(summary, dict) else None,
            sync_run_id=sync_run_id,
            vendor=None,
            drift_event_ids=[drift.id],
            session=session,
            knowledge_cache=knowledge_cache,
            verifier_url=active_verifier or None,
        )
    else:
        result = controller.handle_from_signals(
            spec=spec_dict,
            signals=list(signals),
            fingerprint_delta=delta,
            drift_event_ids=[drift.id],
            session=session,
        )

    # The run handle() just persisted (same session, flushed): newest row.
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

    out: dict[str, Any] = {
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
    if semantic_info:
        # What the semantic climb did (provider mode, L3 attempt-or-skip,
        # verifier verdict summary) — the demo and the UI read the rows,
        # but the sync result carries the headline too.
        out["provider_mode"] = semantic_info.get("provider_mode")
        out["l3_status"] = semantic_info.get("l3_status")
        out["verdict"] = semantic_info.get("verdict")
    return out
