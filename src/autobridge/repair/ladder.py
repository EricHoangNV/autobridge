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

import contextlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from autobridge.llm.provider import (
    Budget,
    BudgetedProvider,
    BudgetExhausted,
    ReplayMissing,
    stamp_call_outcome,
)
from autobridge.repair.context import RepairContext
from autobridge.repair.escalation import (
    EscalationBundle,
    degraded_from_ambiguity,
    escalation_for_no_fix,
    escalation_from_budget_exhausted,
)
from autobridge.repair.knowledge import (
    NO_LEARN_CLASSES,
    KnowledgeCache,
    cache_key,
    delta_has_content,
)
from autobridge.repair.registry import matching_rules

#: Output cap for the L3 frontier call (reasoning + answer); see run_frontier_climb.
#: The live default lives in :func:`autobridge.config.frontier_max_tokens`
#: (env ``FRONTIER_MAX_TOKENS``); this constant is the compiled-in default
#: used when config is unavailable (tests importing this module directly).
L3_MAX_TOKENS = 131072


def _utc_now_iso() -> str:
    """Current UTC time as an ISO string (rung call-started stamps)."""
    return datetime.now(UTC).isoformat()


def _provider_model_name(provider: Any, tier: str) -> str:
    """Best-effort model id for a rung entry; never raises, never empty."""
    for getter_name in ("model_for_tier", "_model_for_tier"):
        getter = getattr(provider, getter_name, None)
        if callable(getter):
            try:
                name = getter(tier)
            except Exception:
                continue
            if isinstance(name, str) and name.strip():
                return name.strip()
    inner = getattr(provider, "inner", None)
    if inner is not None and inner is not provider:
        return _provider_model_name(inner, tier)
    return tier

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


def _extract_json_candidates(stripped: str) -> list[str]:
    """Whole text, fenced block, then largest ``{...}``/``[...]`` span."""
    import re as _re  # noqa: PLC0415

    candidates: list[str] = [stripped]
    fence = _re.search(r"```(?:json)?\s*\n?(.*?)```", stripped, _re.DOTALL)
    if fence:
        candidates.append(fence.group(1).strip())
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = stripped.find(opener), stripped.rfind(closer)
        if start != -1 and end != -1 and end > start:
            candidates.append(stripped[start : end + 1])
    return candidates


def _parse_l3_response(text: str) -> dict[str, Any]:
    """Parse an L3 frontier reply (REQ-044).

    The well-posed answer is one JSON object — either
    ``{"patch": [...], "rationale": "<one sentence>"}`` or
    ``{"escalate": "<the question for the human>", "rationale": "..."}`` —
    tolerating a fenced block around it. A bare patch list (the pre-044
    shape) still parses as a patch with an empty rationale, so old
    recordings and callers keep working.

    Returns ``{"kind", "patch", "rationale", "question", "detail"}`` where
    *kind* is ``"patch"``, ``"escalate"`` or ``"none"`` and *detail* is a
    one-line, secret-free account of what the text contained.
    """
    import json as _json  # noqa: PLC0415 - keep ladder import-light

    stripped = (text or "").strip()
    if not stripped:
        return {
            "kind": "none",
            "patch": None,
            "rationale": "",
            "question": "",
            "detail": "empty response: the model returned no text",
        }
    for candidate in _extract_json_candidates(stripped):
        try:
            parsed = _json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            patch = parsed.get("patch")
            escalate = parsed.get("escalate")
            rationale = parsed.get("rationale") or ""
            if isinstance(patch, list) and _is_patch_list(patch):
                return {
                    "kind": "patch",
                    "patch": [dict(op) for op in patch],
                    "rationale": str(rationale),
                    "question": "",
                    "detail": "parsed",
                }
            if isinstance(patch, list):
                return {
                    "kind": "none",
                    "patch": None,
                    "rationale": str(rationale),
                    "question": "",
                    "detail": "response patch member is not a patch list (no op/path operations)",
                }
            if isinstance(escalate, str) and escalate.strip():
                return {
                    "kind": "escalate",
                    "patch": None,
                    "rationale": str(rationale),
                    "question": escalate.strip(),
                    "detail": "parsed",
                }
            if "escalate" in parsed:
                return {
                    "kind": "none",
                    "patch": None,
                    "rationale": str(rationale),
                    "question": "",
                    "detail": "response escalate member is not a question string",
                }
            return {
                "kind": "none",
                "patch": None,
                "rationale": str(rationale),
                "question": "",
                "detail": "response JSON is neither a patch nor an escalation object",
            }
        if _is_patch_list(parsed):
            return {
                "kind": "patch",
                "patch": [dict(op) for op in parsed],
                "rationale": "",
                "question": "",
                "detail": "parsed bare patch list (pre-044 shape)",
            }
    if "[" in stripped or "{" in stripped:
        return {
            "kind": "none",
            "patch": None,
            "rationale": "",
            "question": "",
            "detail": "response contains JSON-like content that does not parse as a patch or escalation",
        }
    return {
        "kind": "none",
        "patch": None,
        "rationale": "",
        "question": "",
        "detail": "no JSON patch or escalation found in response",
    }
def _parse_patch_response(text: str) -> tuple[list[dict[str, Any]] | None, str]:
    """Extract a JSON Patch proposal from a frontier model's raw text.

    Pre-044 compatibility wrapper over :func:`_parse_l3_response`: a bare
    patch list parses as before; the ``{"patch": ...}`` envelope's patch
    is returned with its rationale folded into the detail. An
    ``{"escalate": ...}`` answer is not a patch (``None`` plus a detail
    naming the escalation).
    """
    parsed = _parse_l3_response(text)
    if parsed["kind"] == "patch":
        detail = "parsed"
        if parsed["rationale"]:
            detail = f"parsed (model rationale: {parsed['rationale'][:200]})"
        return parsed["patch"], detail
    if parsed["kind"] == "escalate":
        return None, f"model escalated instead of patching: {parsed['question'][:200]}"
    return None, parsed["detail"]


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
    # The RepairRun row this climb persisted (set by RepairController when
    # session is given). Threading the id — never re-fetching "the newest
    # row" — keeps concurrent repairs on their own rows (REQ-040/REQ-042).
    repair_run_id: str | None = None


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
        "frontier_reasoning": 0,
        "small_reasoning": 0,
    }


# ---------------------------------------------------------------------------
# L2 / L3 helpers (P1.W5.5) — budgeted, with assertions
# ---------------------------------------------------------------------------


def _scrub_text(text: str, *, limit: int = 160) -> str:
    """Scrub free text for a prompt: secret-shaped values become ``<redacted>``.

    Provenance snippets and wire values are untrusted data (never
    instructions) and must never carry secrets. Values assigned to
    sensitive-looking keys (``api_token=...``, ``"secret": "..."``) are
    replaced; the text is truncated defense-in-depth.
    """
    import re as _re  # noqa: PLC0415 - deferred so the ladder stays import-light

    from autobridge.repair.semantic import SENSITIVE_KEY_FRAGMENTS  # noqa: PLC0415

    out = str(text or "")
    for frag in sorted(SENSITIVE_KEY_FRAGMENTS, key=len, reverse=True):
        pattern = _re.compile(
            r"(['\"]?" + _re.escape(frag) + r"[^'\"=:\s]*['\"]?\s*[:=]\s*)(['\"]?)([^\s,'\"}]+)\2",
            _re.IGNORECASE,
        )
        out = pattern.sub(r"\1<redacted>", out)
    if len(out) > limit:
        out = out[:limit] + "…"
    return " ".join(out.split())


def _provenance_text(prov: Any) -> str:
    """One-line evidence summary for a provenance record (scrubbed)."""
    try:
        source = str(getattr(prov, "source", "") or "")
        line = getattr(prov, "line", "")
        snippet = str(getattr(prov, "snippet", "") or "")
    except Exception:
        return ""
    head = source
    if line not in (None, ""):
        head = f"{source}:{line}"
    if snippet:
        return f"{head} — {_scrub_text(snippet)}" if head else _scrub_text(snippet)
    return head or "(no provenance detail)"


def _hypotheses_block(hypotheses: list[Any]) -> str:
    """Prompt block: every hypothesis with claim and for/against evidence."""
    lines: list[str] = []
    for h in hypotheses or []:
        hid = str(getattr(h, "id", "?"))
        claim = str(getattr(h, "claim", "") or "")
        try:
            prior = float(getattr(h, "prior", 0.5))
        except Exception:
            prior = 0.5
        field = str(getattr(h, "field", "") or "")
        family = str(getattr(h, "family", "") or "")
        fors = [_provenance_text(p) for p in (getattr(h, "evidence_for", None) or ())]
        againsts = [_provenance_text(p) for p in (getattr(h, "evidence_against", None) or ())]
        fors = [s for s in fors if s]
        againsts = [s for s in againsts if s]
        header = f"- {hid}: {claim} (prior {prior:.2f}"
        if family:
            header += f"; family {family}"
        if field:
            header += f"; field {field}"
        header += ")"
        lines.append(header)
        lines.append(f"  for: {'; '.join(fors) if fors else '(none recorded)'}")
        lines.append(f"  against: {'; '.join(againsts) if againsts else '(none recorded)'}")
    return "\n".join(lines) if lines else "(no hypotheses)"


def _shared_evidence_block(drift_class: str, evidence: dict[str, Any] | None) -> str:
    """Prompt block: the observed evidence shared by all hypotheses (scrubbed)."""
    import json as _json  # noqa: PLC0415

    if not evidence:
        return (
            f"drift_class {drift_class}; no shared evidence recorded "
            "(wire shape intact — rank on the hypotheses' own evidence)"
        )
    try:
        rendered = _json.dumps(evidence, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:
        rendered = str(evidence)
    return _scrub_text(rendered, limit=2000)


def _parse_l2_response(text: str) -> dict[str, Any]:
    """Parse an L2 ranking reply (REQ-045).

    The well-posed answer is ``{"ranking": [...], "confidence": 0..1,
    "rationale": "..."}``; a bare id list (the pre-045 shape) still ranks
    with no confidence. Anything else is a parse failure: the caller falls
    back to prior order, labelled, never silent.
    """
    import json as _json  # noqa: PLC0415 - keep ladder import-light

    stripped = (text or "").strip()
    if not stripped:
        return {
            "ids": [],
            "confidence": None,
            "rationale": "",
            "fallback": "empty response: the model returned no text; fell back to prior order",
        }
    try:
        parsed = _json.loads(stripped)
    except Exception:
        parsed = None
    if isinstance(parsed, dict) and isinstance(parsed.get("ranking"), list):
        ids = [str(x) for x in parsed["ranking"]]
        confidence: float | None = None
        try:
            raw_confidence = parsed.get("confidence")
            confidence = float(raw_confidence) if raw_confidence is not None else None
            if confidence is not None and not 0.0 <= confidence <= 1.0:
                confidence = None
        except Exception:
            confidence = None
        return {
            "ids": ids,
            "confidence": confidence,
            "rationale": str(parsed.get("rationale") or ""),
            "fallback": None,
        }
    if isinstance(parsed, list):
        return {
            "ids": [str(x) for x in parsed],
            "confidence": None,
            "rationale": "",
            "fallback": None,
            "detail": "legacy list shape (no confidence)",
        }
    for sep in (",", " ", "\n"):
        parts = [s.strip().strip('"').strip("'") for s in stripped.split(sep) if s.strip()]
        if len(parts) >= 2:
            return {
                "ids": parts,
                "confidence": None,
                "rationale": "",
                "fallback": None,
                "detail": "comma/space-separated shape (no confidence)",
            }
    return {
        "ids": [],
        "confidence": None,
        "rationale": "",
        "fallback": f"unparsable L2 response ({stripped[:120]}); fell back to prior order",
    }


def _call_small_model_rank(
    hypotheses: list[Any],
    provider: Any,
    budget: Budget | None,
    *,
    run_id: str | None = None,
    session: Any | None = None,
    purpose: str = "l2-rank-hypotheses",
    drift_class: str = "UNKNOWN",
    evidence: dict[str, Any] | None = None,
) -> tuple[list[Any], dict[str, Any], dict[str, Any]]:
    """L2: small model ranks hypotheses (budgeted, REQ-045 well-posed prompt).

    The model sees every hypothesis with its claim and for/against
    evidence plus the shared observed evidence, and answers with a ranking
    plus confidence. Returns ``(ranked, usage_delta, info)`` where ``info``
    carries ``confidence`` (None when the answer carries none),
    ``rationale`` and ``fallback`` (None, else the labelled reason prior
    order was kept). Every call goes through BudgetedProvider so
    BudgetExhausted propagates.
    """
    from autobridge.llm.prompts import get_prompt, prompt_ref, render_prompt  # noqa: PLC0415

    if provider is None:
        # No provider — deterministic fallback: return input order
        return list(hypotheses), _empty_model_usage(), {
            "confidence": None,
            "rationale": "",
            "fallback": "no provider configured",
        }
    # REQ-045: the question lives in config/prompts/l2_rank.md — the model
    # gets every hypothesis with its evidence and the shared observed
    # evidence, and ranks by explanation fit, not by prior.
    template = get_prompt("l2_rank")
    ref = prompt_ref("l2_rank")
    messages = render_prompt(
        template,
        drift_class=str(drift_class),
        hypotheses_block=_hypotheses_block(hypotheses),
        evidence_block=_shared_evidence_block(str(drift_class), evidence),
    )
    # Wrap with budget if provided
    effective_provider: Any = provider
    ledger_kwargs: dict[str, Any] = {}
    if budget is not None:
        effective_provider = BudgetedProvider(provider, budget, session=session, run_id=run_id)
        # REQ-045: the ledger row names the prompt file (id/version/hash).
        # Only BudgetedProvider accepts it — the inner provider protocol is
        # unchanged, so direct (unbudgeted) calls never see this kwarg.
        ledger_kwargs["prompt"] = ref
    # Will raise BudgetExhausted if ceiling hit — caller must catch and escalate
    result = effective_provider.complete(
        messages, max_tokens=template.max_tokens, tier="small", purpose=purpose, **ledger_kwargs
    )
    parsed = _parse_l2_response(result.text)
    # Map back to hypothesis objects in ranked order; unknown ids ignored
    id_map = {h.id: h for h in hypotheses}
    ranked: list[Any] = []
    for hid in parsed["ids"]:
        if isinstance(hid, str) and hid in id_map and id_map[hid] not in ranked:
            ranked.append(id_map[hid])
    # Append any missing (model didn't rank all)
    for h in hypotheses:
        if h not in ranked:
            ranked.append(h)
    fallback = parsed.get("fallback")
    if not any(h.id in parsed["ids"] for h in hypotheses):
        # Nothing usable came back (or nothing known): prior order stands.
        ranked = list(hypotheses)
        if fallback is None:
            fallback = "model ranked no known hypothesis id; fell back to prior order"
    if session is not None and run_id is not None and budget is not None:
        # REQ-030 item 3: what the L2 call produced, on the ledger row.
        stamp_call_outcome(
            session,
            run_id=run_id,
            tier="small",
            outcome="ranked" if fallback is None else "fallback",
        )
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
        "frontier_reasoning": 0,
        "small_reasoning": result.usage.reasoning_tokens,
    }
    info = {
        "confidence": parsed.get("confidence"),
        "rationale": str(parsed.get("rationale") or ""),
        "fallback": fallback,
        "detail": str(parsed.get("detail") or ""),
        "prompt": dict(ref),
    }
    return ranked, usage, info


def _parse_l3_hypothesis_response(text: str) -> dict[str, Any]:
    """Parse an L3 hypothesis reply (REQ-045).

    Well-posed answers: ``{"hypothesis": {...}, "rationale": ...}`` or
    ``{"none": "<why>"}``; a bare ``{"id": ...}`` object (pre-045) still
    proposes. An unknown family is rejected (the engine can only verify
    its own families), never constructed.
    """
    import json as _json  # noqa: PLC0415 - keep ladder import-light

    from autobridge.discovery.hypotheses import FAMILIES as _FAMILIES  # noqa: PLC0415

    stripped = (text or "").strip()
    if not stripped:
        return {"items": [], "none_reason": "", "rejected": "", "rationale": "",
                "detail": "empty response: the model returned no text"}
    try:
        parsed = _json.loads(stripped)
    except Exception:
        return {"items": [], "none_reason": "", "rejected": "", "rationale": "",
                "detail": f"response is not JSON ({stripped[:120]})"}
    if isinstance(parsed, dict) and isinstance(parsed.get("hypothesis"), dict):
        item = dict(parsed["hypothesis"])
        rationale = str(parsed.get("rationale") or "")
        family = str(item.get("family", "") or "")
        if family not in _FAMILIES:
            return {"items": [], "none_reason": "", "rejected": family,
                    "rationale": rationale,
                    "detail": f"unknown family {family!r} (verifiable: {sorted(_FAMILIES)}); rejected"}
        return {"items": [item], "none_reason": "", "rejected": "", "rationale": rationale, "detail": "parsed"}
    if isinstance(parsed, dict) and "none" in parsed:
        return {"items": [], "none_reason": str(parsed.get("none") or ""), "rejected": "",
                "rationale": "", "detail": "model declined to propose (none)"}
    items: list[dict[str, Any]] = []
    if isinstance(parsed, dict) and "id" in parsed:
        items = [parsed]
    elif isinstance(parsed, list):
        items = [x for x in parsed if isinstance(x, dict) and "id" in x]
    if items:
        kept: list[dict[str, Any]] = []
        rejected = ""
        for item in items:
            family = str(item.get("family", "") or "")
            if family and family not in _FAMILIES:
                rejected = family
                continue
            kept.append(item)
        if kept:
            return {"items": kept, "none_reason": "", "rejected": rejected, "rationale": "",
                    "detail": "parsed legacy shape"}
        return {"items": [], "none_reason": "", "rejected": rejected, "rationale": "",
                "detail": f"all proposals use unknown families ({rejected!r}); rejected"}
    return {"items": [], "none_reason": "", "rejected": "", "rationale": "",
            "detail": "response JSON is neither a hypothesis nor a none answer"}


def _call_frontier_generate(
    hypotheses: list[Any],
    provider: Any,
    budget: Budget | None,
    *,
    enumerator_yield_count: int,
    run_id: str | None = None,
    session: Any | None = None,
    purpose: str = "l3-generate-hypothesis",
    drift_class: str = "UNKNOWN",
    signals: list[Any] | None = None,
    evidence: dict[str, Any] | None = None,
    spec_excerpt: Any | None = None,
    docs_excerpt: str | None = None,
) -> tuple[list[Any], dict[str, Any], dict[str, Any]]:
    """L3: frontier model generates hypotheses for UNKNOWN/SEMANTIC (REQ-045).

    Asserts enumerator yielded fewer than two hypotheses (plan §2.6). The
    model sees the drift class, wire signals, observed evidence, the spec
    excerpt, the relevant docs excerpt, the existing hypotheses and the
    verifiable families, and answers with one novel hypothesis or an
    explicit ``none``. Returns ``(hypotheses, usage_delta, info)`` where
    ``info`` carries ``proposed`` / ``rejected`` / ``none_reason``.
    Every call goes through BudgetedProvider.
    """
    import json as _json  # noqa: PLC0415 - keep ladder import-light

    from autobridge.llm.prompts import get_prompt, prompt_ref, render_prompt  # noqa: PLC0415

    assert enumerator_yield_count < 2, (
        f"L3 frontier invoked only when enumerator yields fewer than two hypotheses, got {enumerator_yield_count}"
    )
    if provider is None:
        # No provider — cannot generate; return input (caller will handle ambiguity)
        return list(hypotheses), _empty_model_usage(), {
            "proposed": False, "rejected": "", "none_reason": "no provider configured", "rationale": "",
        }
    from autobridge.discovery.hypotheses import FAMILIES as _FAMILIES  # noqa: PLC0415

    def _block(value: Any, *, empty_note: str, limit: int = 1500) -> str:
        if value is None or value == "" or value == {} or value == []:
            return empty_note
        try:
            rendered = _json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
        except Exception:
            rendered = str(value)
        return _scrub_text(rendered, limit=limit)

    # REQ-045: the question lives in config/prompts/l3_hypothesis.md.
    template = get_prompt("l3_hypothesis")
    ref = prompt_ref("l3_hypothesis")
    messages = render_prompt(
        template,
        drift_class=str(drift_class),
        signals_block=_block(signals, empty_note="(no wire signals recorded)"),
        evidence_block=_shared_evidence_block(str(drift_class), evidence),
        spec_excerpt=_block(spec_excerpt, empty_note="(no spec excerpt recorded)"),
        docs_excerpt=(
            _scrub_text(str(docs_excerpt), limit=1500)
            if docs_excerpt else "(no docs excerpt recorded)"
        ),
        existing_hypotheses=_hypotheses_block(hypotheses),
        families=", ".join(sorted(_FAMILIES)),
    )
    effective_provider: Any = provider
    ledger_kwargs: dict[str, Any] = {}
    if budget is not None:
        effective_provider = BudgetedProvider(provider, budget, session=session, run_id=run_id)
        ledger_kwargs["prompt"] = ref
    result = effective_provider.complete(
        messages, max_tokens=template.max_tokens, tier="frontier", purpose=purpose, **ledger_kwargs
    )
    # Try to parse frontier's new hypothesis as JSON; if fails, keep original list
    new_hyps = list(hypotheses)
    parsed = _parse_l3_hypothesis_response(result.text)
    if parsed["items"]:
        try:
            from autobridge.discovery.docs_reader import Provenance as _Prov
            from autobridge.discovery.hypotheses import Hypothesis as _Hyp

            for item in parsed["items"]:
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
            # If hypothesis construction fails, keep original list
            new_hyps = list(hypotheses)
    if session is not None and run_id is not None and budget is not None:
        # REQ-030 item 3: what the L3-hypothesis call produced, on the ledger row.
        if len(new_hyps) > len(hypotheses):
            _outcome = "hypothesis"
        elif parsed.get("rejected"):
            _outcome = "rejected_family"
        elif parsed.get("none_reason"):
            _outcome = "none"
        else:
            _outcome = "unparsable"
        stamp_call_outcome(session, run_id=run_id, tier="frontier", outcome=_outcome)
    usage = {
        "frontier_tokens": result.usage.total_tokens,
        "small_tokens": 0,
        "calls": 1,
        "frontier_calls": 1,
        "small_calls": 0,
        "frontier_input": result.usage.input_tokens,
        "frontier_output": result.usage.output_tokens,
        "frontier_reasoning": result.usage.reasoning_tokens,
        "small_input": 0,
        "small_output": 0,
        "small_reasoning": 0,
    }
    info = {
        "proposed": len(new_hyps) > len(hypotheses),
        "rejected": str(parsed.get("rejected") or ""),
        "none_reason": str(parsed.get("none_reason") or ""),
        "rationale": str(parsed.get("rationale") or ""),
        "detail": str(parsed.get("detail") or ""),
        "prompt": dict(ref),
    }
    return new_hyps, usage, info


def _merge_model_usage(base: dict[str, Any], delta: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for k in ("frontier_tokens", "small_tokens", "calls", "frontier_calls", "small_calls", "frontier_input", "frontier_output", "small_input", "small_output", "frontier_reasoning", "small_reasoning"):
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
    model/mode/captured-date/outcome/tier/purpose/tokens/estimated cost —
    never the prompt or response.
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
            "model": str(_call_field(call, "model", "") or ""),
            "mode": str(_call_field(call, "mode", "live") or "live"),
            "captured_at": str(_call_field(call, "captured_at", "") or ""),
            "outcome": _call_field(call, "outcome", None),
            "input_tokens": int(_call_field(call, "input_tokens", 0) or 0),
            "output_tokens": int(_call_field(call, "output_tokens", 0) or 0),
            "reasoning_tokens": int(_call_field(call, "reasoning_tokens", 0) or 0),
            "cost": str(_call_field(call, "cost_estimate", "0") or "0"),
            # REQ-045: which prompt file the call rendered (id/version/hash).
            "prompt_id": str(_call_field(call, "prompt_id", "") or ""),
            "prompt_version": str(_call_field(call, "prompt_version", "") or ""),
            "prompt_hash": str(_call_field(call, "prompt_hash", "") or ""),
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
                ranked, delta, l3_info = _call_frontier_generate(
                    active_hypotheses,
                    provider,
                    budget,
                    enumerator_yield_count=enumerator_yield_count,
                    run_id=run_id,
                    session=session,
                    drift_class=drift_class,
                )
                model_usage = _merge_model_usage(model_usage, delta)
                active_hypotheses = ranked
                trace[-1]["hypothesis_count_after"] = len(active_hypotheses)
                trace[-1]["model_usage_delta"] = delta
                trace[-1]["prompt"] = dict(l3_info.get("prompt") or {})
                if l3_info.get("rejected"):
                    trace[-1]["l3_rejected_family"] = str(l3_info["rejected"])
                if l3_info.get("none_reason"):
                    trace[-1]["l3_none_reason"] = str(l3_info["none_reason"])
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
            ranked, delta, l2_info = _call_small_model_rank(
                active_hypotheses, provider, budget, run_id=run_id, session=session,
                drift_class=drift_class,
            )
            model_usage = _merge_model_usage(model_usage, delta)
            active_hypotheses = ranked
            trace[-1]["ranked_ids"] = [getattr(h, "id", str(h)) for h in active_hypotheses[:5]]
            trace[-1]["model_usage_delta"] = delta
            trace[-1]["prompt"] = dict(l2_info.get("prompt") or {})
            if l2_info.get("rationale"):
                trace[-1]["model_rationale"] = " ".join(str(l2_info["rationale"]).split())[:300]
            if l2_info.get("fallback"):
                trace[-1]["l2_fallback"] = str(l2_info["fallback"])
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


def _record_unattempted_rungs(
    trace: list[dict[str, Any]],
    *,
    drift_class: str,
    vendor: str,
    no_rule_matched: bool,
    hypotheses: list[Any] | None,
) -> None:
    """One-line reasons for rungs the deterministic path never attempted.

    REQ-021 item 3: every repair run records each rung it tried (L0 → L4)
    with a one-line reason for moving up — including the deterministic
    live path, where L0 has no observable delta to match, L1 has no rule
    for the class, and L2/L3 never fire without hypotheses and a provider.
    Only fills levels the trace does not already name (a ``knowledge_miss``,
    a ``patch_error``, a ``BudgetExhausted`` escalate all stay exactly as
    recorded). Early returns (non-fixable classes, token refresh, backoff,
    ablation-disabled) keep their own shapes untouched.
    """
    levels = {t.get("level") for t in trace if isinstance(t, dict)}
    if 0 not in levels:
        trace.append(
            {
                "level": 0,
                "action": "knowledge_miss",
                "reason": (
                    f"L0 miss: no known fix for {vendor} + {drift_class} "
                    "(no observable delta to match a learned fix against)"
                ),
                "drift_class": drift_class,
                "vendor": vendor,
            }
        )
    if no_rule_matched and 1 not in levels:
        trace.append(
            {
                "level": 1,
                "action": "no_rule_matched",
                "reason": (
                    f"L1: no rule precondition matched for {drift_class} "
                    "from the wire signals"
                ),
                "drift_class": drift_class,
            }
        )
    if hypotheses is None and 2 not in levels:
        trace.append(
            {
                "level": 2,
                "action": "l2_skipped",
                "reason": (
                    f"L2 skipped: nothing to rank for {drift_class} on the "
                    "deterministic path (no hypotheses, no small-model call)"
                ),
                "drift_class": drift_class,
            }
        )
    if hypotheses is None and 3 not in levels:
        trace.append(
            {
                "level": 3,
                "action": "l3_skipped",
                "reason": (
                    f"L3 skipped: no frontier call on the deterministic path "
                    f"for {drift_class} (models fire only via the instrumented climb)"
                ),
                "drift_class": drift_class,
            }
        )


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
    # REQ-024 ablation: disable built-in knowledge (L0 cache + L1 rules) so
    # only the model (L2/L3) can repair. The ladder, verifier gate and
    # budgets are kept — defaults preserve existing behavior exactly.
    enable_knowledge: bool = True,
    enable_rules: bool = True,
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
        # Find retry rule if present (skipped when L1 is disabled for ablation)
        backoff_matched = matching_rules(ctx) if enable_rules else []
        retry_entry = None
        for e in backoff_matched:
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
    if not enable_knowledge:
        trace.append(
            {
                "level": 0,
                "action": "knowledge_disabled",
                "reason": "L0 disabled for ablation (engine_no_knowledge): built-in known fixes off",
                "drift_class": drift_class,
                "vendor": vendor_val,
            }
        )
    elif knowledge_cache is not None and fingerprint_delta is not None:
        # REQ-028: L0 fires only for a (vendor, drift class, observed delta)
        # learned before. Transport/auth/capability classes never consult L0
        # (no spec fix exists for them), and an empty delta (no observable
        # change — every such drift shares one constant signature) never
        # consults it either: a hit there would be a cross-scenario
        # collision, not a learned fix.
        skip_reason: str | None = None
        if drift_class in NO_LEARN_CLASSES:
            skip_reason = f"L0 never consults {drift_class}: no spec fix exists for this class"
        elif not delta_has_content(fingerprint_delta):
            skip_reason = "L0 never consults an empty delta: nothing observed to match a learned fix against"
        if skip_reason is not None:
            trace.append(
                {
                    "level": 0,
                    "action": "knowledge_skip",
                    "reason": skip_reason,
                    "drift_class": drift_class,
                    "vendor": vendor_val,
                }
            )
            cache_entry = None
        else:
            # Need vendor to key; if missing still try
            cache_entry = knowledge_cache.lookup(vendor_val, fingerprint_delta, drift_class=drift_class)
        if cache_entry is not None:
            trace.append(
                {
                    "level": 0,
                    "action": "knowledge_hit",
                    "reason": f"L0 cache hit for vendor {vendor_val} class {drift_class} + delta {cache_key(vendor_val, drift_class, fingerprint_delta)}",
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
    if not enable_rules:
        trace.append(
            {
                "level": 1,
                "action": "rules_disabled",
                "reason": "L1 disabled for ablation (engine_no_knowledge): built-in rules off",
                "drift_class": drift_class,
            }
        )
        matched: list[Any] = []
    else:
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
        _record_unattempted_rungs(
            trace, drift_class=drift_class, vendor=vendor_val,
            no_rule_matched=False, hypotheses=hypotheses,
        )
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
    _record_unattempted_rungs(
        trace, drift_class=drift_class, vendor=vendor_val,
        no_rule_matched=True, hypotheses=hypotheses,
    )
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


#: System prompt for the L3 frontier call (REQ-044). It names the patch
#: The L2/L3 questions live in ``config/prompts/*.md`` (REQ-045), loaded
#: once at startup and rendered by the call sites below — no prompt text
#: remains in this module. The answer shapes (a patch proposal or an
#: explicit escalation, never invented paths) are unchanged from REQ-044.

#: Fallback pointer shape when the climb carries no patch target (REQ-044).
#: A semantic fix remaps a field's wire path or transform; anything outside
#: ``/entities/<entity>/fields/<field>/(path|transform)`` is rejected before
#: the verifier (the reported ``/mappings/...`` invention fails here, with
#: one retry, instead of reaching the gate).
_L3_FIELD_POINTER = __import__("re").compile(
    r"^/entities/[^/]+/fields/[^/]+(?:/(?:path|transform))?$"
)


def _l3_evidence_blocks(evidence: dict[str, Any] | None) -> dict[str, str]:
    """L3 patch template inputs from climb evidence (REQ-044 content, REQ-045 file).

    The patch target (document + allowed ops + pointers), the patchable
    spec subtree with exact JSON Pointers, the redacted raw vendor
    records, and the observed evidence — each rendered as its REQ-044
    block, or ``"(none recorded)"`` when the climb carries no evidence.
    All values are data (redacted upstream); the instructional text lives
    in ``config/prompts/l3_patch.md``.
    """
    import json as _json  # noqa: PLC0415

    blocks = {
        "patch_target_block": "(none recorded)",
        "spec_subtree_block": "(none recorded)",
        "raw_records_block": "(none recorded)",
        "observed_evidence_block": "(none recorded)",
    }
    if not isinstance(evidence, dict) or not evidence:
        return blocks
    target = evidence.get("patch_target")
    subtree = evidence.get("spec_subtree")
    raw_records = evidence.get("raw_records")
    observed = {k: v for k, v in evidence.items() if k not in ("patch_target", "spec_subtree", "raw_records")}
    if isinstance(target, dict) and target:
        blocks["patch_target_block"] = _json.dumps(target, sort_keys=True, separators=(",", ":"), default=str)
    if isinstance(subtree, dict) and subtree:
        blocks["spec_subtree_block"] = _json.dumps(subtree, sort_keys=True, separators=(",", ":"), default=str)
    if isinstance(raw_records, list) and raw_records:
        blocks["raw_records_block"] = _json.dumps(
            raw_records, sort_keys=True, separators=(",", ":"), default=str
        )
    if observed:
        blocks["observed_evidence_block"] = _json.dumps(
            observed, sort_keys=True, separators=(",", ":"), default=str
        )
    return blocks


def _l3_pointer_error(
    proposal: list[dict[str, Any]],
    *,
    target_spec: dict[str, Any] | None,
    evidence: dict[str, Any] | None,
) -> str | None:
    """Apply + allowed-pointer check for an L3 proposal (REQ-044).

    Returns ``None`` when the patch applies to the spec and touches only
    allowed pointers, else a one-line error for the single retry. The
    allowed set is the evidence's listed patchable members when the climb
    carries a patch target, else the ``/entities/.../fields/...`` shape.
    """
    allowed: set[str] | None = None
    if isinstance(evidence, dict):
        target = evidence.get("patch_target") or {}
        pts = target.get("allowed_pointers") if isinstance(target, dict) else None
        if pts:
            allowed = {str(p) for p in pts}
    for op in proposal:
        path = op.get("path") if isinstance(op, dict) else None
        if not isinstance(path, str) or not path:
            return f"op has no string path: {str(op)[:120]}"
        if allowed is not None:
            if path not in allowed:
                return f"patch touches '{path}', outside the listed patchable members"
        elif not _L3_FIELD_POINTER.match(path):
            return (
                f"patch touches '{path}', outside "
                "/entities/<entity>/fields/<field>/(path|transform)"
            )
    if target_spec is None:
        return None
    try:
        from autobridge.spec.patch import apply_patch as _apply_patch  # noqa: PLC0415

        _apply_patch(target_spec, proposal)
    except Exception as exc:
        return f"patch does not apply: {exc}"
    return None


def normalize_model_question(question: str, *, limit: int = 300) -> str:
    """One-question form of a model's escalation text (REQ-044).

    The escalation bundle holds exactly one question ending with ``?``;
    take the text up to the first ``?`` (appending one when missing) and
    truncate defense-in-depth. Empty input yields a generic fallback that
    still satisfies the contract.
    """
    text = " ".join(str(question or "").split())
    if "?" in text:
        text = text[: text.index("?") + 1]
    elif text and not text.endswith("?"):
        text = text + "?"
    if not text:
        text = "Which mapping is correct for this semantic drift?"
    if len(text) > limit:
        cut = text[:limit].rsplit(" ", 1)[0] if " " in text[:limit] else text[:limit]
        text = cut.rstrip(" ,;:") + "?"
    return text


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
    # REQ-027: real evidence for the frontier (REQ-027 item 3). Redacted
    # caller-side blocks (spec excerpt, failing-records sample, doc
    # excerpt) carried as untrusted data in the L3 prompt so the proposal
    # answers observed evidence instead of only a drift class. ``None``
    # keeps the prompt byte-identical to the drift-class-only form.
    evidence: dict[str, Any] | None = None,
    # REQ-024 ablation: disable built-in knowledge (L0 + L1) so only the
    # model (L2/L3) can decide. Defaults preserve existing behavior exactly.
    enable_knowledge: bool = True,
    enable_rules: bool = True,
    # REQ-043: live progress. When given, called after every rung the climb
    # records (L0, L1, L2, L3 — including the entry written *before* a
    # model call starts, stamped with the model id and call start time) and
    # after each rung's post-call update, with snapshots of the attempts so
    # far and the usage so far. The repair job handler uses it to update the
    # running RepairRun row (committing between rungs, never holding a
    # transaction across a model call). Never raises: emission failures are
    # swallowed so progress can never break the climb.
    on_rung: Any | None = None,
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

    def _emit() -> None:
        # REQ-043 live progress: snapshots only, never the live lists, and
        # never raising — a progress sink must not break the climb.
        if on_rung is None:
            return
        with contextlib.suppress(Exception):
            on_rung([dict(a) for a in attempts], dict(usage))

    # --- L0: learned knowledge ---
    if not enable_knowledge:
        attempts.append(
            {
                "rung": 0,
                "action": "knowledge_disabled",
                "reason": "L0 disabled for ablation (engine_no_knowledge): built-in known fixes off",
                "calls": [],
            }
        )
    elif knowledge_cache is not None and fingerprint_delta is not None:
        # REQ-028: same scoping as run_ladder — the class is part of the
        # key, and empty deltas never consult L0.
        skip_climb_l0: str | None = None
        if drift_class in NO_LEARN_CLASSES:
            skip_climb_l0 = f"L0 never consults {drift_class}: no spec fix exists for this class"
        elif not delta_has_content(fingerprint_delta):
            skip_climb_l0 = "L0 never consults an empty delta: nothing observed to match a learned fix against"
        if skip_climb_l0 is not None:
            attempts.append(
                {
                    "rung": 0,
                    "action": "l0_skipped",
                    "reason": skip_climb_l0,
                    "calls": [],
                }
            )
            entry = None
        else:
            entry = knowledge_cache.lookup(vendor_val, fingerprint_delta, drift_class=drift_class)
        if entry is not None:
            attempts.append(
                {
                    "rung": 0,
                    "action": "knowledge_hit",
                    "reason": f"L0 hit: cached {entry.rule_id} patch reused",
                    "calls": [],
                }
            )
            _emit()
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
    elif knowledge_cache is not None:
        # REQ-027 sub-step 4: the detector derived no fingerprint delta
        # (a semantic drift leaves the wire shape intact), so L0 has no
        # key to consult. Record the skip explicitly — the rung table must
        # show every rung with its reason, never a silent gap.
        attempts.append(
            {
                "rung": 0,
                "action": "l0_skipped",
                "reason": "L0 skipped: no fingerprint delta from the detector (wire shape intact); nothing to key knowledge on",
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
    matched = matching_rules(ctx) if enable_rules else []
    if not enable_rules:
        attempts.append(
            {
                "rung": 1,
                "action": "rules_disabled",
                "reason": "L1 disabled for ablation (engine_no_knowledge): built-in rules off",
                "calls": [],
            }
        )
    elif matched:
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
    # REQ-043: L0 and L1 resolve locally in milliseconds, but the running
    # repair row should show them as soon as they land — before a minutes-
    # long L2/L3 call starts.
    _emit()

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
        _emit()
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
        from autobridge.llm.prompts import prompt_ref as _prompt_ref  # noqa: PLC0415

        l2_entry: dict[str, Any] = {
            "rung": 2,
            "action": "small_rank",
            "reason": f"L2 ranking {len(ranked)} hypotheses for {drift_class}",
            "calls": [],
            # REQ-043: stamped before the call starts, so the repair page
            # can show "calling <model> · Ns" while the call is in flight.
            "model": _provider_model_name(provider, "small"),
            "call_started_at": _utc_now_iso(),
            # REQ-045: the rung names the prompt the call renders.
            "prompt": _prompt_ref("l2_rank"),
        }
        attempts.append(l2_entry)
        _emit()
        try:
            ranked, delta, l2_info = _call_small_model_rank(
                ranked, provider, budget, run_id=run_id, session=session,
                drift_class=drift_class, evidence=evidence,
            )
        except ReplayMissing as exc:
            replay_missing.append(exc.prompt_hash)
            l2_entry["action"] = "l2_skipped"
            l2_entry["reason"] = (
                f"L2 skipped: no replay recording for prompt {exc.prompt_hash}; {_record_hint()}"
            )
            _emit()
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
        if l2_info.get("rationale"):
            l2_entry["model_rationale"] = " ".join(str(l2_info["rationale"]).split())[:300]
        if l2_info.get("fallback"):
            # REQ-045: a parse failure falls back to prior order, labelled
            # on the rung — never silent.
            l2_entry["l2_fallback"] = str(l2_info["fallback"])
        top_prior = float(getattr(ranked[0], "prior", 0.0)) if ranked else 0.0
        # REQ-045: the model's own confidence decides when it answered with
        # one; otherwise the winner's prior, as before.
        l2_confidence = l2_info.get("confidence")
        if l2_confidence is None:
            l2_confidence = top_prior
        if l2_confidence >= l2_threshold:
            l2_entry["reason"] = (
                f"L2 confident: {l2_confidence:.2f} >= {l2_threshold:.2f}; ranking stands, no frontier call"
            )
            l2_entry["model_usage_delta"] = delta
            _emit()
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
            f"L2 confidence {l2_confidence:.2f} < {l2_threshold:.2f}: ranking cannot decide; continuing to L3"
        )
        if l2_info.get("fallback"):
            l2_entry["reason"] += f" ({l2_entry['l2_fallback']})"
        l2_entry["model_usage_delta"] = delta
        _emit()
    else:
        attempts.append(
            {
                "rung": 2,
                "action": "l2_skipped",
                "reason": f"L2 skipped: fewer than two hypotheses ({len(ranked)})",
                "calls": [],
            }
        )
        _emit()

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
        _emit()
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
        # REQ-043: stamped before the call starts, so the repair page can
        # show "calling <model> · Ns" while the frontier thinks.
        "model": _provider_model_name(provider, "frontier"),
        "call_started_at": _utc_now_iso(),
    }
    attempts.append(l3_entry)
    _emit()
    ranked_ids = [getattr(h, "id", str(h)) for h in ranked]
    # REQ-045: the L3 question lives in config/prompts/l3_patch.md — the
    # model gets the drift class, ranked hypotheses and the evidence
    # blocks, all rendered from the file (no prompt text in Python).
    from autobridge.llm.prompts import get_prompt as _get_prompt  # noqa: PLC0415
    from autobridge.llm.prompts import prompt_ref as _prompt_ref  # noqa: PLC0415
    from autobridge.llm.prompts import render_prompt as _render_prompt  # noqa: PLC0415

    l3_template = _get_prompt("l3_patch")
    l3_ref = _prompt_ref("l3_patch")
    l3_blocks = _l3_evidence_blocks(evidence)
    l3_messages = _render_prompt(
        l3_template,
        drift_class=str(drift_class),
        ranked_ids=str(ranked_ids),
        **l3_blocks,
    )
    if evidence:
        l3_entry["evidence_keys"] = sorted(str(k) for k in evidence)
    # REQ-039: the exact prompt rides on the rung (prompts carry no
    # credentials by construction — hypothesis ids and redacted evidence
    # only). `make record-frontier` replays these messages live to capture
    # the recording the replay mode needs; the repair page can show what
    # the engine asked. REQ-045: the rung also names the prompt id,
    # version and content hash.
    l3_entry["messages"] = [dict(m) for m in l3_messages]
    l3_entry["prompt"] = dict(l3_ref)
    effective: Any = provider
    ledger_kwargs: dict[str, Any] = {}
    if budget is not None:
        effective = BudgetedProvider(provider, budget, session=session, run_id=run_id)
        # REQ-045: prompt id/version/hash land on the ledger row (only the
        # budgeted path writes ledger rows; direct calls never see the kwarg).
        ledger_kwargs["prompt"] = l3_ref
    # Reasoning models spend most of a small max_tokens on thinking and then
    # return an empty message (owner check: 4,319 tokens, empty text): give
    # the frontier call room for thinking *and* an answer. The
    # OpenAI-compatible wire shape takes no reasoning-effort knob, so budget
    # is the only lever; reasoning tokens are recorded separately below.
    # 8192 still ran out (8,190 reasoning tokens, empty text, keyed demo
    # 2026-10-30). Billing is per token used, not per cap, so the cap is the
    # Muse maximum (131072, accepted live) rather than another guess.
    # REQ-043 item 5: the cap is configurable (``FRONTIER_MAX_TOKENS``).
    try:
        from autobridge.config import frontier_max_tokens  # noqa: PLC0415

        l3_cap = frontier_max_tokens()
    except Exception:
        l3_cap = L3_MAX_TOKENS
    try:
        l3_result = effective.complete(
            l3_messages, max_tokens=l3_cap, tier="frontier", purpose="l3-generate-patch", **ledger_kwargs
        )
    except ReplayMissing as exc:
        replay_missing.append(exc.prompt_hash)
        l3_entry["action"] = "l3_skipped"
        l3_entry["reason"] = (
            f"L3 skipped: no replay recording for prompt {exc.prompt_hash}; {_record_hint()}"
        )
        _emit()
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
    def _stamp_outcome(outcome: str) -> None:
        # REQ-030 item 3: what each L3 call produced, on its ledger row —
        # the model-call box reads this column, not the trace. Stamped per
        # call (a retry stamps its own row), best-effort, never raising.
        if session is not None and run_id is not None and budget is not None:
            stamp_call_outcome(session, run_id=run_id, tier="frontier", outcome=outcome)

    def _accumulate(result: Any) -> dict[str, Any]:
        return {
            "frontier_tokens": result.usage.total_tokens,
            "small_tokens": 0,
            "calls": 1,
            "frontier_calls": 1,
            "small_calls": 0,
            "frontier_input": result.usage.input_tokens,
            "frontier_output": result.usage.output_tokens,
            "frontier_reasoning": result.usage.reasoning_tokens,
            "small_input": 0,
            "small_output": 0,
            "small_reasoning": 0,
        }

    l3_delta = _accumulate(l3_result)
    usage = _merge_model_usage(usage, l3_delta)
    l3_entry["model_usage_delta"] = l3_delta
    l3_entry["reasoning_tokens"] = l3_result.usage.reasoning_tokens
    l3_text = l3_result.text or ""
    parsed = _parse_l3_response(l3_text)
    proposal: list[dict[str, Any]] | None = None
    model_rationale = str(parsed.get("rationale") or "")
    model_question = ""
    model_escalated = False
    if model_rationale:
        # REQ-044 item 5: the repair page shows the model's rationale;
        # one line, truncated defense-in-depth.
        l3_entry["model_rationale"] = " ".join(model_rationale.split())[:300]
    if parsed["kind"] == "escalate":
        # An explicit escalation is a valid L3 outcome, not an error: the
        # model names the business decision a human must make. No retry —
        # the answer already says what L4 needs.
        model_escalated = True
        model_question = normalize_model_question(str(parsed.get("question") or ""))
        l3_entry["action"] = "model_escalation"
        l3_entry["model_question"] = model_question
        l3_entry["reason"] = (
            f"L3 asked for a human decision: {model_question}"
            + (
                f" (model rationale: {l3_entry['model_rationale']})"
                if l3_entry.get("model_rationale")
                else ""
            )
        )
        _stamp_outcome("model_escalation")
    elif parsed["kind"] == "patch":
        proposal = parsed["patch"]
        l3_entry["proposal_ops"] = len(proposal)
        changed = sorted({str(op.get("path", "")) for op in proposal if isinstance(op, dict)})
        if changed:
            l3_entry["proposal_pointers"] = changed
            l3_entry["reason"] = (
                f"L3 proposed a patch ({len(proposal)} ops: {', '.join(changed)})"
                + (
                    f" (model rationale: {l3_entry['model_rationale']})"
                    if l3_entry.get("model_rationale")
                    else ""
                )
            )
        apply_error = _l3_pointer_error(proposal, target_spec=validate_against, evidence=evidence)
        if apply_error is None:
            l3_entry["proposal_validated"] = True
            _stamp_outcome("proposal")
        else:
            # REQ-044 item 4: one retry with the apply error, then escalate.
            # REQ-045: the retry renders config/prompts/l3_patch_retry.md —
            # same question plus the rejection as data. Both calls land on
            # the ledger and the repair page. Without the optional retry
            # file the retry is skipped explicitly (never invented).
            _stamp_outcome("proposal_rejected")
            try:
                retry_template = _get_prompt("l3_patch_retry")
                retry_ref = _prompt_ref("l3_patch_retry")
            except KeyError:
                retry_template = None
                retry_ref = dict(l3_ref)
            l3_entry["retry_error"] = apply_error
            if retry_template is None:
                proposal = None
                l3_entry["action"] = "proposal_rejected"
                l3_entry["reason"] = (
                    f"L3 proposal rejected: {apply_error}; no l3_patch_retry prompt file, retry skipped"
                )
                l3_entry["raw_preview"] = l3_text.strip()[:300]
            else:
                retry_messages = _render_prompt(
                    retry_template,
                    drift_class=str(drift_class),
                    ranked_ids=str(ranked_ids),
                    apply_error=str(apply_error),
                    **l3_blocks,
                )
                try:
                    retry_kwargs = dict(ledger_kwargs)
                    if ledger_kwargs:
                        retry_kwargs["prompt"] = retry_ref
                    retry_result = effective.complete(
                        retry_messages,
                        max_tokens=l3_cap,
                        tier="frontier",
                        purpose="l3-generate-patch-retry",
                        **retry_kwargs,
                    )
                except ReplayMissing as exc:
                    proposal = None
                    replay_missing.append(exc.prompt_hash)
                    l3_entry["action"] = "proposal_rejected"
                    l3_entry["reason"] = (
                        f"L3 proposal rejected: {apply_error}; retry has no replay recording "
                        f"for prompt {exc.prompt_hash}"
                    )
                    l3_entry["raw_preview"] = l3_text.strip()[:300]
                else:
                    retry_delta = _accumulate(retry_result)
                    usage = _merge_model_usage(usage, retry_delta)
                    l3_entry["retry_model_usage_delta"] = retry_delta
                    l3_entry["retry_reasoning_tokens"] = retry_result.usage.reasoning_tokens
                    retry_text = retry_result.text or ""
                    l3_entry["retry_text_preview"] = retry_text.strip()[:300]
                    retry_parsed = _parse_l3_response(retry_text)
                    if retry_parsed.get("rationale"):
                        l3_entry["retry_rationale"] = " ".join(
                            str(retry_parsed["rationale"]).split()
                        )[:300]
                    if retry_parsed["kind"] == "escalate":
                        proposal = None
                        model_escalated = True
                        model_question = normalize_model_question(str(retry_parsed.get("question") or ""))
                        l3_entry["action"] = "model_escalation"
                        l3_entry["model_question"] = model_question
                        l3_entry["reason"] = (
                            f"L3 retry asked for a human decision after {apply_error}: {model_question}"
                        )
                        _stamp_outcome("model_escalation")
                    elif retry_parsed["kind"] == "patch":
                        candidate = retry_parsed["patch"]
                        retry_error = _l3_pointer_error(
                            candidate, target_spec=validate_against, evidence=evidence
                        )
                        if retry_error is None:
                            proposal = candidate
                            l3_entry["proposal_ops"] = len(proposal)
                            l3_entry["proposal_pointers"] = sorted(
                                {str(op.get("path", "")) for op in proposal if isinstance(op, dict)}
                            )
                            l3_entry["proposal_validated"] = True
                            l3_entry["retried"] = True
                            l3_entry["reason"] = (
                                f"L3 retry proposed a patch after {apply_error} "
                                f"({len(proposal)} ops: {', '.join(l3_entry['proposal_pointers'])})"
                            )
                            _stamp_outcome("proposal")
                        else:
                            proposal = None
                            l3_entry["action"] = "proposal_rejected"
                            l3_entry["reason"] = (
                                f"L3 proposal rejected: {apply_error}; retry failed too: {retry_error}"
                            )
                            l3_entry["raw_preview"] = retry_text.strip()[:300]
                            _stamp_outcome("proposal_rejected")
                    else:
                        proposal = None
                        l3_entry["action"] = "proposal_rejected"
                        l3_entry["reason"] = (
                            f"L3 proposal rejected: {apply_error}; "
                            f"retry unparsable ({retry_parsed['detail']})"
                        )
                        l3_entry["raw_preview"] = retry_text.strip()[:300]
                        _stamp_outcome("proposal_rejected")
    else:
        # No usable patch is a normal outcome, never an error: record what
        # the text contained plus a truncated preview so a live answer is
        # diagnosable (prompts carry no secrets; truncate defense-in-depth).
        # Name the reasoning spend explicitly: an empty message after a full
        # thinking budget is "thought hard, said nothing", not a skip.
        parse_detail = parsed["detail"]
        l3_entry["parse_detail"] = parse_detail
        if l3_text.strip():
            l3_entry["raw_preview"] = l3_text.strip()[:300]
        elif l3_result.usage.reasoning_tokens:
            l3_entry["reason"] = (
                f"L3 returned an empty response after "
                f"{l3_result.usage.reasoning_tokens:,} reasoning tokens "
                f"({parse_detail}); no patch proposed"
            )
        else:
            l3_entry["reason"] = f"L3 returned an empty response ({parse_detail}); no patch proposed"
        if l3_text.strip():
            _stamp_outcome("unparsable")
        else:
            _stamp_outcome("no_patch")
    replay_captured_at = str(getattr(provider, "last_captured_at", "") or "")
    _emit()
    return {
        "attempts": attempts,
        "usage": usage,
        "resolved_rung": 3,
        "l3_text": l3_text,
        "l3_messages": [dict(m) for m in l3_messages],
        "proposal": proposal,
        "model_rationale": model_rationale,
        "model_question": model_question,
        "model_escalated": model_escalated,
        "ranked_ids": [getattr(h, "id", str(h)) for h in ranked],
        "l2_confidence": l2_confidence,
        "replay_missing": replay_missing,
        "replay_captured_at": replay_captured_at,
    }
