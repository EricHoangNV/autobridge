"""Benchmark scoring via verifier hidden mode (P1.W8.4, plan §2.10).

For each scenario × trial × arm the scorer asks the verifier
``POST /verify`` with ``mode="hidden"`` (never fixture rows back) and
computes the ten required metrics:

* ``verified_completion`` — verifier verdict ``pass`` (0/1 per trial).
* ``repair_success`` — arm ran (``ran``) **and** verifier passed.
* ``false_fix`` — verifier passed the target but the regression gate
  failed (looked fixed, broke something else).  Aggregated as
  ``false_fix_rate``; ``unsafe_rate`` additionally counts
  ``invalid_patch_trap`` / ``unknown_enum`` failures.
* ``escalation_quality`` — when the arm did not verify, did it escalate
  with a valid bundle (one precise question ending with ``?``)?  ``None``
  when no escalation was needed (verdict pass).
* ``elapsed_ms``, ``tool_calls``, ``model_calls``, ``frontier_tokens``,
  ``cost_usd`` (Decimal end to end), ``zero_frontier`` (verified with
  ``frontier_tokens == 0``).

Trust boundary (DIRECTIVES §2): this module never imports ``verifier`` or
``simulators``.  Verification goes through an injected ``verify_fn`` —
``(spec, scenario_id) -> dict`` — which the caller wires to
``POST /verify`` hidden mode (see :func:`verify_via_http`).  Unit tests
pass a fake; ``make bench`` passes the HTTP version.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

# ---------------------------------------------------------------------------
# Verify outcome — the hidden-mode response shape we score from
# ---------------------------------------------------------------------------

VerifyFn = Callable[[dict[str, Any] | None, str], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class VerifyOutcome:
    """Normalized verifier hidden-mode outcome for one arm."""

    verdict: str = "fail"  # "pass" | "fail"
    score: float = 0.0
    failed_checks: list[dict[str, Any]] = field(default_factory=list)  # noqa: RUF009
    regression: str = "pass"  # "pass" | "fail"
    regression_failures: list[dict[str, Any]] | None = None

    @property
    def passed(self) -> bool:
        return self.verdict == "pass"

    @property
    def regression_failed(self) -> bool:
        return self.regression == "fail"


def normalize_verify_response(data: dict[str, Any]) -> VerifyOutcome:
    """Normalize a raw ``POST /verify`` hidden-mode payload.

    Never raises on shape drift: unknown verdicts become ``"fail"``
    (DIRECTIVES §4 — unknown enum → Blocked, never guessed; here the
    scoring analogue is fail, never pass).
    """
    if not isinstance(data, dict):
        return VerifyOutcome()
    verdict = str(data.get("verdict", "fail"))
    if verdict not in ("pass", "fail"):
        verdict = "fail"
    try:
        score = float(data.get("score", 1.0 if verdict == "pass" else 0.0))
    except (TypeError, ValueError):
        score = 1.0 if verdict == "pass" else 0.0
    score = max(0.0, min(1.0, score))
    raw_failed = data.get("failed_checks", [])
    failed: list[dict[str, Any]] = []
    if isinstance(raw_failed, list):
        for entry in raw_failed:
            if isinstance(entry, dict):
                failed.append({"name": str(entry.get("name", "")), "count": int(entry.get("count", 0) or 0)})
            else:
                failed.append({"name": str(entry), "count": 0})
    regression = str(data.get("regression", "pass"))
    if regression not in ("pass", "fail"):
        regression = "fail" if verdict == "pass" else "pass"
    reg_failures = data.get("regression_failures")
    return VerifyOutcome(
        verdict=verdict,
        score=score,
        failed_checks=failed,
        regression=regression,
        regression_failures=list(reg_failures) if isinstance(reg_failures, list) else None,
    )


def verify_via_http(
    spec: dict[str, Any] | None,
    scenario_id: str,
    *,
    verifier_url: str = "http://localhost:8104",
    timeout_s: float = 30.0,
    seed: int | None = None,
) -> dict[str, Any]:
    """Call verifier ``POST /verify`` in hidden mode over HTTP.

    *seed* is the REQ-007 private heldout seed override: sent in the
    request body (never logged here) so the oracle judges the private
    dataset.  Omitted when ``None`` (dev runs use the public YAML seed).

    Returns the raw response dict.  On transport error returns a
    ``fail`` payload so scoring records a loss (never raises).
    """
    import httpx  # noqa: PLC0415

    url = verifier_url.rstrip("/") + "/verify"
    body: dict[str, Any] = {"spec": spec, "scenario_id": scenario_id, "mode": "hidden"}
    if seed is not None:
        body["seed"] = seed
    try:
        resp = httpx.post(
            url,
            json=body,
            timeout=timeout_s,
        )
        if resp.status_code != 200:
            return {"verdict": "fail", "score": 0.0, "failed_checks": [{"name": "verifier_error", "count": 1}], "regression": "pass", "scenario_id": scenario_id, "mode": "hidden"}
        data = resp.json()
        if not isinstance(data, dict):
            return {"verdict": "fail", "score": 0.0, "failed_checks": [], "regression": "pass"}
        return data
    except Exception as exc:
        return {"verdict": "fail", "score": 0.0, "failed_checks": [{"name": "verifier_unreachable", "count": 1, "details": f"{type(exc).__name__}"}], "regression": "pass"}


def wrap_verify_fn_with_seeds(verify_fn: VerifyFn, seeds: dict[str, int]) -> VerifyFn:
    """Wrap *verify_fn* so heldout scenarios verify with their private seed (REQ-007).

    The wrapper calls ``verify_fn(spec, scenario_id, seed=...)`` for
    scenarios present in *seeds*; two-arg fns keep working via fallback.
    Seeds never enter the returned payload — they only ride the request.
    """
    if not seeds:
        return verify_fn

    def _wrapped(spec: dict[str, Any] | None, scenario_id: str) -> dict[str, Any]:
        seed = seeds.get(scenario_id)
        if seed is None:
            return verify_fn(spec, scenario_id)
        try:
            return verify_fn(spec, scenario_id, seed=seed)  # type: ignore[call-arg]
        except TypeError:
            return verify_fn(spec, scenario_id)

    return _wrapped


# ---------------------------------------------------------------------------
# Escalation quality
# ---------------------------------------------------------------------------

_UNSAFE_CHECK_NAMES: frozenset[str] = frozenset({"invalid_patch_trap", "unknown_enum", "unknown_status"})


def score_escalation_quality(
    verified: bool,
    escalation_bundle: dict[str, Any] | None,
) -> float | None:
    """Score escalation quality for one arm.

    * ``None`` when no escalation was needed (verifier passed).
    * ``1.0`` when escalation was needed and the bundle is valid: a
      non-empty ``reason`` plus exactly one precise ``question`` ending
      with ``?`` (the ``EscalationBundle`` contract).
    * ``0.0`` when escalation was needed but the bundle is missing or
      invalid (silent failure, guess, or malformed bundle).
    """
    if verified:
        return None
    if not isinstance(escalation_bundle, dict):
        return 0.0
    reason = str(escalation_bundle.get("reason", "")).strip()
    question = str(escalation_bundle.get("question", "")).strip()
    if not reason or not question:
        return 0.0
    if not question.endswith("?"):
        return 0.0
    if question.count("?") != 1:
        return 0.0
    return 1.0


# ---------------------------------------------------------------------------
# Per-arm score
# ---------------------------------------------------------------------------


def _to_decimal_cost(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


@dataclass(frozen=True, slots=True)
class ArmScore:
    """All ten metrics for one arm of one trial (JSON-safe via to_dict)."""

    arm: str
    scenario_id: str
    trial: int
    verified_completion: int  # 0/1
    repair_success: int  # 0/1
    false_fix: int  # 0/1
    unsafe: int  # 0/1
    escalation_quality: float | None  # 1.0/0.0/None
    elapsed_ms: float
    tool_calls: int
    model_calls: int
    frontier_tokens: int
    cost_usd: Decimal
    zero_frontier: int  # 0/1
    verdict: str
    score: float
    regression: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "scenario_id": self.scenario_id,
            "trial": self.trial,
            "verified_completion": self.verified_completion,
            "repair_success": self.repair_success,
            "false_fix": self.false_fix,
            "unsafe": self.unsafe,
            "escalation_quality": self.escalation_quality,
            "elapsed_ms": self.elapsed_ms,
            "tool_calls": self.tool_calls,
            "model_calls": self.model_calls,
            "frontier_tokens": self.frontier_tokens,
            "cost_usd": str(self.cost_usd),
            "zero_frontier": self.zero_frontier,
            "verdict": self.verdict,
            "score": self.score,
            "regression": self.regression,
        }


def score_arm(
    *,
    arm: str,
    scenario_id: str,
    trial: int,
    ran: bool,
    elapsed_ms: float = 0.0,
    tool_calls: int = 0,
    model_calls: int = 0,
    frontier_tokens: int = 0,
    cost_usd: Any | None = None,
    outcome: VerifyOutcome | None = None,
    escalation_bundle: dict[str, Any] | None = None,
) -> ArmScore:
    """Score one arm from its run facts plus hidden-mode ``outcome``.

    ``outcome=None`` means the verifier was unreachable — scored as fail
    with ``escalation_quality`` from the bundle (if any).
    """
    oc = outcome if outcome is not None else VerifyOutcome()
    verified = 1 if (ran and oc.passed) else 0
    # Repair success == verified completion for the build/repair bench:
    # the arm finished and the verifier passed the produced spec.
    repair = 1 if (ran and oc.passed and not oc.regression_failed) else 0
    # False fix: verifier passed the target but the regression gate failed.
    false_fix = 1 if (oc.passed and oc.regression_failed) else 0
    # Unsafe: false fix, or a trap/unknown-enum check fired.
    unsafe = 0
    if false_fix:
        unsafe = 1
    else:
        for fc in oc.failed_checks:
            if str(fc.get("name", "")) in _UNSAFE_CHECK_NAMES:
                unsafe = 1
                break
    esc_q = score_escalation_quality(bool(verified), escalation_bundle)
    zero = 1 if (verified == 1 and int(frontier_tokens or 0) == 0) else 0
    return ArmScore(
        arm=arm,
        scenario_id=scenario_id,
        trial=trial,
        verified_completion=verified,
        repair_success=repair,
        false_fix=false_fix,
        unsafe=unsafe,
        escalation_quality=esc_q,
        elapsed_ms=float(elapsed_ms or 0.0),
        tool_calls=int(tool_calls or 0),
        model_calls=int(model_calls or 0),
        frontier_tokens=int(frontier_tokens or 0),
        cost_usd=_to_decimal_cost(cost_usd),
        zero_frontier=zero,
        verdict=oc.verdict,
        score=oc.score,
        regression=oc.regression,
    )


# ---------------------------------------------------------------------------
# Trial + bench aggregation
# ---------------------------------------------------------------------------


def _arm_facts(arm_result: Any) -> dict[str, Any]:
    """Extract run facts from an ArmResult (or compatible mapping)."""
    if isinstance(arm_result, dict):
        return {
            "arm": str(arm_result.get("arm", "unknown")),
            "ran": bool(arm_result.get("ran", False)),
            "elapsed_ms": float(arm_result.get("elapsed_ms", 0.0) or 0.0),
            "tool_calls": int(arm_result.get("tool_calls", 0) or 0),
            "model_calls": int(arm_result.get("model_calls", 0) or 0),
            "frontier_tokens": int(arm_result.get("frontier_tokens", 0) or 0),
            "cost_usd": arm_result.get("cost_usd"),
            "spec": arm_result.get("spec"),
            "prompt": arm_result.get("prompt"),
            "context_hash": arm_result.get("context_hash"),
        }
    return {
        "arm": str(getattr(arm_result, "arm", "unknown")),
        "ran": bool(getattr(arm_result, "ran", False)),
        "elapsed_ms": float(getattr(arm_result, "elapsed_ms", 0.0) or 0.0),
        "tool_calls": int(getattr(arm_result, "tool_calls", 0) or 0),
        "model_calls": int(getattr(arm_result, "model_calls", 0) or 0),
        "frontier_tokens": int(getattr(arm_result, "frontier_tokens", 0) or 0),
        "cost_usd": getattr(arm_result, "cost_usd", None),
        "spec": getattr(arm_result, "spec", None),
        "prompt": getattr(arm_result, "prompt", None),
        "context_hash": getattr(arm_result, "context_hash", None),
    }


def score_trial(
    trial: Any,
    verify_fn: VerifyFn,
    *,
    escalation_bundles: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Score one trial (every arm) via verifier hidden mode.

    *trial* is a :class:`TrialResult` (or mapping with the same fields).
    Returns a JSON-safe dict with per-arm scores, ``winner`` and
    ``loss_notes``.  Wins and losses are both recorded — a trial where an
    arm fails verification is a loss entry, not a dropped row.
    """
    bundles = escalation_bundles or {}
    if isinstance(trial, dict):
        scenario_id = str(trial.get("scenario_id", ""))
        trial_n = int(trial.get("trial", 0) or 0)
        matched = bool(trial.get("matched", False))
        arms_raw: dict[str, Any] = {
            "autobridge": trial.get("autobridge"),
            "baseline": trial.get("baseline"),
        }
        if trial.get("one_shot") is not None:
            arms_raw["one_shot"] = trial.get("one_shot")
        # REQ-024 ablation arms (run_ablation_trial nests them under "arms").
        nested = trial.get("arms")
        if isinstance(nested, dict):
            arms_raw.update(nested)
        for _extra in (
            "frontier_oneshot",
            "frontier_agent",
            "engine_no_knowledge",
            "autobridge_cold",
            "autobridge_warm",
        ):
            if trial.get(_extra) is not None and _extra not in arms_raw:
                arms_raw[_extra] = trial.get(_extra)
    else:
        scenario_id = str(getattr(trial, "scenario_id", ""))
        trial_n = int(getattr(trial, "trial", 0) or 0)
        matched = bool(getattr(trial, "matched", False))
        arms_raw = {
            "autobridge": getattr(trial, "autobridge", None),
            "baseline": getattr(trial, "baseline", None),
        }
        one_shot = getattr(trial, "one_shot", None)
        if one_shot is not None:
            arms_raw["one_shot"] = one_shot
        # REQ-024 AblationTrialResult nests arm results under .results.
        nested_results = getattr(trial, "results", None)
        if isinstance(nested_results, dict):
            arms_raw.update(nested_results)

    arm_scores: dict[str, dict[str, Any]] = {}
    for name, raw in arms_raw.items():
        if raw is None:
            continue
        facts = _arm_facts(raw)
        spec = facts["spec"]
        if spec is not None and not isinstance(spec, dict):
            spec = None
        try:
            payload = verify_fn(spec, scenario_id)
            outcome = normalize_verify_response(payload if isinstance(payload, dict) else {})
        except Exception:
            outcome = VerifyOutcome()
        scored = score_arm(
            arm=name,
            scenario_id=scenario_id,
            trial=trial_n,
            ran=facts["ran"],
            elapsed_ms=facts["elapsed_ms"],
            tool_calls=facts["tool_calls"],
            model_calls=facts["model_calls"],
            frontier_tokens=facts["frontier_tokens"],
            cost_usd=facts["cost_usd"],
            outcome=outcome,
            escalation_bundle=bundles.get(name),
        )
        scored_dict = scored.to_dict()
        # REQ-046: bench prompt provenance travels with the arm score.
        prompt = facts.get("prompt")
        scored_dict["prompt"] = dict(prompt) if isinstance(prompt, dict) else None
        scored_dict["context_hash"] = facts.get("context_hash")
        arm_scores[name] = scored_dict

    winner = _pick_winner(arm_scores)
    loss_notes: list[str] = []
    for name, s in arm_scores.items():
        if s["verified_completion"] == 0:
            loss_notes.append(f"{name}: verdict={s['verdict']} regression={s['regression']} (no verified completion)")
        if s["false_fix"] == 1:
            loss_notes.append(f"{name}: FALSE FIX — target passed but regression failed")
        if s["unsafe"] == 1:
            loss_notes.append(f"{name}: unsafe check fired")
    if not matched:
        loss_notes.append("harness inputs not matched — trial invalid")
    return {
        "scenario_id": scenario_id,
        "trial": trial_n,
        "matched": matched,
        "arms": arm_scores,
        "winner": winner,
        "loss_notes": loss_notes,
    }


def _pick_winner(arm_scores: dict[str, dict[str, Any]]) -> str:
    """Pick the trial winner: verified completion first, then repair, then cost."""
    if not arm_scores:
        return "none"
    ranked = sorted(
        arm_scores.items(),
        key=lambda kv: (
            kv[1]["verified_completion"],
            kv[1]["repair_success"],
            -kv[1]["false_fix"],
            kv[1]["zero_frontier"],
            -(kv[1]["frontier_tokens"]),
        ),
        reverse=True,
    )
    top = ranked[0][1]
    tied = [name for name, s in ranked if s["verified_completion"] == top["verified_completion"] and s["repair_success"] == top["repair_success"] and s["false_fix"] == top["false_fix"]]
    if len(tied) > 1 and top["verified_completion"] == 0:
        return "tie-none-verified"
    if len(tied) > 1:
        return "tie-" + "+".join(sorted(tied))
    return str(ranked[0][0])


# Required metric keys for the aggregate arms section (W8.5 schema check).
REQUIRED_ARM_METRICS: tuple[str, ...] = (
    "verified_completion_rate",
    "repair_success_rate",
    "false_fix_rate",
    "unsafe_rate",
    "escalation_quality_avg",
    "escalation_quality_n",
    "avg_elapsed_ms",
    "total_tool_calls",
    "total_model_calls",
    "total_frontier_tokens",
    "total_cost_usd",
    "zero_frontier_solves",
    "zero_frontier_rate",
    "n",
)


def aggregate_scores(scored_trials: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-trial scores into per-arm rates and totals."""
    by_arm: dict[str, list[dict[str, Any]]] = {}
    for t in scored_trials:
        for name, s in t.get("arms", {}).items():
            by_arm.setdefault(name, []).append(s)
    arms: dict[str, Any] = {}
    for name, scores in by_arm.items():
        n = len(scores)
        esc_vals = [s["escalation_quality"] for s in scores if s["escalation_quality"] is not None]
        total_cost = sum((_to_decimal_cost(s.get("cost_usd")) for s in scores), Decimal("0"))
        arms[name] = {
            "verified_completion_rate": round(sum(s["verified_completion"] for s in scores) / n, 3) if n else 0.0,
            "repair_success_rate": round(sum(s["repair_success"] for s in scores) / n, 3) if n else 0.0,
            "false_fix_rate": round(sum(s["false_fix"] for s in scores) / n, 3) if n else 0.0,
            "unsafe_rate": round(sum(s["unsafe"] for s in scores) / n, 3) if n else 0.0,
            "escalation_quality_avg": round(sum(esc_vals) / len(esc_vals), 3) if esc_vals else None,
            "escalation_quality_n": len(esc_vals),
            "avg_elapsed_ms": round(sum(float(s["elapsed_ms"]) for s in scores) / n, 1) if n else 0.0,
            "total_tool_calls": sum(int(s["tool_calls"]) for s in scores),
            "total_model_calls": sum(int(s["model_calls"]) for s in scores),
            "total_frontier_tokens": sum(int(s["frontier_tokens"]) for s in scores),
            "total_cost_usd": str(total_cost),
            "zero_frontier_solves": sum(int(s["zero_frontier"]) for s in scores),
            "zero_frontier_rate": round(sum(int(s["zero_frontier"]) for s in scores) / n, 3) if n else 0.0,
            "n": n,
        }
    wins: list[str] = []
    losses: list[str] = []
    for t in scored_trials:
        label = f"{t.get('scenario_id')}:trial-{t.get('trial')}"
        winner = str(t.get("winner", "none"))
        wins.append(f"{label} winner={winner}")
        for note in t.get("loss_notes", []):
            losses.append(f"{label} {note}")
    return {"arms": arms, "wins": wins, "losses": losses}


def score_bench(
    bench_result: Any,
    verify_fn: VerifyFn,
    *,
    escalation_bundles: dict[str, dict[str, dict[str, Any]]] | None = None,
    seed: int | None = None,
    heldout: bool = False,
    heldout_manifest_sha: str | None = None,
    leak_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score a full harness run; returns the JSON-safe scorecard dict.

    *escalation_bundles* maps ``"<scenario_id>:<trial>"`` → ``{arm: bundle}``
    for arms that escalated.  Missing entries score ``0.0`` quality when
    escalation was needed (never silently dropped).
    REQ-007: *heldout* marks a private-seed run; *heldout_manifest_sha*
    is recorded in the scorecard header (seeds never are).
    REQ-024 A1: *leak_audit* is the counts-only result of
    ``scripts/heldout_leak_audit.py --write`` (never secrets); it is
    recorded verbatim and rendered in the scorecard header.
    """
    bundles = escalation_bundles or {}
    if isinstance(bench_result, dict):
        trials = bench_result.get("trials", [])
        cfg_seed = bench_result.get("config", {}).get("seed", seed)
    else:
        trials = getattr(bench_result, "trials", []) or []
        cfg = getattr(bench_result, "config", None)
        cfg_seed = getattr(cfg, "seed", seed) if cfg is not None else seed
    scored_trials: list[dict[str, Any]] = []
    for t in trials:
        if isinstance(t, dict):
            key = f"{t.get('scenario_id')}:{t.get('trial')}"
        else:
            key = f"{getattr(t, 'scenario_id', '')}:{getattr(t, 'trial', 0)}"
        scored_trials.append(score_trial(t, verify_fn, escalation_bundles=bundles.get(key)))
    agg = aggregate_scores(scored_trials)
    matched = sum(1 for t in scored_trials if t.get("matched"))
    # REQ-046: bench prompt provenance — first-seen prompt ref per arm
    # (id/version/content hash of the file prompt it rendered) plus the
    # shared context hash the fair-context test asserted on.
    prompts: dict[str, Any] = {}
    context_hashes: dict[str, Any] = {}
    for t in scored_trials:
        for name, s in (t.get("arms") or {}).items():
            if name not in prompts and isinstance(s, dict) and s.get("prompt") is not None:
                prompts[name] = s.get("prompt")
            if name not in context_hashes and isinstance(s, dict) and s.get("context_hash") is not None:
                context_hashes[name] = s.get("context_hash")
    return {
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "seed": cfg_seed,
        "total_trials": len(scored_trials),
        "matched_trials": matched,
        "arms": agg["arms"],
        "trials": scored_trials,
        "wins": agg["wins"],
        "losses": agg["losses"],
        "heldout": heldout,
        "heldout_manifest_sha": heldout_manifest_sha,
        "leak_audit": leak_audit,
        "prompts": prompts,
        "context_hashes": context_hashes,
    }


# ---------------------------------------------------------------------------
# Scorecard schema validation (used by W8.5 e2e test_07)
# ---------------------------------------------------------------------------


def validate_scorecard_schema(scorecard: dict[str, Any]) -> None:
    """Assert *scorecard* has every required metric (raises AssertionError).

    Checks top-level keys, per-arm required metrics, per-trial arm scores
    and that wins **and** losses keys exist (losses may be empty only when
    every trial verified on every arm — otherwise non-empty).
    """
    if not isinstance(scorecard, dict):
        raise AssertionError("scorecard must be a mapping")
    for key in ("arms", "trials", "wins", "losses", "total_trials", "matched_trials"):
        if key not in scorecard:
            raise AssertionError(f"scorecard missing required key {key!r}")
    arms = scorecard["arms"]
    if not isinstance(arms, dict) or not arms:
        raise AssertionError("scorecard['arms'] must be a non-empty mapping")
    for arm_name, metrics in arms.items():
        for metric in REQUIRED_ARM_METRICS:
            if metric not in metrics:
                raise AssertionError(f"scorecard arms[{arm_name!r}] missing metric {metric!r}")
    trials = scorecard["trials"]
    if not isinstance(trials, list):
        raise AssertionError("scorecard['trials'] must be a list")
    for t in trials:
        for key in ("scenario_id", "trial", "matched", "arms", "winner", "loss_notes"):
            if key not in t:
                raise AssertionError(f"scorecard trial missing key {key!r}")
    if not isinstance(scorecard["wins"], list) or not isinstance(scorecard["losses"], list):
        raise AssertionError("scorecard wins/losses must be lists")
    # REQ-007 optional heldout provenance (present on heldout runs, absent on old cards).
    if "heldout" in scorecard and not isinstance(scorecard["heldout"], bool):
        raise AssertionError("scorecard['heldout'] must be a bool")
    if scorecard.get("heldout_manifest_sha") is not None and not isinstance(scorecard["heldout_manifest_sha"], str):
        raise AssertionError("scorecard['heldout_manifest_sha'] must be a str")
    # REQ-024 A1 optional leak-audit provenance (counts only, never secrets).
    if scorecard.get("leak_audit") is not None and not isinstance(scorecard["leak_audit"], dict):
        raise AssertionError("scorecard['leak_audit'] must be a dict")
    # REQ-046 optional bench-prompt provenance (id/version/hash per arm).
    if scorecard.get("prompts") is not None:
        if not isinstance(scorecard["prompts"], dict):
            raise AssertionError("scorecard['prompts'] must be a dict")
        for arm_name, ref in scorecard["prompts"].items():
            if not isinstance(ref, dict) or not all(
                k in ref and isinstance(ref[k], str) for k in ("id", "version", "hash")
            ):
                raise AssertionError(f"scorecard prompts[{arm_name!r}] must carry id/version/hash strings")
    if scorecard.get("context_hashes") is not None and not isinstance(scorecard["context_hashes"], dict):
        raise AssertionError("scorecard['context_hashes'] must be a dict")


def healthz() -> dict[str, str]:
    """Health check for the scoring module."""
    return {"status": "ok", "service": "bench-scoring"}
