"""page_to_cursor rule — pagination strategy flip page -> cursor.

Mirrors :mod:`offset_to_cursor` for page-paginated vendors (AtlasPMS after
REQ-019: ``pageNumber``/``pageSize`` baseline). A drift to cursor pagination
is repaired by flipping the spec strategy to ``cursor`` with the cursor
param, next path and the ``repeated_cursor`` guard.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from autobridge.repair.rules.wire import wire_cursor_for_strategy

if TYPE_CHECKING:
    from autobridge.repair.context import RepairContext

RULE_ID = "page_to_cursor"
CONFIDENCE = 0.94
VERIFICATION_LEVEL = "visible"
PRODUCES_PATCH = True


def _operations_with_page(spec: dict[str, Any]) -> list[str]:
    ops: list[str] = []
    for op_name, op_spec in spec.get("operations", {}).items():
        if not isinstance(op_spec, dict):
            continue
        pag = op_spec.get("pagination")
        if isinstance(pag, dict) and pag.get("strategy") == "page":
            ops.append(op_name)
    return ops


def _delta_shows_page_to_cursor(delta: Any | None) -> bool:
    if delta is None:
        return False
    details: dict[str, Any] | None = None
    if hasattr(delta, "pagination_details"):
        details = delta.pagination_details
    elif isinstance(delta, dict):
        details = delta.get("pagination_details")
        if details is None and any(isinstance(v, dict) and "previous" in v for v in delta.values()):
            details = delta  # treat as pagination_details directly
    if not details:
        return False
    for _, info in details.items():
        if not isinstance(info, dict):
            continue
        prev = info.get("previous") if isinstance(info.get("previous"), dict) else None
        cur = info.get("current") if isinstance(info.get("current"), dict) else None
        # Check strategy change
        if prev and cur and prev.get("strategy") == "page" and cur.get("strategy") == "cursor":
            return True
    return False


def precondition(ctx: RepairContext) -> bool:
    """True when pagination should flip from page to cursor."""
    if ctx.drift_class != "PAGINATION_CHANGE":
        return False
    # Spec must currently be page for at least one op
    if not _operations_with_page(ctx.spec):
        return False
    # Wire evidence (REQ-027): the detector saw a cursor leaf on the raw
    # envelope for a page-strategy op. The fingerprint delta never shows a
    # strategy flip (strategies come from the spec), so this is the signal
    # the real sync path provides.
    wire = wire_cursor_for_strategy(ctx.signals, {"page"})
    if wire:
        return True
    # If delta is present, require it to show page->cursor
    if ctx.fingerprint_delta is not None:  # noqa: SIM102
        if not _delta_shows_page_to_cursor(ctx.fingerprint_delta):  # noqa: SIM102
            # Also allow explicit extra flag
            if ctx.extra.get("target_strategy") != "cursor" and ctx.extra.get("pagination_mode") != "cursor":
                return False
    # If explicit extra says cursor, allow; otherwise allow based on spec + class
    return True


def generate_patch(ctx: RepairContext) -> list[dict[str, Any]]:
    ops = _operations_with_page(ctx.spec)
    # Prefer the wire-observed cursor leaf over the default names: the patch
    # then points at the field the vendor actually serves. A strategy flip
    # is vendor-level, so every page op flips (evidence on one op plus the
    # same spec strategy on the others); the verifier gate confirms.
    wire = wire_cursor_for_strategy(ctx.signals, {"page"})
    # If specific operations are named in extra, filter to those
    if ctx.extra.get("operations"):
        wanted = ctx.extra["operations"]
        if isinstance(wanted, list):
            ops = [o for o in ops if o in wanted]
    patches: list[dict[str, Any]] = []
    for op_name in ops:
        # next_path follows the wire-observed response leaf; cursor_param
        # stays the request param the vendor reads (response leaf and
        # request param are different names).
        next_path = f"$.{wire[op_name]}" if op_name in wire else "$.next_cursor"
        base = f"/operations/{op_name}/pagination"
        patches.append({"op": "replace", "path": f"{base}/strategy", "value": "cursor"})
        patches.append({"op": "replace", "path": f"{base}/cursor_param", "value": "cursor"})
        patches.append({"op": "replace", "path": f"{base}/next_path", "value": next_path})
        patches.append({"op": "replace", "path": f"{base}/page_param", "value": None})
        # Ensure stop_when includes repeated_cursor guard ( Harbor-style )
        patches.append({"op": "replace", "path": f"{base}/stop_when", "value": ["null_cursor", "repeated_cursor"]})
    return patches
