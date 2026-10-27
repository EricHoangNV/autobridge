"""P2.S4.1 — MCP Guardian (plan §5 S4).

Passing criteria (plan §5 S4):
  * Pinned consumer keeps working through the gateway (replay Complete).
  * Unsafe mappings are rejected by replay tests (validate 422 + Blocked).
  * Comparison against plain tool rediscovery (gateway Complete,
    rediscovery Blocked on the renamed tools).
  * Own e2e file: ``tests/e2e/test_s4_mcp_gateway.py``.

Fake transports (TestClient over ``mcp_stretch.app``) keep the suite green
without Docker; live tests use the ``e2e_stack`` fixture against the
``mcp-hotel`` Compose service (engine-net, port 8115) and skip when the
stack is down. Trust boundary: the mock lives in ``mcp_stretch/`` (engine
zone); it never imports verifier/ or simulators/, and no engine code
imports it.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

MCP_URL = "http://localhost:8115"

UNSAFE_DROPPED_AMOUNT = {
    "add_folio_charge": {
        "target": "post_folio_charge",
        "params": {
            "reservation_id": "booking_id",
            "description": "memo",
            "business_date": "business_date",
        },
    }
}


def _fake_client() -> TestClient:
    from mcp_stretch.app import app as mcp_app

    client = TestClient(mcp_app)
    client.post("/reset")
    return client


# ---------------------------------------------------------------------------
# Fake: catalog snapshot/diff + pinned gateway replay
# ---------------------------------------------------------------------------


def test_s4_catalog_diff_fake() -> None:
    """The V1→V2 diff reports 3 renames and 1 addition, deterministically."""
    with _fake_client() as client:
        v1 = client.get("/v1/catalog").json()
        v2 = client.get("/v2/catalog").json()
        assert (len(v1["tools"]), len(v2["tools"])) == (3, 4)
        d = client.get("/catalog/diff").json()
        assert d["added"] == ["list_amenities"]
        assert d["removed"] == []
        assert {(c["from"], c["to"]) for c in d["rename_candidates"]} == {
            ("search_hotels", "search_properties"),
            ("get_reservation", "get_booking"),
            ("add_folio_charge", "post_folio_charge"),
        }


def test_s4_gateway_keeps_pinned_consumer_working_fake() -> None:
    """Pinned consumer replay through the gateway ends Complete."""
    with _fake_client() as client:
        r = client.post("/replay/run", json={"mode": "gateway"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["outcome"] == "Complete", body
        assert [s["tool"] for s in body["steps"]] == [
            "search_hotels",
            "get_reservation",
            "add_folio_charge",
        ]


def test_s4_unsafe_mapping_rejected_fake() -> None:
    """A mapping that drops the money param is rejected (422 + Blocked)."""
    with _fake_client() as client:
        r = client.post("/gateway/validate", json={"mapping": UNSAFE_DROPPED_AMOUNT})
        assert r.status_code == 422, r.text
        assert "dropped_money_param" in r.text
        # Unknown V1 tool through the gateway is Blocked, never guessed.
        unknown = client.post(
            "/gateway/invoke", json={"tool": "get_booking", "params": {"booking_id": "BK-1001"}}
        )
        assert unknown.status_code == 200
        assert unknown.json()["outcome"] == "Blocked"


def test_s4_rediscovery_comparison_fake() -> None:
    """Gateway Complete vs plain rediscovery Blocked on renamed tools."""
    with _fake_client() as client:
        r = client.post("/replay/compare")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["gateway_keeps_consumer_working"] is True
        assert body["rediscovery_breaks_consumer"] is True
        assert body["rediscovery"]["unmet_criterion"] == "tool_not_found:search_hotels"


# ---------------------------------------------------------------------------
# Live: same assertions against the mcp-hotel Compose service
# ---------------------------------------------------------------------------


def _live_reset() -> None:
    httpx.post(f"{MCP_URL}/reset", timeout=10.0)


def test_s4_catalog_diff_live(e2e_stack: str) -> None:
    """Live mcp-hotel serves both catalogs and the diff."""
    assert e2e_stack
    try:
        r = httpx.get(f"{MCP_URL}/readyz", timeout=10.0)
    except httpx.ConnectError as exc:
        pytest.skip(f"mcp-hotel unreachable live: {exc}")
        return
    assert r.status_code == 200, r.text
    assert r.json()["ready"] is True
    d = httpx.get(f"{MCP_URL}/catalog/diff", timeout=10.0).json()
    assert d["added"] == ["list_amenities"]
    assert len(d["rename_candidates"]) == 3


def test_s4_gateway_replay_live(e2e_stack: str) -> None:
    """Live: pinned consumer works via gateway; rediscovery breaks; unsafe rejected."""
    assert e2e_stack
    try:
        _live_reset()
    except httpx.ConnectError as exc:
        pytest.skip(f"mcp-hotel unreachable live: {exc}")
        return
    compared = httpx.post(f"{MCP_URL}/replay/compare", timeout=15.0)
    assert compared.status_code == 200, compared.text
    body: dict[str, Any] = compared.json()
    assert body["gateway"]["outcome"] == "Complete", body
    assert body["rediscovery"]["outcome"] == "Blocked", body
    assert body["gateway_keeps_consumer_working"] is True
    assert body["rediscovery_breaks_consumer"] is True
    unsafe = httpx.post(
        f"{MCP_URL}/gateway/validate", json={"mapping": UNSAFE_DROPPED_AMOUNT}, timeout=10.0
    )
    assert unsafe.status_code == 422, unsafe.text
    # Per-run isolation: a unique folio charge posts cleanly live.
    charge_id = f"live-{uuid.uuid4().hex[:8]}"
    invoked = httpx.post(
        f"{MCP_URL}/gateway/invoke",
        json={
            "tool": "add_folio_charge",
            "params": {
                "reservation_id": "BK-1001",
                "amount": "7.50",
                "description": charge_id,
                "business_date": "2026-11-04",
            },
        },
        timeout=10.0,
    )
    assert invoked.status_code == 200, invoked.text
    charge = invoked.json()["charge"]
    assert charge["amount"] == "7.50"
    assert charge["description"] == charge_id
