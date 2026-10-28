"""REQ-020 item 6 — two Meridian faults repaired through the ladder.

Both scenarios use the real Meridian presentation layer
(``to_wire_*`` from ``simulators.meridianpms.app``) over the seeded
generator dataset, served through an httpx MockTransport that honors the
``Limitation`` cursor contract — so fakes and the live simulator cannot
drift apart (same pattern as test_03's Cloudbeds fakes).

  meridian_state_renamed  -> rename_field            (wire State -> Status)
  meridian_cursor_renamed -> cursor_next_path_shift  (wire Cursor -> NextCursor)

Each scenario: sync the discovered v2 spec against the drifted wire,
classify, run the ladder (L0 miss -> L1 rule, frontier_tokens == 0),
persist a RepairRun, apply the patch to a verified ConnectorRelease,
assert the oracle passes, assert the new release syncs Complete, and
assert the L0 knowledge-cache hit on the second attempt.
"""

from __future__ import annotations

import json
import os
import pathlib
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

if TYPE_CHECKING:
    import pytest

from autobridge.control.models import (
    Base,
    Connector,
    ConnectorRelease,
    ModelCall,
    RepairRun,
    spec_signature,
)
from autobridge.discovery.bootstrap import build_draft_spec
from autobridge.drift.classifier import classify
from autobridge.drift.fingerprint import FingerprintDelta, compute_fingerprint, diff_fingerprints
from autobridge.drift.signals import signals_from_sync
from autobridge.engine.sync_run import execute_sync
from autobridge.repair.controller import RepairController
from autobridge.repair.knowledge import KnowledgeCache
from autobridge.runtime.http import HttpClient
from autobridge.spec.models import RetryPolicy, load_spec_from_dict
from autobridge.spec.patch import apply_patch

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MERIDIAN_YAML = (REPO_ROOT / "simulators" / "docs" / "meridian-openapi.yaml").read_text(encoding="utf-8")

CLIENT_TOKEN = "meridian-client-token"
ACCESS_TOKEN = "meridian-access-token"

# Real presentation layer: the same functions the live sim uses.
from simulators.common.generator import generate_dataset  # noqa: E402
from simulators.meridianpms.app import (  # noqa: E402
    _dumps_exact,
    _paginate_by_cursor,
    account_id_for,
    to_wire_customer,
    to_wire_enterprise,
    to_wire_order_item,
    to_wire_reservation,
)


def _discovered_spec_dict() -> dict:
    result = build_draft_spec(
        MERIDIAN_YAML,
        source="simulators/docs/meridian-openapi.yaml",
        connector_id="meridianpms",
        spec_vocabulary="v2",
    )
    assert result.success, f"visible_errors={result.visible_errors}"
    return dict(result.draft_spec)


def _meridian_fake(scenario_id: str, dataset: dict[str, list[dict[str, Any]]]) -> HttpClient:
    """Serve the seeded dataset through the real wire functions + cursor paging."""

    def handler(request: httpx.Request) -> httpx.Response:
        try:
            body = json.loads(request.content.decode() or "{}")
        except Exception:
            body = {}
        path = request.url.path
        lim = body.get("Limitation") or {}
        try:
            count = max(1, min(1000, int(lim.get("Count", 100))))
        except (TypeError, ValueError):
            count = 100
        cursor = lim.get("Cursor")
        if "reservations" in path:
            wires = [to_wire_reservation(r, scenario_id) for r in dataset.get("reservations", [])]
            page, out = _paginate_by_cursor(wires, count, cursor)
            leaf = "NextCursor" if scenario_id == "meridian_cursor_renamed" else "Cursor"
            return httpx.Response(200, content=_dumps_exact({"Reservations": page, leaf: out}).encode(), headers={"Content-Type": "application/json"})
        if "customers" in path:
            wires = []
            for r in dataset.get("reservations", []):
                account = account_id_for(str(r.get("reservation_id")))
                wires.append(to_wire_customer(account, r.get("guest_name"), "2026-10-26T10:00:00Z"))
            page, out = _paginate_by_cursor(wires, count, cursor)
            leaf = "NextCursor" if scenario_id == "meridian_cursor_renamed" else "Cursor"
            return httpx.Response(200, content=_dumps_exact({"Customers": page, leaf: out}).encode(), headers={"Content-Type": "application/json"})
        if "orderItems" in path:
            wires = [to_wire_order_item(f) for f in dataset.get("folios", [])]
            page, out = _paginate_by_cursor(wires, count, cursor)
            leaf = "NextCursor" if scenario_id == "meridian_cursor_renamed" else "Cursor"
            return httpx.Response(200, content=_dumps_exact({"OrderItems": page, leaf: out}).encode(), headers={"Content-Type": "application/json"})
        if "configuration" in path:
            payload = {"Enterprises": [to_wire_enterprise(p) for p in dataset.get("properties", [])]}
            return httpx.Response(200, content=_dumps_exact(payload).encode(), headers={"Content-Type": "application/json"})
        raise AssertionError(f"unexpected path {path!r}")  # pragma: no cover

    transport = httpx.MockTransport(handler)

    def factory(**kw: Any) -> httpx.Client:
        return httpx.Client(transport=transport, timeout=kw.get("timeout", 5))

    return HttpClient(
        "https://meridian.example.com",
        retry=RetryPolicy(statuses=[429, 503], backoff="exponential", max_attempts=3),
        client_factory=factory,
        sleep_fn=lambda _: None,
    )


def _make_db():  # type: ignore[no-untyped-def]
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)


def _create_connector_and_release(spec: dict[str, Any], connector_id: str, SessionLocal) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    session = SessionLocal()
    conn = Connector(
        id=connector_id,
        name=connector_id,
        base_url="https://meridian.example.com",
        credential_ref="MERIDIAN_CLIENT_TOKEN",
        target_entities=["Reservation", "FolioCharge"],
        runtime_target={},
    )
    session.add(conn)
    session.flush()
    sig = spec_signature(spec)
    rel = ConnectorRelease(connector_id=conn.id, version=1, spec=spec, signature=sig)
    session.add(rel)
    session.flush()
    conn.active_release_id = rel.id
    session.commit()
    cid, rid = conn.id, rel.id
    session.close()
    return cid, rid


def _verify_oracle_pass(spec: dict[str, Any], scenario_id: str) -> None:
    from verifier.oracle import oracle_verdict

    verdict, score, failed = oracle_verdict(scenario_id, spec)
    assert verdict == "pass", f"oracle verdict fail for {scenario_id}: {failed}"
    assert score == 1.0
    assert not failed


def _run_meridian_repair(
    *,
    scenario_id: str,
    expected_rule_id: str,
    expected_class: str,
    extra: dict[str, Any],
    manual_delta: FingerprintDelta | None = None,
    assert_auto_class: bool = True,
) -> None:
    os.environ.setdefault("MERIDIAN_CLIENT_TOKEN", CLIENT_TOKEN)
    os.environ.setdefault("MERIDIAN_ACCESS_TOKEN", ACCESS_TOKEN)
    spec = _discovered_spec_dict()
    load_spec_from_dict(spec)
    dataset = generate_dataset(seed=42)

    # --- 1. Drifted sync + auto fingerprint/classification ---
    drift_summary = execute_sync(spec, _meridian_fake(scenario_id, dataset))
    baseline_fp = compute_fingerprint(spec, {}, {})
    current_fp = compute_fingerprint(spec, drift_summary.get("operation_results", {}), drift_summary.get("canonical_records", {}))
    auto_delta = diff_fingerprints(baseline_fp, current_fp)
    signals = signals_from_sync(drift_summary, spec=spec, previous_fingerprint=baseline_fp, current_fingerprint=current_fp)
    classification = classify(signals)
    if assert_auto_class:
        assert classification.drift_class == expected_class, (
            f"{scenario_id}: auto class {classification.drift_class} != {expected_class} "
            f"(outcome={drift_summary.get('outcome')})"
        )
    assert classification.fixable is True
    # Rename diagnosis comes from wire-level evidence (manual delta + extra),
    # the same pattern test_03 uses: a canonical fingerprint sees a nulled
    # field where the wire renamed the leaf, not the rename itself.
    drift_class = classification.drift_class if assert_auto_class else expected_class
    effective_delta = manual_delta if manual_delta is not None else auto_delta

    # --- 2. Ladder ---
    engine, SessionLocal = _make_db()
    connector_db_id, release_id = _create_connector_and_release(spec, "meridianpms", SessionLocal)
    cache = KnowledgeCache()
    ctrl = RepairController(knowledge_cache=cache)
    session = SessionLocal()
    result = ctrl.handle(
        spec=spec,
        drift_class=drift_class,
        signals=signals,
        fingerprint_delta=effective_delta,
        extra=dict(extra),
        vendor="meridianpms",
        session=session,
    )
    session.commit()
    repair_run = session.query(RepairRun).order_by(RepairRun.created_at.desc()).first()
    assert repair_run is not None
    repair_run_id = repair_run.id
    session.close()

    # --- 3. L1 propose, zero frontier ---
    assert result.decision == "propose", f"{scenario_id}: {result.decision} {result.escalation_reason} {result.ladder_trace}"
    assert result.rule_id == expected_rule_id, f"{scenario_id}: {result.rule_id} {result.ladder_trace}"
    assert result.candidate_patch, f"{scenario_id} empty patch"
    assert result.model_usage["frontier_tokens"] == 0, result.model_usage
    assert result.ladder_level_reached == 1, f"expected L1, got {result.ladder_level_reached}"
    new_spec = apply_patch(spec, result.candidate_patch)
    assert load_spec_from_dict(new_spec) is not None

    # --- 4. Ledger: no frontier ModelCall rows ---
    session = SessionLocal()
    assert session.query(ModelCall).count() == 0, "deterministic repair must not create ModelCall rows"
    rr = session.get(RepairRun, repair_run_id)
    assert rr is not None and (rr.model_usage or {}).get("frontier_tokens", 0) == 0
    session.close()

    # --- 5. Verified release ---
    session3 = SessionLocal()
    from autobridge.engine.releases import create_release_from_patch

    new_release = create_release_from_patch(session3, connector_id=connector_db_id, base_release_id=release_id, patch=result.candidate_patch, provenance_repair_run_id=repair_run_id)
    session3.commit()
    assert new_release.parent_release_id == release_id
    session3.close()

    # --- 6. Oracle: old fails, new passes, baseline still passes ---
    from verifier.oracle import oracle_verdict as _ov

    old_verdict, _, _ = _ov(scenario_id, spec)
    assert old_verdict == "fail", f"old spec should fail for {scenario_id}"
    _verify_oracle_pass(new_spec, scenario_id)
    _verify_oracle_pass(new_spec, "baseline")

    # --- 7. New spec syncs Complete against the drifted wire ---
    new_summary = execute_sync(new_spec, _meridian_fake(scenario_id, dataset))
    assert new_summary["outcome"] == "Complete", f"{scenario_id}: {new_summary['outcome']} {new_summary.get('unmet_criteria')}"
    assert not new_summary["unmet_criteria"]
    for ent_name, money_field in (("Reservation", "total_amount"), ("FolioCharge", "amount")):
        for rec in new_summary["canonical_records"].get(ent_name, []):
            if money_field in rec and rec[money_field] is not None:
                assert isinstance(rec[money_field], Decimal), f"{ent_name}.{money_field} must be Decimal"

    # --- 8. L0 cache hit on repeat ---
    session4 = SessionLocal()
    result2 = ctrl.handle(
        spec=spec,
        drift_class=drift_class,
        signals=signals,
        fingerprint_delta=effective_delta,
        extra=dict(extra),
        vendor="meridianpms",
        session=session4,
    )
    session4.commit()
    session4.close()
    assert result2.decision == "propose"
    assert result2.candidate_patch == result.candidate_patch
    assert result2.ladder_level_reached == 0, f"expected L0 hit, got {result2.ladder_level_reached}"
    engine.dispose()


def test_meridian_state_renamed_zero_frontier(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wire State -> Status repaired by rename_field at L1, frontier 0."""
    monkeypatch.setenv("MERIDIAN_CLIENT_TOKEN", CLIENT_TOKEN)
    monkeypatch.setenv("MERIDIAN_ACCESS_TOKEN", ACCESS_TOKEN)
    _run_meridian_repair(
        scenario_id="meridian_state_renamed",
        expected_rule_id="rename_field",
        expected_class="SCHEMA_RENAME",
        extra={
            "entity": "Reservation",
            "field": "status",
            "new_field": "Status",
            "new_path": "$.Status",
            "rename": {"entity": "Reservation", "old_field": "status", "new_field": "Status"},
        },
        manual_delta=FingerprintDelta(
            fingerprint_changed=True,
            pagination_changed=False,
            keys_changed=False,
            types_changed=True,
            changed_entities=["Reservation"],
            pagination_details={},
            keys_details={},
            types_details={"Reservation": {"previous": {"status": "str", "other": "str"}, "current": {"Status": "str", "other": "str"}}},
        ),
        # Canonical fingerprint sees status nulled (wire leaf renamed), not
        # the rename itself; the rename diagnosis rides on the delta + extra.
        assert_auto_class=False,
    )


def test_meridian_cursor_renamed_zero_frontier(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wire Cursor -> NextCursor repaired by cursor_next_path_shift at L1, frontier 0."""
    monkeypatch.setenv("MERIDIAN_CLIENT_TOKEN", CLIENT_TOKEN)
    monkeypatch.setenv("MERIDIAN_ACCESS_TOKEN", ACCESS_TOKEN)
    _run_meridian_repair(
        scenario_id="meridian_cursor_renamed",
        expected_rule_id="cursor_next_path_shift",
        expected_class="PAGINATION_CHANGE",
        extra={"new_next_path": "$.NextCursor"},
        manual_delta=FingerprintDelta(
            fingerprint_changed=True,
            pagination_changed=True,
            keys_changed=False,
            types_changed=False,
            changed_entities=[],
            pagination_details={
                "list_reservations": {
                    "previous": {"strategy": "body_cursor", "next_path": "$.Cursor", "pages_fetched": 8},
                    "current": {"strategy": "body_cursor", "next_path": "$.Cursor", "pages_fetched": 1},
                },
            },
            keys_details={},
            types_details={},
        ),
    )
