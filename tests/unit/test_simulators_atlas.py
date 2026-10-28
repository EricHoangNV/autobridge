"""REQ-019: AtlasPMS Cloudbeds-style surface — x-api-key, page pagination, envelope, JSON numbers."""

from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from simulators.atlaspms.app import DOCS_DIR
from simulators.atlaspms.app import app as atlas_app
from simulators.common.helpers import check_auth
from simulators.common.state import set_scenario

AUTH = {"x-api-key": "test-token"}
BEARER = {"Authorization": "Bearer test-token"}


def test_atlas_healthz_and_readyz_ok() -> None:
    client = TestClient(atlas_app)
    assert client.get("/healthz").status_code == 200
    assert client.get("/healthz").json() == {"status": "ok"}
    resp = client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["checks"]["default"] == "ok"
    assert body["checks"]["docs"] == "ok"
    assert "scenario" in body["checks"]


def test_atlas_readyz_degraded_when_docs_missing(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Simulate missing docs by pointing DOCS_DIR to non-existent
    from simulators.atlaspms import app as mod

    monkeypatch.setattr(mod, "DOCS_DIR", Path("/tmp/not_exist_atlas_docs_xyz"))
    client = TestClient(mod.app)
    resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["checks"]["docs"] == "missing"
    assert resp.json()["status"] == "degraded"


def test_atlas_openapi_json_is_3_1_and_clean() -> None:
    client = TestClient(atlas_app)
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    data = resp.json()
    assert data["openapi"] == "3.1.0"
    assert "info" in data
    assert data["info"]["title"] == "AtlasPMS API"
    # x-api-key auth present (OAuth2 accepted as alternative)
    schemes = data["components"]["securitySchemes"]
    assert "ApiKey" in schemes
    sch = schemes["ApiKey"]
    assert sch["type"] == "apiKey"
    assert sch["name"] == "x-api-key"
    assert sch["in"] == "header"
    # paths are the versioned method-style surface
    assert "/api/v1.3/getReservations" in data["paths"]
    assert "/api/v1.3/getHotels" in data["paths"]
    assert "/api/v1.3/getTransactions" in data["paths"]
    assert "/api/v1.3/getReservation" in data["paths"]
    assert "/api/v1.3/getGuest" in data["paths"]
    # page pagination params on the list method
    params = {p["name"] for p in data["paths"]["/api/v1.3/getReservations"]["get"]["parameters"]}
    assert "pageNumber" in params
    assert "pageSize" in params
    assert "limit" not in params
    assert "offset" not in params
    assert "cursor" not in params  # page means page only
    # status enum uses the documented spelling (single-l canceled)
    item = data["components"]["schemas"]["ReservationItem"]
    assert item["properties"]["status"]["enum"] == ["not_confirmed", "confirmed", "canceled", "checked_in", "checked_out", "no_show"]


def test_atlas_openapi_yaml_and_spec_alias() -> None:
    client = TestClient(atlas_app)
    resp_y = client.get("/openapi.yaml")
    assert resp_y.status_code == 200
    assert "openapi" in resp_y.text
    assert "AtlasPMS API" in resp_y.text
    # alias
    assert client.get("/spec.json").status_code == 200
    assert client.get("/spec.json").json()["openapi"] == "3.1.0"
    assert client.get("/spec.yaml").status_code == 200


def test_atlas_docs_static_served() -> None:
    client = TestClient(atlas_app)
    resp = client.get("/docs/atlaspms-openapi.json")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/json"
    data = resp.json()
    assert data["openapi"] == "3.1.0"
    # alternate alias files also exist on disk
    assert (DOCS_DIR / "atlas-openapi.json").is_file()
    assert (DOCS_DIR / "atlaspms-openapi.yaml").is_file()
    resp2 = client.get("/docs/atlaspms-openapi.yaml")
    assert resp2.status_code == 200

    # file on disk is byte-identical to served json
    on_disk = json.loads((DOCS_DIR / "atlaspms-openapi.json").read_text(encoding="utf-8"))
    assert resp.json() == on_disk


def test_atlas_api_key_auth_required() -> None:
    set_scenario("baseline", 10)
    client = TestClient(atlas_app)
    # no header -> 401
    resp = client.get("/api/v1.3/getReservations?pageSize=2")
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "unauthorized"
    # Bearer alternative accepted (OAuth2)
    assert client.get("/api/v1.3/getReservations?pageSize=2", headers=BEARER).status_code == 200
    # valid x-api-key -> 200
    assert client.get("/api/v1.3/getReservations?pageSize=2", headers=AUTH).status_code == 200
    # every method requires auth
    assert client.get("/api/v1.3/getHotels").status_code == 401
    assert client.get("/api/v1.3/getHotels", headers=AUTH).status_code == 200
    assert client.get("/api/v1.3/getTransactions?pageSize=2").status_code == 401
    assert client.get("/api/v1.3/getTransactions?pageSize=2", headers=AUTH).status_code == 200
    assert client.get("/api/v1.3/getGuest?reservationID=x").status_code == 401
    # get by id requires auth
    rid = client.get("/api/v1.3/getReservations?pageSize=1", headers=AUTH).json()["data"][0]["reservationID"]
    assert client.get(f"/api/v1.3/getReservation?reservationID={rid}").status_code == 401
    assert client.get(f"/api/v1.3/getReservation?reservationID={rid}", headers=AUTH).status_code == 200
    # getGuest by reservationID
    resp_g = client.get(f"/api/v1.3/getGuest?reservationID={rid}", headers=AUTH)
    assert resp_g.status_code == 200
    assert "firstName" in resp_g.json()["data"]


def test_atlas_auth_fault_overrides() -> None:
    # Even with a valid key, token_expired still 401 with its own code
    set_scenario("token_expired", 10)
    client = TestClient(atlas_app)
    resp = client.get("/api/v1.3/getReservations?pageSize=2", headers=AUTH)
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "token_expired"
    set_scenario("token_revoked", 10)
    resp2 = client.get("/api/v1.3/getReservations?pageSize=2", headers=AUTH)
    assert resp2.status_code == 403
    assert resp2.json()["detail"]["code"] == "token_revoked"


def test_atlas_helpers_check_auth_require_bearer() -> None:
    import pytest
    from fastapi import HTTPException

    # baseline without header should raise when required
    with pytest.raises(HTTPException) as exc:
        check_auth("baseline", None, require_bearer=True)
    assert exc.value.status_code == 401  # type: ignore[attr-defined]
    with pytest.raises(HTTPException):
        check_auth("baseline", "BadPrefix token", require_bearer=True)
    with pytest.raises(HTTPException):
        check_auth("baseline", "Bearer ", require_bearer=True)
    # valid
    check_auth("baseline", "Bearer abc", require_bearer=True)
    # fault still wins even with valid bearer
    with pytest.raises(HTTPException) as exc2:
        check_auth("token_expired", "Bearer abc", require_bearer=True)
    assert exc2.value.status_code == 401  # type: ignore[attr-defined]


def test_atlas_page_pagination_shape() -> None:
    set_scenario("baseline", 42)
    client = TestClient(atlas_app)
    resp = client.get("/api/v1.3/getReservations?pageNumber=1&pageSize=5", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["total"] == 30
    assert body["count"] == 5
    assert len(body["data"]) == 5
    # no cursor/offset/next keys on the page surface
    assert "next_cursor" not in body
    assert "next_offset" not in body
    assert "cursor" not in body
    assert "offset" not in body
    # next page differs
    resp2 = client.get("/api/v1.3/getReservations?pageNumber=2&pageSize=5", headers=AUTH)
    assert resp2.json()["data"] != body["data"]
    # last page is short; past-the-end page is empty
    resp_last = client.get("/api/v1.3/getReservations?pageNumber=7&pageSize=5", headers=AUTH)
    assert resp_last.json()["count"] == 0
    assert resp_last.json()["data"] == []
    # transactions are page-paginated too
    resp_f = client.get("/api/v1.3/getTransactions?pageNumber=1&pageSize=5", headers=AUTH)
    assert resp_f.json()["success"] is True
    assert "next_cursor" not in resp_f.json()


def test_atlas_statuses_and_money_numbers() -> None:
    set_scenario("baseline", 42)
    client = TestClient(atlas_app)
    resp = client.get("/api/v1.3/getReservations?pageNumber=1&pageSize=100", headers=AUTH)
    statuses = {r["status"] for r in resp.json()["data"]}
    assert statuses.issubset({"not_confirmed", "confirmed", "canceled", "checked_in", "checked_out", "no_show"})
    assert len(statuses) >= 2  # deterministic seed should have multiple
    # money arrives as JSON numbers (unquoted in the raw body), never strings
    raw = resp.text
    assert re.search(r'"balance":\s*-?[0-9]+(\.[0-9]+)?', raw), "balance must be a JSON number"
    assert '"balance": "' not in raw
    resp_f = client.get("/api/v1.3/getTransactions?pageNumber=1&pageSize=20", headers=AUTH)
    for rec in resp_f.json()["data"]:
        assert isinstance(rec["amount"], (int, float))
        assert not isinstance(rec["amount"], bool)
    assert re.search(r'"amount":\s*-?[0-9]+(\.[0-9]+)?', resp_f.text)
    # camelCase wire names, no snake_case leaks on the surface
    assert resp.json()["data"][0]["reservationID"]
    assert "reservation_id" not in resp.text


def test_atlas_filter_and_get_by_id() -> None:
    set_scenario("baseline", 42)
    client = TestClient(atlas_app)
    props = client.get("/api/v1.3/getHotels", headers=AUTH).json()["data"]
    pid = props[0]["propertyID"]
    assert props[0]["propertyCurrency"][0]["currencyCode"]
    resp = client.get(f"/api/v1.3/getReservations?propertyID={pid}&pageSize=100", headers=AUTH)
    for r in resp.json()["data"]:
        assert r["propertyID"] == pid
    # transaction filter
    rid = client.get("/api/v1.3/getReservations?pageSize=1", headers=AUTH).json()["data"][0]["reservationID"]
    resp_f = client.get(f"/api/v1.3/getTransactions?reservationID={rid}&pageSize=100", headers=AUTH)
    if resp_f.json()["data"]:
        for f in resp_f.json()["data"]:
            assert f["reservationID"] == rid
    # status filter
    resp_s = client.get("/api/v1.3/getReservations?status=canceled&pageSize=100", headers=AUTH)
    for r in resp_s.json()["data"]:
        assert r["status"] == "canceled"
    # single get by id
    assert client.get("/api/v1.3/getReservation?reservationID=DOES_NOT_EXIST", headers=AUTH).status_code == 404
    one = client.get(f"/api/v1.3/getReservation?reservationID={rid}", headers=AUTH)
    assert one.status_code == 200
    assert one.json()["data"]["reservationID"] == rid
    assert isinstance(one.json()["data"]["total"], (int, float))
    # transactions 404 is covered by empty page (no by-id route); missing guest 404
    assert client.get("/api/v1.3/getGuest?reservationID=DOES_NOT_EXIST", headers=AUTH).status_code == 404


def test_atlas_page_to_cursor_fault() -> None:
    set_scenario("page_to_cursor", 42)
    client = TestClient(atlas_app)
    resp = client.get("/api/v1.3/getReservations?pageNumber=1&pageSize=5", headers=AUTH)
    body = resp.json()
    assert body["success"] is True
    assert "next_cursor" in body
    assert body["next_cursor"] is not None
    # legacy page params ignored: same first page as cursor start
    assert len(body["data"]) == 5


def test_atlas_old_routes_removed() -> None:
    set_scenario("baseline", 42)
    client = TestClient(atlas_app)
    assert client.get("/reservations", headers=AUTH).status_code == 404
    assert client.get("/folios", headers=AUTH).status_code == 404
    assert client.get("/properties", headers=AUTH).status_code == 404


def test_atlas_docs_missing_returns_404(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from simulators.atlaspms import app as mod

    monkeypatch.setattr(mod, "DOCS_DIR", Path("/tmp/not_exist_atlas_docs_xyz2"))
    client = TestClient(mod.app)
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/openapi.yaml").status_code == 404
    assert client.get("/spec.json").status_code == 404


def test_atlas_does_not_import_verifier_or_autobridge() -> None:
    import re
    from pathlib import Path

    text = (Path(__file__).resolve().parents[2] / "simulators/atlaspms/app.py").read_text(encoding="utf-8")
    for m in re.finditer(r"^\s*(?:import|from)\s+(\S+)", text, re.MULTILINE):
        top = m.group(1).split(".")[0]
        assert top not in {"autobridge", "verifier"}, f"atlas app imports {top}"
