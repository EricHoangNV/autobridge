"""enum_extend_requires_evidence — ENUM_NEW_VALUE requires evidence.

Produces a hypothesis, not a patch. The patch generator returns [] and signals
that evidence is required; the ladder should not auto-patch without independent
evidence. If evidence is already present (new enum value observed in probe),
a patch extending the enum_map can be generated.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autobridge.repair.context import RepairContext

RULE_ID = "enum_extend_requires_evidence"
CONFIDENCE = 0.85
VERIFICATION_LEVEL = "evidence_required"
PRODUCES_PATCH = False
PRODUCES_HYPOTHESIS = True


def _unknown_enum_value(ctx: RepairContext) -> str | None:
    # Check extra first
    v = ctx.extra.get("new_enum_value") or ctx.extra.get("unknown_enum_value")
    if isinstance(v, str):
        return v
    # Check signals
    for sig in ctx.signals:
        kind = sig.get("kind") if isinstance(sig, dict) else getattr(sig, "kind", None)
        if kind == "unknown_enum":
            if isinstance(sig, dict):
                details = sig.get("details", {})
                if isinstance(details, dict):
                    # Try to extract field/value
                    cand = details.get("value") or details.get("unmet_criteria")
                    if isinstance(cand, str) and ":" in cand:
                        return cand.split(":")[-1]
            elif hasattr(sig, "entity"):
                ent = getattr(sig, "entity", None)
                if isinstance(ent, str):
                    return ent
    return None


def _entity_for_enum(ctx: RepairContext) -> str | None:
    # Infer entity containing enum_map for status
    for ent_name, ent_spec in ctx.spec.get("entities", {}).items():
        if not isinstance(ent_spec, dict):
            continue
        for _field_name, fm in ent_spec.get("fields", {}).items():
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
                    return ent_name
    return next(iter(ctx.spec.get("entities", {})), None)


def precondition(ctx: RepairContext) -> bool:
    if ctx.drift_class != "ENUM_NEW_VALUE":
        return False
    # Must have unknown_enum signal or Blocked on enum
    has_signal = any(
        (sig.get("kind") == "unknown_enum" if isinstance(sig, dict) else getattr(sig, "kind", None) == "unknown_enum")
        for sig in ctx.signals
    )
    if not has_signal and "unknown_enum" not in str(ctx.extra):
        # Also accept if extra has unknown enum info or unmet contains it
        unmet = ctx.extra.get("unmet_criteria") or ctx.extra.get("unmet")
        if isinstance(unmet, list) and any("unknown_enum" in str(x) for x in unmet):
            has_signal = True
        elif ctx.extra.get("failed_checks"):
            fc = ctx.extra["failed_checks"]
            if isinstance(fc, list) and any("unknown" in str(x).lower() for x in fc):
                has_signal = True
    return has_signal


def generate_patch(ctx: RepairContext) -> list[dict[str, Any]]:
    """Return [] (no patch) until evidence is provided.

    If ctx.extra contains 'evidence_confirmed' and the new value, produce a
    patch that extends the enum_map to include it.
    """
    if not ctx.extra.get("evidence_confirmed"):
        return []
    new_value = _unknown_enum_value(ctx) or ctx.extra.get("new_value") or ctx.extra.get("canonical_value")
    if not isinstance(new_value, str):
        return []
    # Find the enum_map location
    for ent_name, ent_spec in ctx.spec.get("entities", {}).items():
        if not isinstance(ent_spec, dict):
            continue
        for field_name, fm in ent_spec.get("fields", {}).items():
            if not isinstance(fm, dict):
                continue
            tf = fm.get("transform")
            steps: list[Any] = tf if isinstance(tf, list) else ([tf] if isinstance(tf, dict) else [])
            for idx, step in enumerate(steps):
                if isinstance(step, dict) and step.get("name") == "enum_map":
                    # Build patched map
                    args = step.get("args", {}) if isinstance(step.get("args"), dict) else {}
                    mmap = dict(args.get("map", {})) if isinstance(args.get("map"), dict) else {}
                    # Add new value mapped to itself or canonical
                    canonical = ctx.extra.get("canonical_value", new_value)
                    mmap[new_value] = canonical
                    # Patch the map via JSON Pointer
                    pointer = f"/entities/{ent_name}/fields/{field_name}/transform/{idx}/args/map"
                    return [
                        {
                            "op": "replace",
                            "path": pointer,
                            "value": mmap,
                        }
                    ]
    return []


def hypothesis_for(ctx: RepairContext) -> dict[str, Any] | None:
    """Return a hypothesis dict when precondition holds but evidence missing."""
    if not precondition(ctx):
        return None
    new_value = _unknown_enum_value(ctx) or ctx.extra.get("unknown_value") or "unknown"
    return {
        "claim": f"enum_map for status should include new value {new_value!r}",
        "candidate_mapping_fragment": {"new_enum_value": new_value},
        "requires_evidence": True,
        "rule_id": RULE_ID,
    }
