"""Repair ladder L0/L1 wiring (P1.W4.4, plan §2.5).

Levels:
  L0 KnowledgeLookup — profile cache keyed by vendor + fingerprint delta
  L1 RuleRegistry    — deterministic rules (retry_backoff_only, rename_field, etc.)
  L2/L3 reserved for W5.5 (small/frontier models) — not invoked here
  L4 Escalate        — human bundle

Special handling (W4.4 spec):
  - RATE_LIMIT / TRANSIENT (429/503) → no patch, backoff only
  - AUTH_EXPIRED (token_expired) → refresh if permitted else escalate
  - CAPABILITY_REMOVED / AUTH_REVOKED → escalate with no patch attempted
  - All paths emit EvidenceRecords trace and keep frontier_tokens == 0

Trust boundary: no import of verifier/simulators.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from autobridge.repair.context import RepairContext
from autobridge.repair.knowledge import KnowledgeCache, cache_key
from autobridge.repair.registry import matching_rules

# Drift classes that always escalate (no patch attempted)
_NON_FIXABLE: frozenset[str] = frozenset({"CAPABILITY_REMOVED", "AUTH_REVOKED"})

# Drift classes that are backoff-only (no spec patch)
_BACKOFF_ONLY_CLASSES: frozenset[str] = frozenset({"RATE_LIMIT", "TRANSIENT"})


def _can_refresh_token(spec: dict[str, Any]) -> bool:
    """True if spec auth supports refresh (oauth2_client_credentials with token_url)."""
    try:
        source = spec.get("source", {})
        if not isinstance(source, dict):
            return False
        auth = source.get("auth", {})
        if not isinstance(auth, dict):
            return False
        if auth.get("type") != "oauth2_client_credentials":
            return False
        token_url = auth.get("token_url")
        return isinstance(token_url, str) and bool(token_url.strip())
    except Exception:
        return False


def _extract_vendor(spec: dict[str, Any]) -> str:
    try:
        conn = spec.get("connector", {})
        if isinstance(conn, dict):
            v = conn.get("vendor")
            if isinstance(v, str) and v.strip():
                return v.strip()
    except Exception:
        pass
    return "unknown"


@dataclass(frozen=True, slots=True)
class LadderResult:
    """Outcome of one ladder walk."""

    decision: str  # propose | escalate
    ladder_level_reached: int
    candidate_patch: list[dict[str, Any]] | None
    rule_id: str | None
    verification_level: str | None
    model_usage: dict[str, Any]  # frontier_tokens, small_tokens, calls
    evidence_ids: list[str] = field(default_factory=list)
    ladder_trace: list[dict[str, Any]] = field(default_factory=list)
    backoff_only: bool = False
    escalation_reason: str | None = None
    requires_release: bool = True  # False for backoff-only/refresh without release
    confidence: float | None = None


def _empty_model_usage() -> dict[str, Any]:
    return {"frontier_tokens": 0, "small_tokens": 0, "calls": 0, "frontier_calls": 0, "small_calls": 0}


def run_ladder(
    spec: dict[str, Any],
    drift_class: str,
    signals: list[Any] | None = None,
    fingerprint_delta: Any | None = None,
    *,
    vendor: str | None = None,
    classification: Any | None = None,
    extra: dict[str, Any] | None = None,
    knowledge_cache: KnowledgeCache | None = None,
) -> LadderResult:
    """Execute L0/L1 ladder deterministically (no model calls).

    Args:
        spec: ConnectorSpec dict (validated).
        drift_class: Canonical drift class (from classifier).
        signals: DriftSignal list for rule context.
        fingerprint_delta: FingerprintDelta or dict for L0 key and rule delta.
        vendor: Override vendor (else extracted from spec).
        classification: DriftClassification if available.
        extra: Extra evidence dict for RepairContext.
        knowledge_cache: L0 cache (miss if None or no vendor/delta).

    Returns:
        LadderResult with decision, patch, trace, and model_usage (all zero frontier).
    """
    signals_list: list[Any] = list(signals or [])
    extra_dict: dict[str, Any] = dict(extra or {})
    vendor_val = vendor or _extract_vendor(spec)
    trace: list[dict[str, Any]] = []
    model_usage = _empty_model_usage()

    # Extract evidence ids from signals if available
    evidence_ids: list[str] = []
    seen_eids: set[str] = set()
    for sig in signals_list:
        eids: list[str] | None = None
        if isinstance(sig, dict):
            eids = sig.get("evidence_ids")
        elif hasattr(sig, "evidence_ids"):
            eids = getattr(sig, "evidence_ids", None)
        if eids:
            for eid in eids:
                if isinstance(eid, str) and eid not in seen_eids:
                    seen_eids.add(eid)
                    evidence_ids.append(eid)

    # --- Non-fixable escalations: CAPABILITY_REMOVED / AUTH_REVOKED ---
    if drift_class in _NON_FIXABLE:
        reason = f"{drift_class} is not repairable: requires human intervention"
        if drift_class == "CAPABILITY_REMOVED":
            reason = "CAPABILITY_REMOVED: endpoint or capability removed — escalate with no patch"
        elif drift_class == "AUTH_REVOKED":
            reason = "AUTH_REVOKED: credential permanently revoked — escalate with no patch"
        trace.append(
            {
                "level": 4,
                "action": "escalate",
                "reason": reason,
                "drift_class": drift_class,
            }
        )
        return LadderResult(
            decision="escalate",
            ladder_level_reached=4,
            candidate_patch=None,
            rule_id=None,
            verification_level=None,
            model_usage=model_usage,
            evidence_ids=evidence_ids,
            ladder_trace=trace,
            backoff_only=False,
            escalation_reason=reason,
            requires_release=False,
        )

    # --- AUTH_EXPIRED: token_expired -> refresh if permitted else escalate ---
    if drift_class == "AUTH_EXPIRED":
        if _can_refresh_token(spec):
            trace.append(
                {
                    "level": 0,
                    "action": "refresh_token",
                    "reason": "AUTH_EXPIRED with oauth2 refresh permitted — refresh token, no patch",
                    "drift_class": drift_class,
                }
            )
            return LadderResult(
                decision="propose",
                ladder_level_reached=0,
                candidate_patch=[],
                rule_id="token_refresh",
                verification_level="none",
                model_usage=model_usage,
                evidence_ids=evidence_ids,
                ladder_trace=trace,
                backoff_only=True,
                escalation_reason=None,
                requires_release=False,
                confidence=0.98,
            )
        # Not permitted -> escalate
        reason = "AUTH_EXPIRED: token refresh not permitted (non-oauth2 or missing token_url) — escalate"
        trace.append({"level": 4, "action": "escalate", "reason": reason, "drift_class": drift_class})
        return LadderResult(
            decision="escalate",
            ladder_level_reached=4,
            candidate_patch=None,
            rule_id=None,
            verification_level=None,
            model_usage=model_usage,
            evidence_ids=evidence_ids,
            ladder_trace=trace,
            backoff_only=False,
            escalation_reason=reason,
            requires_release=False,
        )

    # --- RATE_LIMIT / TRANSIENT: backoff only (no patch) ---
    if drift_class in _BACKOFF_ONLY_CLASSES:
        # Try L1 rule retry_backoff_only for consistent trace, but result is backoff only
        ctx = RepairContext(
            spec=spec,
            drift_class=drift_class,
            signals=signals_list,
            fingerprint_delta=fingerprint_delta,
            classification=classification,
            extra=extra_dict,
        )
        # Find retry rule if present
        matched = matching_rules(ctx)
        retry_entry = None
        for e in matched:
            if e.id == "retry_backoff_only":
                retry_entry = e
                break
        patch: list[dict[str, Any]] = []
        if retry_entry is not None:
            try:
                patch = retry_entry.generate_patch(ctx)
            except Exception:
                patch = []
            trace.append(
                {
                    "level": 1,
                    "rule_id": retry_entry.id,
                    "action": "backoff_only",
                    "reason": f"{drift_class}: rate limit/transient — backoff only, no spec patch",
                    "drift_class": drift_class,
                    "patch": patch,
                    "confidence": retry_entry.confidence,
                    "verification_level": retry_entry.verification_level,
                }
            )
            # If patch empty, it's still backoff_only with no release needed
            # If patch adds missing status, it is a propose with that patch
            if not patch:
                return LadderResult(
                    decision="propose",
                    ladder_level_reached=1,
                    candidate_patch=[],
                    rule_id=retry_entry.id,
                    verification_level=retry_entry.verification_level,
                    model_usage=model_usage,
                    evidence_ids=evidence_ids,
                    ladder_trace=trace,
                    backoff_only=True,
                    escalation_reason=None,
                    requires_release=False,
                    confidence=retry_entry.confidence,
                )
            # Patch exists (missing status) — propose with release
            return LadderResult(
                decision="propose",
                ladder_level_reached=1,
                candidate_patch=patch,
                rule_id=retry_entry.id,
                verification_level=retry_entry.verification_level,
                model_usage=model_usage,
                evidence_ids=evidence_ids,
                ladder_trace=trace,
                backoff_only=False,
                escalation_reason=None,
                requires_release=True,
                confidence=retry_entry.confidence,
            )
        # Fallback when rule not matched (should not happen)
        trace.append(
            {
                "level": 1,
                "action": "backoff_only",
                "reason": f"{drift_class}: backoff only — no patch",
                "drift_class": drift_class,
            }
        )
        return LadderResult(
            decision="propose",
            ladder_level_reached=1,
            candidate_patch=[],
            rule_id="retry_backoff_only",
            verification_level="none",
            model_usage=model_usage,
            evidence_ids=evidence_ids,
            ladder_trace=trace,
            backoff_only=True,
            escalation_reason=None,
            requires_release=False,
            confidence=0.97,
        )

    # --- L0: Knowledge lookup ---
    if knowledge_cache is not None and fingerprint_delta is not None:
        # Need vendor to key; if missing still try
        cache_entry = knowledge_cache.lookup(vendor_val, fingerprint_delta)
        if cache_entry is not None:
            trace.append(
                {
                    "level": 0,
                    "action": "knowledge_hit",
                    "reason": f"L0 cache hit for vendor {vendor_val} + delta {cache_key(vendor_val, fingerprint_delta)}",
                    "drift_class": drift_class,
                    "rule_id": cache_entry.rule_id,
                    "patch": cache_entry.patch,
                    "vendor": vendor_val,
                    "delta_key": cache_entry.delta_key,
                }
            )
            # Validate patch still applies (spec may have changed)
            try:
                from autobridge.spec.patch import apply_patch as _apply_patch

                _apply_patch(spec, cache_entry.patch)
                return LadderResult(
                    decision="propose",
                    ladder_level_reached=0,
                    candidate_patch=list(cache_entry.patch),
                    rule_id=cache_entry.rule_id,
                    verification_level="visible",
                    model_usage=model_usage,
                    evidence_ids=evidence_ids,
                    ladder_trace=trace,
                    backoff_only=False,
                    escalation_reason=None,
                    requires_release=True,
                    confidence=cache_entry.confidence or 0.95,
                )
            except Exception as e:
                trace.append(
                    {
                        "level": 0,
                        "action": "knowledge_invalid",
                        "reason": f"Cached patch no longer applies: {e}",
                        "rule_id": cache_entry.rule_id,
                    }
                )
                # Fall through to L1
        else:
            trace.append(
                {
                    "level": 0,
                    "action": "knowledge_miss",
                    "reason": f"L0 miss for vendor {vendor_val}",
                    "drift_class": drift_class,
                    "vendor": vendor_val,
                }
            )

    # --- L1: Deterministic rules ---
    ctx = RepairContext(
        spec=spec,
        drift_class=drift_class,
        signals=signals_list,
        fingerprint_delta=fingerprint_delta,
        classification=classification,
        extra=extra_dict,
    )
    matched = matching_rules(ctx)
    if matched:
        for entry in matched:
            try:
                patch = entry.generate_patch(ctx)
            except Exception as e:
                trace.append(
                    {
                        "level": 1,
                        "rule_id": entry.id,
                        "action": "patch_error",
                        "reason": str(e),
                        "drift_class": drift_class,
                    }
                )
                continue
            # PRODUCES_PATCH == False means hypothesis/backoff, not a patch
            if not entry.produces_patch:
                # For enum case, without evidence it returns [] — we should escalate later, not propose empty
                if not patch:
                    trace.append(
                        {
                            "level": 1,
                            "rule_id": entry.id,
                            "action": "no_patch_requires_evidence",
                            "reason": f"Rule {entry.id} requires evidence — no patch produced",
                            "drift_class": drift_class,
                            "confidence": entry.confidence,
                            "verification_level": entry.verification_level,
                        }
                    )
                    continue
                # If it did produce a patch (e.g. retry adding status), propose it
                trace.append(
                    {
                        "level": 1,
                        "rule_id": entry.id,
                        "action": "propose",
                        "reason": f"Rule {entry.id} produced patch for {drift_class}",
                        "drift_class": drift_class,
                        "patch": patch,
                        "confidence": entry.confidence,
                        "verification_level": entry.verification_level,
                    }
                )
                # Validate
                try:
                    from autobridge.spec.patch import apply_patch as _apply_patch

                    _apply_patch(spec, patch)
                except Exception as e:
                    trace.append(
                        {
                            "level": 1,
                            "rule_id": entry.id,
                            "action": "patch_invalid",
                            "reason": str(e),
                        }
                    )
                    continue
                return LadderResult(
                    decision="propose",
                    ladder_level_reached=1,
                    candidate_patch=patch,
                    rule_id=entry.id,
                    verification_level=entry.verification_level,
                    model_usage=model_usage,
                    evidence_ids=evidence_ids,
                    ladder_trace=trace,
                    backoff_only=False,
                    escalation_reason=None,
                    requires_release=True,
                    confidence=entry.confidence,
                )
            # PRODUCES_PATCH True: expect non-empty patch
            if not patch:
                trace.append(
                    {
                        "level": 1,
                        "rule_id": entry.id,
                        "action": "empty_patch",
                        "reason": f"Rule {entry.id} precondition held but produced empty patch",
                        "drift_class": drift_class,
                    }
                )
                continue
            # Validate patch
            try:
                from autobridge.spec.patch import apply_patch as _apply_patch

                _apply_patch(spec, patch)
            except Exception as e:
                trace.append(
                    {
                        "level": 1,
                        "rule_id": entry.id,
                        "action": "patch_invalid",
                        "reason": str(e),
                        "rule_id_trace": entry.id,
                    }
                )
                continue
            trace.append(
                {
                    "level": 1,
                    "rule_id": entry.id,
                    "action": "propose",
                    "reason": f"Rule {entry.id} matched for {drift_class}",
                    "drift_class": drift_class,
                    "patch": patch,
                    "confidence": entry.confidence,
                    "verification_level": entry.verification_level,
                }
            )
            return LadderResult(
                decision="propose",
                ladder_level_reached=1,
                candidate_patch=patch,
                rule_id=entry.id,
                verification_level=entry.verification_level,
                model_usage=model_usage,
                evidence_ids=evidence_ids,
                ladder_trace=trace,
                backoff_only=False,
                escalation_reason=None,
                requires_release=True,
                confidence=entry.confidence,
            )
        # Matched rules but none produced valid patch -> escalate
        reason = f"L1 rules matched but none produced a valid patch for {drift_class}"
        trace.append({"level": 4, "action": "escalate", "reason": reason, "drift_class": drift_class})
        return LadderResult(
            decision="escalate",
            ladder_level_reached=4,
            candidate_patch=None,
            rule_id=None,
            verification_level=None,
            model_usage=model_usage,
            evidence_ids=evidence_ids,
            ladder_trace=trace,
            backoff_only=False,
            escalation_reason=reason,
            requires_release=False,
        )

    # No L1 rule matched -> escalate to L4
    reason = f"No L0/L1 repair for {drift_class} — escalate to L4"
    trace.append({"level": 4, "action": "escalate", "reason": reason, "drift_class": drift_class})
    return LadderResult(
        decision="escalate",
        ladder_level_reached=4,
        candidate_patch=None,
        rule_id=None,
        verification_level=None,
        model_usage=model_usage,
        evidence_ids=evidence_ids,
        ladder_trace=trace,
        backoff_only=False,
        escalation_reason=reason,
        requires_release=False,
    )
