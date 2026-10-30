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
from dataclasses import replace as _replace
from typing import TYPE_CHECKING, Any

from autobridge.control.models import DriftEvent, RepairRun
from autobridge.drift.classifier import DriftClassification, classify
from autobridge.repair.escalation import escalation_for_no_fix
from autobridge.repair.knowledge import (
    KnowledgeCache,
    delta_signature,
    delta_to_json,
    get_default_cache,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from autobridge.drift.fingerprint import FingerprintDelta

from autobridge.repair.ladder import LadderResult, repair_frontier_budget, run_ladder


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


def _hypothesis_ids(hypotheses: list[Any] | None) -> list[str]:
    """Best-effort hypothesis ids for RepairRun attribution (REQ-030 item 3).

    Hypothesis objects carry ``id``; dicts may carry ``"id"``. Anything else
    is stringified so no considered hypothesis is silently dropped.
    """
    ids: list[str] = []
    for hyp in hypotheses or []:
        hid: Any = hyp.get("id") if isinstance(hyp, dict) else getattr(hyp, "id", None)
        text = str(hid) if hid is not None else str(hyp)
        if text and text not in ids:
            ids.append(text)
    return ids


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


def _existing_evidence_ids(session: Any, candidate_ids: list[str]) -> list[str]:
    """Keep only the candidate ids that exist as evidence rows.

    The store rejects unknown parents, and fast-path ladder signals are
    often never persisted as records — linking them would fail the whole
    append, so the derivation keeps the signal ids in ``payload`` shape
    only via stages and links what is really there.
    """
    if not candidate_ids:
        return []
    try:
        from sqlalchemy import select as _select  # noqa: PLC0415

        from autobridge.control.models import EvidenceRecord  # noqa: PLC0415

        rows = session.execute(
            _select(EvidenceRecord.id).where(EvidenceRecord.id.in_(candidate_ids))
        ).scalars().all()
        found = set(rows)
        return [cid for cid in candidate_ids if cid in found]
    except Exception:
        return []

def trace_from_attempts(attempts: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Normalize rung-keyed climb attempts to level-keyed ladder trace entries.

    The same normalization the semantic climb applies to its final write —
    the repair page reads ``ladder_trace`` levels, and the running row must
    show the rungs so far while the climb is still in flight (REQ-043).
    """
    trace: list[dict[str, Any]] = []
    for attempt in attempts or []:
        if not isinstance(attempt, dict):
            continue
        entry = dict(attempt)
        rung = entry.pop("rung", None)
        if isinstance(rung, int) and not isinstance(rung, bool):
            entry["level"] = rung
        trace.append(entry)
    return trace


def current_rung_level(trace: list[dict[str, Any]] | None) -> int:
    """Highest ladder level in a (possibly partial) trace; 0 when empty."""
    best = 0
    for entry in trace or []:
        if not isinstance(entry, dict):
            continue
        level = entry.get("level")
        if isinstance(level, int) and not isinstance(level, bool) and level > best:
            best = level
    return best


def _finalize_repair_row(
    session: Any,
    *,
    result: Any,
    existing_repair_run_id: str | None,
    drift_event_ids: list[str],
    vendor: str | None,
    fingerprint_delta: Any | None,
    drift_class: str | None,
    hypotheses: list[Any] | None,
) -> Any:
    """Fill the repair row for a finished climb (REQ-043).

    When *existing_repair_run_id* names the ``climbing`` row the repair job
    created at start, that same row is filled in place (decision, patch,
    trace, usage, bundle) — never a second row. Otherwise a new row is
    inserted, as before. Returns the row; the caller flushes.
    """
    bundle_dict = result.escalation_bundle.to_dict() if result.escalation_bundle else None
    fields: dict[str, Any] = {
        "ladder_level_reached": result.ladder_level_reached,
        "vendor": vendor,
        "drift_signature": delta_signature(fingerprint_delta),
        "drift_class": drift_class,
        "fingerprint_delta": delta_to_json(fingerprint_delta),
        "candidate_patch": result.candidate_patch,
        "rule_id": result.rule_id,
        "hypothesis_ids": _hypothesis_ids(hypotheses),
        # A finished climb owns these columns: the judge (verifier verdict)
        # and the candidate release write them after this, never before —
        # resetting keeps a retried climb from showing a stale verdict.
        "visible_test_results": [],
        "verifier_report_id": None,
        # REQ-048 item 6: the row carries the frontier budget that applies
        # to it (same source as the L3 cap), so the repair page shows the
        # real budget instead of a hard-coded default.
        "model_usage": {**dict(result.model_usage), "frontier_budget": repair_frontier_budget()},
        "ladder_trace": list(result.ladder_trace),
        "escalation_bundle": bundle_dict,
        "decision": result.decision,
    }
    run: Any = None
    if existing_repair_run_id:
        try:
            run = session.get(RepairRun, existing_repair_run_id)
        except Exception:
            run = None
    if run is None:
        run = RepairRun(
            drift_event_ids=list(drift_event_ids or []),
            **fields,
        )
        session.add(run)
    else:
        for key, value in fields.items():
            setattr(run, key, value)
    session.flush()
    return run


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
        # REQ-039: real observed evidence for the L3 prompt (spec excerpt +
        # failing-records sample, redacted caller-side blocks carried as
        # untrusted data) and the persisted funnel stages. Both default to
        # the previous behavior (drift-class-only prompt, single "distinct"
        # stage) so existing callers are unaffected.
        evidence: dict[str, Any] | None = None,
        hypothesis_stages: list[dict[str, Any]] | None = None,
        # REQ-024 ablation: disable built-in knowledge (L0 + L1) so only the
        # model (L2/L3) can repair. The ladder, verifier gate and budgets
        # are kept. Defaults preserve existing behavior exactly.
        enable_knowledge: bool = True,
        enable_rules: bool = True,
        # REQ-043: live progress + pre-created row. *existing_repair_run_id*
        # names the ``climbing`` row the repair job created at start; the
        # final write fills that same row instead of inserting a second one
        # (REQ-040 threading stays the only link). *on_rung* streams rung
        # snapshots to the job handler, which commits them to the row
        # between rungs — never across a model call.
        on_rung: Any | None = None,
        existing_repair_run_id: str | None = None,
    ) -> LadderResult:
        """Run ladder for given drift context and optionally persist RepairRun.

        If *session* is provided, a RepairRun row is inserted and committed
        (flush) — or, when *existing_repair_run_id* names the job's running
        row, that same row is filled in place (no second row). Caller is
        responsible for outer commit/rollback. If session is None, only the
        LadderResult is returned (pure logic, no DB side-effect).

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
                evidence=evidence,
                hypothesis_stages=hypothesis_stages,
                enable_knowledge=enable_knowledge,
                enable_rules=enable_rules,
                on_rung=on_rung,
                existing_repair_run_id=existing_repair_run_id,
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
            run = _finalize_repair_row(
                session,
                result=result,
                existing_repair_run_id=existing_repair_run_id,
                drift_event_ids=list(drift_event_ids or []),
                vendor=vendor_val,
                fingerprint_delta=fingerprint_delta,
                drift_class=drift_class,
                hypotheses=hypotheses,
            )
            # The row this climb persisted — callers link candidates and
            # verdicts through it, never by re-fetching "the newest row"
            # (concurrent repairs would cross-link, REQ-040/REQ-042).
            # LadderResult is frozen: rebind instead of assigning.
            result = _replace(result, repair_run_id=run.id)
            # REQ-030 2b: proposing caches nothing. A proposal that is still
            # waiting can never be reused at L0, by any connector — the fix
            # is learned only once it is approved (engine approve path), so
            # this walk deliberately leaves the knowledge cache untouched.
            # REQ-030 item 3 + REQ-032 §2: the hypothesis derivation persists
            # as a kind=hypothesis evidence record owned by this repair run,
            # with the stages that actually ran. A ladder that resolved at
            # L0/L1 from wire signals never enumerated hypotheses, so the
            # record says exactly that (one zero-count stage) instead of
            # inventing a funnel. Best-effort: never breaks the decision.
            try:
                from autobridge.evidence.store import EvidenceStore  # noqa: PLC0415

                _store = EvidenceStore(session)
                _hyp_ids = _hypothesis_ids(hypotheses)
                if _hyp_ids:
                    _stages: list[dict[str, Any]] = [
                        {
                            "name": "distinct",
                            "count": len(_hyp_ids),
                            "ids": _hyp_ids,
                            "source": (
                                "hypotheses passed to the climb "
                                "(engine-enumerated or caller-supplied)"
                            ),
                        }
                    ]
                else:
                    _stages = [
                        {
                            "name": "resolved without enumeration",
                            "count": 0,
                            "ids": [],
                            "source": (
                                f"ladder resolved at L{result.ladder_level_reached} "
                                "from wire signals; no hypotheses enumerated"
                            ),
                        }
                    ]
                if enumerator_yield_count is not None and _hyp_ids:
                    _stages.insert(
                        0,
                        {
                            "name": "generated",
                            "count": int(enumerator_yield_count),
                            "ids": [],
                            "source": "enumerator yield count (ids not retained)",
                        },
                    )
                _store.append(
                    kind="hypothesis",
                    payload={
                        "drift_class": drift_class,
                        "enumerator_yield_count": enumerator_yield_count,
                        "stages": _stages,
                    },
                    run_id=f"repair_runs:{run.id}",
                    # Parents must already exist as rows (store rule):
                    # link only the signal evidence ids that were
                    # persisted, never ids nobody stored.
                    parent_ids=_existing_evidence_ids(
                        session, _signal_evidence_ids(signals_list)
                    ),
                )
                session.flush()
            except Exception:
                pass

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
        evidence: dict[str, Any] | None = None,
        hypothesis_stages: list[dict[str, Any]] | None = None,
        enable_knowledge: bool = True,
        enable_rules: bool = True,
        on_rung: Any | None = None,
        existing_repair_run_id: str | None = None,
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

        # REQ-043: when the repair job pre-created the running row, the
        # climb's ModelCalls link to it directly — no post-hoc relink —
        # and the final write fills that same row.
        active_run_id = (
            existing_repair_run_id or run_id or f"repair-{_uuid.uuid4().hex[:12]}"
        )
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
            evidence=evidence,
            enable_knowledge=enable_knowledge,
            enable_rules=enable_rules,
            on_rung=on_rung,
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
                on_rung=on_rung,
                existing_repair_run_id=existing_repair_run_id,
            )

        # Normalize rung-keyed climb attempts to level-keyed ladder trace
        # entries (what rung_ledger and the repairs UI read).
        trace = trace_from_attempts(climb.get("attempts"))
        l3_entry = next((e for e in trace if e.get("level") == 3), None)
        if l3_entry is not None and climb.get("proposal") is not None:
            # Record the actual L3 proposal on its rung (JSON-safe list):
            # callers (matrix, demo) check the verifier/trap verdict on it.
            # It is never placed in candidate_patch: nothing here is released.
            l3_entry["proposal"] = list(climb["proposal"])
        scenario_name = scenario or drift_class
        model_question = str(climb.get("model_question") or "")
        model_rationale = " ".join(str(climb.get("model_rationale") or "").split())[:300]
        model_escalated = bool(climb.get("model_escalated"))
        if model_escalated and not model_question:
            model_escalated = False
        if l3_entry is not None and l3_entry.get("action") == "model_escalation" and model_question:
            # REQ-044 item 3: the model explicitly asked for a human
            # decision — its question resolves L4, labelled as the model's,
            # and counts as a valid L3 outcome, not an error.
            from autobridge.repair.ladder import normalize_model_question  # noqa: PLC0415

            model_question = normalize_model_question(model_question)
            l4_reason = (
                f"L3 asked for a human decision ({scenario_name}): {model_question}"
                + (f" (model rationale: {model_rationale})" if model_rationale else "")
            )
        elif l3_entry is not None and l3_entry.get("action") == "proposal_rejected":
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
        # REQ-047 item 2: the model's question IS the escalation question,
        # with its rationale beneath (in the bundle reason the repair page
        # renders under the question); the canned text is the fallback only.
        _bundle_reason = f"{scenario_name}: semantic drift, no verified fix"
        if model_escalated and model_rationale:
            _bundle_reason += f"; model rationale: {model_rationale}"
        bundle = escalation_for_no_fix(
            _bundle_reason,
            evidence_ids=_signal_evidence_ids(signals_list),
            question=(
                model_question
                if model_escalated and model_question
                else f"{scenario_name}: no verified fix for this semantic drift — which mapping is correct?"
            ),
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
            run = _finalize_repair_row(
                session,
                result=result,
                existing_repair_run_id=existing_repair_run_id,
                drift_event_ids=list(drift_event_ids or []),
                vendor=vendor_val,
                fingerprint_delta=fingerprint_delta,
                drift_class=drift_class,
                hypotheses=surviving,
            )
            # The row this climb persisted — callers link candidates and
            # verdicts through it, never by re-fetching "the newest row"
            # (concurrent repairs would cross-link, REQ-040/REQ-042).
            # LadderResult is frozen: rebind instead of assigning.
            result = _replace(result, repair_run_id=run.id)
            # REQ-030 item 3: the hypothesis derivation persists as a
            # kind=hypothesis evidence record owned by this repair run
            # (counts and ids per stage), and the climb's ModelCall rows —
            # written under the climb's run id — are re-linked to the
            # repair row so the ledger endpoint serves them. Failures are
            # row data or best-effort: the decision above already stands.
            try:
                from autobridge.evidence.store import EvidenceStore  # noqa: PLC0415

                store = EvidenceStore(session)
                store.append(
                    kind="hypothesis",
                    payload={
                        "drift_class": drift_class,
                        "enumerator_yield_count": enumerator_yield_count,
                        # REQ-039: the caller (live path) passes the full
                        # funnel stages (generated → amount →
                        # wire_consistent → distinct); callers that only hand
                        # in hypotheses keep the previous single-stage shape.
                        "stages": list(hypothesis_stages)
                        if hypothesis_stages is not None
                        else [
                            {
                                "name": "distinct",
                                "count": len(surviving),
                                "ids": _hypothesis_ids(surviving),
                                "source": (
                                    "hypotheses passed to the climb "
                                    "(engine-enumerated or caller-supplied)"
                                ),
                            }
                        ],
                    },
                    run_id=f"repair_runs:{run.id}",
                    parent_ids=_signal_evidence_ids(signals_list),
                )
                session.flush()
            except Exception:
                pass
            try:
                from sqlalchemy import select as _select  # noqa: PLC0415

                from autobridge.control.models import ModelCall as _ModelCall  # noqa: PLC0415

                for call in (
                    session.execute(
                        _select(_ModelCall).where(_ModelCall.run_id == active_run_id)
                    )
                    .scalars()
                    .all()
                ):
                    call.run_id = run.id
                session.flush()
            except Exception:
                pass
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


def prepare_semantic_inputs(
    *,
    spec: dict[str, Any],
    signals: list[Any],
    summary: dict[str, Any] | None,
    sync_run_id: str | None,
) -> dict[str, Any]:
    """Derive the semantic climb's inputs from the wire (REQ-039/REQ-042).

    Pure and fast: engine enumeration of amount readings from the spec,
    wire-consistency filter against the observed invariant outcome, the
    climbed reading plus siblings, the persisted funnel stages, and the
    redacted L3 evidence. No provider, no model call, no DB write — the
    sync path runs this inline, then either climbs immediately (replay /
    no-provider) or queues it for the background repair job (live).
    """
    from autobridge.repair import semantic as _semantic  # noqa: PLC0415

    source = f"sync_runs:{sync_run_id}" if sync_run_id else "live-sync"
    enumerated, _fields = _semantic.enumerate_money_hypotheses(spec, source=source)
    failed = _semantic.money_invariant_failed(summary, signals)
    survivors = _semantic.filter_wire_consistent(enumerated, invariant_failed=failed)
    climbed, siblings = _semantic.select_climbed(survivors)
    stages = _semantic.funnel_stages(enumerated, survivors, climbed, siblings, source=source)
    evidence = _semantic.build_l3_evidence(spec, summary, climbed)
    return {
        "climbed": climbed,
        "siblings": list(siblings),
        "stages": stages,
        "evidence": evidence or {},
    }


def run_semantic_repair(
    *,
    spec: dict[str, Any],
    drift_class: str,
    signals: list[Any],
    fingerprint_delta: Any | None,
    summary: dict[str, Any] | None,
    sync_run_id: str | None,
    vendor: str | None,
    drift_event_ids: list[str],
    session: Any,
    knowledge_cache: Any | None = None,
    scenario: str | None = None,
    verifier_url: str | None = None,
    # REQ-043: when the repair job pre-created the running row, thread it
    # through so the final write fills that same row and rung snapshots
    # stream to it while the climb runs.
    on_rung: Any | None = None,
    existing_repair_run_id: str | None = None,
) -> tuple[LadderResult, dict[str, Any]]:
    """Climb a semantic drift the way the live repair path does (REQ-039).

    Enumerates the engine's own amount readings from the wire signals and
    the spec (:mod:`autobridge.repair.semantic` — no docs, no answer keys),
    filters them against the observed outcome, resolves the L2/L3 provider
    the same way everywhere (``resolve_frontier_provider``: live key goes
    live, a recording replays labelled with its capture date, neither
    records an explicit L3 skip), and climbs via
    :meth:`RepairController.handle`. An L3 proposal is judged against the
    verifier (baseline regression) and recorded — never released — then the
    climb escalates with one operator question. The live repair row carries
    the derivation stages, the model call(s) and the rejected proposal.

    Returns ``(result, info)`` where info names ``provider_mode``
    (``live``/``replay``/``off``), ``l3_status`` (``attempt``/``skip`` plus
    the reason), the ``proposal`` when one was recorded, and the verifier
    ``verdict`` when it was judged. Never raises for provider or verifier
    trouble: a misconfigured live mode degrades to ``off``, an unreachable
    verifier is recorded as evidence, and the     escalation always stands.
    """
    controller = (
        RepairController(knowledge_cache=knowledge_cache)
        if knowledge_cache is not None
        else RepairController()
    )
    prepared = prepare_semantic_inputs(
        spec=spec,
        signals=signals,
        summary=summary,
        sync_run_id=sync_run_id,
    )
    climbed = prepared["climbed"]
    stages = prepared["stages"]
    evidence = prepared["evidence"]
    hyps: list[Any] = [climbed] if climbed is not None else []

    from autobridge.llm.provider import Budget, resolve_frontier_provider  # noqa: PLC0415

    try:
        provider, mode = resolve_frontier_provider()
    except Exception:
        # Misconfigured live mode (e.g. FRONTIER_MODE=live without a key)
        # must never lose the drift: climb with no provider so every rung
        # records its reason and the run still escalates.
        provider, mode = None, "off"
    result = controller.handle(
        spec=spec,
        drift_class=drift_class,
        signals=signals,
        fingerprint_delta=fingerprint_delta,
        vendor=vendor,
        drift_event_ids=drift_event_ids,
        session=session,
        hypotheses=hyps,
        provider=provider,
        # REQ-048 item 6: the repair's frontier budget is the L3 cap (one
        # source of truth) — a legitimate L3 attempt can exhaust it only
        # by genuinely exceeding the cap, and BudgetExhausted escalates.
        budget=Budget(max_frontier_tokens=repair_frontier_budget()),
        scenario=scenario,
        validate_against=spec,
        evidence=evidence or None,
        hypothesis_stages=stages,
        on_rung=on_rung,
        existing_repair_run_id=existing_repair_run_id,
    )
    info: dict[str, Any] = {"provider_mode": mode, "stages": stages}
    l3_entry = next(
        (
            e
            for e in (result.ladder_trace or [])
            if isinstance(e, dict) and e.get("level") == 3
        ),
        None,
    )
    if l3_entry is None:
        info["l3_status"] = "skip: no L3 rung (no provider configured)"
    elif l3_entry.get("action") in ("frontier_generate", "proposal_rejected", "model_escalation"):
        info["l3_status"] = "attempt"
    else:
        info["l3_status"] = f"skip: {l3_entry.get('reason', 'L3 skipped')}"
    proposal = None
    if isinstance(l3_entry, dict):
        proposal = l3_entry.get("proposal") or None
    info["proposal"] = proposal
    info["verdict"] = _judge_semantic_proposal(
        session=session,
        spec=spec,
        proposal=proposal,
        verifier_url=verifier_url,
        repair_run_id=result.repair_run_id,
    )
    return result, info


def _judge_semantic_proposal(
    *,
    session: Any,
    spec: dict[str, Any],
    proposal: list[dict[str, Any]] | None,
    verifier_url: str | None,
    repair_run_id: str | None = None,
) -> dict[str, Any]:
    """Judge an L3 proposal against the verifier; record it, never release it.

    Applies the proposal to a copy of the spec and runs the baseline
    regression gate. The verdict (or the reason it could not be judged:
    no proposal, no verifier, unreachable verifier) lands as a
    kind=verification evidence record on the repair row plus the
    row's ``visible_test_results`` (what the repair page's verifier
    section reads). The row is addressed by *repair_run_id* — never by
    re-fetching "the newest row" (concurrent repairs would cross-link,
    REQ-040/REQ-042). Failures are row data — the escalation stands either
    way. Returns a small verdict summary for the caller.
    """
    from autobridge.control.models import RepairRun as _RepairRun  # noqa: PLC0415

    newest: Any = None
    try:
        if repair_run_id:
            newest = session.get(_RepairRun, repair_run_id)
    except Exception:
        newest = None
    if newest is None:
        return {"judged": False, "reason": "no repair row to attach the verdict to"}
    if not proposal:
        return {"judged": False, "reason": "no L3 proposal to judge"}
    if verifier_url is None:
        import os as _os  # noqa: PLC0415

        verifier_url = (_os.environ.get("VERIFIER_URL", "") or "").strip() or None
    checks: list[dict[str, Any]] = []
    verdict_summary: dict[str, Any] = {"judged": False}
    if not verifier_url:
        verdict_summary = {"judged": False, "reason": "no verifier configured; proposal recorded, never released"}
    else:
        try:
            from autobridge.engine.verification import check_regression_gate  # noqa: PLC0415
            from autobridge.spec.patch import apply_patch  # noqa: PLC0415

            patched = apply_patch(spec, proposal)
            allowed, outcome = check_regression_gate(patched, "baseline", verifier_url)
            failed = list((outcome.failed_checks if outcome else []) or [])
            checks = [
                {
                    "name": str(f.get("name", "check")) if isinstance(f, dict) else str(f),
                    "passed": False,
                    "detail": str(f.get("detail", ""))[:300] if isinstance(f, dict) else "",
                }
                for f in failed
            ]
            if allowed:
                checks = [
                    {
                        "name": "baseline regression",
                        "passed": True,
                        "detail": "patched spec passes the baseline gate",
                    }
                ]
            verdict_summary = {
                "judged": True,
                "allowed": bool(allowed),
                "verdict": outcome.verdict if outcome else "unknown",
                "failed_checks": [c["name"] for c in checks if not c["passed"]],
            }
        except Exception as exc:  # noqa: BLE001 — transport/mechanics are row data
            verdict_summary = {"judged": False, "reason": f"verifier unreachable: {exc}"[:300]}
    try:
        from autobridge.evidence.store import EvidenceStore  # noqa: PLC0415

        store = EvidenceStore(session)
        store.append(
            kind="verification",
            payload={
                "scenario": None,
                "verdict": verdict_summary,
                "proposal": list(proposal),
                "checks": checks,
                "recorded_via": "live semantic climb (engine-derived, never released)",
            },
            run_id=f"repair_runs:{newest.id}",
            parent_ids=[],
        )
        session.flush()
    except Exception:
        pass
    try:
        newest.visible_test_results = list(checks)
        session.flush()
    except Exception:
        pass
    return verdict_summary


def _start_running_repair_row(
    session: Any,
    *,
    drift_event_ids: list[str],
    drift_class: str | None,
    vendor: str | None,
    fingerprint_delta: Any | None,
    job_id: str | None,
) -> Any | None:
    """Create (or reuse, on job retry) the running RepairRun row (REQ-043).

    The row starts at decision ``climbing`` with the drift link, so the
    Repairs list and the repair page are reachable while the climb runs.
    The job's ``run_id`` is pointed at it at once, so a crash, a poll, or a
    restart can always find the row from the job. Best-effort: returns
    ``None`` when the row cannot be created (the final write still lands
    the decision on its own row).
    """
    try:
        from autobridge.control.models import Job as _Job  # noqa: PLC0415

        job = session.get(_Job, job_id) if job_id else None
        if job is not None and job.run_id:
            try:
                existing = session.get(RepairRun, job.run_id)
            except Exception:
                existing = None
            if existing is not None and existing.decision in ("climbing", "failed"):
                existing.decision = "climbing"
                trace = list(existing.ladder_trace or [])
                trace.append(
                    {
                        "level": current_rung_level(trace),
                        "action": "restarted",
                        "reason": f"repair job {job_id} retrying the climb",
                    }
                )
                existing.ladder_trace = trace
                session.commit()
                return existing
        row = RepairRun(
            drift_event_ids=list(drift_event_ids or []),
            ladder_level_reached=0,
            vendor=vendor,
            drift_signature=delta_signature(fingerprint_delta),
            drift_class=drift_class,
            fingerprint_delta=delta_to_json(fingerprint_delta),
            candidate_patch=None,
            rule_id=None,
            hypothesis_ids=[],
            visible_test_results=[],
            verifier_report_id=None,
            decision="climbing",
            # REQ-048 item 6: the running row already names the budget that
            # applies, so the page shows it while the climb is in flight.
            model_usage={"frontier_budget": repair_frontier_budget()},
            ladder_trace=[],
            escalation_bundle=None,
        )
        session.add(row)
        session.flush()
        if job is not None:
            job.run_id = row.id
        session.commit()
        return row
    except Exception:
        with contextlib.suppress(Exception):
            session.rollback()
        return None


def fail_orphaned_repairs(
    session: Any,
    *,
    reason: str = "control restarted while the repair was running",
) -> int:
    """Mark every still-``climbing`` repair ``failed`` (REQ-043 item 4).

    A control restart reclaims orphaned ``running`` jobs back to ``queued``;
    their repair rows would otherwise sit at ``repairing`` forever. The
    retried job resets its row to ``climbing`` when it restarts. Returns
    the number of rows marked.
    """
    try:
        from sqlalchemy import select as _select  # noqa: PLC0415

        rows = (
            session.execute(_select(RepairRun).where(RepairRun.decision == "climbing"))
            .scalars()
            .all()
        )
    except Exception:
        return 0
    count = 0
    for row in rows:
        try:
            trace = list(row.ladder_trace or [])
            trace.append(
                {
                    "level": current_rung_level(trace),
                    "action": "failed",
                    "reason": reason[:500],
                }
            )
            row.ladder_trace = trace
            row.decision = "failed"
            count += 1
        except Exception:
            continue
    try:
        session.commit()
    except Exception:
        with contextlib.suppress(Exception):
            session.rollback()
        return 0
    return count


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
        # REQ-042: a deferred repair names the faulted release — load it
        # directly (releases are immutable, so this is the exact spec the
        # sync ran, even if the operator approved another release since).
        release_id = payload.get("release_id")
        if isinstance(release_id, str) and release_id:
            rel = ctx.session.get(ConnectorRelease, release_id)
            if rel is not None and rel.spec:
                spec = rel.spec
        for did in drift_ids:
            if spec is not None:
                break
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

    # REQ-043: the RepairRun row exists from the start of the climb — not
    # after it. Create it here (decision "climbing"), commit at once, and
    # update the same row as the climb moves rung by rung (each update
    # commits before any model call runs, so no transaction is ever held
    # across a call — REQ-042 still holds). The final write fills this
    # same row; there is never a second row for the job (REQ-040).
    running_row = _start_running_repair_row(
        ctx.session,
        drift_event_ids=drift_ids,
        drift_class=drift_class,
        vendor=vendor,
        fingerprint_delta=payload.get("fingerprint_delta"),
        job_id=getattr(ctx, "job_id", None),
    )
    running_row_id: str | None = running_row.id if running_row is not None else None
    if running_row_id is not None:
        ctx.run_id = running_row_id

    def _progress(
        climb_attempts: list[dict[str, Any]], climb_usage: dict[str, Any]
    ) -> None:
        """Write rung snapshots to the running row (REQ-043 item 1)."""
        if running_row_id is None:
            return
        try:
            row = ctx.session.get(RepairRun, running_row_id)
            if row is None or row.decision != "climbing":
                return  # finalized or gone; stop emitting
            trace = trace_from_attempts(climb_attempts)
            row.ladder_trace = trace
            row.model_usage = dict(climb_usage or {})
            row.ladder_level_reached = current_rung_level(trace)
            ctx.session.commit()
        except Exception:
            with contextlib.suppress(Exception):
                ctx.session.rollback()

    def _fail_running_row(reason: str) -> None:
        """Leave a crashed/timed-out climb in a terminal state (REQ-043 item 4)."""
        if running_row_id is None:
            return
        try:
            row = ctx.session.get(RepairRun, running_row_id)
            if row is None:
                return
            trace = list(row.ladder_trace or [])
            trace.append(
                {
                    "level": current_rung_level(trace),
                    "action": "failed",
                    "reason": reason[:500],
                }
            )
            row.ladder_trace = trace
            row.decision = "failed"
            ctx.session.commit()
        except Exception:
            with contextlib.suppress(Exception):
                ctx.session.rollback()

    controller = RepairController()
    assert spec is not None  # noqa: S101 — the fallback above guarantees a spec
    try:
        return _run_repair_climb(
            ctx,
            controller=controller,
            spec=spec,
            drift_class=drift_class,
            vendor=vendor,
            drift_ids=drift_ids,
            payload=payload,
            running_row_id=running_row_id,
            on_rung=_progress,
        )
    except Exception as exc:
        _fail_running_row(f"{type(exc).__name__}: {exc}")
        raise


def _run_repair_climb(
    ctx: Any,
    *,
    controller: RepairController,
    spec: dict[str, Any],
    drift_class: str,
    vendor: str | None,
    drift_ids: list[str],
    payload: dict[str, Any],
    running_row_id: str | None,
    on_rung: Any | None,
) -> dict[str, Any]:
    """Climb the ladder for a repair job and build its result (REQ-043).

    The running RepairRun row already exists (created at job start);
    *running_row_id* threads it through the climb so the final write fills
    that same row, and *on_rung* streams rung snapshots to it while the
    climb runs. ModelCalls link to the running row directly.
    """
    if drift_class == "SEMANTIC_VALUE_DRIFT":
        # REQ-039: the worker repair path climbs the same semantic climb
        # as the sync-inline path (funnel + L3 attempt + verifier judge),
        # never the deterministic 0-call escalation.
        if isinstance(payload.get("hypotheses_json"), list) and payload.get("hypotheses_json"):
            # REQ-042 deferred repair: the sync already derived the funnel
            # (enumeration, wire filter, climbed reading, evidence) and
            # queued it in the job payload. Rebuild the same objects and
            # climb with the live provider — no model call ran in the
            # request, and none is re-derived from a label here.
            from autobridge.discovery.hypotheses import hypothesis_from_json  # noqa: PLC0415
            from autobridge.drift.signals import signal_from_json  # noqa: PLC0415
            from autobridge.llm.provider import Budget, resolve_frontier_provider  # noqa: PLC0415

            parcel_signals: list[Any] = []
            for raw_sig in payload.get("signals_json") or []:
                try:
                    parcel_signals.append(
                        signal_from_json(raw_sig) if isinstance(raw_sig, dict) else raw_sig
                    )
                except Exception:
                    continue
            parcel_hyps: list[Any] = []
            for raw_hyp in payload.get("hypotheses_json") or []:
                try:
                    if isinstance(raw_hyp, dict):
                        parcel_hyps.append(hypothesis_from_json(raw_hyp))
                except Exception as exc:
                    raise ValueError(f"repair job payload carries no usable hypothesis: {exc}") from exc
            if not parcel_hyps:
                raise ValueError("repair job payload carries no usable hypothesis")
            try:
                provider, mode = resolve_frontier_provider()
            except Exception:
                provider, mode = None, "off"
            result = controller.handle(
                spec=spec,
                drift_class=drift_class,
                signals=parcel_signals,
                fingerprint_delta=payload.get("fingerprint_delta"),
                vendor=vendor,
                drift_event_ids=drift_ids,
                session=ctx.session,
                hypotheses=parcel_hyps,
                provider=provider,
                # REQ-048 item 6: same repair budget as the live path — the
                # L3 cap, one source of truth (see above).
                budget=Budget(max_frontier_tokens=repair_frontier_budget()),
                scenario=payload.get("scenario"),
                validate_against=spec,
                evidence=payload.get("l3_evidence") or None,
                hypothesis_stages=payload.get("hypothesis_stages"),
                on_rung=on_rung,
                existing_repair_run_id=running_row_id,
            )
            info: dict[str, Any] = {"provider_mode": mode}
            l3_entry = next(
                (
                    e
                    for e in (result.ladder_trace or [])
                    if isinstance(e, dict) and e.get("level") == 3
                ),
                None,
            )
            if l3_entry is None:
                info["l3_status"] = "skip: no L3 rung (no provider configured)"
            elif l3_entry.get("action") in ("frontier_generate", "proposal_rejected"):
                info["l3_status"] = "attempt"
            else:
                info["l3_status"] = f"skip: {l3_entry.get('reason', 'L3 skipped')}"
            proposal = l3_entry.get("proposal") if isinstance(l3_entry, dict) else None
            info["proposal"] = proposal
            assert spec is not None  # noqa: S101 — fallback above guarantees a spec
            info["verdict"] = _judge_semantic_proposal(
                session=ctx.session,
                spec=spec,
                proposal=proposal if isinstance(proposal, list) else None,
                verifier_url=None,
                repair_run_id=result.repair_run_id,
            )
        else:
            result, info = run_semantic_repair(
                spec=spec,
                drift_class=drift_class,
                signals=list(payload.get("signals") or []),
                fingerprint_delta=payload.get("fingerprint_delta"),
                summary=payload.get("summary"),
                sync_run_id=payload.get("sync_run_id"),
                vendor=vendor,
                drift_event_ids=drift_ids,
                session=ctx.session,
                on_rung=on_rung,
                existing_repair_run_id=running_row_id,
            )
    else:
        info = {}
        result = controller.handle(
            spec=spec,
            drift_class=drift_class,
            signals=payload.get("signals"),
            fingerprint_delta=payload.get("fingerprint_delta"),
            extra=payload.get("extra"),
            vendor=vendor,
            drift_event_ids=drift_ids,
            session=ctx.session,
            on_rung=on_rung,
            existing_repair_run_id=running_row_id,
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
    if info.get("provider_mode"):
        out["provider_mode"] = info["provider_mode"]
    if info.get("l3_status"):
        out["l3_status"] = info["l3_status"]
    # The RepairRun row this climb persisted, threaded through
    # LadderResult — never re-fetched as "the newest row" (concurrent
    # repairs would cross-link, REQ-040/REQ-042).
    if result.repair_run_id is not None:
        ctx.run_id = result.repair_run_id
        out["repair_run_id"] = result.repair_run_id

    return out
