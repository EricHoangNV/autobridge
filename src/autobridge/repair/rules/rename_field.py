"""rename_field rule — fuzzy field rename with value-shape match.

Precondition: drift is SCHEMA_RENAME where one field was renamed to a new
name with the same type (e.g. guest_name -> guest_identifier). Detected via
FingerprintDelta.types_details or via explicit rename info in ctx.extra.

Patch: updates the source JSONPath for the canonical field to point at the
new source field name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from autobridge.repair.rules.wire import wire_rename_hint

if TYPE_CHECKING:
    from autobridge.repair.context import RepairContext

RULE_ID = "rename_field"
CONFIDENCE = 0.92
VERIFICATION_LEVEL = "visible"
PRODUCES_PATCH = True


def _rename_info(ctx: RepairContext) -> tuple[str, str, str] | None:
    """Resolve (entity, old_field, new_field) from delta, extra or wire."""
    info = _delta_rename_info(ctx.fingerprint_delta)
    if info is None:
        info = _extra_rename_info(ctx)
    if info is None:
        # Wire evidence (REQ-027): single missing + single added wire key
        # observed on the raw records by the detector.
        hint = wire_rename_hint(ctx.signals)
        if hint is not None:
            entity, old_field, new_field = hint
            if entity is None:
                # Infer from spec (canonical name or wire path top key).
                for ent_name in ctx.spec.get("entities", {}):
                    if not isinstance(ent_name, str):
                        continue
                    if _canonical_field_for(ctx, ent_name, old_field) is not None:
                        entity = ent_name
                        break
                if entity is None:
                    entity = next(iter(ctx.spec.get("entities", {})), "Reservation")
            info = (entity, old_field, new_field)
    return info


def _path_top_key(path: Any) -> str | None:
    """Top-level wire key a field path reads (``$.guestName`` -> ``guestName``)."""
    if not isinstance(path, str) or not path.startswith("$."):
        return None
    rest = path[2:]
    if not rest:
        return None
    return rest.split(".")[0]


def _canonical_field_for(ctx: RepairContext, entity: str, name: str) -> str | None:
    """Resolve a field reference to its canonical spec field name.

    Live detector evidence carries *wire* key names (``guestName``) while
    hand-built contexts use canonical names (``guest_name``). A wire key
    resolves to the single canonical field whose path top key it is; an
    ambiguous top key (shared envelope) resolves to nothing rather than
    guessing.
    """
    entities = ctx.spec.get("entities", {})
    ent_spec = entities.get(entity) if isinstance(entities, dict) else None
    if not isinstance(ent_spec, dict):
        return None
    fields = ent_spec.get("fields", {})
    if not isinstance(fields, dict):
        return None
    if name in fields and isinstance(fields[name], dict):
        return name
    matches: list[str] = []
    for fname, fm in fields.items():
        if not isinstance(fm, dict):
            continue
        candidates: list[Any] = []
        if fm.get("path") is not None:
            candidates.append(fm.get("path"))
        extra_paths = fm.get("paths")
        if isinstance(extra_paths, list):
            candidates.extend(extra_paths)
        if any(_path_top_key(p) == name for p in candidates):
            matches.append(fname)
    if len(matches) == 1:
        return matches[0]
    return None


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
        # If entity missing, infer from spec: canonical field name or wire
        # path top key (live detector evidence carries wire names).
        if not isinstance(entity, str):
            for ent_name in ctx.spec.get("entities", {}):
                if not isinstance(ent_name, str):
                    continue
                if _canonical_field_for(ctx, ent_name, old_field) is not None:
                    entity = ent_name
                    break
            if not isinstance(entity, str):
                entity = next(iter(ctx.spec.get("entities", {})), "Reservation")
        return (str(entity), old_field, new_field)
    return None


def _spec_field_paths(ctx: RepairContext, entity: str, field: str) -> list[str]:
    """Current source paths of one field (``path`` or ``paths``).

    Accepts a canonical field name or a wire key (resolved to canonical).
    """
    canonical = _canonical_field_for(ctx, entity, field)
    if canonical is None:
        return []
    ent = ctx.spec.get("entities", {}).get(entity)
    if not isinstance(ent, dict):
        return []
    fm = ent.get("fields", {}).get(canonical)
    if not isinstance(fm, dict):
        return []
    if fm.get("path") is not None:
        return [str(fm["path"])]
    if isinstance(fm.get("paths"), list):
        return [str(p) for p in fm["paths"]]
    return []


def _explicit_new_path(ctx: RepairContext) -> str | None:
    """An explicitly supplied full new path, if any (wire renames)."""
    ex = ctx.extra
    if isinstance(ex.get("new_path"), str) and ex["new_path"]:
        return ex["new_path"]
    rename = ex.get("rename")
    if isinstance(rename, dict) and isinstance(rename.get("new_path"), str) and rename["new_path"]:
        return rename["new_path"]
    return None


def precondition(ctx: RepairContext) -> bool:
    """True when a single-field rename can be inferred."""
    if ctx.drift_class != "SCHEMA_RENAME":
        return False
    # Try delta first, then extra, then wire evidence
    info = _rename_info(ctx)
    if info is None:
        return False
    entity, old_field, new_field = info
    # Spec must contain the old field mapping (so we can patch its path).
    # The reference may be canonical (hand-built) or a wire key (live
    # detector evidence); resolve once and use the canonical name below.
    canonical_old = _canonical_field_for(ctx, entity, old_field)
    if canonical_old is None or not _spec_field_paths(ctx, entity, canonical_old):
        return False
    # New field should not already be a canonical field name in that entity
    # (otherwise it's not a rename of source path but a different kind)
    # We allow it — but require old != new, unless the caller names an
    # explicit new path that differs from the current one: a framed record
    # can rename a wire id (``TA`` → ``TX``) while the canonical field
    # (``amount``) keeps its name, and that is a real change, not a no-op.
    if old_field != new_field:
        return True
    wanted = _explicit_new_path(ctx)
    return wanted is not None and wanted not in _spec_field_paths(ctx, entity, canonical_old)


def generate_patch(ctx: RepairContext) -> list[dict[str, Any]]:
    """Produce JSON Patch updating the source path for the renamed field."""
    info = _rename_info(ctx)
    if info is None:
        return []
    entity, old_field, new_field = info
    canonical_old = _canonical_field_for(ctx, entity, old_field)
    if canonical_old is None:
        return []
    # Build pointer: /entities/{entity}/fields/{field}/path
    pointer = f"/entities/{entity}/fields/{canonical_old}/path"
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
