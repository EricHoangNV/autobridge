"""cursor_next_path_shift rule — response cursor leaf renamed.

A cursor-family pagination (``cursor`` or ``body_cursor``) keeps its
strategy and request shape, but the vendor renames the response leaf that
carries the next cursor (e.g. ``Cursor`` -> ``NextCursor``). The spec's
``next_path`` then reads null and the fetch
stops after the first page. The repair points ``next_path`` at the new leaf.

The new leaf name cannot be derived deterministically, so the caller
supplies it via ``extra["new_next_path"]`` (a JSONPath string such as
``"$.NextCursor"``), mirroring how ``rename_field`` takes ``new_path``.
An optional ``extra["operations"]`` list restricts the patch to named ops.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autobridge.repair.context import RepairContext

RULE_ID = "cursor_next_path_shift"
CONFIDENCE = 0.94
VERIFICATION_LEVEL = "visible"
PRODUCES_PATCH = True

_CURSOR_FAMILY = frozenset({"cursor", "body_cursor"})


def _operations_with_cursor_family(spec: dict[str, Any]) -> list[str]:
    ops: list[str] = []
    for op_name, op_spec in spec.get("operations", {}).items():
        if not isinstance(op_spec, dict):
            continue
        pag = op_spec.get("pagination")
        if isinstance(pag, dict) and pag.get("strategy") in _CURSOR_FAMILY:
            ops.append(op_name)
    return ops


def _delta_shows_next_path_shift(delta: Any | None) -> bool:
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
        if prev and cur and prev.get("next_path") != cur.get("next_path"):
            return True
    return False


def _new_next_path(ctx: RepairContext) -> str | None:
    raw = ctx.extra.get("new_next_path")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    nested = ctx.extra.get("next_path_shift")
    if isinstance(nested, dict):
        cand = nested.get("new_next_path") or nested.get("new")
        if isinstance(cand, str) and cand.strip():
            return cand.strip()
    return None


def precondition(ctx: RepairContext) -> bool:
    """True when a cursor leaf rename can be repaired via next_path."""
    if ctx.drift_class != "PAGINATION_CHANGE":
        return False
    if not _operations_with_cursor_family(ctx.spec):
        return False
    if _new_next_path(ctx) is None:
        return False
    # If delta is present, require it to show a next_path shift, unless the
    # caller explicitly names the new leaf (probes may name it directly).
    if ctx.fingerprint_delta is not None:  # noqa: SIM102
        if not _delta_shows_next_path_shift(ctx.fingerprint_delta):  # noqa: SIM102
            if _new_next_path(ctx) is None:
                return False
    return True


def generate_patch(ctx: RepairContext) -> list[dict[str, Any]]:
    ops = _operations_with_cursor_family(ctx.spec)
    if ctx.extra.get("operations"):
        wanted = ctx.extra["operations"]
        if isinstance(wanted, list):
            ops = [o for o in ops if o in wanted]
    new_path = _new_next_path(ctx)
    if new_path is None:
        return []
    return [{"op": "replace", "path": f"/operations/{op_name}/pagination/next_path", "value": new_path} for op_name in ops]
