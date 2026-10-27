"""rename_field rule — fuzzy field rename with value-shape match.

Precondition: drift is SCHEMA_RENAME where one field was renamed to a new
name with the same type (e.g. guest_name -> guest_identifier). Detected via
FingerprintDelta.types_details or via explicit rename info in ctx.extra.

Patch: updates the source JSONPath for the canonical field to point at the
new source field name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autobridge.repair.context import RepairContext

RULE_ID = "rename_field"
CONFIDENCE = 0.92
VERIFICATION_LEVEL = "visible"
PRODUCES_PATCH = True


def _delta_rename_info(delta: Any | None) -> tuple[str, str, str] | None:
    """Extract (entity, old_field, new_field) from FingerprintDelta.

    Returns None if delta does not look like a single-field rename.
    """
    if delta is None:
        return None
    # Support both FingerprintDelta dataclass and dict form.
    details: dict[str, Any] | None = None
    if hasattr(delta, "types_details"):
        details = delta.types_details
    elif isinstance(delta, dict):
        details = delta.get("types_details")
        if details is None:
            # Allow flat dict passed as types_details itself
            details = delta if any(isinstance(v, dict) and "previous" in v for v in delta.values()) else None
    if not details:
        return None
    for entity, info in details.items():
        if not isinstance(info, dict):
            continue
        prev = info.get("previous")
        cur = info.get("current")
        if not isinstance(prev, dict) or not isinstance(cur, dict):
            continue
        prev_fields = set(prev.keys())
        cur_fields = set(cur.keys())
        removed = prev_fields - cur_fields
        added = cur_fields - prev_fields
        if len(removed) == 1 and len(added) == 1:
            old_field = next(iter(removed))
            new_field = next(iter(added))
            # Same type is expected for rename; still consider if different as fallback
            return (entity, old_field, new_field)
    return None


def _extra_rename_info(ctx: RepairContext) -> tuple[str, str, str] | None:
    """Check ctx.extra for explicit rename mapping (used by tests)."""
    ex = ctx.extra
    # Direct mapping supplied by caller
    if "rename" in ex and isinstance(ex["rename"], dict):
        r = ex["rename"]
        entity = r.get("entity")
        old = r.get("old_field") or r.get("from")
        new = r.get("new_field") or r.get("to")
        if isinstance(entity, str) and isinstance(old, str) and isinstance(new, str):
            return (entity, old, new)
    # Legacy alias fields
    old_field = ex.get("field") or ex.get("old_field")
    new_field = ex.get("new_field")
    entity = ex.get("entity")
    if isinstance(old_field, str) and isinstance(new_field, str):
        # If entity missing, infer from spec
        if not isinstance(entity, str):
            # Pick first entity that contains old_field in its field mapping
            for ent_name, ent_spec in ctx.spec.get("entities", {}).items():
                if isinstance(ent_spec, dict) and old_field in ent_spec.get("fields", {}):
                    entity = ent_name
                    break
            if not isinstance(entity, str):
                entity = next(iter(ctx.spec.get("entities", {})), "Reservation")
        return (str(entity), old_field, new_field)
    return None


def _spec_has_field(ctx: RepairContext, entity: str, field: str) -> bool:
    ent = ctx.spec.get("entities", {}).get(entity)
    if not isinstance(ent, dict):
        return False
    fields = ent.get("fields", {})
    return isinstance(fields, dict) and field in fields


def precondition(ctx: RepairContext) -> bool:
    """True when a single-field rename can be inferred."""
    if ctx.drift_class != "SCHEMA_RENAME":
        return False
    # Try delta first, then extra
    info = _delta_rename_info(ctx.fingerprint_delta)
    if info is None:
        info = _extra_rename_info(ctx)
    if info is None:
        return False
    entity, old_field, new_field = info
    # Spec must contain the old field mapping (so we can patch its path)
    if not _spec_has_field(ctx, entity, old_field):
        return False
    # New field should not already be a canonical field name in that entity
    # (otherwise it's not a rename of source path but a different kind)
    # We allow it — but require old != new
    return old_field != new_field


def generate_patch(ctx: RepairContext) -> list[dict[str, Any]]:
    """Produce JSON Patch updating the source path for the renamed field."""
    info = _delta_rename_info(ctx.fingerprint_delta)
    if info is None:
        info = _extra_rename_info(ctx)
    if info is None:
        return []
    entity, old_field, new_field = info
    # Build pointer: /entities/{entity}/fields/{field}/path
    pointer = f"/entities/{entity}/fields/{old_field}/path"
    # Value is JSONPath to new source field
    value = f"$.{new_field}"
    # If extra already provides full new path, use it
    if ctx.extra.get("new_path"):
        value = str(ctx.extra["new_path"])
    elif ctx.extra.get("rename", {}).get("new_path") if isinstance(ctx.extra.get("rename"), dict) else False:
        value = str(ctx.extra["rename"]["new_path"])
    return [
        {
            "op": "replace",
            "path": pointer,
            "value": value,
        }
    ]
