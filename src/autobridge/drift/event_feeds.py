"""Event-feed layout drift detection (generic EVENT transport).

When a framed peer (or any event-driven source) announces the record
layout it will send — FIAS ``LR`` records, but the logic is transport
agnostic — the connector can compare that announcement against the wire
field ids its spec maps for the feed. A mapped id that disappears while a
new id appears is a field rename on the wire; the mapping path must move
(``rename_field`` rule) for the sync to stay ``Complete``.

Everything here is plain data in, plain data out: the announcement is a
list of wire field ids (``["P#", "RN", ...]``), the samples are observed
raw records. No model calls, no transport imports.
"""

from __future__ import annotations

import re
from typing import Any

from autobridge.drift.fingerprint import FingerprintDelta

#: A single-level JSONPath naming a wire field id (``$.TA`` or ``$['P#']``
#: for ids with characters the dot form cannot carry). Only these
#: participate in layout comparison; deeper paths are document structure,
#: not the announced layout.
_WIRE_PATH_RES = (
    re.compile(r"^\$\.([A-Za-z0-9#]{1,8})$"),
    re.compile(r"^\$\['([A-Za-z0-9#]{1,8})'\]$"),
)


def _wire_id_of(path: str) -> str | None:
    for rx in _WIRE_PATH_RES:
        match = rx.match(path)
        if match:
            return match.group(1)
    return None

#: Minor-unit money on the wire is an all-digit string (``"1250"``).
_MONEY_WIRE_RE = re.compile(r"^\d+$")


def _feed_operations(spec: dict[str, Any], record_type: str) -> list[tuple[str, dict[str, Any]]]:
    """Operations with ``method: EVENT`` consuming *record_type*."""
    out: list[tuple[str, dict[str, Any]]] = []
    operations = spec.get("operations")
    if not isinstance(operations, dict):
        return out
    for op_name, op in operations.items():
        if not isinstance(op, dict) or op.get("method") != "EVENT":
            continue
        feed = op.get("feed")
        feed_record = feed.get("record") if isinstance(feed, dict) else getattr(feed, "record", None)
        if feed_record == record_type:
            out.append((str(op_name), op))
    return out


def _entity_for_operation(spec: dict[str, Any], op_name: str) -> tuple[str, dict[str, Any]] | None:
    """First entity mapped ``from`` *op_name* (canonical side of the feed)."""
    entities = spec.get("entities")
    if not isinstance(entities, dict):
        return None
    for ent_name, ent in entities.items():
        if isinstance(ent, dict) and ent.get("from") == op_name:
            return str(ent_name), ent
    return None


def mapped_wire_ids_for_feed(spec: dict[str, Any], record_type: str) -> dict[str, str]:
    """Canonical field → wire id for single-path mappings of the feed.

    Only ``{"path": "$.<id>"}`` mappings count: multi-path (concat) and
    const-led fields carry no single wire id to compare against the
    announcement.
    """
    mapped: dict[str, str] = {}
    for op_name, _op in _feed_operations(spec, record_type):
        found = _entity_for_operation(spec, op_name)
        if found is None:
            continue
        _ent_name, ent = found
        fields = ent.get("fields")
        if not isinstance(fields, dict):
            continue
        for canonical, mapping in fields.items():
            if not isinstance(mapping, dict):
                continue
            path = mapping.get("path")
            if not isinstance(path, str):
                continue
            wid = _wire_id_of(path)
            if wid is not None and canonical not in mapped:
                mapped[str(canonical)] = wid
    return mapped


def _consumed_wire_ids(entity_fields: dict[str, Any]) -> set[str]:
    """Every wire id any mapping of the entity references (``path``/``paths``).

    A rename target must be an id the spec does not already consume: key
    material (``P#``), dates and times are digit strings too, so the money
    shape alone cannot tell them apart from a renamed amount id.
    """
    consumed: set[str] = set()
    for mapping in entity_fields.values():
        if not isinstance(mapping, dict):
            continue
        for key in ("path", "paths"):
            val = mapping.get(key)
            paths = val if isinstance(val, list) else [val]
            for path in paths:
                if isinstance(path, str):
                    wid = _wire_id_of(path)
                    if wid is not None:
                        consumed.add(wid)
    return consumed


def _transform_starts_with(mapping: dict[str, Any], name: str) -> bool:
    steps = mapping.get("transform")
    if not isinstance(steps, list) or not steps:
        return False
    first = steps[0]
    step_name = first.get("name") if isinstance(first, dict) else getattr(first, "name", None)
    return step_name == name


def _money_like(values: list[Any]) -> bool:
    """True when every sample value looks like minor-unit money."""
    if not values:
        return False
    return all(isinstance(v, str) and _MONEY_WIRE_RE.match(v) for v in values)


def detect_feed_field_rename(
    spec: dict[str, Any],
    record_type: str,
    announced_field_ids: list[str],
    sample_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compare an announced feed layout against the spec's mapped wire ids.

    Returns a plain-dict drift report::

        {
            "drift_found": bool,          # a mapped id is missing (mapping
                                          # broken); pure additions are
                                          # reported but do not trip repair,
            "record_type": ..., "entity": ...|None,
            "mapped_wire_ids": {canonical: wire},
            "announced_ids": [...],
            "missing_wire": [...],       # mapped but not announced
            "added_wire": [...],         # announced but not mapped
            "rename": None | {"entity", "canonical_field",
                              "old_wire", "new_wire", "new_path"},
            "delta": FingerprintDelta | None,
            "extra": {...},              # rename info for the repair rule
        }

    The rename resolution is deterministic: exactly one missing id pairs
    with the single added id, or — with several added — with the one whose
    sample values match the missing field's transform shape (today: a
    ``money``-led pipeline pairs with all-digit samples). Otherwise the
    report carries the candidates and leaves the pairing to the operator.
    """
    mapped = mapped_wire_ids_for_feed(spec, record_type)
    announced = [str(fid) for fid in announced_field_ids]
    announced_set = set(announced)
    missing = sorted(wire for wire in mapped.values() if wire not in announced_set)
    mapped_set = set(mapped.values())
    added = sorted(fid for fid in announced if fid not in mapped_set)

    entity: str | None = None
    for op_name, _op in _feed_operations(spec, record_type):
        found = _entity_for_operation(spec, op_name)
        if found is not None:
            entity = found[0]
            break

    rename: dict[str, Any] | None = None
    if len(missing) == 1 and added:
        old_wire = missing[0]
        canonical = next((c for c, w in mapped.items() if w == old_wire), None)
        ent_fields: dict[str, Any] = {}
        if entity is not None:
            entities = spec.get("entities", {})
            ent_fields = entities.get(entity, {}).get("fields", {}) if isinstance(entities, dict) else {}
            if not isinstance(ent_fields, dict):
                ent_fields = {}
        consumed = _consumed_wire_ids(ent_fields)
        new_wire: str | None = None
        if len(added) == 1 and added[0] not in consumed:
            new_wire = added[0]
        elif canonical is not None and entity is not None:
            mapping = ent_fields.get(canonical)
            if isinstance(mapping, dict) and _transform_starts_with(mapping, "money"):
                samples = sample_records or []
                shaped = [
                    fid
                    for fid in added
                    if fid not in consumed
                    and _money_like([r.get(fid) for r in samples if isinstance(r, dict)])
                ]
                if len(shaped) == 1:
                    new_wire = shaped[0]
        if new_wire is not None and canonical is not None and entity is not None:
            rename = {
                "entity": entity,
                "canonical_field": canonical,
                "old_wire": old_wire,
                "new_wire": new_wire,
                "new_path": f"$.{new_wire}",
            }

    drift_found = bool(missing)
    delta: FingerprintDelta | None = None
    if drift_found and entity is not None:
        delta = FingerprintDelta(
            fingerprint_changed=True,
            pagination_changed=False,
            keys_changed=False,
            types_changed=True,
            changed_entities=[entity],
            pagination_details={},
            keys_details={},
            types_details={
                entity: {
                    "previous": {wire: "str" for wire in sorted(mapped.values())},
                    "current": {fid: "str" for fid in announced},
                }
            },
        )
    extra: dict[str, Any] = {
        "record_type": record_type,
        "announced_layout": list(announced),
        "missing_wire": list(missing),
        "added_wire": list(added),
    }
    if rename is not None:
        extra["rename"] = {
            "entity": rename["entity"],
            "old_field": rename["canonical_field"],
            "new_field": rename["new_wire"],
            "new_path": rename["new_path"],
        }
    return {
        "drift_found": drift_found,
        "record_type": record_type,
        "entity": entity,
        "mapped_wire_ids": dict(mapped),
        "announced_ids": list(announced),
        "missing_wire": list(missing),
        "added_wire": list(added),
        "rename": rename,
        "delta": delta,
        "extra": extra,
    }
