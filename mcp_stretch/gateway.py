"""P2.S4.1 — compatibility gateway: pinned V1→V2 mapping + invoke outcomes.

The mapping is declarative data (``PINNED_MAPPING``), reviewed by replay
tests — the gateway never generates code and never calls a model
(frontier_tokens == 0 by construction; Directives §§1, 3).

Outcomes (Directives §4): every ``invoke`` ends ``Complete | Degraded |
Blocked`` with the unmet criterion named. Unknown tool → ``Blocked``
(never guessed); surviving ambiguity (unknown extra params) → ``Degraded``
with open questions.

Money is ``Decimal`` end to end, serialized as exact 2dp strings
(Directives §4). Dates are property-local business dates (``YYYY-MM-DD``,
validated with ``date.fromisoformat``, never converted across timezones).
The mock carries no credentials: ``credential_ref``-style secret passing
is vacuous here and any ``currency`` other than the store's USD is
``Blocked`` (never silently converted).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
from typing import Any

from mcp_stretch.catalog import V1_CATALOG, V2_CATALOG, tool_map

V1_TOOLS = tool_map(V1_CATALOG)
V2_TOOLS = tool_map(V2_CATALOG)

STORE_CURRENCY = "USD"

# ---------------------------------------------------------------------------
# Pinned mapping: V1 tool → V2 target + V1-param → V2-param. Declarative data.
# ---------------------------------------------------------------------------

PINNED_MAPPING: dict[str, dict[str, Any]] = {
    "search_hotels": {
        "target": "search_properties",
        "params": {"query": "query", "check_in": "arrival", "check_out": "departure"},
    },
    "get_reservation": {
        "target": "get_booking",
        "params": {"reservation_id": "booking_id"},
    },
    "add_folio_charge": {
        "target": "post_folio_charge",
        "params": {
            "reservation_id": "booking_id",
            "amount": "amount",
            "description": "memo",
            "business_date": "business_date",
        },
    },
}

MONEY_KIND = "money"


def validate_mapping(mapping: dict[str, dict[str, Any]]) -> tuple[bool, str]:
    """Check a candidate V1→V2 mapping for safety.

    Unsafe (rejected, never applied): unknown V1/V2 tool names, a V1 money
    param dropped or routed to a non-money V2 param, a mapping that invents
    a currency conversion, or a business-date param dropped. Returns
    ``(ok, reason)``; ``reason`` names the violated criterion.
    """
    for v1_name, entry in mapping.items():
        v1_tool = V1_TOOLS.get(v1_name)
        if v1_tool is None:
            return False, f"unknown_v1_tool:{v1_name}"
        target = entry.get("target")
        v2_tool = V2_TOOLS.get(target) if isinstance(target, str) else None
        if v2_tool is None:
            return False, f"unknown_v2_tool:{target}"
        pmap = entry.get("params")
        if not isinstance(pmap, dict):
            return False, f"missing_param_map:{v1_name}"
        for pname, kind in v1_tool["params"].items():
            if pname not in pmap:
                if kind == MONEY_KIND:
                    return False, f"dropped_money_param:{v1_name}.{pname}"
                if kind == "business_date":
                    return False, f"dropped_business_date:{v1_name}.{pname}"
                return False, f"dropped_param:{v1_name}.{pname}"
            mapped = pmap[pname]
            if kind == MONEY_KIND and v2_tool["params"].get(mapped) != MONEY_KIND:
                return False, f"money_routed_to_non_money:{v1_name}.{pname}->{mapped}"
        for pname, mapped in pmap.items():
            if pname not in v1_tool["params"]:
                return False, f"unknown_v1_param:{v1_name}.{pname}"
            if mapped not in v2_tool["params"]:
                return False, f"unknown_v2_param:{target}.{mapped}"
    return True, "mapping_pinned_and_safe"


# ---------------------------------------------------------------------------
# Deterministic toy store (same backend behind V1, V2 and the gateway)
# ---------------------------------------------------------------------------

PROPERTIES: list[dict[str, Any]] = [
    {"property_id": "PROP-001", "name": "Grand Plaza", "timezone": "America/New_York"},
    {"property_id": "PROP-002", "name": "Harbor Inn", "timezone": "America/Los_Angeles"},
]

BOOKINGS: list[dict[str, Any]] = [
    {
        "booking_id": "BK-1001",
        "property_id": "PROP-001",
        "guest_name": "Ada Lovelace",
        "arrival": "2026-11-02",
        "departure": "2026-11-05",
        "total_amount": "450.00",
        "currency": STORE_CURRENCY,
    },
    {
        "booking_id": "BK-1002",
        "property_id": "PROP-002",
        "guest_name": "Grace Hopper",
        "arrival": "2026-11-03",
        "departure": "2026-11-04",
        "total_amount": "199.99",
        "currency": STORE_CURRENCY,
    },
]

AMENITIES: dict[str, list[str]] = {
    "PROP-001": ["wifi", "pool", "gym"],
    "PROP-002": ["wifi", "breakfast"],
}


def parse_money(raw: Any) -> Decimal:
    """Parse a money value to Decimal quantized to exactly 2dp."""
    try:
        dec = Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"invalid_money:{raw!r}") from exc
    return dec.quantize(Decimal("0.00"))


def money_str(dec: Decimal) -> str:
    return format(dec.quantize(Decimal("0.00")), "f")


def parse_business_date(raw: Any) -> str:
    """Validate a property-local business date; return the canonical string."""
    try:
        parsed = dt.date.fromisoformat(str(raw))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"invalid_business_date:{raw!r}") from exc
    return parsed.isoformat()


class Gateway:
    """Compatibility gateway: V1 tool calls executed against the V2 backend."""

    def __init__(self, mapping: dict[str, dict[str, Any]] | None = None) -> None:
        self.mapping = mapping if mapping is not None else PINNED_MAPPING
        ok, reason = validate_mapping(self.mapping)
        if not ok:
            raise ValueError(f"unsafe_gateway_mapping:{reason}")
        self._charges: list[dict[str, Any]] = []
        self._charge_seq = 0

    # -- test isolation ----------------------------------------------------
    def reset(self) -> None:
        self._charges = []
        self._charge_seq = 0

    # -- V2 backend implementations (shared toy store) ---------------------
    def _v2_search_properties(self, params: dict[str, Any]) -> dict[str, Any]:
        query = str(params["query"]).lower()
        arrival = parse_business_date(params["arrival"])
        departure = parse_business_date(params["departure"])
        if arrival >= departure:
            return {"outcome": "Blocked", "unmet_criterion": "arrival_before_departure"}
        hits = [p for p in PROPERTIES if query in p["name"].lower()]
        return {"outcome": "Complete", "properties": hits}

    def _v2_get_booking(self, params: dict[str, Any]) -> dict[str, Any]:
        booking = next((b for b in BOOKINGS if b["booking_id"] == params["booking_id"]), None)
        if booking is None:
            return {"outcome": "Blocked", "unmet_criterion": "booking_not_found"}
        return {"outcome": "Complete", "booking": dict(booking)}

    def _v2_post_folio_charge(self, params: dict[str, Any]) -> dict[str, Any]:
        booking = next((b for b in BOOKINGS if b["booking_id"] == params["booking_id"]), None)
        if booking is None:
            return {"outcome": "Blocked", "unmet_criterion": "booking_not_found"}
        try:
            amount = parse_money(params["amount"])
        except ValueError as exc:
            return {"outcome": "Blocked", "unmet_criterion": str(exc)}
        if amount <= Decimal("0.00"):
            return {"outcome": "Blocked", "unmet_criterion": "amount_must_be_positive"}
        try:
            business_date = parse_business_date(params["business_date"])
        except ValueError as exc:
            return {"outcome": "Blocked", "unmet_criterion": str(exc)}
        self._charge_seq += 1
        charge = {
            "charge_id": f"CHG-{self._charge_seq:04d}",
            "booking_id": booking["booking_id"],
            "property_id": booking["property_id"],
            "amount": money_str(amount),
            "currency": STORE_CURRENCY,
            "memo": str(params["memo"]),
            "business_date": business_date,
        }
        self._charges.append(charge)
        return {"outcome": "Complete", "charge": charge}

    _V2_DISPATCH = {
        "search_properties": _v2_search_properties,
        "get_booking": _v2_get_booking,
        "post_folio_charge": _v2_post_folio_charge,
    }

    # -- V1 entry point ----------------------------------------------------
    def invoke(self, tool: str, params: dict[str, Any]) -> dict[str, Any]:
        """Invoke a V1 tool through the pinned mapping.

        Always returns an outcome envelope; unknown tools are ``Blocked``,
        never guessed (Directives §4).
        """
        entry = self.mapping.get(tool)
        if entry is None:
            return {
                "outcome": "Blocked",
                "tool": tool,
                "unmet_criterion": f"unknown_tool:{tool}",
            }
        v1_tool = V1_TOOLS[tool]
        if "currency" in params and params["currency"] != STORE_CURRENCY:
            return {
                "outcome": "Blocked",
                "tool": tool,
                "unmet_criterion": f"unsupported_currency:{params['currency']}",
            }
        missing = [p for p in v1_tool["params"] if p not in params]
        if missing:
            return {
                "outcome": "Blocked",
                "tool": tool,
                "unmet_criterion": f"missing_param:{missing[0]}",
            }
        extra = sorted(p for p in params if p not in v1_tool["params"] and p != "currency")
        mapped = {entry["params"][k]: params[k] for k in v1_tool["params"]}
        target = entry["target"]
        impl = Gateway._V2_DISPATCH.get(target)
        if impl is None:  # pragma: no cover - validated at construction
            return {"outcome": "Blocked", "tool": tool, "unmet_criterion": f"no_impl:{target}"}
        result = impl(self, mapped)
        # Translate V2 outcome back to V1 shape on success.
        if result.get("outcome") == "Complete" and tool == "get_reservation":
            b = result["booking"]
            result = {
                "outcome": "Complete",
                "reservation": {
                    "reservation_id": b["booking_id"],
                    "hotel_id": b["property_id"],
                    "guest_name": b["guest_name"],
                    "check_in": b["arrival"],
                    "check_out": b["departure"],
                    "total_amount": b["total_amount"],
                    "currency": b["currency"],
                },
            }
        elif result.get("outcome") == "Complete" and tool == "search_hotels":
            result = {
                "outcome": "Complete",
                "hotels": [
                    {"hotel_id": p["property_id"], "name": p["name"]}
                    for p in result["properties"]
                ],
            }
        elif result.get("outcome") == "Complete" and tool == "add_folio_charge":
            c = result["charge"]
            result = {
                "outcome": "Complete",
                "charge": {
                    "charge_id": c["charge_id"],
                    "reservation_id": c["booking_id"],
                    "amount": c["amount"],
                    "currency": c["currency"],
                    "description": c["memo"],
                    "business_date": c["business_date"],
                },
            }
        envelope = {"tool": tool, **result}
        if extra:
            # Surviving ambiguity degrades, never silently passes (Directives §4).
            envelope["outcome"] = "Degraded" if result.get("outcome") == "Complete" else result.get("outcome", "Blocked")
            envelope["open_questions"] = [f"unmapped_param_ignored:{p}" for p in extra]
        return envelope
