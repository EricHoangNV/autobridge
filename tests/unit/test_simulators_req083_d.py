"""REQ-083 D: simulator realism + IP fictionalization.

- D17: harbor/legacystay nest the projected wire record (quirks intact
  under nesting), so a nesting repair is expressible there too.
- D18: meridian never draws dates/format (UTC-instant vendor).
- D19: no real product or people names in docs, targets or the generator.
"""

from __future__ import annotations


def test_harbor_legacystay_nesting_keeps_quirks() -> None:
    """Nested harbor serves H1 + date; nested legacystay keeps its shape."""
    from simulators.harborpms.app import _to_harbor_folio, _to_harbor_reservation
    from simulators.legacystay.app import _to_legacystay_folio, _to_legacystay_reservation

    res = {
        "reservation_id": "R1", "property_id": "p1", "guest_name": "Ava Lindqvist",
        "arrival_date": "2026-10-01", "departure_date": "2026-10-03",
        "status": "confirmed", "total_amount": "100.00",
    }
    nested = {"localInfo": dict(res)}
    wire = _to_harbor_reservation(nested)
    assert set(wire) == {"localInfo"}, wire
    inner = wire["localInfo"]
    assert inner["status"] == "H1", inner
    assert inner["date"] == "2026-10-01", inner

    leg = _to_legacystay_reservation(nested)
    assert set(leg) == {"localInfo"}, leg
    assert leg["localInfo"]["date"] == "2026-10-01", leg

    fol = {"charge_id": "C1", "business_date": "2026-10-02", "amount": "10.00"}
    fw = _to_harbor_folio({"folioInfo": dict(fol)})
    assert set(fw) == {"folioInfo"} and fw["folioInfo"]["date"] == "2026-10-02", fw
    lw = _to_legacystay_folio({"folioInfo": dict(fol)})
    assert set(lw) == {"folioInfo"} and lw["folioInfo"]["date"] == "2026-10-02", lw


def test_meridian_never_draws_dates_format() -> None:
    """REQ-083 D18: no meridian dates/format instance in a wide draw."""
    import simulators.common.faultgen as faultgen

    seen = 0
    for raw in ("d0" * 32, "d1" * 32, "d2" * 32):
        for inst in faultgen.generate_instances(bytes.fromhex(raw), 60):
            if str(inst["vendor"]) != "meridian" or str(inst["family"]) != "dates":
                continue
            seen += 1
            assert str(dict(inst["params"]).get("mode", "")) != "format", inst
    assert seen > 0, "no meridian dates instances drawn at all"


def test_no_real_product_or_people_names() -> None:
    """REQ-083 D19: docs, demo targets and the generator name no real
    products or people."""
    import pathlib

    repo = pathlib.Path(__file__).resolve().parents[2]
    fias_doc = (repo / "simulators" / "docs" / "fias-pabx.md").read_text(encoding="utf-8")
    assert "Fidelio Interface Application Specification" not in fias_doc
    targets = (repo / "config" / "demo_targets.yaml").read_text(encoding="utf-8")
    assert "Cloudbeds" not in targets and "Mews" not in targets
    from simulators.common import generator as gen

    blob = " ".join(gen.GUEST_NAMES)
    for real in ("Lovelace", "Hopper", "Turing", "Liskov", "Knuth", "Torvalds",
                 "Berners-Lee", "Lamarr", "Johnson", "Sandberg", "van Rossum"):
        assert real not in blob, real


def test_backfill_skips_unparseable_amounts() -> None:
    from simulators.common.generator import backfill_reservation_totals

    reservations: list[dict] = [{"reservation_id": "R1", "total_amount": "0.00"}]
    folios: list[dict] = [
        {"reservation_id": "R1", "amount": "10.00"},
        {"reservation_id": "R1", "amount": "oops"},
        {"reservation_id": "R2", "amount": "5.00"},
    ]
    backfill_reservation_totals(reservations, folios)  # type: ignore[arg-type]
    assert reservations[0]["total_amount"] == "10.00"
