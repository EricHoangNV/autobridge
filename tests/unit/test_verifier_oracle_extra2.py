"""Hit remaining oracle branches."""

from verifier.app import _sanitize_failed_checks
from verifier.oracle import (
    _spec_has_enum_mapping_for_status,
    _spec_has_field_path,
    _spec_has_retry_for_status,
    _spec_pagination_strategy,
    _spec_stop_when,
    generate_expected_canonical,
    generate_mutated,
    simulate_produced_records,
)


def test_spec_helpers_exception_paths() -> None:
    # ent not dict
    assert _spec_has_field_path({"entities": {"Reservation": "not_a_dict"}}, "x") is False
    # fields entry not dict already tested
    # operations not dict
    assert _spec_pagination_strategy({"operations": {"op": "not_dict"}}) is None
    assert _spec_stop_when({"operations": {"op": "not_dict"}}) == []
    # operations pagination not dict
    assert _spec_pagination_strategy({"operations": {"op": {"pagination": "bad"}}}) is None
    # enum mapping with exception (bad spec raises)
    class BadDict(dict):
        def get(self, key, default=None):  # type: ignore[override]  # noqa: ARG002
            raise RuntimeError("boom")

    assert _spec_has_field_path(BadDict(), "x") is False
    assert _spec_pagination_strategy(BadDict()) is None
    assert _spec_stop_when(BadDict()) == []
    assert _spec_has_enum_mapping_for_status(BadDict(), ["a"]) is False
    assert _spec_has_retry_for_status(BadDict(), 429) is False
    assert _spec_has_enum_mapping_for_status({"entities": {"R": "not_dict"}}, ["a"]) is False
    assert _spec_has_retry_for_status({"source": "not_dict"}, 429) is False
    assert _spec_has_retry_for_status({"source": {"rate_limit": "not_dict"}}, 429) is False


def test_simulate_truncation_paths() -> None:
    exp = generate_expected_canonical("offset_to_cursor")
    mut = generate_mutated("offset_to_cursor")
    # spec with wrong strategy triggers truncation
    spec = {"operations": {"op": {"pagination": {"strategy": "offset", "offset_param": "o", "limit_param": "l", "items_path": "$.data", "stop_when": ["empty_page"]}}}}
    prod = simulate_produced_records(mut, exp, spec, "offset_to_cursor")
    assert len(prod["reservations"]) < len(exp["reservations"])
    # auth and endpoint already covered but ensure not crashing
    exp2 = generate_expected_canonical("baseline")
    mut2 = generate_mutated("baseline")
    assert simulate_produced_records(mut2, exp2, None, "endpoint_removed")["reservations"] == []
    assert simulate_produced_records(mut2, exp2, None, "token_expired")["reservations"] == []
    assert simulate_produced_records(mut2, exp2, None, "token_revoked")["reservations"] == []
    # rate limit without retry already tested, but with retry passes
    spec_retry = {"source": {"rate_limit": {"retry": {"statuses": [429]}}}}
    mut_rl = generate_mutated("rate_limit_429")
    prod_rl = simulate_produced_records(mut_rl, exp2, spec_retry, "rate_limit_429")
    # with retry, should not truncate (full)
    assert len(prod_rl["reservations"]) == len(mut_rl["reservations"])


def test_sanitize_unknown_name_and_broken() -> None:
    # unknown name mapped to record_set_mismatch
    out = _sanitize_failed_checks([{"name": "weird", "count": 1}])
    assert out[0].name == "record_set_mismatch"
    # missing name defaults to record_set_mismatch
    out2 = _sanitize_failed_checks([{"count": 1}])
    assert out2[0].name == "record_set_mismatch"
    # count as string
    out3 = _sanitize_failed_checks([{"name": "field_missing", "count": "5"}])
    assert out3[0].count == 5
