"""REQ-083 A4: candidate-sync wiring is exercised before the first arm.

- ``sync_candidate_url`` joins the vendor host with the spec's base path
  (meridian ``/api/connector/v1``) instead of dropping it.
- Every vendor's credential refs resolve under the placeholder bench env
  (meridian's exact demo tokens included — no more "Missing env var").
- ``preflight_beacon`` proves the full apparatus (spec + serving +
  judge + prompts + PWD) and fails loudly.
"""

from __future__ import annotations


def _protocol() -> object:
    from bench.beacon_run import BeaconProtocol

    return BeaconProtocol(
        round_after_utc="2026-11-02T00:00:00+00:00",
        target_round=6603643,
        faults=10,
        trials=1,
        arms=("autobridge_cold", "frontier_oneshot"),
        engine_commit="c" * 40,
        generator_commit="d" * 40,
        frozen_at_utc="2026-11-02T00:00:00+00:00",
        rehearsal=True,
        frontier_model="muse-spark-1.3",
        agent_model=None,
        token_ceiling=200000,
        wall_clock_s=600.0,
    )


def test_sync_candidate_url_joins_spec_base_path() -> None:
    from bench import beacon_run
    from bench import faults as faults

    vendors = beacon_run.load_vendors()
    with faults.fake_env():
        atlas = beacon_run.engine_spec_for({"index": 0, "vendor": "atlas"}, vendors)
        assert atlas is not None
        assert beacon_run.sync_candidate_url(atlas, "http://localhost:8105") == "http://localhost:8105"
        meridian = beacon_run.engine_spec_for({"index": 3, "vendor": "meridian"}, vendors)
        assert meridian is not None
        joined = beacon_run.sync_candidate_url(meridian, "http://localhost:8116")
        assert joined == "http://localhost:8116/api/connector/v1", joined


def test_placeholder_credentials_cover_every_vendor() -> None:
    from bench import beacon_run
    from bench import faults as faults

    vendors = beacon_run.load_vendors()
    from autobridge.runtime.auth import build_auth_headers
    from autobridge.spec.models import load_spec_from_dict

    with faults.fake_env(), beacon_run._placeholder_credential_env():
        for pos, entry in enumerate(vendors):
            if not entry.http:
                continue  # framed transport: no HTTP auth to resolve
            spec = beacon_run.engine_spec_for({"index": pos, "vendor": entry.vendor}, vendors)
            assert spec is not None
            # Must not raise: every ref resolves under the bench env.
            build_auth_headers(load_spec_from_dict(spec).source.auth)


def test_preflight_beacon_passes_with_live_judge() -> None:
    from starlette.testclient import TestClient

    from bench import beacon_run
    from verifier import app as verifier_app_mod

    vendors = beacon_run.load_vendors()
    client = TestClient(verifier_app_mod.app)

    def fake_post(_url: str, path: str, payload: dict, _timeout_s: float = 30.0) -> dict:
        import json as _json

        # Mirror _verifier_post: Decimal money serializes via default=str.
        resp = client.post(path, json=_json.loads(_json.dumps(payload, default=str)))
        assert resp.status_code == 200, (path, resp.status_code, resp.text[:300])
        return resp.json()

    old = beacon_run._verifier_post
    beacon_run._verifier_post = fake_post  # type: ignore[assignment]
    try:
        # run_beacon executes the preflight inside the placeholder bench
        # env (same as the arms run under); replicate that here.
        with beacon_run._placeholder_credential_env():
            report = beacon_run.preflight_beacon(
                _protocol(),  # type: ignore[arg-type]
                config=beacon_run.default_beacon_config(),
                eval_seed_hex="ab" * 32,
                verifier_url="http://verifier-test",
                vendors=vendors,
            )
    finally:
        beacon_run._verifier_post = old
    assert set(report["vendors"]) == {v.vendor for v in vendors}
    assert report["prompts"]["oneshot_chars"] > 0
    assert report["prompts"]["agent_chars"] > 0
    assert report["pwd_ok"] is True


def test_preflight_beacon_fails_loudly_on_unresolvable_credentials() -> None:
    import os

    from bench import beacon_run

    vendors = beacon_run.load_vendors()
    # Hide every credential: meridian's refs must be named, not graded as
    # an arm http_error later.
    saved = {k: os.environ.pop(k, None) for k in list(beacon_run._PLACEHOLDER_CREDENTIAL_DEFAULTS)}
    saved.update({k: os.environ.pop(k, None) for k in ("ATLAS_BASE_URL", "HARBOR_BASE_URL")})
    try:
        try:
            beacon_run.preflight_beacon(
                _protocol(),  # type: ignore[arg-type]
                config=beacon_run.default_beacon_config(),
                eval_seed_hex="ab" * 32,
                verifier_url=None,
                vendors=vendors,
            )
        except Exception as exc:  # noqa: BLE001
            assert "preflight" in str(exc), str(exc)[:300]
            return
        raise AssertionError("preflight should fail with no credentials/URLs")
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
