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
            _select(Approval)
            .where(Approval.repair_run_id == repair.id)
            .order_by(Approval.created_at.desc())
        )
        .scalars()
        .first()
    )
    if decided is not None and decided.decision != "rollback":
        # Approved/rejected: the operator decided, so a new detection is
        # a new incident (e.g. the inject-again leg after an approval) —
        # never dedupe onto a decided repair.
        return None, None
    # No approval yet, or the latest approval is a rollback (manual or the
    # automatic one after a failed verifying sync, REQ-053 item 6): the fix
    # was reverted, so a re-detection of the same open drift re-uses the
    # original drift + repair instead of opening a second escalation.
    return drift, repair
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


def _live_frontier_wanted() -> bool:
    """Whether the resolved frontier provider would make a live call.

    REQ-042: the sync/request path must never run a model call. When the
    provider resolves to ``live`` mode (a real key), a semantic climb
    would call the frontier — so the sync queues the repair instead and
    returns. Replay/offline modes are local and fast: they run inline.
    A misconfigured live mode (raises) degrades to inline-off, exactly
    like :func:`autobridge.repair.controller.run_semantic_repair`.
    """
    try:
        from autobridge.llm.provider import resolve_frontier_provider  # noqa: PLC0415

        _provider, mode = resolve_frontier_provider()
        return mode == "live"
    except Exception:
        return False


def _submit_deferred_semantic_repair(
    session: Any,
    *,
    drift_id: str,
    connector_id: str,
    release_id: str,
    prepared: dict[str, Any],
    signals: list[Any],
    delta: Any | None,
    dry_run_fault: dict[str, Any] | None = None,
) -> str:
    """Queue the semantic climb as a background repair job (REQ-042).

    The sync already derived everything the climb needs (funnel stages,
    climbed hypothesis, L3 evidence); the parcel is JSON-safe so the job
    survives threads and restarts. Returns the repair job id.
    """
    from autobridge.control.runner import get_runner  # noqa: PLC0415
    from autobridge.discovery.hypotheses import hypothesis_to_json  # noqa: PLC0415
    from autobridge.drift.signals import signal_to_json  # noqa: PLC0415

    climbed = prepared.get("climbed")
    hyps_json = []
    if climbed is not None:
        hyps_json.append(hypothesis_to_json(climbed))
    runner = get_runner(session)
    return runner.submit_repair(
        [drift_id],
        payload_extra={
            "drift_class": "SEMANTIC_VALUE_DRIFT",
            "connector_id": connector_id,
            "release_id": release_id,
            "scenario": None,
            "signals_json": [signal_to_json(s) for s in (signals or [])],
            "fingerprint_delta": delta,
            "hypotheses_json": hyps_json,
            "hypothesis_stages": list(prepared.get("stages") or []),
            "l3_evidence": dict(prepared.get("evidence") or {}),
            # REQ-053 item 6: the background climb judges L3 proposals on
            # the break's records — no new vendor call from the job.
            "dry_run_fault": dry_run_fault,
        },
    )


def _finalize_proposal(
    session: Any,
    *,
    connector_id: str,
    release_id: str,
    repair_run_id: str | None,
    result: Any,
    verifier_url: str | None = None,
) -> tuple[str | None, str | None]:
    """Persist a proposed patch as a candidate release (verifier-gated).

    The candidate links to the repair row by its threaded id — never by
    re-fetching "the newest row" (concurrent repairs would cross-link,
    REQ-040/REQ-042). Gate/patch failures are row data, never raised.
    Returns ``(candidate_release_id, gate_error)``.
    """
    if result.decision != "propose" or not result.candidate_patch or repair_run_id is None:
        return None, None
    import os as _os  # noqa: PLC0415

    from autobridge.engine.releases import create_release_from_patch  # noqa: PLC0415

    active_verifier = (
        verifier_url if verifier_url is not None else _os.environ.get("VERIFIER_URL")
    )
    try:
        candidate = create_release_from_patch(
            session,
            connector_id=connector_id,
            base_release_id=release_id,
            patch=list(result.candidate_patch),
            provenance_repair_run_id=repair_run_id,
            verifier_url=active_verifier or None,
        )
        session.flush()
        candidate_release_id: str | None = candidate.id
        if candidate.verification_report_id:
            # REQ-030 item 3: the verifier verdict lives on the repair row
            # too (the candidate release carries the report id) — the
            # repair page reads verdicts from here, never recomputed.
            from autobridge.control.models import RepairRun  # noqa: PLC0415

            row = session.get(RepairRun, repair_run_id)
            if row is not None:
                row.verifier_report_id = candidate.verification_report_id
                session.flush()
        return candidate_release_id, None
    except Exception as exc:  # noqa: BLE001 — gate/patch failures are row data
        return None, f"{type(exc).__name__}: {exc}"[:500]


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
    opened = _detect_open(
        session,
        connector_id=connector_id,
        release_id=release_id,
        spec_dict=spec_dict,
        summary=summary,
        sync_run_id=sync_run_id,
    )
    if opened["status"] != "open":
        return opened["out"]
    drift = _persist_drift(
        session,
        connector_id=connector_id,
        classification=opened["classification"],
        sync_run_id=sync_run_id,
    )
    result, semantic_info = _repair_inline(
        session,
        spec_dict=spec_dict,
        signals=opened["signals"],
        classification=opened["classification"],
        delta=opened["delta"],
        drift_id=drift.id,
        summary=summary,
        sync_run_id=sync_run_id,
        verifier_url=verifier_url,
        knowledge_cache=knowledge_cache,
    )
    # REQ-053 item 6: judge a deterministic proposal on the break's
    # records before offering it — a failing patch never reaches Approve.
    result, dry_error = _gate_proposal_on_fault_records(
        session,
        spec_dict=spec_dict,
        summary=summary,
        result=result,
    )
    candidate_release_id, gate_error = _finalize_proposal(
        session,
        connector_id=connector_id,
        release_id=release_id,
        repair_run_id=result.repair_run_id,
        result=result,
        verifier_url=verifier_url,
    )
    if gate_error is None and dry_error is not None:
        gate_error = dry_error
    out: dict[str, Any] = {
        "drift_found": True,
        "outcome": opened["outcome"],
        "drift_id": drift.id,
        "drift_class": opened["classification"].drift_class,
        "drift_reason": opened["classification"].reason,
        "repair_run_id": result.repair_run_id,
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



def _newest_open_engine_drift(
    session: Any, connector_id: str, drift_class: str
) -> Any | None:
    """Newest open engine-derived drift of this class (repair or not)."""
    from autobridge.control.models import DriftEvent

    return (
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


def _pending_repair_job(session: Any, connector_id: str, drift_class: str) -> Any | None:
    """A queued/running repair job for (connector, drift class), if any.

    REQ-042: a deferred semantic repair leaves its drift without a row
    until the background job climbs. A second sync in between must dedupe
    onto the pending job — never open a second drift.
    """
    from autobridge.control.models import Job

    try:
        jobs = (
            session.execute(
                _select(Job)
                .where(
                    Job.kind == "repair",
                    Job.state.in_(("queued", "running")),
                )
                .order_by(Job.created_at.desc())
            )
            .scalars()
            .all()
        )
    except Exception:
        return None
    for job in jobs:
        payload = job.payload if isinstance(job.payload, dict) else {}
        if (
            payload.get("connector_id") == connector_id
            and payload.get("drift_class") == drift_class
        ):
            return job
    return None


def _detect_open(
    session: Any,
    *,
    connector_id: str,
    release_id: str,
    spec_dict: dict[str, Any],
    summary: dict[str, Any],
    sync_run_id: str,
) -> dict[str, Any]:
    """Shared detection core: signals → classify → dedupe-or-open inputs.

    Returns ``{"status": "no_drift" | "dedupe" | "pending" | "open", ...}``:

    * ``no_drift`` — ``out`` is the final detection dict (no rows beyond
      the verifying-sync retirement the caller already handles).
    * ``dedupe`` — ``out`` reuses the existing drift + repair rows.
    * ``pending`` — an open drift with a queued/running repair job but no
      repair row yet (REQ-042 deferred repair in flight); ``out`` names
      the drift and the job.
    * ``rolled_back`` — the sync was the verifying sync for an approval and
      still failed: automatic rollback ran (REQ-053 item 6); ``out`` names
      the rollback, and the original repair stays the one open item.
    * ``open`` — ``signals``, ``classification``, ``delta`` and ``outcome``
      for the caller to persist a drift and repair (inline or queued).
    """
    from autobridge.drift.classifier import classify
    from autobridge.drift.signals import signals_from_sync

    outcome = summary.get("outcome") if isinstance(summary, dict) else None
    # REQ-053 item 6: a still-failing verifying sync rolls back first —
    # the approved change did not fix the drift, so no new drift/repair
    # is opened for this sync; the original repair stays the open item.
    rolled = _maybe_rollback_verifying_sync(
        session,
        connector_id=connector_id,
        release_id=release_id,
        summary=summary if isinstance(summary, dict) else None,
    )
    if rolled is not None:
        return {
            "status": "rolled_back",
            "out": {
                "drift_found": False,
                "outcome": outcome,
                "rolled_back": rolled,
                "reason": rolled["reason"],
                "repair_run_id": rolled["repair_run_id"],
                "decision": "escalate",
                "rule_id": None,
                "ladder_level_reached": None,
                "model_usage": {},
                "candidate_release_id": None,
                "gate_error": None,
            },
        }
    if not _summary_has_responses(summary if isinstance(summary, dict) else {}):
        # No vendor response, no drift evidence: a Blocked connectivity
        # failure stays the sync's own outcome (recorded on the SyncRun),
        # never a DriftEvent, never a repair. Fail closed, loudly visible.
        return {
            "status": "no_drift",
            "out": {"drift_found": False, "outcome": outcome, "reason": "no_vendor_response"},
        }
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
        return {
            "status": "no_drift",
            "out": {"drift_found": False, "outcome": outcome, "drifts_resolved": resolved},
        }
    if all(s.kind == "zero_records" for s in signals):
        # An empty entity on a healthy sync is not drift (REQ-031): the
        # PABX edge connector idles at zero RoomStatus records between
        # postings bursts, and a quiet hotel day is not an incident.
        # Blocked/Degraded outcomes always add their own signals, so this
        # only quiets Complete syncs — the endpoint_removed pattern (zero
        # records + Blocked) still fires through capability_removed.
        resolved = _resolve_verified_drifts(session, connector_id, release_id, summary)
        return {
            "status": "no_drift",
            "out": {
                "drift_found": False,
                "outcome": outcome,
                "drifts_resolved": resolved,
                "reason": "empty_entities_on_healthy_sync",
            },
        }

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
            "status": "dedupe",
            "out": {
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
            },
        }
    pending_job = None
    open_alone = _newest_open_engine_drift(session, connector_id, classification.drift_class)
    if open_alone is not None:
        pending_job = _pending_repair_job(session, connector_id, classification.drift_class)
    if open_alone is not None and pending_job is not None:
        return {
            "status": "pending",
            "out": {
                "drift_found": True,
                "deduped": True,
                "repair_pending": True,
                "outcome": outcome,
                "drift_id": open_alone.id,
                "drift_class": classification.drift_class,
                "drift_reason": classification.reason,
                "repair_job_id": pending_job.id,
                "repair_run_id": None,
                "decision": None,
                "gate_error": None,
            },
        }
    return {
        "status": "open",
        "outcome": outcome,
        "signals": signals,
        "classification": classification,
        "delta": delta,
    }


def _maybe_rollback_verifying_sync(
    session: Any,
    *,
    connector_id: str,
    release_id: str,
    summary: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Automatic rollback when a verifying sync still fails (REQ-053 item 6).

    A sync that lands ``Degraded``/``Blocked`` *on a release created by an
    approved repair* is the verifying sync for that approval — the approved
    change did not fix the drift (e.g. the model proposed a wire field the
    vendor never sends). The engine rolls back to the pre-approval release
    at once, re-opens the original repair as the one open escalation (never
    a second repair for the same open drift), and reports the rollback so
    the caller skips drift/repair creation for this sync.

    The rollback is labelled on the repair row (bundle note, no proposal
    offered) and as a ``rollback`` approval (Decisions page). Fail-closed
    guards: no approval, no pre-approval release, or a sync that never
    reached the vendor (nothing judges the patch) all return ``None`` and
    detection proceeds normally.
    """
    from autobridge.control.models import Approval, ConnectorRelease, RepairRun  # noqa: PLC0415

    if not isinstance(summary, dict) or summary.get("outcome") == "Complete":
        return None
    if not _summary_has_responses(summary):
        return None
    release = session.get(ConnectorRelease, release_id)
    if release is None:
        return None
    provenance_id = getattr(release, "provenance_repair_run_id", None)
    if not provenance_id:
        return None
    latest = (
        session.execute(
            _select(Approval)
            .where(Approval.repair_run_id == provenance_id)
            .order_by(Approval.created_at.desc())
        )
        .scalars()
        .first()
    )
    if latest is None or latest.decision != "approve" or not latest.from_release_id:
        return None
    target = session.get(ConnectorRelease, latest.from_release_id)
    if target is None or target.connector_id != connector_id:
        return None
    from autobridge.engine.releases import rollback_release  # noqa: PLC0415

    outcome = str(summary.get("outcome"))
    unmet = summary.get("unmet_criteria")
    criteria = ", ".join(str(c) for c in unmet) if isinstance(unmet, list) and unmet else outcome
    try:
        new_release, approval = rollback_release(
            session,
            connector_id=connector_id,
            target_release_id=latest.from_release_id,
            actor="engine",
            repair_run_id=provenance_id,
            # The original escalation stays the one open item — never a
            # second escalation for the same open drift.
            reopen_as="escalate",
        )
        session.flush()
    except Exception:
        return None
    run = session.get(RepairRun, provenance_id)
    note = (
        f"verifying sync on r{getattr(release, 'version', '?')} {outcome} ({criteria}); "
        f"automatic rollback to r{getattr(target, 'version', '?')} — the question stands, no patch offered"
    )
    if run is not None:
        bundle = run.escalation_bundle if isinstance(run.escalation_bundle, dict) else {}
        question = bundle.get("question") if isinstance(bundle.get("question"), str) else None
        run.escalation_bundle = {
            **bundle,
            "reason": note,
            "question": question or "The approved fix failed its verifying sync — which change is correct?",
            "proposal": None,
            "verifying_sync_failed": {
                "release_version": getattr(release, "version", None),
                "outcome": outcome,
                "unmet_criteria": list(unmet) if isinstance(unmet, list) else [],
                "rolled_back_to_version": getattr(target, "version", None),
                "rollback_release_id": new_release.id,
                "rollback_approval_id": approval.id,
            },
        }
        run.candidate_patch = None
        session.flush()
    return {
        "rolled_back": True,
        "repair_run_id": provenance_id,
        "from_release_id": release_id,
        "to_release_id": new_release.id,
        "rollback_approval_id": approval.id,
        "outcome": outcome,
        "unmet_criteria": list(unmet) if isinstance(unmet, list) else [],
        "reason": note,
    }


def _gate_proposal_on_fault_records(
    session: Any,
    *,
    spec_dict: dict[str, Any],
    summary: dict[str, Any] | None,
    result: Any,
) -> tuple[Any, str | None]:
    """Dry-run a deterministic proposal on the faulted records (REQ-053 item 6).

    The semantic climb judges model proposals inside the ladder; the
    deterministic path proposes here, so the same judgement runs at the
    detect layer, on the break's sync summary (no new vendor call). A
    passing proposal is offered unchanged. A failing one is dropped and
    the repair becomes a question-only escalation — the persisted row is
    rewritten the same way, so the repair page never offers the bad patch.
    Returns ``(result, dry_run_error_or_None)``.
    """
    from dataclasses import replace as _replace  # noqa: PLC0415

    from autobridge.repair.escalation import escalation_for_no_fix  # noqa: PLC0415

    if result.decision != "propose" or not result.candidate_patch or not result.repair_run_id:
        return result, None
    from autobridge.control.models import RepairRun  # noqa: PLC0415
    from autobridge.repair.dryrun import (  # noqa: PLC0415
        build_dry_run_fault,
        dry_run_proposal,
        field_notes_for_proposal,  # noqa: PLC0415
    )

    try:
        fault = build_dry_run_fault(summary if isinstance(summary, dict) else None)
        ok, detail = dry_run_proposal(spec_dict, list(result.candidate_patch), fault)
        notes = field_notes_for_proposal(spec_dict, list(result.candidate_patch), fault)
    except Exception as exc:  # noqa: BLE001 — judgement failure is row data
        ok, detail, notes = False, f"dry-run errored: {exc}", []
    if ok:
        # Record the pass on the proposing rung: the repair page shows it
        # with the offered patch ("tested on the current records: ...").
        # Deep-copy first: the loaded JSON shares dict objects with the
        # attribute state, so an in-place edit would compare equal and
        # never mark the row dirty.
        import copy as _copy  # noqa: PLC0415

        row = session.get(RepairRun, result.repair_run_id)
        if row is not None:
            trace = _copy.deepcopy(row.ladder_trace or [])
            for entry in reversed(trace):
                if isinstance(entry, dict) and entry.get("level") == result.ladder_level_reached:
                    entry["dry_run"] = "tested on the current records: the checks pass"
                    # REQ-055 item 2: what each changed target field means.
                    if notes:
                        entry["field_notes"] = [dict(n) for n in notes if isinstance(n, dict)]
                    break
            row.ladder_trace = trace
            session.flush()
        return result, None
    reason = f"proposal failed dry-run on the current records: {detail} — no patch offered"
    bundle = escalation_for_no_fix(
        reason,
        evidence_ids=list(result.evidence_ids or []),
        question="The proposed fix failed its dry-run on the current records — which change is correct?",
    )
    row = session.get(RepairRun, result.repair_run_id)
    if row is not None:
        row.decision = "escalate"
        row.candidate_patch = None
        row.escalation_reason = reason
        row.escalation_bundle = bundle.to_dict()
        session.flush()
    return (
        _replace(
            result,
            decision="escalate",
            candidate_patch=None,
            rule_id=None,
            escalation_reason=reason,
            escalation_bundle=bundle,
        ),
        detail,
    )


def _persist_drift(
    session: Any,
    *,
    connector_id: str,
    classification: Any,
    sync_run_id: str,
) -> Any:
    """Insert the open DriftEvent for a fresh detection; returns the row."""
    from autobridge.control.models import DriftEvent

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
    return drift


def _repair_inline(
    session: Any,
    *,
    spec_dict: dict[str, Any],
    signals: list[Any],
    classification: Any,
    delta: Any | None,
    drift_id: str,
    summary: dict[str, Any],
    sync_run_id: str,
    verifier_url: str | None,
    knowledge_cache: Any | None,
    ) -> tuple[Any, dict[str, Any]]:
    """Run the ladder inline (no deferral): deterministic rungs and any
    provider the climb resolves locally (replay/off). Callers that may
    hold a request open use :func:`detect_after_sync` instead, which
    queues the live-model climb as a background job."""
    import os as _os  # noqa: PLC0415

    from autobridge.repair.controller import RepairController  # noqa: PLC0415

    controller = RepairController(knowledge_cache=knowledge_cache) if knowledge_cache is not None else RepairController()
    semantic_info: dict[str, Any] = {}
    if classification.drift_class == "SEMANTIC_VALUE_DRIFT":
        # REQ-039: the live repair path runs the same semantic climb as
        # the instrumented one — engine enumeration from the wire signals
        # and the spec, wire filter, L2 when >= 2, L3 through the frontier
        # provider and budget when < 2, verifier judge on the proposal,
        # escalation question. Never the deterministic 0-call escalation.
        from autobridge.repair.controller import run_semantic_repair  # noqa: PLC0415

        active_verifier = verifier_url if verifier_url is not None else _os.environ.get("VERIFIER_URL")
        result, semantic_info = run_semantic_repair(
            spec=spec_dict,
            drift_class=classification.drift_class,
            signals=list(signals),
            fingerprint_delta=delta,
            summary=summary if isinstance(summary, dict) else None,
            sync_run_id=sync_run_id,
            vendor=None,
            drift_event_ids=[drift_id],
            session=session,
            knowledge_cache=knowledge_cache,
            verifier_url=active_verifier or None,
        )
    else:
        result = controller.handle_from_signals(
            spec=spec_dict,
            signals=list(signals),
            fingerprint_delta=delta,
            drift_event_ids=[drift_id],
            session=session,
        )
    return result, semantic_info


def detect_after_sync(
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
    """Detect drift and arrange its repair without ever calling a model.

    REQ-042: ``POST /sync`` and the scheduler run this (via
    :func:`autobridge.engine.sync_run.run_sync_handler`). It persists the
    ``DriftEvent`` and:

    * repairs deterministic drifts inline (L0/L1, zero model calls) and
      persists the ``RepairRun`` + candidate release exactly like
      :func:`detect_and_repair_after_sync`;
    * queues a semantic drift that would need a live frontier call as a
      background ``repair`` job and returns at once (``repair_pending``).
      The job climbs the same semantic climb with the same inputs and
      finalizes the ``RepairRun``; the UI and ``make demo`` poll the
      repair row. Replay/offline semantic drifts run inline (local reads).

    No DB transaction is held across a model call: the sync commits when
    its job finishes, and the repair job commits before and after each
    call (see :class:`autobridge.llm.provider.BudgetedProvider`).
    """
    opened = _detect_open(
        session,
        connector_id=connector_id,
        release_id=release_id,
        spec_dict=spec_dict,
        summary=summary,
        sync_run_id=sync_run_id,
    )
    if opened["status"] != "open":
        return opened["out"]
    signals = opened["signals"]
    classification = opened["classification"]
    delta = opened["delta"]
    drift = _persist_drift(
        session,
        connector_id=connector_id,
        classification=classification,
        sync_run_id=sync_run_id,
    )
    # The sync returns with the drift durable — and, crucially, without
    # holding a write transaction open: the repair-job submit below runs
    # on its own session, and the background climb must never wait on a
    # lock this request holds (REQ-042).
    session.commit()
    if classification.drift_class == "SEMANTIC_VALUE_DRIFT" and _live_frontier_wanted():
        # The climb would call the frontier: derive its inputs now (fast,
        # local) and queue the climb. The request returns as soon as this
        # job row commits — well under 2 s — while the repair runs in the
        # background drain.
        import json as _json  # noqa: PLC0415

        from autobridge.repair.controller import prepare_semantic_inputs  # noqa: PLC0415
        from autobridge.repair.dryrun import build_dry_run_fault  # noqa: PLC0415

        prepared = prepare_semantic_inputs(
            spec=spec_dict,
            signals=list(signals),
            summary=summary if isinstance(summary, dict) else None,
            sync_run_id=sync_run_id,
        )
        try:
            dry_fault = build_dry_run_fault(summary if isinstance(summary, dict) else None)
        except Exception:
            dry_fault = None
        job_id = _submit_deferred_semantic_repair(
            session,
            drift_id=drift.id,
            connector_id=connector_id,
            release_id=release_id,
            prepared=prepared,
            signals=list(signals),
            delta=_json.loads(_json.dumps(delta, sort_keys=True, default=str))
            if delta is not None
            else None,
            dry_run_fault=dry_fault,
        )
        return {
            "drift_found": True,
            "repair_pending": True,
            "outcome": opened["outcome"],
            "drift_id": drift.id,
            "drift_class": classification.drift_class,
            "drift_reason": classification.reason,
            "repair_job_id": job_id,
            "repair_run_id": None,
            "decision": None,
            "rule_id": None,
            "ladder_level_reached": None,
            "model_usage": {},
            "candidate_release_id": None,
            "gate_error": None,
            "provider_mode": "live",
            "l3_status": f"queued: live climb runs in repair job {job_id}",
        }
    result, semantic_info = _repair_inline(
        session,
        spec_dict=spec_dict,
        signals=signals,
        classification=classification,
        delta=delta,
        drift_id=drift.id,
        summary=summary,
        sync_run_id=sync_run_id,
        verifier_url=verifier_url,
        knowledge_cache=knowledge_cache,
    )
    # REQ-053 item 6: judge a deterministic proposal on the break's
    # records before offering it (same gate as the bench path above).
    result, dry_error = _gate_proposal_on_fault_records(
        session,
        spec_dict=spec_dict,
        summary=summary,
        result=result,
    )
    candidate_release_id, gate_error = _finalize_proposal(
        session,
        connector_id=connector_id,
        release_id=release_id,
        repair_run_id=result.repair_run_id,
        result=result,
        verifier_url=verifier_url,
    )
    if gate_error is None and dry_error is not None:
        gate_error = dry_error
    out: dict[str, Any] = {
        "drift_found": True,
        "outcome": opened["outcome"],
        "drift_id": drift.id,
        "drift_class": classification.drift_class,
        "drift_reason": classification.reason,
        "repair_run_id": result.repair_run_id,
        "decision": result.decision,
        "rule_id": result.rule_id,
        "ladder_level_reached": result.ladder_level_reached,
        "model_usage": dict(result.model_usage),
        "candidate_release_id": candidate_release_id,
        "gate_error": gate_error,
    }
    if semantic_info:
        out["provider_mode"] = semantic_info.get("provider_mode")
        out["l3_status"] = semantic_info.get("l3_status")
        out["verdict"] = semantic_info.get("verdict")
    return out
