"""P1.W2.3: AtlasPMS clean OpenAPI 3.1 docs, bearer, offset, clear statuses, readyz."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from simulators.atlaspms.app import DOCS_DIR
from simulators.atlaspms.app import app as atlas_app
from simulators.common.helpers import check_auth
from simulators.common.state import set_scenario

AUTH = {"Authorization": "Bearer test-token"}


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
    # bearer auth present
    assert "bearerAuth" in data["components"]["securitySchemes"]
    sch = data["components"]["securitySchemes"]["bearerAuth"]
    assert sch["type"] == "http"
    assert sch["scheme"] == "bearer"
    # global security
    assert data["security"] == [{"bearerAuth": []}]
    # paths include clean endpoints
    assert "/reservations" in data["paths"]
    assert "/properties" in data["paths"]
    assert "/folios" in data["paths"]
    # offset pagination params on reservations
    params = {p["name"] for p in data["paths"]["/reservations"]["get"]["parameters"]}
    assert "limit" in params
    assert "offset" in params
    assert "cursor" not in params  # clean means offset only
    # responses mention offset pagination
    schema = data["paths"]["/reservations"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert "total" in schema["properties"]
    assert "next_offset" in schema["properties"]
    # clear statuses enum
    res_schema = data["components"]["schemas"]["Reservation"]
    assert res_schema["properties"]["status"]["enum"] == ["confirmed", "checked_in", "checked_out", "cancelled", "no_show"]


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


def test_atlas_bearer_auth_required() -> None:
    set_scenario("baseline", 10)
    client = TestClient(atlas_app)
    # no header -> 401
    resp = client.get("/reservations?limit=2")
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "unauthorized"
    # invalid prefix
    resp2 = client.get("/reservations?limit=2", headers={"Authorization": "Invalid"})
    assert resp2.status_code == 401
    # empty token after Bearer
    resp3 = client.get("/reservations?limit=2", headers={"Authorization": "Bearer "})
    assert resp3.status_code == 401
    resp3b = client.get("/reservations?limit=2", headers={"Authorization": "Bearer    "})
    assert resp3b.status_code == 401
    # valid -> 200
    resp_ok = client.get("/reservations?limit=2", headers=AUTH)
    assert resp_ok.status_code == 200
    # properties also requires auth
    assert client.get("/properties").status_code == 401
    assert client.get("/properties", headers=AUTH).status_code == 200
    # folios requires auth
    assert client.get("/folios?limit=2").status_code == 401
    assert client.get("/folios?limit=2", headers=AUTH).status_code == 200
    # get by id requires auth
    rid = client.get("/reservations?limit=1", headers=AUTH).json()["data"][0]["reservation_id"]
    assert client.get(f"/reservations/{rid}").status_code == 401
    assert client.get(f"/reservations/{rid}", headers=AUTH).status_code == 200
    # get folio by id requires auth
    cid = client.get("/folios?limit=1", headers=AUTH).json()["data"][0]["charge_id"]
    assert client.get(f"/folios/{cid}").status_code == 401
    assert client.get(f"/folios/{cid}", headers=AUTH).status_code == 200


def test_atlas_bearer_fault_overrides() -> None:
    # Even with valid bearer, token_expired still 401 with its own code
    set_scenario("token_expired", 10)
    client = TestClient(atlas_app)
    resp = client.get("/reservations?limit=2", headers=AUTH)
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "token_expired"
    set_scenario("token_revoked", 10)
    resp2 = client.get("/reservations?limit=2", headers=AUTH)
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


def test_atlas_offset_pagination_shape() -> None:
    set_scenario("baseline", 42)
    client = TestClient(atlas_app)
    resp = client.get("/reservations?limit=5&offset=0", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 30
    assert body["limit"] == 5
    assert body["offset"] == 0
    assert body["next_offset"] == 5
    assert len(body["data"]) == 5
    # next page
    resp2 = client.get("/reservations?limit=5&offset=5", headers=AUTH)
    assert resp2.json()["offset"] == 5
    assert resp2.json()["data"] != body["data"]
    # last page next_offset null
    resp_last = client.get("/reservations?limit=100&offset=0", headers=AUTH)
    assert resp_last.json()["next_offset"] is None
    # folios also offset
    resp_f = client.get("/folios?limit=5&offset=0", headers=AUTH)
    assert "next_offset" in resp_f.json()
    assert "next_cursor" not in resp_f.json()
    # ensure no cursor alias leaks
    for key in body:
        assert key != "next_cursor"
        assert key != "cursor"


def test_atlas_clear_statuses() -> None:
    set_scenario("baseline", 42)
    client = TestClient(atlas_app)
    resp = client.get("/reservations?limit=100", headers=AUTH)
    statuses = {r["status"] for r in resp.json()["data"]}
    assert statuses.issubset({"confirmed", "checked_in", "checked_out", "cancelled", "no_show"})
    assert len(statuses) >= 2  # deterministic seed should have multiple
    # folios amounts are decimal strings
    resp_f = client.get("/folios?limit=20", headers=AUTH)
    for rec in resp_f.json()["data"]:
        amt = rec["amount"]
        assert "." in amt
        # should be valid decimal with 2dp
        from decimal import Decimal

        d = Decimal(amt)
        assert d.as_tuple().exponent == -2


def test_atlas_filter_and_get_by_id() -> None:
    set_scenario("baseline", 42)
    client = TestClient(atlas_app)
    props = client.get("/properties", headers=AUTH).json()["data"]
    pid = props[0]["property_id"]
    resp = client.get(f"/reservations?property_id={pid}&limit=100", headers=AUTH)
    for r in resp.json()["data"]:
        assert r["property_id"] == pid
    # folio filter
    rid = client.get("/reservations?limit=1", headers=AUTH).json()["data"][0]["reservation_id"]
    resp_f = client.get(f"/folios?reservation_id={rid}&limit=100", headers=AUTH)
    if resp_f.json()["data"]:
        for f in resp_f.json()["data"]:
            assert f["reservation_id"] == rid
    # 404 for missing
    assert client.get("/reservations/DOES_NOT_EXIST", headers=AUTH).status_code == 404
    assert client.get("/folios/DOES_NOT_EXIST", headers=AUTH).status_code == 404


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
