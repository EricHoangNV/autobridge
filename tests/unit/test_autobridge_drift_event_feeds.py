"""Event-feed layout drift detection (generic EVENT transport)."""

from __future__ import annotations

from autobridge.drift.event_feeds import detect_feed_field_rename, mapped_wire_ids_for_feed


def _fias_like_spec() -> dict[str, object]:
    """Minimal EVENT-feed spec shaped like the discovered FIAS connector."""
    return {
        "connector": {"id": "fiaspabx", "vendor": "fias"},
        "source": {"protocol": "fias_tcp", "base_url": "fias://listen:5010"},
        "operations": {
            "feed_ps": {"method": "EVENT", "feed": {"record": "PS", "via": "edge_relay"}},
            "wakeup_set": {"method": "EVENT", "record": "WR"},
        },
        "entities": {
            "FolioCharge": {
                "from": "feed_ps",
                "key": "$.P#",
                "fields": {
                    "charge_id": {"paths": ["$.RN", "$.P#"]},
                    "reservation_id": {"path": "$.RN"},
                    "amount": {
                        "path": "$.TA",
                        "transform": [{"name": "money", "args": {"minor_units": True}}],
                    },
                    "posted_at": {"paths": ["$.DA", "$.TI"]},
                    "note": {"transform": [{"name": "const", "args": {"value": "x"}}]},
                },
            }
        },
    }


BASELINE_PS = ["RN", "P#", "DA", "TI", "TA", "DD", "DU", "SO"]
V3_PS = ["P#", "RN", "TI", "DA", "SO", "TM", "DU", "DD", "CX"]


def _v3_samples() -> list[dict[str, str]]:
    return [
        {"P#": "000001", "RN": "101", "TI": "120000", "DA": "261028",
         "SO": "TEL", "TM": "1250", "DU": "000312", "DD": "004930", "CX": "VOICE"},
        {"P#": "000002", "RN": "102", "TI": "121500", "DA": "261028",
         "SO": "MINI", "TM": "450", "DU": "", "DD": "", "CX": "SNACK"},
    ]


def test_mapped_wire_ids_single_path_only() -> None:
    mapped = mapped_wire_ids_for_feed(_fias_like_spec(), "PS")
    assert mapped == {"reservation_id": "RN", "amount": "TA"}
    assert mapped_wire_ids_for_feed(_fias_like_spec(), "WR") == {}
    assert mapped_wire_ids_for_feed(_fias_like_spec(), "NOPE") == {}
    assert mapped_wire_ids_for_feed({}, "PS") == {}


def test_baseline_layout_no_drift() -> None:
    report = detect_feed_field_rename(_fias_like_spec(), "PS", BASELINE_PS, _v3_samples())
    assert report["drift_found"] is False
    assert report["missing_wire"] == []
    assert report["rename"] is None
    assert report["delta"] is None


def test_reorder_and_pure_addition_do_not_trip_repair() -> None:
    # v2 firmware: reorder + CX added, every mapped id still announced.
    report = detect_feed_field_rename(
        _fias_like_spec(), "PS", ["P#", "RN", "TI", "DA", "SO", "TA", "DU", "DD", "CX"], None
    )
    assert report["drift_found"] is False
    assert report["added_wire"] == ["CX", "DA", "DD", "DU", "P#", "SO", "TI"]
    assert report["rename"] is None


def test_v3_rename_detected_with_money_shape() -> None:
    report = detect_feed_field_rename(_fias_like_spec(), "PS", V3_PS, _v3_samples())
    assert report["drift_found"] is True
    assert report["missing_wire"] == ["TA"]
    assert report["added_wire"] == ["CX", "DA", "DD", "DU", "P#", "SO", "TI", "TM"]
    assert report["rename"] == {
        "entity": "FolioCharge",
        "canonical_field": "amount",
        "old_wire": "TA",
        "new_wire": "TM",
        "new_path": "$.TM",
    }
    delta = report["delta"]
    assert delta is not None
    assert delta.types_changed is True
    assert delta.types_details["FolioCharge"]["previous"]["TA"] == "str"
    assert delta.types_details["FolioCharge"]["current"]["TM"] == "str"
    extra = report["extra"]
    assert extra["rename"] == {
        "entity": "FolioCharge",
        "old_field": "amount",
        "new_field": "TM",
        "new_path": "$.TM",
    }


def test_single_added_without_missing_is_not_drift() -> None:
    report = detect_feed_field_rename(_fias_like_spec(), "PS", BASELINE_PS + ["TM"], None)
    assert report["missing_wire"] == []
    # Nothing missing: no drift, even though an id was added.
    assert report["drift_found"] is False


def test_ambiguous_added_without_shape_stays_unpaired() -> None:
    announced = [fid for fid in BASELINE_PS if fid != "TA"] + ["TM", "CX", "ZZ"]
    samples = [dict(s, TM="VOICE", CX="1250", ZZ="1250") for s in _v3_samples()]
    report = detect_feed_field_rename(_fias_like_spec(), "PS", announced, samples)
    assert report["drift_found"] is True
    assert report["missing_wire"] == ["TA"]
    # TM is not money-shaped here and CX/ZZ both are: ambiguous, no auto-pair.
    assert report["rename"] is None
    assert report["delta"] is not None


def test_single_swap_pairs_without_samples() -> None:
    # Minimal feed: exactly one mapped id swapped for one new id.
    report = detect_feed_field_rename(_fias_like_spec(), "PS", ["TA", "RM"], None)
    assert report["drift_found"] is True
    assert report["missing_wire"] == ["RN"]
    assert report["added_wire"] == ["RM"]
    assert report["rename"] == {
        "entity": "FolioCharge",
        "canonical_field": "reservation_id",
        "old_wire": "RN",
        "new_wire": "RM",
        "new_path": "$.RM",
    }


def test_bracket_notation_path_counts_as_consumed() -> None:
    """$['P#'] (bracket form for '#') is the same wire id as $.P#."""
    from autobridge.drift.event_feeds import _consumed_wire_ids, _wire_id_of

    assert _wire_id_of("$.TA") == "TA"
    assert _wire_id_of("$['P#']") == "P#"
    assert _wire_id_of("$.a.b") is None
    consumed = _consumed_wire_ids({"charge_id": {"paths": ["$.RN", "$['P#']"]}})
    assert consumed == {"RN", "P#"}


def test_unknown_record_type_is_not_drift() -> None:
    report = detect_feed_field_rename(_fias_like_spec(), "ZZ", ["AA", "BB"], None)
    assert report["mapped_wire_ids"] == {}
    assert report["entity"] is None
    assert report["drift_found"] is False
    assert report["rename"] is None
