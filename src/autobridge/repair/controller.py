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
from autobridge.repair.escalation import escalation_for_no_fix
from autobridge.repair.knowledge import (
    NO_LEARN_CLASSES,
    KnowledgeCache,
    delta_has_content,
    delta_signature,
    get_default_cache,
)

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


def _signal_evidence_ids(signals_list: list[Any]) -> list[str]:
    """Collect evidence ids from signals (same rule as the ladder)."""
    evidence_ids: list[str] = []
    seen: set[str] = set()
    for sig in signals_list:
        eids: list[str] | None = None
        if isinstance(sig, dict):
            eids = sig.get("evidence_ids")
        elif hasattr(sig, "evidence_ids"):
            eids = getattr(sig, "evidence_ids", None)
        if eids:
            for eid in eids:
                if isinstance(eid, str) and eid not in seen:
                    seen.add(eid)
                    evidence_ids.append(eid)
    return evidence_ids


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
        # REQ-021 semantic climb: when *provider* is wired, a semantic drift
        # climbs L0 -> L1 -> L2 -> L3 via run_frontier_climb and then stops.
        # The controller never releases an L3 proposal: SEMANTIC_VALUE_DRIFT
        # without independent evidence escalates (plan §1.1), so the climb
        # always ends here with decision "escalate" and one operator
        # question; the verifier/trap verdict on the proposal is the
        # caller's job (demo, bench, matrix test). Without a provider the
        # deterministic run_ladder path below is used unchanged.
        hypotheses: list[Any] | None = None,
        enumerator_yield_count: int | None = None,
        provider: Any | None = None,
        budget: Any | None = None,
        scenario: str | None = None,
        validate_against: dict[str, Any] | None = None,
        run_id: str | None = None,
        # REQ-024 ablation: disable built-in knowledge (L0 + L1) so only the
        # model (L2/L3) can repair. The ladder, verifier gate and budgets
        # are kept. Defaults preserve existing behavior exactly.
        enable_knowledge: bool = True,
        enable_rules: bool = True,
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

        if provider is not None:
            return self._handle_semantic_climb(
                spec=spec,
                drift_class=drift_class,
                signals_list=signals_list,
                fingerprint_delta=fingerprint_delta,
                extra_dict=extra_dict,
                vendor_val=vendor_val,
                drift_event_ids=drift_event_ids,
                session=session,
                hypotheses=hypotheses,
                enumerator_yield_count=enumerator_yield_count,
                provider=provider,
                budget=budget,
                scenario=scenario,
                validate_against=validate_against,
                run_id=run_id,
                enable_knowledge=enable_knowledge,
                enable_rules=enable_rules,
            )

        result = run_ladder(
            spec=spec,
            drift_class=drift_class,
            signals=signals_list,
            fingerprint_delta=fingerprint_delta,
            vendor=vendor_val,
            classification=classification,
            extra=extra_dict,
            knowledge_cache=self.knowledge_cache,
            hypotheses=hypotheses,
            enumerator_yield_count=enumerator_yield_count,
            provider=provider,
            budget=budget,
            session=session,
            run_id=run_id,
            enable_knowledge=enable_knowledge,
            enable_rules=enable_rules,
        )

        # Persist RepairRun if session provided
        if session is not None:
            bundle_dict = result.escalation_bundle.to_dict() if result.escalation_bundle else None
            run = RepairRun(
                drift_event_ids=list(drift_event_ids or []),
                ladder_level_reached=result.ladder_level_reached,
                vendor=vendor_val,
                drift_signature=delta_signature(fingerprint_delta),
                candidate_patch=result.candidate_patch,
                rule_id=result.rule_id,
                hypothesis_ids=[],
                visible_test_results=[],
                verifier_report_id=None,
                decision=result.decision,
                model_usage=dict(result.model_usage),
                ladder_trace=list(result.ladder_trace),
                escalation_bundle=bundle_dict,
            )
            session.add(run)
            session.flush()
            # Cache successful propose patches for L0 future hits. A None or
            # empty delta has no content to key on (every such drift would
            # share one constant key), so it is never cached: without an
            # observed change the ladder re-derives from signals every time
            # instead of risking a wrong-patch L0 hit. Transport/auth/
            # capability classes never populate L0 either (REQ-028).
            if (
                result.decision == "propose"
                and result.candidate_patch
                and fingerprint_delta is not None
                and drift_class not in NO_LEARN_CLASSES
                and delta_has_content(fingerprint_delta)
            ):
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

    def _handle_semantic_climb(
        self,
        *,
        spec: dict[str, Any],
        drift_class: str,
        signals_list: list[Any],
        fingerprint_delta: FingerprintDelta | dict[str, Any] | None,
        extra_dict: dict[str, Any],
        vendor_val: str,
        drift_event_ids: list[str] | None,
        session: Session | None,
        hypotheses: list[Any] | None,
        enumerator_yield_count: int | None,
        provider: Any,
        budget: Any | None,
        scenario: str | None,
        validate_against: dict[str, Any] | None,
        run_id: str | None,
        enable_knowledge: bool = True,
        enable_rules: bool = True,
    ) -> LadderResult:
        """Climb L0 -> L1 -> L2 -> L3 for a semantic drift, then escalate.

        Wraps :func:`run_frontier_climb` so the controller path records the
        same full climb the demo and the matrix pin ([0, 1, 2, 3, 4]): every
        rung tried with its one-line reason, the L3 frontier call in the
        usage ledger, and an L4 escalation with one operator question. An
        L3 proposal is never released here — it is recorded on the rung and
        left for the caller (verifier/trap check), never placed in
        ``candidate_patch``.
        """
        import uuid as _uuid  # noqa: PLC0415 - keep module import cost down

        from autobridge.repair.ladder import run_frontier_climb  # noqa: PLC0415

        active_run_id = run_id or f"repair-{_uuid.uuid4().hex[:12]}"
        # AR-4-3 / REQ-036: never fabricate the L3 gate count. The gate on
        # L3 is the enumerator's yield — the surviving hypotheses after wire
        # filtering — so derive it from the hypotheses actually passed in.
        # An explicit caller count still wins.
        surviving = list(hypotheses or [])
        if enumerator_yield_count is None:
            enumerator_yield_count = len(surviving)
        climb = run_frontier_climb(
            spec=spec,
            drift_class=drift_class,
            hypotheses=surviving,
            enumerator_yield_count=enumerator_yield_count,
            provider=provider,
            budget=budget,
            session=session,
            run_id=active_run_id,
            knowledge_cache=self.knowledge_cache,
            fingerprint_delta=fingerprint_delta,
            extra=extra_dict,
            vendor=vendor_val,
            scenario=scenario,
            validate_against=validate_against,
            enable_knowledge=enable_knowledge,
            enable_rules=enable_rules,
        )
        if climb.get("resolved_rung") == 0:
            # L0 knew this drift: fall back to the deterministic path, which
            # replays the cached patch as a normal L0 propose.
            return self.handle(
                spec=spec,
                drift_class=drift_class,
                signals=signals_list,
                fingerprint_delta=fingerprint_delta,
                extra=extra_dict,
                vendor=vendor_val,
                drift_event_ids=drift_event_ids,
                session=session,
                enable_knowledge=enable_knowledge,
                enable_rules=enable_rules,
            )

        # Normalize rung-keyed climb attempts to level-keyed ladder trace
        # entries (what rung_ledger and the repairs UI read).
        trace: list[dict[str, Any]] = []
        for attempt in climb.get("attempts", []):
            if not isinstance(attempt, dict):
                continue
            entry = dict(attempt)
            rung = entry.pop("rung", None)
            if isinstance(rung, int) and not isinstance(rung, bool):
                entry["level"] = rung
            trace.append(entry)
        l3_entry = next((e for e in trace if e.get("level") == 3), None)
        if l3_entry is not None and climb.get("proposal") is not None:
            # Record the actual L3 proposal on its rung (JSON-safe list):
            # callers (matrix, demo) check the verifier/trap verdict on it.
            # It is never placed in candidate_patch: nothing here is released.
            l3_entry["proposal"] = list(climb["proposal"])
        scenario_name = scenario or drift_class
        if l3_entry is not None and l3_entry.get("action") == "proposal_rejected":
            l4_reason = (
                f"L3 proposal rejected: {l3_entry.get('reason', 'does not apply')} "
                f"({scenario_name}); no patch released — one operator question"
            )
        elif l3_entry is not None and (climb.get("proposal") or l3_entry.get("proposal_ops")):
            l4_reason = (
                f"L3 proposed a patch for {scenario_name} with no independent "
                "evidence; semantic drift is never auto-patched — one operator question"
            )
        elif l3_entry is not None and l3_entry.get("action") == "l3_skipped":
            l4_reason = f"{l3_entry.get('reason', 'L3 skipped')} ({scenario_name}); escalate"
        else:
            l4_reason = (
                f"No verified fix for {scenario_name} below L4 "
                "(semantic drift needs the operator)"
            )
        trace.append(
            {"level": 4, "action": "escalate", "reason": l4_reason, "drift_class": drift_class}
        )
        bundle = escalation_for_no_fix(
            f"{scenario_name}: semantic drift, no verified fix",
            evidence_ids=_signal_evidence_ids(signals_list),
            question=f"{scenario_name}: no verified fix for this semantic drift — which mapping is correct?",
        )
        model_usage = dict(climb.get("usage", {}))
        result = LadderResult(
            decision="escalate",
            ladder_level_reached=4,
            candidate_patch=None,
            rule_id=None,
            verification_level=None,
            model_usage=model_usage,
            evidence_ids=_signal_evidence_ids(signals_list),
            ladder_trace=trace,
            backoff_only=False,
            escalation_reason=l4_reason,
            escalation_bundle=bundle,
            requires_release=False,
        )
        if session is not None:
            run = RepairRun(
                drift_event_ids=list(drift_event_ids or []),
                ladder_level_reached=result.ladder_level_reached,
                vendor=vendor_val,
                drift_signature=delta_signature(fingerprint_delta),
                candidate_patch=result.candidate_patch,
                rule_id=result.rule_id,
                hypothesis_ids=[],
                visible_test_results=[],
                verifier_report_id=None,
                decision=result.decision,
                model_usage=dict(result.model_usage),
                ladder_trace=list(result.ladder_trace),
                escalation_bundle=bundle.to_dict(),
            )
            session.add(run)
            session.flush()
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
    if result.escalation_bundle is not None:
        out["escalation_bundle"] = result.escalation_bundle.to_dict()
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
