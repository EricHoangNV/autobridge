"""Declarative joins — enrich base records from other operations (REQ-020 5c).

A join indexes one operation's records by a foreign key and attaches
computed scalars to each base record under the reserved ``_joined``
namespace (``_joined.<as>.<field>``). Entity field mappings then read the
enriched records with ordinary JSONPath + the closed transform set.

Cardinality ``one`` attaches from the first matching record (e.g. a
reservation's guest name from the customers operation by ``AccountId``);
``many`` aggregates over all matches (e.g. a reservation's total as the
exact-Decimal ``sum`` of its order-item gross amounts).

Money stays ``Decimal`` end to end: ``sum`` adds Decimals exactly and
never touches float. Unresolvable joins attach ``None`` (an honest
missing value the mapper omits); structural misuse raises
:class:`JoinError`. Inputs are never mutated.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

try:
    from jsonpath_ng import parse as jsonpath_parse
except ImportError:  # pragma: no cover
    jsonpath_parse = None  # type: ignore[assignment]

from autobridge.runtime.transforms import apply_transform_pipeline

#: Reserved enrichment namespace. Vendor records never carry this key, so
#: joined values cannot collide with wire data.
JOIN_NAMESPACE = "_joined"


class JoinError(ValueError):
    """Structural join failure (unknown operation, uncoercible aggregate)."""


def _extract_all(record: Any, path: str) -> list[Any]:
    """All JSONPath matches in a record; [] when nothing matches."""
    if jsonpath_parse is None:
        raise JoinError("jsonpath-ng required for joins")
    try:
        return [m.value for m in jsonpath_parse(path).find(record)]
    except Exception:
        return []


def _extract_one(record: Any, path: str) -> Any:
    """First JSONPath match in a record; None when nothing matches."""
    matches = _extract_all(record, path)
    return matches[0] if matches else None


def _keys_equal(local: Any, foreign: Any) -> bool:
    """Join-key equality with numeric tolerance (Decimal 5 == int 5)."""
    if local is None or foreign is None:
        return False
    if local == foreign:
        return True
    if isinstance(local, (Decimal, int)) and isinstance(foreign, (Decimal, int)):
        if isinstance(local, bool) or isinstance(foreign, bool):
            return False
        try:
            return Decimal(local) == Decimal(foreign)
        except (InvalidOperation, ValueError, ArithmeticError):
            return False
    return False


def _coerce_decimal(value: Any, *, what: str) -> Decimal:
    """Coerce a money-ish value to Decimal; never float."""
    if isinstance(value, bool):
        raise JoinError(f"join {what}: bool is not a number: {value!r}")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        raise JoinError(f"join {what}: float rejected (money is Decimal): {value!r}")
    if isinstance(value, str):
        try:
            return Decimal(value.strip().replace(",", ""))
        except InvalidOperation:
            raise JoinError(f"join {what}: not a number: {value!r}") from None
    raise JoinError(f"join {what}: unsupported type {type(value).__name__}: {value!r}")


def _aggregate(op: str, values: list[Any], *, what: str) -> Any:
    """Reduce per-record values with a join aggregation operator."""
    present = [v for v in values if v is not None]
    if not present:
        return None
    if op == "first":
        return present[0]
    if op == "sum":
        total = Decimal(0)
        for v in present:
            total += _coerce_decimal(v, what=what)
        return total
    if op in ("min", "max"):
        try:
            return min(present) if op == "min" else max(present)
        except TypeError:
            strs = [str(v) for v in present]
            return min(strs) if op == "min" else max(strs)
    raise JoinError(f"join {what}: unknown aggregate {op!r}")


def _steps_for(field: Any) -> list[dict[str, Any]]:
    """Normalize a join field's transform pipeline to plain dicts."""
    transform = getattr(field, "transform", None)
    if not transform:
        return []
    steps: list[dict[str, Any]] = []
    for s in transform:
        if isinstance(s, dict):
            steps.append({"name": s.get("name"), "args": s.get("args", {}) or {}})
        elif hasattr(s, "model_dump"):
            d = s.model_dump()
            steps.append({"name": d.get("name"), "args": d.get("args", {}) or {}})
        elif hasattr(s, "name"):
            steps.append({"name": s.name, "args": dict(getattr(s, "args", {}) or {})})
    return steps


def _compute_field(
    join_as: str,
    field_name: str,
    field: Any,
    matches: list[dict[str, Any]],
    cardinality: str,
    *,
    context_tz: str | None,
) -> Any:
    """Compute one join field value from the matched records."""
    what = f"{join_as}.{field_name}"
    path = getattr(field, "path", None)
    paths = getattr(field, "paths", None)
    aggregate = getattr(field, "aggregate", None)
    steps = _steps_for(field)

    def _run(value: Any) -> Any:
        norm = [{"name": s["name"], "args": s["args"] if isinstance(s.get("args"), dict) else {}} for s in steps]
        return apply_transform_pipeline(value, norm, context_tz=context_tz, field_name=field_name)

    if cardinality == "one":
        matched = matches[0] if matches else None
        if paths is not None:
            if matched is None:
                return _run(None)
            # All-null stays None (honest missing); present values combine.
            vals = [v for v in (_extract_one(matched, p) for p in paths) if v is not None]
            return _run(vals if vals else None)
        assert path is not None  # models require path when paths is absent
        raw = _extract_one(matched, path) if matched is not None else None
        return _run(raw)

    # cardinality many
    if aggregate == "count":
        return _run(len(matches))
    assert path is not None and aggregate is not None  # models require both
    vals = [_extract_one(m, path) for m in matches]
    return _run(_aggregate(aggregate, vals, what=what))


def _static_tz(source_spec: Any | None) -> str | None:
    """Static timezone from the spec source, if declared."""
    if source_spec is None:
        return None
    tz_src = getattr(source_spec, "timezone_source", None)
    if tz_src is None and isinstance(source_spec, dict):
        tz_src = source_spec.get("timezone_source")
    if tz_src is None:
        return None
    kind = tz_src.get("kind") if isinstance(tz_src, dict) else getattr(tz_src, "kind", None)
    if kind != "static":
        return None
    val = tz_src.get("value") if isinstance(tz_src, dict) else getattr(tz_src, "value", None)
    return str(val) if val else None


def resolve_joins_for_entity(
    entity_spec: Any,
    base_records: list[dict[str, Any]],
    records_by_op: dict[str, list[dict[str, Any]]],
    *,
    context_tz: str | None = None,
    known_ops: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Enrich *base_records* with every join on *entity_spec*.

    Returns new record dicts (inputs untouched); each carries
    ``_joined.<as>.`` scalars. Joins whose operation has no data
    attach ``None`` fields. A ``from_operation`` outside *known_ops*
    raises JoinError (spec validation normally rejects it first); a
    known operation with no fetched data (e.g. the single-operation
    canary probe) contributes no matches.
    """
    joins = getattr(entity_spec, "joins", None) or []
    if not joins:
        return list(base_records)

    out: list[dict[str, Any]] = []
    for rec in base_records:
        enriched = dict(rec)
        ns: dict[str, Any] = dict(enriched.get(JOIN_NAMESPACE, {}) or {})
        for join in joins:
            op_name = join.from_operation
            if op_name not in records_by_op:
                if known_ops is not None and op_name in known_ops:
                    joined: list[dict[str, Any]] = []
                else:
                    raise JoinError(f"join {join.as_}: unknown operation {op_name!r}")
            else:
                joined = records_by_op.get(op_name) or []
            local_key, foreign_key = join.local_key, join.foreign_key
            cardinality, as_name = join.cardinality, join.as_
            fields = join.fields
            local_val = _extract_one(rec, local_key)
            matches = [r for r in joined if _keys_equal(local_val, _extract_one(r, foreign_key))]
            computed: dict[str, Any] = {}
            for fname, fspec in fields.items():
                computed[fname] = _compute_field(as_name, fname, fspec, matches, cardinality, context_tz=context_tz)
            ns[as_name] = computed
        enriched[JOIN_NAMESPACE] = ns
        out.append(enriched)
    return out


def resolve_joins(
    spec: Any,
    results_by_op: dict[str, Any],
    *,
    source_spec: Any | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Enrich every entity's base records; returns entity name → records.

    *results_by_op* maps operation name to an ``OperationResult`` (or a
    plain record list). Operations with no result contribute no matches.
    """
    static_tz = _static_tz(source_spec if source_spec is not None else getattr(spec, "source", None))
    ops = getattr(spec, "operations", {}) or {}
    known_ops = set(ops.keys()) if hasattr(ops, "keys") else set(ops)
    by_op: dict[str, list[dict[str, Any]]] = {}
    for name, r in results_by_op.items():
        if isinstance(r, list):
            by_op[name] = r
        elif r is not None:
            by_op[name] = list(getattr(r, "records", []) or [])
    enriched: dict[str, list[dict[str, Any]]] = {}
    for ent_name, ent_spec in spec.entities.items():
        op_name = ent_spec.from_operation
        res = results_by_op.get(op_name)
        if res is None:
            base: list[dict[str, Any]] = []
        elif isinstance(res, list):
            base = res
        else:
            base = list(getattr(res, "records", []) or [])
        enriched[ent_name] = resolve_joins_for_entity(
            ent_spec, base, by_op, context_tz=static_tz, known_ops=known_ops
        )
    return enriched
