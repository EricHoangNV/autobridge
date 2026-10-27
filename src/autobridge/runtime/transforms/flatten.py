"""flatten — flatten one level of nesting (P1.W3.4)."""

from __future__ import annotations

from typing import Any


def transform_flatten(
    value: Any,
    args: dict[str, Any],  # noqa: ARG001
    *,
    context_tz: str | None = None,  # noqa: ARG001
) -> Any:
    """Flatten one level.

    - None → None
    - list → flatten: for each element if list extend else append
    - scalar → [scalar] (wrapped) per transform contract
    - dict → returned as-is (not flattened)
    """
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        out: list[Any] = []
        for item in value:
            if isinstance(item, list):
                out.extend(item)
            else:
                out.append(item)
        return out
    return [value]
