"""Unit tests for the eight-step demo driver (P1.W10.2, plan §6; step 8 REQ-022 E).

The demo runs offline (local engine evidence) when the Compose stack is
down and drives the control API when it is up. These tests cover both
modes without Docker: live mode is simulated by stubbing the probe/call
helpers, never with real network.
"""

from __future__ import annotations

import json
import os
import pathlib
from decimal import Decimal
from typing import Any

import pytest

from bench import demo as demo_mod
from bench.demo import (
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
    step7_new_api_style,
    step8_onprem_pabx,
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
    report = run_demo(seed=0, control_url="http://127.0.0.1:9")
    assert report["mode"] == "local-evidence"
    assert report["seed"] == 0
    assert len(report["steps"]) == 8
    assert report["failed"] == 0
    for i, step in enumerate(report["steps"], start=1):
        assert step["number"] == f"{i}/8", step
        assert step["status"] == "ok", step
        assert step["control_calls"] == [], step  # offline: no HTTP attempted


def test_step1_complete_zero_model_usage_decimal() -> None:
    step = step1_reusable_artifact(0, False, "http://127.0.0.1:9")
    ev = step["evidence"]
    assert ev["outcome"] == "Complete"
    assert ev["records_observed"] == {"Reservation": 4, "FolioCharge": 3}
    assert ev["model_usage_ledger"]["frontier_tokens"] == 0
    assert ev["credential_ref"] == "ATLAS_API_KEY"  # ref name only, never a value
    assert len(ev["signature"]) == 64


def _step1_live_fake(stored_base_url: str) -> Any:
    """Stub control calls for step 1 live mode with a given stored base_url."""

    def fake_call(base_url: str, method: str, path: str, _payload: Any = None) -> dict[str, Any]:
        assert "localhost:9" not in base_url  # uses the passed base
        if path == "/healthz":
            return {"method": method, "path": path, "status": 200, "body": {"status": "ok"}}
        if path == "/connectors" and method == "GET":
            return {"method": method, "path": path, "status": 200, "body": {"connectors": []}}
        if path == "/connectors" and method == "POST":
            assert _payload is not None and _payload["base_url"] == "http://sim-atlas:8000"
            return {"method": method, "path": path, "status": 201, "body": {"id": "c-1", "name": "atlaspms"}}
        if path == "/connectors/c-1":
            return {
                "method": method,
                "path": path,
                "status": 200,
                "body": {"id": "c-1", "name": "atlaspms", "base_url": stored_base_url},
            }
        if path == "/metrics/model-usage":
            return {"method": method, "path": path, "status": 200, "body": {"calls": 0}}
        raise AssertionError(f"unexpected {method} {path}")

    return fake_call


def test_step1_live_asserts_normalized_address_local(monkeypatch: Any) -> None:
    """REQ-027 sub-step 1: local stack must store a loopback address."""
    monkeypatch.setattr(demo_mod, "_control_probe", lambda _url: True)
    monkeypatch.setattr(demo_mod, "_control_call", _step1_live_fake("http://localhost:8105"))
    monkeypatch.setattr(demo_mod, "_stack_is_local", lambda: True)
    step = step1_reusable_artifact(0, True, "http://control:8101")
    assert step["status"] == "ok", step
    ev = step["evidence"]
    assert ev["connector_base_url_sent"] == "http://sim-atlas:8000"
    assert ev["connector_base_url_stored"] == "http://localhost:8105"
    assert "server-normalized" in ev["address_source"] and "local" in ev["address_source"]


def test_step1_live_fails_on_leaked_compose_hostname(monkeypatch: Any) -> None:
    """REQ-027 sub-step 1: an unnormalized sim hostname fails the step loudly."""
    monkeypatch.setattr(demo_mod, "_control_probe", lambda _url: True)
    monkeypatch.setattr(demo_mod, "_control_call", _step1_live_fake("http://sim-atlas:8000"))
    monkeypatch.setattr(demo_mod, "_stack_is_local", lambda: True)
    step = step1_reusable_artifact(0, True, "http://control:8101")
    assert step["status"] == "failed", step
    assert "leaked" in (step.get("error") or "")


def test_step1_live_asserts_service_dns_in_compose(monkeypatch: Any) -> None:
    monkeypatch.setattr(demo_mod, "_control_probe", lambda _url: True)
    monkeypatch.setattr(demo_mod, "_control_call", _step1_live_fake("http://sim-atlas:8000"))
    monkeypatch.setattr(demo_mod, "_stack_is_local", lambda: False)
    step = step1_reusable_artifact(0, True, "http://control:8101")
    assert step["status"] == "ok", step
    assert step["evidence"]["connector_base_url_stored"] == "http://sim-atlas:8000"


def test_step2_docs_to_draft_spec_no_model_calls() -> None:
    step = step2_unfamiliar_api(0, False, "http://127.0.0.1:9")
    ev = step["evidence"]
    assert ev["doc_type"] == "markdown"
    assert "GET /reservations" in ev["operations"]
    assert ev["hypotheses"] >= 1
    assert ev["draft_spec_built"] is True
    assert ev["model_usage_ledger"]["frontier_tokens"] == 0
    assert ev["model_usage_ledger"]["calls"] == 0


def test_step3_l1_patch_frontier_zero() -> None:
    step = step3_zero_token_recovery(0, False, "http://127.0.0.1:9")
    ev = step["evidence"]
    assert ev["l1_rule"] == "page_to_cursor"
    assert ev["frontier_tokens"] == 0
    assert ev["patch_ops"] >= 4
    assert any(op["path"].endswith("/strategy") and op["value"] == "cursor" for op in ev["patch"])


def test_step4_decoy_rejected_escalation_single_question(monkeypatch: Any) -> None:
    monkeypatch.setenv("REPLAY_DIR", str(REPO_ROOT / "tests" / "fixtures" / "replay"))
    step = step4_hard_semantic_failure(0, False, "http://127.0.0.1:9")
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
    step = step4_hard_semantic_failure(0, False, "http://127.0.0.1:9")
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
    step = step5_head_to_head(0, False, "http://127.0.0.1:9")
    ev = step["evidence"]
    assert ev["scorecard_path"].endswith("experiments/scorecard.md")
    assert isinstance(ev["scorecard_lines"], int)
    assert isinstance(ev["preview"], list)


def test_step7_v1_refuses_v2_connects_offline(monkeypatch: Any) -> None:
    """REQ-020 step 7: before (v1 Blocked, named reason, 0 calls) + after (v2 Complete)."""
    monkeypatch.delenv("MERIDIAN_CLIENT_TOKEN", raising=False)
    monkeypatch.delenv("MERIDIAN_ACCESS_TOKEN", raising=False)
    step = step7_new_api_style(0, False, "http://127.0.0.1:9")
    assert step["status"] == "ok", step
    ev = step["evidence"]
    assert ev["docs_ref"] == "simulators/docs/meridian-openapi.yaml"
    before, after = ev["before"], ev["after"]
    assert before["spec_vocabulary"] == "v1"
    assert before["outcome"] == "Blocked"
    assert "POST" in before["reason"] and "ConnectorSpec v1" in before["reason"]
    assert before["draft_spec"] is None and before["release_persisted"] is False
    assert after["spec_vocabulary"] == "v2" and after["spec_version"] == 2
    assert after["auth_type"] == "body_fields"
    assert after["outcome"] == "Complete"
    assert after["guest_name_joined"] == "Ada Lovelace"
    assert after["total_amount"] == "20.00" and after["money_type"] == "Decimal(2dp)"
    ledger = ev["model_usage_ledger"]
    assert (ledger["calls"], ledger["small_tokens"], ledger["frontier_tokens"]) == (0, 0, 0)
    assert ev["resolved_rung"] == "v1 Blocked → v2 Complete"
    # Demo placeholders must not leak into the real environment.
    assert "MERIDIAN_CLIENT_TOKEN" not in os.environ
    assert "MERIDIAN_ACCESS_TOKEN" not in os.environ


def test_step7_rung_row_reports_zero_frontier() -> None:
    from bench.demo import rung_table_row

    step = step7_new_api_style(0, False, "http://127.0.0.1:9")
    row = rung_table_row(step)
    assert "0 frontier" in row and "v1 Blocked" in row


def test_step8_pabx_layout_repair_zero_frontier() -> None:
    """REQ-022 E: firmware v2→v3 → L1 rename_field patch, 0 model calls."""
    step = step8_onprem_pabx(0, False, "http://127.0.0.1:9")
    assert step["status"] == "ok", step
    ev = step["evidence"]
    assert ev["fault"] == "fias_record_layout_change"
    assert ev["drift_class"] == "FIELD_RENAME"
    assert ev["renamed_wire"] == {"TA": "TM"} and ev["added_wire"] == ["CX"]
    assert ev["baseline"] == {"layout": "v2", "outcome": "Complete", "amount": "12.50"}
    assert ev["stale_mapping_reads"] is None  # silent corruption: why handshake detection
    assert ev["l1_rule"] == "rename_field"
    assert ev["patch"] == [
        {"op": "replace", "path": "/entities/FolioCharge/fields/amount/path", "value": "$.TM"}
    ]
    assert ev["repaired"] == {"layout": "v3", "outcome": "Complete", "amount": "12.50"}
    assert ev["money_type"] == "Decimal(2dp)"
    assert ev["model_usage_ledger"] == {"calls": 0, "small_tokens": 0, "frontier_tokens": 0}
    assert ev["resolved_rung"] == "L1"
    assert [a["rung"] for a in ev["rung_attempts"]] == [0, 1]


def test_step8_restart_redelivery_loses_nothing() -> None:
    step = step8_onprem_pabx(0, False, "http://127.0.0.1:9")
    restart = step["evidence"]["restart"]
    assert restart["lost"] == 0 and restart["duplicated"] == 0
    assert restart["redelivered"] == restart["unique_keys"] >= 1


def test_step8_wire_layouts_pinned_to_fault_yaml() -> None:
    """The demo's v2/v3 wire lists must match the fault scenario file."""
    import yaml

    from bench.demo import FIAS_PS_LAYOUT_V2, FIAS_PS_LAYOUT_V3

    fault = yaml.safe_load((REPO_ROOT / "simulators" / "scenarios" / "fias_record_layout_change.yaml").read_text(encoding="utf-8"))
    assert "".join(FIAS_PS_LAYOUT_V3) == fault["expected"]["announced_ps_layout"]
    assert set(FIAS_PS_LAYOUT_V3) - set(FIAS_PS_LAYOUT_V2) == {"TM", "CX"}
    assert set(FIAS_PS_LAYOUT_V2) - set(FIAS_PS_LAYOUT_V3) == {"TA"}


def test_step8_rung_row_reports_l1_zero_frontier() -> None:
    from bench.demo import rung_table_row

    step = step8_onprem_pabx(0, False, "http://127.0.0.1:9")
    row = rung_table_row(step)
    assert "resolved L1" in row and "0 frontier" in row and "rename_field" in row


def test_step6_signatures_match_build_reports() -> None:
    step = step6_reproducibility(0, False, "http://127.0.0.1:9")
    ev = step["evidence"]
    assert ev["seed"] == 0
    assert len(ev["atlas_reference_signature"]) == 64
    assert len(ev["harbor_reference_signature"]) == 64
    assert ev["atlas_reference_signature"] != ev["harbor_reference_signature"]


def test_live_mode_attaches_redacted_control_calls(monkeypatch: Any) -> None:
    seen: list[tuple[str, str]] = []
    # Stateful stub stack: connectors with builds/releases, atlas repairs
    # that reuse the learned patch at L0 on the inject-again leg, and a
    # /knowledge view derived from the same rows.
    connectors: dict[str, dict[str, Any]] = {}
    jobs: dict[str, dict[str, Any]] = {}
    counters = {"job": 0, "rel": 0}
    atlas_injects = {"n": 0}
    atlas_repairs: list[dict[str, Any]] = []

    def _connector_id(name: str) -> str:
        return f"c-{name.lower().replace(' ', '-')}"

    def _new_release(conn: dict[str, Any], provenance: str | None = None) -> dict[str, Any]:
        counters["rel"] += 1
        rel: dict[str, Any] = {"id": f"r-{counters['rel']}"}
        if provenance:
            rel["provenance_repair_run_id"] = provenance
        conn["releases"].append(rel)
        if conn["active_release_id"] is None:
            conn["active_release_id"] = rel["id"]
        return rel

    def fake_call(base_url: str, method: str, path: str, _payload: dict[str, Any] | None = None) -> dict[str, Any]:
        seen.append((method, path))
        assert "localhost:9" not in base_url  # uses the passed base
        if path == "/connectors" and method == "GET":
            return {"method": method, "path": path, "status": 200,
                    "body": {"connectors": [{"id": c["id"], "name": c["name"]} for c in connectors.values()]}}
        if path == "/connectors" and method == "POST":
            payload = _payload or {}
            conn = {"id": _connector_id(str(payload.get("name", "x"))), "name": str(payload.get("name", "x")),
                    "base_url": str(payload.get("base_url", "")), "active_release_id": None,
                    "releases": [], "builds": []}
            connectors[conn["name"]] = conn
            return {"method": method, "path": path, "status": 201, "body": {"id": conn["id"], "name": conn["name"]}}
        if path.endswith("/build") and method == "POST":
            cid = path.split("/")[2]
            conn = next((c for c in connectors.values() if c["id"] == cid), None)
            assert conn is not None, f"unknown connector {cid}"
            payload = _payload or {}
            vocab = str(payload.get("spec_vocabulary") or "")
            counters["job"] += 1
            jid = f"j-build-{counters['job']}"
            if conn["name"] == "meridianpms" and vocab == "v1":
                conn["builds"].append({"spec_vocabulary": "v1", "success": False})
                jobs[jid] = {"success": False, "release_id": None,
                             "degradation_reason": "operations need POST with a JSON body and body-field auth; not expressible in ConnectorSpec v1"}
            else:
                conn["builds"].append({"spec_vocabulary": vocab or "v1", "success": True})
                rel = _new_release(conn)
                jobs[jid] = {"success": True, "release_id": rel["id"], "connector_id": cid}
            return {"method": method, "path": path, "status": 200,
                    "body": {"job_id": jid, "build_run_id": "b-1", "state": "queued", "connector_id": cid}}
        if path.endswith("/sync") and method == "POST":
            counters["job"] += 1
            jid = f"j-sync-{counters['job']}"
            jobs[jid] = {"sync_run_id": f"s-{counters['job']}", "outcome": "Complete", "unmet_criteria": []}
            return {"method": method, "path": path, "status": 200,
                    "body": {"job_id": jid, "sync_run_id": None, "state": "queued", "release_id": "r-1"}}
        if path.startswith("/jobs/"):
            jid = path.split("/")[2]
            return {"method": method, "path": path, "status": 200,
                    "body": {"id": jid, "kind": "sync", "state": "succeeded", "attempts": 1,
                             "max_attempts": 3, "result": jobs.get(jid, {})}}
        if path == "/demo/inject-fault" and method == "POST":
            if (_payload or {}).get("target") == "sim-atlas":
                atlas_injects["n"] += 1
            return {"method": method, "path": path, "status": 200, "body": {"ok": True}}
        if path.startswith("/drift"):
            query = path.split("connector_id=")[-1] if "connector_id=" in path else ""
            conn = next((c for c in connectors.values() if c["id"] == query), None)
            if conn is not None and conn["name"] == "atlaspms" and atlas_injects["n"] > 0:
                return {"method": method, "path": path, "status": 200,
                        "body": [{"id": "d-1", "connector_id": conn["id"], "drift_class": "PAGINATION_CHANGE",
                                  "severity": "high", "evidence_ids": [], "source_sync_run_id": "s-1",
                                  "status": "open"}]}
            return {"method": method, "path": path, "status": 200, "body": []}
        if path.startswith("/repairs"):
            if method == "POST":
                rid = path.split("/")[2]
                conn = next((c for c in connectors.values() if c["name"] == "atlaspms"), None)
                assert conn is not None
                rel = _new_release(conn, provenance=rid)
                conn["active_release_id"] = rel["id"]
                return {"method": method, "path": path, "status": 200,
                        "body": {"id": "ap-1", "repair_run_id": rid, "actor": "demo",
                                 "decision": "approve", "to_release_id": rel["id"]}}
            query = path.split("connector_id=")[-1] if "connector_id=" in path else ""
            conn = next((c for c in connectors.values() if c["id"] == query), None)
            if conn is not None and conn["name"] == "atlaspms":
                while len(atlas_repairs) < atlas_injects["n"]:
                    rid = f"rr-{len(atlas_repairs) + 1}"
                    atlas_repairs.append({
                        "id": rid, "drift_event_ids": ["d-1"],
                        "ladder_level_reached": 0 if atlas_repairs else 1,
                        "rule_id": "page_to_cursor", "decision": "propose",
                        "model_usage": {"frontier_tokens": 0, "small_tokens": 0, "calls": 0}})
                    # The engine persists the candidate release at repair time.
                    counters["rel"] += 1
                    conn["releases"].append({"id": f"rel-{rid}", "provenance_repair_run_id": rid})
                return {"method": method, "path": path, "status": 200, "body": list(reversed(atlas_repairs))}
            return {"method": method, "path": path, "status": 200, "body": []}
        if path.startswith("/connectors/"):
            parts = path.split("/")
            cid = parts[2] if len(parts) > 2 else ""
            conn = next((c for c in connectors.values() if c["id"] == cid), None)
            assert conn is not None, f"unknown connector path {path}"
            if len(parts) == 5 and parts[4] == "activate" and method == "POST":
                rid = parts[3]
                assert any(r["id"] == rid for r in conn["releases"]), f"unknown release {rid}"
                conn["active_release_id"] = rid
                return {"method": method, "path": path, "status": 200, "body": {"ok": True}}
            return {"method": method, "path": path, "status": 200,
                    "body": {"id": conn["id"], "name": conn["name"], "base_url": conn["base_url"],
                             "active_release_id": conn["active_release_id"], "releases": conn["releases"]}}
        if path == "/knowledge" and method == "GET":
            entries: list[dict[str, Any]] = []
            if atlas_repairs:
                entries.append({"vendor": "atlaspms", "rule_id": "page_to_cursor",
                                "drift_class": "PAGINATION_CHANGE", "drift_signature": "s", "patch": [],
                                "learned_from": {"run_id": atlas_repairs[0]["id"]}, "times_reused": len(atlas_repairs) - 1})
            conns = [{"connector_id": c["id"], "name": c["name"],
                      "vocabulary": next((b["spec_vocabulary"] for b in reversed(c["builds"]) if b["success"]), ""),
                      "refused_vocabularies": [b["spec_vocabulary"] for b in c["builds"] if not b["success"]],
                      "builds": len(c["builds"])} for c in connectors.values()]
            return {"method": method, "path": path, "status": 200,
                    "body": {"entries": entries, "connectors": conns,
                             "counts": {"entries": len(entries),
                                        "reuses": sum(e["times_reused"] for e in entries), "tokens_saved": 0}}}
        return {"method": method, "path": path, "status": 200, "body": {"ok": True, "total": 0}}

    monkeypatch.setattr(demo_mod, "_control_probe", lambda _url: True)
    monkeypatch.setattr(demo_mod, "_control_call", fake_call)
    # Hermetic: this stub stack speaks service DNS regardless of host markers.
    monkeypatch.setattr(demo_mod, "_stack_is_local", lambda: False)
    report = run_demo(seed=0, control_url="http://control:8101")
    assert report["mode"] == "live-control-api"
    assert report["failed"] == 0
    paths = [p for _, p in seen]
    # Each live step drives its control-API calls (plan §6 mapping).
    assert "/healthz" in paths
    assert "/demo/inject-fault" in paths
    assert "/bench/scorecards" in paths
    assert any(p.startswith("/drift") for p in paths)
    # Steps 3/4 drive the live chain: build, sync, job polls, drift/repair
    # reads, and step 3's approval plus resync.
    assert any(p.endswith("/build") for _, p in seen)
    assert any(p.endswith("/sync") for _, p in seen)
    assert any(p.startswith("/jobs/") for _, p in seen)
    assert any(p.startswith("/repairs") for _, p in seen)
    assert any(p == "/knowledge" for _, p in seen)
    # Step 3's inject-again leg reuses the learned patch at L0 with 0 model
    # calls; /knowledge shows times_reused going up.
    step3 = report["steps"][2]
    again = step3["evidence"]["live"]["inject_again"]
    assert again["l0_hit"] is True and again["rung"] == 0, again
    assert again["reuses_after"] == 1 and again["reuses_before"] == 0, again
    # Step 7 records the refused v1 build and the v2 release in use.
    step7 = report["steps"][6]
    profile7 = step7["evidence"]["live"]["knowledge_profile"]
    assert profile7["vocabulary"] == "v2" and profile7["refused_vocabularies"] == ["v1"], profile7
    # Step 8 lists the PABX connector under v3.
    step8 = report["steps"][7]
    assert step8["evidence"]["live"]["knowledge_profile"]["vocabulary"] == "v3"


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
    text = render_text(run_demo(seed=7, control_url="http://127.0.0.1:9"))
    for needle in (
        "Atlas release",
        "Harbor docs",
        "page_to_cursor",
        "gross_to_net",
        "scorecards",
        "Reproducibility",
        "Meridian",
        "frontier_tokens",
        "Decimal",
    ):
        assert needle in text, needle


def test_render_text_failed_step() -> None:
    text = render_text(
        {"mode": "local-evidence", "control_url": "http://x", "seed": 0, "failed": 1,
         "steps": [{"number": "1/8", "name": "boom", "status": "failed", "error": "nope", "control_calls": []}]}
    )
    assert "FAILED" in text and "nope" in text


def test_main_text_and_json_modes(capsys: Any) -> None:
    assert main(["--control-url", "http://127.0.0.1:9", "--seed", "0"]) == 0
    out = capsys.readouterr().out
    assert "Step 1/8" in out and "Step 8/8" in out
    assert main(["--control-url", "http://127.0.0.1:9", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert len(report["steps"]) == 8 and report["failed"] == 0


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
         "steps": [{"number": "1/8", "name": "s", "status": "ok", "evidence": {"k": "v"}, "control_calls": [{"method": "GET", "path": "/healthz", "status": 200}]}]}
    )
    assert "1 steps via control API" in text


def test_harbor_reference_sync_complete_via_cursor_fake() -> None:
    from autobridge.engine.sync_run import execute_sync

    spec = demo_mod._load_reference_spec("harborpms")
    summary = execute_sync(spec, demo_mod._harbor_fake_client())
    assert summary["outcome"] == "Complete", summary.get("unmet_criteria")
    assert summary["records_observed"] == {"Reservation": 4, "FolioCharge": 3}
    assert summary.get("fingerprint_id")


def test_step5_without_scorecard_file(monkeypatch: Any, tmp_path: Any) -> None:
    monkeypatch.setattr(demo_mod, "SCORECARD_PATH", tmp_path / "missing.md")
    step = step5_head_to_head(0, False, "http://127.0.0.1:9")
    assert step["evidence"]["scorecard_lines"] == 0
    assert step["evidence"]["preview"] == ["no scorecard yet — run make bench"]


def test_run_demo_step_failure_is_reported_not_raised(monkeypatch: Any) -> None:
    def _boom(_seed: int, _live: bool, _base_url: str) -> dict[str, Any]:
        raise RuntimeError("synthetic step failure")

    monkeypatch.setattr(demo_mod, "STEPS", (_boom,))
    report = run_demo(seed=0, control_url="http://127.0.0.1:9")
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
    assert "500" in step["error"]
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
    assert modes["6/8"] == "in-process"  # step 6 never calls the control plane
    assert modes["1/8"] == "live"
    text = render_text(report)
    assert "live steps:" in text and "in-process steps:" in text
    assert "(live)" in text and "(in-process)" in text
    # NOTE: the probe is still stubbed True above, so force the offline path.
    monkeypatch.setattr(demo_mod, "_control_probe", lambda _url: False)
    offline = run_demo(seed=0, control_url="http://127.0.0.1:9")
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
    report = run_demo(seed=0, control_url="http://127.0.0.1:9")
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
    report = run_demo(seed=0, control_url="http://127.0.0.1:9")
    assert report["failed"] == 0
    for step in report["steps"]:
        ev = step["evidence"]
        usage = ev.get("model_usage_ledger", {}) if isinstance(ev.get("model_usage_ledger"), dict) else {}
        assert int(usage.get("frontier_tokens", 0) or 0) == 0, step
        if "Hard semantic failure" in step["name"]:
            assert ev["l3_skipped"] is True
            assert "no replay recording" in ev["why_escalated"]


def _live_fake_provider(l3_text: str) -> Any:
    """Frontier answered live (tokens billed) but yielded no usable patch."""
    from autobridge.llm.provider import Completion, FakeProvider, Usage

    return FakeProvider(
        responses={
            "small": Completion(
                text='["gross_is_canonical", "net_is_canonical"]', usage=Usage(96, 12)
            ),
            "frontier": Completion(text=l3_text, usage=Usage(2000, 115)),
        },
        frontier_model="muse-spark-1.3",
        small_model="muse-spark-1.3",
    )


def test_step4_live_prose_without_patch_says_what_happened(monkeypatch: Any) -> None:
    """Owner check 2026-10-28: a live L3 answer with no patch must say the
    call happened (model + tokens), show the redacted raw reply, and never
    claim "no frontier call available"."""
    import autobridge.llm.provider as provider_mod

    fake = _live_fake_provider(
        "I cannot determine a safe mapping. Net vs gross needs an operator decision."
    )
    monkeypatch.setattr(provider_mod, "resolve_frontier_provider", lambda: (fake, "live"))
    step = step4_hard_semantic_failure(0, False, "http://127.0.0.1:9")
    assert step["status"] == "ok", step
    ev = step["evidence"]
    assert ev["l3_skipped"] is False
    assert ev["l3_proposal"] == []
    assert ev["l3_model"] == "muse-spark-1.3"
    assert ev["l3_raw_preview"].startswith("I cannot determine")
    reason = ev["rejection_reason"]
    assert "L3 called live" in reason
    assert "muse-spark-1.3" in reason
    assert "2,115 tokens" in reason
    assert "no JSON Patch found in response" in reason
    assert "no frontier call available" not in reason
    assert "skipped" not in reason.lower()
    assert ev["frontier_replayed"] is False
    assert ev["sync_outcome"] == "Degraded"
    assert ev["release_persisted"] is False
    assert isinstance(ev["escalation_question"], str) and ev["escalation_question"].endswith("?")
    assert [a["rung"] for a in ev["rung_attempts"]] == [0, 1, 2, 3, 4]


def test_step4_live_unparsable_patch_says_what_happened(monkeypatch: Any) -> None:
    """Owner check 2026-10-28: a live L3 answer with an unparsable patch is
    a called rung with a recorded reason, not a skip."""
    import autobridge.llm.provider as provider_mod

    fake = _live_fake_provider('[{"op": "replace", "path": "/entities/Reservation/fields/total_amount/pa')
    monkeypatch.setattr(provider_mod, "resolve_frontier_provider", lambda: (fake, "live"))
    step = step4_hard_semantic_failure(0, False, "http://127.0.0.1:9")
    assert step["status"] == "ok", step
    ev = step["evidence"]
    assert ev["l3_skipped"] is False
    assert ev["l3_proposal"] == []
    reason = ev["rejection_reason"]
    assert "L3 called live" in reason
    assert "muse-spark-1.3" in reason
    assert "2,115 tokens" in reason
    assert "does not parse as a patch" in reason
    assert "no frontier call available" not in reason
    assert ev["l3_raw_preview"].startswith('[{"op": "replace"')
    assert ev["sync_outcome"] == "Degraded"
    assert ev["release_persisted"] is False
