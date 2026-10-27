"""P1.W2.4: HarborPMS Markdown narrative docs, cursor bug, nested folios, opaque statuses, ambiguous date, readyz."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from simulators.common.state import set_scenario
from simulators.harborpms.app import DOCS_DIR, HARBOR_STATUS_MAP
from simulators.harborpms.app import app as harbor_app


def test_harbor_healthz_and_readyz_ok() -> None:
    client = TestClient(harbor_app)
    assert client.get("/healthz").status_code == 200
    assert client.get("/healthz").json() == {"status": "ok"}
    resp = client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["checks"]["default"] == "ok"
    assert body["checks"]["docs"] == "ok"
    assert "scenario" in body["checks"]


def test_harbor_readyz_degraded_when_docs_missing(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from simulators.harborpms import app as mod

    monkeypatch.setattr(mod, "DOCS_DIR", Path("/tmp/not_exist_harbor_docs_xyz"))
    client = TestClient(mod.app)
    resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["checks"]["docs"] == "missing"
    assert resp.json()["status"] == "degraded"


def test_harbor_markdown_docs_served() -> None:
    client = TestClient(harbor_app)
    # Static mount serves harbor.md
    resp = client.get("/docs/harbor.md")
    assert resp.status_code == 200
    text = resp.text
    assert "HarborPMS API" in text
    assert "GET /reservations" in text
    # harborpms.md alias
    resp2 = client.get("/docs/harborpms.md")
    assert resp2.status_code == 200
    # File on disk is byte-identical to served
    on_disk = (DOCS_DIR / "harbor.md").read_text(encoding="utf-8")
    assert resp.text == on_disk
    assert (DOCS_DIR / "harborpms.md").is_file()
    # Docs mention the wrong pagination example (offset) and the actual cursor note
    assert "next_offset" in text or "offset" in text.lower()
    assert "next_cursor" in text or "cursor" in text.lower()
    # Docs mention opaque codes H1..H5
    assert "H1" in text
    assert "opaque" in text.lower()


def test_harbor_markdown_has_wrong_example_and_undocumented_endpoint_note() -> None:
    text = (DOCS_DIR / "harbor.md").read_text(encoding="utf-8")
    # Wrong example: earlier draft showed Atlas English statuses; now docs note it was wrong
    assert "confirmed" in text.lower()  # the wrong example mentions Atlas vocab
    assert "H1" in text  # but real vocab is H1..H5
    # The example pagination in docs shows offset shape (wrong) while real is cursor
    # Confirm example json block contains offset pattern
    assert '"offset"' in text or "offset" in text.lower()
    assert '"next_offset"' in text or "next_offset" in text
    # Undocumented endpoint note: mentions GET /reservations/{id}/folios as not in guide
    assert "/reservations/{reservation_id}/folios" in text or "/reservations/{id}/folios" in text or "nested" in text.lower()
    assert "undocumented" in text.lower()


def test_harbor_markdown_docs_not_openapi() -> None:
    client = TestClient(harbor_app)
    # Harbor should not expose OpenAPI at /openapi.json (it's markdown, not OpenAPI)
    # Atlas does, Harbor does not
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/openapi.yaml").status_code == 404


def test_harbor_cursor_pagination_shape() -> None:
    set_scenario("baseline", 42)
    client = TestClient(harbor_app)
    resp = client.get("/reservations?limit=5&cursor=")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert len(body["data"]) == 5
    assert "next_cursor" in body
    assert "cursor" in body
    assert "total" in body
    # Should NOT have offset pagination keys as primary
    # Harbor default is cursor, not offset
    assert "next_cursor" in body
    # Use next_cursor to paginate
    nxt = body["next_cursor"]
    assert nxt is not None
    resp2 = client.get(f"/reservations?limit=5&cursor={nxt}")
    assert resp2.status_code == 200
    assert resp2.json()["data"] != body["data"]
    # Last page has null next_cursor
    resp_last = client.get("/reservations?limit=100")
    assert resp_last.json()["next_cursor"] is None
    # Folios also cursor
    resp_f = client.get("/folios?limit=3")
    assert "next_cursor" in resp_f.json()
    assert "next_offset" not in resp_f.json()


def test_harbor_opaque_status_codes() -> None:
    set_scenario("baseline", 42)
    client = TestClient(harbor_app)
    resp = client.get("/reservations?limit=100")
    assert resp.status_code == 200
    statuses = {r["status"] for r in resp.json()["data"]}
    # Harbor baseline must be opaque H1..H5, not Atlas clear vocabulary
    assert statuses.issubset({"H1", "H2", "H3", "H4", "H5"})
    assert statuses != {"confirmed", "checked_in", "checked_out", "cancelled", "no_show"}
    assert len(statuses) >= 2
    # Docs wrong example claimed "confirmed" but reality is H1
    # Also ensure every reservation has status in opaque set
    for rec in resp.json()["data"]:
        assert rec["status"] in HARBOR_STATUS_MAP.values()
    # Single get also opaque
    rid = resp.json()["data"][0]["reservation_id"]
    resp_one = client.get(f"/reservations/{rid}")
    assert resp_one.json()["data"]["status"] in HARBOR_STATUS_MAP.values()


def test_harbor_status_fault_overrides_opaque() -> None:
    # When fault status_vocab_change is active, fault mapping wins (active etc.)
    set_scenario("status_vocab_change", 42)
    client = TestClient(harbor_app)
    resp = client.get("/reservations?limit=20")
    assert resp.status_code == 200
    statuses = {r["status"] for r in resp.json()["data"]}
    assert statuses.issubset({"active", "in_house", "departed", "void", "noshow"})
    # Not harbor opaque in this scenario
    assert not statuses.intersection({"H1", "H2", "H3", "H4", "H5"})


def test_harbor_ambiguous_date_field() -> None:
    set_scenario("baseline", 42)
    client = TestClient(harbor_app)
    # Reservations have ambiguous `date` = arrival_date
    resp = client.get("/reservations?limit=10")
    assert resp.status_code == 200
    for rec in resp.json()["data"]:
        assert "date" in rec, "Harbor reservation must have ambiguous `date` field"
        assert "arrival_date" in rec
        assert rec["date"] == rec["arrival_date"]
        # date is property-local YYYY-MM-DD
        assert len(rec["date"]) == 10
        assert rec["date"][4] == "-" and rec["date"][7] == "-"
    # Single reservation also has ambiguous date
    rid = resp.json()["data"][0]["reservation_id"]
    one = client.get(f"/reservations/{rid}").json()["data"]
    assert one["date"] == one["arrival_date"]

    # Folios have ambiguous `date` = business_date
    resp_f = client.get("/folios?limit=10")
    assert resp_f.status_code == 200
    for rec in resp_f.json()["data"]:
        assert "date" in rec, "Harbor folio must have ambiguous `date` field"
        assert "business_date" in rec
        assert rec["date"] == rec["business_date"]
    # Single folio
    cid = resp_f.json()["data"][0]["charge_id"]
    one_f = client.get(f"/folios/{cid}").json()["data"]
    assert one_f["date"] == one_f["business_date"]


def test_harbor_nested_folios_endpoint_exists() -> None:
    set_scenario("baseline", 42)
    client = TestClient(harbor_app)
    # First get a reservation that has folios
    res_resp = client.get("/reservations?limit=1")
    rid = res_resp.json()["data"][0]["reservation_id"]
    # Nested endpoint should work
    resp = client.get(f"/reservations/{rid}/folios?limit=10")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert "next_cursor" in body
    assert "total" in body
    for folio in body["data"]:
        assert folio["reservation_id"] == rid
        assert "date" in folio  # ambiguous date still present on nested
        assert folio["date"] == folio["business_date"]
    # Cursor pagination on nested
    if body["next_cursor"] is not None:
        resp2 = client.get(f"/reservations/{rid}/folios?limit=10&cursor={body['next_cursor']}")
        assert resp2.status_code == 200


def test_harbor_nested_folios_undocumented_in_guide() -> None:
    text = (DOCS_DIR / "harbor.md").read_text(encoding="utf-8")
    # The flat /folios is documented
    assert "GET /folios" in text or "`/folios`" in text
    # But the nested endpoint is mentioned only as undocumented note, not as a documented endpoint listing?
    # We ensure docs contain phrase "not described" or "undocumented" near the nested path
    low = text.lower()
    assert "undocumented" in low
    # The nested path should appear at least once (in the undocumented note)
    assert "reservations/{reservation_id}/folios" in text or "reservations/{id}/folios" in text


def test_harbor_nested_folios_consistency_with_flat() -> None:
    set_scenario("baseline", 42)
    client = TestClient(harbor_app)
    res_resp = client.get("/reservations?limit=5")
    rids = [r["reservation_id"] for r in res_resp.json()["data"][:2]]
    for rid in rids:
        nested = client.get(f"/reservations/{rid}/folios?limit=100").json()["data"]
        flat = client.get(f"/folios?reservation_id={rid}&limit=100").json()["data"]
        # Same records (order may differ but sorted by charge_id in generator)
        nested_ids = sorted(f["charge_id"] for f in nested)
        flat_ids = sorted(f["charge_id"] for f in flat)
        assert nested_ids == flat_ids
        # Amounts are Decimal strings 2dp
        for rec in nested:
            assert "." in rec["amount"]
            from decimal import Decimal

            d = Decimal(rec["amount"])
            assert d.as_tuple().exponent == -2


def test_harbor_nested_folios_404_for_unknown_reservation() -> None:
    set_scenario("baseline", 42)
    client = TestClient(harbor_app)
    resp = client.get("/reservations/RES-DOES-NOT-EXIST/folios")
    assert resp.status_code == 404


def test_harbor_nested_folios_filters_and_pagination() -> None:
    set_scenario("baseline", 42)
    client = TestClient(harbor_app)
    rid = client.get("/reservations?limit=1").json()["data"][0]["reservation_id"]
    # Pagination limit respected
    resp = client.get(f"/reservations/{rid}/folios?limit=1")
    assert len(resp.json()["data"]) <= 1
    # Property filter via flat still works; nested is scoped to reservation already
    # Ensure nested respects auth/fault same as others
    set_scenario("endpoint_removed", 42)
    resp2 = client.get(f"/reservations/{rid}/folios?limit=10")
    assert resp2.status_code == 404


def test_harbor_property_local_dates() -> None:
    set_scenario("baseline", 42)
    client = TestClient(harbor_app)
    resps = client.get("/reservations?limit=50").json()["data"]
    for rec in resps:
        # arrival_date and departure_date are YYYY-MM-DD property-local
        assert len(rec["arrival_date"]) == 10
        assert len(rec["departure_date"]) == 10
        # departure after or equal arrival
        assert rec["departure_date"] >= rec["arrival_date"]
        # ambiguous date is same as arrival_date (property-local)
        assert rec["date"] == rec["arrival_date"]
    # Folios business_date vs posted_at: business_date is property-local
    folios = client.get("/folios?limit=20").json()["data"]
    for rec in folios:
        assert len(rec["business_date"]) == 10
        assert "posted_at" in rec
        assert rec["posted_at"].endswith("+00:00") or rec["posted_at"].endswith("Z") or "+" in rec["posted_at"]
        # business_date should be derivable from posted_at via cutoff (tested in generator)
        # Here just ensure date == business_date
        assert rec["date"] == rec["business_date"]


def test_harbor_faults_still_work() -> None:
    # Verify Harbor still respects all fault mutations via helpers
    client = TestClient(harbor_app)
    # rate limit
    set_scenario("rate_limit_429", 42)
    assert client.get("/reservations?limit=2").status_code == 200
    assert client.get("/reservations?limit=2").status_code == 200
    assert client.get("/reservations?limit=2").status_code == 429
    # token expired/revoked
    set_scenario("token_expired", 42)
    assert client.get("/reservations").status_code == 401
    set_scenario("token_revoked", 42)
    assert client.get("/reservations").status_code == 403
    # endpoint removed
    set_scenario("endpoint_removed", 42)
    assert client.get("/reservations").status_code == 404
    assert client.get("/folios").status_code == 404
    assert client.get("/properties").status_code == 200
    # offset_to_cursor for Harbor should stay cursor (no visible change for harbor)
    set_scenario("offset_to_cursor", 42)
    resp = client.get("/reservations?limit=3")
    assert "next_cursor" in resp.json()
    # rename_guest_id still applies (even though harbor has opaque statuses, guest_name rename should still happen)
    set_scenario("rename_guest_id", 42)
    resp = client.get("/reservations?limit=5")
    for rec in resp.json()["data"]:
        assert "guest_name" not in rec
        assert "guest_identifier" in rec
        # still has opaque status and ambiguous date when not status fault
        # but rename scenario doesn't affect status, so status should still be opaque? Let's check
        # Harbor mapping only applies if status is canonical; rename doesn't change status, so still H1..H5
        # However dataset after rename still has canonical statuses, so harbor will map to H1
        assert rec["status"] in HARBOR_STATUS_MAP.values()
        assert "date" in rec


def test_harbor_does_not_import_verifier_or_autobridge() -> None:
    import re

    text = (Path(__file__).resolve().parents[2] / "simulators/harborpms/app.py").read_text(encoding="utf-8")
    for m in re.finditer(r"^\s*(?:import|from)\s+(\S+)", text, re.MULTILINE):
        top = m.group(1).split(".")[0]
        assert top not in {"autobridge", "verifier"}, f"harbor app imports {top}"


def test_harbor_money_decimal_2dp() -> None:
    set_scenario("baseline", 42)
    client = TestClient(harbor_app)
    resp = client.get("/reservations?limit=20")
    for rec in resp.json()["data"]:
        amt = rec["total_amount"]
        assert "." in amt
        from decimal import Decimal

        assert Decimal(amt).as_tuple().exponent == -2
    resp_f = client.get("/folios?limit=20")
    for rec in resp_f.json()["data"]:
        assert Decimal(rec["amount"]).as_tuple().exponent == -2


def test_harbor_docs_alias_endpoint() -> None:
    client = TestClient(harbor_app)
    resp = client.get("/_harbor_docs")
    assert resp.status_code == 200
    assert "HarborPMS API" in resp.text
    assert "H1" in resp.text


def test_harbor_docs_alias_404_when_missing(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from simulators.harborpms import app as mod

    monkeypatch.setattr(mod, "DOCS_DIR", Path("/tmp/not_exist_harbor_docs_xyz2"))
    client = TestClient(mod.app)
    resp = client.get("/_harbor_docs")
    assert resp.status_code == 404


def test_harbor_offset_branch_via_mock(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Force harbor to use offset pagination branch (covers dead code)
    set_scenario("baseline", 42)
    from simulators.harborpms import app as mod

    monkeypatch.setattr(mod, "pagination_mode_for_scenario", lambda scenario_id, default="cursor": "offset")  # noqa: ARG005
    client = TestClient(mod.app)
    resp = client.get("/reservations?limit=5&offset=0")
    assert resp.status_code == 200
    assert "next_offset" in resp.json()
    assert "next_cursor" not in resp.json()
    resp_f = client.get("/folios?limit=5&offset=0")
    assert "next_offset" in resp_f.json()
    rid = client.get("/reservations?limit=1&offset=0").json()["data"][0]["reservation_id"]
    # Need to restore cursor mode for nested? Force offset again
    resp_n = client.get(f"/reservations/{rid}/folios?limit=5&offset=0")
    assert resp_n.status_code == 200
    assert "next_offset" in resp_n.json()


def test_harbor_get_not_found() -> None:
    set_scenario("baseline", 42)
    client = TestClient(harbor_app)
    assert client.get("/reservations/DOES_NOT_EXIST").status_code == 404
    assert client.get("/folios/DOES_NOT_EXIST").status_code == 404


def test_harbor_filter_by_property_id() -> None:
    set_scenario("baseline", 42)
    client = TestClient(harbor_app)
    props = client.get("/properties").json()["data"]
    pid = props[0]["property_id"]
    resp = client.get(f"/reservations?property_id={pid}&limit=100")
    assert resp.status_code == 200
    for rec in resp.json()["data"]:
        assert rec["property_id"] == pid
    resp_f = client.get(f"/folios?property_id={pid}&limit=100")
    assert resp_f.status_code == 200
    for rec in resp_f.json()["data"]:
        assert rec["property_id"] == pid


def test_harbor_transform_helpers_direct() -> None:
    # Directly test the helper mapping for coverage (and missing branch where business_date missing)
    from simulators.harborpms.app import (
        _harbor_transform_dataset,
        _to_harbor_folio,
        _to_harbor_reservation,
    )

    # Reservation with canonical status maps to H1
    rec = {"reservation_id": "R1", "property_id": "prop-001", "status": "confirmed", "arrival_date": "2026-10-01"}
    out = _to_harbor_reservation(rec)
    assert out["status"] == "H1"
    assert out["date"] == "2026-10-01"
    # Status already opaque fault vocab should not be remapped
    rec2 = {"reservation_id": "R2", "property_id": "prop-001", "status": "active", "arrival_date": "2026-10-01"}
    out2 = _to_harbor_reservation(rec2)
    assert out2["status"] == "active"
    # Folio with business_date
    folio = {"charge_id": "C1", "reservation_id": "R1", "property_id": "prop-001", "business_date": "2026-10-02", "amount": "10.00"}
    fout = _to_harbor_folio(folio)
    assert fout["date"] == "2026-10-02"
    # Folio without business_date (edge) should not add date
    folio2 = {"charge_id": "C2", "reservation_id": "R1", "property_id": "prop-001", "amount": "10.00"}
    fout2 = _to_harbor_folio(folio2)
    assert "date" not in fout2
    # Reservation without arrival_date
    rec3 = {"reservation_id": "R3", "property_id": "prop-001", "status": "H1"}
    out3 = _to_harbor_reservation(rec3)
    assert "date" not in out3
    # Dataset helper
    ds = {"properties": [{"property_id": "prop-001"}], "reservations": [rec], "folios": [folio]}
    transformed = _harbor_transform_dataset(ds)
    assert transformed["reservations"][0]["status"] == "H1"
    assert transformed["folios"][0]["date"] == "2026-10-02"
