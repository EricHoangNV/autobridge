"""Verifier oracle tests (P1.W2.6)."""

from __future__ import annotations

from simulators.common.generator import generate_dataset
from simulators.scenarios import load_all_scenarios
from verifier.checks import (
    check_business_date,
    check_field_presence,
    check_money_exact,
    check_no_duplicates,
    check_no_loss,
    check_pagination_and_transport,
    check_record_set_by_key,
    check_status_mapping,
    run_all_checks,
)
from verifier.oracle import (
    generate_baseline,
    generate_expected_canonical,
    generate_mutated,
    get_scenario_seed,
    oracle_verdict,
    simulate_produced_records,
)


def test_oracle_generates_deterministic_seed() -> None:
    for scen in load_all_scenarios():
        assert get_scenario_seed(scen.id) == scen.seed


def test_baseline_mutated_differ_for_non_baseline() -> None:
    baseline = generate_baseline("baseline")
    for scen in load_all_scenarios():
        if scen.id == "baseline":
            assert generate_mutated("baseline") == baseline
        else:
            mutated = generate_mutated(scen.id)
            # At least one of reservations/folios differs or pagination flag differs
            assert mutated != baseline or scen.id in (
                "rate_limit_429",
                "transient_503",
                "token_expired",
                "token_revoked",
                "endpoint_removed",
                "offset_to_cursor",
                "cursor_repeat_token",
                "tz_boundary_shift",
            )


def test_expected_canonical_is_baseline() -> None:
    for scen in load_all_scenarios():
        expected = generate_expected_canonical(scen.id)
        baseline = generate_baseline(scen.id)
        assert len(expected["reservations"]) == len(baseline["reservations"])
        assert len(expected["folios"]) == len(baseline["folios"])


def test_oracle_verdict_baseline_pass() -> None:
    verdict, score, failed = oracle_verdict("baseline", None)
    assert verdict == "pass"
    assert score == 1.0
    assert failed == []


def test_oracle_verdict_all_scenarios_fail_without_fix() -> None:
    for scen in load_all_scenarios():
        if scen.id == "baseline":
            continue
        verdict, score, failed = oracle_verdict(scen.id, None)
        assert verdict == "fail", f"{scen.id} should fail without fix"
        assert failed
        # Names must match catalog expected
        expected_names = set(scen.expected.get("failed_checks", []))
        actual_names = {f["name"] for f in failed}
        assert expected_names == actual_names, f"{scen.id}: {actual_names} != {expected_names}"


def test_oracle_verdict_with_fixed_specs() -> None:
    # rename fix
    spec_rename = {
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
                    "strategy": "offset",
                    "offset_param": "offset",
                    "limit_param": "limit",
                    "items_path": "$.data",
                    "stop_when": ["empty_page"],
                },
            }
        },
        "entities": {
            "Reservation": {
                "from": "list_reservations",
                "key": "$.reservation_id",
                "fields": {
                    "reservation_id": {"path": "$.reservation_id"},
                    "guest_name": {"path": "$.guest_identifier"},
                },
            }
        },
        "capabilities": {"read": ["Reservation"]},
        "health": {"canary": {"every": "60s", "operation": "list_reservations"}},
    }
    v, _, f = oracle_verdict("rename_guest_id", spec_rename)
    assert v == "pass" and f == []

    # offset_to_cursor fix
    spec_cursor = {
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
    v, _, f = oracle_verdict("offset_to_cursor", spec_cursor)
    assert v == "pass"

    # cursor repeat fix
    v, _, f = oracle_verdict("cursor_repeat_token", spec_cursor)
    assert v == "pass"
    v, _, f = oracle_verdict("cursor_repeat_token", spec_rename)
    assert v == "fail"

    # rate limit fix
    spec_retry = {
        "spec_version": 1,
        "connector": {"id": "test", "vendor": "Test"},
        "source": {
            "base_url": "https://example.com",
            "auth": {"type": "bearer", "credential_ref": "TEST_KEY"},
            "rate_limit": {
                "max_rps": 5,
                "retry": {"statuses": [429, 503], "backoff": "exponential", "max_attempts": 5},
            },
            "timezone_source": {"kind": "static", "value": "UTC"},
        },
        "operations": {
            "list_reservations": {
                "method": "GET",
                "path": "/reservations",
                "pagination": {
                    "strategy": "offset",
                    "offset_param": "offset",
                    "limit_param": "limit",
                    "items_path": "$.data",
                    "stop_when": ["empty_page"],
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
    v, _, _ = oracle_verdict("rate_limit_429", spec_retry)
    assert v == "pass"
    v, _, _ = oracle_verdict("transient_503", spec_retry)
    assert v == "pass"

    # trap patch should still fail
    spec_trap = {
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
                    "strategy": "offset",
                    "offset_param": "offset",
                    "limit_param": "limit",
                    "items_path": "$.data",
                    "stop_when": ["empty_page"],
                },
            }
        },
        "entities": {
            "Reservation": {
                "from": "list_reservations",
                "key": "$.reservation_id",
                "fields": {
                    "reservation_id": {"path": "$.reservation_id"},
                    "total_amount": {"path": "$.net_amount"},
                },
            }
        },
        "capabilities": {"read": ["Reservation"]},
        "health": {"canary": {"every": "60s", "operation": "list_reservations"}},
    }
    v, _, f = oracle_verdict("invalid_patch_trap", spec_trap)
    assert v == "fail"
    assert any(x["name"] == "invalid_patch_trap" for x in f)


def test_simulate_produced_endpoint_and_auth() -> None:
    expected = generate_expected_canonical("baseline")
    for sid in ("endpoint_removed", "token_expired", "token_revoked"):
        mut = generate_mutated(sid)
        prod = simulate_produced_records(mut, expected, None, sid)
        assert prod["reservations"] == [] and prod["folios"] == []


def test_simulate_truncated_for_pagination() -> None:
    expected = generate_expected_canonical("offset_to_cursor")
    mutated = generate_mutated("offset_to_cursor")
    # spec with offset -> truncated
    spec_offset = {
        "operations": {
            "list_reservations": {
                "pagination": {
                    "strategy": "offset",
                    "offset_param": "offset",
                    "limit_param": "limit",
                    "items_path": "$.data",
                    "stop_when": ["empty_page"],
                }
            }
        }
    }
    prod = simulate_produced_records(mutated, expected, spec_offset, "offset_to_cursor")
    assert len(prod["reservations"]) < len(expected["reservations"])


def test_checks_record_set() -> None:
    expected = {"reservations": [{"reservation_id": "R1"}, {"reservation_id": "R2"}], "folios": [], "properties": []}
    produced = {"reservations": [{"reservation_id": "R1"}], "folios": [], "properties": []}
    fails = check_record_set_by_key(expected, produced)
    assert any(f["name"] == "record_set_mismatch" for f in fails)
    fails2 = check_record_set_by_key(expected, expected)
    assert fails2 == []


def test_checks_no_duplicates() -> None:
    produced = {"reservations": [{"reservation_id": "R1"}, {"reservation_id": "R1"}], "folios": [], "properties": []}
    fails = check_no_duplicates(produced)
    assert any(f["name"] == "duplicate_records" for f in fails)
    assert any(f["name"] == "unique_key" for f in fails)
    assert check_no_duplicates({"reservations": [{"reservation_id": "R1"}], "folios": []}) == []


def test_checks_no_loss() -> None:
    expected = {"reservations": [{"reservation_id": "R1"}, {"reservation_id": "R2"}], "folios": []}
    produced = {"reservations": [{"reservation_id": "R1"}], "folios": []}
    fails = check_no_loss(expected, produced)
    assert any(f["name"] == "no_loss" for f in fails)
    assert check_no_loss(expected, expected) == []


def test_checks_money_exact() -> None:
    expected = {"reservations": [{"reservation_id": "R1", "total_amount": "100.00"}], "folios": []}
    produced = {"reservations": [{"reservation_id": "R1", "total_amount": "90.00"}], "folios": []}
    fails = check_money_exact(expected, produced)
    assert any(f["name"] == "money_mismatch" for f in fails)
    # format error
    produced2 = {"reservations": [{"reservation_id": "R1", "total_amount": "100.0"}], "folios": []}
    fails2 = check_money_exact(expected, produced2)
    assert any(f["name"] == "money_format" for f in fails2)
    # pass
    assert check_money_exact(expected, expected) == []


def test_checks_business_date_and_date_order() -> None:
    expected = {
        "reservations": [{"reservation_id": "R1", "arrival_date": "2026-10-01", "departure_date": "2026-10-05"}],
        "folios": [{"charge_id": "C1", "business_date": "2026-10-02", "posted_at": "2026-10-02T10:00:00+00:00", "property_id": "prop-001"}],
        "properties": [{"property_id": "prop-001", "timezone": "America/New_York"}],
    }
    produced = {
        "reservations": [{"reservation_id": "R1", "arrival_date": "2026-10-01", "departure_date": "2026-10-05"}],
        "folios": [{"charge_id": "C1", "business_date": "2026-10-03", "posted_at": "2026-10-02T10:00:00+00:00", "property_id": "prop-001"}],
        "properties": [{"property_id": "prop-001", "timezone": "America/New_York"}],
    }
    fails = check_business_date(expected, produced, expected["properties"])
    assert any(f["name"] == "business_date_mismatch" for f in fails)
    # date order
    produced_bad = {
        "reservations": [{"reservation_id": "R1", "arrival_date": "2026-10-05", "departure_date": "2026-10-01"}],
        "folios": [],
        "properties": [],
    }
    fails2 = check_business_date(expected, produced_bad, [])
    assert any(f["name"] == "date_order" for f in fails2)


def test_checks_status_mapping() -> None:
    produced = {"reservations": [{"status": "active"}], "folios": []}
    fails = check_status_mapping(produced)
    assert any(f["name"] == "unknown_status" for f in fails)
    assert check_status_mapping({"reservations": [{"status": "confirmed"}], "folios": []}) == []


def test_checks_field_presence() -> None:
    expected = {"reservations": [{"reservation_id": "R1", "guest_name": "Ada"}], "folios": []}
    produced = {"reservations": [{"reservation_id": "R1", "guest_identifier": "Ada"}], "folios": []}
    fails = check_field_presence(expected, produced, "rename_guest_id")
    assert any(f["name"] == "field_missing" for f in fails)
    assert check_field_presence(expected, expected, "rename_guest_id") == []
    assert check_field_presence(expected, produced, "baseline") == []


def test_checks_pagination_and_transport() -> None:
    expected = {"reservations": [{"reservation_id": "R1"}], "folios": []}
    produced = {"reservations": [{"reservation_id": "R1"}], "folios": []}
    # offset_to_cursor without cursor strategy
    fails = check_pagination_and_transport("offset_to_cursor", {"operations": {"op": {"pagination": {"strategy": "offset"}}}}, expected, produced)
    assert any(f["name"] == "pagination_shape" for f in fails)
    # cursor repeat without guard
    fails2 = check_pagination_and_transport("cursor_repeat_token", {"operations": {"op": {"pagination": {"strategy": "cursor", "stop_when": ["null_cursor"]}}}}, expected, produced)
    assert any(f["name"] == "repeated_cursor" for f in fails2)
    # with guard passes
    fails3 = check_pagination_and_transport("cursor_repeat_token", {"operations": {"op": {"pagination": {"strategy": "cursor", "stop_when": ["null_cursor", "repeated_cursor"]}}}}, expected, produced)
    assert not any(f["name"] == "repeated_cursor" for f in fails3)
    # rate limited
    fails4 = check_pagination_and_transport("rate_limit_429", None, expected, produced)
    assert any(f["name"] == "rate_limited" for f in fails4)
    # with retry passes
    spec_retry = {"source": {"rate_limit": {"retry": {"statuses": [429]}}}}
    fails5 = check_pagination_and_transport("rate_limit_429", spec_retry, expected, produced)
    assert not any(f["name"] == "rate_limited" for f in fails5)
    # transient
    assert any(f["name"] == "transient_error" for f in check_pagination_and_transport("transient_503", None, expected, produced))
    # auth
    assert any(f["name"] == "auth_expired" for f in check_pagination_and_transport("token_expired", None, expected, produced))
    assert any(f["name"] == "auth_revoked" for f in check_pagination_and_transport("token_revoked", None, expected, produced))
    assert any(f["name"] == "endpoint_not_found" for f in check_pagination_and_transport("endpoint_removed", None, expected, produced))
    # invalid trap
    assert any(f["name"] == "invalid_patch_trap" for f in check_pagination_and_transport("invalid_patch_trap", None, expected, produced))
    trap_spec = {"entities": {"Reservation": {"fields": {"total_amount": {"path": "$.net_amount"}}}}}
    assert any(f["name"] == "invalid_patch_trap" for f in check_pagination_and_transport("invalid_patch_trap", trap_spec, expected, produced))
    # duplicate
    expected2 = {"reservations": [{"reservation_id": "R1"}], "folios": []}
    produced2 = {"reservations": [{"reservation_id": "R1"}, {"reservation_id": "R1"}], "folios": []}
    assert any(f["name"] == "duplicate_records" for f in check_pagination_and_transport("duplicate_late_records", None, expected2, produced2))
    # tz shift
    assert any(f["name"] == "business_date_mismatch" for f in check_pagination_and_transport("tz_boundary_shift", None, expected, produced))


def test_run_all_checks_integration() -> None:
    expected = generate_expected_canonical("baseline")
    mutated = generate_mutated("baseline")
    produced = simulate_produced_records(mutated, expected, None, "baseline")
    assert run_all_checks(expected, produced, mutated, None, "baseline") == []
    # gross_to_net should have money mismatch
    expected_g = generate_expected_canonical("gross_to_net")
    mutated_g = generate_mutated("gross_to_net")
    produced_g = simulate_produced_records(mutated_g, expected_g, None, "gross_to_net")
    fails = run_all_checks(expected_g, produced_g, mutated_g, None, "gross_to_net")
    assert any(f["name"] == "money_mismatch" for f in fails)


def test_spec_helpers_edge_cases() -> None:
    from verifier.oracle import (
        _spec_has_enum_mapping_for_status,
        _spec_has_field_path,
        _spec_has_retry_for_status,
        _spec_pagination_strategy,
        _spec_stop_when,
    )

    assert _spec_has_field_path(None, "x") is False
    assert _spec_has_field_path({}, "x") is False
    assert _spec_pagination_strategy(None) is None
    assert _spec_stop_when(None) == []
    assert _spec_has_retry_for_status(None, 429) is False
    assert _spec_has_enum_mapping_for_status(None, ["a"]) is False
    # enum_map with matching values
    spec = {
        "entities": {
            "Reservation": {
                "fields": {
                    "status": {
                        "path": "$.status",
                        "transform": [{"name": "enum_map", "args": {"map": {"a": "confirmed", "b": "checked_in"}, "on_unknown": "block"}}]
                    }
                }
            }
        }
    }
    assert _spec_has_enum_mapping_for_status(spec, ["confirmed"]) is True
    assert _spec_has_enum_mapping_for_status({"entities": {}}, ["confirmed"]) is False


# --- REQ-007: private seed override ---


def test_seed_override_changes_dataset() -> None:
    public = get_scenario_seed("baseline")
    assert get_scenario_seed("baseline", seed_override=975311) == 975311
    assert generate_baseline("baseline", seed_override=975311) == generate_dataset(seed=975311)
    assert generate_baseline("baseline", seed_override=975311) != generate_baseline("baseline")
    assert public != 975311


def test_seed_override_validation() -> None:
    import pytest

    from verifier.oracle import get_scenario_seed

    with pytest.raises(ValueError):
        get_scenario_seed("baseline", seed_override=-1)
    with pytest.raises(ValueError):
        get_scenario_seed("baseline", seed_override=True)


def test_oracle_verdict_accepts_override() -> None:
    verdict, score, failed = oracle_verdict("baseline", None, seed_override=975311)
    assert verdict in ("pass", "fail")
    assert 0.0 <= score <= 1.0
    assert isinstance(failed, list)
