"""Drift signals from sync runs and the canary (P1.W4.1, plan §2.5).

Signals are the raw observations that the drift classifier (P1.W4.2) will
turn into one of the 12 drift classes.  Each signal carries provenance
(the sync or canary run id, operation name and evidence ids) so later
stages can build an evidence chain.

Signals are collected from:

* **sync runs** — outcome (Complete | Degraded | Blocked), unmet_criteria,
  invariant failures, unknown-enum blocks, HTTP errors, pagination
  stop reasons, repeated_cursor flag, fingerprint drift, zero records,
  checkpoint staleness.
* **canary** — the ``health.canary`` lightweight probe (single operation,
  max_pages=1).  Its signals include http_error, pagination regression,
  fingerprint drift vs last successful sync, and unmet_criteria.

This module is deliberately deterministic: no LLM calls, no hidden
state.  The classifier decides the drift class; signals just name what
was observed.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import Any, Literal

from autobridge.drift.fingerprint import Fingerprint, FingerprintDelta, diff_fingerprints

SignalKind = Literal[
    "outcome_blocked",
    "outcome_degraded",
    "unknown_enum",
    "no_loss",
    "repeated_cursor",
    "pagination_changed",
    "keys_changed",
    "types_changed",
    "fingerprint_changed",
    "zero_records",
    "checkpoint_stale",
    "http_error",
    "auth_error",
    "rate_limited",
    "transient_error",
    "capability_removed",
    "invariant_failure",
    "canary_failed",
    "canary_pagination_regression",
]

Severity = Literal["low", "medium", "high", "critical"]

# HTTP status → signal kind mapping used when operation results carry
# http_errors (added by http client or interpreter on failure).
_HTTP_STATUS_KIND: dict[int, SignalKind] = {
    400: "http_error",
    401: "auth_error",
    402: "http_error",
    403: "auth_error",
    404: "capability_removed",
    405: "http_error",
    409: "http_error",
    429: "rate_limited",
    500: "transient_error",
    502: "transient_error",
    503: "transient_error",
    504: "transient_error",
}


@dataclass(frozen=True, slots=True)
class DriftSignal:
    """One observable drift indicator with provenance."""

    kind: str
    operation: str | None = None
    entity: str | None = None
    severity: Severity = "medium"
    message: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    source_run_id: str | None = None
    fingerprint_delta: FingerprintDelta | None = None


def _severity_for_kind(kind: str) -> Severity:
    if kind in {"outcome_blocked", "unknown_enum", "capability_removed", "auth_error"}:
        return "critical"
    if kind in {"outcome_degraded", "no_loss", "repeated_cursor", "pagination_changed"}:
        return "high"
    if kind in {"keys_changed", "types_changed", "fingerprint_changed"}:
        return "medium"
    if kind in {"zero_records", "checkpoint_stale", "invariant_failure"}:
        return "high"
    if kind == "rate_limited":
        return "medium"
    if kind == "transient_error":
        return "low"
    return "medium"


def _extract_http_signals(
    operation_results: dict[str, Any],
    source_run_id: str | None = None,
) -> list[DriftSignal]:
    """Scan operation results for http_error/status signals."""
    signals: list[DriftSignal] = []
    if not isinstance(operation_results, dict):
        return signals
    for op_name, res in operation_results.items():
        # OperationResult may carry http_status or http_errors list or last_error
        http_status: int | None = None
        http_errors: list[Any] = []
        if res is not None:
            http_status = getattr(res, "http_status", None)
            if http_status is None and isinstance(res, dict):
                http_status = res.get("http_status")
            # Some HttpClient implementations attach last_error string containing status
            last_error = getattr(res, "last_error", None)
            if last_error is None and isinstance(res, dict):
                last_error = res.get("last_error")
            if isinstance(last_error, str):
                # Try to extract status code from error message like "429 ..." or "HTTP 429"
                import re

                m = re.search(r"\b(4\d{2}|5\d{2})\b", last_error)
                if m:
                    with contextlib.suppress(ValueError):
                        http_status = int(m.group(1))
            http_errors_raw = getattr(res, "http_errors", None)
            if http_errors_raw is None and isinstance(res, dict):
                http_errors_raw = res.get("http_errors")
            if isinstance(http_errors_raw, list):
                http_errors = http_errors_raw
            elif isinstance(http_status, int):
                http_errors = [http_status]
            # Also check raw_pages for status hints
            raw_pages = getattr(res, "raw_pages", None)
            if raw_pages is None and isinstance(res, dict):
                raw_pages = res.get("raw_pages")
            if isinstance(raw_pages, list):
                for page in raw_pages:
                    if isinstance(page, dict) and "http_status" in page:
                        try:
                            s = int(page["http_status"])
                            if s not in http_errors:
                                http_errors.append(s)
                        except (TypeError, ValueError):
                            pass

        for status in http_errors:
            try:
                code = int(status) if not isinstance(status, int) else status
            except (TypeError, ValueError):
                continue
            kind: SignalKind = _HTTP_STATUS_KIND.get(code, "http_error")
            sev = _severity_for_kind(kind)
            signals.append(
                DriftSignal(
                    kind=kind,
                    operation=op_name,
                    severity=sev,
                    message=f"HTTP {code} on {op_name}",
                    details={"http_status": code},
                    source_run_id=source_run_id,
                )
            )
        # Also handle explicit http_status when no error list but non-2xx
        # (avoid double emission when already handled)
        if http_status is not None and http_status not in http_errors and http_status >= 400:
            kind = _HTTP_STATUS_KIND.get(http_status, "http_error")
            signals.append(
                DriftSignal(
                    kind=kind,
                    operation=op_name,
                    severity=_severity_for_kind(kind),
                    message=f"HTTP {http_status} on {op_name}",
                    details={"http_status": http_status},
                    source_run_id=source_run_id,
                )
            )
    return signals


def signals_from_sync(
    sync_summary: dict[str, Any],
    *,
    spec: dict[str, Any] | None = None,  # noqa: ARG001 - reserved for pagination expectation enrichment
    previous_fingerprint: Fingerprint | dict[str, Any] | str | None = None,
    current_fingerprint: Fingerprint | dict[str, Any] | None = None,
    source_run_id: str | None = None,
) -> list[DriftSignal]:
    """Extract drift signals from a sync run summary.

    *sync_summary* is the dict returned by ``engine.sync_run.execute_sync``:
    outcome, unmet_criteria, data_readiness, fingerprint_id,
    operation_results, records_observed, canonical_records,
    blocked_records, invariant_results.

    *spec* if provided is used to enrich pagination signals.
    *previous_fingerprint* and *current_fingerprint* enable fingerprint-drift
    signals (when both supplied, a :class:`FingerprintDelta` is computed).
    *source_run_id* is stored as provenance.
    """
    signals: list[DriftSignal] = []
    if not isinstance(sync_summary, dict):
        return signals

    outcome = sync_summary.get("outcome")
    unmet_criteria: list[str] = sync_summary.get("unmet_criteria") or []
    if not isinstance(unmet_criteria, list):
        unmet_criteria = [str(unmet_criteria)]

    records_observed: dict[str, int] = sync_summary.get("records_observed") or {}
    operation_results: dict[str, Any] = sync_summary.get("operation_results") or {}
    blocked_records: dict[str, Any] = sync_summary.get("blocked_records") or {}
    invariant_results: list[dict[str, Any]] = sync_summary.get("invariant_results") or []

    # Outcome signals (Complete → none, Degraded / Blocked → explicit)
    if outcome == "Blocked":
        signals.append(
            DriftSignal(
                kind="outcome_blocked",
                severity="critical",
                message=f"Sync Blocked: {', '.join(unmet_criteria) or 'unknown'}",
                details={"unmet_criteria": list(unmet_criteria), "outcome": outcome},
                source_run_id=source_run_id,
            )
        )
    elif outcome == "Degraded":
        signals.append(
            DriftSignal(
                kind="outcome_degraded",
                severity="high",
                message=f"Sync Degraded: {', '.join(unmet_criteria) or 'degraded'}",
                details={"unmet_criteria": list(unmet_criteria), "outcome": outcome},
                source_run_id=source_run_id,
            )
        )

    # Unmet criteria → specific signals (e.g. unknown_enum:field, no_loss)
    for crit in unmet_criteria:
        if not isinstance(crit, str):
            crit = str(crit)
        if crit.startswith("unknown_enum"):
            # e.g. "unknown_enum:status" or "unknown_enum"
            field = crit.split(":", 1)[1] if ":" in crit else None
            signals.append(
                DriftSignal(
                    kind="unknown_enum",
                    entity=field,
                    severity="critical",
                    message=f"Unknown enum value blocked on {field or 'unknown field'}",
                    details={"unmet_criteria": crit},
                    source_run_id=source_run_id,
                )
            )
        elif crit == "no_loss":
            signals.append(
                DriftSignal(
                    kind="no_loss",
                    severity="high",
                    message="Invariant no_loss failed (record count mismatch)",
                    details={"unmet_criteria": crit},
                    source_run_id=source_run_id,
                )
            )
        elif crit in {"unique_key", "date_order", "transform_error"}:
            signals.append(
                DriftSignal(
                    kind="invariant_failure",
                    severity="high",
                    message=f"Invariant {crit} failed",
                    details={"unmet_criteria": crit},
                    source_run_id=source_run_id,
                )
            )
        elif crit:
            # Generic invariant
            signals.append(
                DriftSignal(
                    kind="invariant_failure",
                    severity="high",
                    message=f"Invariant {crit} failed",
                    details={"unmet_criteria": crit},
                    source_run_id=source_run_id,
                )
            )

    # Blocked records → unknown_enum signal if not already covered
    if blocked_records:
        for ent, blocked in blocked_records.items():
            if not isinstance(blocked, list):
                continue
            for b in blocked:
                if not isinstance(b, dict):
                    continue
                field = b.get("field")
                # Avoid duplicating if unmet_criteria already named it, but still emit
                # per-entity signal for classifier richness.
                already = any(
                    s.kind == "unknown_enum" and s.entity == field for s in signals
                )
                if not already:
                    signals.append(
                        DriftSignal(
                            kind="unknown_enum",
                            entity=field,
                            operation=None,
                            severity="critical",
                            message=f"Blocked record in {ent} field {field}",
                            details={"entity": ent, "field": field, "blocked": b},
                            source_run_id=source_run_id,
                        )
                    )

    # Invariant results (failed checks) → invariant_failure if not already via unmet
    for inv in invariant_results:
        if not isinstance(inv, dict):
            continue
        check = inv.get("check")
        status = inv.get("status")
        if status == "failed" and check:
            # Check if we already have a signal for this check name
            already = any(
                s.details.get("unmet_criteria") == check or s.details.get("check") == check
                for s in signals
            )
            if not already:
                signals.append(
                    DriftSignal(
                        kind="invariant_failure",
                        severity="high",
                        message=f"Invariant {check} failed",
                        details={"check": check, "status": status},
                        source_run_id=source_run_id,
                    )
                )

    # Pagination signals: repeated_cursor and stop_reason analysis
    if isinstance(operation_results, dict):
        for op_name, res in operation_results.items():
            # repeated_cursor
            repeated = getattr(res, "repeated_cursor", None)
            if repeated is None and isinstance(res, dict):
                repeated = res.get("repeated_cursor")
            if repeated:
                signals.append(
                    DriftSignal(
                        kind="repeated_cursor",
                        operation=op_name,
                        severity="high",
                        message=f"Repeated cursor on {op_name} (pagination bug)",
                        details={"operation": op_name},
                        source_run_id=source_run_id,
                    )
                )
            # stop_reason signals (use spec to detect pagination_changed vs expected)
            stop_reason = getattr(res, "stop_reason", None)
            if stop_reason is None and isinstance(res, dict):
                stop_reason = res.get("stop_reason")
            # pages_fetched change signal handled via fingerprint delta below
            # But we surface stop_reason=repeated_cursor as repeated_cursor (above)
            # and stop_reason anomalies for observability
            if stop_reason in {"empty_page_implicit", "no_next_token"}:
                # Implicit stop → likely drift from expected pagination
                signals.append(
                    DriftSignal(
                        kind="pagination_changed",
                        operation=op_name,
                        severity="medium",
                        message=f"Implicit pagination stop {stop_reason} on {op_name}",
                        details={"stop_reason": stop_reason, "operation": op_name},
                        source_run_id=source_run_id,
                    )
                )

    # Zero records signals
    if isinstance(records_observed, dict):
        for ent, cnt in records_observed.items():
            try:
                c = int(cnt)
            except (TypeError, ValueError):
                continue
            if c == 0:
                # Only flag when outcome was not Complete with zero expected?
                # Always flag as signal; classifier will decide if it's capability removed.
                signals.append(
                    DriftSignal(
                        kind="zero_records",
                        entity=ent,
                        severity="high",
                        message=f"Zero records observed for {ent}",
                        details={"entity": ent, "records_observed": records_observed},
                        source_run_id=source_run_id,
                    )
                )
                # Zero records + Blocked often means endpoint removed
                if outcome == "Blocked":
                    signals.append(
                        DriftSignal(
                            kind="capability_removed",
                            entity=ent,
                            severity="critical",
                            message=f"Capability removed? {ent} returned zero with Blocked",
                            details={"entity": ent},
                            source_run_id=source_run_id,
                        )
                    )

    # HTTP error signals from operation results
    signals.extend(_extract_http_signals(operation_results, source_run_id=source_run_id))

    # Fingerprint delta signals (when caller supplies both fingerprints)
    if previous_fingerprint is not None and current_fingerprint is not None:
        delta = diff_fingerprints(previous_fingerprint, current_fingerprint)
        if delta.pagination_changed:
            signals.append(
                DriftSignal(
                    kind="pagination_changed",
                    severity="high",
                    message="Pagination shape drift detected",
                    details={"delta": delta.pagination_details},
                    source_run_id=source_run_id,
                    fingerprint_delta=delta,
                )
            )
        if delta.keys_changed:
            signals.append(
                DriftSignal(
                    kind="keys_changed",
                    severity="medium",
                    message="Entity keys fingerprint drift",
                    details={"delta": delta.keys_details},
                    source_run_id=source_run_id,
                    fingerprint_delta=delta,
                )
            )
        if delta.types_changed:
            signals.append(
                DriftSignal(
                    kind="types_changed",
                    severity="medium",
                    message="Field types drift detected",
                    details={"delta": delta.types_details},
                    source_run_id=source_run_id,
                    fingerprint_delta=delta,
                )
            )
        if delta.fingerprint_changed and not (
            delta.pagination_changed or delta.keys_changed or delta.types_changed
        ):
            # Fallback: fingerprint changed but no dimension flagged (e.g. spec_signature)
            signals.append(
                DriftSignal(
                    kind="fingerprint_changed",
                    severity="medium",
                    message="Fingerprint changed (spec or unknown dimension)",
                    details={},
                    source_run_id=source_run_id,
                    fingerprint_delta=delta,
                )
            )

    return signals


def signals_from_canary(
    canary_summary: dict[str, Any] | None,
    *,
    spec: dict[str, Any] | None = None,
    previous_fingerprint: Fingerprint | dict[str, Any] | str | None = None,
    current_fingerprint: Fingerprint | dict[str, Any] | None = None,
    source_run_id: str | None = None,
) -> list[DriftSignal]:
    """Collect signals from a canary probe.

    *canary_summary* is like a sync summary but for the single
    ``health.canary`` operation (max_pages=1).  When the canary failed its
    own ``execute_sync`` equivalent, it will have outcome Blocked/Degraded
    and http_errors.

    When the canary fingerprint is provided, drift vs the last successful
    sync is reported.
    """
    if not canary_summary:
        return []
    # Reuse sync signals extraction but tag kind as canary_failed where appropriate
    raw_signals = signals_from_sync(
        canary_summary,
        spec=spec,
        previous_fingerprint=previous_fingerprint,
        current_fingerprint=current_fingerprint,
        source_run_id=source_run_id,
    )
    # Rewrite critical outcome signals to canary namespace where they would
    # otherwise be ambiguous (keep original kind but add canary context).
    canary_op = None
    if isinstance(spec, dict):
        health = spec.get("health", {})
        if isinstance(health, dict):
            canary = health.get("canary", {})
            if isinstance(canary, dict):
                canary_op = canary.get("operation")

    out: list[DriftSignal] = []
    for sig in raw_signals:
        # If the signal is outcome_blocked/degraded on the canary, upgrade to canary_failed
        if sig.kind in {"outcome_blocked", "outcome_degraded"}:
            out.append(
                DriftSignal(
                    kind="canary_failed",
                    operation=canary_op or sig.operation,
                    severity="high",
                    message=f"Canary failed: {sig.message}",
                    evidence_ids=list(sig.evidence_ids),
                    details={**sig.details, "original_kind": sig.kind},
                    source_run_id=source_run_id,
                )
            )
        else:
            # For pagination_changed from canary, rename to canary_pagination_regression
            if sig.kind == "pagination_changed" and canary_op:
                out.append(
                    DriftSignal(
                        kind="canary_pagination_regression",
                        operation=canary_op,
                        severity="high",
                        message=f"Canary pagination regression on {canary_op}",
                        evidence_ids=list(sig.evidence_ids),
                        details=dict(sig.details),
                        source_run_id=source_run_id,
                        fingerprint_delta=sig.fingerprint_delta,
                    )
                )
            else:
                out.append(sig)

    # If canary had no raw signals but outcome was not Complete, ensure canary_failed
    if not raw_signals:
        outcome = canary_summary.get("outcome") if isinstance(canary_summary, dict) else None
        if outcome and outcome != "Complete":
            out.append(
                DriftSignal(
                    kind="canary_failed",
                    operation=canary_op,
                    severity="high",
                    message=f"Canary outcome {outcome}",
                    details={"outcome": outcome},
                    source_run_id=source_run_id,
                )
            )

    # If canary summary missing fingerprint but has http error, it will already be
    # in raw_signals via _extract_http_signals; no extra needed.

    return out


def collect_signals(
    sync_summary: dict[str, Any] | None,
    canary_summary: dict[str, Any] | None = None,
    *,
    spec: dict[str, Any] | None = None,
    previous_fingerprint: Fingerprint | dict[str, Any] | str | None = None,
    current_fingerprint: Fingerprint | dict[str, Any] | None = None,
    source_run_id: str | None = None,
) -> list[DriftSignal]:
    """Combine sync + canary signals into a single list.

    Deduplicates by (kind, operation, entity).  The caller (classifier) can
    further prioritize by severity.
    """
    signals: list[DriftSignal] = []
    if sync_summary:
        signals.extend(
            signals_from_sync(
                sync_summary,
                spec=spec,
                previous_fingerprint=previous_fingerprint,
                current_fingerprint=current_fingerprint,
                source_run_id=source_run_id,
            )
        )
    if canary_summary:
        canary_sigs = signals_from_canary(
            canary_summary,
            spec=spec,
            previous_fingerprint=previous_fingerprint,
            current_fingerprint=current_fingerprint,
            source_run_id=source_run_id,
        )
        signals.extend(canary_sigs)

    # Simple deduplication: keep first occurrence of (kind, operation, entity)
    seen: set[tuple[str, str | None, str | None]] = set()
    deduped: list[DriftSignal] = []
    for sig in signals:
        key = (sig.kind, sig.operation, sig.entity)
        if key not in seen:
            seen.add(key)
            deduped.append(sig)
    return deduped
