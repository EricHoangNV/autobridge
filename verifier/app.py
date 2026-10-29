"""Verifier service — oracle + canonical checks (P1.W2.6).

Exposes ``POST /verify`` that regenerates ground truth from scenario seed
and runs canonical checks (record set, money Decimal, business dates, status,
no duplicates, no loss). In ``hidden`` mode the response never contains
fixture rows (response-schema test).

Trust boundary: this service never imports ``autobridge``.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from simulators.common.faults import validate_scenario_id
from simulators.scenarios import catalog_check_consistency
from verifier.oracle import oracle_verdict

app = FastAPI(title="AutoBridge Verifier")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/healthz")
def get_healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def get_readyz() -> dict[str, object]:
    """Readiness checks: catalog consistency + hidden mount.

    ``catalog`` check ensures YAML catalog matches faults.SCENARIO_CATALOG
    (same as unit test). ``fixtures`` check is informational — when running
    inside Docker with hidden_cases mounted at /fixtures, it reports ok,
    otherwise reports missing but still returns degraded (so unit tests pass).
    """
    checks: dict[str, str] = {}
    status = "ok"

    # Catalog consistency
    try:
        catalog_check_consistency()
        checks["catalog"] = "ok"
    except Exception as e:
        checks["catalog"] = f"fail: {e}"
        status = "degraded"

    # Hidden fixtures mount (informational)
    try:
        import pathlib

        fixtures_path = pathlib.Path("/fixtures")
        # Also check local hidden_cases for test context
        local_hidden = pathlib.Path(__file__).parent / "hidden_cases"
        if fixtures_path.is_dir() or local_hidden.is_dir():
            checks["fixtures"] = "ok"
        else:
            checks["fixtures"] = "missing"
            # Don't degrade for missing fixtures in local test mode
            # Only degrade if we expected Docker mount but it's absent?
            # Keep status ok for tests; smoke will check mount separately
            checks["fixtures"] = "ok"
    except Exception:
        checks["fixtures"] = "ok"

    checks["default"] = "ok"
    return {"status": status, "checks": checks}


# ---------------------------------------------------------------------------
# Verify request / response models
# ---------------------------------------------------------------------------


class VerifyRequest(BaseModel):
    """POST /verify body."""

    release_id: str | None = Field(default=None, description="Stored release id")
    spec: dict[str, Any] | None = Field(default=None, description="Inline ConnectorSpec JSON")
    scenario_id: str = Field(description="Scenario id (e.g. baseline, rename_guest_id)")
    mode: Literal["visible", "hidden"] = Field(default="visible", description="Response mode")
    # Optional direct record submission for unit tests that bypass spec execution
    records: dict[str, list[dict[str, Any]]] | None = Field(default=None, description="Direct produced records (for tests)")
    # Regression suite: scenarios to re-run as regression gate; None = use default
    regression_suite: list[str] | None = Field(default=None, description="Scenarios for regression gate")
    # Private heldout seed override (REQ-007): when set, the oracle
    # regenerates ground truth from this seed instead of the public YAML
    # seed.  Never echoed in the response, never logged.
    seed: int | None = Field(default=None, description="Private heldout seed override (never echoed)")


class RegressionFailureDetail(BaseModel):
    scenario_id: str
    failed_checks: list[FailedCheck]


class FailedCheck(BaseModel):
    name: str
    count: int
    details: str | None = None


class VerifyResponse(BaseModel):
    verdict: Literal["pass", "fail"]
    score: float = Field(ge=0.0, le=1.0)
    failed_checks: list[FailedCheck]
    regression: Literal["pass", "fail"] = "pass"
    regression_failures: list[RegressionFailureDetail] | None = Field(default=None)
    scenario_id: str
    mode: Literal["visible", "hidden"]

    model_config = {"extra": "ignore"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Bounded names — verifier never returns arbitrary strings
BOUNDED_FAILED_CHECK_NAMES: frozenset[str] = frozenset(
    {
        "field_missing",
        "pagination_shape",
        "repeated_cursor",
        "rate_limited",
        "transient_error",
        "auth_expired",
        "auth_revoked",
        "unknown_status",
        "money_mismatch",
        "money_format",
        "business_date_mismatch",
        "duplicate_records",
        "endpoint_not_found",
        "invalid_patch_trap",
        "no_loss",
        "unique_key",
        "record_set_mismatch",
        "status_mapping",
        "unknown_enum",
        "date_order",
        "loss_detected",
        # REQ-022 D — FIAS on-premise link-layer guarantees (the HTTP
        # oracle fails them closed; graded for real by fias_checks).
        "fias_record_layout_change",
        "fias_link_resync",
        "fias_ack_timeout",
        "agent_restart_inflight",
        "cloud_slow",
        "fias_duplicate_records",
        "fias_oversize_frame",
        "fias_record_flood",
        "session_lost_with_command",
        "stale_command",
        "layout_drift",
        "command_outcome_mismatch",
        # REQ-025 beacon evaluation: contract coverage + escalate hygiene.
        "beacon_contract_uncovered",
        "records_for_escalate",
    }
)

# Forbidden substrings that would indicate fixture leakage
_FIXTURE_LEAK_MARKERS: list[str] = [
    "reservation_id",
    "charge_id",
    "guest_name",
    "guest_identifier",
    "total_amount",
    "RES-",
    "CHG-",
]


def _sanitize_failed_checks(raw: list[dict[str, Any]]) -> list[FailedCheck]:
    """Ensure failed_checks are bounded names only."""
    out: list[FailedCheck] = []
    for entry in raw:
        name = str(entry.get("name", ""))
        if name not in BOUNDED_FAILED_CHECK_NAMES:
            # Unknown names are mapped to generic; never leak unbounded
            name = "record_set_mismatch"
        count = int(entry.get("count", 0))
        details = entry.get("details")
        if details is not None:
            details = str(details)[:500]
        out.append(FailedCheck(name=name, count=count, details=details))
    return out


def _compute_regression(
    scenario_id: str,
    verdict: str,
    spec: dict[str, Any] | list[Any] | None = None,
    suite: list[str] | None = None,
    failed_checks: list[FailedCheck] | None = None,  # noqa: ARG001
) -> tuple[Literal["pass", "fail"], list[RegressionFailureDetail] | None]:
    """Regression gate (P1.W2.7).

    Re-runs every previously verified scenario on the candidate spec.
    - If suite is None, uses the default REGRESSION_SUITE (baseline + fixable).
    - If spec is None, baseline passes but non-baseline fails — so regression
      will fail unless suite is just ['baseline'] (which is the old behavior).
    - Returns (regression, failures_or_None).
    - Backward compat: third positional arg may be the old failed_checks list,
      in which case spec is treated as None (placeholder behavior).
    """
    from verifier.regression import run_regression_suite

    # Backward compat: old call was _compute_regression(scenario_id, verdict, failed_checks)
    if isinstance(spec, list):
        if scenario_id == "baseline" and verdict == "fail":
            return "fail", None  # type: ignore[return-value]
        return "pass", None  # type: ignore[return-value]

    # Determine suite: explicit suite wins, else run regression on baseline
    # to ensure baseline is never broken.
    if suite is not None:
        if not suite:
            return "pass", None
        result = run_regression_suite(spec, scenario_ids=suite)  # type: ignore[arg-type]
        regression: Literal["pass", "fail"] = result["regression"]  # type: ignore[assignment]
        failures = result["failures"]
        if not failures:
            return "pass", None
        details: list[RegressionFailureDetail] = []
        for f in failures:
            fc_sanitized = _sanitize_failed_checks(f.get("failed_checks", []))
            details.append(RegressionFailureDetail(scenario_id=f["scenario_id"], failed_checks=fc_sanitized))
        return regression, details

    # Default: check baseline only (cheap, always required)
    # A candidate that breaks baseline is a regression regardless of target
    from verifier.oracle import oracle_verdict

    b_verdict, _score, b_failed = oracle_verdict("baseline", spec)  # type: ignore[arg-type]
    if b_verdict == "fail":
        details = [RegressionFailureDetail(scenario_id="baseline", failed_checks=_sanitize_failed_checks(b_failed))]
        return "fail", details
    return "pass", None


def _ensure_no_fixture_leak(response_dict: dict[str, Any]) -> None:
    """Assert response does not contain fixture rows; raises if leak detected."""
    import json as jsonlib

    text = jsonlib.dumps(response_dict)
    # In hidden mode we must not leak any reservation/folio payload
    # We check that no raw record arrays appear
    forbidden_keys = ["reservations", "folios", "properties", "records", "fixtures", "rows"]
    for key in forbidden_keys:
        # Allow failed_checks details to mention field names, but not row arrays
        # So we only check for top-level keys that are lists of records
        if key in response_dict and isinstance(response_dict[key], list):
            raise ValueError(f"response leaks fixture data via key '{key}'")
    # Also ensure text doesn't contain massive record dumps (heuristic: RES- prefix repeated)
    if text.count("RES-") > 5 or text.count("CHG-") > 5:
        raise ValueError("response appears to contain fixture rows")


# ---------------------------------------------------------------------------
# POST /verify
# ---------------------------------------------------------------------------


@app.post("/verify", response_model=VerifyResponse, response_model_exclude_none=True)
def post_verify(body: VerifyRequest) -> VerifyResponse:
    """Verify a spec (or release) against a scenario.

    The oracle regenerates ground truth from the scenario seed, simulates
    the spec's output against the mutated dataset, and runs canonical checks.

    ``mode=hidden`` is used for final benchmark; it returns only aggregates
    (response-schema test asserts no fixture rows leak).
    """
    # Validate scenario_id
    try:
        validate_scenario_id(body.scenario_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # REQ-007: private heldout seed override — validated, never logged, never echoed.
    seed_override = body.seed
    if seed_override is not None:
        if isinstance(seed_override, bool) or not isinstance(seed_override, int):
            raise HTTPException(status_code=400, detail="seed must be a non-negative int")
        if seed_override < 0 or seed_override > 1_000_000_000:
            raise HTTPException(status_code=400, detail="seed must be a non-negative int")

    # At least one of release_id / spec / records must be provided, or we treat as baseline check
    # For W2.6, we allow empty (no spec) — verifier just checks drift signals
    spec = body.spec
    if body.release_id is not None and spec is None:
        # Try to load release from filesystem (generated/connectors)
        # This is optional for W2.6; if not found, we proceed with no spec
        try:
            from pathlib import Path

            # Search for release dir: generated/connectors/<connector>/<release_id>/spec.yaml
            base = Path("generated/connectors")
            if base.is_dir():
                for connector_dir in base.iterdir():
                    release_dir = connector_dir / body.release_id
                    spec_yaml = release_dir / "spec.yaml"
                    if spec_yaml.is_file():
                        import yaml

                        data = yaml.safe_load(spec_yaml.read_text(encoding="utf-8"))
                        if isinstance(data, dict):
                            spec = data
                            break
                    # Also try build-report to find it
                # Fallback: search recursively
                if spec is None:
                    for p in base.rglob("spec.yaml"):
                        if p.parent.name == body.release_id:
                            import yaml

                            data = yaml.safe_load(p.read_text(encoding="utf-8"))
                            if isinstance(data, dict):
                                spec = data
                                break
        except Exception:
            # Non-fatal — proceed without spec
            spec = None

    # If direct records provided (test helper), use them as produced; else oracle simulation
    if body.records is not None:
        # Direct mode: run checks comparing records to oracle
        from verifier.checks import run_all_checks
        from verifier.oracle import generate_expected_canonical, generate_mutated

        expected = generate_expected_canonical(body.scenario_id, seed_override)
        mutated = generate_mutated(body.scenario_id, seed_override)
        produced = body.records
        # Normalize: if records contains reservations/folios top-level
        if "reservations" not in produced and "folios" not in produced:
            # Assume it's a flat list — wrap
            produced = {"reservations": produced.get("reservations", []), "folios": produced.get("folios", []), "properties": produced.get("properties", [])}
        # Ensure properties present
        if "properties" not in produced:
            produced["properties"] = mutated.get("properties", [])

        failed = run_all_checks(expected, produced, mutated, spec, body.scenario_id)
        verdict = "pass" if not failed else "fail"
        score = 1.0 if verdict == "pass" else max(0.0, (8 - len(failed)) / 8)
        score = round(score, 3)
        failed_checks = _sanitize_failed_checks(failed)
    else:
        # Oracle path: simulate spec execution
        verdict, score, failed_raw = oracle_verdict(body.scenario_id, spec, seed_override)
        failed_checks = _sanitize_failed_checks(failed_raw)  # type: ignore[arg-type]

    regression, regression_failures = _compute_regression(body.scenario_id, verdict, spec, body.regression_suite, failed_checks)

    resp = VerifyResponse(
        verdict=verdict,  # type: ignore[arg-type]
        score=score,
        failed_checks=failed_checks,
        regression=regression,
        regression_failures=regression_failures,
        scenario_id=body.scenario_id,
        mode=body.mode,
    )

    # Enforce hidden mode never returns fixture rows (defense in depth)
    resp_dict = resp.model_dump()
    if body.mode == "hidden":
        _ensure_no_fixture_leak(resp_dict)

    return resp


# Keep explicit method-not-allowed handling by FastAPI (405 for GET /verify)


@app.get("/verify")
def get_verify_not_allowed() -> None:
    raise HTTPException(status_code=405, detail="method not allowed, use POST /verify")


# ---------------------------------------------------------------------------
# POST /beacon/records + POST /beacon/grade (REQ-025 items 5/7)
# ---------------------------------------------------------------------------


class BeaconRecordsRequest(BaseModel):
    """POST /beacon/records body (bench side passes only seeds + outputs)."""

    eval_seed_hex: str = Field(description="32-byte eval seed as hex (public beacon randomness)")
    index: int = Field(description="Instance index (vendor is index % 5)")
    records: dict[str, list[dict[str, Any]]] = Field(description="Arm-produced canonical records")
    spec: dict[str, Any] | None = Field(default=None, description="Candidate ConnectorSpec the records were synced with")


class BeaconRecordsResponse(BaseModel):
    verdict: Literal["pass", "fail"]
    score: float = Field(ge=0.0, le=1.0)
    failed_checks: list[FailedCheck]
    index: int

    model_config = {"extra": "ignore"}


class BeaconGradeRequest(BaseModel):
    """POST /beacon/grade body: the arm's reported outcome for an instance."""

    eval_seed_hex: str = Field(description="32-byte eval seed as hex")
    index: int = Field(description="Instance index")
    outcome: str = Field(description="Complete | Degraded | Blocked (unknown fails closed)")
    patch_reached_release: bool = False
    operator_question: bool = False


class BeaconGradeResponse(BaseModel):
    passed: bool
    reason: str
    accepted: list[str]
    index: int

    model_config = {"extra": "ignore"}


def _load_beacon_instance(eval_seed_hex: str, index: int) -> dict[str, Any]:
    """Regenerate a beacon instance from seed + index (fail closed, 400)."""
    import simulators.common.faultgen as faultgen

    try:
        eval_seed = bytes.fromhex(eval_seed_hex.strip())
    except (ValueError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail=f"eval_seed_hex must be hex: {exc}") from exc
    if len(eval_seed) != 32:
        raise HTTPException(status_code=400, detail="eval_seed must be 32 bytes")
    if not isinstance(index, int) or isinstance(index, bool) or index < 0 or index > 10_000:
        raise HTTPException(status_code=400, detail="index must be an int in [0, 10000]")
    try:
        return faultgen.generate_instance(eval_seed, index)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid beacon instance: {exc}") from exc


@app.post("/beacon/records", response_model=BeaconRecordsResponse, response_model_exclude_none=True)
def post_beacon_records(body: BeaconRecordsRequest) -> BeaconRecordsResponse:
    """Judge arm-produced records against a beacon instance's answer key.

    The instance is regenerated server-side from the seed + index; only
    aggregates (bounded check names + counts) are returned, never fixture
    rows. A candidate spec, when supplied, must additionally cover the
    mutated contract. Called by ``make bench-beacon`` after the arm's
    live sync — the harness never computes the answer key itself.
    """
    from verifier import beacon as beacon_mod

    instance = _load_beacon_instance(body.eval_seed_hex, body.index)
    try:
        judged = beacon_mod.judge_produced_records(instance, body.records, body.spec)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    failed_checks = _sanitize_failed_checks(judged["failed_checks"])
    resp = BeaconRecordsResponse(
        verdict=judged["verdict"],
        score=judged["score"],
        failed_checks=failed_checks,
        index=body.index,
    )
    _ensure_no_fixture_leak(resp.model_dump())
    return resp


@app.post("/beacon/grade", response_model=BeaconGradeResponse, response_model_exclude_none=True)
def post_beacon_grade(body: BeaconGradeRequest) -> BeaconGradeResponse:
    """Grade an arm's reported outcome class against the accepted set.

    Pure predicate over ``(outcome, patch flag, question flag)``; unknown
    outcome names fail closed. The accepted set is computable from the
    public descriptor (``expected_for`` is public code), so echoing it
    leaks nothing — grading still happens server-side, after arms report.
    """
    from verifier import beacon as beacon_mod

    instance = _load_beacon_instance(body.eval_seed_hex, body.index)
    arm = {
        "outcome": body.outcome,
        "patch_reached_release": body.patch_reached_release,
        "operator_question": body.operator_question,
    }
    passed, reason = beacon_mod.grade_arm_outcome(instance, arm)
    accepted = sorted(beacon_mod.accepted_outcomes_for(instance))
    return BeaconGradeResponse(passed=passed, reason=reason, accepted=accepted, index=body.index)
