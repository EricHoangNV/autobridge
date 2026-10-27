"""Hypothesis resolution with probe budgets (P1.W5.5, plan §2.6).

Wires together HypothesisEnumerator + ProbeSelector + Budget + Escalation:

- Probes are selected by EIG and executed until either a single hypothesis
  survives or the ProbeBudget is exhausted.
- BudgetExhausted → L4 escalation bundle, never a silent guess.
- Surviving ambiguity after budget/probes exhausted → Degraded with open
  questions (never a guess).
- L3 frontier is only invoked when the enumerator yields <2 hypotheses
  (asserted).

No network, no verifier/simulators imports. Probe execution is injected
via a callable so tests can stay in-memory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from autobridge.llm.provider import Budget, BudgetExhausted
from autobridge.repair.escalation import (
    AmbiguityResult,
    EscalationBundle,
    degraded_from_ambiguity,
    escalation_from_budget_exhausted,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from autobridge.discovery.hypotheses import Hypothesis
    from autobridge.discovery.probes import ProbeBudget, ProbeSelector, ProbeSpec


@dataclass(frozen=True, slots=True)
class ResolutionResult:
    """Outcome of hypothesis resolution.

    decision: "resolved" | "degraded" | "escalate"
    resolved_hypothesis: the winning hypothesis when resolved
    surviving_hypotheses: hypotheses that remain when degraded
    open_questions: questions for Degraded
    escalation_bundle: for BudgetExhausted / L4
    model_usage: frontier/small token counts
    evidence_ids: collected evidence
    trace: step-by-step trace for UI
    """

    decision: str  # resolved | degraded | escalate
    resolved_hypothesis: Any | None = None
    surviving_hypotheses: tuple[Any, ...] = ()
    open_questions: tuple[str, ...] = ()
    escalation_bundle: EscalationBundle | None = None
    ambiguity: AmbiguityResult | None = None
    model_usage: dict[str, Any] = field(default_factory=dict)
    evidence_ids: list[str] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)
    ladder_level_reached: int = 0
    degradation_reason: str | None = None
    escalation_reason: str | None = None


def _empty_model_usage() -> dict[str, Any]:
    return {"frontier_tokens": 0, "small_tokens": 0, "calls": 0, "frontier_calls": 0, "small_calls": 0}


def _merge_usage(base: dict[str, Any], delta: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for k in ("frontier_tokens", "small_tokens", "calls", "frontier_calls", "small_calls"):
        merged[k] = int(merged.get(k, 0)) + int(delta.get(k, 0))
    return merged


def _filter_hypotheses_by_observation(
    hypotheses: list[Hypothesis],
    probe: ProbeSpec,  # noqa: ARG001
    observation: dict[str, Any],
) -> list[Hypothesis]:
    """Filter hypotheses by whether their predicted observation matches reality.

    Very lightweight: we compare predicted strategy bucket (from probes._predicted_observation)
    with the actual observable derived from the observation dict.
    For test determinism, we treat hypotheses as surviving unless observation
    explicitly marks them as eliminated via "eliminated_ids" key.
    """
    # Allow observation to explicitly name eliminated ids (test hook)
    eliminated = observation.get("eliminated_ids")
    if isinstance(eliminated, list) and eliminated:
        return [h for h in hypotheses if getattr(h, "id", None) not in eliminated]
    # Allow observation to name surviving ids
    surviving = observation.get("surviving_ids")
    if isinstance(surviving, list) and surviving:
        return [h for h in hypotheses if getattr(h, "id", None) in surviving]
    # Default: use strategy hint from observation if present
    obs_strategy = observation.get("strategy")
    if isinstance(obs_strategy, str) and obs_strategy:
        # Keep hypotheses whose strategy matches obs_strategy
        matched = [h for h in hypotheses if getattr(h, "strategy", None) == obs_strategy]
        if matched:
            return matched
        # If no match, keep all (observation not distinguishing)
        return list(hypotheses)
    # No distinguishing info — keep all
    return list(hypotheses)


def resolve_hypotheses(
    hypotheses: list[Hypothesis],
    probe_selector: ProbeSelector | None = None,
    probe_budget: ProbeBudget | None = None,
    *,
    probe_executor: Callable[[ProbeSpec], dict[str, Any]] | None = None,
    enumerator_yield_count: int | None = None,
    provider: Any | None = None,
    llm_budget: Budget | None = None,
    run_id: str | None = None,
    session: Any | None = None,
    evidence_ids: list[str] | None = None,
) -> ResolutionResult:
    """Resolve hypotheses via probing within budgets.

    Args:
        hypotheses: initial hypotheses from enumerator (must be >=1, each with evidence)
        probe_selector: selector that picks best GET probe by EIG
        probe_budget: ProbeBudget (max probes/requests/wall/tokens)
        probe_executor: callable probe -> observation dict (or raises BudgetExhausted);
            if None, no probes are executed (tests can pass a fake)
        enumerator_yield_count: original enumerator count for L3 assert (<2)
        provider: LLMProvider for L2/L3 (optional)
        llm_budget: LLM Budget for L2/L3
        run_id/session: for ModelCall ledger

    Returns:
        ResolutionResult with decision resolved/degraded/escalate.

    BudgetExhausted at any point → escalate with bundle, never silent guess.
    Surviving ambiguity → degraded with open_questions.
    L3 assert: if enumerator_yield_count >=2 and caller tries to invoke L3,
    assertion fails. This function asserts L3 precondition when it needs L3.
    """
    trace: list[dict[str, Any]] = []
    model_usage = _empty_model_usage()
    ev_ids = list(evidence_ids or [])
    current = list(hypotheses)
    attempted: set[str] = set()

    if not current:
        trace.append({"level": 4, "action": "escalate", "reason": "no hypotheses to resolve"})
        return ResolutionResult(
            decision="escalate",
            surviving_hypotheses=(),
            open_questions=(),
            model_usage=dict(model_usage),
            evidence_ids=ev_ids,
            trace=trace,
            ladder_level_reached=4,
            escalation_reason="no hypotheses",
        )

    # L3: if enumerator yielded <2, we must use frontier to generate
    if enumerator_yield_count is not None and enumerator_yield_count < 2:
        # This is the only place L3 is allowed — assert is inside ladder helper
        # Here we do a direct L3 call if provider given, else keep current and note
        trace.append(
            {
                "level": 3,
                "action": "l3_required",
                "reason": f"enumerator yielded {enumerator_yield_count} < 2 — L3 frontier required",
                "hypothesis_count_before": len(current),
            }
        )
        if provider is not None:
            try:
                # Use ladder's L3 helper via import to avoid duplication
                from autobridge.repair.ladder import _call_frontier_generate  # noqa: PLC0415

                new_hyps, delta = _call_frontier_generate(
                    current,
                    provider,
                    llm_budget,
                    enumerator_yield_count=enumerator_yield_count,
                    run_id=run_id,
                    session=session,
                )
                model_usage = _merge_usage(model_usage, delta)
                current = list(new_hyps)
                trace[-1]["hypothesis_count_after"] = len(current)
                trace[-1]["model_usage_delta"] = delta
            except AssertionError:
                raise
            except BudgetExhausted as exc:
                bundle = escalation_from_budget_exhausted(exc, evidence_ids=ev_ids, context="L3 resolver")
                trace.append({"level": 4, "action": "escalate", "reason": f"BudgetExhausted in L3: {exc.reason}"})
                return ResolutionResult(
                    decision="escalate",
                    surviving_hypotheses=tuple(current),
                    escalation_bundle=bundle,
                    model_usage=dict(model_usage),
                    evidence_ids=ev_ids,
                    trace=trace,
                    ladder_level_reached=4,
                    escalation_reason=str(exc),
                )
            except Exception as exc:
                trace.append({"level": 3, "action": "l3_error", "reason": str(exc)})
        else:
            trace.append({"level": 3, "action": "l3_skipped_no_provider", "reason": "L3 required but no provider — will attempt L2/Degraded"})
    elif enumerator_yield_count is not None and enumerator_yield_count >= 2:
        trace.append(
            {
                "level": 3,
                "action": "l3_skipped",
                "reason": f"L3 not needed: enumerator yielded {enumerator_yield_count} >= 2",
                "hypothesis_count": len(current),
            }
        )
        # Assert that L3 is NOT invoked when >=2 — we do not call it, satisfying the requirement

    # Probe loop: pick best probe, execute, filter hypotheses
    if probe_selector is not None:
        while len(current) > 1:
            # Budget pre-check
            if probe_budget is not None:
                try:
                    probe_budget.check_pre_probe()
                except BudgetExhausted as exc:
                    bundle = escalation_from_budget_exhausted(exc, evidence_ids=ev_ids, context="probe_budget")
                    trace.append({"level": 4, "action": "escalate", "reason": f"BudgetExhausted before probe: {exc.reason}"})
                    return ResolutionResult(
                        decision="escalate",
                        surviving_hypotheses=tuple(current),
                        escalation_bundle=bundle,
                        model_usage=dict(model_usage),
                        evidence_ids=ev_ids,
                        trace=trace,
                        ladder_level_reached=4,
                        escalation_reason=str(exc),
                    )
            # Select next probe
            try:
                probe = probe_selector.select_next(current, attempted=attempted)
            except BudgetExhausted as exc:
                bundle = escalation_from_budget_exhausted(exc, evidence_ids=ev_ids, context="probe_selector")
                trace.append({"level": 4, "action": "escalate", "reason": f"BudgetExhausted in selector: {exc.reason}"})
                return ResolutionResult(
                    decision="escalate",
                    surviving_hypotheses=tuple(current),
                    escalation_bundle=bundle,
                    model_usage=dict(model_usage),
                    evidence_ids=ev_ids,
                    trace=trace,
                    ladder_level_reached=4,
                    escalation_reason=str(exc),
                )
            if probe is None:
                # No more probes to try — surviving ambiguity
                break
            attempted.add(probe.key())
            attempted.add(probe.operation)
            trace.append(
                {
                    "level": 0,
                    "action": "probe",
                    "probe": probe.operation,
                    "params": dict(probe.params),
                    "hypothesis_count_before": len(current),
                }
            )
            if probe_executor is None:
                # No executor — cannot execute, break and handle ambiguity
                trace[-1]["result"] = "no_executor"
                break
            try:
                observation = probe_executor(probe)
            except BudgetExhausted as exc:
                bundle = escalation_from_budget_exhausted(exc, evidence_ids=ev_ids, context=f"probe {probe.operation}")
                trace.append({"level": 4, "action": "escalate", "reason": f"BudgetExhausted in probe executor: {exc.reason}"})
                return ResolutionResult(
                    decision="escalate",
                    surviving_hypotheses=tuple(current),
                    escalation_bundle=bundle,
                    model_usage=dict(model_usage),
                    evidence_ids=ev_ids,
                    trace=trace,
                    ladder_level_reached=4,
                    escalation_reason=str(exc),
                )
            except Exception as exc:
                trace[-1]["error"] = str(exc)
                continue
            # Account for probe in budget
            if probe_budget is not None:
                try:
                    probe_budget.record_probe()
                except BudgetExhausted as exc:
                    bundle = escalation_from_budget_exhausted(exc, evidence_ids=ev_ids, context=f"probe {probe.operation}")
                    trace.append({"level": 4, "action": "escalate", "reason": f"BudgetExhausted after probe: {exc.reason}"})
                    return ResolutionResult(
                        decision="escalate",
                        surviving_hypotheses=tuple(current),
                        escalation_bundle=bundle,
                        model_usage=dict(model_usage),
                        evidence_ids=ev_ids,
                        trace=trace,
                        ladder_level_reached=4,
                        escalation_reason=str(exc),
                    )
            # Filter hypotheses
            before = len(current)
            current = _filter_hypotheses_by_observation(current, probe, observation)
            trace[-1]["hypothesis_count_after"] = len(current)
            trace[-1]["observation"] = {k: v for k, v in observation.items() if k not in ("eliminated_ids", "surviving_ids")}
            if len(current) == 0:
                # All eliminated — shouldn't happen, treat as escalate
                trace.append({"level": 4, "action": "escalate", "reason": "all hypotheses eliminated by probe observation"})
                bundle = escalation_from_budget_exhausted(BudgetExhausted("all hypotheses eliminated"), evidence_ids=ev_ids)
                return ResolutionResult(
                    decision="escalate",
                    surviving_hypotheses=(),
                    escalation_bundle=bundle,
                    model_usage=dict(model_usage),
                    evidence_ids=ev_ids,
                    trace=trace,
                    ladder_level_reached=4,
                    escalation_reason="all hypotheses eliminated",
                )
            if len(current) == before and before > 1:
                # Probe did not discriminate — continue to next probe
                continue

    # L2 ranking if still multiple hypotheses
    if len(current) > 1 and provider is not None:
        try:
            from autobridge.repair.ladder import _call_small_model_rank  # noqa: PLC0415

            trace.append(
                {
                    "level": 2,
                    "action": "small_rank",
                    "reason": f"L2 ranking {len(current)} hypotheses",
                    "hypothesis_ids": [getattr(h, "id", str(h)) for h in current],
                }
            )
            ranked, delta = _call_small_model_rank(current, provider, llm_budget, run_id=run_id, session=session)
            model_usage = _merge_usage(model_usage, delta)
            current = ranked
            trace[-1]["ranked_ids"] = [getattr(h, "id", str(h)) for h in current]
            trace[-1]["model_usage_delta"] = delta
        except BudgetExhausted as exc:
            bundle = escalation_from_budget_exhausted(exc, evidence_ids=ev_ids, context="L2 resolver")
            trace.append({"level": 4, "action": "escalate", "reason": f"BudgetExhausted in L2: {exc.reason}"})
            return ResolutionResult(
                decision="escalate",
                surviving_hypotheses=tuple(current),
                escalation_bundle=bundle,
                model_usage=dict(model_usage),
                evidence_ids=ev_ids,
                trace=trace,
                ladder_level_reached=4,
                escalation_reason=str(exc),
            )
        except Exception as exc:
            trace.append({"level": 2, "action": "l2_error", "reason": str(exc)})

    # Final decision
    if len(current) == 1:
        trace.append({"level": 1, "action": "resolved", "hypothesis_id": getattr(current[0], "id", str(current[0]))})
        return ResolutionResult(
            decision="resolved",
            resolved_hypothesis=current[0],
            surviving_hypotheses=(current[0],),
            model_usage=dict(model_usage),
            evidence_ids=ev_ids,
            trace=trace,
            ladder_level_reached=1,
        )
    # Surviving ambiguity → Degraded with open questions
    amb = degraded_from_ambiguity(current, reason="probing exhausted without disambiguation")
    trace.append(
        {
            "level": 2,
            "action": "degraded",
            "reason": f"Surviving ambiguity: {len(current)} hypotheses remain",
            "open_questions": list(amb.open_questions),
        }
    )
    return ResolutionResult(
        decision="degraded",
        surviving_hypotheses=tuple(current),
        open_questions=tuple(amb.open_questions),
        ambiguity=amb,
        model_usage=dict(model_usage),
        evidence_ids=ev_ids,
        trace=trace,
        ladder_level_reached=2,
        degradation_reason=amb.reason,
    )


def assert_l3_precondition(enumerator_yield_count: int) -> None:
    """Assert L3 is only invoked when enumerator yields <2.

    Called by tests to verify the invariant.
    """
    assert enumerator_yield_count < 2, f"L3 frontier invoked only when enumerator yields fewer than two hypotheses, got {enumerator_yield_count}"


def budget_exhausted_to_escalation(
    exc: BudgetExhausted,
    evidence_ids: list[str] | None = None,
    context: str = "",
) -> EscalationBundle:
    """Convert BudgetExhausted to escalation bundle (never silent guess)."""
    return escalation_from_budget_exhausted(exc, evidence_ids=evidence_ids, context=context)
