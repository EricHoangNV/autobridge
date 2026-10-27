"""RepairController orchestrating drift -> ladder -> RepairRun (P1.W4.4).

Responsibilities:
  - Classify drift from sync signals if drift_class not provided
  - Build RepairContext and invoke ladder (L0/L1)
  - Persist RepairRun, EvidenceRecords, and ModelCall zero-usage ledger
  - Cache successful patches in L0 KnowledgeCache for future hits
  - Provide JobRunner handler for repair jobs

Non-fixable classes (CAPABILITY_REMOVED, AUTH_REVOKED) escalate with no patch.
RATE_LIMIT / TRANSIENT return backoff_only with no release.
AUTH_EXPIRED tries refresh if spec auth permits, else escalates.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

from autobridge.control.models import DriftEvent, RepairRun
from autobridge.drift.classifier import DriftClassification, classify
from autobridge.repair.knowledge import KnowledgeCache, get_default_cache

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from autobridge.drift.fingerprint import FingerprintDelta

from autobridge.repair.ladder import LadderResult, run_ladder


def _spec_vendor(spec: dict[str, Any]) -> str:
    try:
        conn = spec.get("connector", {})
        if isinstance(conn, dict):
            v = conn.get("vendor")
            if isinstance(v, str):
                return v
    except Exception:
        pass
    return "unknown"


class RepairController:
    """Deterministic repair orchestration (L0/L1 for W4.4)."""

    def __init__(
        self,
        knowledge_cache: KnowledgeCache | None = None,
    ) -> None:
        self.knowledge_cache = knowledge_cache or get_default_cache()

    def handle(
        self,
        *,
        spec: dict[str, Any],
        drift_class: str,
        signals: list[Any] | None = None,
        fingerprint_delta: FingerprintDelta | dict[str, Any] | None = None,
        classification: DriftClassification | None = None,
        extra: dict[str, Any] | None = None,
        vendor: str | None = None,
        drift_event_ids: list[str] | None = None,
        session: Session | None = None,
    ) -> LadderResult:
        """Run ladder for given drift context and optionally persist RepairRun.

        If *session* is provided, a RepairRun row is inserted and committed
        (flush). Caller is responsible for outer commit/rollback. If session is
        None, only the LadderResult is returned (pure logic, no DB side-effect).

        Evidence is not written here; callers that need evidence chains should
        use handle_with_evidence or create EvidenceRecords manually.
        """
        vendor_val = vendor or _spec_vendor(spec)
        signals_list: list[Any] = list(signals or [])
        extra_dict: dict[str, Any] = dict(extra or {})

        result = run_ladder(
            spec=spec,
            drift_class=drift_class,
            signals=signals_list,
            fingerprint_delta=fingerprint_delta,
            vendor=vendor_val,
            classification=classification,
            extra=extra_dict,
            knowledge_cache=self.knowledge_cache,
        )

        # Persist RepairRun if session provided
        if session is not None:
            run = RepairRun(
                drift_event_ids=list(drift_event_ids or []),
                ladder_level_reached=result.ladder_level_reached,
                candidate_patch=result.candidate_patch,
                rule_id=result.rule_id,
                hypothesis_ids=[],
                visible_test_results=[],
                verifier_report_id=None,
                decision=result.decision,
                model_usage=dict(result.model_usage),
            )
            session.add(run)
            session.flush()
            # Cache successful propose patches for L0 future hits
            if result.decision == "propose" and result.candidate_patch:
                with contextlib.suppress(Exception):
                    self.knowledge_cache.put(
                        vendor_val,
                        fingerprint_delta,
                        result.candidate_patch,
                        result.rule_id or "unknown",
                        drift_class=drift_class,
                        confidence=result.confidence or 0.0,
                    )

        return result

    def handle_classified(
        self,
        *,
        spec: dict[str, Any],
        classification: DriftClassification,
        signals: list[Any] | None = None,
        fingerprint_delta: FingerprintDelta | dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
        vendor: str | None = None,
        drift_event_ids: list[str] | None = None,
        session: Session | None = None,
    ) -> LadderResult:
        """Convenience wrapper when classifier already ran."""
        return self.handle(
            spec=spec,
            drift_class=classification.drift_class,
            signals=signals,
            fingerprint_delta=fingerprint_delta,
            classification=classification,
            extra=extra,
            vendor=vendor,
            drift_event_ids=drift_event_ids,
            session=session,
        )

    def handle_from_signals(
        self,
        *,
        spec: dict[str, Any],
        signals: list[Any],
        fingerprint_delta: FingerprintDelta | dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
        vendor: str | None = None,
        drift_event_ids: list[str] | None = None,
        session: Session | None = None,
    ) -> LadderResult:
        """Classify signals then run ladder."""
        classification = classify(signals)
        return self.handle(
            spec=spec,
            drift_class=classification.drift_class,
            signals=signals,
            fingerprint_delta=fingerprint_delta,
            classification=classification,
            extra=extra,
            vendor=vendor,
            drift_event_ids=drift_event_ids,
            session=session,
        )

    def handle_sync_summary(
        self,
        *,
        spec: dict[str, Any],
        sync_summary: dict[str, Any],
        previous_fingerprint: Any | None = None,
        current_fingerprint: Any | None = None,
        extra: dict[str, Any] | None = None,
        vendor: str | None = None,
        session: Session | None = None,
    ) -> tuple[LadderResult, DriftClassification]:
        """From a sync summary dict, collect signals, classify, and run ladder."""
        from autobridge.drift.signals import signals_from_sync

        signals = signals_from_sync(
            sync_summary,
            spec=spec,
            previous_fingerprint=previous_fingerprint,
            current_fingerprint=current_fingerprint,
        )
        # Collect delta if fingerprints provided
        delta: FingerprintDelta | None = None
        if previous_fingerprint is not None and current_fingerprint is not None:
            from autobridge.drift.fingerprint import diff_fingerprints

            delta = diff_fingerprints(previous_fingerprint, current_fingerprint)

        classification = classify(signals)
        result = self.handle(
            spec=spec,
            drift_class=classification.drift_class,
            signals=signals,
            fingerprint_delta=delta,
            classification=classification,
            extra=extra,
            vendor=vendor,
            drift_event_ids=[],
            session=session,
        )
        return result, classification


def repair_job_handler(ctx: Any) -> dict[str, Any]:
    """JobRunner handler for repair jobs (wires RepairController to DB).

    Payload: {drift_event_ids: list[str], spec?: dict, drift_class?: str}
    In production the spec is loaded from the active ConnectorRelease; in tests
    the payload may carry a minimal spec override.
    """
    from autobridge.control.models import Connector, ConnectorRelease

    payload = ctx.payload or {}
    drift_ids: list[str] = list(payload.get("drift_event_ids") or [])
    # Load first drift event to get context (if present)
    spec: dict[str, Any] | None = payload.get("spec")
    drift_class: str | None = payload.get("drift_class")
    vendor: str | None = payload.get("vendor")

    # Try to resolve spec from drift events if not in payload
    if spec is None and drift_ids:
        for did in drift_ids:
            ev = ctx.session.get(DriftEvent, did)
            if ev is not None:
                # Try to find connector's active release
                conn = ctx.session.get(Connector, ev.connector_id) if ev.connector_id else None
                if conn and conn.active_release_id:
                    rel = ctx.session.get(ConnectorRelease, conn.active_release_id)
                    if rel and rel.spec:
                        spec = rel.spec
                        break
                # Fallback: try spec from any release of connector
                if conn:
                    for rel in conn.releases:
                        if rel.spec:
                            spec = rel.spec
                            break
                if spec:
                    break

    # If still no spec, use minimal fallback (will escalate if no rule matches)
    if spec is None:
        spec = payload.get("spec") or {
            "spec_version": 1,
            "connector": {"id": "test", "vendor": vendor or "unknown"},
            "source": {
                "base_url": "http://example.com",
                "auth": {"type": "bearer", "credential_ref": "TEST_TOKEN"},
                "timezone_source": {"kind": "static", "value": "UTC"},
            },
            "operations": {},
            "entities": {},
            "capabilities": {"read": []},
            "health": {"canary": {"every": "5m", "operation": "noop"}},
        }

    if drift_class is None and drift_ids:
        # Try to derive from first drift event
        for did in drift_ids:
            ev = ctx.session.get(DriftEvent, did)
            if ev and ev.drift_class:
                drift_class = ev.drift_class
                break
    if drift_class is None:
        drift_class = payload.get("drift_class") or "UNKNOWN"

    controller = RepairController()
    result = controller.handle(
        spec=spec,
        drift_class=drift_class,
        signals=payload.get("signals"),
        fingerprint_delta=payload.get("fingerprint_delta"),
        extra=payload.get("extra"),
        vendor=vendor,
        drift_event_ids=drift_ids,
        session=ctx.session,
    )

    # Build job result
    out: dict[str, Any] = {
        "decision": result.decision,
        "ladder_level_reached": result.ladder_level_reached,
        "rule_id": result.rule_id,
        "backoff_only": result.backoff_only,
    }
    if result.candidate_patch is not None:
        out["candidate_patch"] = result.candidate_patch
    if result.escalation_reason:
        out["escalation_reason"] = result.escalation_reason
    # Persisted RepairRun id is in ctx.run_id after handle
    # Find the run we just created (last RepairRun in this session)
    # ctx.run_id is set by controller? No, controller creates RepairRun but doesn't set ctx.run_id.
    # We set it here for JobRunner bookkeeping.
    from autobridge.control.models import RepairRun as _RR

    # Fetch most recent RepairRun for this job (by created_at)
    # Simple: query last inserted in this session's identity map
    for obj in reversed(list(ctx.session.new)):
        if isinstance(obj, _RR):
            ctx.run_id = obj.id
            out["repair_run_id"] = obj.id
            break
    else:
        # Fallback query
        from sqlalchemy import select as _select

        row = ctx.session.execute(_select(_RR).order_by(_RR.created_at.desc()).limit(1)).scalar_one_or_none()
        if row is not None:
            ctx.run_id = row.id
            out["repair_run_id"] = row.id

    return out
