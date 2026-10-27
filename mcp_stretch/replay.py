"""P2.S4.1 — pinned-consumer workflow replay + plain-rediscovery comparison.

A pinned consumer is a recorded sequence of V1 tool calls with assertions on
the results. ``replay`` executes it against a backend callable and reports a
per-step outcome plus an overall ``Complete | Degraded | Blocked`` verdict
with the unmet criterion named (Directives §4).

``compare`` runs the same workflow twice: once through the compatibility
gateway (pinned mapping) and once through plain tool rediscovery (naive
exact-name lookup in the V2 catalog, i.e. what a consumer that simply
re-discovers tools after the upgrade would do). Passing criteria (plan §5
S4): the pinned consumer keeps working through the gateway while plain
rediscovery fails on the renamed tools.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp_stretch import gateway as gateway_mod
from mcp_stretch.catalog import V2_CATALOG, tool_map

V2_NAMES = set(tool_map(V2_CATALOG))

# The pinned consumer: three V1 calls recorded before the V1→V2 migration.
PINNED_WORKFLOW: list[dict[str, Any]] = [
    {
        "step": 1,
        "tool": "search_hotels",
        "params": {"query": "plaza", "check_in": "2026-11-01", "check_out": "2026-11-06"},
        "assert": {"hotel_present": "PROP-001"},
    },
    {
        "step": 2,
        "tool": "get_reservation",
        "params": {"reservation_id": "BK-1001"},
        "assert": {"total_amount": "450.00", "guest_name": "Ada Lovelace"},
    },
    {
        "step": 3,
        "tool": "add_folio_charge",
        "params": {
            "reservation_id": "BK-1001",
            "amount": "19.99",
            "description": "minibar",
            "business_date": "2026-11-03",
        },
        "assert": {"amount": "19.99", "business_date": "2026-11-03"},
    },
]

Backend = Callable[[str, dict[str, Any]], dict[str, Any]]


def gateway_backend(gw: gateway_mod.Gateway) -> Backend:
    def call(tool: str, params: dict[str, Any]) -> dict[str, Any]:
        return gw.invoke(tool, dict(params))

    return call


def plain_rediscovery_backend(gw: gateway_mod.Gateway) -> Backend:
    """Naive post-upgrade consumer: exact V1 names looked up in V2.

    Renamed tools are not found (never guessed → step Blocked); only tools
    whose name survived verbatim could execute. Here none survived, so the
    replay fails fast at step 1 — the point of the comparison.
    """

    def call(tool: str, params: dict[str, Any]) -> dict[str, Any]:
        if tool not in V2_NAMES:
            return {"outcome": "Blocked", "tool": tool, "unmet_criterion": f"tool_not_found:{tool}"}
        impl = gateway_mod.Gateway._V2_DISPATCH.get(tool)
        if impl is None:
            return {"outcome": "Blocked", "tool": tool, "unmet_criterion": f"no_impl:{tool}"}
        try:
            return {"outcome": "Complete", "tool": tool, **_wrap(impl(gw, dict(params)))}
        except (KeyError, ValueError) as exc:
            return {"outcome": "Blocked", "tool": tool, "unmet_criterion": f"param_mismatch:{exc}"}

    return call


def _wrap(result: dict[str, Any]) -> dict[str, Any]:
    result = dict(result)
    result.pop("outcome", None)
    return {"result": result}


def _check_assertions(step: dict[str, Any], envelope: dict[str, Any]) -> tuple[bool, str]:
    """Verify a step's recorded assertions against the envelope payload."""
    inner = envelope.get("result") or {}
    blob = envelope.get("reservation") or envelope.get("charge") or inner.get("booking") or inner.get("charge") or {}
    hotels = envelope.get("hotels") or inner.get("properties") or []
    for key, expected in step.get("assert", {}).items():
        if key == "hotel_present":
            ids = [h.get("hotel_id") for h in hotels]
            if expected not in ids:
                return False, f"assertion_failed:{key}"
        elif blob.get(key) != expected:
            return False, f"assertion_failed:{key}"
    return True, ""


def replay(workflow: list[dict[str, Any]], backend: Backend) -> dict[str, Any]:
    """Replay a pinned workflow; overall verdict is the worst step outcome."""
    steps: list[dict[str, Any]] = []
    worst = "Complete"
    unmet: str | None = None
    for step in workflow:
        envelope = backend(step["tool"], step["params"])
        outcome = envelope.get("outcome", "Blocked")
        detail: dict[str, Any] = {"step": step["step"], "tool": step["tool"], "outcome": outcome}
        if outcome == "Complete":
            ok, reason = _check_assertions(step, envelope)
            if not ok:
                outcome = "Blocked"
                detail["outcome"] = outcome
                detail["unmet_criterion"] = reason
        if outcome != "Complete":
            detail.setdefault("unmet_criterion", envelope.get("unmet_criterion", "unknown"))
            detail.setdefault("open_questions", envelope.get("open_questions", []))
        steps.append(detail)
        if outcome == "Blocked" or (outcome == "Degraded" and worst == "Complete"):
            worst = outcome
            if unmet is None:
                unmet = detail["unmet_criterion"]
    report: dict[str, Any] = {"outcome": worst, "steps": steps}
    if unmet is not None:
        report["unmet_criterion"] = unmet
    return report


def compare(gw: gateway_mod.Gateway) -> dict[str, Any]:
    """Run the pinned workflow through the gateway and through rediscovery."""
    gw.reset()
    via_gateway = replay(PINNED_WORKFLOW, gateway_backend(gw))
    gw.reset()
    via_rediscovery = replay(PINNED_WORKFLOW, plain_rediscovery_backend(gw))
    return {
        "gateway": via_gateway,
        "rediscovery": via_rediscovery,
        "gateway_keeps_consumer_working": via_gateway["outcome"] == "Complete",
        "rediscovery_breaks_consumer": via_rediscovery["outcome"] == "Blocked",
    }
