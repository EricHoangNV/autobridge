"""Regression runner — re-runs previously verified scenarios on a candidate release (P1.W2.7).

Plan §2.7: "Regression suite: every previously verified scenario for the connector
re-runs on each candidate release." A release that breaks any previously verified
scenario is blocked (regression gate).

The runner is pure: it calls oracle_verdict for each scenario in the suite and
reports pass/fail. It never imports autobridge (trust boundary).
"""

from __future__ import annotations

from typing import Any

from simulators.common.faults import SCENARIO_CATALOG, transport_for_scenario
from verifier.oracle import oracle_verdict

# ---------------------------------------------------------------------------
# Suite definition
# ---------------------------------------------------------------------------

# "Previously verified" = baseline (always verified) + every fixable HTTP
# scenario. The FIAS on-premise family (transport fias_tcp, REQ-022 D) is
# excluded: the HTTP oracle cannot verify link guarantees (it fails them
# closed by design), and grading them here would be vacuous passes. Their
# regression surface is the FIAS e2e file.
REGRESSION_SUITE: list[str] = [
    entry["id"]
    for entry in SCENARIO_CATALOG
    if entry["fixable"] == "fixable" and transport_for_scenario(entry["id"]) == "http"
]
# REGRESSION_SUITE includes baseline plus all fixable dev+heldout scenarios
# (baseline is fixable in catalog as well).

# Alias for explicitness
BASELINE_SCENARIO = "baseline"


def get_regression_suite(exclude: str | None = None) -> list[str]:
    """Return the default regression suite (baseline + fixable scenarios).

    If exclude is given, that scenario_id is omitted (useful when the caller
    is already evaluating exclude as the target scenario).
    """
    if exclude is None:
        return list(REGRESSION_SUITE)
    return [s for s in REGRESSION_SUITE if s != exclude]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class RegressionFailure(Exception):
    """Single scenario failure inside a regression run."""


def _run_one(scenario_id: str, spec: dict[str, Any] | None) -> tuple[str, list[dict[str, Any]]]:
    """Run oracle_verdict for one scenario, return (verdict, failed_checks)."""
    verdict, _score, failed = oracle_verdict(scenario_id, spec)
    return verdict, failed


def run_regression_suite(
    spec: dict[str, Any] | None,
    scenario_ids: list[str] | None = None,
    exclude: str | None = None,
) -> dict[str, Any]:
    """Run every scenario in scenario_ids against spec, return regression result.

    Returns:
        {
            "regression": "pass" | "fail",
            "failures": [{"scenario_id": str, "failed_checks": [...]}, ...],
            "checked": int,  # number of scenarios evaluated
        }

    - No mutation of inputs.
    - Empty suite (or no spec) is pass.
    - If spec is None, we treat as baseline spec: baseline passes, others fail
      (so regression would fail — this is intentional for early stages).
    - Caller may pass explicit scenario_ids or rely on default suite.
    """
    if scenario_ids is None:
        scenario_ids = get_regression_suite(exclude=exclude)
    elif exclude is not None:
        scenario_ids = [s for s in scenario_ids if s != exclude]

    failures: list[dict[str, Any]] = []
    for sid in scenario_ids:
        try:
            verdict, failed = _run_one(sid, spec)
        except Exception as exc:  # unknown scenario etc.
            failures.append({"scenario_id": sid, "failed_checks": [{"name": "record_set_mismatch", "count": 1, "details": str(exc)[:200]}]})
            continue
        if verdict == "fail":
            failures.append({"scenario_id": sid, "failed_checks": failed})

    regression = "fail" if failures else "pass"
    return {
        "regression": regression,
        "failures": failures,
        "checked": len(scenario_ids),
    }


def regression_gate_allows(spec: dict[str, Any] | None, target_scenario: str) -> tuple[bool, dict[str, Any]]:
    """Convenience: run default regression suite (excluding target) and report.

    Returns (allowed, result_dict).
    """
    # The default suite should always include baseline. If the candidate
    # breaks baseline, it's blocked regardless of target.
    suite = get_regression_suite(exclude=None)
    # Always check baseline even if target is baseline
    result = run_regression_suite(spec, suite, exclude=None)
    # If target is not baseline, we already computed regression over all includes baseline.
    # If target is baseline, same suite includes it — no extra handling needed.
    # However if spec is intentionally fixing target, target will appear in failures
    # only if fix is incomplete — we should not penalize that if target verdict is pass.
    # So we re-evaluate target separately and remove it from failures if it passes.
    if target_scenario in suite:
        verdict, _failed = _run_one(target_scenario, spec)
        if verdict == "pass":
            # Remove target from failures if present (fix succeeded)
            result["failures"] = [f for f in result["failures"] if f["scenario_id"] != target_scenario]
            result["regression"] = "fail" if result["failures"] else "pass"

    allowed = result["regression"] == "pass"
    return allowed, result
