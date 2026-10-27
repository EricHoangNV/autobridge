"""P2.S4.1 MCP Guardian unit tests (plan §5 S4).

Covers the declarative catalog snapshot/diff, the pinned gateway mapping
(safe by construction, unsafe candidates rejected), invoke outcomes
(Complete | Degraded | Blocked with the criterion named), Decimal money,
property-local business dates, the pinned-workflow replay comparison, and
the no-secrets invariant. Collected by ``make test`` via the
``test_autobridge_*`` pattern; ``mcp_stretch/`` itself is outside the
coverage gate (plan §5 isolation) but every branch here is asserted.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from mcp_stretch import replay as replay_mod
from mcp_stretch.app import app as mcp_app
from mcp_stretch.catalog import (
    V1_CATALOG,
    V1_VERSION,
    V2_CATALOG,
    V2_VERSION,
    diff,
    snapshot,
    tool_map,
)
from mcp_stretch.gateway import (
    PINNED_MAPPING,
    Gateway,
    money_str,
    parse_business_date,
    parse_money,
    validate_mapping,
)


@pytest.fixture()
def gw() -> Gateway:
    gateway = Gateway()
    gateway.reset()
    return gateway


# ---------------------------------------------------------------------------
# Catalog: snapshot determinism + diff
# ---------------------------------------------------------------------------


def test_catalog_snapshots_deterministic() -> None:
    assert snapshot(V1_CATALOG, V1_VERSION) == snapshot(V1_CATALOG, V1_VERSION)
    assert snapshot(V2_CATALOG, V2_VERSION) == snapshot(V2_CATALOG, V2_VERSION)
    assert len(tool_map(V1_CATALOG)) == 3
    assert len(tool_map(V2_CATALOG)) == 4


def test_catalog_diff_finds_renames_and_addition() -> None:
    d = diff(V1_CATALOG, V2_CATALOG)
    assert d["added"] == ["list_amenities"]
    assert d["removed"] == []
    assert d["param_changed"] == []
    pairs = {(c["from"], c["to"]) for c in d["rename_candidates"]}
    assert pairs == {
        ("search_hotels", "search_properties"),
        ("get_reservation", "get_booking"),
        ("add_folio_charge", "post_folio_charge"),
    }
    folio = next(c for c in d["rename_candidates"] if c["from"] == "add_folio_charge")
    assert folio["param_map"] == {"reservation_id": "booking_id", "description": "memo"}


def test_pinned_mapping_matches_diff_candidates() -> None:
    d = diff(V1_CATALOG, V2_CATALOG)
    cand = {(c["from"], c["to"]): c["param_map"] for c in d["rename_candidates"]}
    for v1_name, entry in PINNED_MAPPING.items():
        key = (v1_name, entry["target"])
        assert key in cand, f"pinned mapping not drawn from diff candidates: {key}"
        for src, dst in entry["params"].items():
            # Every pinned rename must agree with the diff (identity params agree trivially).
            if src in cand[key]:
                assert cand[key][src] == dst


# ---------------------------------------------------------------------------
# Mapping validation: safe pinned, unsafe rejected
# ---------------------------------------------------------------------------


def test_pinned_mapping_valid() -> None:
    ok, reason = validate_mapping(PINNED_MAPPING)
    assert ok, reason


def test_unsafe_mapping_dropped_money_rejected() -> None:
    bad = {
        "add_folio_charge": {
            "target": "post_folio_charge",
            "params": {
                "reservation_id": "booking_id",
                "description": "memo",
                "business_date": "business_date",
            },
        }
    }
    ok, reason = validate_mapping(bad)
    assert not ok
    assert reason == "dropped_money_param:add_folio_charge.amount"


def test_unsafe_mapping_money_to_non_money_rejected() -> None:
    bad = {
        "add_folio_charge": {
            "target": "post_folio_charge",
            "params": {
                "reservation_id": "booking_id",
                "amount": "memo",
                "description": "memo",
                "business_date": "business_date",
            },
        }
    }
    ok, reason = validate_mapping(bad)
    assert not ok
    assert "money_routed_to_non_money" in reason


def test_unsafe_mapping_unknown_tools_rejected() -> None:
    ok, reason = validate_mapping({"nope": {"target": "get_booking", "params": {}}})
    assert not ok and reason.startswith("unknown_v1_tool")
    ok, reason = validate_mapping({"get_reservation": {"target": "nope", "params": {}}})
    assert not ok and reason.startswith("unknown_v2_tool")


def test_gateway_rejects_unsafe_mapping_at_construction() -> None:
    with pytest.raises(ValueError, match="unsafe_gateway_mapping"):
        Gateway({"get_reservation": {"target": "get_booking", "params": {}}})


# ---------------------------------------------------------------------------
# Money is Decimal, dates are property-local business dates
# ---------------------------------------------------------------------------


def test_money_decimal_exact_2dp() -> None:
    assert parse_money("19.99") == Decimal("19.99")
    assert parse_money("19.9") == Decimal("19.90")
    assert money_str(Decimal("19.99") + Decimal("0.01")) == "20.00"
    with pytest.raises(ValueError, match="invalid_money"):
        parse_money("not-a-number")


def test_business_date_validated_not_converted() -> None:
    assert parse_business_date("2026-11-03") == "2026-11-03"
    with pytest.raises(ValueError, match="invalid_business_date"):
        parse_business_date("11/03/2026")
    with pytest.raises(ValueError, match="invalid_business_date"):
        parse_business_date("2026-13-01")


# ---------------------------------------------------------------------------
# Gateway invoke outcomes
# ---------------------------------------------------------------------------


def test_invoke_search_hotels_complete(gw: Gateway) -> None:
    out = gw.invoke(
        "search_hotels",
        {"query": "plaza", "check_in": "2026-11-01", "check_out": "2026-11-06"},
    )
    assert out["outcome"] == "Complete"
    assert [h["hotel_id"] for h in out["hotels"]] == ["PROP-001"]


def test_invoke_get_reservation_complete_v1_shape(gw: Gateway) -> None:
    out = gw.invoke("get_reservation", {"reservation_id": "BK-1001"})
    assert out["outcome"] == "Complete"
    res = out["reservation"]
    assert res["reservation_id"] == "BK-1001"
    assert res["hotel_id"] == "PROP-001"
    assert res["total_amount"] == "450.00"
    assert Decimal(res["total_amount"]).as_tuple().exponent == -2


def test_invoke_add_folio_charge_complete(gw: Gateway) -> None:
    out = gw.invoke(
        "add_folio_charge",
        {
            "reservation_id": "BK-1002",
            "amount": "19.99",
            "description": "minibar",
            "business_date": "2026-11-03",
        },
    )
    assert out["outcome"] == "Complete"
    charge = out["charge"]
    assert charge["amount"] == "19.99"
    assert charge["business_date"] == "2026-11-03"
    assert charge["currency"] == "USD"


def test_invoke_unknown_tool_blocked_never_guessed(gw: Gateway) -> None:
    out = gw.invoke("get_booking", {"booking_id": "BK-1001"})
    assert out["outcome"] == "Blocked"
    assert out["unmet_criterion"] == "unknown_tool:get_booking"


def test_invoke_missing_param_blocked(gw: Gateway) -> None:
    out = gw.invoke("get_reservation", {})
    assert out["outcome"] == "Blocked"
    assert out["unmet_criterion"] == "missing_param:reservation_id"


def test_invoke_invalid_money_blocked(gw: Gateway) -> None:
    out = gw.invoke(
        "add_folio_charge",
        {
            "reservation_id": "BK-1001",
            "amount": "free",
            "description": "x",
            "business_date": "2026-11-03",
        },
    )
    assert out["outcome"] == "Blocked"
    assert "invalid_money" in out["unmet_criterion"]


def test_invoke_bad_date_and_currency_blocked(gw: Gateway) -> None:
    bad_date = gw.invoke(
        "add_folio_charge",
        {
            "reservation_id": "BK-1001",
            "amount": "5.00",
            "description": "x",
            "business_date": "yesterday",
        },
    )
    assert bad_date["outcome"] == "Blocked"
    assert "invalid_business_date" in bad_date["unmet_criterion"]
    eur = gw.invoke("get_reservation", {"reservation_id": "BK-1001", "currency": "EUR"})
    assert eur["outcome"] == "Blocked"
    assert eur["unmet_criterion"] == "unsupported_currency:EUR"


def test_invoke_extra_param_degraded_with_open_question(gw: Gateway) -> None:
    out = gw.invoke(
        "get_reservation", {"reservation_id": "BK-1001", "loyalty_tier": "gold"}
    )
    assert out["outcome"] == "Degraded"
    assert out["open_questions"] == ["unmapped_param_ignored:loyalty_tier"]
    assert out["reservation"]["reservation_id"] == "BK-1001"


def test_no_secrets_in_catalog_or_results(gw: Gateway) -> None:
    blob = snapshot(V1_CATALOG, V1_VERSION) + snapshot(V2_CATALOG, V2_VERSION)
    for needle in ("api_key", "token", "secret", "password", "credential"):
        assert needle not in blob.lower()
    out = gw.invoke("get_reservation", {"reservation_id": "BK-1001"})
    assert "api_key" not in str(out).lower()


# ---------------------------------------------------------------------------
# Replay: gateway keeps the pinned consumer working, rediscovery breaks
# ---------------------------------------------------------------------------


def test_replay_gateway_complete(gw: Gateway) -> None:
    report = replay_mod.replay(replay_mod.PINNED_WORKFLOW, replay_mod.gateway_backend(gw))
    assert report["outcome"] == "Complete"
    assert [s["outcome"] for s in report["steps"]] == ["Complete"] * 3


def test_replay_rediscovery_blocked_on_renamed_tool(gw: Gateway) -> None:
    report = replay_mod.replay(
        replay_mod.PINNED_WORKFLOW, replay_mod.plain_rediscovery_backend(gw)
    )
    assert report["outcome"] == "Blocked"
    assert report["unmet_criterion"] == "tool_not_found:search_hotels"


def test_compare_verdict(gw: Gateway) -> None:
    verdict = replay_mod.compare(gw)
    assert verdict["gateway_keeps_consumer_working"] is True
    assert verdict["rediscovery_breaks_consumer"] is True
    assert verdict["gateway"]["outcome"] == "Complete"
    assert verdict["rediscovery"]["outcome"] == "Blocked"


def test_replay_assertion_failure_blocks(gw: Gateway) -> None:
    tampered = [{**s, "assert": {"total_amount": "0.01"}} for s in replay_mod.PINNED_WORKFLOW[:2]]
    report = replay_mod.replay(tampered, replay_mod.gateway_backend(gw))
    assert report["outcome"] == "Blocked"
    assert "assertion_failed" in report["unmet_criterion"]


# ---------------------------------------------------------------------------
# HTTP surface (fake transport, no Docker)
# ---------------------------------------------------------------------------


def test_app_health_and_readyz() -> None:
    client = TestClient(mcp_app)
    assert client.get("/healthz").status_code == 200
    r = client.get("/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["ready"] is True
    assert body["checks"] == {"catalog": "ok", "pinned_mapping": "ok"}
    assert (body["v1_tools"], body["v2_tools"]) == (3, 4)


def test_app_gateway_and_replay_endpoints() -> None:
    client = TestClient(mcp_app)
    diff_body = client.get("/catalog/diff").json()
    assert diff_body["added"] == ["list_amenities"]
    assert len(diff_body["rename_candidates"]) == 3
    invoked = client.post(
        "/gateway/invoke", json={"tool": "get_reservation", "params": {"reservation_id": "BK-1001"}}
    )
    assert invoked.status_code == 200
    assert invoked.json()["outcome"] == "Complete"
    unsafe = client.post(
        "/gateway/validate",
        json={
            "mapping": {
                "add_folio_charge": {
                    "target": "post_folio_charge",
                    "params": {
                        "reservation_id": "booking_id",
                        "description": "memo",
                        "business_date": "business_date",
                    },
                }
            }
        },
    )
    assert unsafe.status_code == 422
    compared = client.post("/replay/compare").json()
    assert compared["gateway_keeps_consumer_working"] is True
    assert compared["rediscovery_breaks_consumer"] is True
    assert client.post("/replay/run", json={"mode": "nope"}).status_code == 422
