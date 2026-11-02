"""REQ-083 B10: warm in-run learning stores and looks up under one key.

The descriptor vendor (``atlas``) and the spec's connector vendor
(``AtlasPMS``) never met in the lowercased cache key, so the warm arm
could never reuse what it learned. Both now use the spec form — the
same key production approval learning and the ladder lookup use. A
warm reuse within one run is observed (``warm_used_learned_from``).
"""

from __future__ import annotations


def test_learn_vendor_is_spec_form_not_descriptor() -> None:
    from bench import beacon_run

    vendors = beacon_run.load_vendors()
    spec = beacon_run.engine_spec_for({"index": 0, "vendor": "atlas"}, vendors)
    assert spec is not None
    conn_vendor = spec["connector"]["vendor"]
    assert conn_vendor != "atlas"
    assert beacon_run._spec_vendor_for_learn(dict(spec), "atlas") == conn_vendor
    assert beacon_run._spec_vendor_for_learn({}, "atlas") == "atlas"
    # The lowercased cache key agrees between store and lookup forms.
    from autobridge.repair.knowledge import cache_key

    assert cache_key(conn_vendor, "PAGINATION_CHANGE", None) == cache_key(
        str(conn_vendor).lower(), "PAGINATION_CHANGE", None
    )
    assert cache_key(conn_vendor, "PAGINATION_CHANGE", None) != cache_key(
        "atlas", "PAGINATION_CHANGE", None
    )


def test_warm_learn_and_reuse_observed() -> None:
    """Store a verified fix, then observe the reuse flag on an L0 hit."""
    import types

    from autobridge.repair.knowledge import KnowledgeCache
    from bench import beacon_run

    cache = KnowledgeCache()
    vendors = beacon_run.load_vendors()
    spec = beacon_run.engine_spec_for({"index": 0, "vendor": "atlas"}, vendors)
    assert spec is not None
    vendor = beacon_run._spec_vendor_for_learn(dict(spec), "atlas")
    delta = {"pagination": {"previous": {"strategy": "page"}, "current": {"strategy": "cursor"}}}
    patch = [{"op": "replace", "path": "/operations/list_reservations/pagination/strategy",
              "value": "cursor"}]

    learned_grade = beacon_run.BeaconArmGrade(
        arm="autobridge_warm", instance_index=0, trial=1,
        vendor="atlas", family="pagination",
    )
    learned_grade.verified_completion = 1
    learned_result = types.SimpleNamespace(spec={
        "candidate_patch": patch,
        "rule_id": "page_to_cursor",
        "ladder_level_reached": 1,
        "learn_inputs": {"vendor": vendor, "drift_class": "PAGINATION_CHANGE", "delta": delta},
    })
    beacon_run._beacon_warm_learn(cache, 0, learned_result, learned_grade)
    # The fix is stored under the spec-form key the ladder looks up.
    assert cache.lookup(vendor, delta, drift_class="PAGINATION_CHANGE") is not None
    assert cache.lookup("atlas", delta, drift_class="PAGINATION_CHANGE") is None

    # A later instance reusing it at L0 reports the source instance.
    reuse_grade = beacon_run.BeaconArmGrade(
        arm="autobridge_warm", instance_index=5, trial=1,
        vendor="atlas", family="pagination",
    )
    reuse_result = types.SimpleNamespace(spec={
        "candidate_patch": patch,
        "rule_id": "page_to_cursor",
        "ladder_level_reached": 0,
        "learn_inputs": {"vendor": vendor, "drift_class": "PAGINATION_CHANGE", "delta": delta},
        "beacon_diagnostics": {},
    })
    beacon_run._beacon_warm_learn(cache, 5, reuse_result, reuse_grade)
    assert reuse_grade.warm_used_learned_from == 0
