# ruff: noqa: TC003
"""Rung ledger tests (REQ-021 item 3).

Every build and repair run records each rung it tried with a one-line
reason, linked to its ModelCall rows; the repair page shows the climb and
``make demo`` prints one table row per run.
"""

from __future__ import annotations

import pathlib
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from autobridge.control.app import app
from autobridge.control.deps import get_db
from autobridge.control.models import (
    Base,
    Connector,
    DriftEvent,
    RepairRun,
    session_factory,
)
from autobridge.llm.provider import Usage, record_model_call
from autobridge.repair.controller import RepairController
from autobridge.repair.ladder import ledger_totals, rung_ledger


def _trace() -> list[dict[str, Any]]:
    return [
        {"level": 0, "action": "knowledge_miss", "reason": "L0 miss: no known fix for atlas+d1"},
        {"level": 1, "action": "rule_applied", "reason": "L1 page_to_cursor matched"},
        {"stage": "probe", "note": "no level — not a rung attempt"},
        {"level": 2, "action": "l2_rank", "reason": "L2 confidence 0.41 < 0.70"},
        {"level": 3, "action": "frontier_generate", "reason": "L3 invoked: enumerator yielded 1 < 2"},
        {"level": 4, "action": "escalate", "reason": "budget exhausted"},
    ]


def _call(tier: str, purpose: str, inp: int, out: int, cost: str = "0") -> dict[str, Any]:
    return {"tier": tier, "purpose": purpose, "input_tokens": inp, "output_tokens": out, "cost_estimate": cost}


def test_rung_ledger_links_calls_to_rungs() -> None:
    calls = [
        _call("small", "l2-rank-hypotheses", 100, 20, "0.000120"),
        _call("frontier", "l3-generate-hypothesis", 200, 50, "0.001125"),
    ]
    attempts = rung_ledger(_trace(), calls)
    assert [a["rung"] for a in attempts] == [0, 1, 2, 3, 4]
    assert attempts[0]["calls"] == [] and attempts[1]["calls"] == []
    assert attempts[2]["calls"] == [
        {"tier": "small", "purpose": "l2-rank-hypotheses", "input_tokens": 100, "output_tokens": 20, "cost": "0.000120"}
    ]
    assert attempts[3]["calls"][0]["purpose"] == "l3-generate-hypothesis"
    assert attempts[4]["calls"] == []


def test_rung_ledger_unknown_tier_attaches_to_last_attempt() -> None:
    attempts = rung_ledger([{"level": 1, "action": "x", "reason": "y"}], [_call("mystery", "p", 1, 1)])
    assert len(attempts) == 1
    assert attempts[0]["calls"][0]["tier"] == "mystery"


def test_rung_ledger_skips_unleveled_and_empty() -> None:
    assert rung_ledger([]) == []
    assert rung_ledger(None) == []
    assert rung_ledger([{"stage": "probe"}]) == []


def test_ledger_totals() -> None:
    attempts = rung_ledger(
        _trace(),
        [_call("small", "l2", 100, 20, "0.000120"), _call("frontier", "l3", 200, 50, "0.001125")],
    )
    totals = ledger_totals(attempts)
    assert totals == {"calls": 2, "small_tokens": 120, "frontier_tokens": 250, "cost": "0.001245"}
    assert ledger_totals([]) == {"calls": 0, "small_tokens": 0, "frontier_tokens": 0, "cost": "0"}


def _client_for(tmp_path: pathlib.Path) -> tuple[TestClient, Any]:
    engine = create_engine(f"sqlite:///{tmp_path}/ledger_test.db")
    Base.metadata.create_all(engine)
    factory = session_factory(engine)

    def override_db():  # type: ignore[no-untyped-def]
        s = factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_db
    return TestClient(app), factory


def _repair_id(factory: Any) -> str:
    controller = RepairController()
    with factory() as s:
        conn = Connector(
            name="Ledger", base_url="http://example.com", credential_ref="K",
            target_entities=["Reservation"], runtime_target={"kind": "local_process"},
        )
        s.add(conn)
        s.flush()
        drift = DriftEvent(connector_id=conn.id, drift_class="CAPABILITY_REMOVED")
        s.add(drift)
        s.flush()
        result = controller.handle(
            spec={"spec_version": 1, "connector": {"id": "ledger", "vendor": "t"}},
            drift_class="CAPABILITY_REMOVED",
            drift_event_ids=[drift.id],
            session=s,
        )
        assert result.decision == "escalate"
        s.commit()
        with factory() as check:
            row = check.query(RepairRun).order_by(RepairRun.created_at.desc()).first()
            assert row is not None
            assert any(t.get("level") == 4 for t in (row.ladder_trace or []))
            return row.id


def test_controller_persists_ladder_trace(tmp_path: pathlib.Path) -> None:
    _client, factory = _client_for(tmp_path)
    try:
        repair_id = _repair_id(factory)
        with factory() as s:
            row = s.get(RepairRun, repair_id)
            assert row is not None
            assert [t.get("level") for t in row.ladder_trace] == [4]
            assert "CAPABILITY_REMOVED" in row.ladder_trace[0].get("reason", "")
    finally:
        app.dependency_overrides.clear()


def test_ledger_endpoint_links_model_calls(tmp_path: pathlib.Path) -> None:
    client, factory = _client_for(tmp_path)
    try:
        repair_id = _repair_id(factory)
        with factory() as s:
            record_model_call(
                s, run_id=repair_id, provider="muse", model="muse-spark-1.3",
                tier="frontier", usage=Usage(1000, 0), purpose="l3-generate-hypothesis",
            )
            s.commit()
        r = client.get(f"/repairs/{repair_id}/ledger")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["repair_run_id"] == repair_id
        assert body["decision"] == "escalate"
        assert body["resolved_rung"] == 4
        assert len(body["attempts"]) == 1
        assert body["attempts"][0]["rung"] == 4
        # No L3 attempt exists, so the frontier call attaches to the last one.
        assert body["attempts"][0]["calls"][0]["purpose"] == "l3-generate-hypothesis"
        assert body["total_calls"] == 1
        assert body["frontier_tokens"] == 1000
        assert body["small_tokens"] == 0
        assert Decimal(body["total_cost"]) == Decimal("0.001250")
        r = client.get("/repairs/does-not-exist/ledger")
        assert r.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_ledger_endpoint_trace_visible_on_repair_out(tmp_path: pathlib.Path) -> None:
    client, factory = _client_for(tmp_path)
    try:
        repair_id = _repair_id(factory)
        r = client.get(f"/repairs/{repair_id}")
        assert r.status_code == 200, r.text
        assert r.json()["ladder_trace"][0]["level"] == 4
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Demo per-run table
# ---------------------------------------------------------------------------


def test_demo_rung_table_row() -> None:
    from autobridge.bench.demo import render_rung_summary, rung_table_row

    step = {
        "name": "Zero-token recovery",
        "evidence": {
            "resolved_rung": "L1",
            "model_usage_ledger": {"calls": 0, "small_tokens": 0, "frontier_tokens": 0},
        },
    }
    row = rung_table_row(step)
    assert row.startswith("Zero-token recovery | resolved L1 | 0 calls | 0 small / 0 frontier |")

    escalated = {
        "name": "Hard semantic failure",
        "evidence": {
            "resolved_rung": "L4 (escalated)",
            "model_usage_ledger": {"calls": 2, "small_tokens": 120, "frontier_tokens": 250},
            "why_escalated": "total_amount must stay gross",
        },
    }
    row = rung_table_row(escalated)
    assert "2 calls" in row and "120 small / 250 frontier" in row
    assert "total_amount must stay gross" in row

    summary = render_rung_summary({"steps": [step, escalated]})
    assert summary.startswith("per-run model usage:")
    assert "Zero-token recovery" in summary and "Hard semantic failure" in summary
