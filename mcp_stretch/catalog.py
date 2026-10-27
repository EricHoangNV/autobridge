"""P2.S4.1 — declarative HotelTools V1/V2 catalogs, snapshot + diff.

A catalog is data: a list of tool descriptors (name, params, money fields,
date fields). ``snapshot()`` renders a canonical JSON string; ``diff()``
compares two snapshots and reports added / removed / renamed-candidate /
param-changed tools. The gateway's pinned mapping (gateway.py) is validated
against this diff — mappings are data reviewed by replay tests, never
generated code (Directives §1).
"""

from __future__ import annotations

import json
from typing import Any

# ---------------------------------------------------------------------------
# Declarative tool catalogs (frozen fixtures for the V1 → V2 migration)
# ---------------------------------------------------------------------------

V1_VERSION = "HotelToolsV1"
V2_VERSION = "HotelToolsV2"

# param name → kind, kind in {"string", "money", "business_date"}
V1_CATALOG: list[dict[str, Any]] = [
    {
        "name": "search_hotels",
        "params": {"query": "string", "check_in": "business_date", "check_out": "business_date"},
        "returns": "list[hotel]",
    },
    {
        "name": "get_reservation",
        "params": {"reservation_id": "string"},
        "returns": "reservation",
    },
    {
        "name": "add_folio_charge",
        "params": {
            "reservation_id": "string",
            "amount": "money",
            "description": "string",
            "business_date": "business_date",
        },
        "returns": "folio_charge",
    },
]

V2_CATALOG: list[dict[str, Any]] = [
    {
        "name": "search_properties",
        "params": {"query": "string", "arrival": "business_date", "departure": "business_date"},
        "returns": "list[property]",
    },
    {
        "name": "get_booking",
        "params": {"booking_id": "string"},
        "returns": "booking",
    },
    {
        "name": "post_folio_charge",
        "params": {
            "booking_id": "string",
            "amount": "money",
            "memo": "string",
            "business_date": "business_date",
        },
        "returns": "folio_charge",
    },
    {
        "name": "list_amenities",
        "params": {"property_id": "string"},
        "returns": "list[amenity]",
    },
]


def snapshot(catalog: list[dict[str, Any]], version: str) -> str:
    """Render a canonical (sorted-keys, indented) JSON snapshot of a catalog."""
    payload = {"version": version, "tools": sorted(catalog, key=lambda t: t["name"])}
    return json.dumps(payload, sort_keys=True, indent=2)


def V1_SNAPSHOT() -> str:
    return snapshot(V1_CATALOG, V1_VERSION)


def V2_SNAPSHOT() -> str:
    return snapshot(V2_CATALOG, V2_VERSION)


def tool_map(catalog: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {t["name"]: t for t in catalog}


def diff(v1: list[dict[str, Any]], v2: list[dict[str, Any]]) -> dict[str, Any]:
    """Diff two catalogs.

    Returns ``{"added", "removed", "param_changed", "rename_candidates"}``.
    A rename candidate pairs a removed V1 tool with an added V2 tool whose
    param-kind multiset matches (same kinds, possibly renamed keys) — the
    gateway may only pin mappings drawn from these candidates; anything
    else is unsafe and rejected by replay tests.
    """
    m1, m2 = tool_map(v1), tool_map(v2)
    names1, names2 = set(m1), set(m2)
    added = sorted(names2 - names1)
    removed = sorted(names1 - names2)

    param_changed: list[dict[str, Any]] = []
    for name in sorted(names1 & names2):
        if m1[name]["params"] != m2[name]["params"]:
            param_changed.append(
                {"tool": name, "v1_params": m1[name]["params"], "v2_params": m2[name]["params"]}
            )

    def kinds(params: dict[str, str]) -> list[str]:
        return sorted(params.values())

    unmatched_v2 = list(added)
    rename_candidates: list[dict[str, Any]] = []
    for old in removed:
        match = next(
            (new for new in unmatched_v2 if kinds(m2[new]["params"]) == kinds(m1[old]["params"])),
            None,
        )
        if match is not None:
            unmatched_v2.remove(match)
            # Pair same-name keys first, then the rest in declaration order
            # (dicts preserve insertion order). Only renamed pairs are listed.
            p1 = m1[old]["params"]
            p2 = m2[match]["params"]
            rest1 = [k for k in p1 if k not in p2]
            rest2 = [k for k in p2 if k not in p1]
            param_map = {k1: k2 for k1, k2 in zip(rest1, rest2, strict=True) if k1 != k2}
            rename_candidates.append({"from": old, "to": match, "param_map": param_map})
    return {
        "added": sorted(unmatched_v2),
        "removed": sorted(n for n in removed if all(c["from"] != n for c in rename_candidates)),
        "param_changed": param_changed,
        "rename_candidates": rename_candidates,
    }
