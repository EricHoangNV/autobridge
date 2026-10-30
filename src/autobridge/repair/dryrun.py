"""Dry-run a proposed patch against the faulted sync's records (REQ-053 item 6).

Before the engine offers Approve on a proposal, it applies the patch to a
copy of the active spec and evaluates the touched entities' checks against
the records the break's sync run observed — no new vendor call. A patch
that references a wire field that does not exist (e.g. ``$.net_sum`` on a
vendor that never sends it), or that leaves the failing checks failing, is
rejected with a one-line error the model gets exactly once as a retry; a
still-failing proposal is never offered (question only).

The engine never synthesises the answer: the dry-run only judges the
model's (or rule's) proposal against observed records.
"""

from __future__ import annotations

import copy
from typing import Any


def _json_safe(value: Any) -> Any:
    """JSON-safe copy for the repair-job parcel (Decimal money stays exact as str)."""
    import json as _json

    try:
        return _json.loads(_json.dumps(value, default=str))
    except Exception:
        return []


def build_dry_run_fault(summary: dict[str, Any] | None) -> dict[str, Any] | None:
    """Fault parcel from a faulted sync summary (fast, local, no vendor call).

    Returns ``None`` when the sync never reached the vendor (no responses:
    nothing to judge a patch against — the sync's own outcome already says
    Blocked). Otherwise ``{"unmet_criteria", "canonical_records",
    "wire_records", "raw_first_pages", "entity_ops"}``, all JSON-safe.
    """
    if not isinstance(summary, dict):
        return None
    results = summary.get("operation_results")
    if not isinstance(results, dict) or not results:
        return None

    def _records_of(res: Any) -> list[dict[str, Any]]:
        records = getattr(res, "records", None)
        if records is None and isinstance(res, dict):
            records = res.get("records")
        return [r for r in (records or []) if isinstance(r, dict)]

    def _raw_pages_of(res: Any) -> list[dict[str, Any]]:
        pages = getattr(res, "raw_pages", None)
        if pages is None and isinstance(res, dict):
            pages = res.get("raw_pages")
        return [p for p in (pages or []) if isinstance(p, dict)]

    canonical = summary.get("canonical_records")
    canonical_records = (
        {k: [dict(r) for r in v if isinstance(r, dict)] for k, v in canonical.items()}
        if isinstance(canonical, dict)
        else {}
    )
    wire_records: dict[str, list[dict[str, Any]]] = {}
    raw_first_pages: dict[str, dict[str, Any]] = {}
    for op_name, res in results.items():
        if not isinstance(op_name, str):
            continue
        recs = _records_of(res)
        if recs:
            wire_records[op_name] = [dict(r) for r in recs]
        pages = _raw_pages_of(res)
        if pages:
            raw_first_pages[op_name] = dict(pages[0])
    if not wire_records and not canonical_records:
        return None
    unmet = summary.get("unmet_criteria")
    return {
        "unmet_criteria": [str(c) for c in unmet] if isinstance(unmet, list) else [],
        "canonical_records": _json_safe(canonical_records),
        "wire_records": _json_safe(wire_records),
        "raw_first_pages": _json_safe(raw_first_pages),
    }


def _touched_entities(patch: list[dict[str, Any]]) -> set[str]:
    touched: set[str] = set()
    for op in patch:
        if not isinstance(op, dict):
            continue
        path = op.get("path")
        if not isinstance(path, str):
            continue
        parts = path.split("/")
        if len(parts) >= 3 and parts[1] == "entities" and parts[2]:
            touched.add(parts[2])
    return touched


def _mapping_touched(patch: list[dict[str, Any]], entity: str) -> bool:
    """Whether the patch changes how *entity* maps (vs only its checks)."""
    prefix = f"/entities/{entity}/"
    for op in patch:
        if not isinstance(op, dict):
            continue
        path = op.get("path")
        if not isinstance(path, str) or not path.startswith(prefix):
            continue
        rest = path[len(prefix):]
        if not rest.startswith("checks/"):
            return True
    return False


def _entity_op_name(spec: dict[str, Any], entity: str) -> str | None:
    entities = spec.get("entities")
    if not isinstance(entities, dict):
        return None
    ent_spec = entities.get(entity)
    if not isinstance(ent_spec, dict):
        return None
    for key in ("from_operation", "from"):
        cand = ent_spec.get(key)
        if isinstance(cand, str):
            return cand
    ops = spec.get("operations")
    if isinstance(ops, dict) and len(ops) == 1:
        name = next(iter(ops))
        return name if isinstance(name, str) else None
    return None


def _available_keys(raw_first_page: Any) -> list[str]:
    if isinstance(raw_first_page, dict):
        return sorted(k for k in raw_first_page if isinstance(k, str))[:12]
    return []


def _pointer_key(pointer: Any) -> str | None:
    """Top-level response key for a ``$.key...`` pointer, else None."""
    if not isinstance(pointer, str) or not pointer.startswith("$."):
        return None
    key = pointer[2:].split(".")[0].split("[")[0]
    return key or None


def _pagination_field_roles(spec: dict[str, Any]) -> dict[str, str]:
    """Wire-key roles from the spec's pagination config (REQ-055 item 4a).

    The spec was built from the vendor docs, so its pagination config
    documents what the page-control fields mean — generically, for any
    vendor: cursor/limit/offset/page params and next/items paths.
    """
    roles: dict[str, str] = {}
    ops = spec.get("operations") if isinstance(spec, dict) else {}
    if not isinstance(ops, dict):
        return roles
    for op in ops.values():
        if not isinstance(op, dict):
            continue
        pag = op.get("pagination")
        if not isinstance(pag, dict):
            continue
        for param_key, role in (
            ("cursor_param", "page cursor"),
            ("limit_param", "page size"),
            ("offset_param", "page offset"),
            ("page_param", "page number"),
        ):
            name = pag.get(param_key)
            if isinstance(name, str) and name and name not in roles:
                roles[name] = role
        for path_key, role in (
            ("next_path", "next-page cursor"),
            ("items_path", "the records on this page"),
        ):
            key = _pointer_key(pag.get(path_key))
            if key and key not in roles:
                roles[key] = role
    return roles


def _spec_field_notes(spec: dict[str, Any]) -> dict[str, list[str]]:
    """Wire-key notes from the spec's own mappings and checks (REQ-055 4a).

    A field the spec maps (``entities.<E>.fields.<f>.path``) or compares
    (``compare_to``) is documented by that use — the meaning the docs gave
    it when the connector was built, in generic words.
    """
    notes: dict[str, list[str]] = {}
    entities = spec.get("entities") if isinstance(spec, dict) else {}
    if not isinstance(entities, dict):
        return notes
    for ent_name, ent in entities.items():
        if not isinstance(ent, dict):
            continue
        fields = ent.get("fields")
        if isinstance(fields, dict):
            for fname, fspec in fields.items():
                key = _pointer_key(fspec.get("path") if isinstance(fspec, dict) else None)
                if key:
                    notes.setdefault(key, []).append(f"maps to {ent_name}.{fname}")
        checks = ent.get("checks")
        if isinstance(checks, list):
            for chk in checks:
                if not isinstance(chk, dict):
                    continue
                key = _pointer_key(chk.get("compare_to"))
                if not key:
                    continue
                kind = str(chk.get("kind") or "check")
                field = str(chk.get("field") or ent_name)
                notes.setdefault(key, []).append(f"compared by the {kind} check on `{field}`")
    return notes


def field_meanings_for_retry(
    spec: dict[str, Any],
    fault: dict[str, Any] | None,
) -> dict[str, str]:
    """Meaning of each available wire field, for the L3 retry (REQ-055 4a).

    Built from the connector's own spec (pagination roles, field mappings,
    check references — the documented meaning from the docs it was built
    from) plus the observed response (a record count that matches the page
    size, else the value and its shape). Generic: no vendor or scenario
    names anywhere; unknown keys fall back to what was observed.
    """
    meanings: dict[str, str] = {}
    if not isinstance(spec, dict):
        return meanings
    roles = _pagination_field_roles(spec)
    documented = _spec_field_notes(spec)
    raw_pages = fault.get("raw_first_pages") if isinstance(fault, dict) else {}
    first: dict[str, Any] = {}
    if isinstance(raw_pages, dict):
        for page in raw_pages.values():
            if isinstance(page, dict) and page:
                first = page
                break
    page_sizes = {len(v) for v in first.values() if isinstance(v, list)}
    keys = set(roles) | set(documented) | set(_available_keys(first))
    for key in sorted(keys):
        parts: list[str] = []
        if key in roles:
            parts.append(roles[key])
        parts.extend(documented.get(key, []))
        value = first.get(key, None) if isinstance(first, dict) else None
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and page_sizes
            and value in page_sizes
            and "number of records on this page" not in parts
        ):
            parts.append("number of records on this page")
        if not parts:
            parts.append(f"`{_short_json(value)}` in the current response ({_value_shape(value)})")
        meanings[key] = "; ".join(parts)
    return meanings


def _available_fields_note(spec: dict[str, Any], raw_first_page: Any) -> str:
    """``; available: `k` (meaning); …`` for the retry (REQ-055 item 4a)."""
    keys = _available_keys(raw_first_page)
    if not keys:
        return ""
    try:
        meanings = field_meanings_for_retry(spec, {"raw_first_pages": {"op": raw_first_page}})
    except Exception:
        meanings = {}
    bits = []
    for key in keys:
        meaning = meanings.get(key)
        bits.append(f"`{key}` ({meaning})" if meaning else f"`{key}`")
    note = "; available: " + "; ".join(bits)
    return note if len(note) <= 1200 else note[:1199] + "…"


def _short_json(value: Any, limit: int = 40) -> str:
    try:
        import json as _json

        rendered = _json.dumps(value, default=str)
    except Exception:
        rendered = str(value)
    return rendered if len(rendered) <= limit else rendered[: limit - 1] + "…"


def _value_shape(value: Any) -> str:
    """One-line shape of a resolved ``compare_to`` value (generic wording)."""
    if value is None:
        return "missing or null"
    if isinstance(value, bool):
        return "a boolean"
    try:
        from autobridge.runtime.checks import looks_like_money_sum  # noqa: PLC0415

        if looks_like_money_sum(value):
            return "a money sum"
    except Exception:
        pass
    if isinstance(value, int):
        return "an integer"
    if isinstance(value, float):
        return "a number"
    if isinstance(value, str):
        return "a string"
    return f"a {type(value).__name__}"


def _check_ref(checks: Any, index: int) -> dict[str, Any] | None:
    if isinstance(checks, list) and 0 <= index < len(checks) and isinstance(checks[index], dict):
        return checks[index]
    return None


def field_notes_for_proposal(
    spec: dict[str, Any],
    patch: list[dict[str, Any]] | None,
    fault: dict[str, Any] | None,
) -> list[dict[str, str]]:
    """Per-op notes naming what the target wire field means (REQ-055 item 2).

    Built from observed evidence only — the raw first page the break's sync
    saw, plus the previous pointer the patch replaces. The note cites the
    resolved value and its shape (`` `$.total` is `30` in the current
    response (an integer)``), so the operator sees what the vendor docs'
    field carries without the engine inventing docs wording. Returns
    ``[{path, note}]`` for ops that change a check's ``compare_to`` (or
    remove a check); ``[]`` otherwise and when there is no fault context.
    """
    notes: list[dict[str, str]] = []
    if not isinstance(patch, list) or fault is None or not isinstance(spec, dict):
        return notes
    try:
        from autobridge.runtime.interpreter import extract_jsonpath  # noqa: PLC0415
    except Exception:
        return notes
    raw_first_pages = fault.get("raw_first_pages")
    if not isinstance(raw_first_pages, dict):
        return notes
    old_entities = spec.get("entities") if isinstance(spec, dict) else {}
    if not isinstance(old_entities, dict):
        return notes
    for op in patch:
        if not isinstance(op, dict):
            continue
        op_kind = op.get("op")
        path = op.get("path")
        if not isinstance(path, str):
            continue
        parts = path.split("/")
        if len(parts) < 4 or parts[1] != "entities" or not parts[2]:
            continue
        entity = parts[2]
        old_ent = old_entities.get(entity)
        old_checks = old_ent.get("checks") if isinstance(old_ent, dict) else []
        raw_first = raw_first_pages.get(_entity_op_name(spec, entity))
        if not isinstance(raw_first, dict):
            continue
        # A removed check: name what stops being verified.
        if op_kind == "remove" and len(parts) == 5 and parts[3] == "checks":
            try:
                idx = int(parts[4])
            except (TypeError, ValueError):
                continue
            old_chk = _check_ref(old_checks, idx)
            if old_chk is None:
                continue
            kind = str(old_chk.get("kind") or "check")
            field = str(old_chk.get("field") or entity)
            old_ptr = old_chk.get("compare_to")
            if isinstance(old_ptr, str) and old_ptr:
                notes.append(
                    {
                        "path": path,
                        "note": (
                            f"removes the {kind} check on `{field}` "
                            f"(was comparing against `{old_ptr}`); "
                            "nothing verifies it after this change"
                        ),
                    }
                )
            else:
                notes.append(
                    {
                        "path": path,
                        "note": f"removes the {kind} check on `{field}`; nothing verifies it after this change",
                    }
                )
            continue
        # A changed compare_to pointer: cite the resolved value and shape,
        # contrasting with the pointer it replaces.
        if op_kind in ("replace", "add") and len(parts) == 6 and parts[3] == "checks" and parts[5] == "compare_to":
            ptr = op.get("value")
            if not isinstance(ptr, str) or not ptr:
                continue
            try:
                idx = int(parts[4])
            except (TypeError, ValueError):
                continue
            old_chk = _check_ref(old_checks, idx)
            old_ptr = old_chk.get("compare_to") if old_chk else None
            try:
                resolved = extract_jsonpath(raw_first, ptr)
            except Exception:
                resolved = None
            note = f"`{ptr}` is `{_short_json(resolved)}` in the current response ({_value_shape(resolved)})"
            if isinstance(old_ptr, str) and old_ptr and old_ptr != ptr:
                try:
                    old_resolved = extract_jsonpath(raw_first, old_ptr)
                except Exception:
                    old_resolved = None
                note += f"; was `{old_ptr}` (`{_short_json(old_resolved)}`, {_value_shape(old_resolved)})"
            notes.append({"path": path, "note": note})
    return notes


def dry_run_proposal(
    spec: dict[str, Any],
    patch: list[dict[str, Any]],
    fault: dict[str, Any] | None,
) -> tuple[bool, str]:
    """Apply *patch* to a copy of *spec* and judge it on the faulted records.

    Returns ``(True, detail)`` when every check of every touched entity
    passes on the break's records, else ``(False, one-line error)``. The
    error names a missing wire field with the available keys when a new
    ``compare_to`` pointer resolves to nothing (e.g. ```$.net_sum` is not
    a field in the response; available: …``), or the still-failing checks.
    ``fault=None`` (no faulted records, e.g. bench paths without a sync)
    skips the judgement as passed — the verifier gate still applies.
    """
    if fault is None:
        return True, "no faulted records: dry-run skipped"
    if not isinstance(patch, list) or not patch:
        return False, "no patch ops to dry-run"
    from autobridge.spec.patch import apply_patch  # noqa: PLC0415

    try:
        patched = apply_patch(copy.deepcopy(spec), [dict(op) for op in patch if isinstance(op, dict)])
    except Exception as exc:
        return False, f"patch does not apply: {exc}"
    touched = _touched_entities(patch)
    if not touched:
        return True, "patch touches no entity: nothing to judge"
    from autobridge.runtime.checks import (
        evaluate_entity_checks,  # noqa: PLC0415
        looks_like_money_sum,  # noqa: PLC0415
    )
    from autobridge.runtime.interpreter import extract_jsonpath  # noqa: PLC0415

    canonical_records = fault.get("canonical_records")
    wire_records = fault.get("wire_records")
    raw_first_pages = fault.get("raw_first_pages")
    if not isinstance(canonical_records, dict):
        canonical_records = {}
    if not isinstance(wire_records, dict):
        wire_records = {}
    if not isinstance(raw_first_pages, dict):
        raw_first_pages = {}

    old_entities = spec.get("entities") if isinstance(spec, dict) else {}
    new_entities = patched.get("entities") if isinstance(patched, dict) else {}
    if not isinstance(old_entities, dict):
        old_entities = {}
    if not isinstance(new_entities, dict):
        new_entities = {}

    for entity in sorted(touched):
        new_ent = new_entities.get(entity)
        if not isinstance(new_ent, dict):
            return False, f"patch removes entity {entity!r}"
        new_checks = new_ent.get("checks")
        if not isinstance(new_checks, list):
            new_checks = []
        old_ent = old_entities.get(entity)
        old_checks = old_ent.get("checks") if isinstance(old_ent, dict) else []
        if not isinstance(old_checks, list):
            old_checks = []

        op_name = _entity_op_name(patched, entity)
        raw_first = raw_first_pages.get(op_name) if isinstance(op_name, str) else None

        # A new compare_to pointer that resolves to nothing names the
        # missing field (generic wording, no scenario names): the model's
        # retry gets the exact error. Pointers the old spec already used
        # resolve the same way — a missing old field is the drift's story,
        # not the patch's, so only new pointers fail here.
        old_pointers = {
            str(c.get("compare_to"))
            for c in old_checks
            if isinstance(c, dict) and c.get("compare_to")
        }
        for chk in new_checks:
            if not isinstance(chk, dict):
                continue
            ptr = chk.get("compare_to")
            if not ptr or not isinstance(ptr, str):
                continue
            is_new = ptr not in old_pointers
            found: Any = None
            if isinstance(raw_first, dict):
                try:
                    found = extract_jsonpath(raw_first, ptr)
                except Exception:
                    found = None
            if found is None:
                if not is_new:
                    continue
                # REQ-055 item 4a: names alone steer the model wrong (a
                # record count looks like a net total). List each available
                # field with its documented meaning from the spec the
                # connector was built from — generic, no scenario names.
                avail = _available_fields_note(spec, raw_first)
                return False, f"`{ptr}` is not a field in the response{avail}"
            # REQ-055 item 2: a money check pointed at a value that is not
            # a money sum (e.g. a record count) cannot evaluate — the totals
            # comparison would silently switch off while the label claims it
            # was repointed. This holds whether the patch introduced the
            # pointer or not: a check that is skipped or cannot evaluate
            # counts as not passed (generic wording, so the retry message
            # says so too).
            if chk.get("kind") == "money_invariant" and not looks_like_money_sum(found):
                return False, f"the patched check did not run: `{ptr}` is not a money sum"

        # Records to judge on: re-map the wire records with the patched
        # spec when the patch changes the mapping; otherwise the faulted
        # canonical records stand (mapping untouched by the patch).
        records = canonical_records.get(entity)
        records = [r for r in records if isinstance(r, dict)] if isinstance(records, list) else []
        if _mapping_touched(patch, entity):
            wire = wire_records.get(op_name) if isinstance(op_name, str) else None
            wire = [r for r in wire if isinstance(r, dict)] if isinstance(wire, list) else []
            if wire:
                try:
                    from autobridge.runtime.mapping import map_records_to_entities  # noqa: PLC0415

                    source = patched.get("source") if isinstance(patched, dict) else None
                    remapped, blocked = map_records_to_entities(
                        wire, new_ent, source_spec=source, entity_name=entity
                    )
                    if blocked:
                        first = blocked[0] if isinstance(blocked, list) and blocked else {}
                        detail = str(first.get("error") or first.get("field") or "blocked")[:160]
                        return False, f"records blocked after re-map for {entity}: {detail}"
                    records = remapped
                except Exception as exc:
                    return False, f"re-map failed for {entity} with the patched spec: {exc}"

        compare_value: Any = None
        for chk in new_checks:
            if not isinstance(chk, dict):
                continue
            ptr = chk.get("compare_to")
            if not ptr or not isinstance(ptr, str) or not isinstance(raw_first, dict):
                continue
            try:
                extracted = extract_jsonpath(raw_first, ptr)
            except Exception:
                extracted = None
            if extracted is not None and compare_value is None:
                compare_value = extracted
        unmet = evaluate_entity_checks(
            entity,
            records,
            new_checks,
            expected_count=compare_value if isinstance(compare_value, int) else None,
            compare_to_value=compare_value,
        )
        # The faulted sync's money re-run (mirror of engine.sync_run: a
        # money check whose compare_to resolved to a sum is judged against
        # that sum, even when the no_loss extraction won the shared slot).
        for chk in new_checks:
            if not isinstance(chk, dict) or chk.get("kind") != "money_invariant":
                continue
            field = chk.get("field")
            ptr = chk.get("compare_to")
            if not field or not ptr or not isinstance(ptr, str) or not isinstance(raw_first, dict):
                continue
            try:
                money_sum = extract_jsonpath(raw_first, ptr)
            except Exception:
                money_sum = None
            # REQ-055 item 2: never judge a money invariant against a value
            # that is not a money sum (defense in depth: the patched-pointer
            # check above already rejects such proposals).
            if money_sum is None or not looks_like_money_sum(money_sum):
                continue
            try:
                from autobridge.runtime.checks import check_money_invariant  # noqa: PLC0415

                for name in check_money_invariant(
                    records, field=str(field), entity_name=entity,
                    expected_sum=str(money_sum),
                    tolerance_pct=chk.get("tolerance_pct", 5.0),
                ):
                    if name not in unmet:
                        unmet.append(name)
            except Exception:
                continue
        if unmet:
            return False, f"check(s) still fail on the current records: {', '.join(sorted(set(unmet)))}"
    return True, "the checks pass on the current records"
