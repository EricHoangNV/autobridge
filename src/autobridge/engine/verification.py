"""Regression gate — every candidate release verified before it ships (P1.W6.4).

The verifier is a separate trust boundary (plan §2.7). This module is the
engine's HTTP client for it: it never imports ``verifier`` or ``simulators``,
only calls ``POST /verify`` via httpx. Secrets never appear (spec carries
``credential_ref`` only). The gate blocks a release that breaks any previously
verified scenario, and the plausible-but-wrong patch is rejected because its
verdict is ``fail`` with ``invalid_patch_trap``.

Usage:
    from autobridge.engine.verification import verify_spec, check_regression_gate

    result = verify_spec(spec, "rename_guest_id", verifier_url, regression_suite=["baseline"])
    allowed, result = check_regression_gate(spec, "rename_guest_id", verifier_url, verified_scenarios=[...])
    if not allowed:
        raise GateBlockedError(result)
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any


class GateBlockedError(ValueError):
    """Candidate release blocked by verification or regression gate."""

    def __init__(
        self,
        message: str,
        result: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.result = result
        self.reason = reason


@dataclass(frozen=True, slots=True)
class GateOutcome:
    """Parsed verifier response with gate decision."""

    verdict: str
    regression: str
    score: float
    failed_checks: list[dict[str, Any]]
    regression_failures: list[dict[str, Any]] | None
    scenario_id: str
    raw: dict[str, Any]
    allowed: bool
    block_reason: str | None = None
    # Echo of the verifier's response mode ("visible"|"hidden"). verify_spec
    # requires it to match the requested mode (AR-1-5).
    mode: str = ""
    # sha256 canonical-JSON signature of the exact spec that was verified
    # (matches ConnectorRelease.signature for the same spec dict), recorded
    # alongside the verification outcome so evidence binds to what ran (AR-1-5).
    spec_signature: str | None = None


def _parse_verifier_response(data: dict[str, Any]) -> GateOutcome:
    """Parse raw verifier JSON into GateOutcome with allowed logic.

    Allowed means: verdict == "pass" AND regression == "pass".
    Block reasons:
      - verdict fail -> failed_checks named (including invalid_patch_trap)
      - regression fail -> regression_failures list
    """
    verdict = str(data.get("verdict", "fail"))
    regression = str(data.get("regression", "pass"))
    score = float(data.get("score", 0.0))
    failed_checks = list(data.get("failed_checks", []))
    regression_failures = data.get("regression_failures")
    if regression_failures is not None:
        regression_failures = list(regression_failures)
    scenario_id = str(data.get("scenario_id", ""))
    mode = str(data.get("mode", ""))

    allowed = verdict == "pass" and regression == "pass"
    block_reason: str | None = None
    if not allowed:
        if verdict != "pass":
            names = [str(f.get("name", "")) for f in failed_checks]
            # Surface invalid_patch_trap explicitly as plausible-but-wrong rejection
            if "invalid_patch_trap" in names:
                block_reason = f"verdict fail: invalid_patch_trap (plausible-but-wrong patch rejected) {names}"
            else:
                block_reason = f"verdict fail: {names} ({failed_checks[:2]})"
        elif regression != "pass":
            # regression failures list scenarios that broke
            broken = []
            if regression_failures:
                for rf in regression_failures:
                    sid = rf.get("scenario_id", "") if isinstance(rf, dict) else ""
                    broken.append(str(sid))
            block_reason = f"regression fail: breaks {broken or regression_failures}"

    return GateOutcome(
        verdict=verdict,
        regression=regression,
        score=score,
        failed_checks=failed_checks,
        regression_failures=regression_failures,
        scenario_id=scenario_id,
        raw=data,
        allowed=allowed,
        block_reason=block_reason,
        mode=mode,
    )


def verify_spec(
    spec: dict[str, Any],
    scenario_id: str,
    verifier_url: str,
    *,
    mode: str = "visible",
    regression_suite: list[str] | None = None,
    timeout: float = 10.0,
) -> GateOutcome:
    """Call verifier POST /verify for spec+scenario and return parsed outcome.

    Args:
        spec: Candidate ConnectorSpec dict (validated).
        scenario_id: Scenario to verify against.
        verifier_url: Base URL of verifier (e.g. http://verifier:8000 or http://localhost:8104).
        mode: visible|hidden (visible for gate, hidden for benchmark).
        regression_suite: Explicit list of scenario_ids to re-run as regression gate.
            If None, verifier defaults to baseline-only check (cheap, always required).
            For full gate, pass previously verified scenario ids (e.g. ["baseline", "rename_guest_id"]).
        timeout: HTTP timeout seconds.

    Raises:
        GateBlockedError: if verifier unreachable or returns non-200, or if
            the response scenario_id/mode does not match the request (AR-1-5).
        ValueError: if verifier_url empty.

    Note: This function never imports verifier/simulators (trust boundary).
    """
    if not verifier_url or not verifier_url.strip():
        raise ValueError("verifier_url must be non-empty")

    import httpx  # noqa: PLC0415 - local import to keep module importable without httpx at typecheck

    base = verifier_url.rstrip("/")
    url = base if base.endswith("/verify") else base + "/verify"

    payload: dict[str, Any] = {"spec": spec, "scenario_id": scenario_id, "mode": mode}
    if regression_suite is not None:
        payload["regression_suite"] = list(regression_suite)

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload)
    except Exception as exc:
        raise GateBlockedError(f"verifier unreachable at {url}: {exc}") from exc

    if resp.status_code != 200:
        # Try to extract detail
        detail = ""
        try:
            detail = str(resp.json().get("detail", resp.text)[:500])
        except Exception:
            detail = resp.text[:500]
        raise GateBlockedError(
            f"verifier returned HTTP {resp.status_code} for {scenario_id}: {detail}",
            result=None,
            reason=f"http_{resp.status_code}",
        )

    try:
        data = resp.json()
    except Exception as exc:
        raise GateBlockedError(f"verifier returned non-JSON for {scenario_id}: {exc}") from exc

    outcome = _parse_verifier_response(data)
    # AR-1-5: bind the verdict to what was requested. A pass verdict for a
    # different scenario (or mode) must never count as allowed — e.g. when
    # the verifier endpoint is redirected, the response could be for
    # anything. Fail closed.
    if outcome.scenario_id != scenario_id:
        raise GateBlockedError(
            f"verifier response scenario mismatch: requested {scenario_id!r}, got {outcome.scenario_id!r}",
            result=data,
            reason="scenario_mismatch",
        )
    if outcome.mode != mode:
        raise GateBlockedError(
            f"verifier response mode mismatch: requested {mode!r}, got {outcome.mode!r}",
            result=data,
            reason="mode_mismatch",
        )
    # Record the signature of exactly the spec that was verified, so the
    # outcome binds to a ConnectorRelease.signature for the same spec dict.
    from autobridge.control.models import spec_signature  # noqa: PLC0415 - local to keep the client import-light

    return replace(outcome, spec_signature=spec_signature(spec))


def check_regression_gate(
    spec: dict[str, Any],
    scenario_id: str,
    verifier_url: str | None,
    *,
    verified_scenarios: list[str] | None = None,
    mode: str = "visible",
    timeout: float = 10.0,
) -> tuple[bool, GateOutcome | None]:
    """Check candidate spec against verifier regression gate.

    If verifier_url is None or empty, gate is skipped (returns allowed=True, None).
    This preserves backward compat for unit tests without Docker.

    Args:
        spec: Candidate spec.
        scenario_id: Target scenario being fixed.
        verifier_url: Verifier base URL or None to skip.
        verified_scenarios: Previously verified scenario ids to re-run.
            If None, verifier uses its default (baseline only). For full gate,
            pass the connector's verified set.
        mode: visible|hidden.
        timeout: HTTP seconds.

    Returns:
        (allowed, outcome_or_None) — allowed True means gate passed (both verdict
        and regression pass). outcome is None when gate skipped.

    Raises:
        GateBlockedError: only if verifier_url provided but verifier error occurs?
        Actually we treat verifier errors as blocked? Current implementation raises.
        Caller may catch GateBlockedError to surface as blocked.

    Example: a release that breaks baseline is blocked even when target is
    rename_guest_id — because regression_suite includes baseline.
    The plausible-but-wrong patch (net_amount->total_amount) is rejected because
    its verdict fail includes invalid_patch_trap.
    """
    if not verifier_url:
        return True, None

    # Gate calls verify with regression_suite = verified_scenarios
    # If verified_scenarios None, let verifier decide default (baseline)
    outcome = verify_spec(
        spec,
        scenario_id,
        verifier_url,
        mode=mode,
        regression_suite=verified_scenarios,
        timeout=timeout,
    )
    return outcome.allowed, outcome


def ensure_gate_passes(
    spec: dict[str, Any],
    scenario_id: str,
    verifier_url: str | None,
    *,
    verified_scenarios: list[str] | None = None,
    mode: str = "visible",
    timeout: float = 10.0,
) -> GateOutcome | None:
    """Ensure gate passes or raise GateBlockedError.

    Returns GateOutcome when gate was checked, None when skipped (no verifier_url).
    Raises GateBlockedError with block_reason when verdict or regression fail.
    """
    allowed, outcome = check_regression_gate(
        spec,
        scenario_id,
        verifier_url,
        verified_scenarios=verified_scenarios,
        mode=mode,
        timeout=timeout,
    )
    if outcome is None:
        return None
    if not allowed:
        msg = outcome.block_reason or f"gate blocked for {scenario_id}: verdict={outcome.verdict} regression={outcome.regression}"
        # Include failed checks for diagnostics
        raise GateBlockedError(msg, result=outcome.raw, reason=outcome.block_reason)
    return outcome
