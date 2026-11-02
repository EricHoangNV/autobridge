"""Reference repairs as spec patches (REQ-083 A2).

For every family keyed ``repair``, the generator's reference repair —
expressed as a JSON Patch in the closed transform/pointer vocabulary —
must repair the instance when applied to the pre-drift spec and synced
through the REAL ``sync_candidate`` path. Where no spec patch can
express the repair, the key is unreachable by patch (the function
returns ``None``): semantic scaling has no factor arg in the closed
transform set (the key stays reachable via its accepted escalation),
and ``dates/tz_shift`` has no hour/day offset arg (B7 removes it from
the draw).

The prover is bench-side test machinery, not the engine: it may build
from instance parameters (the generator's own reference data) and
observed wire diffs. The ENGINE must still decide from observed
evidence only. What the prover proves is expressibility: the closed
vocabulary can name the repair, the real runtime applies it, and the
verifier's records judge passes.
"""

from __future__ import annotations

from typing import Any

ENTITY_FOR = {"reservation": "Reservation", "folio": "FolioCharge"}


def _entity_key(instance: dict[str, Any]) -> str:
    entity = str(instance["params"].get("entity", "reservation"))
    return ENTITY_FOR.get(entity, "Reservation")


def _op_for_entity(spec: dict[str, Any], entity_key: str) -> str | None:
    ent = (spec.get("entities") or {}).get(entity_key, {})
    from_op = ent.get("from_operation") or ent.get("from")
    return str(from_op) if isinstance(from_op, str) and from_op else None


def reference_patch_for(
    instance: dict[str, Any],
    spec: dict[str, Any],
    *,
    faulted_wire: dict[str, list[dict[str, Any]]] | None = None,
    baseline_wire: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]] | None:
    """Reference repair for *instance* as a JSON Patch on *spec*.

    Returns the patch (closed transform/pointer vocabulary only), or
    ``None`` when no spec patch can express the repair. ``faulted_wire``
    / ``baseline_wire`` map operation name → served wire records (from
    the pre-drift spec's faulted/baseline syncs); required for
    ``rename`` (the served new key is observed, never hard-coded).
    """
    family = str(instance.get("family", ""))
    if family == "rename":
        return _rename_patch(instance, spec, faulted_wire, baseline_wire)
    if family == "nesting":
        return _nesting_patch(instance, spec)
    if family == "pagination":
        return _pagination_patch(instance, spec)
    if family == "code_list":
        return _code_list_patch(instance, spec)
    if family == "semantic":
        return _semantic_patch(instance, spec)
    if family == "dates":
        return _dates_patch(instance, spec, faulted_wire, baseline_wire)
    if family == "fias_layout":
        return _fias_layout_patch(instance, spec)
    return None


def _wire_keys(wire: dict[str, list[dict[str, Any]]] | None, op: str) -> set[str]:
    if not wire:
        return set()
    return {str(k) for rec in wire.get(op, []) if isinstance(rec, dict) for k in rec}


def _rename_patch(
    instance: dict[str, Any],
    spec: dict[str, Any],
    faulted_wire: dict[str, list[dict[str, Any]]] | None,
    baseline_wire: dict[str, list[dict[str, Any]]] | None,
) -> list[dict[str, Any]] | None:
    """Repoint the renamed field at the served new key (observed)."""
    if faulted_wire is None or baseline_wire is None:
        return None
    params = dict(instance.get("params", {}))
    field = str(params.get("field", ""))
    ekey = _entity_key(instance)
    op = _op_for_entity(spec, ekey)
    if not op:
        return None
    added = _wire_keys(faulted_wire, op) - _wire_keys(baseline_wire, op)
    removed = _wire_keys(baseline_wire, op) - _wire_keys(faulted_wire, op)
    if len(added) != 1:
        return None
    new_key = next(iter(added))
    fields = ((spec.get("entities") or {}).get(ekey, {}).get("fields") or {})
    current = (fields.get(field) or {}).get("path")
    if not isinstance(current, str) or "." not in current:
        return None
    parent, _, leaf = current.rpartition(".")
    # The mapped old key must really have vanished (a serializer may tie
    # several wire keys to one record field — e.g. Atlas derives both
    # startDate and dateCreated from arrival_date — so the removed set
    # may be larger than the one mapped key).
    if leaf not in removed:
        return None
    return [{
        "op": "replace",
        "path": f"/entities/{ekey}/fields/{field}/path",
        "value": f"{parent}.{new_key}",
    }]


def _nesting_patch(instance: dict[str, Any], spec: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Prefix every mapped field path with the served parents.

    The mutation wraps ``parents[0]`` innermost (``{stay: {details: rec}}``
    for ``parents=["details", "stay"]``), so the wire path is outer-first:
    ``$.stay.details.<leaf>``.
    """
    params = dict(instance.get("params", {}))
    parents = [str(p) for p in params.get("parents", [])]
    if not parents:
        return None
    outer_first = list(reversed(parents))
    ekey = _entity_key(instance)
    fields = ((spec.get("entities") or {}).get(ekey, {}).get("fields") or {})
    ops: list[dict[str, Any]] = []
    for fname, fdef in fields.items():
        if not isinstance(fdef, dict):
            return None
        current = fdef.get("path")
        if current is None:
            # No wire path (const/computed fields): nothing to repoint.
            continue
        if not isinstance(current, str) or not current.startswith("$."):
            # Multi-path fields are repaired through /paths (FIAS only).
            return None
        if current.startswith("$._joined"):
            # Join-computed fields resolve through the join machinery,
            # not the nested wire record: leave them alone.
            continue
        rest = current[2:]
        if "[" in rest or not rest:
            return None
        ops.append({
            "op": "replace",
            "path": f"/entities/{ekey}/fields/{fname}/path",
            "value": f"$.{'.'.join(outer_first + [rest])}",
        })
    # Joins read the raw wire record: a join local key on the nested
    # entity's own wire must move under the parents too. Foreign keys
    # (other operations serve flat) and canonical-side entity keys stay.
    for i, join in enumerate((spec.get("entities") or {}).get(ekey, {}).get("joins") or []):
        if not isinstance(join, dict):
            return None
        local = join.get("local_key")
        if isinstance(local, str) and local.startswith("$.") and not local.startswith("$._joined"):
            rest = local[2:]
            if "[" in rest or not rest:
                return None
            ops.append({
                "op": "replace",
                "path": f"/entities/{ekey}/joins/{i}/local_key",
                "value": f"$.{'.'.join(outer_first + [rest])}",
            })
    # The nested operation also feeds joins on OTHER entities (e.g. the
    # reservation's folio aggregate over nested orderItems): their foreign
    # keys and join field paths move under the parents as well.
    nested_op = _op_for_entity(spec, ekey)
    if nested_op:
        for other_key, other_ent in (spec.get("entities") or {}).items():
            if not isinstance(other_ent, dict) or str(other_key) == ekey:
                continue
            for i, join in enumerate(other_ent.get("joins") or []):
                if not isinstance(join, dict) or str(join.get("from_operation", "")) != nested_op:
                    continue
                foreign = join.get("foreign_key")
                if isinstance(foreign, str) and foreign.startswith("$.") and not foreign.startswith("$._joined"):
                    rest = foreign[2:]
                    if "[" in rest or not rest:
                        return None
                    ops.append({
                        "op": "replace",
                        "path": f"/entities/{other_key}/joins/{i}/foreign_key",
                        "value": f"$.{'.'.join(outer_first + [rest])}",
                    })
                for fname, fspec in (join.get("fields") or {}).items():
                    if not isinstance(fspec, dict):
                        return None
                    for pkey in ("path", "paths"):
                        current = fspec.get(pkey)
                        if current is None:
                            continue
                        if pkey == "path":
                            if not isinstance(current, str) or not current.startswith("$.") or current.startswith("$._joined"):
                                return None
                            rest = current[2:]
                            if "[" in rest or not rest:
                                return None
                            ops.append({
                                "op": "replace",
                                "path": f"/entities/{other_key}/joins/{i}/fields/{fname}/path",
                                "value": f"$.{'.'.join(outer_first + [rest])}",
                            })
                        else:
                            if not isinstance(current, list):
                                return None
                            new_paths = []
                            for p in current:
                                if not isinstance(p, str) or not p.startswith("$.") or p.startswith("$._joined"):
                                    return None
                                rest = p[2:]
                                if "[" in rest or not rest:
                                    return None
                                new_paths.append(f"$.{'.'.join(outer_first + [rest])}")
                            ops.append({
                                "op": "replace",
                                "path": f"/entities/{other_key}/joins/{i}/fields/{fname}/paths",
                                "value": new_paths,
                            })
    return ops or None


def _pagination_patch(instance: dict[str, Any], spec: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Replace list-operation pagination with the served contract.

    The contract parameters ride the instance (the generator's drawn
    pool — ``OFFSET_PARAM_POOL`` etc. — is never served, announced or
    documented, so the verifier demands the exact drawn tokens (B6) and
    the vendor docs must announce them for the repair to be observable;
    the patch itself names the drawn tokens from the instance)."""
    params = dict(instance.get("params", {}))
    strategy = str(params.get("strategy", ""))
    if strategy not in ("offset", "page", "cursor", "body_cursor"):
        return None
    ops: list[dict[str, Any]] = []
    for op_name, op_def in (spec.get("operations") or {}).items():
        if not isinstance(op_def, dict) or not isinstance(op_def.get("pagination"), dict):
            continue
        old_pag = dict(op_def["pagination"])
        if strategy == "offset":
            new_pag = {
                "strategy": "offset",
                "cursor_param": None,
                "offset_param": str(params.get("offset_param", "offset")),
                "page_param": None,
                "limit_param": str(params.get("limit_param", "limit")),
                "next_path": None,
                "items_path": _items_path(params.get("items_path")),
                "total_path": old_pag.get("total_path"),
                # The contract envelope carries no next token for
                # offset/page: the loop must stop on the empty page, or
                # a null-cursor guard stops it after the first page.
                "stop_when": ["empty_page"],
            }
        elif strategy == "page":
            new_pag = {
                "strategy": "page",
                "cursor_param": None,
                "offset_param": None,
                "page_param": str(params.get("page_param", "page")),
                "limit_param": str(params.get("per_page_param", "limit")),
                "next_path": None,
                "items_path": _items_path(params.get("items_path")),
                "total_path": old_pag.get("total_path"),
                # Same no-next-token contract as offset: empty_page stops.
                "stop_when": ["empty_page"],
            }
        elif strategy == "cursor":
            new_pag = {
                "strategy": "cursor",
                "cursor_param": str(params.get("cursor_param", "cursor")),
                "offset_param": None,
                "page_param": None,
                "limit_param": str(params.get("limit_param", "limit")),
                "next_path": _dotted(params.get("next_path")),
                "items_path": _items_path(params.get("items_path")),
                "total_path": old_pag.get("total_path"),
                "stop_when": old_pag.get("stop_when") or ["empty_page"],
            }
        else:  # body_cursor (meridian): renamed size + cursor leaves
            cursor_leaf = str(params.get("cursor_leaf", "Cursor"))
            page_size = str(params.get("page_size_param", "Count"))
            new_pag = {
                "strategy": "body_cursor",
                "cursor_path": f"Limitation.{cursor_leaf}",
                "count_path": f"Limitation.{page_size}",
                "count_value": old_pag.get("count_value", 100),
                "next_path": f"$.{cursor_leaf}",
                "items_path": old_pag.get("items_path"),
                "stop_when": old_pag.get("stop_when") or ["null_cursor", "empty_page"],
            }
        ops.append({
            "op": "replace",
            "path": f"/operations/{op_name}/pagination",
            "value": new_pag,
        })
    return ops or None


def _items_path(value: Any) -> str:
    if isinstance(value, list) and value:
        return "$." + ".".join(str(p) for p in value)
    return "$.data"


def _dotted(value: Any) -> str | None:
    if isinstance(value, list) and value:
        return "$." + ".".join(str(p) for p in value)
    return None


def _code_list_patch(instance: dict[str, Any], spec: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Extend the status enum map with the served new codes."""
    params = dict(instance.get("params", {}))
    if params.get("unknown_code"):
        return None  # escalation instance, not a repair
    mapping = {str(k): str(v) for k, v in dict(params.get("mapping", {})).items()}
    if not mapping:
        return None
    fields = ((spec.get("entities") or {}).get("Reservation", {}).get("fields") or {})
    status = fields.get("status")
    if not isinstance(status, dict):
        return None
    steps = status.get("transform")
    if not isinstance(steps, list):
        return None
    for i, step in enumerate(steps):
        if isinstance(step, dict) and step.get("name") == "enum_map":
            merged = dict((step.get("args") or {}).get("map", {}))
            merged.update(mapping)
            return [{
                "op": "replace",
                "path": f"/entities/Reservation/fields/status/transform/{i}/args/map",
                "value": merged,
            }]
    return None


def _semantic_patch(instance: dict[str, Any], spec: dict[str, Any]) -> list[dict[str, Any]] | None:
    """minor_units repair for minor<->major faults.

    ``gross_net`` scaling has no factor arg in the closed transform set
    (adding one is a plan change), so it is patch-inexpressible — the
    key stays reachable via its accepted escalation. ``minor_major``
    (dollars served as cents) repairs by flipping the money transforms
    to ``minor_units: true`` (exact Decimal division by 100).
    """
    params = dict(instance.get("params", {}))
    if str(params.get("kind", "")) != "minor_major":
        return None
    ops: list[dict[str, Any]] = []
    entities = spec.get("entities") or {}
    for ekey in ("Reservation", "FolioCharge"):
        fields = (entities.get(ekey, {}).get("fields") or {})
        for fname in ("total_amount", "amount"):
            fdef = fields.get(fname)
            if not isinstance(fdef, dict):
                continue
            steps = fdef.get("transform")
            if not isinstance(steps, list):
                continue
            for i, step in enumerate(steps):
                if isinstance(step, dict) and step.get("name") == "money":
                    args = dict(step.get("args") or {})
                    args["minor_units"] = True
                    ops.append({
                        "op": "replace",
                        "path": f"/entities/{ekey}/fields/{fname}/transform/{i}/args",
                        "value": args,
                    })
    return ops or None


def _dates_patch(
    instance: dict[str, Any],
    spec: dict[str, Any],
    faulted_wire: dict[str, list[dict[str, Any]]] | None = None,
    baseline_wire: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]] | None:
    """Reference repairs for the dates family.

    - ``format``: the declarative ``input_format`` on the date transforms
      (REQ-081 item 4) — modified in place, or a minimal ``date_local``
      step appended when the field has no date transform.
    - ``cutoff``: the vendor recomputed business dates under a new
      cutoff, so the mapped business-date string is re-derived from the
      posted instant under the baseline cutoff — the business_date path
      is repointed at the entity's own posted-at wire field (same tz,
      ``cutoff`` 4 = the generator baseline). Meridian ``cutoff`` is
      keyed ``no_patch`` (the wire never serves it), not a repair.
    - ``tz_shift``: patch-inexpressible (no hour/day offset arg in the
      closed set) — B7 removes it from the draw; ``None`` here.
    """
    from simulators.common.generator import BUSINESS_CUTOFF_HOUR as _BASE_CUTOFF  # noqa: PLC0415

    params = dict(instance.get("params", {}))
    mode = str(params.get("mode", ""))
    if mode == "tz_shift":
        return None
    if mode == "format":
        fmt = str(params.get("date_format", ""))
        if not fmt:
            return None
        ops: list[dict[str, Any]] = []
        entities = spec.get("entities") or {}
        for ekey in ("Reservation", "FolioCharge"):
            fields = (entities.get(ekey, {}).get("fields") or {})
            op = _op_for_entity(spec, ekey)
            for fname in ("arrival_date", "departure_date", "business_date"):
                fdef = fields.get(fname)
                if not isinstance(fdef, dict):
                    continue
                steps = fdef.get("transform")
                if not isinstance(steps, list):
                    continue
                patched = False
                for i, step in enumerate(steps):
                    if isinstance(step, dict) and step.get("name") in ("date_local", "business_date", "datetime_utc"):
                        args = dict(step.get("args") or {})
                        args["input_format"] = fmt
                        ops.append({
                            "op": "replace",
                            "path": f"/entities/{ekey}/fields/{fname}/transform/{i}/args",
                            "value": args,
                        })
                        patched = True
                if not patched:
                    ops.append({
                        "op": "replace",
                        "path": f"/entities/{ekey}/fields/{fname}/transform",
                        "value": steps + [{"name": "date_local", "args": {"tz": "source", "input_format": fmt}}],
                    })
                # A vendor may null the derived field and surface the raw
                # date under a new key (meridian nulls ScheduledStartUtc,
                # serves arrival_date verbatim): repoint by value pairing
                # when the mapped path goes dead.
                current = fdef.get("path")
                if (
                    op and isinstance(current, str) and current.startswith("$.")
                    and faulted_wire is not None and baseline_wire is not None
                ):
                    new_path = _dead_path_replacement(
                        current, op, faulted_wire, baseline_wire, fmt
                    )
                    if new_path is not None:
                        ops.append({
                            "op": "replace",
                            "path": f"/entities/{ekey}/fields/{fname}/path",
                            "value": new_path,
                        })
        return ops or None
    if mode == "cutoff":
        if str(instance.get("vendor", "")) == "meridian":
            return None  # keyed no_patch, not a repair
        entities = spec.get("entities") or {}
        folio = entities.get("FolioCharge", {})
        fields = folio.get("fields") or {}
        posted = fields.get("posted_at")
        business = fields.get("business_date")
        if not isinstance(posted, dict) or not isinstance(business, dict):
            return None
        posted_path = posted.get("path")
        if not isinstance(posted_path, str) or not posted_path:
            return None
        tz = "source"
        steps = business.get("transform")
        if isinstance(steps, list) and steps and isinstance(steps[0], dict):
            tz = str((steps[0].get("args") or {}).get("tz", "source"))
        return [{
            "op": "replace",
            "path": "/entities/FolioCharge/fields/business_date",
            "value": {
                "path": posted_path,
                "transform": [{"name": "business_date", "args": {"tz": tz, "cutoff": _BASE_CUTOFF}}],
            },
        }]
    return None


def _extract_wire_path(records: list[dict[str, Any]], pointer: str) -> list[Any]:
    """Values at a ``$.leaf`` wire pointer across records (None when absent)."""
    if not pointer.startswith("$."):
        return []
    leaf = pointer[2:]
    if not leaf or "." in leaf or "[" in leaf:
        return []
    return [rec.get(leaf) if isinstance(rec, dict) else None for rec in records]


def _dead_path_replacement(
    current: str,
    op: str,
    faulted_wire: dict[str, list[dict[str, Any]]],
    baseline_wire: dict[str, list[dict[str, Any]]],
    fmt: str,
) -> str | None:
    """New path for a date field whose mapped wire value went dead.

    When every faulted value at ``current`` is null/missing but an added
    wire key holds values that parse with ``fmt`` to the baseline dates,
    return the added key's pointer. Else None (mapped path still alive).
    """
    from autobridge.runtime.transforms.base import parse_with_input_format  # noqa: PLC0415

    faulted_recs = faulted_wire.get(op, [])
    baseline_recs = baseline_wire.get(op, [])
    if not faulted_recs or not baseline_recs:
        return None
    if any(v is not None for v in _extract_wire_path(faulted_recs, current)):
        return None  # mapped path still serves values
    baseline_dates = [_to_calendar_date(v) for v in _extract_wire_path(baseline_recs, current)]
    if not baseline_dates or any(d is None for d in baseline_dates):
        return None
    faulted_keys = (
        {k for rec in faulted_recs if isinstance(rec, dict) for k in rec}
        - {k for rec in baseline_recs if isinstance(rec, dict) for k in rec}
    )
    for key in sorted(faulted_keys):
        vals = [rec.get(key) for rec in faulted_recs if isinstance(rec, dict)]
        if any(v is None for v in vals):
            continue
        try:
            parsed = [parse_with_input_format(str(v), fmt) for v in vals]
        except Exception:
            continue
        if any(p is None for p in parsed):
            continue
        if [p.date() for p in parsed] == baseline_dates:  # type: ignore[union-attr]
            return f"$.{key}"
    return None


def _to_calendar_date(value: Any) -> Any:
    """Calendar date for an ISO date or instant string, else None."""
    import datetime as _dt

    s = str(value).strip()
    try:
        return _dt.date.fromisoformat(s)
    except (ValueError, TypeError):
        pass
    try:
        return _dt.datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except (ValueError, TypeError):
        return None


def _wire_id(pointer: str) -> str:
    """Bare wire id for a field pointer (``$.TA`` / ``$['P#']`` → ``TA`` / ``P#``)."""
    return str(pointer).strip().lstrip("$").lstrip(".").strip("[]'\"")


def _with_wire_id(pointer: str, new_id: str) -> str:
    """Rewrite a field pointer to a new wire id, keeping its shape."""
    raw = str(pointer).strip()
    if raw.startswith("$["):
        return f"$['{new_id}']"
    return f"$.{new_id}"


def _fias_layout_patch(instance: dict[str, Any], spec: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Rewrite FIAS multi-path ids the layout renamed (``/paths`` pointers)."""
    params = dict(instance.get("params", {}))
    renamed = {str(k): str(v) for k, v in dict(params.get("renamed", {})).items()}
    if not renamed:
        return None
    entities = spec.get("entities") or {}
    ops: list[dict[str, Any]] = []
    for ekey, edef in entities.items():
        if not isinstance(edef, dict):
            continue
        for fname, fdef in (edef.get("fields") or {}).items():
            if not isinstance(fdef, dict):
                continue
            paths = fdef.get("paths")
            if isinstance(paths, list) and any(
                isinstance(p, str) and _wire_id(p) in renamed for p in paths
            ):
                ops.append({
                    "op": "replace",
                    "path": f"/entities/{ekey}/fields/{fname}/paths",
                    "value": [
                        _with_wire_id(p, renamed[_wire_id(p)])
                        if isinstance(p, str) and _wire_id(p) in renamed else p
                        for p in paths
                    ],
                })
            single = fdef.get("path")
            if isinstance(single, str) and _wire_id(single) in renamed:
                ops.append({
                    "op": "replace",
                    "path": f"/entities/{ekey}/fields/{fname}/path",
                    "value": _with_wire_id(single, renamed[_wire_id(single)]),
                })
    return ops or None
