"""REQ-022 sub-step C — ConnectorSpec v3: event-driven (FIAS) vocabulary.

Covers, without any stack: the v3 spec models (``protocol: fias_tcp``,
link settings, EVENT feeds, multi-path fields, command templates,
``edge_relay`` auth) with the v1/v2 vocabularies frozen; the closed-set
transform extensions (``money`` minor units, wire date/time, ``naive_as``);
the FIAS docs parse; the v1/v2 refusal (``Blocked``-named, 0 model calls)
and the v3 compile; in-process sync ``Complete`` over scripted feeds with
resend folding; repair rules applying to v3 specs; and the spec→bundle
conversion. Live PABX/agent coverage stays in ``tests/e2e/``.
"""

from __future__ import annotations

import copy
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from autobridge.discovery.bootstrap import build_draft_spec
from autobridge.discovery.docs_reader import read_docs_file
from autobridge.engine.sync_run import execute_sync
from autobridge.repair.context import RepairContext
from autobridge.repair.rules import rename_field
from autobridge.runtime.feeds import FeedError, RelayFeedReader, fold_resends
from autobridge.runtime.transforms import apply_transform
from autobridge.spec.models import (
    load_spec_from_dict,
    spec_to_json_data,
)
from autobridge.spec.patch import apply_patch
from autobridge.spec.registry import spec_signature

REPO_ROOT = Path(__file__).resolve().parents[2]
FIAS_MD_PATH = REPO_ROOT / "simulators" / "docs" / "fias-pabx.md"
FIAS_MD = FIAS_MD_PATH.read_text(encoding="utf-8")

BASE_OP = {"method": "GET", "path": "/v2/bookings"}


def _v1_base(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "spec_version": 1,
        "connector": {"id": "probe", "vendor": "Probe", "runtime_target": "cloud"},
        "source": {
            "base_url": "https://api.example.com",
            "auth": {"type": "bearer", "credential_ref": "PROBE_TOKEN"},
            "timezone_source": {"kind": "static", "value": "America/New_York"},
        },
        "operations": {"list_things": dict(BASE_OP)},
        "entities": {
            "Reservation": {
                "from": "list_things",
                "key": "$.id",
                "fields": {"reservation_id": {"path": "$.id"}},
                "checks": [],
            }
        },
        "capabilities": {"read": ["Reservation"], "action": []},
        "health": {"canary": {"every": "60s", "operation": "list_things", "max_pages": 1}},
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# v1/v2 vocabularies stay frozen: every v3 construct needs spec_version 3
# ---------------------------------------------------------------------------


def test_v1_rejects_every_v3_construct() -> None:
    base = _v1_base()
    cases = {
        # Consistent fias sources: the v1 gate (not the shape check) refuses.
        "protocol": lambda d: d["source"].update(
            {"protocol": "fias_tcp", "base_url": "fias://listen:5010",
             "link": {"announced_records": ["PS"]}}
        ),
        "link": lambda d: d["source"].update(
            {"protocol": "fias_tcp", "base_url": "fias://listen:5010",
             "link": {"announced_records": ["PS"]}}
        ),
        "event_op": lambda d: d["operations"].update(
            {"feed_ps": {"method": "EVENT", "feed": {"record": "PS"}}}
        ),
        "edge_relay": lambda d: d["source"]["auth"].update(
            {"type": "edge_relay", "credential_ref": "RELAY_TOKEN"}
        ),
        "paths": lambda d: d["entities"]["Reservation"]["fields"].update(
            {"combo": {"paths": ["$.a", "$.b"]}}
        ),
        "commands": lambda d: d.update(
            {"commands": {"wr": {"record": "WR", "fields": {"RN": "{room_number}"}}}}
        ),
    }
    for mutate in cases.values():
        bad = copy.deepcopy(base)
        mutate(bad)
        with pytest.raises(ValidationError, match="spec_version 3"):
            load_spec_from_dict(bad)


def test_v2_rejects_every_v3_construct() -> None:
    base = _v1_base(spec_version=2)
    bad = copy.deepcopy(base)
    bad["source"].update(
        {
            "protocol": "fias_tcp",
            "base_url": "fias://listen:5010",
            "auth": {"type": "edge_relay", "credential_ref": "RELAY_TOKEN"},
            "link": {"announced_records": ["PS"]},
        }
    )
    bad["operations"] = {"feed_ps": {"method": "EVENT", "feed": {"record": "PS"}}}
    bad["entities"] = {
        "FolioCharge": {
            "from": "feed_ps",
            "key": "$.P",
            "fields": {"charge_id": {"paths": ["$.P"]}},
            "checks": [],
        }
    }
    bad["capabilities"] = {"read": ["FolioCharge"], "action": []}
    bad["health"] = {"canary": {"every": "60s", "operation": "feed_ps", "max_pages": 1}}
    bad["commands"] = {"wr": {"record": "WR", "fields": {"RN": "{room_number}"}}}
    with pytest.raises(ValidationError, match="spec_version 3"):
        load_spec_from_dict(bad)


def test_fias_url_shape_checked_against_protocol() -> None:
    bad = _v1_base(spec_version=3)
    bad["source"]["base_url"] = "fias://listen:5010"
    with pytest.raises(ValidationError, match="protocol"):
        load_spec_from_dict(bad)
    bad = _v1_base(spec_version=3)
    bad["source"].update({"protocol": "fias_tcp", "link": {"announced_records": ["PS"]}})
    with pytest.raises(ValidationError, match="fias://"):
        load_spec_from_dict(bad)


# ---------------------------------------------------------------------------
# Signatures: v1/v2 dumps ignore defaulted v3 keys; v3 changes move them
# ---------------------------------------------------------------------------


def test_v1_signature_ignores_defaulted_v3_keys() -> None:
    from autobridge.spec.models import signature_canonical_data

    dumped = spec_to_json_data(load_spec_from_dict(_v1_base()))
    padded = copy.deepcopy(dumped)
    padded["source"]["protocol"] = "http"
    padded["source"]["link"] = None
    padded["operations"]["list_things"]["feed"] = None
    padded["operations"]["list_things"]["path"] = "/v2/bookings"
    padded["entities"]["Reservation"]["fields"]["reservation_id"]["paths"] = None
    padded["commands"] = {}
    assert spec_signature(padded) == spec_signature(dumped)
    assert signature_canonical_data(padded) == signature_canonical_data(dumped)


def test_v3_field_change_moves_signature() -> None:
    result = build_draft_spec(
        FIAS_MD, source="simulators/docs/fias-pabx.md", connector_id="fiaspabx", spec_vocabulary="v3"
    )
    assert result.success, result.visible_errors
    base = dict(result.draft_spec)
    base_sig = spec_signature(load_spec_from_dict(base))

    def mutated(path: str, value: Any) -> str:
        patched = apply_patch(copy.deepcopy(base), [{"op": "replace", "path": path, "value": value}])
        return spec_signature(load_spec_from_dict(patched))

    assert mutated("/operations/feed_ps/feed/record", "RE") != base_sig
    assert mutated("/source/link/ack_mode", False) != base_sig
    assert mutated("/commands/wr/record", "WC") != base_sig
    assert mutated("/entities/FolioCharge/fields/amount/path", "$['TX']") != base_sig


# ---------------------------------------------------------------------------
# Transforms: minor units, wire dates, naive_as
# ---------------------------------------------------------------------------


def test_money_minor_units() -> None:
    assert apply_transform("1250", "money", {"scale": 2, "minor_units": True}) == Decimal("12.50")
    assert apply_transform("899", "money", {"scale": 2, "minor_units": True}) == Decimal("8.99")
    # Default unchanged: major units.
    assert apply_transform("1250", "money", {"scale": 2}) == Decimal("1250.00")
    assert apply_transform(Decimal("1250"), "money", {"scale": 2, "minor_units": True}) == Decimal("12.50")
    with pytest.raises(Exception, match="minor_units must be bool"):
        apply_transform("1250", "money", {"scale": 2, "minor_units": "yes"})


def test_wire_datetime_business_date_is_property_local() -> None:
    tz = {"tz": "America/New_York", "naive_as": "source"}
    # 23:15 NY Oct 28 -> business Oct 28 (cutoff 04:00).
    assert (
        apply_transform("261028 231504", "business_date", tz, context_tz="America/New_York")
        == "2026-10-28"
    )
    # 02:30 NY Oct 29 -> before cutoff -> business Oct 28.
    assert (
        apply_transform("261029 023000", "business_date", tz, context_tz="America/New_York")
        == "2026-10-28"
    )
    # Run-together digits work too.
    assert (
        apply_transform("261029023000", "business_date", tz, context_tz="America/New_York")
        == "2026-10-28"
    )
    # Bare wire date, interpreted in the source timezone.
    assert (
        apply_transform("261028", "date_local", {"tz": "America/New_York", "naive_as": "source"},
                        context_tz="UTC")
        == "2026-10-28"
    )
    # Impossible calendar values fall through (legacy lenient behavior).
    assert apply_transform("991399", "business_date", tz, context_tz="America/New_York") == "991399"


def test_wire_posted_at_is_aware_utc() -> None:
    out = apply_transform(
        "261028 231504", "datetime_utc", {"tz": "source"}, context_tz="America/New_York"
    )
    assert out == "2026-10-29T03:15:04+00:00"


def test_naive_as_rejects_garbage() -> None:
    with pytest.raises(Exception, match="naive_as"):
        apply_transform("261028 231504", "business_date", {"naive_as": "tokyo"})


def test_multipath_concat_mapping_with_hash_ids() -> None:
    from autobridge.runtime.mapping import map_record_to_entity

    rec = {"RN": "101", "P#": "000417", "DA": "261028", "TI": "231504"}
    ent = {
        "fields": {
            "charge_id": {
                "paths": ["$.RN", "$['P#']", "$.DA", "$.TI"],
                "transform": [{"name": "concat", "args": {"separator": "-"}}],
            }
        }
    }
    assert map_record_to_entity(rec, ent) == {"charge_id": "101-000417-261028-231504"}


# ---------------------------------------------------------------------------
# Docs parse: protocol, link, site facts, records, answers
# ---------------------------------------------------------------------------


def test_fias_docs_parse_protocol_link_and_site() -> None:
    iface = read_docs_file(str(FIAS_MD_PATH))
    assert iface.protocol == "fias_tcp"
    assert iface.link is not None
    assert iface.link.ack_mode is True
    assert iface.link.max_frame_bytes == 65536
    assert set(iface.link.announced_records) == {"PS", "RE"}
    assert iface.link.listen_port == 5010
    assert iface.property_code == "prop-001"
    assert iface.currency_code == "USD"


def test_fias_docs_parse_records_and_answers() -> None:
    iface = read_docs_file(str(FIAS_MD_PATH))
    by_id = {op.operation_id: op for op in iface.operations}
    assert by_id["fias_ps"].raw["kind"] == "feed"
    assert set(by_id["fias_ps"].raw["fields"]) >= {"RN", "P#", "DA", "TI", "TA", "SO"}
    assert by_id["fias_re"].raw["kind"] == "feed"
    assert by_id["fias_wa"].raw["kind"] == "answer"
    assert by_id["fias_dr"].raw["kind"] == "control"
    assert by_id["fias_kr"].raw["kind"] == "command"
    assert by_id["fias_kr"].raw["answer"] == "KA"
    assert by_id["fias_kd"].raw["answer"] == "KA"
    assert by_id["fias_kz"].raw["answer"] == "KZ"
    # Conditional answers stay unmarked (scenario tracks them, not templates).
    assert "answer" not in by_id["fias_wr"].raw
    assert set(by_id["fias_gi"].raw["fields"]) >= {"RN", "G#", "GN", "DA", "TI"}


def test_non_fias_docs_stay_http() -> None:
    iface = read_docs_file("simulators/docs/harbor.md")
    assert iface.protocol == "http"
    assert iface.link is None


# ---------------------------------------------------------------------------
# Bootstrap: v1/v2 refuse, v3 compiles a valid spec
# ---------------------------------------------------------------------------


def test_fias_v1_and_v2_refuse_with_named_reason_zero_calls() -> None:
    for vocab in ("v1", "v2"):
        result = build_draft_spec(
            FIAS_MD, source="simulators/docs/fias-pabx.md", connector_id="fiaspabx",
            spec_vocabulary=vocab,
        )
        assert not result.success
        assert result.draft_spec is None
        assert "fias_tcp" in result.degradation_reason
        assert f"ConnectorSpec {vocab}" in result.degradation_reason
        assert result.model_usage.get("calls", 0) == 0
        assert result.model_usage.get("frontier_tokens", 0) == 0
        assert result.model_usage.get("small_tokens", 0) == 0
        assert any(t.get("stage") == "vocabulary_guard" for t in result.trace)


def test_fias_v3_compiles_valid_spec() -> None:
    result = build_draft_spec(
        FIAS_MD, source="simulators/docs/fias-pabx.md", connector_id="fiaspabx",
        spec_vocabulary="v3",
    )
    assert result.success, result.visible_errors
    spec = load_spec_from_dict(result.draft_spec)
    assert spec.spec_version == 3
    assert spec.source.protocol == "fias_tcp"
    assert spec.source.base_url == "fias://listen:5010"
    assert spec.connector.runtime_target == "edge"
    assert spec.source.auth.type == "edge_relay"
    assert set(spec.operations) == {"feed_ps", "feed_re"}
    assert set(spec.entities) == {"FolioCharge", "RoomStatus"}
    assert len(spec.commands) == 10
    folio = spec.entities["FolioCharge"]
    assert folio.fields["amount"].transform[0].args["minor_units"] is True
    assert spec_to_json_data(spec)  # dump round-trips


# ---------------------------------------------------------------------------
# Sync: Complete over scripted feeds, resends folded, feed errors Blocked
# ---------------------------------------------------------------------------

PS_RECORDS = [
    {"RN": "101", "P#": "000417", "DA": "261028", "TI": "231504",
     "TA": "1250", "DD": "004930XXXXXX", "DU": "000312", "SO": "TEL"},
    # The PABX resends until PA: the same instance arrives twice.
    {"RN": "101", "P#": "000417", "DA": "261028", "TI": "231504",
     "TA": "1250", "DD": "004930XXXXXX", "DU": "000312", "SO": "TEL"},
    {"RN": "102", "P#": "000418", "DA": "261029", "TI": "023000", "TA": "899", "SO": "MINI"},
]
RE_RECORDS = [{"RN": "101", "RS": "3", "DA": "261028", "TI": "180000"}]


class _ScriptFeed:
    def __init__(self, by_record: dict[str, list[dict[str, Any]]]) -> None:
        self.by_record = by_record

    def read(self, feed: Any, operation_name: str) -> list[dict[str, Any]]:
        rec = feed["record"] if isinstance(feed, dict) else feed.record
        records = self.by_record.get(rec)
        if records is None:
            raise KeyError(f"unknown feed {rec!r} for {operation_name!r}")
        return [dict(r) for r in records]


def _fias_spec_dict() -> dict[str, Any]:
    result = build_draft_spec(
        FIAS_MD, source="simulators/docs/fias-pabx.md", connector_id="fiaspabx",
        spec_vocabulary="v3",
    )
    assert result.success, result.visible_errors
    return dict(result.draft_spec)


def test_fias_sync_complete_minor_units_and_business_dates() -> None:
    out = execute_sync(
        _fias_spec_dict(), None,
        feed_reader=_ScriptFeed({"PS": PS_RECORDS, "RE": RE_RECORDS}),
    )
    assert out["outcome"] == "Complete", out.get("unmet_criteria")
    folios = out["canonical_records"]["FolioCharge"]
    assert len(folios) == 2  # resend folded, not doubled
    first, second = folios
    assert first["charge_id"] == "101-000417-261028-231504"
    assert first["amount"] == Decimal("12.50")
    assert first["posted_at"] == "2026-10-29T03:15:04+00:00"
    assert first["business_date"] == "2026-10-28"
    assert first["currency"] == "USD" and first["property_id"] == "prop-001"
    # 02:30 local is before the 04:00 cutoff -> prior business date.
    assert second["amount"] == Decimal("8.99")
    assert second["business_date"] == "2026-10-28"
    rooms = out["canonical_records"]["RoomStatus"]
    assert rooms == [{"room_number": "101", "status_code": "3",
                      "observed_at": "2026-10-28T22:00:00+00:00"}]


def test_fias_sync_without_reader_is_blocked_not_crash() -> None:
    out = execute_sync(_fias_spec_dict(), None, feed_reader=None)
    assert out["outcome"] == "Blocked"
    assert out["unmet_criteria"] == ["feed_unavailable"]


def test_fold_resends_keeps_first_per_key() -> None:
    rows = [
        {"P#": "1", "RN": "101"},
        {"P#": "1", "RN": "101"},
        {"P#": "2", "RN": "101"},
        {"RN": "102"},  # missing a key field: never folded away
        {"RN": "102"},
    ]
    folded = fold_resends(rows, ["P#", "RN"])
    assert folded == [rows[0], rows[2], rows[3], rows[4]]


# ---------------------------------------------------------------------------
# Relay feed reader: filters by record type, fails closed without env
# ---------------------------------------------------------------------------


def test_relay_feed_reader_filters_and_dedupes(monkeypatch: pytest.MonkeyPatch) -> None:
    from autobridge.edge.relay import _Store

    store = _Store(":memory:")
    assert store.store_posting("a1", "k1", "PS", '{"RN": "101", "P#": "1"}', None)
    assert store.store_posting("a1", "k2", "RE", '{"RN": "101", "RS": "3"}', None)
    assert not store.store_posting("a1", "k1", "PS", '{"RN": "101", "P#": "1"}', None)

    class _FakeResponse:
        def __init__(self, payload: dict[str, Any]) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return self._payload

    class _FakeClient:
        def __init__(self, *_args: Any, **kwargs: Any) -> None:
            assert kwargs.get("trust_env") is False, "feed reader must not consult system proxies"

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def get(self, url: str, **kwargs: Any) -> _FakeResponse:
            assert url == "http://relay/v1/postings", url
            assert kwargs["headers"] == {"Authorization": "Bearer op-token"}, kwargs["headers"]
            return _FakeResponse({"postings": store.postings(None, None)})

    import httpx

    monkeypatch.setattr(httpx, "Client", _FakeClient)
    monkeypatch.setenv("EDGE_RELAY_URL", "http://relay")
    monkeypatch.setenv("RELAY_OP_TOKEN", "op-token")
    reader = RelayFeedReader.from_env({"credential_ref": "RELAY_OP_TOKEN"})
    assert reader.token_configured
    ps = reader.read({"record": "PS", "via": "edge_relay", "dedupe_keys": ["P#", "RN"]}, "feed_ps")
    assert ps == [{"RN": "101", "P#": "1"}]
    assert reader.read({"record": "XX", "via": "edge_relay"}, "feed_xx") == []


def test_relay_feed_reader_missing_env_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EDGE_RELAY_URL", raising=False)
    monkeypatch.delenv("RELAY_OP_TOKEN", raising=False)
    with pytest.raises(FeedError, match="EDGE_RELAY_URL"):
        RelayFeedReader.from_env({"credential_ref": "RELAY_OP_TOKEN"})
    monkeypatch.setenv("EDGE_RELAY_URL", "http://relay")
    with pytest.raises(FeedError, match="unset or empty"):
        RelayFeedReader.from_env({"credential_ref": "RELAY_OP_TOKEN"})


# ---------------------------------------------------------------------------
# Repairs apply to v3 specs; spec commands convert to executable bundles
# ---------------------------------------------------------------------------


def test_rename_field_repairs_v3_wire_path() -> None:
    spec = _fias_spec_dict()
    ctx = RepairContext(
        spec=spec, drift_class="SCHEMA_RENAME", fingerprint_delta=None,
        extra={"entity": "FolioCharge", "old_field": "amount", "new_field": "amount",
               "new_path": "$['TX']"},
    )
    assert rename_field.precondition(ctx)
    patch = rename_field.generate_patch(ctx)
    assert patch == [{"op": "replace", "path": "/entities/FolioCharge/fields/amount/path",
                      "value": "$['TX']"}]
    out = apply_patch(spec, patch)
    assert out["entities"]["FolioCharge"]["fields"]["amount"]["path"] == "$['TX']"
    load_spec_from_dict(out)


def test_rename_field_same_name_without_new_path_stays_rejected() -> None:
    spec = _fias_spec_dict()
    ctx = RepairContext(
        spec=spec, drift_class="SCHEMA_RENAME", fingerprint_delta=None,
        extra={"entity": "FolioCharge", "old_field": "amount", "new_field": "amount"},
    )
    assert not rename_field.precondition(ctx)


def test_bundle_from_spec_renders_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    from autobridge.edge.bundle import bundle_from_spec, render_command, verify_bundle

    monkeypatch.delenv("EDGE_RELEASE_SECRET", raising=False)
    bundle = bundle_from_spec(_fias_spec_dict())
    assert bundle["property_timezone"] == "America/New_York"
    assert set(bundle["commands"]) == {"gi", "gc", "go", "xl", "xd", "wr", "wc", "kr", "kd", "kz"}
    record, fields, expect = render_command(
        bundle, "wr",
        {"room_number": "101", "wake_up_date_yymmdd": "261029", "wake_up_time_hhmm00": "063000"},
    )
    assert record == "WR"
    assert ("RN", "101") in fields and ("DA", "261029") in fields and ("TI", "063000") in fields
    assert expect == "ack"
    record, kr_fields, expect = render_command(
        bundle, "kr",
        {"encoder_id_e_g_01": "01", "room_number": "101", "number_of_keys_to_write": "1",
         "key_validity_from_to_yymmdd": "261028", "key_validity_from_to_yymmdd_2": "261030",
         "reservation_number": "RES-1", "time": "120000", "time_2": "120000"},
    )
    assert record == "KR" and expect == "KA"
    assert ("KC", "01") in kr_fields
    # Unverified bundle data still loads in local-dev mode (keyed deployments
    # verify instead — covered by the bundle's own tests).
    verified = verify_bundle(bundle)
    assert verified["commands"]["wr"]["record"] == "WR"


def test_capabilities_payload_lists_v3() -> None:
    from autobridge.control.routes.capabilities import capabilities_payload

    payload = capabilities_payload()
    assert set(payload["versions"]) == {"v1", "v2", "v3"}
    v3 = payload["versions"]["v3"]
    assert v3["methods"] == ["EVENT"]
    assert v3["auth_types"] == ["edge_relay"]
    assert v3["protocols"] == ["fias_tcp"]
    assert v3["event_feeds"] and v3["command_templates"] and v3["multi_path_mapping"]
    assert payload["versions"]["v1"]["protocols"] == ["http"]
    assert payload["versions"]["v2"]["methods"] == ["POST"]
    assert any("edge_relay" in line for line in payload["v2_v3_diff"])


# ---------------------------------------------------------------------------
# Framed override is protocol-decided (REQ-027 owner check on 96ba3137)
# ---------------------------------------------------------------------------


def test_framed_build_ignores_non_fias_override() -> None:
    """The PABX connector record carries the edge relay queue address
    (``edge-relay://…``) for its runtime target; the framed build must not
    copy it into the spec source — the docs-derived ``fias://`` address
    stands, so the v3 build validates."""
    result = build_draft_spec(
        FIAS_MD,
        source="simulators/docs/fias-pabx.md",
        connector_id="fiaspabx",
        spec_vocabulary="v3",
        base_url_override="edge-relay://queue/fias-commands",
    )
    assert result.success, result.visible_errors
    spec = load_spec_from_dict(dict(result.draft_spec))
    assert spec.source.base_url == "fias://listen:5010"
    assert spec.source.protocol == "fias_tcp"


def test_framed_build_honors_fias_override() -> None:
    """An explicit framed address still wins over the docs-derived one."""
    result = build_draft_spec(
        FIAS_MD,
        source="simulators/docs/fias-pabx.md",
        connector_id="fiaspabx",
        spec_vocabulary="v3",
        base_url_override="fias://edge:5010",
    )
    assert result.success, result.visible_errors
    spec = load_spec_from_dict(dict(result.draft_spec))
    assert spec.source.base_url == "fias://edge:5010"
