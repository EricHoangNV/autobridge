"""REQ-084 — meridian nesting reachable; preflight proves repairs; L3 traces kept.

1. ``customers_get_all`` unwraps a nesting-wrapped internal record (like
   the other two serializers): under nesting it serves the guest names
   and correct account ids instead of ``ACC-None`` + dropped names. The
   audit's other derived endpoints (atlas detail/guest + id lookups,
   harbor/legacy id lookups) survive nesting too.
2. ``preflight_prove_repairs`` proves every drawn ``repair`` instance:
   the reference repair through the real ``sync_candidate`` path and the
   judge; an unprovable repair fails loudly with instance + patch.
3. The trace recorder persists redacted model request/response per
   instance (secrets: key names only).
"""

from __future__ import annotations

import json
from typing import Any

import pytest


def _eval_seed_hex() -> str:
    return "ab" * 32


def _meridian_nesting_instance() -> tuple[str, int, dict]:
    import simulators.common.faultgen as faultgen

    seed_hex = _eval_seed_hex()
    for index in range(60):
        if index % 5 != 3:  # meridian position in the vendor round-robin
            continue
        inst = faultgen.generate_instance(bytes.fromhex(seed_hex), index)
        if (
            str(inst.get("vendor")) == "meridian"
            and str(inst.get("family")) == "nesting"
            and str(dict(inst.get("params", {})).get("entity")) == "reservation"
            and str(inst.get("expected")) == "repair"
        ):
            return seed_hex, index, inst
    raise AssertionError("no meridian reservation nesting repair in scan")


def test_meridian_customers_survive_nesting() -> None:
    """Item 1: customers serve guest names + account ids under nesting."""
    from bench import beacon_run, faults

    seed_hex, index, _inst = _meridian_nesting_instance()
    vendors = beacon_run.load_vendors()
    spec = beacon_run.engine_spec_for({"index": index, "vendor": "meridian"}, vendors)
    assert spec is not None
    faulted, baseline = faults.beacon_sync_pair(
        vendor="meridian", eval_seed_hex=seed_hex, index=index, spec=spec
    )

    def customers(summary: dict) -> list[dict]:
        for _op, res in (summary.get("operation_results", {}) or {}).items():
            recs = res.get("records") if isinstance(res, dict) else getattr(res, "records", None)
            if isinstance(recs, list) and recs and isinstance(recs[0], dict) and "FirstName" in recs[0]:
                return [r for r in recs if isinstance(r, dict)]
        raise AssertionError("no customers operation served")

    faulted_rows = customers(faulted)
    baseline_rows = customers(baseline)
    assert len(faulted_rows) == 30
    # No join data lost to the wrap: every id resolves (never ACC-None).
    for row in faulted_rows:
        assert str(row.get("Id", "")).startswith("ACC-"), row
        assert str(row.get("Id", "")) != "ACC-None", row
    # Unwrapping serves exactly the baseline customer data (nesting the
    # projected reservation wire must not change what customers serve —
    # a baseline None stays a null, anything else keeps its names).
    def _row_key(r: dict) -> tuple[str, str, str]:
        return (str(r.get("Id")), str(r.get("FirstName")), str(r.get("LastName")))

    assert sorted(map(_row_key, faulted_rows)) == sorted(map(_row_key, baseline_rows))
    assert sum(1 for r in faulted_rows if r.get("FirstName")) > 0


def test_meridian_reference_nesting_repair_passes_judge() -> None:
    """Item 1 acceptance: the reference nesting repair passes the judge."""
    import importlib

    import verifier.beacon as vbeacon
    from autobridge.spec.patch import apply_patch
    from bench import beacon_run, faults
    from bench import reference_patch as refpatch

    seed_hex, index, inst = _meridian_nesting_instance()
    vendors = beacon_run.load_vendors()
    spec = beacon_run.engine_spec_for({"index": index, "vendor": "meridian"}, vendors)
    assert spec is not None
    faulted, _baseline = faults.beacon_sync_pair(
        vendor="meridian", eval_seed_hex=seed_hex, index=index, spec=spec
    )

    def wire_by_op(summary: dict) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for op, res in (summary.get("operation_results", {}) or {}).items():
            recs = res.get("records") if isinstance(res, dict) else getattr(res, "records", None)
            if isinstance(recs, list):
                out[str(op)] = [r for r in recs if isinstance(r, dict)]
        return out

    patch = refpatch.reference_patch_for(
        inst, spec,
        faulted_wire=wire_by_op(faulted), baseline_wire=wire_by_op(_baseline),
    )
    assert patch, "no reference patch for meridian reservation nesting"
    patched = apply_patch(dict(spec), list(patch))
    with faults.isolated_sim_state(), faults.fake_env():
        from simulators.common import beacon_serve as _beacon
        from simulators.common import state as _sim_state

        _beacon.set_beacon(seed_hex, index)
        app = importlib.import_module(faults._BEACON_VENDOR_APPS["meridian"]).app
        import urllib.parse as _urlparse

        from autobridge.runtime.http import resolve_env_placeholders as _resolve

        base_path = _urlparse.urlsplit(_resolve(str(spec["source"]["base_url"]))).path.rstrip("/")
        fake_base = faults._FAKE_BASE_URL + base_path

        def factory(**_kw: Any) -> Any:
            from starlette.testclient import TestClient

            return TestClient(app, base_url=fake_base)

        try:
            summary = beacon_run.sync_candidate(patched, "http://localhost:9", client_factory=factory)
        finally:
            _sim_state.set_beacon_state(None, None)
    assert summary.get("outcome") == "Complete", (summary.get("outcome"), summary.get("unmet_criteria"))
    produced = beacon_run.canonical_to_records(summary.get("canonical_records", {}))
    base_records = beacon_run.unfaulted_baseline_records("meridian", seed_hex, index, spec)
    judged = vbeacon.judge_produced_records(inst, produced, patched, base_records)
    assert judged["verdict"] == "pass", (judged["failed_checks"], patch)


def test_atlas_derived_endpoints_survive_nesting() -> None:
    """Item 1 audit: atlas detail/guest unwrap a nested internal record."""
    from simulators.atlaspms import app as atlas_app

    rec = {
        "reservation_id": "RES-1", "property_id": "prop-001",
        "guest_name": "Ada Lovelace", "status": "confirmed",
        "arrival_date": "2026-11-03", "departure_date": "2026-11-06",
        "total_amount": "200.00", "currency": "USD", "updated_at": "2026-11-03T10:00:00+00:00",
    }
    wrapped = {"stay": dict(rec)}
    guest = atlas_app.to_wire_guest(wrapped)
    assert guest.get("firstName") == "Ada", guest
    assert guest.get("lastName") == "Lovelace", guest
    detail = atlas_app.to_wire_reservation_detail(wrapped)
    # The detail nests like the list item so a path patch repairs it.
    assert set(detail) == {"stay"}, detail
    inner_detail = detail["stay"]
    assert inner_detail.get("guestName") == "Ada Lovelace", inner_detail
    assert str(inner_detail.get("total")) == "200.00", inner_detail


def _protocol(faults: int, arms: tuple = ("autobridge_cold",)) -> Any:
    from bench.beacon_run import BeaconProtocol

    return BeaconProtocol(
        round_after_utc="2026-11-02T00:00:00+00:00",
        target_round=6603643,
        faults=faults,
        trials=1,
        arms=arms,
        engine_commit="c" * 40,
        generator_commit="d" * 40,
        frozen_at_utc="2026-11-02T00:00:00+00:00",
        rehearsal=True,
        frontier_model="muse-spark-1.3",
        agent_model=None,
        token_ceiling=200000,
        wall_clock_s=600.0,
    )


def test_preflight_prove_repairs_drawn_instances() -> None:
    """Item 2: every drawn repair in the protocol is proven, loudly."""
    from bench import beacon_run

    def fake_post(_url: str, path: str, _payload: dict, _timeout_s: float = 30.0) -> dict:
        assert path == "/beacon/records"
        return {"verdict": "pass", "failed_checks": []}

    old = beacon_run._verifier_post
    beacon_run._verifier_post = fake_post  # type: ignore[assignment]
    try:
        with beacon_run._placeholder_credential_env():
            report = beacon_run.preflight_prove_repairs(
                _protocol(5), eval_seed_hex=_eval_seed_hex(), verifier_url="http://verifier-test",
            )
    finally:
        beacon_run._verifier_post = old
    assert report["proved"], "no repair instance proven in 5 draws"
    assert all(isinstance(i, int) for i in report["proved"])


def test_preflight_prove_repairs_fails_loudly_on_unreachable() -> None:
    """Item 2: an unreachable repair key fails with instance + patch info."""
    from bench import beacon_run
    from bench.beacon_run import BeaconError

    def no_patch(_inst: Any, _spec: Any, **_kw: Any) -> None:
        return None

    import bench.reference_patch as refpatch

    saved = refpatch.reference_patch_for
    refpatch.reference_patch_for = no_patch  # type: ignore[assignment]
    try:
        with beacon_run._placeholder_credential_env(), pytest.raises(
            BeaconError, match="no reference patch for repair instance"
        ):
            beacon_run.preflight_prove_repairs(
                _protocol(5), eval_seed_hex=_eval_seed_hex(), verifier_url=None,
            )
    finally:
        refpatch.reference_patch_for = saved


def test_trace_recorder_redacts_secrets() -> None:
    """Item 3: the recorder masks secret values, keeps shapes."""
    from autobridge.llm.provider import Completion, FakeProvider, Usage
    from bench.beacon_run import _redact_trace_value, _TraceRecordingProvider

    assert _redact_trace_value("ClientToken", "abc123") == "***"
    assert _redact_trace_value("brief", "api_key: hunter2") == "api_key:***"
    long_text = "x" * 3000
    assert _redact_trace_value("content", long_text).endswith("… (truncated)")
    prov = FakeProvider(responses={
        "frontier": Completion(text="Bearer live-secret-token", usage=Usage(input_tokens=1, output_tokens=2)),
    })
    rec = _TraceRecordingProvider(prov)
    assert rec.frontier_model == "fake-frontier"  # delegation works
    rec.complete(
        [{"role": "user", "content": "ClientToken: abc123 please"}],
        max_tokens=10, tier="frontier", purpose="l3-generate-patch",
    )
    assert len(rec.calls) == 1
    call = rec.calls[0]
    assert call["request"][0]["content"] == "ClientToken:*** please"
    assert "live-secret-token" not in call["response_text"]
    assert call["usage"]["input_tokens"] == 1


def test_run_instance_trial_writes_arm_trace(tmp_path: Any) -> None:
    """Item 3: one trace file per arm lands next to grades.jsonl."""
    import pathlib

    from autobridge.llm.provider import Completion, FakeProvider, Usage
    from bench import beacon_run
    from bench.harness import HarnessConfig as _Cfg
    from tests.unit.test_autobridge_beacon_run import _descriptor

    descriptor = _descriptor(0)
    protocol = _protocol(1, arms=("frontier_oneshot",))
    provider = FakeProvider(responses={
        "frontier": Completion(text='{"patch": []}', usage=Usage(input_tokens=3, output_tokens=4)),
    })
    grades, _reports = beacon_run.run_instance_trial(
        protocol, "05ec57a0c1e763e985c9ac18df2313a98392a9c24ecbceefd0b21216a9beaefa",
        descriptor, 0, config=_Cfg(trials_per_scenario=1),
        runners={}, provider=provider, verifier_url=None, trace_dir=tmp_path,
    )
    assert len(grades) == 1
    trace_file = pathlib.Path(tmp_path) / "traces" / "instance-0-trial-0-frontier_oneshot.json"
    assert trace_file.exists(), sorted((pathlib.Path(tmp_path) / "traces").glob("*"))
    payload = json.loads(trace_file.read_text(encoding="utf-8"))
    assert payload["arm"] == "frontier_oneshot"
    assert payload["calls"], "model request/response missing from trace"
    assert payload["calls"][0]["request"], "request messages missing"


def test_run_arm_records_engine_and_agent_traces() -> None:
    """Item 3: engine arms record provider calls, the agent its brief."""
    import simulators.common.faultgen as faultgen
    from autobridge.llm.provider import FakeProvider
    from bench import beacon_run
    from bench.harness import HarnessConfig as _Cfg
    from bench.harness import make_trial_inputs

    seed_hex = "05ec57a0c1e763e985c9ac18df2313a98392a9c24ecbceefd0b21216a9beaefa"
    inst = faultgen.generate_instance(bytes.fromhex(seed_hex), 0)
    descriptor = {"index": 0, "vendor": inst["vendor"],
                  "family": inst["family"], "params": inst["params"]}
    vendors = beacon_run.load_vendors()
    case = beacon_run.beacon_case_for(descriptor, vendors)
    assert case is not None
    inputs = make_trial_inputs(_Cfg(trials_per_scenario=1), "beacon:0", 0)
    sink: list[dict] = []
    engine = beacon_run._run_arm(
        "autobridge_cold", inputs, case, {}, FakeProvider(), None,
        descriptor=descriptor, eval_seed_hex=seed_hex, trace_sink=sink,
    )
    assert engine.ran
    assert len(sink) == 1 and sink[0]["arm"] == "autobridge_cold"
    assert isinstance(sink[0]["calls"], list)
    agent_sink: list[dict] = []
    agent = beacon_run._run_arm(
        "frontier_agent", inputs, case, {}, FakeProvider(), None,
        descriptor=descriptor, eval_seed_hex=seed_hex, trace_sink=agent_sink,
    )
    assert agent is not None
    assert len(agent_sink) == 1 and agent_sink[0]["arm"] == "frontier_agent"
    assert agent_sink[0]["brief"], "agent brief missing from trace"
