"""Fingerprinting for drift detection (P1.W4.1, plan §2.4 + §2.5).

A fingerprint captures three orthogonal dimensions of a successful sync:

* **keys** — the set of entity keys observed (sampled, hashed)
* **types** — field name → type name for the first record per entity
* **pagination_shape** — per-operation strategy, paths, params, stop_when,
  pages_fetched, stop_reason and repeated_cursor

Each dimension can be toggled via ``health.fingerprint`` in the spec (all
true by default). The overall ``fingerprint_id`` is a 16-hex digest over the
combined payload so a drift in any enabled dimension flips the id.

The fingerprint is stored on ``SyncRun.fingerprint_id`` by
``engine.sync_run.execute_sync``.  Drift detection compares the new
fingerprint to the prior one via :func:`diff_fingerprints`.

Trust boundary: no import of ``verifier`` or ``simulators``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


def _hash_string(value: str) -> str:
    """Short hash for key samples (12 hex chars)."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def fingerprint_config(spec: dict[str, Any]) -> dict[str, bool]:
    """Extract ``health.fingerprint`` toggles (all true by default)."""
    health = spec.get("health", {})
    fp = health.get("fingerprint", {}) if isinstance(health, dict) else {}
    if not isinstance(fp, dict):
        return {"keys": True, "types": True, "pagination_shape": True}
    return {
        "keys": bool(fp.get("keys", True)),
        "types": bool(fp.get("types", True)),
        "pagination_shape": bool(fp.get("pagination_shape", True)),
    }


def fingerprint_keys(
    canonical_records: dict[str, list[dict[str, Any]]] | None,
) -> dict[str, str]:
    """Hash first-10 keys per entity into a short fingerprint fragment.

    For each entity the first 10 canonical records are sampled.  For each
    record the first matching field among ``reservation_id``, ``charge_id``,
    ``id``, ``key`` is taken (or the first value as fallback), stringified,
    sorted and hashed via :func:`_hash_string`.  Empty entity → ``"empty"``.
    """
    if not canonical_records:
        return {}
    out: dict[str, str] = {}
    for ent, records in canonical_records.items():
        sample_keys: list[str] = []
        for rec in records[:10]:
            for kf in ("reservation_id", "charge_id", "id", "key"):
                if kf in rec and rec[kf] is not None:
                    sample_keys.append(str(rec[kf]))
                    break
            else:
                if rec:
                    sample_keys.append(str(next(iter(rec.values()))))
        sample_keys = sorted(sample_keys)
        out[ent] = _hash_string("|".join(sample_keys)) if sample_keys else "empty"
    return out


def _type_name(value: Any) -> str:
    """Canonical type name for fingerprinting (Decimal via quantize)."""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if hasattr(value, "quantize"):
        # Decimal has quantize; int/float already handled above.
        return "Decimal"
    if isinstance(value, str):
        return "str"
    if value is None:
        return "null"
    return type(value).__name__


def fingerprint_types(
    canonical_records: dict[str, list[dict[str, Any]]] | None,
) -> dict[str, dict[str, str]]:
    """Histogram ``field → type`` for the first record per entity."""
    if not canonical_records:
        return {}
    out: dict[str, dict[str, str]] = {}
    for ent, records in canonical_records.items():
        if not records:
            continue
        sample = records[0]
        field_types: dict[str, str] = {}
        for fname, fval in sample.items():
            field_types[fname] = _type_name(fval)
        out[ent] = field_types
    return out


def fingerprint_pagination_shape(
    spec: dict[str, Any],
    operation_results: dict[str, Any],
) -> dict[str, Any]:
    """Extract pagination shape per operation for fingerprinting.

    For each operation the spec's pagination fields (strategy,
    cursor/offset/page/limit params, next/items paths, sorted stop_when)
    plus runtime values (pages_fetched, stop_reason, repeated_cursor) are
    collected. Missing results → 0 pages, None stop_reason.
    """
    ops_raw = spec.get("operations", {})
    ops: dict[str, Any] = ops_raw if isinstance(ops_raw, dict) else {}
    shape: dict[str, Any] = {}
    for op_name, op_spec in ops.items():
        if not isinstance(op_spec, dict):
            continue
        pag_raw = op_spec.get("pagination")
        pag: dict[str, Any] = pag_raw if isinstance(pag_raw, dict) else {}
        res = operation_results.get(op_name) if isinstance(operation_results, dict) else None
        stop_when_raw = pag.get("stop_when", [])
        stop_when_sorted = sorted(stop_when_raw) if isinstance(stop_when_raw, list) else []
        # Support both dataclass OperationResult and plain dict results.
        if res is not None:
            pages_fetched = getattr(res, "pages_fetched", None)
            if pages_fetched is None and isinstance(res, dict):
                pages_fetched = res.get("pages_fetched", 0)
            stop_reason = getattr(res, "stop_reason", None)
            if stop_reason is None and isinstance(res, dict):
                stop_reason = res.get("stop_reason")
            repeated_cursor = getattr(res, "repeated_cursor", None)
            if repeated_cursor is None and isinstance(res, dict):
                repeated_cursor = res.get("repeated_cursor", False)
        else:
            pages_fetched = 0
            stop_reason = None
            repeated_cursor = False
        shape[op_name] = {
            "strategy": pag.get("strategy"),
            "cursor_param": pag.get("cursor_param"),
            "offset_param": pag.get("offset_param"),
            "page_param": pag.get("page_param"),
            "limit_param": pag.get("limit_param"),
            "next_path": pag.get("next_path"),
            "items_path": pag.get("items_path"),
            "stop_when": stop_when_sorted,
            "pages_fetched": pages_fetched if pages_fetched is not None else 0,
            "stop_reason": stop_reason,
            "repeated_cursor": bool(repeated_cursor),
        }
    return shape


@dataclass(frozen=True, slots=True)
class Fingerprint:
    """Full fingerprint with fragments and overall id."""

    fingerprint_id: str
    spec_signature: str
    pagination_shape: dict[str, Any] | None
    keys_sample: dict[str, str] | None
    types: dict[str, dict[str, str]] | None
    config: dict[str, bool]


@dataclass(frozen=True, slots=True)
class FingerprintDelta:
    """Delta between two fingerprints (None = no prior)."""

    fingerprint_changed: bool
    pagination_changed: bool
    keys_changed: bool
    types_changed: bool
    changed_entities: list[str]
    pagination_details: dict[str, Any]
    keys_details: dict[str, Any]
    types_details: dict[str, Any]


def _spec_signature(spec: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def compute_fingerprint(
    spec: dict[str, Any],
    operation_results: dict[str, Any],
    canonical_records: dict[str, list[dict[str, Any]]] | None = None,
) -> Fingerprint:
    """Compute the full :class:`Fingerprint` for a sync run."""
    cfg = fingerprint_config(spec)
    spec_sig = _spec_signature(spec)

    pagination_shape: dict[str, Any] | None = None
    if cfg["pagination_shape"]:
        pagination_shape = fingerprint_pagination_shape(spec, operation_results)

    keys_sample: dict[str, str] | None = None
    if cfg["keys"] and canonical_records is not None:
        keys_sample = fingerprint_keys(canonical_records)

    types_frag: dict[str, dict[str, str]] | None = None
    if cfg["types"] and canonical_records is not None:
        types_frag = fingerprint_types(canonical_records)
        if not types_frag:
            types_frag = None

    # Build payload matching the historic engine.sync_run._fingerprint_id shape.
    payload: dict[str, Any] = {"spec_signature": spec_sig}
    if pagination_shape is not None:
        payload["pagination_shape"] = pagination_shape
    else:
        # Legacy fallback when pagination_shape disabled: pages + stop_reasons.
        payload["pages"] = {}
        payload["stop_reasons"] = {}
        if isinstance(operation_results, dict):
            for k, v in operation_results.items():
                pf = getattr(v, "pages_fetched", None)
                if pf is None and isinstance(v, dict):
                    pf = v.get("pages_fetched", 0)
                sr = getattr(v, "stop_reason", None)
                if sr is None and isinstance(v, dict):
                    sr = v.get("stop_reason")
                payload["pages"][k] = pf if pf is not None else 0
                payload["stop_reasons"][k] = sr
    if keys_sample is not None:
        payload["keys_sample"] = keys_sample
    if types_frag is not None:
        payload["types"] = types_frag

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    fid = hashlib.sha256(canonical.encode()).hexdigest()[:16]

    return Fingerprint(
        fingerprint_id=fid,
        spec_signature=spec_sig,
        pagination_shape=pagination_shape,
        keys_sample=keys_sample,
        types=types_frag,
        config=cfg,
    )


def compute_fingerprint_id(
    spec: dict[str, Any],
    operation_results: dict[str, Any],
    canonical_records: dict[str, list[dict[str, Any]]] | None = None,
) -> str:
    """Convenience: fingerprint id string only (compat with sync_run)."""
    return compute_fingerprint(spec, operation_results, canonical_records).fingerprint_id


# Back-compat alias so callers importing the old helper keep working.
fingerprint_id = compute_fingerprint_id


def diff_fingerprints(
    previous: Fingerprint | dict[str, Any] | str | None,
    current: Fingerprint | dict[str, Any],
) -> FingerprintDelta:
    """Compare two fingerprints and report which dimensions changed.

    *previous* may be a :class:`Fingerprint`, a dict with the fragment keys,
    a bare id string (then only ``fingerprint_changed`` is meaningful), or
    ``None`` (no prior → no change flagged except overall).

    *current* must be a :class:`Fingerprint` or a dict with the same shape
    (``pagination_shape``, ``keys_sample``, ``types``).
    """
    # Normalize to dicts of fragments for comparison.
    def _to_dict(fp: Fingerprint | dict[str, Any] | str | None) -> dict[str, Any] | None:
        if fp is None:
            return None
        if isinstance(fp, str):
            return {"fingerprint_id": fp}
        if isinstance(fp, Fingerprint):
            return {
                "fingerprint_id": fp.fingerprint_id,
                "spec_signature": fp.spec_signature,
                "pagination_shape": fp.pagination_shape,
                "keys_sample": fp.keys_sample,
                "types": fp.types,
            }
        if isinstance(fp, dict):
            # Already payload-like; support both full payload and Fingerprint dict.
            return fp
        return None

    prev_d = _to_dict(previous)
    cur_d = _to_dict(current)

    if prev_d is None:
        return FingerprintDelta(
            fingerprint_changed=True,
            pagination_changed=False,
            keys_changed=False,
            types_changed=False,
            changed_entities=[],
            pagination_details={},
            keys_details={},
            types_details={},
        )

    # fingerprint_changed: id differs or spec_signature differs when id missing
    prev_id = prev_d.get("fingerprint_id")
    cur_id = cur_d.get("fingerprint_id") if cur_d else None
    if prev_id is not None and cur_id is not None:
        fingerprint_changed = prev_id != cur_id
    else:
        fingerprint_changed = prev_d != cur_d

    # Dimension-level diffs (only when both sides present)
    pagination_changed = False
    pagination_details: dict[str, Any] = {}
    prev_pag = prev_d.get("pagination_shape")
    cur_pag = cur_d.get("pagination_shape") if cur_d else None
    if prev_pag is not None and cur_pag is not None:  # noqa: SIM102
        if prev_pag != cur_pag:  # noqa: SIM102
            pagination_changed = True
            # Detail per operation which keys differ
            all_ops = set(prev_pag.keys()) | set(cur_pag.keys())
            for op in sorted(all_ops):
                if prev_pag.get(op) != cur_pag.get(op):
                    pagination_details[op] = {
                        "previous": prev_pag.get(op),
                        "current": cur_pag.get(op),
                    }
    elif prev_pag != cur_pag:  # noqa: SIM102
        # One side missing → consider changed if we have concrete values
        if prev_pag is not None or cur_pag is not None:  # noqa: SIM102
            # Only flag when non-empty to avoid false positives on toggled config
            if prev_pag or cur_pag:  # noqa: SIM102
                pagination_changed = True

    keys_changed = False
    keys_details: dict[str, Any] = {}
    prev_keys = prev_d.get("keys_sample")
    cur_keys = cur_d.get("keys_sample") if cur_d else None
    if prev_keys is not None and cur_keys is not None:  # noqa: SIM102
        if prev_keys != cur_keys:  # noqa: SIM102
            keys_changed = True
            all_ents = set(prev_keys.keys()) | set(cur_keys.keys())
            for ent in sorted(all_ents):
                if prev_keys.get(ent) != cur_keys.get(ent):
                    keys_details[ent] = {"previous": prev_keys.get(ent), "current": cur_keys.get(ent)}
    elif prev_keys != cur_keys:  # noqa: SIM102
        if prev_keys or cur_keys:  # noqa: SIM102
            keys_changed = True

    types_changed = False
    types_details: dict[str, Any] = {}
    prev_types = prev_d.get("types")
    cur_types = cur_d.get("types") if cur_d else None
    if prev_types is not None and cur_types is not None:  # noqa: SIM102
        if prev_types != cur_types:  # noqa: SIM102
            types_changed = True
            all_ents = set(prev_types.keys()) | set(cur_types.keys())
            for ent in sorted(all_ents):
                if prev_types.get(ent) != cur_types.get(ent):
                    types_details[ent] = {
                        "previous": prev_types.get(ent),
                        "current": cur_types.get(ent),
                    }
    elif prev_types != cur_types:  # noqa: SIM102
        if prev_types or cur_types:  # noqa: SIM102
            types_changed = True

    # changed_entities: union of keys and types changes plus pagination ops? Keep entities.
    changed_entities_set: set[str] = set()
    changed_entities_set.update(keys_details.keys())
    changed_entities_set.update(types_details.keys())

    return FingerprintDelta(
        fingerprint_changed=fingerprint_changed,
        pagination_changed=pagination_changed,
        keys_changed=keys_changed,
        types_changed=types_changed,
        changed_entities=sorted(changed_entities_set),
        pagination_details=pagination_details,
        keys_details=keys_details,
        types_details=types_details,
    )
