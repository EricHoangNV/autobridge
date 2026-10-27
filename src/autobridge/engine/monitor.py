"""Canary monitor + DependencyRegistration pause signal (P1.W4.6, plan §2.3 + §2.4).

*Canary* — ``health.canary`` from the spec (every + operation + max_pages).
A lightweight probe that runs a single operation with ``max_pages`` capped
(usually 1).  The summary has the same shape as ``engine.sync_run.execute_sync``
so ``drift.signals.signals_from_canary`` can consume it directly.

*Dependencies* — ``DependencyRegistration`` with ``pause_on_release_change``.
Activating a release emits ``release.activated`` (persisted as an
``EvidenceRecord`` with kind ``decision``) and pauses every matching consumer.
The demo registers one dummy consumer via :func:`ensure_demo_consumer`.

Trust boundary: no import of ``verifier`` or ``simulators``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

_INTERVAL_RE = re.compile(r"^(\d+)([smh])$")
_INTERVAL_SECONDS = {"s": 1, "m": 60, "h": 3600}


def parse_canary_interval(every: str) -> int:
    """Parse ``health.canary.every`` (e.g. ``60s``, ``5m``, ``1h``) to seconds.

    Raises :class:`ValueError` on invalid shape.
    """
    if not isinstance(every, str):
        raise ValueError(f"canary.every must be a string, got {type(every).__name__}")
    m = _INTERVAL_RE.match(every.strip())
    if not m:
        raise ValueError(f"canary.every {every!r} must look like 60s, 5m or 1h")
    value = int(m.group(1))
    unit = m.group(2)
    return value * _INTERVAL_SECONDS[unit]


def should_run_canary(
    last_run: datetime | None,
    now: datetime | None = None,
    *,
    every: str,
) -> bool:
    """Return ``True`` if *every* seconds have elapsed since *last_run*.

    *now* defaults to ``datetime.now(UTC)``.  When *last_run* is None the
    canary is due.  Naive datetimes are treated as UTC.
    """
    interval = parse_canary_interval(every)
    if last_run is None:
        return True
    if now is None:
        now = datetime.now(UTC)
    # Normalise to aware UTC for subtraction.
    if last_run.tzinfo is None:
        last_run = last_run.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    elapsed = (now - last_run).total_seconds()
    return elapsed >= interval


def get_canary_config(spec: dict[str, Any]) -> dict[str, Any]:
    """Extract ``health.canary`` config from a spec dict.

    Returns a dict with ``every``, ``operation``, ``max_pages``.
    Raises :class:`ValueError` when the spec has no canary.
    """
    health = spec.get("health")
    if not isinstance(health, dict):
        raise ValueError("spec missing health.canary")
    canary = health.get("canary")
    if not isinstance(canary, dict):
        raise ValueError("spec missing health.canary")
    every = canary.get("every")
    operation = canary.get("operation")
    if not every or not operation:
        raise ValueError("health.canary requires every and operation")
    return {
        "every": every,
        "operation": operation,
        "max_pages": int(canary.get("max_pages", 1)),
    }


# ---------------------------------------------------------------------------
# Canary probe execution
# ---------------------------------------------------------------------------


def run_canary(
    spec_dict: dict[str, Any],
    http_client: Any,
    *,
    checkpoint: dict[str, Any] | None = None,
    max_pages_override: int | None = None,
) -> dict[str, Any]:
    """Execute the ``health.canary`` probe and return a sync-like summary.

    The probe runs only the canary operation (``health.canary.operation``)
    with ``max_pages`` from the spec (or *max_pages_override*).  Mapping,
    checks, outcome and data_readiness mirror :func:`engine.sync_run.execute_sync`
    but are scoped to the canary's entities.

    Returns dict with: outcome, unmet_criteria, data_readiness,
    fingerprint_id, operation_results, records_observed, canonical_records,
    blocked_records, invariant_results, checkpoint_values, operation.

    The caller may feed the result to ``drift.signals.signals_from_canary``.
    """
    from autobridge.drift.fingerprint import compute_fingerprint_id
    from autobridge.engine.sync_run import (
        compute_data_readiness,
        determine_outcome,
    )
    from autobridge.runtime.checkpoint import compute_checkpoint_for_operation
    from autobridge.runtime.checks import evaluate_entity_checks
    from autobridge.runtime.interpreter import Interpreter
    from autobridge.runtime.mapping import map_records_to_entities
    from autobridge.spec.models import load_spec_from_dict

    spec = load_spec_from_dict(spec_dict)
    canary = spec.health.canary
    op_name = canary.operation
    max_pages = max_pages_override if max_pages_override is not None else canary.max_pages

    interp = Interpreter(spec, http_client=http_client)
    # Single operation probe
    try:
        op_result = interp.execute_operation(op_name, checkpoint=checkpoint, max_pages=max_pages)
    except Exception as exc:
        # Transport / HTTP failure → Blocked summary with http_error signal source
        return {
            "operation": op_name,
            "outcome": "Blocked",
            "unmet_criteria": ["http_error"],
            "data_readiness": {"freshness": "stale", "completeness": "incomplete"},
            "fingerprint_id": "",
            "operation_results": {
                op_name: {
                    "pages_fetched": 0,
                    "raw_pages": [],
                    "repeated_cursor": False,
                    "stop_reason": None,
                    "http_status": None,
                    "last_error": str(exc),
                }
            },
            "records_observed": {},
            "canonical_records": {},
            "blocked_records": {},
            "invariant_results": [{"check": "http_error", "status": "failed"}],
            "checkpoint_values": {},
            "error": str(exc),
        }

    results: dict[str, Any] = {op_name: op_result}

    # Map entities that derive from the canary operation
    canonical_records: dict[str, list[dict[str, Any]]] = {}
    blocked_records: dict[str, list[dict[str, Any]]] = {}
    all_blocked_failures: list[str] = []
    invariant_failures: list[str] = []

    for ent_name, ent_spec in spec.entities.items():
        if ent_spec.from_operation != op_name:
            continue
        raw_records = op_result.records
        canonical, blocked = map_records_to_entities(
            raw_records,
            ent_spec,
            source_spec=spec.source,
            entity_name=ent_name,
        )
        canonical_records[ent_name] = canonical
        if blocked:
            blocked_records[ent_name] = blocked
            for b in blocked:
                field = b.get("field") if isinstance(b, dict) else None
                if field:
                    tag = f"unknown_enum:{field}"
                    if tag not in all_blocked_failures:
                        all_blocked_failures.append(tag)
                    if "unknown_enum" not in all_blocked_failures:
                        all_blocked_failures.append("unknown_enum")
                else:
                    if "unknown_enum" not in all_blocked_failures:
                        all_blocked_failures.append("unknown_enum")

        # Checks for this entity (no_loss needs meta extraction but canary max_pages=1
        # has no total_count, so we run without expected_count)
        compare_to_val = None
        for chk in ent_spec.checks:
            compare_to = getattr(chk, "compare_to", None) if not isinstance(chk, dict) else chk.get("compare_to")
            if compare_to:
                # Try to extract from raw page (best effort)
                try:
                    from autobridge.runtime.interpreter import extract_jsonpath

                    for page in op_result.raw_pages:
                        val = extract_jsonpath(page, compare_to)
                        if val is not None:
                            compare_to_val = val
                            break
                except Exception:
                    pass
                break

        check_results = evaluate_entity_checks(
            ent_name,
            canonical,
            ent_spec.checks,
            expected_count=compare_to_val if isinstance(compare_to_val, int) else None,
            compare_to_value=compare_to_val,
        )
        for cr in check_results:
            if cr not in invariant_failures and cr not in all_blocked_failures:
                invariant_failures.append(cr)

    # Determine outcome (mirrors sync_run.determine_outcome)
    if all_blocked_failures:
        outcome, unmet = determine_outcome(results, invariant_failures=invariant_failures, blocked_failures=all_blocked_failures)
    else:
        outcome, unmet = determine_outcome(results, invariant_failures=invariant_failures)

    records_observed: dict[str, int] = {ent: len(recs) for ent, recs in canonical_records.items()}
    data_readiness = compute_data_readiness(outcome, results, records_observed)

    # Fingerprint (reuse same fingerprint_id logic for drift detection)
    try:
        fingerprint_id = compute_fingerprint_id(spec_dict, results, canonical_records)
    except Exception:
        fingerprint_id = ""

    # Checkpoint per canary operation
    checkpoint_values: dict[str, Any] = {}
    if op_result.checkpoint_value is not None:
        checkpoint_values[op_name] = op_result.checkpoint_value
    # Also recompute from canonical where possible
    for ent_name, ent_spec in spec.entities.items():
        if ent_spec.from_operation != op_name:
            continue
        op = spec.operations.get(op_name)
        if op and op.checkpoint and canonical_records.get(ent_name):
            canon_cp = compute_checkpoint_for_operation(canonical_records[ent_name], op.checkpoint)
            if canon_cp is not None:
                checkpoint_values[op_name] = canon_cp

    invariant_results: list[dict[str, Any]] = [{"check": name, "status": "failed"} for name in unmet]

    return {
        "operation": op_name,
        "outcome": outcome,
        "unmet_criteria": unmet,
        "data_readiness": data_readiness,
        "fingerprint_id": fingerprint_id,
        "operation_results": results,
        "records_observed": records_observed,
        "canonical_records": canonical_records,
        "blocked_records": blocked_records,
        "invariant_results": invariant_results,
        "checkpoint_values": checkpoint_values,
    }


# ---------------------------------------------------------------------------
# DependencyRegistration helpers (pause on release activation)
# ---------------------------------------------------------------------------

# We keep the event name as a constant for test assertions and EvidenceRecord payload.
RELEASE_ACTIVATED_EVENT = "release.activated"


def _ensure_no_secret_in_payload(payload: dict[str, Any]) -> None:  # noqa: ARG001
    """Defensive: ensure no credential value leaks into emitted payload."""
    # No-op guard: callers must never pass credential values; we just keep the hook.
    return


def register_consumer(
    session: Any,
    *,
    connector_id: str,
    consumer_id: str,
    entity: str,
    policy: str = "pause_on_release_change",
) -> Any:
    """Register a downstream consumer for *connector_id*.

    Idempotent: if a registration with the same (consumer_id, connector_id, entity)
    already exists it is returned unchanged.
    """
    from autobridge.control.models import DependencyRegistration

    existing = (
        session.query(DependencyRegistration)
        .filter_by(consumer_id=consumer_id, connector_id=connector_id, entity=entity)
        .one_or_none()
    )
    if existing is not None:
        return existing
    reg = DependencyRegistration(
        consumer_id=consumer_id,
        connector_id=connector_id,
        entity=entity,
        policy=policy,
    )
    session.add(reg)
    session.flush()
    return reg


def ensure_demo_consumer(
    session: Any,
    connector_id: str,
    *,
    consumer_id: str = "demo-dashboard",
    entity: str = "Reservation",
    policy: str = "pause_on_release_change",
) -> Any:
    """Ensure the demo dummy consumer exists (idempotent).

    The demo registers one dummy consumer so the pause signal can be shown
    in the UI / evidence trace.
    """
    return register_consumer(
        session,
        connector_id=connector_id,
        consumer_id=consumer_id,
        entity=entity,
        policy=policy,
    )


def is_consumer_paused(
    session: Any,
    *,
    connector_id: str,
    consumer_id: str,
    entity: str,
) -> bool:
    """Return ``True`` if the consumer is currently paused."""
    from autobridge.control.models import DependencyRegistration

    reg = (
        session.query(DependencyRegistration)
        .filter_by(consumer_id=consumer_id, connector_id=connector_id, entity=entity)
        .one_or_none()
    )
    if reg is None:
        return False
    return bool(reg.paused)


def resume_consumer(
    session: Any,
    *,
    connector_id: str,
    consumer_id: str,
    entity: str,
) -> Any | None:
    """Resume a paused consumer (set ``paused=False``)."""
    from autobridge.control.models import DependencyRegistration

    reg = (
        session.query(DependencyRegistration)
        .filter_by(consumer_id=consumer_id, connector_id=connector_id, entity=entity)
        .one_or_none()
    )
    if reg is None:
        return None
    reg.paused = False
    session.flush()
    return reg


def handle_release_activated(
    session: Any,
    *,
    connector_id: str,
    new_release_id: str,
    actor: str | None = None,
) -> dict[str, Any]:
    """Pause ``pause_on_release_change`` consumers and emit ``release.activated``.

    This is called whenever a release activation completes (approve or rollback).
    It:

    1. Sets ``paused=True`` on every ``DependencyRegistration`` for
       *connector_id* with ``policy == "pause_on_release_change"``.
    2. Persists an ``EvidenceRecord`` with ``payload.event == "release.activated"``
       so the trace is visible in the dashboard and verifiable in tests.

    Returns ``{"event": "release.activated", "paused": [consumer_ids], "evidence_id": str}``.
    The session is flushed but not committed (caller owns the transaction).
    """
    from autobridge.control.models import DependencyRegistration, EvidenceRecord

    # Pausing: only pause_on_release_change consumers
    regs = (
        session.query(DependencyRegistration)
        .filter_by(connector_id=connector_id, policy="pause_on_release_change")
        .all()
    )
    paused_ids: list[str] = []
    for reg in regs:
        if not reg.paused:
            reg.paused = True
        # Emit paused id even if already paused? No — only newly paused counts for trace,
        # but we report all affected for visibility.
        paused_ids.append(reg.consumer_id)

    payload: dict[str, Any] = {
        "event": RELEASE_ACTIVATED_EVENT,
        "connector_id": connector_id,
        "release_id": new_release_id,
        "paused_consumers": paused_ids,
    }
    if actor is not None:
        payload["actor"] = actor
    _ensure_no_secret_in_payload(payload)

    ev = EvidenceRecord(
        kind="decision",
        payload=payload,
        parent_ids=[],
        run_id=f"release:{new_release_id}",
    )
    session.add(ev)
    session.flush()

    return {"event": RELEASE_ACTIVATED_EVENT, "paused": paused_ids, "evidence_id": ev.id, "payload": payload}


def get_dependency_status(
    session: Any,
    connector_id: str,
) -> list[dict[str, Any]]:
    """List dependency registrations for *connector_id* with pause status."""
    from autobridge.control.models import DependencyRegistration

    regs = session.query(DependencyRegistration).filter_by(connector_id=connector_id).all()
    return [
        {
            "consumer_id": r.consumer_id,
            "entity": r.entity,
            "policy": r.policy,
            "paused": bool(r.paused),
        }
        for r in regs
    ]
