"""P2.S1.1: LegacyStay simulator — offset pagination, strict Bearer auth, plain-English
statuses, ambiguous date, nullable chaos, baseline throttle, readyz."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from simulators.common.state import set_scenario
from simulators.legacystay.app import DOCS_DIR, THROTTLE_EVERY_N
from simulators.legacystay.app import app as legacy_app

AUTH = {"Authorization": "Bearer test-token"}


def test_legacystay_healthz_and_readyz_ok() -> None:
    client = TestClient(legacy_app)
    assert client.get("/healthz").status_code == 200
    assert client.get("/healthz").json() == {"status": "ok"}
    resp = client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["checks"]["default"] == "ok"
    assert body["checks"]["docs"] == "ok"
    assert "scenario" in body["checks"]


def test_legacystay_readyz_degraded_when_docs_missing(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from simulators.legacystay import app as mod

    monkeypatch.setattr(mod, "DOCS_DIR", Path("/tmp/not_exist_legacy_docs_xyz"))
    client = TestClient(mod.app)
    resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["checks"]["docs"] == "missing"
    assert resp.json()["status"] == "degraded"


def test_legacystay_markdown_docs_served() -> None:
    client = TestClient(legacy_app)
    resp = client.get("/docs/legacystay.md")
    assert resp.status_code == 200
    text = resp.text
    assert "LegacyStay API" in text
    assert "GET /reservations" in text
    on_disk = (DOCS_DIR / "legacystay.md").read_text(encoding="utf-8")
    assert resp.text == on_disk
    # Docs carry the misleading traps: outdated cursor example + wrong status words
    assert "next_cursor" in text
    assert "next_offset" in text
    assert "`active`" in text
    assert "confirmed" in text


def test_legacystay_requires_bearer_auth() -> None:
    set_scenario("baseline", 42)
    client = TestClient(legacy_app)
    # No auth header -> 401 (strict, unlike Harbor's permissive auth)
    assert client.get("/reservations?limit=1").status_code == 401
    assert client.get("/folios?limit=1").status_code == 401
    assert client.get("/properties").status_code == 401
    # Empty bearer also rejected
    assert client.get("/reservations?limit=1", headers={"Authorization": "Bearer "}).status_code == 401
    # Valid bearer passes
    assert client.get("/reservations?limit=1", headers=AUTH).status_code == 200


def test_legacystay_offset_pagination_shape() -> None:
    set_scenario("baseline", 42)
    client = TestClient(legacy_app)
    resp = client.get("/reservations?limit=5&offset=0", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 5
    assert "next_offset" in body
    assert body["next_offset"] == 5
    assert "total" in body
    assert "gross_sum" in body
    # Offset, not cursor
    assert "next_cursor" not in body
    # Second page differs; last page has null next_offset
    resp2 = client.get("/reservations?limit=5&offset=5", headers=AUTH)
    assert resp2.status_code == 200
    assert resp2.json()["data"] != body["data"]
    resp_last = client.get("/reservations?limit=100", headers=AUTH)
    assert resp_last.json()["next_offset"] is None
    # Folios also offset
    resp_f = client.get("/folios?limit=3&offset=0", headers=AUTH)
    assert "next_offset" in resp_f.json()
    assert "next_cursor" not in resp_f.json()
    assert "gross_sum" in resp_f.json()


def test_legacystay_cursor_branch_under_offset_to_cursor() -> None:
    # Fault-injected cursor drift is handled via the shared cursor paginator
    set_scenario("offset_to_cursor", 42)
    client = TestClient(legacy_app)
    resp = client.get("/reservations?limit=5", headers=AUTH)
    assert resp.status_code == 200
    assert "next_cursor" in resp.json()
    assert "next_offset" not in resp.json()
    set_scenario("baseline", 0)


def test_legacystay_plain_english_statuses() -> None:
    set_scenario("baseline", 42)
    client = TestClient(legacy_app)
    resp = client.get("/reservations?limit=100", headers=AUTH)
    assert resp.status_code == 200
    statuses = {r["status"] for r in resp.json()["data"]}
    assert statuses.issubset({"confirmed", "checked_in", "checked_out", "cancelled", "no_show"})
    # No opaque codes, no misleading `active` words in reality
    assert not statuses.intersection({"H1", "H2", "H3", "H4", "H5"})
    assert "active" not in statuses
    assert len(statuses) >= 2


def test_legacystay_ambiguous_date_field() -> None:
    set_scenario("baseline", 42)
    client = TestClient(legacy_app)
    resp = client.get("/reservations?limit=10", headers=AUTH)
    assert resp.status_code == 200
    for rec in resp.json()["data"]:
        assert "date" in rec
        assert rec["date"] == rec["arrival_date"]
    resp_f = client.get("/folios?limit=10", headers=AUTH)
    assert resp_f.status_code == 200
    for rec in resp_f.json()["data"]:
        assert "date" in rec
        assert rec["date"] == rec["business_date"]
    # Single-item fetches carry the ambiguous date too
    rid = resp.json()["data"][0]["reservation_id"]
    one = client.get(f"/reservations/{rid}", headers=AUTH).json()["data"]
    assert one["date"] == one["arrival_date"]
    cid = resp_f.json()["data"][0]["charge_id"]
    one_f = client.get(f"/folios/{cid}", headers=AUTH).json()["data"]
    assert one_f["date"] == one_f["business_date"]


def test_legacystay_nullable_chaos() -> None:
    set_scenario("baseline", 42)
    client = TestClient(legacy_app)
    resps = client.get("/reservations?limit=100", headers=AUTH).json()["data"]
    null_guests = sum(1 for r in resps if r["guest_name"] is None)
    # Deterministic extra nulling on top of the seeded 10%: a visible share is null
    assert null_guests > len(resps) // 10, f"expected nullable chaos, got {null_guests} nulls of {len(resps)}"
    folios = client.get("/folios?limit=100", headers=AUTH).json()["data"]
    null_descs = sum(1 for f in folios if f["description"] is None)
    assert null_descs > len(folios) // 10


def test_legacystay_baseline_throttle_every_eighth() -> None:
    set_scenario("baseline", 42)
    client = TestClient(legacy_app)
    seen_429_at: list[int] = []
    for i in range(1, THROTTLE_EVERY_N * 2 + 1):
        resp = client.get("/reservations?limit=1", headers=AUTH)
        if resp.status_code == 429:
            seen_429_at.append(i)
            assert resp.headers.get("retry-after") == "1"
    # Throttle fires on the 8th and 16th requests of a fresh count
    assert seen_429_at == [THROTTLE_EVERY_N, THROTTLE_EVERY_N * 2]


def test_legacystay_faults_still_work() -> None:
    client = TestClient(legacy_app)
    # Catalog rate-limit fault stacks on top of baseline throttle
    set_scenario("rate_limit_429", 42)
    codes = [client.get("/reservations?limit=2", headers=AUTH).status_code for _ in range(9)]
    assert 429 in codes
    # Auth faults
    set_scenario("token_expired", 42)
    assert client.get("/reservations", headers=AUTH).status_code == 401
    set_scenario("token_revoked", 42)
    assert client.get("/reservations", headers=AUTH).status_code == 403
    # Endpoint removed
    set_scenario("endpoint_removed", 42)
    assert client.get("/reservations", headers=AUTH).status_code == 404
    assert client.get("/folios", headers=AUTH).status_code == 404
    assert client.get("/properties", headers=AUTH).status_code == 200
    # Rename fault applies through the LegacyStay shape (guest_identifier, date kept)
    set_scenario("rename_guest_id", 42)
    resp = client.get("/reservations?limit=5", headers=AUTH)
    for rec in resp.json()["data"]:
        assert "guest_name" not in rec
        assert "guest_identifier" in rec
        assert rec["status"] in {"confirmed", "checked_in", "checked_out", "cancelled", "no_show"}
        assert "date" in rec


def test_legacystay_does_not_import_verifier_or_autobridge() -> None:
    import re

    text = (Path(__file__).resolve().parents[2] / "simulators/legacystay/app.py").read_text(encoding="utf-8")
    for m in re.finditer(r"^\s*(?:import|from)\s+(\S+)", text, re.MULTILINE):
        top = m.group(1).split(".")[0]
        assert top not in {"autobridge", "verifier"}, f"legacystay app imports {top}"


def test_legacystay_money_decimal_2dp() -> None:
    set_scenario("baseline", 42)
    client = TestClient(legacy_app)
    for rec in client.get("/reservations?limit=20", headers=AUTH).json()["data"]:
        assert Decimal(rec["total_amount"]).as_tuple().exponent == -2
    for rec in client.get("/folios?limit=20", headers=AUTH).json()["data"]:
        assert Decimal(rec["amount"]).as_tuple().exponent == -2


def test_legacystay_docs_alias_endpoint() -> None:
    client = TestClient(legacy_app)
    resp = client.get("/_legacystay_docs")
    assert resp.status_code == 200
    assert "LegacyStay API" in resp.text


def test_legacystay_docs_alias_404_when_missing(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from simulators.legacystay import app as mod

    monkeypatch.setattr(mod, "DOCS_DIR", Path("/tmp/not_exist_legacy_docs_xyz2"))
    client = TestClient(mod.app)
    resp = client.get("/_legacystay_docs")
    assert resp.status_code == 404


def test_legacystay_get_not_found() -> None:
    set_scenario("baseline", 42)
    client = TestClient(legacy_app)
    assert client.get("/reservations/DOES_NOT_EXIST", headers=AUTH).status_code == 404
    assert client.get("/folios/DOES_NOT_EXIST", headers=AUTH).status_code == 404


def test_legacystay_filter_by_property_and_reservation() -> None:
    set_scenario("baseline", 42)
    client = TestClient(legacy_app)
    props = client.get("/properties", headers=AUTH).json()["data"]
    pid = props[0]["property_id"]
    for rec in client.get(f"/reservations?property_id={pid}&limit=100", headers=AUTH).json()["data"]:
        assert rec["property_id"] == pid
    for rec in client.get(f"/folios?property_id={pid}&limit=100", headers=AUTH).json()["data"]:
        assert rec["property_id"] == pid
    rid = client.get(f"/reservations?property_id={pid}&limit=1", headers=AUTH).json()["data"][0]["reservation_id"]
    for rec in client.get(f"/folios?reservation_id={rid}&limit=100", headers=AUTH).json()["data"]:
        assert rec["reservation_id"] == rid


def test_legacystay_transform_helpers_direct() -> None:
    from simulators.legacystay.app import (
        _null_chaos,
        _to_legacystay_folio,
        _to_legacystay_reservation,
    )

    # None stays None
    assert _null_chaos(None, "salt") is None
    # Deterministic: same salt same result
    assert _null_chaos("Ada", "RES-1") == _null_chaos("Ada", "RES-1")
    # Reservation without arrival_date gets no date
    out = _to_legacystay_reservation({"reservation_id": "R1", "guest_name": "Ada"})
    assert "date" not in out
    # Folio without business_date gets no date
    fout = _to_legacystay_folio({"charge_id": "C1", "description": "x"})
    assert "date" not in fout
    # Full shapes carry the ambiguous date
    rec = {"reservation_id": "R2", "guest_name": "Ada", "arrival_date": "2026-10-01", "status": "confirmed"}
    assert _to_legacystay_reservation(rec)["date"] == "2026-10-01"
    fol = {"charge_id": "C2", "business_date": "2026-10-02", "description": "x"}
    assert _to_legacystay_folio(fol)["date"] == "2026-10-02"
