"""Mutation tests — flip a transform or drop a check must make verifier fail (P1.W2.7).

Plan §2.7: "Mutation tests on the spec: flip a transform or drop a check → verifier must fail."

These helpers produce mutated versions of a passing spec so that tests can assert
the verifier catches the mutation. They are also usable by the regression suite
to self-test its sensitivity.

Never imports autobridge (trust boundary).
"""

from __future__ import annotations

import copy
from typing import Any

# ---------------------------------------------------------------------------
# Mutation definitions
# ---------------------------------------------------------------------------

MUTATION_IDS: list[str] = [
    "flip_money_to_string",
    "drop_money_transform",
    "flip_enum_map_keys",
    "drop_unique_key_check",
    "drop_no_loss_check",
    "flip_pagination_strategy",
    "drop_retry",
    "flip_date_transform",
    "drop_field",
    "add_wrong_field",
]


def mutate_spec(spec: dict[str, Any], mutation_id: str) -> dict[str, Any]:
    """Return a deep-copied spec with one mutation applied.

    Raises ValueError for unknown mutation_id.
    """
    if mutation_id not in MUTATION_IDS:
        raise ValueError(f"unknown mutation: {mutation_id}")
    mutated = copy.deepcopy(spec)
    if mutation_id == "flip_money_to_string":
        _flip_money_to_string(mutated)
    elif mutation_id == "drop_money_transform":
        _drop_money_transform(mutated)
    elif mutation_id == "flip_enum_map_keys":
        _flip_enum_map_keys(mutated)
    elif mutation_id == "drop_unique_key_check":
        _drop_check(mutated, "unique_key")
    elif mutation_id == "drop_no_loss_check":
        _drop_check(mutated, "no_loss")
    elif mutation_id == "flip_pagination_strategy":
        _flip_pagination_strategy(mutated)
    elif mutation_id == "drop_retry":
        _drop_retry(mutated)
    elif mutation_id == "flip_date_transform":
        _flip_date_transform(mutated)
    elif mutation_id == "drop_field":
        _drop_field(mutated)
    elif mutation_id == "add_wrong_field":
        _add_wrong_field(mutated)
    return mutated


# ---------------------------------------------------------------------------
# Individual mutators (in-place on a copy)
# ---------------------------------------------------------------------------


def _flip_money_to_string(spec: dict[str, Any]) -> None:
    """Replace money transform with a no-op/coalesce so Decimal formatting breaks."""
    for ent in spec.get("entities", {}).values():
        if not isinstance(ent, dict):
            continue
        for field_name, fm in ent.get("fields", {}).items():
            if not isinstance(fm, dict):
                continue
            if "total_amount" in field_name or "amount" in field_name:
                # Replace transform with empty (raw string) — but keep path so structure changes
                fm.pop("transform", None)
                # Change path to a sibling that is numeric-like but not Decimal-formatted
                # (e.g., point at net_amount instead of total_amount)
                path = str(fm.get("path", ""))
                if "total_amount" in path:
                    fm["path"] = path.replace("total_amount", "net_amount")
                elif "amount" in path:
                    fm["path"] = path.replace("amount", "net_amount")


def _drop_money_transform(spec: dict[str, Any]) -> None:
    """Remove money transform steps entirely."""
    for ent in spec.get("entities", {}).values():
        if not isinstance(ent, dict):
            continue
        for fm in ent.get("fields", {}).values():
            if not isinstance(fm, dict):
                continue
            tf = fm.get("transform")
            if isinstance(tf, list):
                # Keep only non-money steps
                fm["transform"] = [s for s in tf if isinstance(s, dict) and s.get("name") != "money"]
                if not fm["transform"]:
                    fm.pop("transform", None)
            elif isinstance(tf, dict) and tf.get("name") == "money":
                fm.pop("transform", None)


def _flip_enum_map_keys(spec: dict[str, Any]) -> None:
    """Swap an enum_map so that canonical status mapping is inverted/broken."""
    for ent in spec.get("entities", {}).values():
        if not isinstance(ent, dict):
            continue
        for fm in ent.get("fields", {}).values():
            if not isinstance(fm, dict):
                continue
            tf = fm.get("transform")
            steps: list[Any] = []
            if isinstance(tf, list):
                steps = tf
            elif isinstance(tf, dict):
                steps = [tf]
            for step in steps:
                if isinstance(step, dict) and step.get("name") == "enum_map":
                    args = step.get("args", {})
                    mmap = args.get("map") if isinstance(args, dict) else None
                    if isinstance(mmap, dict) and mmap:
                        # Swap values to wrong mapping
                        keys = list(mmap.keys())
                        vals = list(mmap.values())
                        # Rotate values by one
                        for i, k in enumerate(keys):
                            mmap[k] = vals[(i + 1) % len(vals)]


def _drop_check(spec: dict[str, Any], check_name: str) -> None:
    """Remove a named check from spec['checks'] if present; otherwise inject a marker."""
    checks = spec.get("checks")
    if isinstance(checks, list):
        remaining = [c for c in checks if not (isinstance(c, dict) and c.get("name") == check_name)]
        if len(remaining) < len(checks):
            spec["checks"] = remaining
            return
        # If check not found but checks list exists, drop first entry to ensure mutation
        if remaining:
            spec["checks"] = remaining[1:]
            return
    # If no checks list, we cannot drop one via spec — instead add a flag that the
    # verifier's produce simulation can interpret as "check removed" by
    # injecting a field drop that will cause downstream check to be untestable.
    # Simplest: remove a required field so record_set will fail.
    _drop_field(spec)
    # Annotate for diagnostics
    spec.setdefault("_mutated", []).append(f"dropped_{check_name}")


def _flip_pagination_strategy(spec: dict[str, Any]) -> None:
    """Flip offset <-> cursor pagination strategy."""
    for op in spec.get("operations", {}).values():
        if not isinstance(op, dict):
            continue
        pag = op.get("pagination")
        if isinstance(pag, dict):
            strat = pag.get("strategy")
            if strat == "offset":
                pag["strategy"] = "cursor"
                pag.pop("offset_param", None)
                pag.pop("limit_param", None)
                # Add minimal cursor fields if missing
                pag.setdefault("cursor_param", "cursor")
                pag.setdefault("next_path", "$.meta.next_cursor")
            elif strat == "cursor":
                pag["strategy"] = "offset"
                pag.pop("cursor_param", None)
                pag.pop("next_path", None)
                pag.setdefault("offset_param", "offset")
                pag.setdefault("limit_param", "limit")


def _drop_retry(spec: dict[str, Any]) -> None:
    """Remove retryStatuses so rate-limited requests are not retried."""
    source = spec.get("source")
    if isinstance(source, dict):
        rate_limit = source.get("rate_limit")
        if isinstance(rate_limit, dict) and "retry" in rate_limit:
            rate_limit.pop("retry", None)


def _flip_date_transform(spec: dict[str, Any]) -> None:
    """Break date handling by changing business date path or transform."""
    mutated = False
    for ent in spec.get("entities", {}).values():
        if not isinstance(ent, dict):
            continue
        for field_name, fm in ent.get("fields", {}).items():
            if not isinstance(fm, dict):
                continue
            if field_name in ("business_date", "arrival_date", "posted_at"):
                tf = fm.get("transform")
                if isinstance(tf, dict) and tf.get("name") in ("date_local", "business_date", "datetime_utc"):
                    fm["transform"] = {"name": "const", "args": {"value": "2099-01-01"}}
                    mutated = True
                elif isinstance(tf, list):
                    for step in tf:
                        if isinstance(step, dict) and step.get("name") in ("date_local", "business_date", "datetime_utc"):
                            step["name"] = "const"
                            step["args"] = {"value": "2099-01-01"}
                            mutated = True
    if not mutated:
        # Fallback: corrupt a date field path so it will not resolve
        for ent in spec.get("entities", {}).values():
            if not isinstance(ent, dict):
                continue
            fields = ent.get("fields", {})
            if isinstance(fields, dict):
                for field_name in ("business_date", "arrival_date"):
                    if field_name in fields and isinstance(fields[field_name], dict):
                        fields[field_name] = {"path": "$.nonexistent_date_xyz", "transform": [{"name": "const", "args": {"value": "2099-99-99"}}]}
                        mutated = True
                        break
                if mutated:
                    break


def _drop_field(spec: dict[str, Any]) -> None:
    """Remove the last field mapping from the first entity (simulates missing field)."""
    for ent in spec.get("entities", {}).values():
        if not isinstance(ent, dict):
            continue
        fields = ent.get("fields")
        if isinstance(fields, dict) and fields:
            last_key = list(fields.keys())[-1]
            fields.pop(last_key, None)
            break


def _add_wrong_field(spec: dict[str, Any]) -> None:
    """Add a field mapping that points at a nonexistent/wrong path."""
    for ent in spec.get("entities", {}).values():
        if not isinstance(ent, dict):
            continue
        fields = ent.get("fields")
        if isinstance(fields, dict):
            fields["_trap_extra"] = {"path": "$.nonexistent_field_xyz", "transform": [{"name": "const", "args": {"value": "trap"}}]}
            break


# ---------------------------------------------------------------------------
# Convenience for tests
# ---------------------------------------------------------------------------


def all_mutations_for_spec(spec: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Generate all mutated specs for a given spec."""
    return [(mid, mutate_spec(spec, mid)) for mid in MUTATION_IDS]
