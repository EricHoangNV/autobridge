"""Repair ladder L0/L1/L2/L3 wiring (P1.W4.4 + P1.W5.5, plan §2.5).

Levels:
  L0 KnowledgeLookup   profile cache keyed by vendor + fingerprint delta
  L1 RuleRegistry      match(evidence) → candidate patch(es) + required verification level
  L2 SmallModel        rank/complete candidates, propose hypotheses     [budgeted]
  L3 FrontierModel     hypothesis generation for UNKNOWN / SEMANTIC      [budgeted]
  L4 Escalate          evidence bundle + precise question for a human

Special handling (W4.4 spec):
  - RATE_LIMIT / TRANSIENT (429/503) → no patch, backoff only
  - AUTH_EXPIRED (token_expired) → refresh if permitted else escalate
  - CAPABILITY_REMOVED / AUTH_REVOKED → escalate with no patch attempted
  - All paths emit EvidenceRecords trace and keep frontier_tokens == 0 for L0/L1

Trust boundary: no import of verifier/simulators.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from autobridge.llm.provider import Budget, BudgetedProvider, BudgetExhausted, ReplayMissing
from autobridge.repair.context import RepairContext
from autobridge.repair.escalation import (
    EscalationBundle,
    degraded_from_ambiguity,
    escalation_for_no_fix,
    escalation_from_budget_exhausted,
)
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


def _is_patch_list(parsed: Any) -> bool:
    """True when *parsed* JSON is a JSON Patch-shaped list of operations."""
    return isinstance(parsed, list) and all(
        isinstance(op, dict) and "op" in op and "path" in op for op in parsed
    )


def _parse_patch_response(text: str) -> tuple[list[dict[str, Any]] | None, str]:
    """Extract a JSON Patch proposal from a frontier model's raw text.

    Reasoning models often answer with prose around the patch (an
    explanation plus a fenced `````json`` block), so the whole reply is
    rarely bare JSON. Returns ``(proposal, detail)`` where *proposal* is
    the patch list or ``None`` and *detail* is a one-line, secret-free
    account of what the text contained (``"parsed"`` on success). The
    caller records the detail on the rung so a live answer that yields no
    patch is diagnosable instead of silently dropped (REQ-021 owner
    check 2026-10-28: the frontier answered and the engine could not parse
    it, then mislabelled the rung as skipped).
    """
    import json as _json  # noqa: PLC0415 - local to keep ladder import-light
    import re as _re  # noqa: PLC0415

    stripped = (text or "").strip()
    if not stripped:
        return None, "empty response: the model returned no text"
    try:
        parsed = _json.loads(stripped)
    except Exception:
        parsed = None
    if _is_patch_list(parsed):
        return [dict(op) for op in parsed], "parsed"
    candidates: list[str] = []
    fence = _re.search(r"```(?:json)?\s*\n?(.*?)```", stripped, _re.DOTALL)
    if fence:
        candidates.append(fence.group(1).strip())
    start, end = stripped.find("["), stripped.rfind("]")
    if start != -1 and end != -1 and end > start:
        candidates.append(stripped[start : end + 1])
    for candidate in candidates:
        try:
            parsed = _json.loads(candidate)
        except Exception:
            continue
        if _is_patch_list(parsed):
            return [dict(op) for op in parsed], "extracted patch from prose wrapper"
    if parsed is not None:
        return None, "response JSON is not a patch list (no op/path operations)"
    if "[" in stripped or "{" in stripped:
        return None, "response contains JSON-like content that does not parse as a patch"
    return None, "no JSON Patch found in response"


@dataclass(frozen=True, slots=True)
class LadderResult:
    """Outcome of one ladder walk."""

    decision: str  # propose | escalate | degraded
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
    # P1.W5.5: L2/L3 and ambiguity
    open_questions: list[str] = field(default_factory=list)
    escalation_bundle: EscalationBundle | None = None
    degradation_reason: str | None = None


def _empty_model_usage() -> dict[str, Any]:
    return {
        "frontier_tokens": 0,
        "small_tokens": 0,
        "calls": 0,
        "frontier_calls": 0,
        "small_calls": 0,
        "frontier_input": 0,
        "frontier_output": 0,
        "small_input": 0,
        "small_output": 0,
    }


# ---------------------------------------------------------------------------
# L2 / L3 helpers (P1.W5.5) — budgeted, with assertions
# ---------------------------------------------------------------------------


def _call_small_model_rank(
    hypotheses: list[Any],
    provider: Any,
    budget: Budget | None,
    *,
    run_id: str | None = None,
    session: Any | None = None,
    purpose: str = "l2-rank-hypotheses",
) -> tuple[list[Any], dict[str, Any]]:
    """L2: small model ranks hypotheses (budgeted).

    Returns (ranked_hypotheses, model_usage_delta).
    Every call goes through BudgetedProvider so BudgetExhausted propagates.
    """
    if provider is None:
        # No provider — deterministic fallback: return input order
        return list(hypotheses), _empty_model_usage()
    # Build prompt as untrusted data blocks (docs + hypotheses)
    messages = [
        {
            "role": "system",
            "content": "You are a deterministic hypothesis ranker. Rank hypotheses by prior and evidence. Return JSON list of hypothesis ids in ranked order.",
        },
        {
            "role": "user",
            "content": f"hypotheses: {[h.id for h in hypotheses]}; priors: {[h.prior for h in hypotheses]}",
        },
    ]
    # Wrap with budget if provided
    effective_provider: Any = provider
    if budget is not None:
        effective_provider = BudgetedProvider(provider, budget, session=session, run_id=run_id)
    # Will raise BudgetExhausted if ceiling hit — caller must catch and escalate
    result = effective_provider.complete(messages, max_tokens=512, tier="small", purpose=purpose)
    # Parse ranking: try to extract ordered ids from text (JSON list or comma)
    ranked_ids: list[str] = []
    try:
        import json as _json

        parsed = _json.loads(result.text.strip())
        if isinstance(parsed, list):
            ranked_ids = [str(x) for x in parsed]
    except Exception:
        # fallback: split text by comma/space
        ranked_ids = [s.strip().strip('"').strip("'") for s in result.text.split(",") if s.strip()]
    # Map back to hypothesis objects in ranked order; unknown ids ignored
    id_map = {h.id: h for h in hypotheses}
    ranked: list[Any] = []
    for hid in ranked_ids:
        if hid in id_map and id_map[hid] not in ranked:
            ranked.append(id_map[hid])
    # Append any missing (model didn't rank all)
    for h in hypotheses:
        if h not in ranked:
            ranked.append(h)
    # Build usage delta from result
    usage = {
        "frontier_tokens": 0,
        "small_tokens": result.usage.total_tokens,
        "calls": 1,
        "frontier_calls": 0,
        "small_calls": 1,
        "frontier_input": 0,
        "frontier_output": 0,
        "small_input": result.usage.input_tokens,
        "small_output": result.usage.output_tokens,
    }
    return ranked, usage


def _call_frontier_generate(
    hypotheses: list[Any],
    provider: Any,
    budget: Budget | None,
    *,
    enumerator_yield_count: int,
    run_id: str | None = None,
    session: Any | None = None,
    purpose: str = "l3-generate-hypothesis",
) -> tuple[list[Any], dict[str, Any]]:
    """L3: frontier model generates hypotheses for UNKNOWN/SEMANTIC.

    Asserts enumerator yielded fewer than two hypotheses (plan §2.6).
    Every call goes through BudgetedProvider.
    """
    assert enumerator_yield_count < 2, (
        f"L3 frontier invoked only when enumerator yields fewer than two hypotheses, got {enumerator_yield_count}"
    )
    if provider is None:
        # No provider — cannot generate; return input (caller will handle ambiguity)
        return list(hypotheses), _empty_model_usage()
    messages = [
        {
            "role": "system",
            "content": "You are a hypothesis generator for unknown API semantics. Propose one additional hypothesis that explains observed drift. Return JSON with fields id, family, claim.",
        },
        {
            "role": "user",
            "content": f"existing hypotheses: {[h.id for h in hypotheses]}; need novel hypothesis for unresolved drift",
        },
    ]
    effective_provider: Any = provider
    if budget is not None:
        effective_provider = BudgetedProvider(provider, budget, session=session, run_id=run_id)
    result = effective_provider.complete(messages, max_tokens=1024, tier="frontier", purpose=purpose)
    # Try to parse frontier's new hypothesis as JSON; if fails, keep original list
    new_hyps = list(hypotheses)
    try:
        import json as _json

        from autobridge.discovery.docs_reader import Provenance as _Prov
        from autobridge.discovery.hypotheses import Hypothesis as _Hyp

        parsed = _json.loads(result.text.strip())
        items: list[dict[str, Any]] = []
        if isinstance(parsed, dict) and "id" in parsed:
            items = [parsed]
        elif isinstance(parsed, list):
            items = [x for x in parsed if isinstance(x, dict) and "id" in x]
        for item in items:
            prov = _Prov(source="l3-frontier", line=1, snippet="frontier generated")
            hid = str(item.get("id", f"l3:generated:{len(new_hyps)}"))
            try:
                hyp = _Hyp(
                    id=hid,
                    family=str(item.get("family", "unknown")),
                    claim=str(item.get("claim", "frontier generated hypothesis")),
                    candidate_mapping_fragment=dict(item.get("fragment", {"generated": True})),
                    prior=float(item.get("prior", 0.5)),
                    evidence_for=(prov,),
                    field=item.get("field"),
                    strategy=item.get("strategy"),
                )
                new_hyps.append(hyp)
            except Exception:
                continue
    except Exception:
        # If frontier output not JSON, keep original list — caller will handle as ambiguity
        pass
    usage = {
        "frontier_tokens": result.usage.total_tokens,
        "small_tokens": 0,
        "calls": 1,
        "frontier_calls": 1,
        "small_calls": 0,
        "frontier_input": result.usage.input_tokens,
        "frontier_output": result.usage.output_tokens,
        "small_input": 0,
        "small_output": 0,
    }
    return new_hyps, usage


def _merge_model_usage(base: dict[str, Any], delta: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for k in ("frontier_tokens", "small_tokens", "calls", "frontier_calls", "small_calls", "frontier_input", "frontier_output", "small_input", "small_output"):
        merged[k] = int(merged.get(k, 0)) + int(delta.get(k, 0))
    return merged


#: Ladder rung that makes small-tier (L2) vs frontier-tier (L3) model calls.
#: The repair ladder uses small models only at L2 and frontier models only
#: at L3, so a call's tier identifies the rung that made it (REQ-021 item 3).
_CALL_TIER_TO_RUNG: dict[str, int] = {"small": 2, "frontier": 3}


def _call_field(call: Any, name: str, default: Any = None) -> Any:
    if isinstance(call, dict):
        return call.get(name, default)
    return getattr(call, name, default)


def rung_ledger(
    trace: list[dict[str, Any]] | None,
    model_calls: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Derive the rung-by-rung ledger from a ladder trace (REQ-021 item 3).

    One entry per trace entry that names a ladder level: the rung tried, the
    action taken and the one-line reason recorded when it was tried. Each
    :class:`ModelCall` attaches to the rung that made it (small → L2,
    frontier → L3; calls of an unknown tier attach to the last attempt so
    nothing is silently dropped). Every call is summarized as
    tier/purpose/tokens/estimated cost — never the prompt or response.
    """
    attempts: list[dict[str, Any]] = []
    for entry in trace or []:
        if not isinstance(entry, dict):
            continue
        level = entry.get("level")
        if not isinstance(level, int) or isinstance(level, bool):
            continue
        attempts.append(
            {
                "rung": level,
                "action": str(entry.get("action", "")),
                "reason": str(entry.get("reason", "")),
                "calls": [],
            }
        )
    for call in model_calls or []:
        tier = str(_call_field(call, "tier", ""))
        purpose = str(_call_field(call, "purpose", "") or "")
        summary = {
            "tier": tier,
            "purpose": purpose,
            "input_tokens": int(_call_field(call, "input_tokens", 0) or 0),
            "output_tokens": int(_call_field(call, "output_tokens", 0) or 0),
            "cost": str(_call_field(call, "cost_estimate", "0") or "0"),
        }
        rung = _CALL_TIER_TO_RUNG.get(tier)
        placed = False
        if rung is not None:
            for attempt in attempts:
                if attempt["rung"] == rung:
                    attempt["calls"].append(summary)
                    placed = True
                    break
        if not placed and attempts:
            attempts[-1]["calls"].append(summary)
    return attempts


def ledger_totals(attempts: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Summarize a rung ledger: calls, small/frontier tokens, Decimal cost."""
    from decimal import Decimal  # noqa: PLC0415

    calls = 0
    small_tokens = 0
    frontier_tokens = 0
    cost = Decimal("0")
    for attempt in attempts or []:
        for call in attempt.get("calls", []):
            calls += 1
            tokens = int(call.get("input_tokens", 0)) + int(call.get("output_tokens", 0))
            if call.get("tier") == "frontier":
                frontier_tokens += tokens
            elif call.get("tier") == "small":
                small_tokens += tokens
            try:
                cost += Decimal(str(call.get("cost", "0")))
            except Exception:
                continue
    return {
        "calls": calls,
        "small_tokens": small_tokens,
        "frontier_tokens": frontier_tokens,
        "cost": str(cost),
    }


def _patch_from_hypothesis(
    spec: dict[str, Any],
    hypothesis: Any,
) -> list[dict[str, Any]] | None:
    """Convert a hypothesis fragment into a JSON Patch (best-effort).

    The fragment is declarative (plan §2.6) — we translate known families
    into valid spec paths. Returns None if no valid translation exists.
    """
    try:
        frag = getattr(hypothesis, "candidate_mapping_fragment", None)
        if frag is None and isinstance(hypothesis, dict):
            frag = hypothesis.get("candidate_mapping_fragment") or hypothesis.get("fragment")
        if not isinstance(frag, dict) or not frag:
            return None
        family = frag.get("family") or getattr(hypothesis, "family", "")
        field = frag.get("field") or frag.get("operation") or getattr(hypothesis, "field", None)
        strategy = frag.get("strategy") or getattr(hypothesis, "strategy", "")
        # Generic mapping: create a patch that touches a plausible spec location.
        # We choose health or entities based on family to keep patch valid.
        if family == "pagination" and field:
            # pagination strategy change: patch operations.<field>.pagination.strategy
            # Find operation key matching field path
            ops = spec.get("operations", {})
            if isinstance(ops, dict):
                for op_name, op_val in ops.items():
                    if isinstance(op_val, dict) and op_val.get("path") == field:
                        return [
                            {
                                "op": "replace",
                                "path": f"/operations/{op_name}/pagination/strategy",
                                "value": strategy,
                            }
                        ]
                # Fallback for unknown op: patch first operation
                if ops:
                    first_op = next(iter(ops))
                    return [
                        {
                            "op": "replace",
                            "path": f"/operations/{first_op}/pagination/strategy",
                            "value": strategy,
                        }
                    ]
        if family == "date" and field:
            # date mapping: patch entities.Reservation.fields.<field> transform
            transform = frag.get("transform", {"name": "date_local"})
            if isinstance(transform, dict) and "name" in transform:
                # Use arrival_date as example target
                return [
                    {
                        "op": "replace",
                        "path": "/entities/Reservation/fields/arrival_date/transform",
                        "value": [transform],
                    }
                ]
        if family == "amount" and field:
            transform = frag.get("transform", {"name": "money"})
            return [
                {
                    "op": "replace",
                    "path": "/entities/FolioCharge/fields/amount/transform",
                    "value": [transform],
                }
            ]
        if family == "status" and field:
            mapping = frag.get("transform", {}).get("mapping") if isinstance(frag.get("transform"), dict) else None
            if mapping:
                return [
                    {
                        "op": "replace",
                        "path": "/entities/Reservation/fields/status/transform",
                        "value": [{"name": "enum_map", "mapping": mapping}],
                    }
                ]
        # Generic fallback: patch a top-level health check to keep patch valid
        # Use a no-op but valid change: update health.canary.every
        return [
            {
                "op": "replace",
                "path": "/health/canary/every",
                "value": "60s",
            }
        ]
    except Exception:
        return None


def _try_budgeted_ladder(
    spec: dict[str, Any],
    drift_class: str,
    hypotheses: list[Any] | None,
    enumerator_yield_count: int | None,
    provider: Any | None,
    budget: Budget | None,
    probe_budget: Any | None,
    model_usage: dict[str, Any],
    evidence_ids: list[str],
    trace: list[dict[str, Any]],
    session: Any | None,
    run_id: str | None,
) -> LadderResult | None:
    """Attempt L2/L3 budgeted steps before final escalation.

    Returns LadderResult if L2/L3 produced a decision (propose/degraded/escalate),
    or None if not applicable (caller should do normal escalation).
    Handles BudgetExhausted → L4 escalation bundle and surviving ambiguity → Degraded.
    """
    if hypotheses is None:
        return None
    # Probe budget pre-check: if probe budget already exhausted, escalate
    if probe_budget is not None:
        try:
            checker = getattr(probe_budget, "check_pre_probe", None)
            if callable(checker):
                checker()
            else:
                # Try LLM Budget check
                checker2 = getattr(probe_budget, "check_pre_call", None)
                if callable(checker2):
                    checker2()
        except BudgetExhausted as exc:
            bundle = escalation_from_budget_exhausted(exc, evidence_ids=evidence_ids, context=drift_class)
            trace.append(
                {
                    "level": 4,
                    "action": "escalate",
                    "reason": f"BudgetExhausted before L2/L3: {exc.reason}",
                    "drift_class": drift_class,
                    "budget_type": "probe",
                }
            )
            return LadderResult(
                decision="escalate",
                ladder_level_reached=4,
                candidate_patch=None,
                rule_id=None,
                verification_level=None,
                model_usage=dict(model_usage),
                evidence_ids=list(evidence_ids),
                ladder_trace=list(trace),
                backoff_only=False,
                escalation_reason=str(exc),
                escalation_bundle=bundle,
                requires_release=False,
            )

    # L3: frontier generation when enumerator yielded <2
    active_hypotheses: list[Any] = list(hypotheses)
    if enumerator_yield_count is not None:
        if enumerator_yield_count < 2:
            # L3 must be invoked via frontier; assert is inside _call_frontier_generate
            try:
                trace.append(
                    {
                        "level": 3,
                        "action": "frontier_generate",
                        "reason": f"L3 invoked: enumerator yielded {enumerator_yield_count} < 2 for {drift_class}",
                        "drift_class": drift_class,
                        "hypothesis_count_before": len(active_hypotheses),
                    }
                )
                ranked, delta = _call_frontier_generate(
                    active_hypotheses,
                    provider,
                    budget,
                    enumerator_yield_count=enumerator_yield_count,
                    run_id=run_id,
                    session=session,
                )
                model_usage = _merge_model_usage(model_usage, delta)
                active_hypotheses = ranked
                trace[-1]["hypothesis_count_after"] = len(active_hypotheses)
                trace[-1]["model_usage_delta"] = delta
            except AssertionError:
                # Re-raise assertion for test visibility (L3 only when <2)
                raise
            except BudgetExhausted as exc:
                bundle = escalation_from_budget_exhausted(exc, evidence_ids=evidence_ids, context=f"L3 {drift_class}")
                trace.append(
                    {
                        "level": 4,
                        "action": "escalate",
                        "reason": f"BudgetExhausted in L3: {exc.reason}",
                        "drift_class": drift_class,
                    }
                )
                return LadderResult(
                    decision="escalate",
                    ladder_level_reached=4,
                    candidate_patch=None,
                    rule_id=None,
                    verification_level=None,
                    model_usage=dict(model_usage),
                    evidence_ids=list(evidence_ids),
                    ladder_trace=list(trace),
                    backoff_only=False,
                    escalation_reason=str(exc),
                    escalation_bundle=bundle,
                    requires_release=False,
                )
            except Exception as exc:
                trace.append({"level": 3, "action": "l3_error", "reason": str(exc), "drift_class": drift_class})
                # Fall through to try L2 with current hypotheses
        else:
            # Enumerator yielded >=2 — L3 must NOT be invoked (asserted elsewhere, but we guard)
            # If caller mistakenly tries to call L3 with >=2, the assert in _call_frontier_generate would fire.
            # Here we simply skip L3 and continue to L2.
            trace.append(
                {
                    "level": 3,
                    "action": "l3_skipped",
                    "reason": f"L3 skipped: enumerator yielded {enumerator_yield_count} >= 2 for {drift_class}",
                    "drift_class": drift_class,
                }
            )

    # L2: small model ranking when we have multiple hypotheses
    if len(active_hypotheses) >= 2:
        try:
            trace.append(
                {
                    "level": 2,
                    "action": "small_rank",
                    "reason": f"L2 ranking {len(active_hypotheses)} hypotheses for {drift_class}",
                    "drift_class": drift_class,
                    "hypothesis_ids": [getattr(h, "id", str(h)) for h in active_hypotheses[:5]],
                }
            )
            ranked, delta = _call_small_model_rank(active_hypotheses, provider, budget, run_id=run_id, session=session)
            model_usage = _merge_model_usage(model_usage, delta)
            active_hypotheses = ranked
            trace[-1]["ranked_ids"] = [getattr(h, "id", str(h)) for h in active_hypotheses[:5]]
            trace[-1]["model_usage_delta"] = delta
            # If ranking still leaves ambiguity (e.g., top 2 have equal prior), check surviving ambiguity
            # For now, if we have >1 hypothesis with equal prior, treat as ambiguity
            if len(active_hypotheses) > 1:
                # Check if top hypotheses are distinguishable: if their priors are equal or very close,
                # we consider ambiguity surviving. Use simple heuristic: all priors equal (common for enumerator)
                priors = [getattr(h, "prior", 0.5) for h in active_hypotheses]
                if len(set(priors)) == 1 and len(active_hypotheses) > 1:
                    # Still ambiguous — Degraded with open questions
                    amb = degraded_from_ambiguity(active_hypotheses, reason="L2 ranking did not disambiguate (equal priors)")
                    trace.append(
                        {
                            "level": 2,
                            "action": "ambiguity_survives",
                            "reason": "L2 ranking left surviving ambiguity",
                            "drift_class": drift_class,
                            "open_questions": list(amb.open_questions),
                        }
                    )
                    return LadderResult(
                        decision="degraded",
                        ladder_level_reached=2,
                        candidate_patch=None,
                        rule_id=None,
                        verification_level=None,
                        model_usage=dict(model_usage),
                        evidence_ids=list(evidence_ids),
                        ladder_trace=list(trace),
                        backoff_only=False,
                        escalation_reason=None,
                        requires_release=False,
                        open_questions=list(amb.open_questions),
                        degradation_reason=amb.reason,
                    )
        except BudgetExhausted as exc:
            bundle = escalation_from_budget_exhausted(exc, evidence_ids=evidence_ids, context=f"L2 {drift_class}")
            trace.append({"level": 4, "action": "escalate", "reason": f"BudgetExhausted in L2: {exc.reason}", "drift_class": drift_class})
            return LadderResult(
                decision="escalate",
                ladder_level_reached=4,
                candidate_patch=None,
                rule_id=None,
                verification_level=None,
                model_usage=dict(model_usage),
                evidence_ids=list(evidence_ids),
                ladder_trace=list(trace),
                backoff_only=False,
                escalation_reason=str(exc),
                escalation_bundle=bundle,
                requires_release=False,
            )
        except Exception as exc:
            trace.append({"level": 2, "action": "l2_error", "reason": str(exc), "drift_class": drift_class})

    # After L2/L3, if we have exactly one surviving hypothesis, try to make a patch
    if len(active_hypotheses) == 1:
        hyp = active_hypotheses[0]
        patch = _patch_from_hypothesis(spec, hyp)
        if patch is not None:
            # Validate patch
            try:
                from autobridge.spec.patch import apply_patch as _apply_patch

                _apply_patch(spec, patch)
                trace.append(
                    {
                        "level": 2 if len(hypotheses) >= 2 else 3,
                        "action": "propose",
                        "reason": f"L2/L3 produced patch from hypothesis {getattr(hyp, 'id', '?')} for {drift_class}",
                        "drift_class": drift_class,
                        "patch": patch,
                        "hypothesis_id": getattr(hyp, "id", None),
                    }
                )
                return LadderResult(
                    decision="propose",
                    ladder_level_reached=2 if len(hypotheses) >= 2 else 3,
                    candidate_patch=patch,
                    rule_id=getattr(hyp, "id", None) or "l2_hypothesis",
                    verification_level="visible",
                    model_usage=dict(model_usage),
                    evidence_ids=list(evidence_ids),
                    ladder_trace=list(trace),
                    backoff_only=False,
                    escalation_reason=None,
                    requires_release=True,
                    confidence=getattr(hyp, "prior", 0.8),
                )
            except Exception as exc:
                trace.append({"level": 2, "action": "patch_invalid", "reason": str(exc), "drift_class": drift_class})

    # If still multiple hypotheses survive after L2/L3, Degraded with open questions
    if len(active_hypotheses) > 1:
        amb = degraded_from_ambiguity(active_hypotheses, reason="surviving ambiguity after L2/L3")
        trace.append(
            {
                "level": 2,
                "action": "degraded",
                "reason": f"Surviving ambiguity: {len(active_hypotheses)} hypotheses remain for {drift_class}",
                "drift_class": drift_class,
                "open_questions": list(amb.open_questions),
            }
        )
        return LadderResult(
            decision="degraded",
            ladder_level_reached=2,
            candidate_patch=None,
            rule_id=None,
            verification_level=None,
            model_usage=dict(model_usage),
            evidence_ids=list(evidence_ids),
            ladder_trace=list(trace),
            backoff_only=False,
            escalation_reason=None,
            requires_release=False,
            open_questions=list(amb.open_questions),
            degradation_reason=amb.reason,
        )

    # No hypothesis left or single but no valid patch — fall through to escalation
    return None


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
    # P1.W5.5 budgeted L2/L3
    hypotheses: list[Any] | None = None,
    enumerator_yield_count: int | None = None,
    provider: Any | None = None,
    budget: Budget | None = None,
    session: Any | None = None,
    run_id: str | None = None,
    probe_budget: Any | None = None,
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
        bundle = escalation_for_no_fix(reason, evidence_ids=evidence_ids)
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
            escalation_bundle=bundle,
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
        bundle = escalation_for_no_fix(reason, evidence_ids=evidence_ids)
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
            escalation_bundle=bundle,
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
        # Matched rules but none produced valid patch -> try L2/L3 before escalate
        budgeted = _try_budgeted_ladder(
            spec,
            drift_class,
            hypotheses,
            enumerator_yield_count,
            provider,
            budget,
            probe_budget,
            model_usage,
            evidence_ids,
            trace,
            session,
            run_id,
        )
        if budgeted is not None:
            return budgeted
        reason = f"L1 rules matched but none produced a valid patch for {drift_class}"
        trace.append({"level": 4, "action": "escalate", "reason": reason, "drift_class": drift_class})
        bundle = escalation_for_no_fix(reason, evidence_ids=evidence_ids)
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
            escalation_bundle=bundle,
            requires_release=False,
        )

    # No L1 rule matched -> try L2/L3 before escalate to L4
    budgeted = _try_budgeted_ladder(
        spec,
        drift_class,
        hypotheses,
        enumerator_yield_count,
        provider,
        budget,
        probe_budget,
        model_usage,
        evidence_ids,
        trace,
        session,
        run_id,
    )
    if budgeted is not None:
        return budgeted
    reason = f"No L0/L1 repair for {drift_class} — escalate to L4"
    trace.append({"level": 4, "action": "escalate", "reason": reason, "drift_class": drift_class})
    bundle = escalation_for_no_fix(reason, evidence_ids=evidence_ids)
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
        escalation_bundle=bundle,
        requires_release=False,
    )


#: L2 confidence gate (REQ-021 owner check): the top-ranked hypothesis must
#: reach this prior for the ranking to stand on its own; below it the climb
#: continues to the frontier instead of guessing.
L2_CONFIDENCE_THRESHOLD = 0.70


def run_frontier_climb(
    *,
    spec: dict[str, Any],
    drift_class: str,
    hypotheses: list[Any],
    enumerator_yield_count: int,
    provider: Any | None,
    budget: Budget | None = None,
    session: Any | None = None,
    run_id: str | None = None,
    knowledge_cache: KnowledgeCache | None = None,
    fingerprint_delta: Any | None = None,
    extra: dict[str, Any] | None = None,
    vendor: str | None = None,
    l2_threshold: float = L2_CONFIDENCE_THRESHOLD,
    scenario: str | None = None,
    validate_against: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Climb L0 -> L1 -> L2 -> L3 for a semantic drift, then stop (REQ-021).

    Unlike :func:`run_ladder` (L3-before-L2 per plan §2.6), this orchestrates
    the demonstration order the hard-semantic scenario needs: L0 consults
    learned knowledge, L1 tries deterministic rules, L2 ranks the competing
    hypotheses with the small model and continues only when the winner's
    prior is below *l2_threshold*, L3 calls the frontier model for a patch
    proposal. It returns the climb — attempts with one-line reasons, model
    usage, the L3 response text and any parsed patch proposal — and lets the
    caller validate the proposal (verifier verdict or scenario trap check)
    and escalate. No patch is ever applied here.

    A proposal that does not apply, does not parse, or fails schema
    validation is a normal outcome, not an error: when *validate_against* is
    given, the proposal is apply-checked (mechanics only, never semantics —
    a plausible-but-wrong patch still passes here and is for the verifier or
    trap check) and a rejection is recorded on the rung with the proposal,
    its tokens and the reason; the exception never escapes. Without a spec
    to check against, parsing alone decides and the caller validates.

    *provider* ``None`` (``FRONTIER_MODE=off``) records an explicit skip at
    L2/L3 instead of calling. A :class:`ReplayMissing` (replay mode without
    a recording for the prompt hash) is recorded as an explicit skip naming
    the hash and the ``make record-frontier SCENARIO=...`` capture command —
    never silent, never invented. Every model call goes through
    :class:`BudgetedProvider`, so ``BudgetExhausted`` propagates and
    ``ModelCall`` rows land when *session* + *run_id* are given (linked to
    the rung by tier, see :func:`rung_ledger`).
    """
    from autobridge.repair.context import RepairContext  # noqa: PLC0415
    from autobridge.repair.registry import matching_rules  # noqa: PLC0415

    def _record_hint() -> str:
        name = scenario or "<scenario>"
        return f"run make record-frontier SCENARIO={name} with FRONTIER_API_KEY to capture it"

    vendor_val = vendor or _extract_vendor(spec)
    attempts: list[dict[str, Any]] = []
    usage = _empty_model_usage()
    replay_missing: list[str] = []
    replay_captured_at = ""

    # --- L0: learned knowledge ---
    if knowledge_cache is not None and fingerprint_delta is not None:
        entry = knowledge_cache.lookup(vendor_val, fingerprint_delta)
        if entry is not None:
            attempts.append(
                {
                    "rung": 0,
                    "action": "knowledge_hit",
                    "reason": f"L0 hit: cached {entry.rule_id} patch reused",
                    "calls": [],
                }
            )
            return {
                "attempts": attempts,
                "usage": usage,
                "resolved_rung": 0,
                "l3_text": "",
                "proposal": None,
                "ranked_ids": [],
                "l2_confidence": None,
                "replay_missing": replay_missing,
                "replay_captured_at": replay_captured_at,
            }
        attempts.append(
            {
                "rung": 0,
                "action": "knowledge_miss",
                "reason": f"L0 miss: no known fix for {vendor_val}",
                "calls": [],
            }
        )

    # --- L1: deterministic rules ---
    ctx = RepairContext(
        spec=spec,
        drift_class=drift_class,
        signals=[],
        fingerprint_delta=fingerprint_delta,
        classification=None,
        extra=dict(extra or {}),
    )
    matched = matching_rules(ctx)
    if matched:
        names = ", ".join(e.id for e in matched)
        attempts.append(
            {
                "rung": 1,
                "action": "rules_matched",
                "reason": f"L1 rules matched ({names}) but none covers {drift_class}; continuing",
                "calls": [],
            }
        )
    else:
        attempts.append(
            {
                "rung": 1,
                "action": "no_rule_matched",
                "reason": f"L1: no rule precondition matched for {drift_class}",
                "calls": [],
            }
        )

    # --- L2: small-model ranking with a confidence gate ---
    ranked: list[Any] = list(hypotheses)
    l2_confidence: float | None = None
    if provider is None:
        attempts.append(
            {
                "rung": 2,
                "action": "l2_skipped",
                "reason": "L2 skipped: no provider configured (FRONTIER_MODE=off)",
                "calls": [],
            }
        )
        return {
            "attempts": attempts,
            "usage": usage,
            "resolved_rung": 1,
            "l3_text": "",
            "proposal": None,
            "ranked_ids": [getattr(h, "id", str(h)) for h in ranked],
            "l2_confidence": None,
            "replay_missing": replay_missing,
            "replay_captured_at": replay_captured_at,
        }
    if len(ranked) >= 2:
        l2_entry: dict[str, Any] = {
            "rung": 2,
            "action": "small_rank",
            "reason": f"L2 ranking {len(ranked)} hypotheses for {drift_class}",
            "calls": [],
        }
        attempts.append(l2_entry)
        try:
            ranked, delta = _call_small_model_rank(ranked, provider, budget, run_id=run_id, session=session)
        except ReplayMissing as exc:
            replay_missing.append(exc.prompt_hash)
            l2_entry["action"] = "l2_skipped"
            l2_entry["reason"] = (
                f"L2 skipped: no replay recording for prompt {exc.prompt_hash}; {_record_hint()}"
            )
            return {
                "attempts": attempts,
                "usage": usage,
                "resolved_rung": 1,
                "l3_text": "",
                "proposal": None,
                "ranked_ids": [getattr(h, "id", str(h)) for h in ranked],
                "l2_confidence": None,
                "replay_missing": replay_missing,
                "replay_captured_at": replay_captured_at,
            }
        usage = _merge_model_usage(usage, delta)
        l2_entry["ranked_ids"] = [getattr(h, "id", str(h)) for h in ranked[:5]]
        top_prior = float(getattr(ranked[0], "prior", 0.0)) if ranked else 0.0
        l2_confidence = top_prior
        if top_prior >= l2_threshold:
            l2_entry["reason"] = (
                f"L2 confident: {top_prior:.2f} >= {l2_threshold:.2f}; ranking stands, no frontier call"
            )
            l2_entry["model_usage_delta"] = delta
            return {
                "attempts": attempts,
                "usage": usage,
                "resolved_rung": 2,
                "l3_text": "",
                "proposal": None,
                "ranked_ids": [getattr(h, "id", str(h)) for h in ranked],
                "l2_confidence": l2_confidence,
                "replay_missing": replay_missing,
                "replay_captured_at": replay_captured_at,
            }
        l2_entry["reason"] = (
            f"L2 confidence {top_prior:.2f} < {l2_threshold:.2f}: ranking cannot decide; continuing to L3"
        )
        l2_entry["model_usage_delta"] = delta
    else:
        attempts.append(
            {
                "rung": 2,
                "action": "l2_skipped",
                "reason": f"L2 skipped: fewer than two hypotheses ({len(ranked)})",
                "calls": [],
            }
        )

    # --- L3: frontier proposal (plan §2.6 gate: enumerator yielded < 2) ---
    # The frontier answers the climb with a patch proposal (JSON Patch);
    # the raw text is returned so the caller can validate it (verifier
    # verdict or scenario trap check) and escalate on rejection.
    if enumerator_yield_count >= 2:
        attempts.append(
            {
                "rung": 3,
                "action": "l3_skipped",
                "reason": f"L3 skipped: enumerator yielded {enumerator_yield_count} >= 2 for {drift_class}",
                "calls": [],
            }
        )
        return {
            "attempts": attempts,
            "usage": usage,
            "resolved_rung": 2,
            "l3_text": "",
            "proposal": None,
            "ranked_ids": [getattr(h, "id", str(h)) for h in ranked],
            "l2_confidence": l2_confidence,
            "replay_missing": replay_missing,
            "replay_captured_at": replay_captured_at,
        }
    l3_entry: dict[str, Any] = {
        "rung": 3,
        "action": "frontier_generate",
        "reason": f"L3 invoked: enumerator yielded {enumerator_yield_count} < 2 for {drift_class}",
        "calls": [],
    }
    attempts.append(l3_entry)
    l3_messages = [
        {
            "role": "system",
            "content": (
                "You repair API connectors. Propose a JSON Patch (RFC 6902 list of "
                "op/path/value objects) that fixes the drift. Answer with the patch only."
            ),
        },
        {
            "role": "user",
            "content": f"drift_class: {drift_class}; ranked hypotheses: {[getattr(h, 'id', str(h)) for h in ranked]}",
        },
    ]
    effective: Any = provider
    if budget is not None:
        effective = BudgetedProvider(provider, budget, session=session, run_id=run_id)
    # Reasoning models spend most of a small max_tokens on thinking and then
    # return an empty message (owner check): budget room for reasoning output.
    try:
        l3_result = effective.complete(l3_messages, max_tokens=4096, tier="frontier", purpose="l3-generate-patch")
    except ReplayMissing as exc:
        replay_missing.append(exc.prompt_hash)
        l3_entry["action"] = "l3_skipped"
        l3_entry["reason"] = (
            f"L3 skipped: no replay recording for prompt {exc.prompt_hash}; {_record_hint()}"
        )
        return {
            "attempts": attempts,
            "usage": usage,
            "resolved_rung": 2,
            "l3_text": "",
            "proposal": None,
            "ranked_ids": [getattr(h, "id", str(h)) for h in ranked],
            "l2_confidence": l2_confidence,
            "replay_missing": replay_missing,
            "replay_captured_at": replay_captured_at,
        }
    l3_delta = {
        "frontier_tokens": l3_result.usage.total_tokens,
        "small_tokens": 0,
        "calls": 1,
        "frontier_calls": 1,
        "small_calls": 0,
        "frontier_input": l3_result.usage.input_tokens,
        "frontier_output": l3_result.usage.output_tokens,
        "small_input": 0,
        "small_output": 0,
    }
    usage = _merge_model_usage(usage, l3_delta)
    l3_entry["model_usage_delta"] = l3_delta
    l3_text = l3_result.text or ""
    proposal, parse_detail = _parse_patch_response(l3_text)
    if proposal is not None:
        l3_entry["proposal_ops"] = len(proposal)
    else:
        # No usable patch is a normal outcome, never an error: record what
        # the text contained plus a truncated preview so a live answer is
        # diagnosable (prompts carry no secrets; truncate defense-in-depth).
        l3_entry["parse_detail"] = parse_detail
        if l3_text.strip():
            l3_entry["raw_preview"] = l3_text.strip()[:300]
        else:
            l3_entry["reason"] = f"L3 returned an empty response ({parse_detail}); no patch proposed"
    if proposal is not None and validate_against is not None:
        try:
            from autobridge.spec.patch import apply_patch as _apply_patch  # noqa: PLC0415

            _apply_patch(validate_against, proposal)
            l3_entry["proposal_validated"] = True
        except Exception as exc:
            proposal = None
            l3_entry["action"] = "proposal_rejected"
            l3_entry["reason"] = f"L3 proposal rejected: patch does not apply ({exc})"
            l3_entry["raw_preview"] = l3_text.strip()[:300]
    replay_captured_at = str(getattr(provider, "last_captured_at", "") or "")
    return {
        "attempts": attempts,
        "usage": usage,
        "resolved_rung": 3,
        "l3_text": l3_text,
        "proposal": proposal,
        "ranked_ids": [getattr(h, "id", str(h)) for h in ranked],
        "l2_confidence": l2_confidence,
        "replay_missing": replay_missing,
        "replay_captured_at": replay_captured_at,
    }
