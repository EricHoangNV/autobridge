"""Drift classifier for the 12 canonical classes of plan §2.5 (P1.W4.2).

Each SyncRun ends ``Complete | Degraded | Blocked`` with ``unmet_criteria``.
The raw observations are :mod:`autobridge.drift.signals` (deterministic,
no LLM). This module maps a list of :class:`DriftSignal` — or a
``sync_summary`` dict — to exactly one of the 12 canonical drift classes.

Canonical classes (plan §2.5)::

    AUTH_EXPIRED, AUTH_REVOKED, RATE_LIMIT, TRANSIENT, SCHEMA_RENAME,
    SCHEMA_REMOVED, SCHEMA_TYPE_CHANGE, PAGINATION_CHANGE, ENUM_NEW_VALUE,
    SEMANTIC_VALUE_DRIFT, CAPABILITY_REMOVED, UNKNOWN

Trust boundary: no import of ``verifier`` or ``simulators``.

Evidence wiring: every :class:`DriftSignal` carries ``evidence_ids`` (ids of
``EvidenceRecord`` rows that support it). The classifier aggregates the ids
of the signals that decided the class — callers persist them on
``DriftEvent.evidence_ids``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from autobridge.drift.signals import DriftSignal, signals_from_sync

if TYPE_CHECKING:
    from collections.abc import Callable

    from autobridge.drift.fingerprint import FingerprintDelta

# ---------------------------------------------------------------------------
# Canonical drift classes (plan §2.5)
# ---------------------------------------------------------------------------

DRIFT_CLASSES: frozenset[str] = frozenset(
    {
        "AUTH_EXPIRED",
        "AUTH_REVOKED",
        "RATE_LIMIT",
        "TRANSIENT",
        "SCHEMA_RENAME",
        "SCHEMA_REMOVED",
        "SCHEMA_TYPE_CHANGE",
        "PAGINATION_CHANGE",
        "ENUM_NEW_VALUE",
        "SEMANTIC_VALUE_DRIFT",
        "CAPABILITY_REMOVED",
        "UNKNOWN",
    }
)

# Fixability per plan §4 W4: CAPABILITY_REMOVED and AUTH_REVOKED always
# escalate (no patch). SEMANTIC_VALUE_DRIFT without independent evidence also
# escalates (handled by W6, not here). For the classifier we only mark the
# two always-escalate classes as not fixable.
_NON_FIXABLE: frozenset[str] = frozenset({"CAPABILITY_REMOVED", "AUTH_REVOKED"})

_SEVERITY_BY_CLASS: dict[str, str] = {
    "AUTH_EXPIRED": "high",
    "AUTH_REVOKED": "critical",
    "RATE_LIMIT": "medium",
    "TRANSIENT": "low",
    "SCHEMA_RENAME": "high",
    "SCHEMA_REMOVED": "critical",
    "SCHEMA_TYPE_CHANGE": "high",
    "PAGINATION_CHANGE": "high",
    "ENUM_NEW_VALUE": "high",
    "SEMANTIC_VALUE_DRIFT": "high",
    "CAPABILITY_REMOVED": "critical",
    "UNKNOWN": "medium",
}

_CONFIDENCE_BY_CLASS: dict[str, float] = {
    "AUTH_EXPIRED": 0.98,
    "AUTH_REVOKED": 0.99,
    "RATE_LIMIT": 0.97,
    "TRANSIENT": 0.95,
    "SCHEMA_RENAME": 0.92,
    "SCHEMA_REMOVED": 0.93,
    "SCHEMA_TYPE_CHANGE": 0.90,
    "PAGINATION_CHANGE": 0.94,
    "ENUM_NEW_VALUE": 0.96,
    "SEMANTIC_VALUE_DRIFT": 0.88,
    "CAPABILITY_REMOVED": 0.99,
    "UNKNOWN": 0.5,
}


@dataclass(frozen=True, slots=True)
class DriftClassification:
    """Deterministic classification result with provenance."""

    drift_class: str
    confidence: float
    severity: str
    evidence_ids: list[str] = field(default_factory=list)
    reason: str = ""
    supporting_signal_kinds: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    fixable: bool = True


# ---------------------------------------------------------------------------
# Helpers — signal grouping and evidence aggregation
# ---------------------------------------------------------------------------


def _group_by_kind(signals: list[DriftSignal]) -> dict[str, list[DriftSignal]]:
    grouped: dict[str, list[DriftSignal]] = {}
    for sig in signals:
        grouped.setdefault(sig.kind, []).append(sig)
    return grouped


def _evidence_for_kinds(grouped: dict[str, list[DriftSignal]], kinds: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for k in kinds:
        for sig in grouped.get(k, []):
            for eid in sig.evidence_ids or []:
                if eid not in seen:
                    seen.add(eid)
                    out.append(eid)
    return out


def _http_status(sig: DriftSignal) -> int | None:
    v = sig.details.get("http_status")
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None
    return None


def _analyze_types_delta(delta: FingerprintDelta | None) -> str | None:
    """Inspect a types delta to distinguish rename / removed / type-change.

    Returns one of SCHEMA_RENAME, SCHEMA_REMOVED, SCHEMA_TYPE_CHANGE or None
    when the delta is absent or inconclusive.
    """
    if delta is None or not delta.types_details:
        return None
    for _ent, detail in delta.types_details.items():
        prev = detail.get("previous")
        cur = detail.get("current")
        if not isinstance(prev, dict) or not isinstance(cur, dict):
            continue
        prev_fields = set(prev.keys())
        cur_fields = set(cur.keys())
        removed = prev_fields - cur_fields
        added = cur_fields - prev_fields
        common = prev_fields & cur_fields
        # Type change takes precedence over rename/removed when same field changes type.
        for fld in common:
            if prev.get(fld) != cur.get(fld):
                return "SCHEMA_TYPE_CHANGE"
        # Rename: one field replaced by one new field with same type.
        if len(removed) == 1 and len(added) == 1:
            old_field = next(iter(removed))
            new_field = next(iter(added))
            if prev.get(old_field) == cur.get(new_field):
                return "SCHEMA_RENAME"
            # Even with different type, still a rename (covers edge cases)
            return "SCHEMA_RENAME"
        # Removed: fields disappeared with no replacement.
        if removed and not added:
            return "SCHEMA_REMOVED"
        # Added without removal is unusual for drift, but treat as rename (new field)
        if added and not removed:
            return "SCHEMA_RENAME"
    # Fallback when we have delta but no fine-grained detail: treat as rename
    return "SCHEMA_RENAME"


def _collect_fingerprint_deltas(signals: list[DriftSignal]) -> list[FingerprintDelta]:
    out: list[FingerprintDelta] = []
    for sig in signals:
        if sig.fingerprint_delta is not None:
            out.append(sig.fingerprint_delta)
    return out


# ---------------------------------------------------------------------------
# Core priority matching
# ---------------------------------------------------------------------------


def _wire_provenance(
    grouped: dict[str, list[DriftSignal]], kinds: list[str]
) -> list[dict[str, Any]]:
    """Wire-observation provenance for the signals that decided the class.

    Each entry names where the evidence came from (``raw_pages`` envelope
    vs ``records``) so later stages (demo steps, REQ-027 item 4 output) can
    say which input each repair input came from.
    """
    out: list[dict[str, Any]] = []
    for kind in kinds:
        for sig in grouped.get(kind, []):
            details = sig.details if isinstance(sig.details, dict) else {}
            if not isinstance(details, dict) or "wire_evidence" not in details:
                continue
            entry: dict[str, Any] = {
                "signal_kind": kind,
                "operation": sig.operation,
                "source": details.get("wire_evidence"),
            }
            for key in (
                "spec_strategy",
                "wire_strategy_hint",
                "cursor_field",
                "envelope_keys",
                "entity",
                "missing",
                "added",
                "spec_items_path",
                "array_candidates",
            ):
                if key in details:
                    entry[key] = details[key]
            out.append(entry)
    return out


def _match_auth_revoked(grouped: dict[str, list[DriftSignal]]) -> tuple[str, list[str], str] | None:
    # Any auth_error with 403, or explicit http 403, is revoked (permanent).
    for sig in grouped.get("auth_error", []):
        if _http_status(sig) == 403:
            return ("AUTH_REVOKED", ["auth_error"], "HTTP 403 auth revoked")
        # Heuristic: details may contain 'token_revoked' string
        details_str = str(sig.details).lower()
        if "revoked" in details_str or "token_revoked" in details_str:
            return ("AUTH_REVOKED", ["auth_error"], "Auth error indicates revoked token")
    # Also check capability_removed with 403
    for sig in grouped.get("capability_removed", []):
        if _http_status(sig) == 403:
            return ("AUTH_REVOKED", ["capability_removed"], "HTTP 403 treated as auth revoked")
    # Check sync outcome Blocked with auth hint
    for sig in grouped.get("outcome_blocked", []):
        uc = sig.details.get("unmet_criteria", [])
        text = " ".join(uc) if isinstance(uc, list) else str(uc)
        if "revoked" in text.lower() or "auth_revoked" in text.lower():
            return ("AUTH_REVOKED", ["outcome_blocked"], "Blocked with auth_revoked criterion")
    return None


def _match_auth_expired(grouped: dict[str, list[DriftSignal]]) -> tuple[str, list[str], str] | None:
    for sig in grouped.get("auth_error", []):
        st = _http_status(sig)
        if st == 401:
            return ("AUTH_EXPIRED", ["auth_error"], "HTTP 401 token expired")
        # Fallback: any auth_error without 403 is treated as expired
        # but only if no other auth signal claimed revoked
        details_str = str(sig.details).lower()
        if "expired" in details_str or "token_expired" in details_str:
            return ("AUTH_EXPIRED", ["auth_error"], "Auth error indicates expired token")
    # Explicit outcome with auth_expired
    for sig in grouped.get("outcome_blocked", []):
        uc = sig.details.get("unmet_criteria", [])
        text = " ".join(uc) if isinstance(uc, list) else str(uc)
        if "expired" in text.lower() or "auth_expired" in text.lower():
            return ("AUTH_EXPIRED", ["outcome_blocked"], "Blocked with auth_expired criterion")
    # Generic auth_error fallback (single auth signal, not revoked)
    if "auth_error" in grouped:
        return ("AUTH_EXPIRED", ["auth_error"], "Auth error fallback to expired")
    return None


def _match_capability_removed(grouped: dict[str, list[DriftSignal]]) -> tuple[str, list[str], str] | None:
    if "capability_removed" in grouped:
        return ("CAPABILITY_REMOVED", ["capability_removed"], "Capability removed signal")
    for sig in grouped.get("http_error", []):
        if _http_status(sig) == 404:
            return ("CAPABILITY_REMOVED", ["http_error"], "HTTP 404 endpoint removed")
    # Zero records + Blocked is the classic endpoint_removed pattern.
    if "zero_records" in grouped and "outcome_blocked" in grouped:
        return ("CAPABILITY_REMOVED", ["zero_records", "outcome_blocked"], "Zero records with Blocked outcome")
    # Check outcome_blocked with 404 in details
    for sig in grouped.get("outcome_blocked", []):
        text = str(sig.details).lower()
        if "404" in text or "endpoint_not_found" in text or "capability_removed" in text:
            return ("CAPABILITY_REMOVED", ["outcome_blocked"], "Blocked indicates removed capability")
    return None


def _match_rate_limit(grouped: dict[str, list[DriftSignal]]) -> tuple[str, list[str], str] | None:
    if "rate_limited" in grouped:
        return ("RATE_LIMIT", ["rate_limited"], "Rate limited signal")
    for sig in grouped.get("http_error", []):
        if _http_status(sig) == 429:
            return ("RATE_LIMIT", ["http_error"], "HTTP 429 rate limit")
    return None


def _match_transient(grouped: dict[str, list[DriftSignal]]) -> tuple[str, list[str], str] | None:
    if "transient_error" in grouped:
        return ("TRANSIENT", ["transient_error"], "Transient error signal")
    for sig in grouped.get("http_error", []):
        st = _http_status(sig)
        if st in (500, 502, 503, 504):
            return ("TRANSIENT", ["http_error"], f"HTTP {st} transient")
    return None


def _match_enum_new_value(grouped: dict[str, list[DriftSignal]]) -> tuple[str, list[str], str] | None:
    if "unknown_enum" in grouped:
        return ("ENUM_NEW_VALUE", ["unknown_enum"], "Unknown enum value")
    # Also check outcome_blocked with unknown_enum in details
    for sig in grouped.get("outcome_blocked", []):
        uc = sig.details.get("unmet_criteria", [])
        text = " ".join(uc) if isinstance(uc, list) else str(uc)
        if "unknown_enum" in text:
            return ("ENUM_NEW_VALUE", ["outcome_blocked"], "Blocked on unknown enum")
    return None


def _match_pagination_change(grouped: dict[str, list[DriftSignal]]) -> tuple[str, list[str], str] | None:
    if "repeated_cursor" in grouped:
        return ("PAGINATION_CHANGE", ["repeated_cursor"], "Repeated cursor pagination bug")
    if "pagination_changed" in grouped:
        return ("PAGINATION_CHANGE", ["pagination_changed"], "Pagination shape changed")
    if "canary_pagination_regression" in grouped:
        return ("PAGINATION_CHANGE", ["canary_pagination_regression"], "Canary pagination regression")
    if "fingerprint_changed" in grouped:
        # Fingerprint changed alone could be pagination; but pagination_changed
        # already handled. Only treat as pagination if other pagination hints exist.
        # Here we conservatively check fingerprint_changed with pagination details.
        for sig in grouped["fingerprint_changed"]:
            if sig.fingerprint_delta and sig.fingerprint_delta.pagination_changed:
                return ("PAGINATION_CHANGE", ["fingerprint_changed"], "Fingerprint pagination drift")
    # Also check fingerprint deltas on pagination_changed already handled above
    return None


def _wire_schema_rename(
    signals: list[DriftSignal],
) -> tuple[str, str, str] | None:
    """Single missing + single added wire key (REQ-027 wire evidence).

    The canonical-types fingerprint cannot show a rename (unmapped wire keys
    never reach canonical records), so this reads the wire-schema signal
    details directly. Returns (entity, old_key, new_key) or None.
    """
    for sig in signals:
        details = sig.details if isinstance(sig.details, dict) else None
        if not details:
            continue
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
            return (
                entity if isinstance(entity, str) else "",
                missing[0],
                str(added[0]["key"]),
            )
    return None


def _match_wire_schema_rename(
    grouped: dict[str, list[DriftSignal]], signals: list[DriftSignal]
) -> tuple[str, list[str], str] | None:
    if "keys_changed" not in grouped:
        return None
    hit = _wire_schema_rename(signals)
    if hit is None:
        return None
    entity, old_key, new_key = hit
    return (
        "SCHEMA_RENAME",
        ["keys_changed"],
        f"Wire rename on {entity or 'entity'}: {old_key} -> {new_key}",
    )


def _match_schema_from_types(
    grouped: dict[str, list[DriftSignal]], signals: list[DriftSignal]
) -> tuple[str, list[str], str] | None:
    if "types_changed" not in grouped and "keys_changed" not in grouped:
        return None
    deltas = _collect_fingerprint_deltas(signals)
    for delta in deltas:
        sub = _analyze_types_delta(delta)
        if sub == "SCHEMA_TYPE_CHANGE":
            return ("SCHEMA_TYPE_CHANGE", ["types_changed"], "Field type changed")
        if sub == "SCHEMA_REMOVED":
            return ("SCHEMA_REMOVED", ["types_changed"], "Field removed from schema")
        if sub == "SCHEMA_RENAME":
            return ("SCHEMA_RENAME", ["types_changed"], "Field renamed in schema")
    # Fallback when delta absent or inconclusive: generic rename/type-change mapping
    # Prefer SCHEMA_RENAME for types_changed (field rename is most common)
    if "types_changed" in grouped:
        # Check if any signal details hint at removed
        for sig in grouped["types_changed"]:
            text = str(sig.details).lower()
            if "removed" in text:
                return ("SCHEMA_REMOVED", ["types_changed"], "Field removed")
            if "type" in text and "change" in text:
                return ("SCHEMA_TYPE_CHANGE", ["types_changed"], "Field type change hint")
        return ("SCHEMA_RENAME", ["types_changed"], "Field schema changed (rename fallback)")
    if "keys_changed" in grouped:
        return ("SCHEMA_RENAME", ["keys_changed"], "Entity keys changed (rename)")
    return None


def _match_semantic(grouped: dict[str, list[DriftSignal]]) -> tuple[str, list[str], str] | None:
    # SEMANTIC_VALUE_DRIFT is schema-valid but invariant fails (money, date, counts).
    if "invariant_failure" in grouped:
        return ("SEMANTIC_VALUE_DRIFT", ["invariant_failure"], "Invariant failure indicates semantic drift")
    if "no_loss" in grouped:
        return ("SEMANTIC_VALUE_DRIFT", ["no_loss"], "no_loss violation indicates semantic drift")
    if "outcome_degraded" in grouped:
        # Degraded with unmet_criteria not already classified as enum/pagination etc.
        for sig in grouped["outcome_degraded"]:
            uc = sig.details.get("unmet_criteria", [])
            text = " ".join(str(x) for x in uc) if isinstance(uc, list) else str(uc)
            # If it contains known invariant names, it's semantic
            for key in (
                "money",
                "money_invariant",
                "business_date_invariant",
                "count_invariant",
                "unique_key",
                "date_order",
                "transform_error",
                "duplicate",
            ):
                if key in text.lower():
                    return ("SEMANTIC_VALUE_DRIFT", ["outcome_degraded"], f"Degraded with {key} invariant")
        # Generic degraded fallback
        return ("SEMANTIC_VALUE_DRIFT", ["outcome_degraded"], "Degraded with invariant failure")
    return None


# Priority ordered matchers — most specific / highest severity first.
# ENUM must precede CAPABILITY_REMOVED because status_vocab_change produces
# zero_records + Blocked with unknown_enum, but should be ENUM not capability.
_MATCHERS: list[Callable[..., tuple[str, list[str], str] | None]] = [
    _match_auth_revoked,
    _match_auth_expired,
    _match_enum_new_value,
    _match_capability_removed,
    _match_rate_limit,
    _match_transient,
    _match_pagination_change,
    _match_wire_schema_rename,
    _match_schema_from_types,
    _match_semantic,
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify(signals: list[DriftSignal]) -> DriftClassification:
    """Classify a list of drift signals into one canonical drift class.

    Deterministic, no model calls. Evidence ids are aggregated from the
    supporting signals.

    When *signals* is empty or contains no recognizable pattern, returns
    ``UNKNOWN`` with confidence 0.5.

    Args:
        signals: Output of :func:`autobridge.drift.signals.collect_signals` or
                 manually constructed :class:`DriftSignal` list.

    Returns:
        A :class:`DriftClassification` whose ``drift_class`` is one of
        :data:`DRIFT_CLASSES`.
    """
    if not signals:
        return DriftClassification(
            drift_class="UNKNOWN",
            confidence=_CONFIDENCE_BY_CLASS["UNKNOWN"],
            severity=_SEVERITY_BY_CLASS["UNKNOWN"],
            evidence_ids=[],
            reason="No drift signals observed",
            supporting_signal_kinds=[],
            details={},
            fixable="UNKNOWN" not in _NON_FIXABLE,
        )

    grouped = _group_by_kind(signals)

    # Walk priority-ordered matchers; first hit wins.
    for matcher in _MATCHERS:
        hit = (
            matcher(grouped, signals)
            if matcher in (_match_schema_from_types, _match_wire_schema_rename)
            else matcher(grouped)
        )
        if hit is not None:
            drift_class, supporting_kinds, reason = hit
            evidence_ids = _evidence_for_kinds(grouped, supporting_kinds)
            # Also include evidence ids from fingerprint deltas where relevant
            # (signals already carry them; no extra step needed).
            return DriftClassification(
                drift_class=drift_class,
                confidence=_CONFIDENCE_BY_CLASS[drift_class],
                severity=_SEVERITY_BY_CLASS[drift_class],
                evidence_ids=evidence_ids,
                reason=reason,
                supporting_signal_kinds=supporting_kinds,
                details={
                    "matched_by": matcher.__name__,
                    "signal_counts": {k: len(v) for k, v in grouped.items()},
                    "wire_evidence": _wire_provenance(grouped, supporting_kinds),
                },
                fixable=drift_class not in _NON_FIXABLE,
            )

    # Fallback: unrecognized signals — treat as UNKNOWN but preserve evidence.
    all_evidence: list[str] = []
    seen: set[str] = set()
    for sig in signals:
        for eid in sig.evidence_ids or []:
            if eid not in seen:
                seen.add(eid)
                all_evidence.append(eid)
    return DriftClassification(
        drift_class="UNKNOWN",
        confidence=_CONFIDENCE_BY_CLASS["UNKNOWN"],
        severity=_SEVERITY_BY_CLASS["UNKNOWN"],
        evidence_ids=all_evidence,
        reason="Signals present but no matcher fired — unrecognized drift pattern",
        supporting_signal_kinds=sorted(grouped.keys()),
        details={"signal_counts": {k: len(v) for k, v in grouped.items()}},
        fixable="UNKNOWN" not in _NON_FIXABLE,
    )


def classify_sync(
    sync_summary: dict[str, Any] | None,
    *,
    spec: dict[str, Any] | None = None,
    previous_fingerprint: Any | None = None,
    current_fingerprint: Any | None = None,
    source_run_id: str | None = None,
) -> DriftClassification:
    """Classify directly from a sync summary (convenience wrapper).

    Builds signals via :func:`autobridge.drift.signals.signals_from_sync` then
    delegates to :func:`classify`.

    A ``None`` or empty *sync_summary* (e.g. baseline Complete with no drift)
    yields ``UNKNOWN``. Callers that need to distinguish "no drift" from
    "unknown drift" should check ``sync_summary.get("outcome") == "Complete"``
    themselves; the classifier always returns a drift class.

    Args:
        sync_summary: Dict returned by :func:`autobridge.engine.sync_run.execute_sync`
            (outcome, unmet_criteria, operation_results, etc.).
        spec: Optional ConnectorSpec dict for pagination enrichment.
        previous_fingerprint: Fingerprint before the sync (for delta signals).
        current_fingerprint: Fingerprint after the sync.
        source_run_id: Provenance run id stored on signals.

    Returns:
        A :class:`DriftClassification`.
    """
    if not sync_summary:
        return DriftClassification(
            drift_class="UNKNOWN",
            confidence=_CONFIDENCE_BY_CLASS["UNKNOWN"],
            severity=_SEVERITY_BY_CLASS["UNKNOWN"],
            evidence_ids=[],
            reason="No sync summary provided",
            supporting_signal_kinds=[],
            details={},
            fixable=True,
        )
    signals = signals_from_sync(
        sync_summary,
        spec=spec,
        previous_fingerprint=previous_fingerprint,
        current_fingerprint=current_fingerprint,
        source_run_id=source_run_id,
    )
    result = classify(signals)
    # Attach sync context for debugging
    result.details["sync_outcome"] = sync_summary.get("outcome")
    result.details["sync_unmet_criteria"] = sync_summary.get("unmet_criteria")
    return result


# Backwards-compatible alias: some early drafts used ``classify_signals``.
classify_signals = classify
