"""Closed transform registry — 9 registered transforms (P1.W3.4, plan §2.4).

The set is closed: adding a name is a plan change, not a task decision.
Each transform is a pure function (value, args, context_tz) -> value.
Pipeline helper ``apply_transform_pipeline`` runs the closed set sequentially
and propagates ``TransformBlocked`` for unknown enum values.
"""

from __future__ import annotations

from typing import Any

from autobridge.runtime.transforms.base import TransformBlocked, TransformError
from autobridge.runtime.transforms.business_date import transform_business_date
from autobridge.runtime.transforms.coalesce import transform_coalesce
from autobridge.runtime.transforms.concat import transform_concat
from autobridge.runtime.transforms.const import transform_const
from autobridge.runtime.transforms.date_local import transform_date_local
from autobridge.runtime.transforms.datetime_utc import transform_datetime_utc
from autobridge.runtime.transforms.enum_map import transform_enum_map
from autobridge.runtime.transforms.flatten import transform_flatten
from autobridge.runtime.transforms.money import transform_money

# Closed set — must match spec.models.TRANSFORMS
TRANSFORMS: tuple[str, ...] = (
    "date_local",
    "datetime_utc",
    "money",
    "enum_map",
    "flatten",
    "coalesce",
    "const",
    "concat",
    "business_date",
)

# Registry mapping name → function
REGISTRY: dict[str, Any] = {
    "date_local": transform_date_local,
    "datetime_utc": transform_datetime_utc,
    "money": transform_money,
    "enum_map": transform_enum_map,
    "flatten": transform_flatten,
    "coalesce": transform_coalesce,
    "const": transform_const,
    "concat": transform_concat,
    "business_date": transform_business_date,
}


def apply_transform(
    value: Any,
    name: str,
    args: dict[str, Any],
    *,
    context_tz: str | None = None,
    field_name: str | None = None,
) -> Any:
    """Apply single transform *name* with *args* to *value*."""
    fn = REGISTRY.get(name)
    if fn is None:
        raise TransformError(f"unknown transform {name!r} — closed set {TRANSFORMS}")
    # Propagate field name into enum_map/money args for error reporting
    if name in ("enum_map", "money") and field_name and "field" not in args:
        args = {**args, "field": field_name}
    return fn(value, args, context_tz=context_tz)


def apply_transform_pipeline(
    value: Any,
    steps: list[dict[str, Any]],
    *,
    context_tz: str | None = None,
    field_name: str | None = None,
) -> Any:
    """Apply a list of transform steps sequentially.

    Each step is ``{"name": str, "args": dict}``.
    Unknown transform → TransformError.
    Unknown enum inside enum_map → TransformBlocked (caller maps to Blocked outcome).
    """
    cur = value
    for step in steps:
        name = step.get("name")
        args = step.get("args", {})
        if not isinstance(args, dict):
            args = {}
        if not isinstance(name, str):
            raise TransformError(f"transform step missing name: {step!r}")
        cur = apply_transform(cur, name, args, context_tz=context_tz, field_name=field_name)
        # Short-circuit if value became None and next transform is not coalesce/const
        # but coalesce/const are allowed to revive None, so continue
    return cur


__all__ = [
    "TRANSFORMS",
    "REGISTRY",
    "TransformBlocked",
    "TransformError",
    "apply_transform",
    "apply_transform_pipeline",
    "transform_business_date",
    "transform_coalesce",
    "transform_concat",
    "transform_const",
    "transform_date_local",
    "transform_datetime_utc",
    "transform_enum_map",
    "transform_flatten",
    "transform_money",
]
