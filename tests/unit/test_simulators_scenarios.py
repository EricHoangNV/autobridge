"""P1.W2.5 — Scenario catalog YAML + drift-class isolation test.

Verifies:
  1. Every ``simulators/scenarios/*.yaml`` file is well-formed and covers the
     14 scenarios from plan §2.8.
  2. The YAML catalog is consistent with ``simulators.common.faults.SCENARIO_CATALOG``.
  3. Each scenario applied to the reference spec (baseline dataset → mutated
     dataset) fails in exactly its intended drift class and no other.

The "reference spec" is modelled as the baseline generator output: a
canonical reservation/folio dataset that passes every invariant.  Mutating
it via the fault switchboard must trigger one and only one drift detector.
"""

from __future__ import annotations

import pathlib
from decimal import Decimal

import pytest
import yaml

from simulators.common.faults import (
    KNOWN_SCENARIO_IDS,
    STATUS_VOCAB_MAP,
    apply_dataset_mutations,
    drift_class_for_scenario,
    is_auth_expired,
    is_auth_revoked,
    is_endpoint_removed,
    is_fixable,
    is_pagination_mutated,
    is_rate_limited,
    is_transient_error,
    is_tz_shift_active,
    pagination_mode_for_scenario,
    should_repeat_cursor,
)
from simulators.common.generator import generate_dataset
from simulators.scenarios import catalog_check_consistency, load_all_scenarios, load_scenario

SCENARIOS_DIR = pathlib.Path("simulators/scenarios")


# ---------------------------------------------------------------------------
# Catalog structure
# ---------------------------------------------------------------------------


def test_yaml_count_matches_catalog() -> None:
    yaml_files = list(SCENARIOS_DIR.glob("*.yaml"))
    assert len(yaml_files) == 14, f"expected 14 YAML files, found {len(yaml_files)}: {yaml_files}"
    assert len(KNOWN_SCENARIO_IDS) == 14


def test_yaml_ids_match_fault_ids() -> None:
    yaml_ids = {p.stem for p in SCENARIOS_DIR.glob("*.yaml")}
    assert yaml_ids == KNOWN_SCENARIO_IDS


def test_yaml_required_keys_and_enums() -> None:
    for scen in load_all_scenarios():
        assert scen.id
        assert scen.drift_class
        assert scen.fixable in ("fixable", "escalate")
        assert scen.visibility in ("dev", "heldout")
        assert scen.vendor in ("both", "atlas", "harbor")
        assert isinstance(scen.seed, int)
        assert scen.description
        assert isinstance(scen.expected, dict)
        # expected.fails is present in every scenario
        assert "fails" in scen.expected
        assert isinstance(scen.expected["fails"], bool)
        # expected.http_status and failed_checks
        assert "http_status" in scen.expected
        assert "failed_checks" in scen.expected


def test_yaml_visibility_split_includes_both() -> None:
    scenarios = load_all_scenarios()
    visibilities = {s.visibility for s in scenarios}
    assert "dev" in visibilities
    assert "heldout" in visibilities
    # At least 3 dev and 1 heldout
    dev_count = sum(1 for s in scenarios if s.visibility == "dev")
    heldout_count = sum(1 for s in scenarios if s.visibility == "heldout")
    assert dev_count >= 3
    assert heldout_count >= 1


def test_yaml_fixable_split_includes_both() -> None:
    scenarios = load_all_scenarios()
    fixables = {s.fixable for s in scenarios}
    assert "fixable" in fixables
    assert "escalate" in fixables


def test_yaml_baseline_does_not_fail() -> None:
    scen = load_scenario("baseline")
    assert scen.expected["fails"] is False
    assert scen.drift_class == "none"
    assert scen.expected["failed_checks"] == []


def test_yaml_non_baseline_all_fail() -> None:
    for scen in load_all_scenarios():
        if scen.id == "baseline":
            continue
        assert scen.expected["fails"] is True, f"{scen.id} should have fails=true"


def test_yaml_drift_classes_cover_plan_list() -> None:
    # All non-baseline scenarios must have unique intent; baseline is none
    drift_classes = {s.drift_class for s in load_all_scenarios()}
    # At least the core drift classes are present
    assert "FIELD_RENAME" in drift_classes
    assert "PAGINATION_CHANGE" in drift_classes
    assert "RATE_LIMIT" in drift_classes
    assert "SEMANTIC_VALUE_DRIFT" in drift_classes
    assert "CAPABILITY_REMOVED" in drift_classes


def test_catalog_consistency_with_faults() -> None:
    # YAML catalog must match SCENARIO_CATALOG exactly
    catalog_check_consistency()


def test_load_scenario_unknown_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_scenario("does_not_exist")


def test_yaml_files_are_valid_yaml() -> None:
    for path in SCENARIOS_DIR.glob("*.yaml"):
        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        assert isinstance(data, dict), f"{path} top-level must be mapping"


def test_yaml_id_matches_filename() -> None:
    for path in SCENARIOS_DIR.glob("*.yaml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert data["id"] == path.stem, f"{path}: id {data['id']!r} != stem {path.stem!r}"


# ---------------------------------------------------------------------------
# Drift-class isolation: each scenario fails in exactly its intended class
# ---------------------------------------------------------------------------

# Detection helpers — each returns True iff that drift signal is present
# when comparing baseline vs mutated dataset (or inspecting scenario flags).


def _has_field_rename(mutation: str, mutated_reservations: list[dict[str, object]]) -> bool:
    if mutation != "rename_guest_id":
        return False
    # All reservations should have guest_identifier and no guest_name
    if not mutated_reservations:
        return False
    return all("guest_identifier" in r and "guest_name" not in r for r in mutated_reservations)


def _has_pagination_change(mutation: str) -> bool:
    return mutation == "offset_to_cursor" and is_pagination_mutated(mutation)


def _has_pagination_bug(mutation: str) -> bool:
    return mutation == "cursor_repeat_token" and should_repeat_cursor(mutation)


def _has_rate_limit(mutation: str) -> bool:
    # Use request_count that triggers rate limit (3)
    return is_rate_limited(mutation, 3)


def _has_transient(mutation: str) -> bool:
    return is_transient_error(mutation, 4)


def _has_auth_expired(mutation: str) -> bool:
    return is_auth_expired(mutation)


def _has_auth_revoked(mutation: str) -> bool:
    return is_auth_revoked(mutation)


def _has_status_vocab(mutation: str, mutated_reservations: list[dict[str, object]]) -> bool:
    if mutation != "status_vocab_change":
        return False
    vocab_values = set(STATUS_VOCAB_MAP.values())
    return any(r.get("status") in vocab_values for r in mutated_reservations)


def _has_gross_to_net(mutation: str, baseline_res: list[dict[str, object]], mutated_res: list[dict[str, object]]) -> bool:
    if mutation != "gross_to_net":
        return False
    if not mutated_res or not baseline_res:
        return False
    # Check money factor
    for base, mut in zip(baseline_res, mutated_res, strict=False):
        try:
            base_amt = Decimal(str(base["total_amount"]))
            mut_amt = Decimal(str(mut["total_amount"]))
            expected = (base_amt * Decimal("0.9")).quantize(Decimal("0.01"))
            if mut_amt != expected:
                return False
        except Exception:
            return False
    return True


def _has_duplicate_records(mutation: str, baseline_count: int, mutated_count: int) -> bool:
    if mutation != "duplicate_late_records":
        return False
    return mutated_count > baseline_count


def _has_endpoint_removed(mutation: str) -> bool:
    return is_endpoint_removed(mutation)


def _has_tz_boundary_shift(mutation: str) -> bool:
    return is_tz_shift_active(mutation)


def _has_invalid_patch_trap(mutation: str, mutated_res: list[dict[str, object]]) -> bool:
    if mutation != "invalid_patch_trap":
        return False
    return any("net_amount" in r and "total_amount" in r for r in mutated_res)


# Map scenario_id -> list of detection function names that SHOULD be true
_EXPECTED_SIGNALS: dict[str, list[str]] = {
    "baseline": [],
    "rename_guest_id": ["field_rename"],
    "offset_to_cursor": ["pagination_change"],
    "cursor_repeat_token": ["pagination_bug"],
    "rate_limit_429": ["rate_limit"],
    "transient_503": ["transient"],
    "token_expired": ["auth_expired"],
    "token_revoked": ["auth_revoked"],
    "status_vocab_change": ["status_vocab"],
    "gross_to_net": ["gross_to_net"],
    "tz_boundary_shift": ["tz_boundary_shift"],
    "duplicate_late_records": ["duplicate_records"],
    "endpoint_removed": ["endpoint_removed"],
    "invalid_patch_trap": ["invalid_patch_trap"],
}

_ALL_SIGNAL_NAMES = [
    "field_rename",
    "pagination_change",
    "pagination_bug",
    "rate_limit",
    "transient",
    "auth_expired",
    "auth_revoked",
    "status_vocab",
    "gross_to_net",
    "tz_boundary_shift",
    "duplicate_records",
    "endpoint_removed",
    "invalid_patch_trap",
]


def _detect_all(scenario_id: str, seed: int = 42) -> dict[str, bool]:
    """Run all drift detectors for scenario_id, return signal -> bool."""
    baseline = generate_dataset(seed=seed)
    mutated = apply_dataset_mutations(baseline, scenario_id)
    baseline_res = baseline["reservations"]
    mutated_res = mutated["reservations"]
    return {
        "field_rename": _has_field_rename(scenario_id, mutated_res),
        "pagination_change": _has_pagination_change(scenario_id),
        "pagination_bug": _has_pagination_bug(scenario_id),
        "rate_limit": _has_rate_limit(scenario_id),
        "transient": _has_transient(scenario_id),
        "auth_expired": _has_auth_expired(scenario_id),
        "auth_revoked": _has_auth_revoked(scenario_id),
        "status_vocab": _has_status_vocab(scenario_id, mutated_res),
        "gross_to_net": _has_gross_to_net(scenario_id, baseline_res, mutated_res),
        "tz_boundary_shift": _has_tz_boundary_shift(scenario_id),
        "duplicate_records": _has_duplicate_records(
            scenario_id, len(baseline_res), len(mutated_res)
        ),
        "endpoint_removed": _has_endpoint_removed(scenario_id),
        "invalid_patch_trap": _has_invalid_patch_trap(scenario_id, mutated_res),
    }


def test_baseline_triggers_no_drift_signal() -> None:
    signals = _detect_all("baseline")
    for name, fired in signals.items():
        assert fired is False, f"baseline should not trigger {name}"


@pytest.mark.parametrize("scenario_id", sorted(KNOWN_SCENARIO_IDS))
def test_each_scenario_triggers_exactly_its_intended_drift(scenario_id: str) -> None:
    """Each scenario must trigger only its intended signal(s) and none other."""
    signals = _detect_all(scenario_id)
    expected_true = set(_EXPECTED_SIGNALS[scenario_id])
    for name in _ALL_SIGNAL_NAMES:
        if name in expected_true:
            assert signals[name] is True, f"{scenario_id}: expected {name} to fire but it did not"
        else:
            assert signals[name] is False, f"{scenario_id}: unexpected drift signal {name} fired"


def test_each_scenario_has_exactly_one_primary_signal() -> None:
    """No scenario should have zero or multiple primary signals (except baseline=0)."""
    for sid in sorted(KNOWN_SCENARIO_IDS):
        signals = _detect_all(sid)
        fired = [n for n, v in signals.items() if v]
        expected_count = len(_EXPECTED_SIGNALS[sid])
        assert len(fired) == expected_count, f"{sid}: fired {fired} expected {expected_count}"


def test_drift_class_matches_yaml_for_every_scenario() -> None:
    for scen in load_all_scenarios():
        assert scen.drift_class == drift_class_for_scenario(scen.id), (
            f"{scen.id}: yaml drift_class {scen.drift_class!r} != faults {drift_class_for_scenario(scen.id)!r}"
        )


def test_fixable_tag_matches_catalog() -> None:
    for scen in load_all_scenarios():
        assert scen.fixable == ("fixable" if is_fixable(scen.id) else "escalate")


def test_pagination_modes_consistent_with_faults() -> None:
    """YAML expected pagination_mode must agree with helper pagination_mode_for_scenario."""
    for scen in load_all_scenarios():
        expected_mode = scen.expected.get("pagination_mode")
        if expected_mode is not None:
            # Default offset for baseline etc, cursor for offset_to_cursor etc
            actual = pagination_mode_for_scenario(scen.id, default="offset")
            assert actual == expected_mode, f"{scen.id}: pagination_mode {actual!r} != yaml {expected_mode!r}"


def test_http_status_consistent() -> None:
    """YAML expected http_status must match fault-induced HTTP semantics."""
    expected_status: dict[str, int] = {
        "baseline": 200,
        "rename_guest_id": 200,
        "offset_to_cursor": 200,
        "cursor_repeat_token": 200,
        "rate_limit_429": 429,
        "transient_503": 503,
        "token_expired": 401,
        "token_revoked": 403,
        "status_vocab_change": 200,
        "gross_to_net": 200,
        "tz_boundary_shift": 200,
        "duplicate_late_records": 200,
        "endpoint_removed": 404,
        "invalid_patch_trap": 200,
    }
    for scen in load_all_scenarios():
        assert scen.expected["http_status"] == expected_status[scen.id], (
            f"{scen.id}: http_status yaml {scen.expected['http_status']} != expected {expected_status[scen.id]}"
        )


def test_scenarios_yaml_validates_against_schema() -> None:
    """Cross-load every YAML via the loader and via raw yaml — they must agree."""
    raw_ids = set()
    for path in SCENARIOS_DIR.glob("*.yaml"):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        raw_ids.add(raw["id"])
        loaded = load_scenario(raw["id"])
        assert loaded.id == raw["id"]
        assert loaded.drift_class == raw["drift_class"]
        assert loaded.fixable == raw["fixable"]
        assert loaded.visibility == raw["visibility"]
    assert raw_ids == KNOWN_SCENARIO_IDS


# ---------------------------------------------------------------------------
# Loader validation error branches (coverage for simulators/scenarios/__init__.py)
# ---------------------------------------------------------------------------


def test_loader_rejects_missing_key() -> None:
    from simulators.scenarios import _validate_scenario_dict

    base = {
        "id": "rename_guest_id",
        "drift_class": "FIELD_RENAME",
        "fixable": "fixable",
        "visibility": "dev",
        "vendor": "both",
        "seed": 42,
        "description": "desc",
        "expected": {"fails": True},
    }
    for key in list(base.keys()):
        bad = dict(base)
        del bad[key]
        with pytest.raises(ValueError, match=f"missing required key '{key}'"):
            _validate_scenario_dict(bad, "rename_guest_id.yaml")


def test_loader_rejects_invalid_enums() -> None:
    from simulators.scenarios import _validate_scenario_dict

    base = {
        "id": "x",
        "drift_class": "FIELD_RENAME",
        "fixable": "fixable",
        "visibility": "dev",
        "vendor": "both",
        "seed": 42,
        "description": "desc",
        "expected": {"fails": True},
    }
    bad = dict(base, drift_class="BAD_CLASS")
    with pytest.raises(ValueError, match="unknown drift_class"):
        _validate_scenario_dict(bad, "x.yaml")
    bad = dict(base, fixable="maybe")
    with pytest.raises(ValueError, match="fixable must be"):
        _validate_scenario_dict(bad, "x.yaml")
    bad = dict(base, visibility="secret")
    with pytest.raises(ValueError, match="visibility must be"):
        _validate_scenario_dict(bad, "x.yaml")
    bad = dict(base, vendor="nowhere")
    with pytest.raises(ValueError, match="vendor must be"):
        _validate_scenario_dict(bad, "x.yaml")


def test_loader_rejects_bad_types() -> None:
    from simulators.scenarios import _validate_scenario_dict

    base = {
        "id": "x",
        "drift_class": "FIELD_RENAME",
        "fixable": "fixable",
        "visibility": "dev",
        "vendor": "both",
        "seed": 42,
        "description": "desc",
        "expected": {"fails": True},
    }
    bad = dict(base, seed="not_int")
    with pytest.raises(ValueError, match="seed must be int"):
        _validate_scenario_dict(bad, "x.yaml")
    bad = dict(base, description="")
    with pytest.raises(ValueError, match="description must be non-empty"):
        _validate_scenario_dict(bad, "x.yaml")
    bad = dict(base, expected="not_dict")
    with pytest.raises(ValueError, match="expected must be a mapping"):
        _validate_scenario_dict(bad, "x.yaml")
    bad = dict(base, expected={"fails": "yes"})
    with pytest.raises(ValueError, match="expected.fails must be bool"):
        _validate_scenario_dict(bad, "x.yaml")


def test_loader_rejects_non_dict_top_level() -> None:
    from simulators.scenarios import _validate_scenario_dict

    with pytest.raises(ValueError, match="top-level must be a mapping"):
        _validate_scenario_dict([], "x.yaml")  # type: ignore[arg-type]


def test_loader_rejects_id_filename_mismatch() -> None:
    from simulators.scenarios import _validate_scenario_dict

    data = {
        "id": "other_id",
        "drift_class": "FIELD_RENAME",
        "fixable": "fixable",
        "visibility": "dev",
        "vendor": "both",
        "seed": 42,
        "description": "desc",
        "expected": {"fails": True},
    }
    with pytest.raises(ValueError, match="does not match filename"):
        _validate_scenario_dict(data, "some_file.yaml")


def test_catalog_consistency_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from simulators.common import faults
    from simulators.scenarios import catalog_check_consistency

    # Drift class mismatch — patch SCENARIO_CATALOG to diverge from YAML

    orig = list(faults.SCENARIO_CATALOG)
    with pytest.raises(AssertionError, match="drift_class"):
        bad_catalog = [dict(e) for e in orig]
        bad_catalog[1] = dict(bad_catalog[1], drift_class="WRONG")
        monkeypatch.setattr(faults, "SCENARIO_CATALOG", bad_catalog)
        catalog_check_consistency()
    # Fixable mismatch
    with pytest.raises(AssertionError, match="fixable"):
        bad_catalog = [dict(e) for e in orig]
        flipped = "escalate" if bad_catalog[1]["fixable"] == "fixable" else "fixable"
        bad_catalog[1] = dict(bad_catalog[1], fixable=flipped)
        monkeypatch.setattr(faults, "SCENARIO_CATALOG", bad_catalog)
        catalog_check_consistency()
    # Visibility mismatch
    with pytest.raises(AssertionError, match="visibility"):
        bad_catalog = [dict(e) for e in orig]
        flipped_v = "heldout" if bad_catalog[1]["visibility"] == "dev" else "dev"
        bad_catalog[1] = dict(bad_catalog[1], visibility=flipped_v)
        monkeypatch.setattr(faults, "SCENARIO_CATALOG", bad_catalog)
        catalog_check_consistency()
    # Extra id in catalog (missing in yaml)
    with pytest.raises(AssertionError, match="missing_in_yaml"):
        bad_catalog = orig + [{"id": "phantom", "drift_class": "none", "fixable": "fixable", "visibility": "dev"}]
        monkeypatch.setattr(faults, "SCENARIO_CATALOG", bad_catalog)
        catalog_check_consistency()


def test_detector_edge_cases() -> None:
    # Empty mutated list should not fire field_rename
    assert _has_field_rename("rename_guest_id", []) is False
    # Wrong mutation should not fire
    assert _has_field_rename("baseline", [{"guest_identifier": "x"}]) is False
    assert _has_pagination_change("baseline") is False
    assert _has_pagination_bug("baseline") is False
    assert _has_status_vocab("baseline", [{"status": "active"}]) is False
    assert _has_gross_to_net("baseline", [], []) is False
    assert _has_gross_to_net("gross_to_net", [], []) is False
    assert _has_duplicate_records("baseline", 10, 20) is False
    assert _has_invalid_patch_trap("baseline", []) is False
    # Non-0.9 factor should fail gross_to_net detection — handled via mismatch
    base = [{"total_amount": "100.00"}]
    mut = [{"total_amount": "100.00"}]  # not 90.00
    assert _has_gross_to_net("gross_to_net", base, mut) is False
