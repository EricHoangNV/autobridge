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
from autobridge.bench import beacon_run
from autobridge.bench.beacon_run import BeaconError

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
        if descriptor["vendor"] == "fias-pabx":
            assert case is None
            seen.add("fias:" + descriptor["family"])
            continue
        assert case is not None, descriptor
        seen.add(descriptor["family"])
        assert case.drift_class != "UNKNOWN"
    for family in ("rename", "nesting", "pagination", "code_list", "semantic", "dates", "transport_auth"):
        assert family in seen, f"family {family} not covered in 60 instances"


def test_semantic_hypotheses_decoy_empty() -> None:
    assert beacon_run._semantic_hypotheses({"kind": "decoy", "decoy_field": "X"}) == ()
    hyps = beacon_run._semantic_hypotheses({"kind": "gross_net"})
    assert len(hyps) == 2


def test_engine_spec_failure_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    from autobridge.bench import ablation as _ablation

    monkeypatch.setattr(_ablation, "_load_reference_spec", lambda _conn: (_ for _ in ()).throw(KeyError("nope")))
    assert beacon_run.engine_spec_for(_descriptor(0)) is None


def test_run_arm_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    from autobridge.bench import ablation as _ablation

    inputs = beacon_run_load_inputs()
    with pytest.raises(BeaconError):
        beacon_run._run_arm("nope", inputs, None, {}, None, None)
    monkeypatch.setattr(_ablation, "run_frontier_oneshot_arm", lambda _i, **_: "oneshot")
    assert beacon_run._run_arm("frontier_oneshot", inputs, None, {}, "prov", None) == "oneshot"
    monkeypatch.setattr(_ablation, "run_frontier_agent_arm", lambda _i: "agent")
    assert beacon_run._run_arm("frontier_agent", inputs, None, {}, None, None) == "agent"


def beacon_run_load_inputs() -> Any:
    from autobridge.bench.harness import HarnessConfig as _Cfg
    from autobridge.bench.harness import make_trial_inputs as _inputs

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
    from autobridge.bench.harness import HarnessConfig as _Cfg

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


def test_full_trial_records_fail_marks_wrong_patch(monkeypatch: pytest.MonkeyPatch) -> None:
    from autobridge.bench.harness import HarnessConfig as _Cfg

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
    assert grade.wrong_patch_released == 1


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
