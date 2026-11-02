"""rename_field rule — rename repair only for unambiguous wire evidence.

Precondition: drift is SCHEMA_RENAME where exactly one mapped field
vanished or went dark and exactly one new wire key carries values of the
shape the spec expects for that field. The new name must also be
lexically close to the old wire key or the canonical field name (a
generic string similarity over the folded names — no word lists, no
per-field vocabulary).

Anything else (several missing or added keys, no value evidence, a
lexically unrelated name, an added object holding the missing key) is
not an L1 match: the rule declines and the drift climbs to the model,
which sees the new keys and raw records and patches or asks one
question. An explicit caller-supplied rename (``extra`` — a vendor
announcement, a framed record) is a statement, not an inference, and is
exempt from the closeness and value gates.

Patch: updates the source JSONPath for the canonical field to point at
the new source field name.
"""

from __future__ import annotations

import difflib
import re
from typing import TYPE_CHECKING, Any

from autobridge.repair.rules.wire import wire_rename_hint

if TYPE_CHECKING:
    from autobridge.repair.context import RepairContext

RULE_ID = "rename_field"
CONFIDENCE = 0.92
VERIFICATION_LEVEL = "visible"
PRODUCES_PATCH = True

#: Minimum folded-name similarity for an inferred rename to repair at L1.
#: A generic near-match bar: trivial relabels (a suffix, a case change)
#: clear it, unrelated names do not. Anything below climbs to the model.
_LEXICAL_SIMILARITY_THRESHOLD = 0.75

#: Transform names whose values must read as dates (closed transform set).
_DATE_TRANSFORMS = frozenset(
    {"date_local", "datetime_utc", "business_date", "date", "datetime"}
)

#: Transform names whose values must read as numbers.
_MONEY_TRANSFORMS = frozenset({"money"})

_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_DATE_ALT_RE = re.compile(r"^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}")
_MONEY_RE = re.compile(r"^-?[\d,]*(\.\d+)?$")


def _fold_name(name: Any) -> str:
    """Lowercase alphanumeric fold for generic name comparison."""
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


def _name_similarity(first: Any, second: Any) -> float:
    """Generic folded-name similarity in [0, 1] (no word lists)."""
    left, right = _fold_name(first), _fold_name(second)
    if not left or not right:
        return 0.0
    return difflib.SequenceMatcher(None, left, right).ratio()


def _names_lexically_close(old_wire_key: Any, canonical_old: Any, new_field: Any) -> bool:
    """Whether the new name is a near-match of the old wire key or field."""
    return max(
        _name_similarity(new_field, old_wire_key),
        _name_similarity(new_field, canonical_old),
    ) >= _LEXICAL_SIMILARITY_THRESHOLD


def _rename_info(ctx: RepairContext) -> tuple[str, str, str, str] | None:
    """Resolve (entity, old_field, new_field, source) from extra, delta or wire.

    *source* is ``"extra"`` (an explicit caller-supplied rename, e.g. a
    vendor-announced layout change), ``"delta"`` (fingerprint rename) or
    ``"wire"`` (detector value-pairing from keys_changed /
    mapped_field_emptied). An explicit caller statement wins over
    inference: it is exempt from the closeness and value gates below.
    """
    info = _extra_rename_info(ctx)
    if info is not None:
        return (*info, "extra")
    info = _delta_rename_info(ctx.fingerprint_delta)
    if info is not None:
        return (*info, "delta")
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
        return (entity, old_field, new_field, "wire")
    return None


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

    Returns None unless the delta shows exactly one single-field rename
    overall: several renames (or a rename plus other changes) are not an
    unambiguous L1 match and climb instead.
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
    found: tuple[str, str, str] | None = None
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
            if found is not None:
                return None
            found = (entity, next(iter(removed)), next(iter(added)))
    return found


def _delta_same_type(delta: Any | None, entity: str, old_field: str, new_field: str) -> bool:
    """Whether the delta shows the same wire type before and after."""
    details: dict[str, Any] | None = None
    if delta is None:
        return False
    if hasattr(delta, "types_details"):
        details = delta.types_details
    elif isinstance(delta, dict):
        details = delta.get("types_details")
    if not isinstance(details, dict):
        return False
    info = details.get(entity)
    if not isinstance(info, dict):
        return False
    prev = info.get("previous")
    cur = info.get("current")
    if not isinstance(prev, dict) or not isinstance(cur, dict):
        return False
    return old_field in prev and new_field in cur and prev[old_field] == cur[new_field]


def _rename_value_samples(
    ctx: RepairContext, entity: str, old_field: str, new_field: str
) -> tuple[list[Any], list[Any]]:
    """New-key samples + lost values for one inferred pairing (REQ-083 C14).

    New samples come from the ``keys_changed`` added entries (several
    values, not one) or the ``mapped_field_emptied`` candidate samples;
    lost values are the vanished key's last-good baseline values the
    detector attached (``lost_samples``). Empty when the evidence does
    not carry them — the gate then declines.
    """
    from autobridge.repair.rules.wire import _signal_triples  # noqa: PLC0415

    new_samples: list[Any] = []
    lost: list[Any] = []
    for _kind, _op, details in _signal_triples(ctx.signals):
        sig_entity = details.get("entity")
        if isinstance(sig_entity, str) and isinstance(entity, str) and sig_entity != entity:
            continue
        if _kind == "keys_changed":
            for entry in details.get("added") or []:
                if (
                    isinstance(entry, dict)
                    and str(entry.get("key") or "") == str(new_field)
                ):
                    for value in entry.get("samples") or []:
                        if value is not None and value not in new_samples:
                            new_samples.append(value)
                    if entry.get("sample") is not None and entry["sample"] not in new_samples:
                        new_samples.append(entry["sample"])
            lost_map = details.get("lost_samples")
            if isinstance(lost_map, dict):
                for value in lost_map.get(old_field, []) or []:
                    if value is not None and value not in lost:
                        lost.append(value)
        elif _kind == "mapped_field_emptied":
            if details.get("wire_key") != old_field and old_field not in (
                details.get("canonical_fields") or []
            ):
                continue
            cand_samples = details.get("added_candidate_samples")
            if isinstance(cand_samples, dict):
                for value in cand_samples.get(new_field, []) or []:
                    if value is not None and value not in new_samples:
                        new_samples.append(value)
            for value in details.get("lost_samples") or []:
                if value is not None and value not in lost:
                    lost.append(value)
    return (new_samples, lost)


def _new_samples_agree_with_lost(new_samples: list[Any], lost: list[Any]) -> bool:
    """Value agreement on more than one sample (REQ-083 C14).

    The gate fires only when at least two new-key values each agree with
    a lost value (exact or Decimal equality, generic — no name lists).
    Anything else declines so the drift climbs to the model.
    """
    from autobridge.repair.rules.wire import values_agree  # noqa: PLC0415

    if len(new_samples) < 2 or not lost:
        return False
    return all(
        any(values_agree(sample, old) for old in lost) for sample in new_samples
    )


def _wire_shape_sets(ctx: RepairContext, entity: str) -> tuple[set[str], set[str]]:
    """Old and new wire keys the signals report for one entity.

    Prefers the ``keys_changed`` novel-key shape (it excludes perennial
    unmapped keys the build probe saw); falls back to the
    ``mapped_field_emptied`` added-key candidates when no ``keys_changed``
    signal names the entity.
    """
    from autobridge.repair.rules.wire import _signal_triples  # noqa: PLC0415

    missing_kc: set[str] = set()
    added_kc: set[str] = set()
    emptied_old: set[str] = set()
    emptied_new: set[str] = set()
    for _kind, _op, details in _signal_triples(ctx.signals):
        sig_entity = details.get("entity")
        if isinstance(sig_entity, str) and isinstance(entity, str) and sig_entity != entity:
            continue
        if _kind == "keys_changed":
            for name in details.get("missing") or []:
                if isinstance(name, str):
                    missing_kc.add(name)
            for entry in details.get("added") or []:
                if isinstance(entry, dict) and isinstance(entry.get("key"), str):
                    added_kc.add(str(entry["key"]))
        elif _kind == "mapped_field_emptied":
            wire_key = details.get("wire_key")
            if isinstance(wire_key, str):
                emptied_old.add(wire_key)
            for name in details.get("added_candidates") or []:
                if isinstance(name, str):
                    emptied_new.add(name)
    if missing_kc or added_kc:
        return (missing_kc, added_kc)
    return (emptied_old, emptied_new)


def _added_sample(ctx: RepairContext, new_field: str) -> tuple[Any, str | None]:
    """Sample value and type the detector saw for a new wire key."""
    from autobridge.repair.rules.wire import _signal_triples  # noqa: PLC0415

    for _kind, _op, details in _signal_triples(ctx.signals):
        if _kind != "keys_changed":
            continue
        for entry in details.get("added") or []:
            if isinstance(entry, dict) and str(entry.get("key") or "") == str(new_field):
                return (entry.get("sample"), entry.get("type"))
    return (None, None)


def _spec_transform_names(ctx: RepairContext, entity: str, canonical: str) -> set[str]:
    """Transform pipeline names declared for one canonical field."""
    entities = ctx.spec.get("entities", {})
    ent_spec = entities.get(entity) if isinstance(entities, dict) else None
    fields = ent_spec.get("fields") if isinstance(ent_spec, dict) else None
    field = fields.get(canonical) if isinstance(fields, dict) else None
    if not isinstance(field, dict):
        return set()
    names: set[str] = set()
    for tr in field.get("transform") or []:
        if isinstance(tr, str):
            names.add(tr)
        elif isinstance(tr, dict) and isinstance(tr.get("name"), str):
            names.add(str(tr["name"]))
    return names


def _looks_like_date(value: Any) -> bool:
    """Whether a scalar value reads as a calendar date/datetime."""
    if not isinstance(value, str) or not value.strip():
        return False
    text = value.strip()
    try:
        from datetime import datetime  # noqa: PLC0415

        datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T", 1) if " " in text and "T" not in text else text.replace("Z", "+00:00"))
        return True
    except ValueError:
        pass
    return bool(_DATE_PREFIX_RE.match(text) or _DATE_ALT_RE.match(text))


def _looks_like_money(value: Any) -> bool:
    """Whether a scalar value reads as a monetary amount."""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if not isinstance(value, str) or not value.strip():
        return False
    return bool(_MONEY_RE.match(value.strip().replace(",", "")) and any(ch.isdigit() for ch in value))


def _sample_matches_field(sample: Any, transforms: set[str]) -> bool:
    """Whether a new-key sample fits the shape the spec expects.

    The check derives only from the field's declared transform pipeline
    (the closed transform set) — never from field-name word lists. A
    field with no shape-declaring transform accepts any present scalar.
    """
    if sample is None:
        return False
    if isinstance(sample, (dict, list)):
        return False
    if isinstance(sample, str) and not sample.strip():
        return False
    if transforms & _DATE_TRANSFORMS:
        return _looks_like_date(sample)
    if transforms & _MONEY_TRANSFORMS:
        return _looks_like_money(sample)
    return isinstance(sample, (str, int, float, bool))


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


def _added_holds_missing(ctx: RepairContext, old_field: str, new_field: str) -> bool:
    """True when the added wire key is an object holding the missing key.

    The detector's added entries carry sample values; when the new key's
    sample is a dict containing the old key, the vendor nested the field
    (a path move), and a rename patch at the new key would be wrong.
    """
    try:
        from autobridge.repair.rules.wire import _signal_triples  # noqa: PLC0415
    except Exception:
        return False
    try:
        old_top = _path_top_key(f"$.{old_field}") or str(old_field).split(".")[0]
        for _kind, _op, details in _signal_triples(ctx.signals):
            if not isinstance(details, dict):
                continue
            added = details.get("added")
            if not isinstance(added, list):
                continue
            for entry in added:
                if not isinstance(entry, dict):
                    continue
                if str(entry.get("key") or "") != str(new_field):
                    continue
                sample = entry.get("sample")
                if isinstance(sample, dict) and old_top in sample:
                    return True
                # The nested object may arrive under a sibling added key.
                if isinstance(sample, dict):
                    for v in sample.values():
                        if isinstance(v, dict) and old_top in v:
                            return True
    except Exception:
        return False
    return False


def precondition(ctx: RepairContext) -> bool:
    """True when an unambiguous rename is inferred (or stated).

    An explicit caller statement (``extra``) repairs as before. An
    inferred pairing (``delta`` or ``wire``) repairs only when the
    evidence is unambiguous by a generic, vocabulary-free test: exactly
    one mapped field emptied or vanished, exactly one new wire key, the
    new key's values match the lost values in type and shape, and the new
    name is lexically close to the old wire key or the canonical field
    name. Anything else declines so the drift climbs to the model.
    """
    if ctx.drift_class != "SCHEMA_RENAME":
        return False
    # Explicit caller statements first, then delta, then wire evidence
    info = _rename_info(ctx)
    if info is None:
        return False
    entity, old_field, new_field, source = info
    # Spec must contain the old field mapping (so we can patch its path).
    # The reference may be canonical (hand-built) or a wire key (live
    # detector evidence); resolve once and use the canonical name below.
    canonical_old = _canonical_field_for(ctx, entity, old_field)
    if canonical_old is None or not _spec_field_paths(ctx, entity, canonical_old):
        return False
    # REQ-078 item 4: an added object that contains the missing keys is a
    # path move, not a rename — refuse so nested_path_shift (or the model
    # climb) owns it instead of proposing a rename into an object.
    if _added_holds_missing(ctx, old_field, new_field):
        return False
    if source == "extra":
        # A stated rename is not an inference: the gates below do not apply.
        pass
    elif source == "delta":
        if not _delta_same_type(ctx.fingerprint_delta, entity, old_field, new_field):
            return False
        if not _names_lexically_close(old_field, canonical_old, new_field):
            return False
    else:  # source == "wire"
        old_set, new_set = _wire_shape_sets(ctx, entity)
        if old_set != {old_field} or new_set != {new_field}:
            return False
        sample, _sample_type = _added_sample(ctx, new_field)
        if not _sample_matches_field(
            sample, _spec_transform_names(ctx, entity, canonical_old)
        ):
            return False
        # REQ-083 C14: value agreement on more than one sample — the new
        # key's values must each agree with a lost value (exact or
        # Decimal equality, generic). A single shaped sample is not
        # enough; anything else climbs to the model.
        _new_samples, _lost_values = _rename_value_samples(ctx, entity, old_field, new_field)
        if not _new_samples_agree_with_lost(_new_samples, _lost_values):
            return False
        if not _names_lexically_close(old_field, canonical_old, new_field):
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
    entity, old_field, new_field, _source = info
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
