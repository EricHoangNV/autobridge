"""Extra verifier coverage (P1.W2.6) — hits remaining branches."""

from __future__ import annotations

import pathlib
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from verifier.app import _ensure_no_fixture_leak, _sanitize_failed_checks, app
from verifier.checks import (
    check_business_date,
    check_money_exact,
    check_pagination_and_transport,
    run_all_checks,
)
from verifier.oracle import _spec_has_enum_mapping_for_status, _spec_has_field_path

client = TestClient(app)


def test_readyz_catalog_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    import verifier.app as mod

    def bad_consistency() -> None:
        raise AssertionError("catalog broken")

    monkeypatch.setattr(mod, "catalog_check_consistency", bad_consistency)
    r = client.get("/readyz")
    assert r.status_code == 200
    assert r.json()["status"] == "degraded"
    assert "catalog" in r.json()["checks"]


def test_readyz_fixtures_missing() -> None:
    # Patch Path to make both /fixtures and local hidden_cases appear missing
    with mock.patch.object(pathlib.Path, "is_dir", return_value=False):
        r = client.get("/readyz")
        assert r.status_code == 200
        # Should still be ok (code sets to ok even when missing)
        assert r.json()["status"] in ("ok", "degraded")


def test_sanitize_unknown_name() -> None:
    out = _sanitize_failed_checks([{"name": "unknown_mystery", "count": 1, "details": "x"}])
    assert out[0].name == "record_set_mismatch"
    # bounded name passes through
    out2 = _sanitize_failed_checks([{"name": "field_missing", "count": 2, "details": "y" * 600}])
    assert out2[0].name == "field_missing"
    assert len(out2[0].details or "") == 500  # truncated


def test_ensure_leak_raises() -> None:
    with pytest.raises(ValueError, match="fixture data"):
        _ensure_no_fixture_leak({"reservations": [{"a": 1}], "verdict": "pass"})
    with pytest.raises(ValueError, match="fixture rows"):
        _ensure_no_fixture_leak({"verdict": "pass", "text": "RES-aaa RES-bbb RES-ccc RES-ddd RES-eee RES-fff"})
    # ok cases
    _ensure_no_fixture_leak({"verdict": "pass", "failed_checks": []})
    _ensure_no_fixture_leak({"verdict": "fail", "failed_checks": [{"name": "field_missing", "count": 1, "details": "RES-001 missing"}]})


def test_release_id_with_real_file() -> None:
    # Create a fake generated release dir
    base = pathlib.Path("generated/connectors")
    # Use monkeypatch to point base to tmp_path
    # Instead test the app's release_id branch by creating file under real base

    base.mkdir(parents=True, exist_ok=True)
    conn_dir = base / "testconn"
    rel_dir = conn_dir / "r1"
    rel_dir.mkdir(parents=True, exist_ok=True)
    spec_yaml = rel_dir / "spec.yaml"
    spec_yaml.write_text(
        """
spec_version: 1
connector: { id: testconn, vendor: Test }
source:
  base_url: https://example.com
  auth: { type: bearer, credential_ref: TEST_KEY }
  timezone_source: { kind: static, value: UTC }
operations:
  list_reservations:
    method: GET
    path: /reservations
    pagination: { strategy: offset, offset_param: offset, limit_param: limit, items_path: $.data, stop_when: [empty_page] }
entities:
  Reservation:
    from: list_reservations
    key: $.reservation_id
    fields:
      reservation_id: { path: $.reservation_id }
checks: []
capabilities: { read: [Reservation] }
health: { canary: { every: 60s, operation: list_reservations } }
""",
        encoding="utf-8",
    )
    try:
        r = client.post("/verify", json={"scenario_id": "baseline", "release_id": "r1", "mode": "visible"})
        assert r.status_code == 200
        assert r.json()["verdict"] == "pass"
    finally:
        # cleanup
        import shutil

        if conn_dir.exists():
            shutil.rmtree(conn_dir, ignore_errors=True)
        # also clean base if empty
        try:
            if base.exists() and not any(base.iterdir()):
                base.rmdir()
        except Exception:
            pass


def test_records_without_reservations_key() -> None:
    r = client.post("/verify", json={"scenario_id": "baseline", "records": {"foo": []}, "mode": "visible"})
    assert r.status_code == 200


def test_oracle_spec_helpers_edge() -> None:
    assert _spec_has_field_path({"entities": {"Reservation": {"fields": {"a": {"path": "$.x"}}}}}, "x") is True
    assert _spec_has_field_path({"entities": {"Reservation": {"fields": {"a": "not_dict"}}}}, "x") is False
    assert _spec_has_field_path({"operations": {}}, "x") is False
    # pagination strategy edge
    from verifier.oracle import _spec_pagination_strategy, _spec_stop_when

    assert _spec_pagination_strategy({"operations": {"op": {"pagination": "bad"}}}) is None
    assert _spec_stop_when({"operations": {"op": {"pagination": {"stop_when": "bad"}}}}) == []
    assert _spec_stop_when({"operations": {"op": {"pagination": {"stop_when": ["a"]}}}}) == ["a"]
    # enum mapping edge with non-dict transform
    assert _spec_has_enum_mapping_for_status({"entities": {"Reservation": {"fields": {"status": {"transform": "money"}}}}}, ["a"]) is False
    bad_spec = {"entities": {"Reservation": {"fields": {"status": {"transform": [{"name": "enum_map", "args": {"map": {}}}]}}}}}
    assert _spec_has_enum_mapping_for_status(bad_spec, ["a"]) is False


def test_checks_money_format_edge() -> None:
    expected = {"reservations": [{"reservation_id": "R1", "total_amount": "10.00"}], "folios": []}
    produced = {"reservations": [{"reservation_id": "R1", "total_amount": None}], "folios": []}  # type: ignore[dict-item]
    fails = check_money_exact(expected, produced)
    assert any(f["name"] == "money_format" for f in fails)


def test_checks_business_date_with_properties_none() -> None:
    expected = {
        "reservations": [{"reservation_id": "R1", "arrival_date": "2026-10-01"}],
        "folios": [{"charge_id": "C1", "business_date": "2026-10-01", "posted_at": "2026-10-01T04:00:00+00:00", "property_id": "prop-001"}],
        "properties": [{"property_id": "prop-001", "timezone": "America/New_York"}],
    }
    produced = {
        "reservations": [{"reservation_id": "R1", "arrival_date": "2026-10-01"}],
        "folios": [{"charge_id": "C1", "business_date": "2026-10-01", "posted_at": "invalid", "property_id": "prop-001"}],
        "properties": [{"property_id": "prop-001", "timezone": "America/New_York"}],
    }
    fails = check_business_date(expected, produced, expected["properties"])
    # invalid posted_at should not crash
    assert isinstance(fails, list)


def test_checks_pagination_duplicate_and_tz() -> None:
    expected = {"reservations": [{"reservation_id": "R1"}], "folios": []}
    produced = {"reservations": [{"reservation_id": "R1"}, {"reservation_id": "R1"}], "folios": []}
    fails = check_pagination_and_transport("duplicate_late_records", None, expected, produced)
    assert any(f["name"] == "duplicate_records" for f in fails)
    # tz shift with spec (no fix, still fails)
    fails2 = check_pagination_and_transport("tz_boundary_shift", {"source": {}}, expected, produced)
    assert any(f["name"] == "business_date_mismatch" for f in fails2)


def test_run_all_checks_baseline_empty() -> None:
    from verifier.oracle import (
        generate_expected_canonical,
        generate_mutated,
        simulate_produced_records,
    )

    exp = generate_expected_canonical("baseline")
    mut = generate_mutated("baseline")
    prod = simulate_produced_records(mut, exp, None, "baseline")
    assert run_all_checks(exp, prod, mut, None, "baseline") == []


def test_invalid_patch_trap_with_spec_path() -> None:
    expected = {"reservations": [{"reservation_id": "R1"}], "folios": []}
    produced = {"reservations": [{"reservation_id": "R1"}], "folios": []}
    spec = {"entities": {"Reservation": {"fields": {"total_amount": {"path": "$.net_amount"}}}}}
    fails = check_pagination_and_transport("invalid_patch_trap", spec, expected, produced)
    assert any(f["name"] == "invalid_patch_trap" for f in fails)


def test_rate_limit_with_retry_pass() -> None:
    expected = {"reservations": [{"reservation_id": "R1"}], "folios": []}
    produced = {"reservations": [{"reservation_id": "R1"}], "folios": []}
    spec = {"source": {"rate_limit": {"retry": {"statuses": [429, 503]}}}}
    assert check_pagination_and_transport("rate_limit_429", spec, expected, produced) == []
    assert check_pagination_and_transport("transient_503", spec, expected, produced) == []
