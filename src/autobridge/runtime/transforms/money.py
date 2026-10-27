"""money — exact Decimal with scale (P1.W3.4).

Money is Decimal end to end, never float.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from autobridge.runtime.transforms.base import TransformError

_MONEY_CHARS_RE = re.compile(r"[^0-9.\-]")


def transform_money(
    value: Any,
    args: dict[str, Any],
    *,
    context_tz: str | None = None,  # noqa: ARG001
) -> Any:
    """Convert value to Decimal quantized to scale.

    - None → None (nullable)
    - Decimal → quantized
    - int (not bool) → interpreted as units then quantized (e.g. 5 → 5.00).
      Callers with cents-as-int should use string.
    - str → strip currency symbols, commas, parse Decimal
    - float → rejected (money is Decimal end to end, never float; a float
      has already lost precision upstream, so it is never laundered via str).
      Vendor JSON must be parsed with parse_float=Decimal.
    - Returns Decimal quantized to scale (default 2).
    - On invalid, raises TransformError (not Blocked).
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise TransformError(f"money: bool not allowed: {value!r}")

    scale = args.get("scale", 2)
    if not isinstance(scale, int) or isinstance(scale, bool) or not 0 <= scale <= 6:
        raise TransformError(f"money: scale must be int 0..6, got {scale!r}")

    dec: Decimal
    if isinstance(value, Decimal):
        dec = value
    elif isinstance(value, int):
        dec = Decimal(value)
    elif isinstance(value, float):
        # Money is Decimal end to end, never float: a float has already lost
        # precision upstream, so it is rejected rather than laundered via str().
        # Vendor JSON responses must be parsed with parse_float=Decimal.
        field = args.get("field") or "unknown"
        raise TransformError(
            f"money: float input rejected for field {field!r}: {value!r} "
            "(money is Decimal, never float)"
        )
    elif isinstance(value, str):
        s = value.strip()
        # Remove currency symbols, commas, spaces
        # Keep digits, dot, minus
        # Handle empty
        if not s:
            raise TransformError("money: empty string")
        s = s.replace(",", "")
        s = _MONEY_CHARS_RE.sub("", s)
        if not s or s in (".", "-", "-."):
            raise TransformError(f"money: invalid string {value!r}")
        try:
            dec = Decimal(s)
        except InvalidOperation as e:
            raise TransformError(f"money: invalid decimal {value!r}: {e}") from e
    else:
        raise TransformError(f"money: unsupported type {type(value).__name__}: {value!r}")

    if not dec.is_finite():
        raise TransformError(f"money: non-finite {value!r}")

    quant_str = "1." + "0" * scale if scale > 0 else "1"
    quant = Decimal("1") if scale == 0 else Decimal(quant_str)  # noqa: SIM108
    # Quantize with half up
    try:
        quantized = dec.quantize(quant, rounding=ROUND_HALF_UP)
    except InvalidOperation as e:
        raise TransformError(f"money: quantize failed {value!r} scale {scale}: {e}") from e
    return quantized
