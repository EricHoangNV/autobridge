"""Escalation bundle for L4 (P1.W5.5, plan §2.5, W6.3).

BudgetExhausted never produces a silent guess — it produces an escalation
bundle with one precise question for a human plus the evidence collected so
far. Surviving ambiguity after probing yields Degraded with open questions.

This module defines the bundle shape and helpers. Storage on RepairRun
(DB) is completed in W6.3; here the bundle is an in-memory value that the
ladder and resolver return.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autobridge.llm.provider import BudgetExhausted


@dataclass(frozen=True, slots=True)
class EscalationBundle:
    """Evidence bundle + one precise question for a human (L4).

    Attributes:
        reason: machine-readable reason (e.g. "BudgetExhausted: max_probes").
        question: single precise question for a human operator.
        evidence_ids: ids of evidence records that support the question.
        evidence_payloads: optional redacted payloads for the bundle.
        suggested_action: hint for the operator (e.g. "increase budget").
    """

    reason: str
    question: str
    evidence_ids: tuple[str, ...] = ()
    evidence_payloads: tuple[dict[str, Any], ...] = ()
    suggested_action: str = ""


def escalation_from_budget_exhausted(
    exc: BudgetExhausted,
    *,
    evidence_ids: list[str] | None = None,
    context: str = "",
) -> EscalationBundle:
    """Build an escalation bundle from a BudgetExhausted exception.

    Never guesses — always asks for human input.
    """
    reason = exc.reason if hasattr(exc, "reason") else str(exc)
    ctx = f" ({context})" if context else ""
    question = f"Budget exhausted: {reason}{ctx}. How should the run proceed? Provide additional budget or approve manual resolution."
    # Keep question precise and single-sentence for W6.3 contract
    # For probe budget, ask about increasing probe ceiling; for LLM budget, ask about token ceiling.
    if "probe" in reason.lower() or "request" in reason.lower() or "wall" in reason.lower():
        question = f"Probe budget exhausted ({reason}){ctx}: cannot disambiguate hypotheses without additional probes. Should the probe budget be increased or should a human choose the mapping?"
    elif "token" in reason.lower() or "call" in reason.lower():
        question = f"Model budget exhausted ({reason}){ctx}: L2/L3 model call would exceed budget. Should the token/call budget be increased or should this drift be escalated for manual review?"
    return EscalationBundle(
        reason=reason,
        question=question,
        evidence_ids=tuple(evidence_ids or []),
        evidence_payloads=(),
        suggested_action="increase budget or manually resolve",
    )


def escalation_for_no_fix(
    reason: str,
    *,
    evidence_ids: list[str] | None = None,
    question: str | None = None,
) -> EscalationBundle:
    """Build escalation when no L0/L1 fix exists (non-budget path)."""
    q = question or f"No deterministic repair for: {reason}. Human review required — which mapping or permission change is correct?"
    return EscalationBundle(
        reason=reason,
        question=q,
        evidence_ids=tuple(evidence_ids or []),
        evidence_payloads=(),
        suggested_action="manual review",
    )


@dataclass(frozen=True, slots=True)
class AmbiguityResult:
    """Surviving ambiguity after probing — maps to SyncRun Degraded.

    Attributes:
        open_questions: one question per unresolved ambiguity family.
        surviving_hypothesis_ids: ids of hypotheses that remain viable.
        reason: why ambiguity could not be resolved.
    """

    open_questions: tuple[str, ...]
    surviving_hypothesis_ids: tuple[str, ...] = ()
    reason: str = ""


def degraded_from_ambiguity(
    surviving_hypotheses: list[Any],
    *,
    reason: str = "",
) -> AmbiguityResult:
    """Build Degraded open-questions from surviving hypotheses.

    Each surviving hypothesis family contributes one open question.
    """
    # Group by family
    by_family: dict[str, list[Any]] = {}
    for h in surviving_hypotheses:
        fam = getattr(h, "family", "unknown")
        by_family.setdefault(fam, []).append(h)
    questions: list[str] = []
    for family, hyps in sorted(by_family.items()):
        strat_list = ", ".join(sorted({getattr(h, "strategy", "?") or "?" for h in hyps}))
        questions.append(f"Ambiguous {family}: surviving strategies [{strat_list}] — which is correct?")
    if not questions:
        questions.append("Ambiguous mapping: cannot determine correct field semantics from docs and probes alone.")
    return AmbiguityResult(
        open_questions=tuple(questions),
        surviving_hypothesis_ids=tuple(getattr(h, "id", "") for h in surviving_hypotheses),
        reason=reason or "probing exhausted without disambiguation",
    )
