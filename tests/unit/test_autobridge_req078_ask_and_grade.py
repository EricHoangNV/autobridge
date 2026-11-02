"""REQ-078: the engine asks when it does not know; the beacon grades it fairly.

Item 10 tests (FakeProvider / fakes only — no network, no key):

1. Every drift class climbs past L1 with a fake provider when no rule
   fixes it (no class list anywhere — parametrised over UNKNOWN,
   SCHEMA_RENAME, PAGINATION_CHANGE, ENUM_NEW_VALUE).
2. A verified model patch becomes an approvable candidate (decision
   ``propose``); an unverified one never does (``escalate``, no patch).
3. Detection sweep (item 4): fixed seeds x every family x vendor — no
   instance whose faulted canonical records differ from the baseline may
   end Complete with 0 signals.
4. A crashed arm never passes an ``escalate`` instance (verifier ``Error``
   outcome fails closed for every expected class).
5. The frontier-agent driver cannot read the repo or reach the
   verifier/admin plane (sandbox cwd required, bench-internal env
   scrubbed).
6. Resume after a mid-run abort keeps earlier rows (grades appended as
   they land; summary.json gates the no-re-roll refusal).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from autobridge.llm.provider import Completion, FakeProvider, Usage


def _usage() -> Usage:
    return Usage(input_tokens=100, output_tokens=50)


def _escalation_provider(question: str = "Which mapping is correct?") -> FakeProvider:
    return FakeProvider(
        responses={
            "frontier": Completion(
                text=json.dumps({"escalate": question, "rationale": "no rule fits"}),
                usage=_usage(),
            )
        }
    )


def _patch_spec() -> dict[str, Any]:
    """Minimal spec with one patchable entity field."""
    return {
        "spec_version": 3,
        "connector": {"id": "t", "vendor": "test"},
        "source": {
            "base_url": "http://example.com",
            "auth": {"type": "bearer", "credential_ref": "TEST_TOKEN"},
            "timezone_source": {"kind": "static", "value": "UTC"},
        },
        "operations": {
            "list_reservations": {
                "method": "GET",
                "path": "/reservations",
                "pagination": {"strategy": "page", "page_param": "page", "items_path": "$.data", "stop_when": ["empty_page"]},
            }
        },
        "entities": {
            "Reservation": {
                "from": "list_reservations",
                "key": "$.id",
                "fields": {
                    "guest_name": {"path": "$.guestName"},
                    "status": {"path": "$.status"},
                },
                "checks": [],
            }
        },
        "capabilities": {"read": ["Reservation"]},
        "health": {"canary": {"every": "5m", "operation": "list_reservations"}},
    }


@pytest.mark.parametrize(
    "drift_class", ["UNKNOWN", "SCHEMA_RENAME", "PAGINATION_CHANGE", "ENUM_NEW_VALUE"]
)
def test_every_class_climbs_past_l1_with_provider(drift_class: str) -> None:
    """No class list gates the model climb: with no fitting rule and a
    provider wired, every repairable class reaches L2/L3 (provider called)
    instead of a deterministic 0-call escalation."""
    from autobridge.repair.controller import RepairController

    provider = _escalation_provider()
    ctrl = RepairController()
    result = ctrl.handle(
        spec=_patch_spec(),
        drift_class=drift_class,
        signals=[],
        fingerprint_delta=None,
        hypotheses=[],
        provider=provider,
        budget=None,
        validate_against=_patch_spec(),
    )
    assert provider.calls != [], drift_class
    assert result.decision == "escalate"
    assert result.ladder_level_reached is not None and result.ladder_level_reached >= 3


def test_non_fixable_classes_never_climb() -> None:
    """CAPABILITY_REMOVED / AUTH_REVOKED / backoff classes stay
    deterministic with a provider wired (0 model calls)."""
    from autobridge.repair.controller import RepairController

    for drift_class in ("CAPABILITY_REMOVED", "AUTH_REVOKED", "RATE_LIMIT", "TRANSIENT"):
        provider = _escalation_provider()
        ctrl = RepairController()
        result = ctrl.handle(
            spec=_patch_spec(),
            drift_class=drift_class,
            signals=[],
            fingerprint_delta=None,
            hypotheses=[],
            provider=provider,
            budget=None,
            validate_against=_patch_spec(),
        )
        assert provider.calls == [], drift_class
        assert result.model_usage.get("frontier_tokens", 0) == 0


def test_verified_model_patch_becomes_approvable_candidate() -> None:
    """A non-semantic L3 plain patch that passes the pointer check (no
    fault context rides the climb, so nothing to dry-run against) becomes
    decision=propose with the patch — the operator can Approve it."""
    from autobridge.repair.controller import RepairController

    patch = [{"op": "replace", "path": "/entities/Reservation/fields/guest_name/path", "value": "$.fullName"}]
    provider = FakeProvider(
        responses={"frontier": Completion(text=json.dumps({"patch": patch}), usage=_usage())}
    )
    ctrl = RepairController()
    result = ctrl.handle(
        spec=_patch_spec(),
        drift_class="SCHEMA_RENAME",
        signals=[],
        fingerprint_delta=None,
        hypotheses=[],
        enable_rules=False,
        provider=provider,
        budget=None,
        validate_against=_patch_spec(),
    )
    assert result.decision == "propose"
    assert result.candidate_patch == patch


def test_unverified_model_patch_never_released() -> None:
    """A patch outside the allowed pointers is dropped with one retry and
    ends as a question-only escalation — never a candidate."""
    from autobridge.repair.controller import RepairController

    bad = [{"op": "replace", "path": "/operations/elsewhere/strategy", "value": "cursor"}]
    provider = FakeProvider(
        responses={"frontier": Completion(text=json.dumps({"patch": bad}), usage=_usage())}
    )
    ctrl = RepairController()
    result = ctrl.handle(
        spec=_patch_spec(),
        drift_class="SCHEMA_RENAME",
        signals=[],
        fingerprint_delta=None,
        hypotheses=[],
        enable_rules=False,
        provider=provider,
        budget=None,
        validate_against=_patch_spec(),
        evidence={"patch_target": {"allowed_pointers": ["/entities/Reservation/fields/guest_name/path"]}},
    )
    assert result.decision == "escalate"
    assert result.candidate_patch is None


def test_garbage_model_answer_escalates_without_patch() -> None:
    """Unparsable model text is a normal outcome: escalation, no patch."""
    from autobridge.repair.controller import RepairController

    provider = FakeProvider(
        responses={"frontier": Completion(text="thinking… no answer", usage=_usage())}
    )
    ctrl = RepairController()
    result = ctrl.handle(
        spec=_patch_spec(),
        drift_class="UNKNOWN",
        signals=[],
        fingerprint_delta=None,
        hypotheses=[],
        enable_rules=False,
        provider=provider,
        budget=None,
        validate_against=_patch_spec(),
    )
    assert result.decision == "escalate"
    assert result.candidate_patch is None


def test_crashed_arm_never_passes_escalate() -> None:
    """The verifier's Error outcome fails closed for every expected
    class — a crashed arm passes nothing, not even escalate."""
    from verifier.beacon import accepted_outcomes_for, grade_arm_outcome

    for expected in ("repair", "escalate", "degraded_question", "no_patch"):
        arm = {"outcome": "Error", "patch_reached_release": False, "operator_question": False,
               "failure": "arm timed out"}
        passed, reason = grade_arm_outcome({"expected": expected, "family": "rename"}, arm)
        assert passed is False, expected
        assert "failed" in reason, reason
    # And accepted_outcomes_for never admits Error.
    assert "Error" not in accepted_outcomes_for({"expected": "escalate", "family": "rename"})


def test_agent_driver_requires_sandbox_cwd_and_scrubs_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """The opencode driver runs only inside its sandbox dir, and the child
    env carries no verifier/admin endpoints, held-out paths or simulator
    credentials (no oracle, no repo reads via env)."""
    import subprocess

    from bench.baseline_coding_agent import OpenCodeCliDriver, OpenCodeCliError

    drv = OpenCodeCliDriver()
    with pytest.raises(OpenCodeCliError):
        drv.run("hi")

    seen: dict[str, Any] = {}

    class _Proc:
        returncode = 0
        stdout = '{"type":"text","text":"ok"}'
        stderr = ""

    def _fake_run(_cmd: Any, **_kwargs: Any) -> Any:
        seen.update(_kwargs)
        return _Proc()

    monkeypatch.setenv("VERIFIER_URL", "http://localhost:8104")
    monkeypatch.setenv("HELDOUT_DIR", "/tmp/heldout")
    monkeypatch.setenv("ATLAS_API_KEY", "super-secret-should-not-propagate")
    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setattr(
        "bench.baseline_coding_agent.parse_opencode_output",
        lambda _t: type("R", (), {"session_id": "", "stream_error": "", "text": "ok"})(),
    )
    drv.run("hi", cwd=tmp_path)
    assert seen.get("cwd") == str(tmp_path)
    child_env = seen.get("env", {})
    for key in ("VERIFIER_URL", "HELDOUT_DIR", "ATLAS_API_KEY", "HARBOR_API_KEY"):
        assert key not in child_env, key


def _beacon_case_for_descriptor(descriptor: dict[str, Any]) -> Any:
    from bench.beacon_run import beacon_case_for

    return beacon_case_for(descriptor, None)


def test_detection_sweep_no_silent_wrong_records() -> None:
    """Fixed seeds x every family x vendor: when the faulted canonical
    records differ from the baseline, the sync must not end Complete with
    0 signals (it must be detected, then climb per item 1)."""
    from autobridge.drift.signals import signals_from_sync
    from bench import faults as _faults
    from bench.beacon_run import engine_spec_for

    vendors = ("atlas", "harbor", "legacystay", "meridian")
    families = ("rename", "nesting", "pagination", "code_list", "semantic", "dates", "transport_auth")
    seed_hex = "ab" * 32
    seed_bytes = bytes.fromhex(seed_hex)
    checked = 0
    seen: set[tuple[str, str]] = set()
    silent: list[str] = []
    for vendor in vendors:
        picked: dict[str, int] = {}
        for idx in range(80):
            try:
                from simulators.common import faultgen as _fg
                fam = str(_fg.generate_instance(seed_bytes, idx).get("family", ""))
            except Exception:
                continue
            if fam in families and fam not in picked:
                picked[fam] = idx
            if len(picked) == len(families):
                break
        for family, idx in sorted(picked.items()):
            try:
                pre_spec = engine_spec_for({"vendor": vendor, "index": idx}, None)
                if pre_spec is None:
                    continue
                faulted, baseline = _faults.beacon_sync_pair(
                    vendor=vendor, eval_seed_hex=seed_hex, index=idx, spec=pre_spec,
                )
            except Exception:
                continue
            faulted_canon = faulted.get("canonical_records") or {}
            baseline_canon = baseline.get("canonical_records") or {}
            seen.add((vendor, family))
            if faulted_canon == baseline_canon:
                continue
            checked += 1
            sigs = signals_from_sync(faulted, spec=pre_spec, baseline_summary=baseline)
            outcome = faulted.get("outcome")
            if outcome == "Complete" and not sigs:
                silent.append(f"{vendor}/{family}/{idx}")
    missing = {(v, f) for v in vendors for f in families} - seen
    assert not missing, f"sweep missed families: {sorted(missing)[:10]}"
    assert checked > 0, "sweep observed no differing instances"
    assert not silent, f"silent wrong records (Complete, 0 signals): {silent[:10]}"


def test_detection_rehearsal_misses_now_detected() -> None:
    """REQ-078 (reopened 2026-11-02, round 6603643 rehearsal misses).

    legacystay ``gross_net`` was 0 signals / Complete (silent): the
    cent-rounded wire values never ratio exactly 0.5, so the exact-match
    observer stayed blind. Meridian reservation ``nesting`` classified
    ``ENUM_NEW_VALUE`` (11 signals): the wrapped record reads as unknown
    enums, but the emptied wire keys name the structural cause. Both must
    detect (and then climb per item 1); a true vocab change still reads
    ENUM_NEW_VALUE.
    """
    from bench import ablation as _ablation
    from bench import faults as _faults
    from bench.beacon_run import engine_spec_for

    cases = [
        # (vendor, index, seed_hex, expected drift class)
        ("legacystay", 2, "cd" * 32, "SEMANTIC_VALUE_DRIFT"),
        ("meridian", 13, "ab" * 32, "SCHEMA_RENAME"),
        ("harbor", 61, "cd" * 32, "ENUM_NEW_VALUE"),
    ]
    for vendor, index, seed_hex, expected in cases:
        pre_spec = engine_spec_for({"vendor": vendor, "index": index}, None)
        assert pre_spec is not None, (vendor, index)
        faulted, baseline = _faults.beacon_sync_pair(
            vendor=vendor, eval_seed_hex=seed_hex, index=index, spec=dict(pre_spec),
        )
        faulted_canon = faulted.get("canonical_records") or {}
        baseline_canon = baseline.get("canonical_records") or {}
        assert faulted_canon != baseline_canon, (vendor, index, "records must differ")
        diag = _ablation.derive_from_summaries(
            dict(pre_spec), baseline_summary=baseline, faulted_summary=faulted,
        )
        assert list(diag.signals), (vendor, index, "no signals")
        assert diag.drift_class == expected, (vendor, index, diag.drift_class)


def test_beacon_resume_keeps_landed_rows(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Grades appended as they land survive a mid-run abort: a rerun
    resumes from them (no summary.json), while a finished run (summary
    present) still refuses."""
    import os as _os

    import bench.beacon_run as br

    # REQ-079: the run's placeholder credentials must not leak
    # process-globally — a leak made later localhost syncs resolve for
    # real and trip the DNS guard in whichever test ran next.
    _refs = ("ATLAS_API_KEY", "HARBOR_API_KEY", "BENCH_API_KEY")
    _saved = {ref: _os.environ.pop(ref, None) for ref in _refs}
    out_dir = tmp_path / "r"
    out_dir.mkdir()
    landed = [
        {"arm": "autobridge_cold", "instance_index": 0, "trial": 0, "vendor": "atlas",
         "family": "rename", "verified_completion": 1},
    ]
    (out_dir / "grades.jsonl").write_text("\n".join(json.dumps(r) for r in landed) + "\n")

    from bench.beacon_run import BeaconProtocol

    protocol = BeaconProtocol(
        round_after_utc="2030-01-01T00:00:00+00:00", target_round=9999999,
        faults=2, trials=1, arms=("autobridge_cold",),
        engine_commit="a", generator_commit="b", frozen_at_utc="2030-01-01T00:00:00+00:00",
        rehearsal=True,
    )
    monkeypatch.setattr(br.beacon_client, "fetch_chain_info", lambda _b: {"genesis_time": 1, "period": 1, "chain": "x"})
    monkeypatch.setattr(br.beacon_client, "fetch_latest", lambda _b: {"round": 99999999})
    monkeypatch.setattr(
        br.beacon_client, "fetch_verified_round",
        lambda *_a, **_k: type("V", (), {
            "eval_seed_hex": "ab" * 32, "randomness_hex": "cd" * 32,
            "published_at_utc": 0, "round": 9999999,
            "randomness": "cd" * 32, "signature": "ee" * 96,
        })(),
    )

    pushed: list[int] = []
    monkeypatch.setattr(br, "push_beacon", lambda _seed, i, vendor, **_kwargs: (
        pushed.append(i), {"index": i, "vendor": vendor, "family": "rename", "params": {}},
    )[1])
    monkeypatch.setattr(br, "sanity_unchanged_repairs", lambda *_a, **_k: [])
    monkeypatch.setattr(
        br, "load_vendors",
        lambda: [type("V", (), {"vendor": "atlas", "http": True})()],
    )
    monkeypatch.setattr(br, "admin_urls", lambda *_a, **_k: {"atlas": "http://x"})
    monkeypatch.setattr(br, "public_urls", lambda *_a, **_k: {"atlas": "http://x"})
    monkeypatch.setattr(br, "beacon_case_for", lambda *_a, **_k: None)

    def _fake_trial(_protocol: Any, _seed: Any, descriptor: Any, trial: Any, **_kwargs: Any) -> Any:
        from bench.beacon_run import BeaconArmGrade

        return [BeaconArmGrade(arm="autobridge_cold", instance_index=int(descriptor["index"]),
                               trial=trial, vendor="atlas", family="rename")], {}

    monkeypatch.setattr(br, "run_instance_trial", _fake_trial)

    br.run_beacon(protocol, output_dir=out_dir, runners={})
    rows = [json.loads(line) for line in (out_dir / "grades.jsonl").read_text().splitlines() if line.strip()]
    # Instance descriptors are re-pushed (only grades.jsonl survived the
    # abort), but graded trials never re-run: index 0's trial was skipped.
    assert pushed == [0, 1], pushed
    assert len(rows) == 2, rows
    assert {(r["instance_index"], r["trial"]) for r in rows} == {(0, 0), (1, 0)}
    assert (out_dir / "summary.json").exists()
    # …and a finished run refuses.
    with pytest.raises(br.BeaconError):
        br.run_beacon(protocol, output_dir=out_dir, runners={})
    for _ref in _refs:
        _restored = _os.environ.pop(_ref, None)
        _want = _saved[_ref]
        if _want is None:
            assert _restored is None, f"run_beacon leaked {_ref} process-globally"
        else:
            _os.environ[_ref] = _want
