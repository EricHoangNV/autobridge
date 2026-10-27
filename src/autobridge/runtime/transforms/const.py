"""const — constant value (P1.W3.4)."""

from __future__ import annotations

from typing import Any


def transform_const(
    value: Any,  # noqa: ARG001
    args: dict[str, Any],
    *,
    context_tz: str | None = None,  # noqa: ARG001
) -> Any:
    """Return args.value regardless of input."""
    if "value" not in args:
        raise ValueError("const requires 'value' arg")
    return args["value"]
