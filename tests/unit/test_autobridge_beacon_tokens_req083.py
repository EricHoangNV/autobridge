"""REQ-083 B11: in/out per model tier on one basis for every arm."""

from __future__ import annotations

import json as _json
import types


def _protocol(arms: tuple) -> object:
    from bench.beacon_run import BeaconProtocol

    return BeaconProtocol(
        round_after_utc="2026-11-02T00:00:00+00:00",
        target_round=6603643,
        faults=10,
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


def _no_patch_descriptor() -> tuple[dict, object]:
    import simulators.common.faultgen as faultgen
    from bench import beacon_run

    vendors = beacon_run.load_vendors()
    for raw in ("ab" * 32, "cd" * 32):
        for index in range(60):
            inst = faultgen.generate_instance(bytes.fromhex(raw), index)
            if str(inst["family"]) == "transport_auth" and str(inst["expected"]) == "no_patch":
                desc = {"index": index, "vendor": str(inst["vendor"]),
                        "family": "transport_auth", "params": dict(inst["params"])}
                case = beacon_run.beacon_case_for(desc, vendors)
                assert case is not None
                return desc, raw
    raise AssertionError("no no_patch transport instance in scan")


def test_per_tier_tokens_same_basis_for_every_arm(monkeypatch) -> None:
    from bench import beacon_run
    from bench.harness import ArmResult

    _fake_verifier(monkeypatch, beacon_run)
    desc, seed_hex = _no_patch_descriptor()

    def cold_result(_inputs):
        return types.SimpleNamespace(
            arm="autobridge_cold", ran=True, error=None, outcome=None,
            input_tokens=1000, output_tokens=200,
            frontier_tokens=100, reasoning_tokens=50,
            frontier_input=800, frontier_output=150,
            small_input=200, small_output=50,
            cost_usd=0.001, model="m",
            spec={"decision": "propose", "candidate_patch": None,
                  "ladder_level_reached": 4},
        )

    def one_shot_result(_inputs):
        return types.SimpleNamespace(
            arm="frontier_oneshot", ran=True, error=None, outcome=None,
            input_tokens=5000, output_tokens=600,
            frontier_tokens=5600, reasoning_tokens=100,
            frontier_input=5000, frontier_output=600,
            small_input=0, small_output=0,
            cost_usd=0.02, model="m",
            spec={"decision": "propose", "candidate_patch": None},
        )

    runners = {"autobridge_cold": cold_result, "frontier_oneshot": one_shot_result}
    vendors = beacon_run.load_vendors()
    vendor = str(desc["vendor"])
    grades, _reports = beacon_run.run_instance_trial(
        _protocol(("autobridge_cold", "frontier_oneshot")),  # type: ignore[arg-type]
        seed_hex, desc, 1,
        config=beacon_run.default_beacon_config(),
        runners=runners, provider=None, knowledge_warm=None,
        verifier_url="http://verifier-test",
        vendors=vendors,
        vendor_cfg=next(v for v in vendors if v.vendor == vendor),
        public_url="http://localhost:9",
    )
    by_arm = {g.arm: g for g in grades}
    cold, one = by_arm["autobridge_cold"], by_arm["frontier_oneshot"]
    # Engine arm: the split, not a swapped total.
    assert (cold.frontier_input, cold.frontier_output) == (800, 150)
    assert (cold.small_input, cold.small_output) == (200, 50)
    assert cold.input_tokens == 1000 and cold.output_tokens == 200
    # Frontier arm: frontier tier only, same fields.
    assert (one.frontier_input, one.frontier_output) == (5000, 600)
    assert (one.small_input, one.small_output) == (0, 0)
    # Rows serialize the split (grades.jsonl basis).
    row = cold.to_dict()
    assert row["frontier_input"] == 800 and row["small_output"] == 50

    # ArmResult carries the split with zero defaults (legacy runners green).
    assert ArmResult is not None
    from bench.harness import make_trial_inputs

    r = ArmResult(arm="one_shot",
                  inputs=make_trial_inputs(beacon_run.default_beacon_config(), "s", 0),
                  ran=True)
    assert (r.frontier_input, r.frontier_output, r.small_input, r.small_output) == (0, 0, 0, 0)


def _fake_verifier(monkeypatch, beacon_run) -> None:
    from starlette.testclient import TestClient

    from verifier import app as verifier_app_mod

    client = TestClient(verifier_app_mod.app)

    def fake_post(_url: str, path: str, payload: dict, _timeout_s: float = 30.0) -> dict:
        resp = client.post(path, json=_json.loads(_json.dumps(payload, default=str)))
        assert resp.status_code == 200, (path, resp.status_code, resp.text[:300])
        return resp.json()

    monkeypatch.setattr(beacon_run, "_verifier_post", fake_post)
