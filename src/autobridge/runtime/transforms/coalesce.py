"""coalesce — first non-null value (P1.W3.4)."""

from __future__ import annotations

from typing import Any


def transform_coalesce(
    value: Any,
    args: dict[str, Any],
    *,
    context_tz: str | None = None,  # noqa: ARG001
) -> Any:
    """Return first non-null.

    - If value is not None/empty string, return it.
    - If value is list, return first non-None/non-"" element.
    - Else check args fallbacks: args.default / args.value / args.fallback / args.values
    - If still None, return None.
    """
    # Direct value present
    if value is not None and value != "":
        if isinstance(value, list):
            for item in value:
                if item is not None and item != "":
                    return item
            # All items null, try fallback
        else:
            return value

    # Fallbacks from args
    for key in ("default", "value", "fallback", "else"):
        if key in args and args[key] is not None and args[key] != "":
            return args[key]

    # args.values list
    if "values" in args and isinstance(args["values"], list):
        for item in args["values"]:
            if item is not None and item != "":
                return item

    return value
