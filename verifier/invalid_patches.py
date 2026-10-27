"""Invalid-patch trap set — plausible but wrong patches that must be rejected (P1.W2.7).

Plan §2.7: 'Invalid-patch challenge: a seeded plausible but wrong patch set
(e.g. map `net` to `total_amount` after gross→net drift) must be rejected.'

Each trap is a description of a patch that looks reasonable but is semantically
wrong. The verifier detects the trap via spec inspection (checks.py does this
for invalid_patch_trap; this module provides the explicit set and helpers for
tests and the regression harness).

Never imports autobridge (trust boundary).
"""

from __future__ import annotations

import copy
from typing import Any

# ---------------------------------------------------------------------------
# Trap set — each entry is a plausible-but-wrong patch
# ---------------------------------------------------------------------------

# The canonical example: after gross→net, a naive patch maps net_amount (decoy
# added by invalid_patch_trap scenario) to total_amount. This is wrong because
# total_amount is still gross while net_amount is the decoy net (and the oracle
# expects canonical gross, so net would be a money_mismatch if misapplied in
# other direction).
#
# We enumerate traps per scenario pattern; W2.7 provides at least the gross→net trap.
INVALID_PATCH_TRAPS: list[dict[str, Any]] = [
    {
        "id": "gross_to_net_net_to_total_amount",
        "scenario_id": "invalid_patch_trap",
        "drift_class": "SEMANTIC_VALUE_DRIFT",
        "description": "After gross→net, maps decoy net_amount to total_amount (plausible but wrong — total_amount must stay gross).",
        "marker": "net_amount",
        "target_field": "total_amount",
        "rejected_check": "invalid_patch_trap",
    },
    {
        "id": "gross_to_net_flip_factor",
        "scenario_id": "gross_to_net",
        "drift_class": "SEMANTIC_VALUE_DRIFT",
        "description": "Applies 1.11x multiplier to gross to fake net instead of recognizing field semantics; wrong because it inverts the factor direction.",
        "marker": "total_amount",
        "target_field": "total_amount",
        "rejected_check": "money_mismatch",
    },
    {
        "id": "rename_guest_id_stale_path",
        "scenario_id": "rename_guest_id",
        "drift_class": "FIELD_RENAME",
        "description": "Keeps old guest_name path even though field was renamed; plausible if assuming alias.",
        "marker": "guest_identifier",
        "target_field": "guest_name",
        "rejected_check": "field_missing",
    },
    {
        "id": "status_vocab_identity_map",
        "scenario_id": "status_vocab_change",
        "drift_class": "STATUS_VOCAB",
        "description": "Maps status via identity (no enum_map), assuming vocab unchanged.",
        "marker": "status",
        "target_field": "status",
        "rejected_check": "unknown_status",
    },
    {
        "id": "tz_boundary_keep_utc",
        "scenario_id": "tz_boundary_shift",
        "drift_class": "SEMANTIC_VALUE_DRIFT",
        "description": "Treats posted_at as UTC without property-local cutoff adjustment.",
        "marker": "posted_at",
        "target_field": "business_date",
        "rejected_check": "business_date_mismatch",
    },
    {
        "id": "pagination_keep_offset_on_cursor",
        "scenario_id": "offset_to_cursor",
        "drift_class": "PAGINATION_CHANGE",
        "description": "Keeps offset pagination params when API switched to cursor.",
        "marker": "offset_param",
        "target_field": "pagination",
        "rejected_check": "pagination_shape",
    },
    {
        "id": "pagination_drop_repeated_guard",
        "scenario_id": "cursor_repeat_token",
        "drift_class": "PAGINATION_BUG",
        "description": "Removes repeated_cursor stop_when guard (assumes API fixed the bug).",
        "marker": "repeated_cursor",
        "target_field": "pagination",
        "rejected_check": "repeated_cursor",
    },
]


def get_trap_ids() -> list[str]:
    """Return all trap ids."""
    return [t["id"] for t in INVALID_PATCH_TRAPS]


def get_trap(trap_id: str) -> dict[str, Any]:
    """Return trap by id, raising ValueError if unknown."""
    for t in INVALID_PATCH_TRAPS:
        if t["id"] == trap_id:
            return copy.deepcopy(t)
    raise ValueError(f"unknown trap: {trap_id}")


def get_traps_for_scenario(scenario_id: str) -> list[dict[str, Any]]:
    """Return traps relevant to a scenario_id."""
    return [copy.deepcopy(t) for t in INVALID_PATCH_TRAPS if t["scenario_id"] == scenario_id]


# ---------------------------------------------------------------------------
# Detection — does a spec contain a trap patch?
# ---------------------------------------------------------------------------


def spec_contains_trap(spec: dict[str, Any] | None, trap_id: str | None = None) -> bool:
    """Return True if spec contains any (or a specific) trap patch signature.

    Detection is heuristic based on field path/transform inspection — same
    approach the oracle's checks use for invalid_patch_trap.
    """
    if spec is None:
        return False
    traps = INVALID_PATCH_TRAPS if trap_id is None else [get_trap(trap_id)]
    for trap in traps:
        # Generic (agnostic) check skips purely structural traps that require scenario
        # context (e.g., having offset pagination is not a trap unless the API is cursor).
        if trap_id is None and trap["id"] in ("pagination_keep_offset_on_cursor",):
            continue
        marker = str(trap.get("marker", ""))
        if _spec_contains_marker(spec, marker, trap):
            return True
    return False


def _spec_contains_marker(spec: dict[str, Any], marker: str, trap: dict[str, Any]) -> bool:
    """Check if spec contains the marker that indicates the trap patch."""
    trap_id = trap.get("id", "")
    try:
        entities = spec.get("entities", {})
        ops = spec.get("operations", {})

        # Field-based traps
        for ent in entities.values():
            if not isinstance(ent, dict):
                continue
            fields = ent.get("fields", {})
            if not isinstance(fields, dict):
                continue
            for fm in fields.values():
                if not isinstance(fm, dict):
                    continue
                path = str(fm.get("path", ""))
                if marker in path:
                    # For gross_to_net_net_to_total_amount, having net_amount path is the trap
                    if trap_id == "gross_to_net_net_to_total_amount" and "net_amount" in path:
                        return True
                    # For rename_guest_id_stale_path, old path present means not updated
                    # but trap expects stale — this is detected at scenario level
                    # For status_vocab identity, absence of enum_map is the trap
                    # For others, marker presence is the signal
                    if trap_id not in ("status_vocab_identity_map",):
                        return True

        # Pagination-based traps
        if trap_id == "pagination_keep_offset_on_cursor":
            for op in ops.values():
                if isinstance(op, dict):
                    pag = op.get("pagination", {})
                    if isinstance(pag, dict) and "offset_param" in pag:
                        # If pag still has offset_param when scenario is cursor, it's trap
                        return True

        # For status_vocab identity map trap, detection is absence of enum_map for status
        if trap_id == "status_vocab_identity_map":
            has_enum_map = False
            for ent in entities.values():
                if not isinstance(ent, dict):
                    continue
                for fm in ent.get("fields", {}).values() if isinstance(ent.get("fields"), dict) else []:
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
                            has_enum_map = True
            # Trap = no enum_map for status scenario
            if not has_enum_map:
                return True

    except Exception:
        return False
    return False


# ---------------------------------------------------------------------------
# Trap spec builder — produce a spec that triggers the trap (for tests)
# ---------------------------------------------------------------------------


def apply_trap_to_spec(base_spec: dict[str, Any], trap_id: str) -> dict[str, Any]:
    """Apply a trap patch to base_spec and return the mutated spec.

    The result should be rejected by the verifier (verdict fail with trap's
    rejected_check named).
    """
    # Validate trap exists (raises if unknown)
    get_trap(trap_id)
    spec = copy.deepcopy(base_spec)

    if trap_id == "gross_to_net_net_to_total_amount":
        # Map net_amount -> total_amount (wrong)
        for ent in spec.get("entities", {}).values():
            if not isinstance(ent, dict):
                continue
            fields = ent.get("fields", {})
            if isinstance(fields, dict) and "total_amount" in fields:
                fields["total_amount"] = {"path": "$.net_amount", "transform": [{"name": "money", "args": {"currency": "USD"}}]}
                break

    elif trap_id == "gross_to_net_flip_factor":
        for ent in spec.get("entities", {}).values():
            if not isinstance(ent, dict):
                continue
            for fm in ent.get("fields", {}).values() if isinstance(ent.get("fields"), dict) else []:
                if isinstance(fm, dict) and "total_amount" in str(fm.get("path", "")):
                    fm["transform"] = [{"name": "money", "args": {"currency": "USD", "factor": 1.11}}]

    elif trap_id == "rename_guest_id_stale_path":
        for ent in spec.get("entities", {}).values():
            if not isinstance(ent, dict):
                continue
            fields = ent.get("fields", {})
            if isinstance(fields, dict) and "guest_name" in fields:
                fields["guest_name"] = {"path": "$.guest_name"}

    elif trap_id == "status_vocab_identity_map":
        for ent in spec.get("entities", {}).values():
            if not isinstance(ent, dict):
                continue
            for fm in ent.get("fields", {}).values() if isinstance(ent.get("fields"), dict) else []:
                if isinstance(fm, dict):
                    tf = fm.get("transform")
                    if isinstance(tf, list):
                        fm["transform"] = [s for s in tf if not (isinstance(s, dict) and s.get("name") == "enum_map")]

    elif trap_id == "tz_boundary_keep_utc":
        for ent in spec.get("entities", {}).values():
            if not isinstance(ent, dict):
                continue
            for fm in ent.get("fields", {}).values() if isinstance(ent.get("fields"), dict) else []:
                if isinstance(fm, dict) and "business_date" in str(fm.get("path", "")):
                    # Remove business_date transform handling
                    fm.pop("transform", None)

    elif trap_id == "pagination_keep_offset_on_cursor":
        for op in spec.get("operations", {}).values():
            if isinstance(op, dict):
                pag = op.get("pagination", {})
                if isinstance(pag, dict):
                    pag["strategy"] = "offset"
                    pag["offset_param"] = "offset"
                    pag["limit_param"] = "limit"

    elif trap_id == "pagination_drop_repeated_guard":
        for op in spec.get("operations", {}).values():
            if isinstance(op, dict):
                pag = op.get("pagination", {})
                if isinstance(pag, dict):
                    sw = pag.get("stop_when", [])
                    if isinstance(sw, list) and "repeated_cursor" in sw:
                        pag["stop_when"] = [x for x in sw if x != "repeated_cursor"]

    return spec


def is_trapped_spec_rejected(spec: dict[str, Any] | None, scenario_id: str) -> tuple[bool, list[dict[str, Any]]]:
    """Check if trapped spec is correctly rejected by oracle.

    Returns (rejected, failed_checks). rejected is True when verdict is fail.
    """
    from verifier.oracle import oracle_verdict

    verdict, _score, failed = oracle_verdict(scenario_id, spec)
    return (verdict == "fail"), failed
