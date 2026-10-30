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

    Format stored on ``RepairRun.escalation_bundle`` (JSON):
        {
            "reason": "CAPABILITY_REMOVED: ...",
            "question": "Which alternative endpoint should replace ...?",
            "evidence_ids": ["ev-1", ...],
            "suggested_action": "manual review",
            "evidence_payloads": []  # optional redacted payloads
        }
    ``question`` is always a single precise question ending with '?'.

    Attributes:
        reason: machine-readable reason (e.g. "BudgetExhausted: max_probes").
        question: single precise question for a human operator.
        evidence_ids: ids of evidence records that support the question.
        evidence_payloads: optional redacted payloads for the bundle.
        suggested_action: hint for the operator (e.g. "increase budget").
        proposal: the model's proposed change for the human decision, if
            the model offered one (REQ-049): ``{"label", "effect",
            "patch"}`` — label is short button text, effect says what
            approving does in one sentence, patch is RFC 6902 ops. The
            engine never synthesises this; it only carries the model's own
            proposal. ``None`` for a plain escalation.
    """

    reason: str
    question: str
    evidence_ids: tuple[str, ...] = ()
    evidence_payloads: tuple[dict[str, Any], ...] = ()
    suggested_action: str = ""
    proposal: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-safe dict for DB storage."""
        out: dict[str, Any] = {
            "reason": self.reason,
            "question": self.question,
            "evidence_ids": list(self.evidence_ids),
            "evidence_payloads": list(self.evidence_payloads),
            "suggested_action": self.suggested_action,
        }
        if self.proposal is not None:
            out["proposal"] = dict(self.proposal)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EscalationBundle:
        """Deserialize from DB dict (validates one-question contract)."""
        reason = str(data.get("reason", ""))
        question = str(data.get("question", ""))
        if not reason:
            raise ValueError("EscalationBundle reason must be non-empty")
        if not question.strip():
            raise ValueError("EscalationBundle question must be non-empty")
        if not question.strip().endswith("?"):
            raise ValueError("EscalationBundle question must end with '?'")
        # One precise question: count '?' - allow one, plus optional trailing
        if question.count("?") != 1:
            raise ValueError("EscalationBundle question must be exactly one question (single '?')")
        evidence_ids = tuple(str(x) for x in (data.get("evidence_ids") or []))
        evidence_payloads = tuple(dict(x) for x in (data.get("evidence_payloads") or []))
        suggested_action = str(data.get("suggested_action", ""))
        proposal: dict[str, Any] | None = None
        raw_proposal = data.get("proposal")
        if raw_proposal is not None:
            # REQ-049: the model's proposed change rides the bundle. It is
            # validated like the wire shape (label/effect/patch) and never
            # synthesised here — from_dict only carries it.
            if not isinstance(raw_proposal, dict):
                raise ValueError("EscalationBundle proposal must be an object")
            label = raw_proposal.get("label")
            effect = raw_proposal.get("effect")
            patch = raw_proposal.get("patch")
            if not isinstance(label, str) or not label.strip():
                raise ValueError("EscalationBundle proposal needs a label")
            if not isinstance(effect, str) or not effect.strip():
                raise ValueError("EscalationBundle proposal needs an effect")
            if not isinstance(patch, list) or not patch:
                raise ValueError("EscalationBundle proposal needs patch ops")
            proposal = {
                "label": " ".join(str(label).split())[:120],
                "effect": " ".join(str(effect).split())[:300],
                "patch": [dict(op) for op in patch if isinstance(op, dict)],
            }
        return cls(
            reason=reason,
            question=question,
            evidence_ids=evidence_ids,
            evidence_payloads=evidence_payloads,
            suggested_action=suggested_action,
            proposal=proposal,
        )


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
    question = f"Budget exhausted: {reason}{ctx} — should the budget be increased or should this be manually resolved?"
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
    proposal: dict[str, Any] | None = None,
) -> EscalationBundle:
    """Build escalation when no L0/L1 fix exists (non-budget path).

    *proposal* (REQ-049) is the model's own proposed change for the
    human decision (``{"label", "effect", "patch"}``), carried verbatim —
    the engine never synthesises one. ``None`` for a plain escalation.
    """
    q = question or f"No deterministic repair for: {reason}. Human review required — which mapping or permission change is correct?"
    return EscalationBundle(
        reason=reason,
        question=q,
        evidence_ids=tuple(evidence_ids or []),
        evidence_payloads=(),
        suggested_action="manual review",
        proposal=dict(proposal) if proposal is not None else None,
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
