"""Beacon run extended coverage (REQ-025): offline end-to-end paths.

Every live seam is faked at the boundary (transports, verifier HTTP,
sync execution, drand fetch): no network, no stack. The full freeze →
run → verify-repro loop is exercised with the real orchestration code,
and one test drives the real engine runtime against a MockTransport.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

import pytest

import simulators.common.faultgen as faultgen
from bench import beacon_run
from bench.beacon_run import BeaconError

if TYPE_CHECKING:
    import pathlib

EVAL_SEED_HEX = "05ec57a0c1e763e985c9ac18df2313a98392a9c24ecbceefd0b21216a9beaefa"
EVAL_SEED = bytes.fromhex(EVAL_SEED_HEX)

FAKE_INFO = {
    "public_key": "868f005eb8e6e4ca0a47c8a77ceaa5309a47978a7c71bc5cce96366b5d7a569937c529eeda66c7293784a9402801af31",
    "period": 30,
    "genesis_time": 1595431050,
    "hash": beacon_run.beacon_client.LOE_MAINNET_CHAIN_HASH,
    "schemeID": beacon_run.beacon_client.LOE_SCHEME_ID,
}


def _descriptor(index: int) -> dict[str, Any]:
    inst = faultgen.generate_instance(EVAL_SEED, index)
    return {"index": index, "vendor": inst["vendor"], "family": inst["family"], "params": inst["params"]}


def _protocol(**over: Any) -> beacon_run.BeaconProtocol:
    base = dict(
        round_after_utc="2030-01-01T00:00:00+00:00", target_round=9999999,
        faults=2, trials=1, arms=("autobridge_cold",),
        engine_commit="a", generator_commit="b", frozen_at_utc="2030-01-01T00:00:00+00:00",
    )
    base.update(over)
    return beacon_run.BeaconProtocol(**base)  # type: ignore[arg-type]


def test_load_vendors_edges(tmp_path: pathlib.Path) -> None:
    bad = tmp_path / "v.yaml"
    bad.write_text("not a mapping list\n", encoding="utf-8")
    with pytest.raises(BeaconError):
        beacon_run.load_vendors(bad)
    empty = tmp_path / "e.yaml"
    empty.write_text("order: []\n", encoding="utf-8")
    with pytest.raises(BeaconError):
        beacon_run.load_vendors(empty)
    nonmap = tmp_path / "n.yaml"
    nonmap.write_text("order:\n  - justastring\n", encoding="utf-8")
    with pytest.raises(BeaconError):
        beacon_run.load_vendors(nonmap)
    noname = tmp_path / "m.yaml"
    noname.write_text("order:\n  - http: true\n", encoding="utf-8")
    with pytest.raises(BeaconError):
        beacon_run.load_vendors(noname)
    with pytest.raises(BeaconError):
        beacon_run.load_vendors(tmp_path / "missing.yaml")


def test_url_maps_and_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    vendors = beacon_run.load_vendors()
    assert set(beacon_run.admin_urls(vendors)) == {v.vendor for v in vendors}
    monkeypatch.setenv("BEACON_ADMIN_URLS", "atlas=http://example.invalid:1,,badchunk")
    assert beacon_run.admin_urls(vendors)["atlas"] == "http://example.invalid:1"
    assert beacon_run._parse_url_map("") == {}
    assert beacon_run.admin_headers() == {"X-Admin-Network": "admin-net"}
    monkeypatch.setenv("SIM_ADMIN_TOKEN", "tok")
    assert beacon_run.admin_headers()["X-Admin-Token"] == "tok"


def test_push_beacon_transport_and_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx as _httpx

    def boom(*_a: Any, **_k: Any) -> Any:
        raise _httpx.ConnectError("down")

    monkeypatch.setattr(_httpx, "post", boom)
    with pytest.raises(BeaconError):
        beacon_run.push_beacon(EVAL_SEED_HEX, 0, "atlas", admin_url="http://x")

    class _Bad:
        status_code = 500

        def json(self) -> Any:
            return {}

    monkeypatch.setattr(_httpx, "post", lambda *_a, **_k: _Bad())
    with pytest.raises(BeaconError):
        beacon_run.push_beacon(EVAL_SEED_HEX, 0, "atlas", admin_url="http://x")

    class _NonDict:
        status_code = 200

        def json(self) -> Any:
            return [1, 2]

    monkeypatch.setattr(_httpx, "post", lambda *_a, **_k: _NonDict())
    with pytest.raises(BeaconError):
        beacon_run.push_beacon(EVAL_SEED_HEX, 0, "atlas", admin_url="http://x")

    class _MissingKeys:
        status_code = 200

        def json(self) -> Any:
            return {"index": 0, "vendor": "atlas"}

    monkeypatch.setattr(_httpx, "post", lambda *_a, **_k: _MissingKeys())
    with pytest.raises(BeaconError):
        beacon_run.push_beacon(EVAL_SEED_HEX, 0, "atlas", admin_url="http://x")

    with pytest.raises(BeaconError):
        beacon_run.push_beacon(EVAL_SEED_HEX, 0, "nosuchvendor")

    seen: dict[str, str] = {}

    class _Ok:
        status_code = 200

        def json(self) -> Any:
            return {"beacon": {"index": 4, "vendor": "fias-pabx", "family": "fias_link",
                               "params": {"kind": "resync"}}}

    def _capture(url: str, **_k: Any) -> Any:
        seen["url"] = url
        return _Ok()

    monkeypatch.setattr(_httpx, "post", _capture)
    descriptor = beacon_run.push_beacon(EVAL_SEED_HEX, 4, "fias-pabx", admin_url="http://x:8119")
    assert seen["url"] == "http://x:8119/admin/fias/beacon"
    assert descriptor["family"] == "fias_link"


def test_beacon_case_all_families() -> None:
    seen: set[str] = set()
    for index in range(60):
        descriptor = _descriptor(index)
        case = beacon_run.beacon_case_for(descriptor)
        # REQ-062 item 2: every beacon vendor has an in-process engine
        # spec, PABX included — no instance is outcome-graded-only.
        assert case is not None, descriptor
        if descriptor["vendor"] == "fias-pabx":
            assert case.connector_id == "fiaspabx", descriptor
            seen.add("fias:" + descriptor["family"])
            continue
        seen.add(descriptor["family"])
        # Equal task setup only: spec + connector + docs, never diagnostics.
        assert case.spec and case.connector_id and case.docs_ref
        assert not hasattr(case, "drift_class")
        assert not hasattr(case, "hypotheses")
    for family in ("rename", "nesting", "pagination", "code_list", "semantic", "dates", "transport_auth"):
        assert family in seen, f"family {family} not covered in 60 instances"
    assert any(s.startswith("fias:") for s in seen), "no fias instances covered"


def test_beacon_case_needs_no_instance_params() -> None:
    """The case carries no instance parameters (REQ-024 owner check).

    ``beacon_case_for`` resolves the same setup when the descriptor's
    ``params`` are blanked: params are serving detail for the simulator,
    never an arm input.
    """
    descriptor = _descriptor(0)
    assert descriptor["vendor"] != "fias-pabx", "need an HTTP instance for this test"
    full = beacon_run.beacon_case_for(descriptor)
    assert full is not None
    blanked = dict(descriptor, params={})
    same = beacon_run.beacon_case_for(blanked)
    assert same is not None
    assert same.spec == full.spec
    assert same.connector_id == full.connector_id
    assert same.docs_ref == full.docs_ref


def test_beacon_derive_observes_served_instance() -> None:
    """Engine arms observe the beacon instance end to end (REQ-024 check).

    Drives a real instance through the vendor app in-process (no stack),
    derives the drift class from the served observations, and runs a full
    engine arm on it. The derived class must come from the serving — the
    descriptor's family label is never passed to the arm.
    """
    from bench import ablation as _ablation
    from bench.harness import HarnessConfig as _Cfg
    from bench.harness import make_trial_inputs as _inputs

    descriptor = None
    for index in range(60):
        cand = _descriptor(index)
        if cand["vendor"] in ("atlas", "harbor") and cand["family"] in ("rename", "pagination", "semantic"):
            descriptor = cand
            break
    assert descriptor is not None
    case = beacon_run.beacon_case_for(descriptor)
    assert case is not None
    diag = beacon_run.derive_beacon_diagnostics(
        case.spec,
        descriptor=descriptor,
        eval_seed_hex=EVAL_SEED_HEX,
        vendor=str(descriptor["vendor"]),
        connector_id=case.connector_id,
    )
    assert diag.drift_class, descriptor
    assert diag.signals, descriptor
    inputs = _inputs(_Cfg(), f"beacon:{descriptor['index']}", 0)

    def _derive(spec: Any, _case: Any, _inputs: Any) -> Any:
        return beacon_run.derive_beacon_diagnostics(
            spec,
            descriptor=descriptor,
            eval_seed_hex=EVAL_SEED_HEX,
            vendor=str(descriptor["vendor"]),
            connector_id=case.connector_id,
        )

    from autobridge.llm.provider import FakeProvider

    res = _ablation.run_engine_arm(
        inputs, case, ablation="autobridge_cold", provider=FakeProvider(), derive=_derive
    )
    assert res.spec is not None
    assert res.spec["decision"] in ("propose", "escalate", "degraded")
    assert res.spec["candidate_patch"] == [] or isinstance(res.spec["candidate_patch"], list)


def test_engine_spec_failure_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    from bench import ablation as _ablation

    monkeypatch.setattr(_ablation, "_load_reference_spec", lambda _conn: (_ for _ in ()).throw(KeyError("nope")))
    assert beacon_run.engine_spec_for(_descriptor(0)) is None


def test_run_arm_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    from bench import ablation as _ablation

    inputs = beacon_run_load_inputs()
    with pytest.raises(BeaconError):
        beacon_run._run_arm("nope", inputs, None, {}, None, None)
    monkeypatch.setattr(_ablation, "run_frontier_oneshot_arm", lambda _i, **_: "oneshot")
    assert beacon_run._run_arm("frontier_oneshot", inputs, None, {}, "prov", None) == "oneshot"
    monkeypatch.setattr(_ablation, "run_frontier_agent_arm", lambda _i, **_: "agent")
    assert beacon_run._run_arm("frontier_agent", inputs, None, {}, None, None) == "agent"


def beacon_run_load_inputs() -> Any:
    from bench.harness import HarnessConfig as _Cfg
    from bench.harness import make_trial_inputs as _inputs

    return _inputs(_Cfg(), "beacon:0", 0)


def test_candidate_spec_branches() -> None:
    from types import SimpleNamespace as _NS

    assert beacon_run._candidate_spec_for(_NS(success=False, spec=None), None) is None
    descriptor = _descriptor(0)
    case = beacon_run.beacon_case_for(descriptor)
    assert case is not None
    # Empty patch → None.
    assert beacon_run._candidate_spec_for(_NS(success=True, spec={"decision": "propose"}), case) is None
    # Unapplicable patch → None (fail closed, never raises).
    bad = _NS(success=True, spec={"decision": "propose", "candidate_patch": [{"op": "remove", "path": "/nope"}]})
    assert beacon_run._candidate_spec_for(bad, case) is None
    # Oneshot full-spec passthrough.
    full = {"operations": {"a": {}}, "entities": {"E": {}}}
    assert beacon_run._candidate_spec_for(_NS(success=True, spec=full), case) == full
    # Real patch application on the case spec.
    patch = [{"op": "replace", "path": "/source/timezone_source/value", "value": "America/New_York"}]
    good = _NS(success=True, spec={"decision": "propose", "candidate_patch": patch})
    candidate = beacon_run._candidate_spec_for(good, case)
    assert candidate is not None and candidate["source"]["timezone_source"]["value"] == "America/New_York"


def test_verifier_post_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx as _httpx

    def boom(*_a: Any, **_k: Any) -> Any:
        raise _httpx.ConnectError("down")

    monkeypatch.setattr(_httpx, "post", boom)
    with pytest.raises(BeaconError):
        beacon_run._verifier_post("http://x", "/beacon/grade", {})

    class _Err:
        status_code = 500
        text = "boom"

    monkeypatch.setattr(_httpx, "post", lambda *_a, **_k: _Err())
    with pytest.raises(BeaconError):
        beacon_run._verifier_post("http://x", "/beacon/grade", {})

    class _BadJson:
        status_code = 200

        def json(self) -> Any:
            raise ValueError("nope")

    monkeypatch.setattr(_httpx, "post", lambda *_a, **_k: _BadJson())
    with pytest.raises(BeaconError):
        beacon_run._verifier_post("http://x", "/beacon/grade", {})

    class _List:
        status_code = 200

        def json(self) -> Any:
            return []

    monkeypatch.setattr(_httpx, "post", lambda *_a, **_k: _List())
    with pytest.raises(BeaconError):
        beacon_run._verifier_post("http://x", "/beacon/grade", {})


def _fake_verifier(monkeypatch: pytest.MonkeyPatch, records_verdict: str = "pass") -> None:
    import httpx as _httpx

    class _Resp:
        status_code = 200

        def __init__(self, payload: Any) -> None:
            self._payload = payload

        def json(self) -> Any:
            return self._payload

    def _post(url: str, **k: Any) -> Any:
        import json as _json

        raw = k.get("content", "")
        try:
            body = _json.loads(raw) if isinstance(raw, str) else {}
        except ValueError:
            body = {}
        if url.endswith("/beacon/records"):
            return _Resp({"verdict": records_verdict, "score": 1.0, "failed_checks": []})
        if url.endswith("/beacon/grade"):
            outcome = body.get("outcome")
            passed = (outcome == "Complete" and body.get("patch_reached_release")) or (
                outcome in ("Blocked", "Degraded")
            )
            return _Resp({"passed": passed, "reason": "fake", "accepted": []})
        raise AssertionError(f"unexpected verifier path {url}")

    monkeypatch.setattr(_httpx, "post", _post)


_VALID_PATCH = [{"op": "replace", "path": "/source/timezone_source/value", "value": "America/New_York"}]


def _engine_result(patch: Any = None) -> Any:
    from types import SimpleNamespace as _NS

    return _NS(success=True, frontier_tokens=10, input_tokens=5, output_tokens=3,
               cost_usd=0.01, model="fake-ladder",
               spec={"decision": "propose", "candidate_patch": patch if patch is not None else list(_VALID_PATCH),
                     "ladder_level_reached": 1, "rule_id": "fake"})


def test_full_trial_offline_graded(monkeypatch: pytest.MonkeyPatch) -> None:
    from bench.harness import HarnessConfig as _Cfg

    descriptor = None
    for index in range(30):
        cand = _descriptor(index)
        if cand["family"] == "rename" and cand["vendor"] in ("atlas", "harbor"):
            descriptor = cand
            break
    assert descriptor is not None
    _fake_verifier(monkeypatch, records_verdict="pass")
    monkeypatch.setattr(
        beacon_run, "sync_candidate",
        lambda _spec, _url, **_k: {"outcome": "Complete",
                                "canonical_records": {"Reservation": [{"reservation_id": "R1"}]}},
    )
    protocol = _protocol(arms=("autobridge_cold", "autobridge_warm"))
    grades, reports = beacon_run.run_instance_trial(
        protocol, EVAL_SEED_HEX, descriptor, 0, config=_Cfg(trials_per_scenario=1),
        runners={"autobridge_cold": lambda _i: _engine_result(),
                 "autobridge_warm": lambda _i: _engine_result()},
        verifier_url="http://fake-verifier",
    )
    assert len(grades) == 2
    assert all(g.verified_completion == 1 for g in grades)
    assert all(g.wrong_patch_released == 0 for g in grades)
    assert "autobridge_cold" in reports


def test_full_trial_records_fail_is_no_release(monkeypatch: pytest.MonkeyPatch) -> None:
    # REQ-062 item 4: a claimed Complete whose records fail (or never
    # sync) is a failed attempt, not a release — wrong_patch_released
    # counts only rows whose patch reached a release and failed the
    # outcome grade.
    from bench.harness import HarnessConfig as _Cfg

    descriptor = _descriptor(0)
    _fake_verifier(monkeypatch, records_verdict="fail")
    monkeypatch.setattr(
        beacon_run, "sync_candidate",
        lambda _spec, _url, **_k: {"outcome": "Complete", "canonical_records": {}},
    )
    protocol = _protocol()
    (grade,) = beacon_run.run_instance_trial(
        protocol, EVAL_SEED_HEX, descriptor, 0, config=_Cfg(trials_per_scenario=1),
        runners={"autobridge_cold": lambda _i: _engine_result()},
        verifier_url="http://fake-verifier",
    )[0]
    assert grade.verified_completion == 0
    assert grade.wrong_patch_released == 0
    assert grade.records_verdict == "fail"
    assert grade.records_failed_checks == ()
    assert grade.patch_summary and "replace" in grade.patch_summary


def test_full_trial_release_then_grade_fail_marks_wrong_patch(monkeypatch: pytest.MonkeyPatch) -> None:
    # REQ-062 item 4: records verified (release reached) but the outcome
    # grade fails → wrong_patch_released 1, and the row names the patch
    # and the failed verifier checks.
    import httpx as _httpx

    from bench.harness import HarnessConfig as _Cfg

    class _Resp:
        status_code = 200

        def __init__(self, payload: Any) -> None:
            self._payload = payload

        def json(self) -> Any:
            return self._payload

    def _post(url: str, **_k: Any) -> Any:
        if url.endswith("/beacon/records"):
            return _Resp({"verdict": "pass", "score": 1.0,
                          "failed_checks": [{"name": "money_mismatch", "count": 2}]})
        if url.endswith("/beacon/grade"):
            return _Resp({"passed": False, "reason": "outcome Complete not in ['escalate']", "accepted": []})
        raise AssertionError(f"unexpected verifier path {url}")

    monkeypatch.setattr(_httpx, "post", _post)
    monkeypatch.setattr(
        beacon_run, "sync_candidate",
        lambda _spec, _url, **_k: {"outcome": "Complete",
                                "canonical_records": {"Reservation": [{"reservation_id": "R1"}]}},
    )
    descriptor = _descriptor(0)
    protocol = _protocol()
    (grade,) = beacon_run.run_instance_trial(
        protocol, EVAL_SEED_HEX, descriptor, 0, config=_Cfg(trials_per_scenario=1),
        runners={"autobridge_cold": lambda _i: _engine_result()},
        verifier_url="http://fake-verifier",
    )[0]
    assert grade.verified_completion == 0
    assert grade.wrong_patch_released == 1
    assert grade.records_verdict == "pass"
    assert grade.records_failed_checks == ("money_mismatch",)
    assert grade.patch_summary and "replace" in grade.patch_summary
    assert "money_mismatch" not in (grade.grade_reason or "")
    assert "engine:" in (grade.grade_reason or "")


@pytest.fixture()
def _clean_bench_env() -> Any:
    """run_beacon installs non-secret placeholder credentials; restore afterwards."""
    import os as _os

    saved = {ref: _os.environ.get(ref) for ref in ("ATLAS_API_KEY", "HARBOR_API_KEY", "BENCH_API_KEY")}
    try:
        yield
    finally:
        for ref, value in saved.items():
            if value is None:
                _os.environ.pop(ref, None)
            else:
                _os.environ[ref] = value


def test_run_beacon_loop_offline(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, _clean_bench_env: Any) -> None:
    monkeypatch.setattr(beacon_run.beacon_client, "fetch_chain_info", lambda _base_url: dict(FAKE_INFO))
    monkeypatch.setattr(
        beacon_run.beacon_client, "fetch_latest",
        lambda _base_url: {"round": 9999999, "randomness": "ab" * 32,
                          "signature": "cd" * 96, "previous_signature": "ef" * 96},
    )
    verified = beacon_run.beacon_client.VerifiedRound(
        round=9999999, randomness_hex="ab" * 32, eval_seed_hex=EVAL_SEED_HEX, published_at_utc=1893456000)
    monkeypatch.setattr(beacon_run.beacon_client, "fetch_verified_round",
                        lambda _base_url, _round, **_: verified)
    monkeypatch.setattr(beacon_run, "push_beacon", lambda _seed, index, _vendor, **_: _descriptor(index))
    monkeypatch.setattr(
        beacon_run, "sync_candidate", lambda _spec, _url, **_k: {"outcome": "Complete", "canonical_records": {}})
    _fake_verifier(monkeypatch, records_verdict="pass")
    protocol = _protocol(faults=2)
    out = tmp_path / "beacon" / "9999999"
    result = beacon_run.run_beacon(
        protocol, config=None,
        runners={"autobridge_cold": lambda _i: _engine_result()},
        verifier_url="http://fake-verifier",
        base_url="https://example.invalid", output_dir=out,
    )
    assert len(result.instances) == 2
    assert len(result.grades) == 2
    assert (out / "instances.json").is_file()
    assert (out / "grades.jsonl").is_file()
    assert (out / "summary.json").is_file()
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["round"] == 9999999 and summary["faults"] == 2


def test_verify_repro_round_trip(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "beacon" / "424242"
    out.mkdir(parents=True)
    instances = [_descriptor(0), _descriptor(1)]
    (out / "instances.json").write_text(json.dumps(instances) + "\n", encoding="utf-8")
    monkeypatch.setattr(beacon_run.beacon_client, "fetch_chain_info", lambda _base_url: dict(FAKE_INFO))
    verified = beacon_run.beacon_client.VerifiedRound(
        round=424242, randomness_hex="ab" * 32, eval_seed_hex=EVAL_SEED_HEX, published_at_utc=1893456000)
    monkeypatch.setattr(beacon_run.beacon_client, "fetch_verified_round",
                        lambda _base_url, _round, **_: verified)
    monkeypatch.setattr(beacon_run, "push_beacon",
                        lambda _seed, index, _vendor, **_: _descriptor(index))
    assert beacon_run.verify_repro(424242, base_url="https://example.invalid", output_dir=out) is True
    # Tampered published params fail closed.
    tampered = [dict(instances[0], params={"a": 1})] + instances[1:]
    (out / "instances.json").write_text(json.dumps(tampered) + "\n", encoding="utf-8")
    with pytest.raises(BeaconError):
        beacon_run.verify_repro(424242, base_url="https://example.invalid", output_dir=out)


def test_main_rehearse_offline(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, _clean_bench_env: Any
) -> None:
    beacon_dir = tmp_path / "beacon"
    monkeypatch.setattr(beacon_run, "BEACON_DIR", beacon_dir)
    monkeypatch.setattr(beacon_run, "_git_dirty", lambda: False)
    monkeypatch.setattr(beacon_run, "_git_commit", lambda: "abc123")
    monkeypatch.setattr(beacon_run.beacon_client, "fetch_chain_info", lambda _base_url: dict(FAKE_INFO))
    monkeypatch.setattr(beacon_run.beacon_client, "fetch_latest",
                        lambda _base_url: dict(round=6593481, randomness="ab" * 32,
                                              signature="cd" * 96, previous_signature="ef" * 96))
    verified = beacon_run.beacon_client.VerifiedRound(
        round=6593481, randomness_hex="ab" * 32, eval_seed_hex=EVAL_SEED_HEX, published_at_utc=1793235450)
    monkeypatch.setattr(beacon_run.beacon_client, "fetch_verified_round",
                        lambda _base_url, _round, **_: verified)
    monkeypatch.setattr(beacon_run, "push_beacon", lambda _seed, index, _vendor, **_: _descriptor(index))
    monkeypatch.setattr(beacon_run, "sync_candidate",
                        lambda _spec, _url, **_k: {"outcome": "Complete", "canonical_records": {}})
    _fake_verifier(monkeypatch, records_verdict="pass")
    assert beacon_run.main(["rehearse", "--round", "6593481", "--faults", "2", "--trials", "1",
                            "--verifier-url", "http://fake", "--base-url", "https://example.invalid"]) == 0
    assert (beacon_dir / "rehearsals" / "6593481" / "summary.json").is_file()
    assert beacon_run.main(["verify-repro", "--round", "6593481", "--base-url", "https://example.invalid",
                            "--output-dir", str(beacon_dir / "rehearsals" / "6593481")]) == 0


def test_main_verify_repro_missing_round() -> None:
    assert beacon_run.main(["verify-repro", "--round", "1", "--output-dir", "/nonexistent-xyz"]) == 1


def test_sync_candidate_real_runtime() -> None:
    """The real interpreter syncs a patched spec against a MockTransport."""
    import httpx as _httpx

    from autobridge.runtime.http import HttpClient

    descriptor = None
    for index in range(120):
        cand = _descriptor(index)
        if cand["family"] == "rename" and cand["vendor"] in ("atlas", "harbor"):
            descriptor = cand
            break
    assert descriptor is not None
    case = beacon_run.beacon_case_for(descriptor)
    assert case is not None
    field = str(descriptor["params"]["field"])
    new_name = str(descriptor["params"]["new_name"])
    if descriptor["vendor"] == "atlas":
        wire = {
            "reservationID": "R1",
            "propertyID": "prop-001",
            "guestName": "Doe, John",
            "startDate": "2026-10-26",
            "endDate": "2026-10-28",
            "status": "confirmed",
            "balance": "125.00",
            "dateModified": "2026-10-27T12:00:00",
        }
        camel = {"guest_name": "guestName", "arrival_date": "startDate", "departure_date": "endDate",
                 "total_amount": "balance", "updated_at": "dateModified", "status": "status"}[field]
        wire[new_name] = wire.pop(camel)
        res_path, folio_path = "/api/v1.3/getReservations", "/api/v1.3/getTransactions"
    else:
        wire = {
            "reservation_id": "R1",
            "property_id": "prop-001",
            "guest_name": "Doe, John",
            "arrival_date": "2026-10-26",
            "departure_date": "2026-10-28",
            "status": "H1",
            "total_amount": "125.00",
            "currency": "USD",
            "updated_at": "2026-10-27T12:00:00",
        }
        wire[new_name] = wire.pop(field)
        res_path, folio_path = "/reservations", "/folios"

    seen_paths: list[str] = []

    def _handler(request: _httpx.Request) -> _httpx.Response:
        seen_paths.append(str(request.url))
        if request.url.path == res_path:
            return _httpx.Response(200, json={"success": True, "data": [wire], "count": 1, "total": 1,
                                              "gross_sum": "125.00", "next_cursor": None})
        if request.url.path == folio_path:
            return _httpx.Response(200, json={"success": True, "data": [], "count": 0, "total": 0,
                                              "gross_sum": "0.00", "next_cursor": None})
        return _httpx.Response(404, json={"msg": "not found"})

    transport = _httpx.MockTransport(_handler)

    def _factory(**kw: Any) -> _httpx.Client:
        return _httpx.Client(transport=transport, timeout=kw.get("timeout", 5))

    import copy as _copy

    candidate = _copy.deepcopy(case.spec)
    entity = candidate["entities"]["Reservation"]
    entity["fields"][field]["path"] = f"$.{new_name}"
    client = HttpClient("https://demo.invalid", auth=None, retry=None,
                        client_factory=_factory, sleep_fn=lambda _: None)
    from autobridge.engine.sync_run import execute_sync

    summary = execute_sync(candidate, client)
    assert summary["outcome"] == "Complete", summary.get("unmet_criteria")
    produced = beacon_run.canonical_to_records(summary.get("canonical_records", {}))
    assert produced["reservations"][0][field] is not None


def test_placeholder_env_and_summarize_edges() -> None:
    saved = {ref: os.environ.pop(ref, None) for ref in ("ATLAS_API_KEY", "HARBOR_API_KEY", "BENCH_API_KEY")}
    try:
        beacon_run._placeholder_credential_env()
        assert os.environ["ATLAS_API_KEY"] == beacon_run.BEACON_BENCH_CREDENTIAL
    finally:
        for ref, value in saved.items():
            if value is None:
                os.environ.pop(ref, None)
            else:
                os.environ[ref] = value
    empty = beacon_run.summarize_grades([])
    assert empty["overall"]["n"] == 0
    single = beacon_run.summarize_grades([
        beacon_run.BeaconArmGrade(arm="a", instance_index=0, trial=0, vendor="v", family="f",
                                  verified_completion=1, frontier_tokens=3)])
    assert single["overall"]["verified_completion"]["mean"] == 1.0


def test_verifier_post_serializes_decimal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Money stays Decimal to the wire: Decimal serializes exactly."""
    from decimal import Decimal as _Decimal

    import httpx as _httpx

    seen: dict[str, str] = {}

    class _Ok:
        status_code = 200

        def json(self) -> object:
            return {"verdict": "pass", "score": 1.0, "failed_checks": []}

    def _capture(_url: str, content: str = "", **_k: object) -> object:
        seen["content"] = content
        return _Ok()

    monkeypatch.setattr(_httpx, "post", _capture)
    out = beacon_run._verifier_post("http://x", "/beacon/grade",
                                    {"eval_seed_hex": "ab", "index": 0, "amount": _Decimal("125.00")})
    assert out["verdict"] == "pass"
    assert '"125.00"' in seen["content"]


def test_run_guard_allows_protocol_only_dir(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, _clean_bench_env: Any
) -> None:
    """A result dir holding only protocol.md (rehearsal freeze) does not trip the no-re-roll guard."""
    out = tmp_path / "r"
    out.mkdir()
    (out / "protocol.md").write_text("# rehearsal\n", encoding="utf-8")
    monkeypatch.setattr(beacon_run.beacon_client, "fetch_chain_info", lambda _b: dict(FAKE_INFO))
    monkeypatch.setattr(beacon_run.beacon_client, "fetch_latest",
                        lambda _b: {"round": 9999999, "randomness": "ab" * 32,
                                    "signature": "cd" * 96, "previous_signature": "ef" * 96})
    verified = beacon_run.beacon_client.VerifiedRound(
        round=9999999, randomness_hex="ab" * 32, eval_seed_hex=EVAL_SEED_HEX, published_at_utc=1893456000)
    monkeypatch.setattr(beacon_run.beacon_client, "fetch_verified_round",
                        lambda _b, _r, **_: verified)
    monkeypatch.setattr(beacon_run, "push_beacon", lambda _s, i, _v, **_: _descriptor(i))
    monkeypatch.setattr(beacon_run, "sync_candidate",
                        lambda _s, _u, **_k: {"outcome": "Complete", "canonical_records": {}})
    _fake_verifier(monkeypatch, records_verdict="pass")
    result = beacon_run.run_beacon(
        _protocol(faults=1), config=None,
        runners={"autobridge_cold": lambda _i: _engine_result()},
        verifier_url="http://fake", base_url="https://example.invalid", output_dir=out)
    assert len(result.grades) == 1


# ---------------------------------------------------------------------------
# REQ-062: every arm really attempts every instance, every row gradable
# ---------------------------------------------------------------------------


def _http_descriptor(pred: Any) -> dict[str, Any]:
    for index in range(60):
        cand = _descriptor(index)
        if cand["vendor"] in ("atlas", "harbor", "legacystay", "meridian", "fias-pabx") and pred(cand):
            return cand
    raise AssertionError("no matching beacon instance in range(60)")


def test_sanity_flags_silent_repair_and_spares_observed_wire(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-063 item 3: the bench sanity report flags a repair instance whose
    faulted canonical output equals the baseline with no drift signals
    (generator bug), and spares a repair the detector sees on the wire
    (solvable: the ladder repairs from the observed signals) and a no-patch
    instance with identical output."""
    from types import SimpleNamespace as _NS

    import bench.faults as _faults

    silent = {"index": 0, "vendor": "atlas", "family": "rename",
              "params": {"entity": "reservation", "field": "guest_name", "new_name": "zzQ"}}
    summary = {"outcome": "Complete", "unmet_criteria": [], "operation_results": {},
               "canonical_records": {"Reservation": [{"reservation_id": "R1"}]}}
    monkeypatch.setattr(_faults, "beacon_sync_pair", lambda **_: (dict(summary), dict(summary)))
    monkeypatch.setattr(beacon_run, "derive_beacon_diagnostics",
                        lambda *_, **__: _NS(signals=(), drift_class="UNKNOWN",
                                             classification_reason="none", faulted_outcome="Complete"))
    flags = beacon_run.sanity_unchanged_repairs(EVAL_SEED_HEX, [silent])
    assert len(flags) == 1 and flags[0]["index"] == 0
    assert flags[0]["expected"] == "repair"

    # The detector sees the wire drift: not a silent no-op, no flag.
    monkeypatch.setattr(beacon_run, "derive_beacon_diagnostics",
                        lambda *_, **__: _NS(signals=(_NS(kind="pagination_changed"),),
                                             drift_class="PAGINATION_CHANGE",
                                             classification_reason="wire", faulted_outcome="Complete"))
    assert beacon_run.sanity_unchanged_repairs(EVAL_SEED_HEX, [silent]) == []

    # A no-patch instance with identical output is correct, never flagged.
    nopatch = {"index": 10, "vendor": "atlas", "family": "transport_auth",
               "params": {"kind": "retry_after", "retry_after_s": 1}}
    assert beacon_run.sanity_unchanged_repairs(EVAL_SEED_HEX, [nopatch]) == []


def test_sanity_spares_wire_observed_repair_on_real_serving() -> None:
    """REQ-063 item 3: a repair whose fault the detector sees on the wire
    (Meridian body_cursor rename: canonical equal on a single-page dataset,
    4 drift signals) is solvable and never flagged — the signals spare it."""
    inst = faultgen.generate_instance(EVAL_SEED, 18)
    assert inst["family"] == "pagination", inst
    descriptor = {"index": 18, "vendor": inst["vendor"], "family": inst["family"],
                  "params": dict(inst["params"])}
    assert beacon_run.sanity_unchanged_repairs(EVAL_SEED_HEX, [descriptor]) == []


def test_run_beacon_fails_loud_on_silent_repair(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path,
                                                _clean_bench_env: Any) -> None:
    """REQ-063 item 3: a rehearsal with a silent repair instance refuses the
    run before spending any arm budget (fail loudly, naming the instance)."""
    monkeypatch.setattr(beacon_run.beacon_client, "fetch_chain_info", lambda _base_url: dict(FAKE_INFO))
    monkeypatch.setattr(
        beacon_run.beacon_client, "fetch_latest",
        lambda _base_url: {"round": 9999999, "randomness": "ab" * 32,
                          "signature": "cd" * 96, "previous_signature": "ef" * 96})
    verified = beacon_run.beacon_client.VerifiedRound(
        round=9999999, randomness_hex="ab" * 32, eval_seed_hex=EVAL_SEED_HEX, published_at_utc=1893456000)
    monkeypatch.setattr(beacon_run.beacon_client, "fetch_verified_round",
                        lambda _base_url, _round, **_: verified)
    monkeypatch.setattr(beacon_run, "push_beacon", lambda _seed, index, _vendor, **_: _descriptor(index))
    monkeypatch.setattr(
        beacon_run, "sanity_unchanged_repairs",
        lambda _seed, _descriptors, _vendors=None: [{"index": 0, "vendor": "atlas", "family": "rename",
                                                   "params": {}, "expected": "repair",
                                                   "faulted_outcome": "Complete"}])
    protocol = _protocol(faults=1)
    with pytest.raises(BeaconError, match="generator bug"):
        beacon_run.run_beacon(
            protocol, config=None, runners={}, verifier_url=None,
            base_url="https://example.invalid", output_dir=tmp_path / "beacon" / "9999999",
        )


def test_engine_no_drift_reports_complete_no_patch(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-063 item 1: no drift signals + faulted Complete → every engine
    arm reports Complete, no patch, no question (as production does: no
    repair run opens). The ladder is not called (0 tokens); the row reads
    "no drift detected".

    Real offline engine arm (FakeProvider, deterministic ladder only) on a
    throttled instance whose faulted sync is Complete with no signals. The
    instance expects no_patch, so the row grades as a pass.
    """
    from autobridge.llm.provider import FakeProvider
    from bench.harness import HarnessConfig as _Cfg

    descriptor = _http_descriptor(
        lambda c: c["vendor"] == "atlas" and c["family"] == "transport_auth")
    case = beacon_run.beacon_case_for(descriptor)
    assert case is not None
    diag = beacon_run.derive_beacon_diagnostics(
        case.spec, descriptor=descriptor, eval_seed_hex=EVAL_SEED_HEX,
        vendor=str(descriptor["vendor"]), connector_id=case.connector_id)
    assert not diag.signals, descriptor
    assert diag.faulted_outcome == "Complete", descriptor

    import httpx as _httpx

    class _Resp:
        status_code = 200

        def __init__(self, payload: Any) -> None:
            self._payload = payload

        def json(self) -> Any:
            return self._payload

    def _post(url: str, **k: Any) -> Any:
        import json as _json

        raw = k.get("content", "")
        try:
            body = _json.loads(raw) if isinstance(raw, str) else {}
        except ValueError:
            body = {}
        if url.endswith("/beacon/records"):
            return _Resp({"verdict": "pass", "score": 1.0, "failed_checks": []})
        if url.endswith("/beacon/grade"):
            # Mirror the real no_patch rule: Complete with no patch passes.
            passed = body.get("outcome") == "Complete" and not body.get("patch_reached_release")
            return _Resp({"passed": passed, "reason": "no_patch: complete without a patch", "accepted": ["no_patch"]})
        raise AssertionError(f"unexpected verifier path {url}")

    monkeypatch.setattr(_httpx, "post", _post)
    protocol = _protocol(arms=("autobridge_cold",))
    grades, reports = beacon_run.run_instance_trial(
        protocol, EVAL_SEED_HEX, descriptor, 0, config=_Cfg(trials_per_scenario=1),
        runners={}, provider=FakeProvider(), verifier_url="http://fake-verifier")
    (grade,) = grades
    assert grade.executed is True
    assert grade.frontier_tokens == 0 and grade.small_tokens == 0
    assert grade.blocked_step is None
    assert grade.grade_reason is not None and "no drift detected" in grade.grade_reason
    assert grade.verified_completion == 1
    report = reports["autobridge_cold"]
    assert report["decision"] == "propose" and not report["has_patch"]


def test_fias_layout_derive_attempts_with_reason() -> None:
    """REQ-062 items 1+2: PABX instances derive in-process and state why they block.

    A rename of a mapped wire id attempts the ladder (SCHEMA_RENAME with
    the missing id named); the row must name the ladder step since no
    rule matches the multi-candidate ambiguity.
    """
    from types import SimpleNamespace as _NS

    descriptor = _http_descriptor(
        lambda c: c["vendor"] == "fias-pabx" and c["family"] == "fias_layout"
        and "RN" in (c["params"].get("renamed") or {}))
    case = beacon_run.beacon_case_for(descriptor)
    assert case is not None and case.connector_id == "fiaspabx"
    diag = beacon_run.derive_beacon_diagnostics(
        case.spec, descriptor=descriptor, eval_seed_hex=EVAL_SEED_HEX,
        vendor="fias-pabx", connector_id=case.connector_id)
    assert diag.drift_class == "SCHEMA_RENAME"
    assert diag.signals, descriptor
    result = _NS(arm="autobridge_cold", ran=True, outcome=None, error=None,
                 spec={"decision": "escalate", "candidate_patch": [],
                       "ladder_level_reached": 4, "rule_id": None,
                       "beacon_diagnostics": {"drift_class": diag.drift_class,
                                              "n_signals": len(diag.signals),
                                              "n_missing": 1, "n_added": 2,
                                              "classification_reason": diag.classification_reason}})
    report = beacon_run._arm_outcome_for(result, False)
    assert report["outcome"] == "Blocked"
    step = beacon_run._blocked_step_for(result, report)
    assert step is not None and step.startswith("ladder:")
    assert "no rule matched SCHEMA_RENAME" in step
    summary = beacon_run._attempt_summary_for(result, None)
    assert "engine: detected SCHEMA_RENAME" in summary


def test_fias_layout_mapping_intact_is_detection_block() -> None:
    """A FIAS addition that breaks no mapping blocks at detection, not the ladder."""
    from types import SimpleNamespace as _NS

    result = _NS(arm="autobridge_cold", ran=True, outcome=None, error=None,
                 spec={"decision": "escalate", "candidate_patch": [],
                       "ladder_level_reached": 4, "rule_id": None,
                       "beacon_diagnostics": {"drift_class": "UNKNOWN", "n_signals": 0,
                                              "n_missing": 0, "n_added": 2,
                                              "classification_reason": "No drift signals observed"}})
    report = beacon_run._arm_outcome_for(result, False)
    step = beacon_run._blocked_step_for(result, report)
    assert step is not None and step.startswith("detection:")


def test_arm_outcome_maps_frontier_escalation() -> None:
    """REQ-062 item 4: a frontier escalation is Blocked with its question, never Complete."""
    from types import SimpleNamespace as _NS

    escalate = _NS(ran=True, outcome="escalated",
                   spec={"answer_kind": "escalate", "question": "Net or gross?",
                         "rationale": "ambiguous", "proposal_patch": []})
    report = beacon_run._arm_outcome_for(escalate, False)
    assert report == {"outcome": "Blocked", "patch_reached_release": False, "operator_question": True}
    assert beacon_run._patch_summary_for(escalate, None) is not None
    assert "Net or gross?" in (beacon_run._patch_summary_for(escalate, None) or "")


def test_arm_outcome_maps_engine_escalation_with_question() -> None:
    """REQ-063 item 2: an engine L4 escalation carries the ladder's question
    (operator_question=True); without one the row says the ladder wrote
    none (operator_question=False, never an invented question)."""
    from types import SimpleNamespace as _NS

    with_question = _NS(ran=True, outcome=None, error=None,
                        spec={"decision": "escalate", "candidate_patch": [],
                              "ladder_level_reached": 4, "rule_id": None,
                              "question": "Which mapping is correct?",
                              "beacon_diagnostics": {"drift_class": "UNKNOWN", "n_signals": 3,
                                                     "n_missing": -1, "n_added": -1,
                                                     "classification_reason": "r"}})
    report = beacon_run._arm_outcome_for(with_question, False)
    assert report == {"outcome": "Blocked", "patch_reached_release": False, "operator_question": True}
    summary = beacon_run._attempt_summary_for(with_question, None)
    assert "with operator question" in summary
    step = beacon_run._blocked_step_for(with_question, report)
    assert step is not None and "operator question carried" in step
    assert beacon_run._patch_summary_for(with_question, None) == "escalation, no patch: Which mapping is correct?"

    without_question = _NS(ran=True, outcome=None, error=None,
                           spec={"decision": "escalate", "candidate_patch": [],
                                 "ladder_level_reached": 4, "rule_id": None,
                                 "question": "",
                                 "beacon_diagnostics": {"drift_class": "UNKNOWN", "n_signals": 3,
                                                        "n_missing": -1, "n_added": -1,
                                                        "classification_reason": "r"}})
    bare = beacon_run._arm_outcome_for(without_question, False)
    assert bare == {"outcome": "Blocked", "patch_reached_release": False, "operator_question": False}
    bare_summary = beacon_run._attempt_summary_for(without_question, None)
    assert "the ladder wrote no question" in bare_summary
    bare_step = beacon_run._blocked_step_for(without_question, bare)
    assert bare_step is not None and "the ladder wrote no question" in bare_step
    assert beacon_run._patch_summary_for(without_question, None) == (
        "escalation, no patch (the ladder wrote no question)")


def test_token_accounting_frontier_vs_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-062 item 5: frontier input tokens never land in small_tokens."""
    from types import SimpleNamespace as _NS

    from bench.harness import HarnessConfig as _Cfg

    descriptor = _http_descriptor(lambda c: c["family"] == "rename")
    _fake_verifier(monkeypatch, records_verdict="pass")

    def _arm_result(arm: str, **tokens: Any) -> Any:
        from bench.harness import make_trial_inputs as _inputs

        return _NS(arm=arm, inputs=_inputs(_Cfg(), "beacon:0", 0), ran=True,
                   frontier_tokens=tokens.get("frontier_tokens", 0),
                   input_tokens=tokens.get("input_tokens", 0),
                   output_tokens=tokens.get("output_tokens", 0),
                   reasoning_tokens=tokens.get("reasoning_tokens", 0),
                   cost_usd=None, model="m", error=None,
                   spec={"decision": "escalate", "candidate_patch": []},
                   outcome=None)

    protocol = _protocol(arms=("frontier_oneshot", "autobridge_cold"))
    grades, _reports = beacon_run.run_instance_trial(
        protocol, EVAL_SEED_HEX, descriptor, 0, config=_Cfg(trials_per_scenario=1),
        runners={"frontier_oneshot": lambda _i: _arm_result(
                     "frontier_oneshot", frontier_tokens=150, input_tokens=100,
                     output_tokens=50, reasoning_tokens=20),
                 "autobridge_cold": lambda _i: _arm_result(
                     "autobridge_cold", frontier_tokens=0, input_tokens=5,
                     output_tokens=0, reasoning_tokens=0)},
        verifier_url="http://fake-verifier")
    by_arm = {g.arm: g for g in grades}
    one_shot, cold = by_arm["frontier_oneshot"], by_arm["autobridge_cold"]
    assert (one_shot.frontier_tokens, one_shot.small_tokens) == (150, 0)
    assert (one_shot.input_tokens, one_shot.output_tokens, one_shot.reasoning_tokens) == (100, 50, 20)
    assert (cold.frontier_tokens, cold.small_tokens) == (0, 5)
    assert "frontier:" in (one_shot.grade_reason or "")
    assert "engine:" in (cold.grade_reason or "")


def test_beacon_frontier_context_matches_engine_task() -> None:
    """REQ-062 item 6: frontier arms get the engine's spec + fault evidence.

    Both frontier arms render the same file-based prompts over one shared
    context whose spec is byte-identical to the engine's pre-drift spec.
    """
    from bench.evidence import spec_to_yaml as _spec_to_yaml
    from bench.harness import HarnessConfig as _Cfg
    from bench.harness import make_trial_inputs as _inputs

    descriptor = _http_descriptor(
        lambda c: c["vendor"] == "harbor" and c["family"] == "rename")
    case = beacon_run.beacon_case_for(descriptor)
    assert case is not None
    inputs = _inputs(_Cfg(), f"beacon:{descriptor['index']}", 0)
    ctx = beacon_run.beacon_bench_context(descriptor, case, inputs, EVAL_SEED_HEX)
    assert ctx.pre_drift_spec_yaml == _spec_to_yaml(case.spec)
    assert "sync outcome" in ctx.evidence_text
    assert ctx.content_hash()

    from autobridge.llm.provider import FakeProvider
    from bench import ablation as _ablation

    one = _ablation.run_frontier_oneshot_arm(inputs, provider=FakeProvider(), case=case, context=ctx)
    agent = _ablation.run_frontier_agent_arm(inputs, case=case, context=ctx)
    assert one.context_hash == ctx.content_hash() == agent.context_hash
    assert (one.prompt or {}).get("id") == "frontier_oneshot"
    assert (agent.prompt or {}).get("id") == "frontier_agent"


def test_default_beacon_config_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-062 item 6: beacon runs use the dev ablation's 200k ceiling, env-overridable."""
    monkeypatch.delenv("BENCH_TOKEN_CEILING", raising=False)
    monkeypatch.delenv("BENCH_WALL_CLOCK", raising=False)
    cfg = beacon_run.default_beacon_config()
    assert cfg.token_ceiling == 200000
    assert cfg.wall_clock_s == 600.0
    monkeypatch.setenv("BENCH_TOKEN_CEILING", "0")
    assert beacon_run.default_beacon_config().token_ceiling == 0
    monkeypatch.setenv("BENCH_TOKEN_CEILING", "abc")
    import pytest as _pytest

    with _pytest.raises(beacon_run.BeaconError):
        beacon_run.default_beacon_config()
    monkeypatch.delenv("BENCH_TOKEN_CEILING", raising=False)
    monkeypatch.delenv("BENCH_WALL_CLOCK", raising=False)
    assert beacon_run._cli_config(None, None) is None
    assert beacon_run._cli_config(10, None).token_ceiling == 10


def test_summarize_grades_total_tokens_and_agreement() -> None:
    """REQ-062 items 4+5: summary totals agree with rows; per-arm min/med/max totals."""
    from bench.beacon_run import BeaconArmGrade as _Grade

    rows = [_Grade(arm="autobridge_cold", instance_index=i, trial=0, vendor="atlas",
                   family="rename", wrong_patch_released=int(i == 0),
                   input_tokens=2 * i, output_tokens=i)
            for i in range(4)]
    summary = beacon_run.summarize_grades(rows)
    cold = summary["by_arm"]["autobridge_cold"]
    assert cold["wrong_patch_released"]["total"] == 1 == sum(g.wrong_patch_released for g in rows)
    assert cold["total_tokens"]["min"] == 0
    assert cold["total_tokens"]["max"] == 9
    assert cold["total_tokens"]["median"] == 4.5


def test_fias_feed_layout_aliases_and_added() -> None:
    """REQ-062 item 2: the FIAS serving layer exposes aliases + added (observation, not diagnosis)."""
    from bench import faults as _faults

    layout = _faults.beacon_fias_layout(EVAL_SEED_HEX, 24)
    assert layout["family"] == "fias_layout"
    assert layout["aliases"] == {"TX": "TA"}
    assert layout["added"] == []
    row = _faults._fias_feed_row(layout["aliases"], layout["added"])
    assert row["TX"] == "1250" and row["RN"] == "101"
    assert "TA" not in row
    assert row["SO"] == "TEL"
    link = _faults.beacon_fias_layout(EVAL_SEED_HEX, 4)
    assert link["family"] == "fias_link"
    assert link["aliases"] == {} and link["added"] == []
    with __import__("pytest").raises(ValueError):
        _faults.beacon_fias_layout(EVAL_SEED_HEX, 0)


def test_fias_beacon_sync_pair_serves_announced_and_baseline() -> None:
    """REQ-062 item 2: faulted feed carries the announced layout, baseline the pre-drift shape."""
    from bench import faults as _faults

    def _ps_keys(summary: Any) -> set[str]:
        for op_result in (summary.get("operation_results", {}) or {}).values():
            if isinstance(op_result, dict):
                records = op_result.get("records")
            else:
                records = getattr(op_result, "records", None)
            if isinstance(records, list) and records:
                return {str(k) for r in records if isinstance(r, dict) for k in r}
        raise AssertionError("no PS wire records")

    descriptor = _http_descriptor(
        lambda c: c["vendor"] == "fias-pabx" and c["family"] == "fias_layout"
        and "RN" in (c["params"].get("renamed") or {}))
    case = beacon_run.beacon_case_for(descriptor)
    assert case is not None
    faulted, baseline = _faults.beacon_sync_pair(
        vendor="fias-pabx", eval_seed_hex=EVAL_SEED_HEX, index=int(descriptor["index"]), spec=case.spec)
    assert faulted.get("outcome") in ("Complete", "Degraded", "Blocked")
    assert baseline.get("outcome") == "Complete"
    faulted_keys, baseline_keys = _ps_keys(faulted), _ps_keys(baseline)
    assert "RN" in baseline_keys and "RN" not in faulted_keys
    new_name = str(descriptor["params"]["renamed"]["RN"])
    assert new_name in faulted_keys and new_name not in baseline_keys
