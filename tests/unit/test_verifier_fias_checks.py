"""Link-layer delivery checks for the FIAS on-premise family (REQ-022 D)."""

from __future__ import annotations

import pytest

from verifier.fias_checks import (
    check_command_outcome,
    check_delivery_money_exact,
    check_delivery_no_duplicates,
    check_delivery_no_loss,
    check_layout_drift,
    grade_fias_scenario,
)


def test_no_loss_and_duplicates_clean() -> None:
    keys = ["PS:000001:101:x", "PS:000002:102:y"]
    assert check_delivery_no_loss(keys, keys + keys[:1]) == []
    assert check_delivery_no_duplicates(keys) == []


def test_no_loss_reports_missing() -> None:
    failed = check_delivery_no_loss(["a", "b", "c"], ["a", "c"])
    assert len(failed) == 1 and failed[0]["name"] == "no_loss" and failed[0]["count"] == 1


def test_duplicates_reports_each_key_once() -> None:
    failed = check_delivery_no_duplicates(["a", "b", "a", "a", "c", "b"])
    assert failed == [{"name": "duplicate_records", "count": 2, "details": failed[0]["details"]}]
    assert "a" in failed[0]["details"] and "b" in failed[0]["details"]


def test_money_exact_decimal() -> None:
    assert check_delivery_money_exact({"k": "12.50"}, {"k": "12.50"}) == []
    failed = check_delivery_money_exact({"k": "12.50"}, {"k": "12.51"})
    assert failed[0]["name"] == "money_mismatch"
    # Absence is no_loss's job, not money's.
    assert check_delivery_money_exact({"k": "12.50"}, {}) == []
    # Garbage is a mismatch, not a crash.
    assert check_delivery_money_exact({"k": "12.50"}, {"k": "soon"})[0]["name"] == "money_mismatch"


def test_layout_drift_names_missing() -> None:
    assert check_layout_drift(["TA", "RN"], ["TA", "RN", "CX"]) == []
    failed = check_layout_drift(["TA", "RN"], ["TM", "RN", "CX"])
    assert failed[0]["name"] == "layout_drift" and failed[0]["count"] == 1
    assert "TA" in failed[0]["details"]


def test_command_outcome_matrix() -> None:
    assert check_command_outcome(["ack_timeout"], "ack_timeout", False, False) == []
    assert check_command_outcome(["sent"], "sent", True, True) == []
    assert check_command_outcome([], "sent", False, True)[0]["name"] == "command_outcome_mismatch"
    assert check_command_outcome(["sent", "sent"], "sent", True, True)[0]["name"] == (
        "command_outcome_mismatch"
    )
    assert check_command_outcome(["failed"], "sent", False, True)[0]["name"] == (
        "command_outcome_mismatch"
    )
    # Stale commands must never reach the PABX.
    assert check_command_outcome(["expired"], "expired", True, False)[0]["name"] == (
        "command_outcome_mismatch"
    )
    assert check_command_outcome(["expired"], "expired", False, False) == []


def test_grade_delivery_scenario() -> None:
    keys = ["PS:1", "PS:2"]
    assert grade_fias_scenario("fias_link_resync", {"expected_keys": keys, "produced_keys": keys}) == []
    failed = grade_fias_scenario("fias_duplicate_records", {"expected_keys": keys, "produced_keys": keys + ["PS:1"]})
    assert [f["name"] for f in failed] == ["duplicate_records"]
    failed = grade_fias_scenario(
        "agent_restart_inflight",
        {"expected_keys": keys, "produced_keys": ["PS:1"], "expected_amounts": {"PS:1": "1.00"},
         "produced_amounts": {"PS:1": "1.01"}},
    )
    assert [f["name"] for f in failed] == ["no_loss", "money_mismatch"]


def test_grade_layout_and_command_scenarios() -> None:
    assert grade_fias_scenario(
        "fias_record_layout_change", {"mapped_wire_ids": ["TA"], "announced_ids": ["TA"]}
    ) == []
    failed = grade_fias_scenario(
        "fias_record_layout_change", {"mapped_wire_ids": ["TA"], "announced_ids": ["TM"]}
    )
    assert [f["name"] for f in failed] == ["layout_drift"]
    failed = grade_fias_scenario(
        "fias_ack_timeout",
        {"reported_outcomes": ["ack_timeout"], "expected_outcome": "ack_timeout",
         "sent_to_pabx": False, "may_send": False},
    )
    assert failed == []
    failed = grade_fias_scenario(
        "stale_command",
        {"reported_outcomes": [], "expected_outcome": "expired", "sent_to_pabx": False,
         "may_send": False},
    )
    assert [f["name"] for f in failed] == ["command_outcome_mismatch"]


def test_grade_unknown_scenario_raises() -> None:
    with pytest.raises(ValueError, match="unknown scenario"):
        grade_fias_scenario("nope", {})


def test_grade_empty_evidence_is_clean() -> None:
    assert grade_fias_scenario("cloud_slow", {}) == []
