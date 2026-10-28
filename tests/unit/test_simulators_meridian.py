"""MeridianPMS simulator (REQ-020 sub-step A): POST/JSON body API, body auth,
Limitation cursor pagination, customers / order items / configuration joins,
and the spec-v1 "before" pin (POST is not expressible in ConnectorSpec v1).
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from simulators.common.state import set_scenario
from simulators.meridianpms.app import app as meridian_app

AUTH_BODY = {
    "ClientToken": "meridian-client-token",
    "AccessToken": "meridian-access-token",
    "Client": "BridgeApp/1.4",
}

RES_PATH = "/api/connector/v1/reservations/getAll/2023-06-06"


def _post(client: TestClient, path: str, body: dict) -> object:
    merged = dict(AUTH_BODY)
    merged.update(body)
    return client.post(path, json=merged)


@pytest.fixture(autouse=True)
def _baseline():  # type: ignore[no-untyped-def]
    set_scenario("baseline", 42)
    yield
    set_scenario("baseline", 42)


def test_healthz_and_readyz_ok() -> None:
    client = TestClient(meridian_app)
    assert client.get("/healthz").json() == {"status": "ok"}
    resp = client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["checks"]["docs"] == "ok"
    assert "scenario" in resp.json()


def test_readyz_degraded_when_docs_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import simulators.meridianpms.app as mod

    monkeypatch.setattr(mod, "DOCS_DIR", Path("/tmp/not_exist_meridian_docs_xyz"))
    client = TestClient(mod.app)
    resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["checks"]["docs"] == "missing"
    assert resp.json()["status"] == "degraded"


def test_reservations_page_shape_and_cursor_contract() -> None:
    client = TestClient(meridian_app)
    first = _post(client, RES_PATH, {"Limitation": {"Count": 5}})
    assert first.status_code == 200, first.text
    payload = first.json()
    assert len(payload["Reservations"]) == 5
    cursor = payload["Cursor"]
    assert cursor == payload["Reservations"][-1]["Id"]
    # Second page continues after the cursor, no overlap.
    second = _post(client, RES_PATH, {"Limitation": {"Count": 5, "Cursor": cursor}})
    assert second.status_code == 200
    ids1 = {r["Id"] for r in payload["Reservations"]}
    ids2 = [r["Id"] for r in second.json()["Reservations"]]
    assert not (ids1 & set(ids2))
    assert second.json()["Cursor"] == (ids2[-1] if ids2 else None)


def test_cursor_null_only_when_no_data() -> None:
    client = TestClient(meridian_app)
    # Walk to the end, then one page past it: empty page carries null cursor.
    cursor = None
    for _ in range(50):
        resp = _post(client, RES_PATH, {"Limitation": {"Count": 100, "Cursor": cursor}})
        assert resp.status_code == 200
        payload = resp.json()
        if len(payload["Reservations"]) < 100:
            break
        cursor = payload["Cursor"]
        assert cursor is not None
    last = _post(client, RES_PATH, {"Limitation": {"Count": 100, "Cursor": payload["Cursor"]}})
    assert last.status_code == 200
    assert last.json()["Reservations"] == []
    assert last.json()["Cursor"] is None


def test_reservation_wire_shape() -> None:
    client = TestClient(meridian_app)
    payload = _post(client, RES_PATH, {"Limitation": {"Count": 1}}).json()
    res = payload["Reservations"][0]
    # PascalCase, no guest name, no money on the reservation itself.
    assert set(res) >= {
        "Id", "ServiceId", "AccountId", "BookerId", "Number", "State", "Origin",
        "CreatedUtc", "UpdatedUtc", "ScheduledStartUtc", "ScheduledEndUtc",
        "RateId", "AssignedResourceId",
    }
    assert "guest_name" not in res and "GuestName" not in res
    assert "total_amount" not in res and "TotalAmount" not in res
    assert res["State"] in ("Inquired", "Confirmed", "Started", "Processed", "Canceled", "Optional", "Requested")
    assert str(res["ScheduledStartUtc"]).endswith("Z")
    # "Canceled" is the documented spelling (single l) for cancelled stays.
    cancelled = _post(client, RES_PATH, {"States": ["Canceled"], "Limitation": {"Count": 100}}).json()
    assert cancelled["Reservations"]
    assert {r["State"] for r in cancelled["Reservations"]} == {"Canceled"}


def test_missing_tokens_401_and_wrong_tokens_403() -> None:
    client = TestClient(meridian_app)
    assert client.post(RES_PATH, json={"Client": "x"}).status_code == 401
    assert client.post(RES_PATH, json={**AUTH_BODY, "AccessToken": "nope"}).status_code == 403
    body = client.post(RES_PATH, json={**AUTH_BODY, "ClientToken": "nope"}).json()
    assert client.post(RES_PATH, json={**AUTH_BODY, "ClientToken": "nope"}).status_code == 403
    assert body["detail"]["code"] == "forbidden"


def test_limitation_contract_breaches_are_400() -> None:
    client = TestClient(meridian_app)
    for bad in ({"Count": 0}, {"Count": 1001}, {"Count": "many"}):
        resp = _post(client, RES_PATH, {"Limitation": bad})
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "invalid_limitation"
    resp = _post(
        client, RES_PATH,
        {"UpdatedUtc": {"StartUtc": "2026-01-01T00:00:00Z", "EndUtc": "2026-06-01T00:00:00Z"},
         "Limitation": {"Count": 10}},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "interval_too_long"
    resp = _post(client, RES_PATH, {"ReservationIds": [f"R-{i}" for i in range(1001)]})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "too_many_ids"


def test_customers_join_by_account_id() -> None:
    client = TestClient(meridian_app)
    res = _post(client, RES_PATH, {"Limitation": {"Count": 3}}).json()["Reservations"]
    account = res[0]["AccountId"]
    cust = _post(
        client, "/api/connector/v1/customers/getAll/2023-06-06", {"CustomerIds": [account]}
    ).json()["Customers"]
    assert len(cust) == 1
    assert cust[0]["Id"] == account
    assert set(cust[0]) >= {"Id", "FirstName", "LastName", "Email", "UpdatedUtc"}


def test_order_items_money_exact_and_joinable() -> None:
    client = TestClient(meridian_app)
    res = _post(client, RES_PATH, {"Limitation": {"Count": 3}}).json()["Reservations"]
    account = res[0]["AccountId"]
    items = _post(
        client, "/api/connector/v1/orderItems/getAll/2023-06-06", {"AccountIds": [account]}
    ).json()["OrderItems"]
    assert items
    assert {i["AccountId"] for i in items} == {account}
    for item in items:
        money = item["UnitAmount"]
        assert money["Currency"]
        gross = Decimal(str(money["GrossValue"]))
        net = Decimal(str(money["NetValue"]))
        tax = sum(Decimal(str(t["Value"])) for t in money["TaxValues"])
        assert net + tax == gross


def test_configuration_gives_timezone_and_currency() -> None:
    client = TestClient(meridian_app)
    resp = _post(client, "/api/connector/v1/configuration/get/2023-06-06", {})
    assert resp.status_code == 200
    enterprises = resp.json()["Enterprises"]
    assert enterprises
    for ent in enterprises:
        assert ent["TimeZone"] and "/" in ent["TimeZone"] or ent["TimeZone"] == "UTC"
        assert ent["CurrencyCode"]


def test_wrong_version_is_404() -> None:
    client = TestClient(meridian_app)
    resp = _post(client, "/api/connector/v1/reservations/getAll/2023-04-12", {})
    assert resp.status_code == 404


def test_fault_switchboard_still_applies() -> None:
    set_scenario("rate_limit_429", 7)
    client = TestClient(meridian_app)
    codes = set()
    for _ in range(6):
        codes.add(_post(client, RES_PATH, {"Limitation": {"Count": 5}}).status_code)
    assert 429 in codes
    set_scenario("token_revoked", 7)
    assert _post(client, RES_PATH, {"Limitation": {"Count": 5}}).status_code == 403


def test_error_paths_all_named() -> None:
    """Every 400 names its contract breach; faults and aliases behave."""
    from fastapi.testclient import TestClient

    from simulators.common.state import set_scenario
    from simulators.meridianpms.app import _dumps_exact

    set_scenario("baseline", 42)
    client = TestClient(meridian_app)
    assert _post(client, RES_PATH, {"Limitation": "nope"}).status_code == 400
    assert _post(client, RES_PATH, {"Limitation": {"Count": 5, "Cursor": 7}}).status_code == 400
    assert _post(client, RES_PATH, {"ReservationIds": "R-1"}).status_code == 400
    assert _post(client, RES_PATH, {"UpdatedUtc": {}}).status_code == 400
    r = _post(client, RES_PATH, {"UpdatedUtc": {"StartUtc": "oops", "EndUtc": "oops"}})
    assert r.status_code == 400
    r = _post(
        client, RES_PATH,
        {"UpdatedUtc": {"StartUtc": "2026-02-01T00:00:00Z", "EndUtc": "2026-01-01T00:00:00Z"}},
    )
    assert r.status_code == 400 and r.json()["detail"]["code"] == "invalid_filter"
    # Unknown cursor restarts from the beginning instead of erroring.
    first = _post(client, RES_PATH, {"Limitation": {"Count": 2, "Cursor": "no-such-id"}})
    assert first.status_code == 200 and len(first.json()["Reservations"]) == 2
    # Docs alias serves the YAML single source.
    assert client.get("/_meridian_docs").status_code == 200
    assert client.get("/_meridian_docs").headers["content-type"].startswith("text/yaml")
    # Exact-JSON serializer never lets a float or unknown type on the wire.
    import pytest as _pytest

    with _pytest.raises(ValueError):
        _dumps_exact({"amount": 1.5})
    with _pytest.raises(ValueError):
        _dumps_exact({"amount": object()})


def test_fault_auth_and_removed_are_named() -> None:
    from fastapi.testclient import TestClient

    from simulators.common.state import set_scenario

    set_scenario("token_expired", 7)
    client = TestClient(meridian_app)
    assert _post(client, RES_PATH, {"Limitation": {"Count": 5}}).status_code == 401
    set_scenario("endpoint_removed", 7)
    assert _post(client, RES_PATH, {"Limitation": {"Count": 5}}).status_code == 404


def test_filters_across_operations() -> None:
    from fastapi.testclient import TestClient

    from simulators.common.state import set_scenario

    set_scenario("baseline", 42)
    client = TestClient(meridian_app)
    res = _post(client, RES_PATH, {"Limitation": {"Count": 1}}).json()["Reservations"][0]
    ent = str(res["ServiceId"]).removeprefix("SVC-")
    got = _post(client, RES_PATH, {"EnterpriseIds": [ent], "Limitation": {"Count": 100}}).json()
    assert got["Reservations"]
    assert all(str(r["ServiceId"]) == res["ServiceId"] for r in got["Reservations"])
    svc = _post(
        client, "/api/connector/v1/orderItems/getAll/2023-06-06",
        {"EnterpriseIds": [ent], "Limitation": {"Count": 100}},
    ).json()
    assert svc["OrderItems"]
    cfg = _post(
        client, "/api/connector/v1/configuration/get/2023-06-06", {"EnterpriseIds": [ent]}
    ).json()
    assert [e["Id"] for e in cfg["Enterprises"]] == [ent]
    cust = _post(
        client, "/api/connector/v1/customers/getAll/2023-06-06",
        {"UpdatedUtc": {"StartUtc": "2020-01-01T00:00:00Z", "EndUtc": "2020-01-02T00:00:00Z"},
         "Limitation": {"Count": 10}},
    ).json()
    assert cust["Customers"] == [] and cust["Cursor"] is None
