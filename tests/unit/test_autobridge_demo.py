"""Unit tests for the six-step demo driver (P1.W10.2, plan §6).

The demo runs offline (local engine evidence) when the Compose stack is
down and drives the control API when it is up. These tests cover both
modes without Docker: live mode is simulated by stubbing the probe/call
helpers, never with real network.
"""

from __future__ import annotations

import json
import pathlib
from decimal import Decimal
from typing import Any

import pytest

from autobridge.bench import demo as demo_mod
from autobridge.bench.demo import (
    _redact_body,
    main,
    render_text,
    run_demo,
    step1_reusable_artifact,
    step2_unfamiliar_api,
    step3_zero_token_recovery,
    step4_hard_semantic_failure,
    step5_head_to_head,
    step6_reproducibility,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _no_live_frontier(monkeypatch: Any, tmp_path: Any) -> None:
    """Unit tests never spend a real frontier key: force replay with an
    empty recordings dir (L3 skips plainly) unless a test opts into the
    fixtures capture."""
    monkeypatch.setenv("FRONTIER_MODE", "replay")
    monkeypatch.setenv("REPLAY_DIR", str(tmp_path / "no-recordings"))
    monkeypatch.delenv("FRONTIER_API_KEY", raising=False)


def test_run_demo_all_six_steps_ok_offline() -> None:
    report = run_demo(seed=0, control_url="http://localhost:9")
    assert report["mode"] == "local-evidence"
    assert report["seed"] == 0
    assert len(report["steps"]) == 6
    assert report["failed"] == 0
    for i, step in enumerate(report["steps"], start=1):
        assert step["number"] == f"{i}/6", step
        assert step["status"] == "ok", step
        assert step["control_calls"] == [], step  # offline: no HTTP attempted


def test_step1_complete_zero_model_usage_decimal() -> None:
    step = step1_reusable_artifact(0, False, "http://localhost:9")
    ev = step["evidence"]
    assert ev["outcome"] == "Complete"
    assert ev["records_observed"] == {"Reservation": 4, "FolioCharge": 3}
    assert ev["model_usage_ledger"]["frontier_tokens"] == 0
    assert ev["credential_ref"] == "ATLAS_API_KEY"  # ref name only, never a value
    assert len(ev["signature"]) == 64


def test_step2_docs_to_draft_spec_no_model_calls() -> None:
    step = step2_unfamiliar_api(0, False, "http://localhost:9")
    ev = step["evidence"]
    assert ev["doc_type"] == "markdown"
    assert "GET /reservations" in ev["operations"]
    assert ev["hypotheses"] >= 1
    assert ev["draft_spec_built"] is True
    assert ev["model_usage_ledger"]["frontier_tokens"] == 0
    assert ev["model_usage_ledger"]["calls"] == 0


def test_step3_l1_patch_frontier_zero() -> None:
    step = step3_zero_token_recovery(0, False, "http://localhost:9")
    ev = step["evidence"]
    assert ev["l1_rule"] == "page_to_cursor"
    assert ev["frontier_tokens"] == 0
    assert ev["patch_ops"] >= 4
    assert any(op["path"].endswith("/strategy") and op["value"] == "cursor" for op in ev["patch"])


def test_step4_decoy_rejected_escalation_single_question(monkeypatch: Any) -> None:
    monkeypatch.setenv("REPLAY_DIR", str(REPO_ROOT / "tests" / "fixtures" / "replay"))
    step = step4_hard_semantic_failure(0, False, "http://localhost:9")
    ev = step["evidence"]
    assert ev["sync_outcome"] == "Degraded"
    assert ev["unmet_criteria"] == ["money_invariant"]
    assert ev["decoy_patch_rejected"] == "invalid_patch_trap"
    assert isinstance(ev["escalation_question"], str) and ev["escalation_question"]
    assert ev["money_type"] == "Decimal(2dp)"
    assert ev["l3_skipped"] is False
    assert [a["rung"] for a in ev["rung_attempts"]] == [0, 1, 2, 3, 4]


def test_step4_without_recording_labels_l3_skipped(monkeypatch: Any, tmp_path: Any) -> None:
    """No key + no recording: L3 is labelled skipped, never invented."""
    monkeypatch.setenv("REPLAY_DIR", str(tmp_path / "empty"))
    monkeypatch.delenv("FRONTIER_API_KEY", raising=False)
    step = step4_hard_semantic_failure(0, False, "http://localhost:9")
    assert step["status"] == "ok", step
    ev = step["evidence"]
    assert ev["l3_skipped"] is True
    assert ev["l3_proposal"] == []
    assert ev["frontier_replayed"] is False
    assert ev["model_usage_ledger"]["frontier_tokens"] == 0
    assert ev["resolved_rung"] == "L4 (escalated)"
    assert isinstance(ev["escalation_question"], str) and ev["escalation_question"].endswith("?")
    assert ev["release_persisted"] is False


def test_step5_scorecard_shape() -> None:
    step = step5_head_to_head(0, False, "http://localhost:9")
    ev = step["evidence"]
    assert ev["scorecard_path"].endswith("experiments/scorecard.md")
    assert isinstance(ev["scorecard_lines"], int)
    assert isinstance(ev["preview"], list)


def test_step6_signatures_match_build_reports() -> None:
    step = step6_reproducibility(0, False, "http://localhost:9")
    ev = step["evidence"]
    assert ev["seed"] == 0
    assert len(ev["atlas_reference_signature"]) == 64
    assert len(ev["harbor_reference_signature"]) == 64
    assert ev["atlas_reference_signature"] != ev["harbor_reference_signature"]


def test_live_mode_attaches_redacted_control_calls(monkeypatch: Any) -> None:
    seen: list[tuple[str, str]] = []

    def fake_call(base_url: str, method: str, path: str, _payload: dict[str, Any] | None = None) -> dict[str, Any]:
        seen.append((method, path))
        assert "localhost:9" not in base_url  # uses the passed base
        return {"method": method, "path": path, "status": 200, "body": {"ok": True}}

    monkeypatch.setattr(demo_mod, "_control_probe", lambda _url: True)
    monkeypatch.setattr(demo_mod, "_control_call", fake_call)
    report = run_demo(seed=0, control_url="http://control:8101")
    assert report["mode"] == "live-control-api"
    assert report["failed"] == 0
    paths = [p for _, p in seen]
    # Each live step drives its control-API calls (plan §6 mapping).
    assert "/healthz" in paths
    assert "/demo/inject-fault" in paths
    assert "/bench/scorecards" in paths
    assert "/drift" in paths


def test_live_scorecard_total_recorded(monkeypatch: Any) -> None:
    monkeypatch.setattr(demo_mod, "_control_probe", lambda _url: True)
    monkeypatch.setattr(
        demo_mod,
        "_control_call",
        lambda _b, m, p, _payload=None: {"method": m, "path": p, "status": 200, "body": {"total": 3}},
    )
    step = step5_head_to_head(0, True, "http://control:8101")
    assert step["evidence"]["control_scorecards_total"] == 3


def test_redact_body_truncates_and_omits_specs() -> None:
    body = {"spec": {"a": 1}, "draft_spec": [1, 2], "ok": True, "long": "x" * 500}
    red = _redact_body(body)
    assert "omitted" in red["spec"]
    assert "omitted" in red["draft_spec"]
    assert red["ok"] is True
    assert red["long"].endswith("…")
    assert _redact_body({"a": {"b": {"c": {"d": 1}}}}) == {"a": {"b": {"c": {"d": "…"}}}}


def test_render_text_lists_all_steps_and_ledger() -> None:
    text = render_text(run_demo(seed=7, control_url="http://localhost:9"))
    for needle in (
        "Atlas release",
        "Harbor docs",
        "page_to_cursor",
        "gross_to_net",
        "scorecards",
        "Reproducibility",
        "frontier_tokens",
        "Decimal",
    ):
        assert needle in text, needle


def test_render_text_failed_step() -> None:
    text = render_text(
        {"mode": "local-evidence", "control_url": "http://x", "seed": 0, "failed": 1,
         "steps": [{"number": "1/6", "name": "boom", "status": "failed", "error": "nope", "control_calls": []}]}
    )
    assert "FAILED" in text and "nope" in text


def test_main_text_and_json_modes(capsys: Any) -> None:
    assert main(["--control-url", "http://localhost:9", "--seed", "0"]) == 0
    out = capsys.readouterr().out
    assert "Step 1/6" in out and "Step 6/6" in out
    assert main(["--control-url", "http://localhost:9", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert len(report["steps"]) == 6 and report["failed"] == 0


def test_control_probe_and_call_with_stubbed_http(monkeypatch: Any) -> None:
    import httpx

    class _Resp:
        status_code = 200
        text = '{"ok": true}'

        def json(self) -> dict[str, Any]:
            return {"ok": True, "spec": {"huge": 1}}

    class _Client:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def get(self, url: str) -> _Resp:
            assert url.endswith("/healthz")
            return _Resp()

        def request(self, method: str, url: str, json: Any = None) -> _Resp:
            assert method and url
            assert json is None or isinstance(json, dict)
            return _Resp()

    monkeypatch.setattr(httpx, "Client", _Client)
    assert demo_mod._control_probe("http://live:8101") is True
    rec = demo_mod._control_call("http://live:8101", "GET", "/connectors/x")
    assert rec["status"] == 200 and "omitted" in rec["body"]["spec"]

    class _BadJson:
        status_code = 200
        text = "not-json" * 100

        def json(self) -> dict[str, Any]:
            raise ValueError("nope")

    class _Client2(_Client):
        def request(self, method: str, url: str, json: Any = None) -> _BadJson:
            assert method and url and json is None
            return _BadJson()

    monkeypatch.setattr(httpx, "Client", _Client2)
    rec2 = demo_mod._control_call("http://live:8101", "GET", "/x")
    assert rec2["body"]["_text"].startswith("not-json")

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise ConnectionError("down")

    monkeypatch.setattr(httpx, "Client", _boom)
    assert demo_mod._control_probe("http://live:8101") is False
    rec3 = demo_mod._control_call("http://live:8101", "GET", "/x")
    assert rec3["status"] == "unreachable"


def test_live_ensure_connector_reuses_existing(monkeypatch: Any) -> None:
    calls = [
        {"method": "GET", "path": "/connectors", "status": 200,
         "body": [{"id": "abc", "name": "Harbor demo"}]},
    ]
    monkeypatch.setattr(demo_mod, "_control_call", lambda _b, _m, _p, _payload=None: calls.pop(0))
    out = demo_mod._live_ensure_connector("http://c", {"name": "Harbor demo"})
    assert len(out) == 1 and out[0]["reused_connector_id"] == "abc"


def test_live_ensure_connector_creates_when_absent(monkeypatch: Any) -> None:
    made = [
        {"method": "GET", "path": "/connectors", "status": 200, "body": []},
        {"method": "POST", "path": "/connectors", "status": 201, "body": {"id": "new"}},
    ]
    monkeypatch.setattr(demo_mod, "_control_call", lambda _b, _m, _p, _payload=None: made.pop(0))
    out = demo_mod._live_ensure_connector("http://c", {"name": "Harbor demo"})
    assert [c["method"] for c in out] == ["GET", "POST"]


def test_render_text_live_complete_line() -> None:
    text = render_text(
        {"mode": "live-control-api", "control_url": "http://c", "seed": 0, "failed": 0,
         "steps": [{"number": "1/6", "name": "s", "status": "ok", "evidence": {"k": "v"}, "control_calls": [{"method": "GET", "path": "/healthz", "status": 200}]}]}
    )
    assert "6 steps via control API" in text


def test_harbor_reference_sync_complete_via_cursor_fake() -> None:
    from autobridge.engine.sync_run import execute_sync

    spec = demo_mod._load_reference_spec("harborpms")
    summary = execute_sync(spec, demo_mod._harbor_fake_client())
    assert summary["outcome"] == "Complete", summary.get("unmet_criteria")
    assert summary["records_observed"] == {"Reservation": 4, "FolioCharge": 3}
    assert summary.get("fingerprint_id")


def test_step5_without_scorecard_file(monkeypatch: Any, tmp_path: Any) -> None:
    monkeypatch.setattr(demo_mod, "SCORECARD_PATH", tmp_path / "missing.md")
    step = step5_head_to_head(0, False, "http://localhost:9")
    assert step["evidence"]["scorecard_lines"] == 0
    assert step["evidence"]["preview"] == ["no scorecard yet — run make bench"]


def test_run_demo_step_failure_is_reported_not_raised(monkeypatch: Any) -> None:
    def _boom(_seed: int, _live: bool, _base_url: str) -> dict[str, Any]:
        raise RuntimeError("synthetic step failure")

    monkeypatch.setattr(demo_mod, "STEPS", (_boom,))
    report = run_demo(seed=0, control_url="http://localhost:9")
    assert report["failed"] == 1
    assert report["steps"][0]["status"] == "failed"


def test_redact_body_list_branch() -> None:
    assert _redact_body([{"a": 1}] * 12) == [{"a": 1}] * 10


def test_demo_money_values_are_decimal_not_float() -> None:
    from autobridge.engine.sync_run import execute_sync

    spec = demo_mod._load_reference_spec("atlaspms")
    summary = execute_sync(spec, demo_mod._atlas_fake_client())
    for rec in summary["canonical_records"]["Reservation"]:
        assert isinstance(rec["total_amount"], Decimal)
        assert rec["total_amount"].as_tuple().exponent == -2


def test_step_write_401_fails_step(monkeypatch: Any) -> None:
    """REQ-016: a 401 on a step's live write fails the step (missing token)."""
    monkeypatch.setattr(demo_mod, "_control_probe", lambda _url: True)
    monkeypatch.setattr(
        demo_mod,
        "_control_call",
        lambda _b, m, p, _payload=None: {"method": m, "path": p, "status": 401,
                                          "body": {"detail": "missing token"}},
    )
    step = step3_zero_token_recovery(0, True, "http://control:8101")
    assert step["status"] == "failed", step
    assert "POST" in step["error"] and "/demo/inject-fault" in step["error"]
    assert "401" in step["error"]
    report = run_demo(seed=0, control_url="http://control:8101")
    assert report["failed"] >= 1
    assert any(s["status"] == "failed" for s in report["steps"])


def test_step_write_500_fails_step(monkeypatch: Any) -> None:
    """REQ-016: a 500 on a step's live write fails the step with detail."""
    monkeypatch.setattr(demo_mod, "_control_probe", lambda _url: True)
    monkeypatch.setattr(
        demo_mod,
        "_control_call",
        lambda _b, m, p, _payload=None: {"method": m, "path": p, "status": 500,
                                          "body": {"detail": "boom"}},
    )
    step = step4_hard_semantic_failure(0, True, "http://control:8101")
    assert step["status"] == "failed", step
    assert "500" in step["error"] and "POST" in step["error"]
    text = render_text(run_demo(seed=0, control_url="http://control:8101"))
    assert "FAILED" in text and "500" in text


def test_demo_labels_live_vs_inprocess_steps(monkeypatch: Any) -> None:
    """REQ-016: output says which steps ran live and which were in-process."""
    monkeypatch.setattr(demo_mod, "_control_probe", lambda _url: True)
    monkeypatch.setattr(
        demo_mod,
        "_control_call",
        lambda _b, m, p, _payload=None: {"method": m, "path": p, "status": 200, "body": {"ok": True}},
    )
    report = run_demo(seed=0, control_url="http://control:8101")
    modes = {s["number"]: s["mode"] for s in report["steps"]}
    assert modes["6/6"] == "in-process"  # step 6 never calls the control plane
    assert modes["1/6"] == "live"
    text = render_text(report)
    assert "live steps:" in text and "in-process steps:" in text
    assert "(live)" in text and "(in-process)" in text
    # NOTE: the probe is still stubbed True above, so force the offline path.
    monkeypatch.setattr(demo_mod, "_control_probe", lambda _url: False)
    offline = run_demo(seed=0, control_url="http://localhost:9")
    assert all(s["mode"] == "in-process" for s in offline["steps"])
    assert "in-process" in render_text(offline)


def test_main_exits_nonzero_when_write_fails(monkeypatch: Any, capsys: Any) -> None:
    """REQ-016: make demo exits non-zero when a live write fails."""
    monkeypatch.setattr(demo_mod, "_control_probe", lambda _url: True)
    monkeypatch.setattr(
        demo_mod,
        "_control_call",
        lambda _b, m, p, _payload=None: {"method": m, "path": p, "status": 401,
                                          "body": {"detail": "missing token"}},
    )
    assert main(["--control-url", "http://control:8101", "--seed", "0"]) == 1
    assert "FAILED" in capsys.readouterr().out


def test_only_hard_semantic_step_uses_frontier(monkeypatch: Any) -> None:
    """REQ-021 item 4: the frontier fires only when cheaper rungs cannot.

    With a replay capture present, the hard semantic step climbs
    L0 -> L1 -> L2 -> L3 (replayed) and escalates after the trap rejects the
    L3 proposal; every other main-script step resolves with 0 frontier
    tokens. Without a capture the step labels L3 skipped and still uses 0.
    """
    monkeypatch.setenv("REPLAY_DIR", str(REPO_ROOT / "tests" / "fixtures" / "replay"))
    report = run_demo(seed=0, control_url="http://localhost:9")
    assert report["failed"] == 0
    for step in report["steps"]:
        ev = step["evidence"]
        usage = ev.get("model_usage_ledger", {}) if isinstance(ev.get("model_usage_ledger"), dict) else {}
        frontier = int(usage.get("frontier_tokens", ev.get("frontier_tokens", 0)) or 0)
        if "Hard semantic failure" in step["name"]:
            assert frontier > 0, step
            assert ev["frontier_replayed"] is True
            assert ev["replay_label"] == "replayed (test capture)"
            assert ev["l3_skipped"] is False
            assert [a["rung"] for a in ev["rung_attempts"]] == [0, 1, 2, 3, 4]
            assert ev["resolved_rung"] == "L4 (escalated)"
        else:
            assert frontier == 0, step


def test_demo_without_recording_skips_l3_with_zero_frontier(monkeypatch: Any, tmp_path: Any) -> None:
    monkeypatch.setenv("REPLAY_DIR", str(tmp_path / "empty"))
    monkeypatch.delenv("FRONTIER_API_KEY", raising=False)
    report = run_demo(seed=0, control_url="http://localhost:9")
    assert report["failed"] == 0
    for step in report["steps"]:
        ev = step["evidence"]
        usage = ev.get("model_usage_ledger", {}) if isinstance(ev.get("model_usage_ledger"), dict) else {}
        assert int(usage.get("frontier_tokens", 0) or 0) == 0, step
        if "Hard semantic failure" in step["name"]:
            assert ev["l3_skipped"] is True
            assert "no replay recording" in ev["why_escalated"]
