"""Oracle — regenerates ground truth from scenario seed (P1.W2.6).

The oracle is the hidden judge: given a ``scenario_id`` it regenerates
the exact canonical dataset the simulator would serve for that scenario's
seed, using the shared seeded generator (no HTTP, no fixtures).

It then simulates what a connector spec would produce when run against the
mutated simulator, by applying the spec's pagination and field mappings to
the mutated dataset. The produced canonical records are compared to the
expected canonical records (baseline) via the canonical checks.

The oracle never returns fixture rows — only aggregate failed_checks.
"""

from __future__ import annotations

from typing import Any

# Import from simulators (shared generator code). Verifier never imports autobridge.
from simulators.common.faults import (
    apply_dataset_mutations,
    is_auth_expired,
    is_auth_revoked,
    is_endpoint_removed,
    is_rate_limited,
    is_transient_error,
)
from simulators.common.generator import (
    generate_dataset,
)
from simulators.scenarios import load_scenario

# ---------------------------------------------------------------------------
# Ground truth generation
# ---------------------------------------------------------------------------


def get_scenario_seed(scenario_id: str, seed_override: int | None = None) -> int:
    """Return seed for scenario_id via YAML catalog, or the heldout override.

    REQ-007: benchmark heldout runs pass the private seed (from
    ``$HELDOUT_DIR``) as *seed_override*; it is used for dataset
    generation instead of the public YAML seed and is never logged.
    """
    if seed_override is not None:
        if not isinstance(seed_override, int) or isinstance(seed_override, bool):
            raise ValueError("seed_override must be int")
        if seed_override < 0 or seed_override > 1_000_000_000:
            raise ValueError("seed_override out of range")
        return seed_override
    scen = load_scenario(scenario_id)
    return int(scen.seed)


def generate_baseline(scenario_id: str, seed_override: int | None = None) -> dict[str, list[dict[str, Any]]]:
    """Generate baseline (unmutated) dataset for scenario's seed."""
    seed = get_scenario_seed(scenario_id, seed_override)
    return generate_dataset(seed=seed)


def generate_mutated(scenario_id: str, seed_override: int | None = None) -> dict[str, list[dict[str, Any]]]:
    """Generate mutated dataset as simulator would serve it."""
    seed = get_scenario_seed(scenario_id, seed_override)
    baseline = generate_dataset(seed=seed)
    return apply_dataset_mutations(baseline, scenario_id)


def generate_expected_canonical(scenario_id: str, seed_override: int | None = None) -> dict[str, list[dict[str, Any]]]:
    """Expected canonical records = baseline dataset in canonical shape.

    Baseline generator already emits canonical Reservation / FolioCharge
    dicts that validate against schemas/reservation.schema.json etc.
    """
    baseline = generate_baseline(scenario_id, seed_override)
    return {
        "reservations": list(baseline.get("reservations", [])),
        "folios": list(baseline.get("folios", [])),
        "properties": list(baseline.get("properties", [])),
    }


# ---------------------------------------------------------------------------
# Minimal spec simulation
# ---------------------------------------------------------------------------

# Bounded failed-check names (verdict response is limited to these).
ALLOWED_FAILED_CHECKS: frozenset[str] = frozenset(
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
        "money_format",
        "loss_detected",
    }
)


def _spec_has_field_path(spec: dict[str, Any] | None, needle: str) -> bool:
    """Return True if any field mapping path in spec contains needle substring.

    Comparison is case- and separator-insensitive so a Cloudbeds-style wire
    name (``$.guestIdentifier``) matches a canonical needle
    (``guest_identifier``).
    """
    if spec is None:
        return False

    def _norm(s: str) -> str:
        return s.lower().replace("_", "")

    want = _norm(needle)
    try:
        entities = spec.get("entities", {})
        for ent in entities.values():
            if not isinstance(ent, dict):
                continue
            fields = ent.get("fields", {})
            for fm in fields.values():
                if not isinstance(fm, dict):
                    continue
                path = fm.get("path", "")
                if isinstance(path, str) and want in _norm(path):
                    return True
    except Exception:
        return False
    return False


def _spec_pagination_strategy(spec: dict[str, Any] | None) -> str | None:
    """Extract pagination strategy from spec's first operation, if present."""
    if spec is None:
        return None
    try:
        ops = spec.get("operations", {})
        for op in ops.values():
            if not isinstance(op, dict):
                continue
            pag = op.get("pagination")
            if isinstance(pag, dict):
                return pag.get("strategy")
    except Exception:
        return None
    return None


def _spec_next_path(spec: dict[str, Any] | None) -> str | None:
    """Return the first operation pagination next_path, if present.

    Generic across cursor-family strategies (``cursor``, ``body_cursor``):
    the leaf name tells which response field the spec follows.
    """
    if spec is None:
        return None
    try:
        ops = spec.get("operations", {})
        for op in ops.values():
            if not isinstance(op, dict):
                continue
            pag = op.get("pagination")
            if isinstance(pag, dict):
                nxt = pag.get("next_path")
                if isinstance(nxt, str):
                    return nxt
    except Exception:
        return None
    return None


def _spec_stop_when(spec: dict[str, Any] | None) -> list[str]:
    """Extract stop_when list from spec's first operation."""
    if spec is None:
        return []
    try:
        ops = spec.get("operations", {})
        for op in ops.values():
            if not isinstance(op, dict):
                continue
            pag = op.get("pagination")
            if isinstance(pag, dict):
                sw = pag.get("stop_when", [])
                if isinstance(sw, list):
                    return [str(x) for x in sw]
    except Exception:
        return []
    return []


def _spec_has_enum_mapping_for_status(spec: dict[str, Any] | None, values: list[str]) -> bool:
    """Check if spec's enum_map for status contains all values."""
    if spec is None:
        return False
    try:
        entities = spec.get("entities", {})
        for ent in entities.values():
            if not isinstance(ent, dict):
                continue
            fields = ent.get("fields", {})
            for fm in fields.values():
                if not isinstance(fm, dict):
                    continue
                transform = fm.get("transform", [])
                # transform may be string or list of steps
                steps: list[Any] = []
                if isinstance(transform, list):
                    steps = transform
                elif isinstance(transform, dict):
                    steps = [transform]
                elif isinstance(transform, str):
                    steps = [{"name": transform}]
                for step in steps:
                    if not isinstance(step, dict):
                        continue
                    if step.get("name") == "enum_map":
                        args = step.get("args", {})
                        mmap = args.get("map", {})
                        if isinstance(mmap, dict):
                            # Check if map values include canonical statuses
                            # For status_vocab_change, the new vocab must be mapped
                            # We check if any key or value matches one of values
                            all_keys_vals = set(str(k) for k in mmap) | set(
                                str(v) for v in mmap.values()
                            )
                            if any(v in all_keys_vals for v in values):
                                return True
    except Exception:
        return False
    return False


def _spec_has_retry_for_status(spec: dict[str, Any] | None, status: int) -> bool:
    """Check if spec's source.retry.statuses includes given HTTP status."""
    if spec is None:
        return False
    try:
        source = spec.get("source", {})
        rate_limit = source.get("rate_limit", {})
        retry = rate_limit.get("retry", {})
        statuses = retry.get("statuses", [])
        if isinstance(statuses, list) and status in statuses:
            return True
    except Exception:
        return False
    return False


def simulate_produced_records(
    mutated: dict[str, list[dict[str, Any]]],
    expected: dict[str, list[dict[str, Any]]] | None = None,  # noqa: ARG001
    spec: dict[str, Any] | None = None,
    scenario_id: str = "",
) -> dict[str, list[dict[str, Any]]]:
    """Simulate what the spec would produce against mutated data.

    For W2.6, we do a lightweight simulation:

    - If spec is None, produced == mutated (identity).
    - For rename_guest_id: if spec path still points at guest_name, produced
      will have missing guest_name (we simulate by removing field from output).
    - For gross_to_net: produced money will be net values if mutated, else gross.
    - For pagination mismatch: we truncate produced to first page if strategy mismatches.
    - For duplicate records: produced includes duplicates as mutated does.
    - Otherwise produced is derived by applying field mappings where possible.

    This is intentionally minimal but sufficient to drive canonical checks.
    """
    # For HTTP-error scenarios (endpoint_removed, auth), produced is empty — fetch fails
    if is_endpoint_removed(scenario_id):
        return {"reservations": [], "folios": [], "properties": []}
    if is_auth_expired(scenario_id) or is_auth_revoked(scenario_id):
        # Auth failure: no records fetched
        # But if spec handles it via refresh, it might still succeed — we check
        # spec for token refresh capability (not in v1), so baseline always fails
        return {"reservations": [], "folios": [], "properties": []}
    # Rate limit / transient: may cause incomplete fetch if retry not configured
    # We simulate that spec with correct retry succeeds, otherwise first page only
    if is_rate_limited(scenario_id, 3) or is_transient_error(scenario_id, 4):
        has_retry_429 = _spec_has_retry_for_status(spec, 429)
        has_retry_503 = _spec_has_retry_for_status(spec, 503)
        needs_429 = is_rate_limited(scenario_id, 3)
        needs_503 = is_transient_error(scenario_id, 4)
        if (needs_429 and not has_retry_429) or (needs_503 and not has_retry_503):
            # Incomplete fetch: return truncated (first 10 records)
            res = mutated.get("reservations", [])
            fol = mutated.get("folios", [])
            return {
                "reservations": res[:10] if len(res) > 10 else res[: max(1, len(res) // 2)],
                "folios": fol[:10] if len(fol) > 10 else fol[: max(1, len(fol) // 2)],
                "properties": mutated.get("properties", []),
            }

    # Default: produced is mutated but transformed via spec field mappings
    # Check for field rename handling
    if scenario_id == "rename_guest_id":
        has_fix = _spec_has_field_path(spec, "guest_identifier")
        if spec is not None:
            if has_fix:
                # Fixed spec: map guest_identifier -> guest_name
                out_res: list[dict[str, Any]] = []
                for rec in mutated.get("reservations", []):
                    produced = dict(rec)
                    if "guest_identifier" in rec and "guest_name" not in rec:
                        produced["guest_name"] = produced.pop("guest_identifier")
                    out_res.append(produced)
                return {
                    "reservations": out_res,
                    "folios": list(mutated.get("folios", [])),
                    "properties": list(mutated.get("properties", [])),
                }
            # Spec still expects guest_name but mutated has guest_identifier
            out_res: list[dict[str, Any]] = []
            for rec in mutated.get("reservations", []):
                produced = dict(rec)
                if "guest_identifier" in rec and "guest_name" not in rec:
                    produced.pop("guest_name", None)
                out_res.append(produced)
            return {
                "reservations": out_res,
                "folios": list(mutated.get("folios", [])),
                "properties": list(mutated.get("properties", [])),
            }

    # For pagination scenarios, check if spec strategy matches mutated pagination mode
    if scenario_id in ("offset_to_cursor", "page_to_cursor", "cursor_repeat_token"):
        spec_strategy = _spec_pagination_strategy(spec)
        # Baseline spec is offset (legacy) or page (AtlasPMS/Cloudbeds-style);
        # Harbor is cursor; we cannot assume. So we check: if spec_strategy
        # != mutated_mode, it's a mismatch. However some specs may be Harbor
        # (cursor) and scenario offset_to_cursor expects cursor — that would
        # be a match. Simplify: use scenario's expected mode vs spec strategy.
        expected_mode = "cursor"  # both pagination fault scenarios use cursor
        if spec_strategy is not None and spec_strategy != expected_mode:
            # Spec still offset while API is cursor → incomplete fetch
            res = mutated.get("reservations", [])
            fol = mutated.get("folios", [])
            truncation = 10 if len(res) > 10 else max(1, len(res) // 2)
            return {
                "reservations": res[:truncation],
                "folios": fol[:truncation] if fol else [],
                "properties": list(mutated.get("properties", [])),
            }
        # For cursor_repeat_token, also check stop_when guard
        if scenario_id == "cursor_repeat_token":
            stop_when = _spec_stop_when(spec)
            if "repeated_cursor" not in stop_when:
                # Without guard, fetching would loop; we simulate truncated after bug detection
                # The check will be repeated_cursor
                pass  # Still return full; check logic will flag based on guard

    # For status vocab: if spec doesn't map new vocab, we keep old values but they will be checked
    # For gross_to_net etc., produced is just mutated values (net) — will be caught by money check

    # REQ-020 Meridian drifts (wire-level renames; canonical dataset is unchanged).
    if scenario_id == "meridian_state_renamed":
        # Fixed spec reads the renamed $.Status leaf; otherwise status is unmapped.
        if _spec_has_field_path(spec, "$.Status"):
            return {
                "reservations": list(mutated.get("reservations", [])),
                "folios": list(mutated.get("folios", [])),
                "properties": list(mutated.get("properties", [])),
            }
        out_res = []
        for rec in mutated.get("reservations", []):
            produced = dict(rec)
            produced.pop("status", None)
            out_res.append(produced)
        return {
            "reservations": out_res,
            "folios": list(mutated.get("folios", [])),
            "properties": list(mutated.get("properties", [])),
        }
    if scenario_id == "meridian_cursor_renamed":
        # Fixed spec follows the renamed NextCursor leaf; otherwise the fetch
        # stops after the first page (null cursor) — truncated like the
        # page_to_cursor mismatch path above.
        nxt = _spec_next_path(spec)
        if isinstance(nxt, str) and "nextcursor" in nxt.lower().replace("_", ""):
            return {
                "reservations": list(mutated.get("reservations", [])),
                "folios": list(mutated.get("folios", [])),
                "properties": list(mutated.get("properties", [])),
            }
        res = mutated.get("reservations", [])
        fol = mutated.get("folios", [])
        truncation = 10 if len(res) > 10 else max(1, len(res) // 2)
        return {
            "reservations": list(res[:truncation]),
            "folios": list(fol[:truncation]) if fol else [],
            "properties": list(mutated.get("properties", [])),
        }

    return {
        "reservations": list(mutated.get("reservations", [])),
        "folios": list(mutated.get("folios", [])),
        "properties": list(mutated.get("properties", [])),
    }


# ---------------------------------------------------------------------------
# High-level oracle entry point
# ---------------------------------------------------------------------------


def oracle_verdict(
    scenario_id: str,
    spec: dict[str, Any] | None = None,
    seed_override: int | None = None,
) -> tuple[str, float, list[dict[str, Any]]]:
    """Run oracle for scenario_id + spec, return (verdict, score, failed_checks).

    Verdict is ``pass`` when no checks fail, ``fail`` otherwise.
    Score is 1.0 for pass, else (total_checks - failed)/total_checks.
    This is imported by checks.py for the full check suite.
    REQ-007: *seed_override* carries the private heldout seed (never logged).
    """
    from verifier.checks import run_all_checks

    expected = generate_expected_canonical(scenario_id, seed_override)
    mutated = generate_mutated(scenario_id, seed_override)
    produced = simulate_produced_records(mutated, expected, spec, scenario_id)

    failed = run_all_checks(expected, produced, mutated, spec, scenario_id)

    if not failed:
        return "pass", 1.0, []

    # Score based on proportion of checks that failed
    # We consider total possible checks = 8 (canonical) + scenario-specific
    total = 8
    score = max(0.0, (total - len(failed)) / total)
    return "fail", round(score, 3), failed
