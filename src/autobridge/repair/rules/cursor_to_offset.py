"""cursor_to_offset rule — pagination strategy flip cursor -> offset."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autobridge.repair.context import RepairContext

RULE_ID = "cursor_to_offset"
CONFIDENCE = 0.94
VERIFICATION_LEVEL = "visible"
PRODUCES_PATCH = True


def _operations_with_cursor(spec: dict[str, Any]) -> list[str]:
    ops: list[str] = []
    for op_name, op_spec in spec.get("operations", {}).items():
        if not isinstance(op_spec, dict):
            continue
        pag = op_spec.get("pagination")
        if isinstance(pag, dict) and pag.get("strategy") == "cursor":
            ops.append(op_name)
    return ops


def _delta_shows_cursor_to_offset(delta: Any | None) -> bool:
    if delta is None:
        return False
    details: dict[str, Any] | None = None
    if hasattr(delta, "pagination_details"):
        details = delta.pagination_details
    elif isinstance(delta, dict):
        details = delta.get("pagination_details")
        if details is None and any(isinstance(v, dict) and "previous" in v for v in delta.values()):
            details = delta
    if not details:
        return False
    for _, info in details.items():
        if not isinstance(info, dict):
            continue
        prev = info.get("previous") if isinstance(info.get("previous"), dict) else None
        cur = info.get("current") if isinstance(info.get("current"), dict) else None
        if prev and cur and prev.get("strategy") == "cursor" and cur.get("strategy") == "offset":
            return True
    return False


def precondition(ctx: RepairContext) -> bool:
    if ctx.drift_class != "PAGINATION_CHANGE":
        return False
    if not _operations_with_cursor(ctx.spec):
        return False
    if ctx.fingerprint_delta is not None:  # noqa: SIM102
        if not _delta_shows_cursor_to_offset(ctx.fingerprint_delta):  # noqa: SIM102
            if ctx.extra.get("target_strategy") != "offset" and ctx.extra.get("pagination_mode") != "offset":
                return False
    return True


def generate_patch(ctx: RepairContext) -> list[dict[str, Any]]:
    ops = _operations_with_cursor(ctx.spec)
    if ctx.extra.get("operations"):
        wanted = ctx.extra["operations"]
        if isinstance(wanted, list):
            ops = [o for o in ops if o in wanted]
    patches: list[dict[str, Any]] = []
    for op_name in ops:
        base = f"/operations/{op_name}/pagination"
        patches.append({"op": "replace", "path": f"{base}/strategy", "value": "offset"})
        patches.append({"op": "replace", "path": f"{base}/offset_param", "value": "offset"})
        patches.append({"op": "replace", "path": f"{base}/limit_param", "value": "limit"})
        patches.append({"op": "replace", "path": f"{base}/next_path", "value": "$.next_offset"})
        patches.append({"op": "replace", "path": f"{base}/cursor_param", "value": None})
        patches.append({"op": "replace", "path": f"{base}/stop_when", "value": ["null_cursor", "empty_page"]})
    return patches
