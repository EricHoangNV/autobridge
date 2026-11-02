"""REQ-085: the engine's model can add a transform argument or repoint a join.

The frontier's add-arg repair
(``add /entities/<E>/fields/<f>/transform/0/args/input_format``) failed
on the engine arms for two generic reasons: the L3 evidence never
showed the field's current transform list, and the pointer gate
rejected every sub-transform path. Fixed: the evidence renders each
field's transform steps (name and args) next to its path(s) and the
entity joins, and the gate allows each existing step's
``/transform/i/args[/<arg>]`` plus the join repoint members.
Whole-transform replace stays allowed; unlisted paths stay rejected.
Generic specs only — no rehearsal instances, no fault knowledge.
"""

from __future__ import annotations


def _spec() -> dict:
    return {
        "spec_version": 3,
        "connector": {"id": "t", "vendor": "test"},
        "source": {
            "base_url": "http://example.com",
            "auth": {"type": "bearer", "credential_ref": "TEST_TOKEN"},
            "timezone_source": {"kind": "static", "value": "UTC"},
        },
        "operations": {
            "list_r": {
                "method": "GET",
                "path": "/reservations",
                "pagination": {
                    "strategy": "cursor",
                    "cursor_param": "cursor",
                    "limit_param": "limit",
                    "next_path": "$.next",
                    "items_path": "$.data",
                    "stop_when": ["null_cursor"],
                },
            },
            "list_f": {
                "method": "GET",
                "path": "/folios",
                "pagination": {
                    "strategy": "cursor",
                    "cursor_param": "cursor",
                    "limit_param": "limit",
                    "next_path": "$.next",
                    "items_path": "$.data",
                    "stop_when": ["null_cursor"],
                },
            },
        },
        "entities": {
            "R": {
                "from": "list_r",
                "key": "$.id",
                "fields": {
                    "arr": {
                        "path": "$.arr",
                        "transform": [
                            {"name": "business_date", "args": {"tz": "America/New_York", "cutoff": 4}}
                        ],
                    },
                    "plain": {"path": "$.plain"},
                },
                "joins": [
                    {
                        "from_operation": "list_f",
                        "local_key": "$.id",
                        "foreign_key": "$.res_id",
                        "as": "folio",
                        "cardinality": "one",
                        "fields": {"amount": {"path": "$.total"}},
                    }
                ],
                "checks": [],
            }
        },
        "capabilities": {"read": ["R"]},
        "health": {"canary": {"every": "5m", "operation": "list_r"}},
    }


def _evidence(spec: dict | None = None) -> dict:
    from autobridge.repair.controller import build_generic_l3_evidence

    spec = spec if spec is not None else _spec()
    signals = [{"kind": "x", "message": "m", "details": {"entity": "R"}}]
    summary = {"canonical_records": {"R": [{"arr": "2026-10-15"}]}, "unmet_criteria": []}
    return build_generic_l3_evidence(spec, signals, None, summary, "DATES_CHANGED")


def test_evidence_renders_transform_steps_with_args() -> None:
    from autobridge.repair.ladder import _l3_evidence_blocks

    ev = _evidence()
    subtree = (ev.get("spec_subtree") or {}).get("R", {}).get("fields", {})
    entry = subtree.get("arr")
    assert isinstance(entry, dict), entry
    assert entry.get("path") == "$.arr", entry
    steps = entry.get("transform")
    assert isinstance(steps, list) and steps, entry
    assert steps[0].get("name") == "business_date", steps
    assert steps[0].get("args", {}).get("tz") == "America/New_York", steps
    assert steps[0].get("args", {}).get("cutoff") == "4", steps
    # A field with no transform renders an empty step list, not a crash.
    assert (subtree.get("plain") or {}).get("transform") == [], subtree
    # The prompt block the model reads names the step and its args.
    block = _l3_evidence_blocks(ev)["spec_subtree_block"]
    assert "business_date" in block and "cutoff" in block and "$.arr" in block


def test_evidence_renders_joins() -> None:
    ev = _evidence()
    joins = (ev.get("spec_subtree") or {}).get("R", {}).get("joins", [])
    assert isinstance(joins, list) and joins, joins
    assert joins[0].get("local_key") == "$.id", joins
    assert joins[0].get("foreign_key") == "$.res_id", joins
    assert joins[0].get("fields", {}).get("amount") == "$.total", joins


def test_add_arg_patch_passes_pointer_gate() -> None:
    from autobridge.repair.ladder import _l3_pointer_error

    ev = _evidence()
    allowed = ev.get("patch_target", {}).get("allowed_pointers", [])
    assert "/entities/R/fields/arr/transform/0/args" in allowed
    assert "/entities/R/fields/arr/transform/0/args/input_format" in allowed
    # Only declared args are listed: 'tz' yes, an invented arg no.
    assert "/entities/R/fields/arr/transform/0/args/tz" in allowed
    assert not any(p.endswith("/args/mode") for p in allowed)
    patch = [
        {
            "op": "add",
            "path": "/entities/R/fields/arr/transform/0/args/input_format",
            "value": "%m-%d-%Y",
        }
    ]
    assert _l3_pointer_error(patch, target_spec=None, evidence=ev) is None
    # A replace-arg patch passes the same gate.
    assert (
        _l3_pointer_error(
            [{"op": "replace", "path": "/entities/R/fields/arr/transform/0/args/cutoff", "value": 0}],
            target_spec=None,
            evidence=ev,
        )
        is None
    )


def test_join_repoint_passes_pointer_gate() -> None:
    from autobridge.repair.ladder import _l3_pointer_error

    ev = _evidence()
    allowed = ev.get("patch_target", {}).get("allowed_pointers", [])
    assert "/entities/R/joins/0/local_key" in allowed
    assert "/entities/R/joins/0/foreign_key" in allowed
    assert "/entities/R/joins/0/fields/amount/path" in allowed
    assert (
        _l3_pointer_error(
            [{"op": "replace", "path": "/entities/R/joins/0/local_key", "value": "$.x"}],
            target_spec=None,
            evidence=ev,
        )
        is None
    )
    # Whole-transform replace stays allowed.
    assert (
        _l3_pointer_error(
            [
                {
                    "op": "replace",
                    "path": "/entities/R/fields/arr/transform",
                    "value": [{"name": "business_date", "args": {"tz": "UTC", "cutoff": 0}}],
                }
            ],
            target_spec=None,
            evidence=ev,
        )
        is None
    )


def test_pointer_outside_listed_members_still_rejected() -> None:
    from autobridge.repair.ladder import _l3_pointer_error

    ev = _evidence()
    err = _l3_pointer_error(
        [{"op": "replace", "path": "/entities/R/fields/arr/mode", "value": "x"}],
        target_spec=None,
        evidence=ev,
    )
    assert err is not None and "outside the listed patchable members" in err
    # An undeclared transform arg is not listed and is rejected.
    err = _l3_pointer_error(
        [{"op": "add", "path": "/entities/R/fields/arr/transform/0/args/mode", "value": "x"}],
        target_spec=None,
        evidence=ev,
    )
    assert err is not None and "outside the listed patchable members" in err
    # A join member that does not exist is rejected.
    err = _l3_pointer_error(
        [{"op": "replace", "path": "/entities/R/joins/3/local_key", "value": "$.x"}],
        target_spec=None,
        evidence=ev,
    )
    assert err is not None and "outside the listed patchable members" in err
