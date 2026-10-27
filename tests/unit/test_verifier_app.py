"""Verifier app tests (P1.W2.6)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from verifier.app import app

client = TestClient(app)


def test_healthz() -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_readyz() -> None:
    r = client.get("/readyz")
    assert r.status_code == 200
    assert r.json()["status"] in ("ok", "degraded")
    assert "checks" in r.json()
    assert "catalog" in r.json()["checks"]


def test_verify_baseline_pass() -> None:
    r = client.post("/verify", json={"scenario_id": "baseline", "mode": "visible"})
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"] == "pass"
    assert body["score"] == 1.0
    assert body["failed_checks"] == []
    assert body["scenario_id"] == "baseline"


def test_verify_all_scenarios_against_catalog() -> None:
    from simulators.scenarios import load_all_scenarios

    for scen in load_all_scenarios():
        r = client.post("/verify", json={"scenario_id": scen.id, "mode": "visible"})
        assert r.status_code == 200, f"{scen.id} status {r.status_code}"
        body = r.json()
        # verdict should align with fails flag
        expected_fail = scen.expected["fails"]
        assert (body["verdict"] == "fail") == expected_fail, f"{scen.id} verdict {body['verdict']} != fails {expected_fail}"
        # failed_checks names should exactly match catalog expected
        expected_names = set(scen.expected.get("failed_checks", []))
        actual_names = {f["name"] for f in body["failed_checks"]}
        assert actual_names == expected_names, f"{scen.id}: {actual_names} != {expected_names}"


def test_verify_hidden_never_returns_fixture_rows() -> None:
    for mode in ("visible", "hidden"):
        r = client.post("/verify", json={"scenario_id": "baseline", "mode": mode})
        assert r.status_code == 200
        body = r.json()
        # Top-level keys must be bounded
        assert set(body.keys()) == {"verdict", "score", "failed_checks", "regression", "scenario_id", "mode"}
        # No fixture arrays
        assert "reservations" not in body
        assert "folios" not in body
        assert "properties" not in body
        assert "records" not in body
        # Hidden mode must not leak RES-/CHG- bulk
        import json

        text = json.dumps(body)
        assert text.count("RES-") <= 1  # details may mention example but not bulk
        assert text.count("CHG-") <= 1
        # All failed_check names are bounded
        for fc in body["failed_checks"]:
            assert fc["name"] in {
                "field_missing",
                "pagination_shape",
                "repeated_cursor",
                "rate_limited",
                "transient_error",
                "auth_expired",
                "auth_revoked",
                "unknown_status",
                "money_mismatch",
                "money_format",
                "business_date_mismatch",
                "duplicate_records",
                "endpoint_not_found",
                "invalid_patch_trap",
                "no_loss",
                "unique_key",
                "record_set_mismatch",
                "status_mapping",
                "unknown_enum",
                "date_order",
                "loss_detected",
            }


def test_verify_hidden_mode_leak_detection() -> None:
    # Ensure hidden mode with failing scenario still doesn't leak
    r = client.post("/verify", json={"scenario_id": "rename_guest_id", "mode": "hidden"})
    assert r.status_code == 200
    assert r.json()["mode"] == "hidden"
    assert "reservations" not in r.json()


def test_verify_invalid_scenario() -> None:
    r = client.post("/verify", json={"scenario_id": "does_not_exist", "mode": "visible"})
    assert r.status_code == 400
    r2 = client.post("/verify", json={"scenario_id": "", "mode": "visible"})
    assert r2.status_code in (400, 422)


def test_verify_with_spec_fixed_passes() -> None:
    spec_fixed = {
        "spec_version": 1,
        "connector": {"id": "test", "vendor": "Test"},
        "source": {
            "base_url": "https://example.com",
            "auth": {"type": "bearer", "credential_ref": "TEST_KEY"},
            "timezone_source": {"kind": "static", "value": "UTC"},
        },
        "operations": {
            "list_reservations": {
                "method": "GET",
                "path": "/reservations",
                "pagination": {
                    "strategy": "cursor",
                    "cursor_param": "cursor",
                    "next_path": "$.meta.next_cursor",
                    "items_path": "$.data",
                    "stop_when": ["null_cursor", "repeated_cursor"],
                },
            }
        },
        "entities": {
            "Reservation": {
                "from": "list_reservations",
                "key": "$.reservation_id",
                "fields": {"reservation_id": {"path": "$.reservation_id"}},
            }
        },
        "capabilities": {"read": ["Reservation"]},
        "health": {"canary": {"every": "60s", "operation": "list_reservations"}},
    }
    r = client.post("/verify", json={"scenario_id": "offset_to_cursor", "spec": spec_fixed, "mode": "visible"})
    assert r.status_code == 200
    assert r.json()["verdict"] == "pass"


def test_verify_with_direct_records() -> None:
    from verifier.oracle import generate_expected_canonical

    expected = generate_expected_canonical("baseline")
    # Direct records that match expected should pass
    r = client.post("/verify", json={"scenario_id": "baseline", "records": expected, "mode": "visible"})
    assert r.status_code == 200
    assert r.json()["verdict"] == "pass"
    # Mismatched money should fail
    bad = {k: list(v) for k, v in expected.items()}
    if bad["reservations"]:
        bad["reservations"] = [dict(r) for r in bad["reservations"]]
        bad["reservations"][0] = dict(bad["reservations"][0])
        bad["reservations"][0]["total_amount"] = "0.01"
    r2 = client.post("/verify", json={"scenario_id": "gross_to_net", "records": bad, "mode": "visible"})
    assert r2.status_code == 200
    # For gross_to_net expected money mismatch, but we sent bad records
    # At least verdict should be fail (since we sent wrong money)
    assert r2.json()["verdict"] == "fail"


def test_verify_regression_logic() -> None:
    r = client.post("/verify", json={"scenario_id": "baseline", "mode": "visible"})
    assert r.json()["regression"] == "pass"
    # Baseline that fails would be regression fail, but baseline never fails
    # We test the helper directly
    from verifier.app import _compute_regression

    assert _compute_regression("baseline", "fail", [])[0] == "fail"
    assert _compute_regression("baseline", "pass", [])[0] == "pass"
    assert _compute_regression("rename_guest_id", "fail", [])[0] == "pass"


def test_verify_get_not_allowed() -> None:
    r = client.get("/verify")
    assert r.status_code == 405


def test_verify_release_id_fallback() -> None:
    # Create a fake release on disk and test release_id path (covered via fallback)
    # Ensure no crash when release_id doesn't exist
    r = client.post("/verify", json={"scenario_id": "baseline", "release_id": "nonexistent", "mode": "visible"})
    assert r.status_code == 200
    assert r.json()["verdict"] == "pass"


def test_verify_bounded_names() -> None:
    r = client.post("/verify", json={"scenario_id": "rename_guest_id", "mode": "visible"})
    for fc in r.json()["failed_checks"]:
        assert fc["name"] in {
            "field_missing",
            "pagination_shape",
            "repeated_cursor",
            "rate_limited",
            "transient_error",
            "auth_expired",
            "auth_revoked",
            "unknown_status",
            "money_mismatch",
            "money_format",
            "business_date_mismatch",
            "duplicate_records",
            "endpoint_not_found",
            "invalid_patch_trap",
            "no_loss",
            "unique_key",
            "record_set_mismatch",
            "status_mapping",
            "unknown_enum",
            "date_order",
            "loss_detected",
        }
        assert isinstance(fc["count"], int) and fc["count"] > 0
