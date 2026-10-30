"""REQ-043: the repair row exists while the climb runs.

A deferred semantic repair used to appear in ``GET /repairs`` only after
the climb finished — a minutes-long live L3 call left the Repairs list
empty and the repair page unreachable. Now the repair job creates the
``RepairRun`` row at start (decision ``climbing``), updates the same row
after every rung (committing between rungs, never across a model call),
and fills it in place at the end — one row per job, never a second one.

* a fake provider that sleeps 30 s: the row exists and ``GET /repairs``
  shows ``repairing`` with rung L3 (model + call start stamped) within
  2 s of the break; a second sync meanwhile dedupes onto the climbing
  row; the finished climb keeps the single row with the full trace.
* a crashing provider: the row lands in ``failed`` with the reason, and
  job retries reuse the same row (still one row when the job exhausts).
* a control restart marks orphaned ``climbing`` rows ``failed``.
* connector state names the rung (``repairing at L<n>``) while the climb
  runs; ``FRONTIER_MAX_TOKENS`` configures the L3 cap.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from autobridge.control.models import (
    Base,
    Connector,
    ConnectorRelease,
    DriftEvent,
    Job,
    RepairRun,
    session_factory,
    spec_signature,
)

ROOT = Path(__file__).resolve().parents[2]


def _money_spec() -> dict[str, Any]:
    return {
        "spec_version": 3,
        "connector": {"id": "harbor", "vendor": "harbor"},
        "source": {
            "base_url": "http://harbor:8000",
            "auth": {"type": "bearer", "credential_ref": "HARBOR_API_KEY"},
            "timezone_source": {"kind": "static", "value": "UTC"},
        },
        "operations": {
            "list_reservations": {
                "method": "GET",
                "path": "/reservations",
                "params": {},
                "pagination": {
                    "strategy": "page",
                    "page_param": "page",
                    "limit_param": "limit",
                    "items_path": "$.data",
                    "stop_when": ["empty_page"],
                },
            },
        },
        "entities": {
            "Reservation": {
                "from": "list_reservations",
                "key": "$.reservation_id",
                "fields": {
                    "reservation_id": {"path": "$.reservation_id"},
                    "total_amount": {"path": "$.total_amount"},
                },
                "checks": [
                    {
                        "kind": "money_invariant",
                        "field": "total_amount",
                        "compare_to": "$.gross_sum",
                    }
                ],
            },
        },
        "capabilities": {"read": ["Reservation"]},
        "health": {"canary": {"every": "60s", "operation": "noop"}},
    }


def _net_summary() -> dict[str, Any]:
    """What a real sync sees when totals read net instead of gross."""
    return {
        "outcome": "Degraded",
        "unmet_criteria": ["money_invariant"],
        "data_readiness": {"freshness": "current", "completeness": "complete"},
        "fingerprint_id": "semantic-live",
        "operation_results": {
            "list_reservations": {
                "records": [
                    {"reservation_id": "r1", "total_amount": "90.00"},
                    {"reservation_id": "r2", "total_amount": "180.00"},
                ],
                "pages_fetched": 1,
                "raw_pages": [{"gross_sum": "300.00", "data": []}],
                "stop_reason": "total_reached",
            }
        },
        "records_observed": {"Reservation": 2},
        "canonical_records": {
            "Reservation": [
                {"reservation_id": "r1", "total_amount": "90.00"},
                {"reservation_id": "r2", "total_amount": "180.00"},
            ],
        },
        "blocked_records": {},
        "invariant_results": [{"check": "money_invariant", "status": "failed"}],
        "checkpoint_values": {},
    }


_DECOY = (
    '[{"op": "replace", '
    '"path": "/entities/Reservation/fields/total_amount/path", '
    '"value": "$.net_amount"}]'
)


class _InterruptibleSleepProvider:
    """Frontier stand-in that blocks like a live 30 s model call (REQ-043).

    Sleeps in slices until *stop* is set (the test short-circuits the wait
    once it has observed the running row) or 30 s elapse — whichever comes
    first. The configured 30 s is the honest budget the requirement names;
    the interrupt only ends the test's wait, and the recorded usage still
    reflects a long call.
    """

    provider_name = "sleep-frontier-30s"

    def __init__(self, text: str = _DECOY, seconds: float = 30.0) -> None:
        self.text = text
        self.seconds = seconds
        self.calls = 0
        self.stop = threading.Event()
        self.max_tokens_seen: list[int] = []

    def complete(  # noqa: ARG002 — test double mirrors the provider protocol
        self,
        _messages: list[dict[str, Any]],
        *_args: Any,
        **_kwargs: Any,
    ) -> Any:
        from autobridge.llm.provider import Completion, Usage  # noqa: PLC0415

        self.calls += 1
        self.max_tokens_seen.append(int(_kwargs.get("max_tokens", 0) or 0))
        deadline = time.monotonic() + self.seconds
        while time.monotonic() < deadline:
            if self.stop.wait(0.05):
                break
        return Completion(
            text=self.text,
            usage=Usage(2000, 115),
            replayed=False,
            captured_at="",
        )

    def model_for_tier(self, tier: str) -> str:
        return f"sleep-{tier}"


class _CrashProvider:
    """Frontier stand-in that always fails (crash → failed row)."""

    provider_name = "crash-frontier"

    def __init__(self) -> None:
        self.calls = 0

    def complete(  # noqa: ARG002 — test double mirrors the provider protocol
        self,
        _messages: list[dict[str, Any]],
        *_args: Any,
        **_kwargs: Any,
    ) -> Any:
        self.calls += 1
        raise RuntimeError("boom: frontier exploded")

    def model_for_tier(self, tier: str) -> str:
        return f"crash-{tier}"


def _factory(tmp_path: Any) -> Any:
    from autobridge.control.models import engine_from_url  # noqa: PLC0415

    engine = engine_from_url(f"sqlite:///{tmp_path}/req043.db")
    Base.metadata.create_all(engine)
    return session_factory(engine)


def _seed(factory: Any, name: str = "harborpms") -> tuple[str, str, dict[str, Any]]:
    spec = _money_spec()
    with factory() as s:
        conn = Connector(
            name=name,
            base_url="http://localhost:8106",
            credential_ref="HARBOR_API_KEY",
            target_entities=["Reservation"],
            runtime_target={"kind": "local_process"},
        )
        s.add(conn)
        s.flush()
        rel = ConnectorRelease(
            connector_id=conn.id, version=1, spec=spec, signature=spec_signature(spec)
        )
        s.add(rel)
        s.flush()
        conn.active_release_id = rel.id
        s.commit()
        return conn.id, rel.id, spec


def _live_provider(monkeypatch: Any, provider: Any) -> Any:
    import autobridge.llm.provider as provider_mod  # noqa: PLC0415

    monkeypatch.setattr(
        provider_mod, "resolve_frontier_provider", lambda: (provider, "live")
    )
    return provider


def _break(factory: Any, conn_id: str, rel_id: str, spec: dict[str, Any]) -> dict[str, Any]:
    from autobridge.engine.detect_repair import detect_after_sync  # noqa: PLC0415

    with factory() as s:
        out = detect_after_sync(
            s,
            connector_id=conn_id,
            release_id=rel_id,
            spec_dict=spec,
            summary=_net_summary(),
            sync_run_id="sync-req043-1",
        )
        s.commit()
        return dict(out)


def _repair_rows(factory: Any) -> list[Any]:
    with factory() as s:
        return s.execute(select(RepairRun)).scalars().all()


def test_running_row_visible_with_rung_l3_within_2s(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """Sleep-30s provider: repairing row with rung L3 lands within 2 s."""
    from autobridge.control.routes.drift import list_repairs  # noqa: PLC0415
    from autobridge.control.runner import get_runner  # noqa: PLC0415

    sleepy = _InterruptibleSleepProvider(seconds=30.0)
    _live_provider(monkeypatch, sleepy)
    factory = _factory(tmp_path)
    conn_id, rel_id, spec = _seed(factory)

    out = _break(factory, conn_id, rel_id, spec)
    assert out["repair_pending"] is True
    assert out["repair_job_id"], out
    # The sync returns before any row exists: the row is created when the
    # *job starts*, not when the sync returns.
    assert _repair_rows(factory) == []

    with factory() as s:
        runner = get_runner(s)
    errors: list[str] = []

    def _drain() -> None:
        try:
            runner.run_next_of_kind("repair")
        except Exception as exc:  # noqa: BLE001 — surfaced below
            errors.append(f"{type(exc).__name__}: {exc}")

    worker = threading.Thread(target=_drain, daemon=True)
    break_at = time.monotonic()
    worker.start()
    try:
        # Within 2 s of the break the row exists and GET /repairs shows it
        # climbing at L3 — the entry the climb wrote before the 30 s call.
        seen_at = 0.0
        running: dict[str, Any] | None = None
        while time.monotonic() - break_at < 10.0:
            with factory() as s:
                rows = list_repairs(connector_id=conn_id, db=s)
            matches = [r for r in rows if r.decision == "climbing"]
            if matches:
                running = matches[0]
                seen_at = time.monotonic() - break_at
                break
            time.sleep(0.05)
        assert running is not None, "no climbing row appeared while the 30 s call ran"
        assert seen_at < 2.0, f"climbing row took {seen_at:.1f}s (must be < 2s)"
        levels = [e.get("level") for e in (running.ladder_trace or []) if isinstance(e, dict)]
        assert 3 in levels, f"row must already sit at L3, trace levels: {levels}"
        l3 = next(e for e in running.ladder_trace if isinstance(e, dict) and e.get("level") == 3)
        assert l3.get("model"), f"L3 entry must name the model: {l3}"
        assert l3.get("call_started_at"), f"L3 entry must stamp the call start: {l3}"

        # A second sync meanwhile dedupes onto the still-running climb —
        # one fault keeps one drift and one repair until the operator
        # decides — instead of opening a second drift or row.
        with factory() as s:
            from autobridge.engine.detect_repair import detect_after_sync  # noqa: PLC0415

            again = detect_after_sync(
                s,
                connector_id=conn_id,
                release_id=rel_id,
                spec_dict=spec,
                summary=_net_summary(),
                sync_run_id="sync-req043-2",
            )
            s.commit()
        assert again.get("repair_run_id") == running.id, again
        assert again.get("decision") == "climbing", again
    finally:
        # End the test's wait; the background climb finishes on its own.
        sleepy.stop.set()
        worker.join(timeout=30.0)
    assert not worker.is_alive(), "background repair did not finish"
    assert not errors, errors
    # The decoy patch targets a valid member but the patched test spec is
    # schema-invalid, so the climb rejects it, retries once with the error
    # (REQ-044 item 4), and rejects again: two frontier calls, then escalate.
    assert sleepy.calls == 2, "first L3 call plus the single apply-error retry"

    with factory() as s:
        runs = s.execute(select(RepairRun)).scalars().all()
        assert len(runs) == 1, "one repair for one break — the running row, finalized"
        run = runs[0]
        assert run.decision == "escalate", run.decision
        assert [t["level"] for t in run.ladder_trace] == [0, 1, 2, 3, 4]
        job = s.get(Job, out["repair_job_id"])
        assert job is not None and job.state == "succeeded", job.state if job else None
        # The job always pointed at the row — even while it was running.
        assert job.run_id == run.id, (job.run_id, run.id)
        assert job.result.get("repair_run_id") == run.id


def test_crash_leaves_failed_row_and_retries_reuse_it(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """A crashing climb marks its row failed; retries reuse the same row."""
    from autobridge.control.runner import get_runner  # noqa: PLC0415

    crashy = _CrashProvider()
    _live_provider(monkeypatch, crashy)
    factory = _factory(tmp_path)
    conn_id, rel_id, spec = _seed(factory)

    out = _break(factory, conn_id, rel_id, spec)
    assert out["repair_pending"] is True
    with factory() as s:
        runner = get_runner(s)

    # Attempt 1: crash → row failed, job re-queued (attempts left).
    runner.run_next_of_kind("repair")
    with factory() as s:
        runs = s.execute(select(RepairRun)).scalars().all()
        assert len(runs) == 1
        assert runs[0].decision == "failed", runs[0].decision
        assert "boom" in str((runs[0].ladder_trace or [])[-1].get("reason", "")), runs[0].ladder_trace
        job = s.get(Job, out["repair_job_id"])
        assert job is not None and job.state == "queued", job.state if job else None
        assert job.run_id == runs[0].id

    # Attempts 2 and 3 exhaust the budget: same row reused, never a second
    # row, and the job ends terminally failed with the row failed.
    runner.run_next_of_kind("repair")
    runner.run_next_of_kind("repair")
    with factory() as s:
        runs = s.execute(select(RepairRun)).scalars().all()
        assert len(runs) == 1, "retries must reuse the row, not insert new ones"
        assert runs[0].decision == "failed", runs[0].decision
        job = s.get(Job, out["repair_job_id"])
        assert job is not None and job.state == "failed", job.state if job else None
        assert "boom" in str(job.last_error or ""), job.last_error
    assert crashy.calls == 3, crashy.calls


def test_restart_marks_orphaned_climbing_failed(tmp_path: Any) -> None:
    """fail_orphaned_repairs: climbing rows go failed with the reason."""
    from autobridge.repair.controller import fail_orphaned_repairs  # noqa: PLC0415

    factory = _factory(tmp_path)
    with factory() as s:
        row = RepairRun(
            drift_event_ids=["d1"],
            ladder_level_reached=2,
            vendor="harbor",
            drift_class="SEMANTIC_VALUE_DRIFT",
            decision="climbing",
            model_usage={},
            ladder_trace=[{"level": 2, "action": "small_rank", "reason": "x"}],
        )
        done = RepairRun(
            drift_event_ids=["d2"],
            ladder_level_reached=1,
            vendor="harbor",
            drift_class="PAGINATION_CHANGE",
            decision="propose",
            model_usage={},
            ladder_trace=[{"level": 1, "action": "rules_matched", "reason": "y"}],
        )
        s.add(row)
        s.add(done)
        s.commit()
        row_id, done_id = row.id, done.id

    with factory() as s:
        marked = fail_orphaned_repairs(s)
    assert marked == 1, marked
    with factory() as s:
        orphan = s.get(RepairRun, row_id)
        assert orphan is not None and orphan.decision == "failed"
        assert "restart" in str((orphan.ladder_trace or [])[-1].get("reason", ""))
        untouched = s.get(RepairRun, done_id)
        assert untouched is not None and untouched.decision == "propose"


def test_connector_state_names_running_rung(tmp_path: Any) -> None:
    """While the climb runs: degrading + repairing at L<n>."""
    from autobridge.control.connector_state import connector_state  # noqa: PLC0415
    from autobridge.control.runner import get_runner  # noqa: PLC0415

    factory = _factory(tmp_path)
    conn_id, _rel_id, _spec = _seed(factory)
    with factory() as s:
        drift = DriftEvent(
            connector_id=conn_id,
            drift_class="SEMANTIC_VALUE_DRIFT",
            severity="high",
            evidence_ids=[],
            source_sync_run_id="sync-x",
            status="open",
        )
        s.add(drift)
        s.flush()
        run = RepairRun(
            drift_event_ids=[drift.id],
            ladder_level_reached=0,
            vendor="harbor",
            drift_class="SEMANTIC_VALUE_DRIFT",
            decision="climbing",
            model_usage={},
            ladder_trace=[
                {"level": 0, "action": "knowledge_miss", "reason": "L0 miss"},
                {"level": 1, "action": "no_rule_matched", "reason": "L1 miss"},
                {
                    "level": 2,
                    "action": "l2_skipped",
                    "reason": "L2 skipped: fewer than two hypotheses (1)",
                },
            ],
        )
        s.add(run)
        s.flush()
        drift_id = drift.id
        s.commit()
        runner = get_runner(s)
        job_id = runner.submit_repair(
            [drift_id],
            payload_extra={"connector_id": conn_id, "drift_class": "SEMANTIC_VALUE_DRIFT"},
        )
        assert s.get(Job, job_id) is not None

    with factory() as s:
        state = connector_state(s, conn_id)
    assert state["state"] == "degrading", state
    assert "repairing at L2" in str(state["activity"]), state


def test_repair_decisions_include_running_states() -> None:
    """The model accepts the running states the job lifecycle writes."""
    from autobridge.control.models import REPAIR_DECISIONS  # noqa: PLC0415

    assert {"climbing", "failed"} <= set(REPAIR_DECISIONS)
    row = RepairRun(
        drift_event_ids=[],
        ladder_level_reached=0,
        decision="climbing",
        model_usage={},
        ladder_trace=[],
    )
    assert row.decision == "climbing"
    row.decision = "failed"
    assert row.decision == "failed"
    with pytest.raises(ValueError):
        row.decision = "running"


def test_frontier_max_tokens_configurable(monkeypatch: Any) -> None:
    """FRONTIER_MAX_TOKENS overrides the L3 cap; default is 131072."""
    from autobridge.config import frontier_max_tokens  # noqa: PLC0415

    monkeypatch.delenv("FRONTIER_MAX_TOKENS", raising=False)
    assert frontier_max_tokens() == 131072
    monkeypatch.setenv("FRONTIER_MAX_TOKENS", "4096")
    assert frontier_max_tokens() == 4096
    monkeypatch.setenv("FRONTIER_MAX_TOKENS", "nope")
    with pytest.raises(ValueError):
        frontier_max_tokens()


def test_l3_call_uses_configured_cap(tmp_path: Any, monkeypatch: Any) -> None:
    """The climb passes the configured cap to the frontier call."""
    from autobridge.repair.controller import RepairController  # noqa: PLC0415

    sleepy = _InterruptibleSleepProvider(seconds=0.0)
    sleepy.stop.set()  # no waiting: only the cap matters here
    _live_provider(monkeypatch, sleepy)
    monkeypatch.setenv("FRONTIER_MAX_TOKENS", "4096")
    factory = _factory(tmp_path)
    _conn_id, _rel_id, spec = _seed(factory)
    with factory() as s:
        controller = RepairController()
        result = controller.handle(
            spec=spec,
            drift_class="SEMANTIC_VALUE_DRIFT",
            signals=[],
            fingerprint_delta=None,
            vendor="harbor",
            drift_event_ids=[],
            session=s,
            hypotheses=[],
            enumerator_yield_count=0,
            provider=sleepy,
            budget=None,
            validate_against=None,
            on_rung=None,
            existing_repair_run_id=None,
        )
    assert sleepy.calls == 1
    assert sleepy.max_tokens_seen == [4096], sleepy.max_tokens_seen
    assert result.decision == "escalate"


def test_pre_0011_database_upgrades_and_accepts_running_states(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 0010 database upgrades to head; climbing/failed rows persist."""
    import sqlalchemy as sa  # noqa: PLC0415
    from alembic.config import Config  # noqa: PLC0415
    from sqlalchemy import create_engine  # noqa: PLC0415

    from alembic import command  # noqa: PLC0415
    from autobridge.control import models as m  # noqa: PLC0415

    db = tmp_path / "pre0011.db"
    monkeypatch.setenv(m.DATABASE_URL_ENV, f"sqlite:///{db}")
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(cfg, "0010_scheduler_pause")
    command.upgrade(cfg, "head")
    engine = create_engine(f"sqlite:///{db}")
    with m.session_factory(engine)() as s:
        for decision in ("climbing", "failed"):
            s.add(
                RepairRun(
                    drift_event_ids=[],
                    ladder_level_reached=0,
                    decision=decision,
                    model_usage={},
                    ladder_trace=[],
                )
            )
        s.commit()
        assert s.execute(select(RepairRun)).scalars().all() != []
    with engine.connect() as conn:
        insp = sa.inspect(conn)
        assert insp.get_table_names() != []
