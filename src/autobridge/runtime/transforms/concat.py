"""concat — join values (P1.W3.4)."""

from __future__ import annotations

from typing import Any


def transform_concat(
    value: Any,
    args: dict[str, Any],
    *,
    context_tz: str | None = None,  # noqa: ARG001
) -> Any:
    """Concatenate.

    - If value is list, join with separator (default "").
    - If value is scalar string, optionally append args.suffix/prefix.
    - Args: separator, prefix, suffix, values (alternative list)
    """
    separator = args.get("separator", "")
    if not isinstance(separator, str):
        separator = str(separator)

    if value is None:
        # Check args.values
        if "values" in args and isinstance(args["values"], list):
            parts = [str(v) for v in args["values"] if v is not None]
            return separator.join(parts)
        return None

    if isinstance(value, list):
        parts = [str(v) for v in value if v is not None]
        # If args.values also present, extend
        if "values" in args and isinstance(args["values"], list):
            parts.extend(str(v) for v in args["values"] if v is not None)
        return separator.join(parts)

    # Scalar string
    s = str(value)
    prefix = args.get("prefix", "")
    suffix = args.get("suffix", "")
    # args.values concatenation
    if "values" in args and isinstance(args["values"], list):
        extra = separator.join(str(v) for v in args["values"] if v is not None)
        if extra:
            s = separator.join([s, extra]) if s else extra

    if prefix:
        s = str(prefix) + s
    if suffix:
        s = s + str(suffix)
    return s
