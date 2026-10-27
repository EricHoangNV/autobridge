"""P2.S2.1: EdgePOS toy POS simulator on the isolated edge network."""

from __future__ import annotations

from fastapi.testclient import TestClient

from simulators.edgepos.app import FOLIO_CHARGES, RESERVATIONS
from simulators.edgepos.app import app as edgepos_app


def test_edgepos_healthz_and_readyz_ok() -> None:
    client = TestClient(edgepos_app)
    assert client.get("/healthz").json() == {"status": "ok"}
    body = client.get("/readyz").json()
    assert body["status"] == "ok"
    assert body["checks"]["dataset"] == "ok"


def test_edgepos_reservations_pagination() -> None:
    client = TestClient(edgepos_app)
    resp = client.get("/reservations", params={"limit": 1, "offset": 0})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == len(RESERVATIONS) == 2
    assert len(body["data"]) == 1
    assert body["data"][0]["reservation_id"] == "EDGE-1001"
    second = client.get("/reservations", params={"limit": 1, "offset": 1}).json()
    assert second["data"][0]["reservation_id"] == "EDGE-1002"


def test_edgepos_folios_and_get_by_id() -> None:
    client = TestClient(edgepos_app)
    folios = client.get("/folios").json()
    assert folios["total"] == len(FOLIO_CHARGES) == 1
    assert folios["data"][0]["business_date"] == "2026-10-27"
    one = client.get("/reservations/EDGE-1001").json()
    assert one["guest_name"] == "Grace Hopper"
    missing = client.get("/reservations/NOPE")
    assert missing.status_code == 404


def test_edgepos_money_and_dates_are_canonical() -> None:
    client = TestClient(edgepos_app)
    for rec in client.get("/reservations").json()["data"]:
        assert rec["total_amount"] == f"{float(rec['total_amount']):.2f}"
        assert len(rec["arrival_date"]) == 10
        assert rec["updated_at"].endswith("+00:00")
