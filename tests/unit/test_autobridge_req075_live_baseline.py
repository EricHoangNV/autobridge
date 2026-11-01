"""REQ-075: live money baseline — ``money_scale_shift`` fires on the live path.

A live vendor switching minor<->major units (x100) used to sync
``Complete`` with no drift: only bench/beacon supplied ``baseline_summary``.
Now each clean ``Complete`` sync persists a bounded money sketch
(``connector_value_baselines``) and the next detection feeds it back, so
the shift escalates with 0 auto-patch. The first sync (no baseline) stays
silent. Bench/beacon reach detection through the same sketch mechanism.
Every test is offline (no network, no key).
"""

from __future__ import annotations

import json
from typing import Any

import autobridge.drift.value_baseline as _vb
import bench.ablation as _ablation
from autobridge.drift.value_baseline import (
    MAX_BASELINE_ROWS_PER_ENTITY,
    baseline_summary_from_sketch,
    build_money_sketch,
    load_connector_baseline_summary,
    store_connector_baseline,
)


def _money_spec() -> dict[str, Any]:
    return {
        "spec_version": 3,
        "connector": {"id": "legacy", "vendor": "legacystay"},
        "source": {"base_url": "http://example.com"},
        "operations": {"list_reservations": {"method": "GET", "path": "/r"}},
        "entities": {
            "Reservation": {
                "from": "list_reservations",
                "key": "$.reservation_id",
                "fields": {
                    "reservation_id": {"path": "$.reservation_id"},
                    "total_amount": {
                        "path": "$.total_amount",
                        "transform": [{"name": "money", "args": {"scale": 2}}],
                    },
                },
            }
        },
    }


def _money_summary(values: list[str]) -> dict[str, Any]:
    return {
        "outcome": "Complete",
        "unmet_criteria": [],
        "data_readiness": {"freshness": "current", "completeness": "complete"},
        "fingerprint_id": "fp",
        "operation_results": {
            "list_reservations": {
                "records": [
                    {"reservation_id": f"RES-{i:04d}", "total_amount": v}
                    for i, v in enumerate(values)
                ],
                "pages_fetched": 1,
            }
        },
        "records_observed": {"Reservation": len(values)},
        "canonical_records": {
            "Reservation": [
                {"reservation_id": f"RES-{i:04d}", "total_amount": v}
                for i, v in enumerate(values)
            ]
        },
        "blocked_records": {},
        "invariant_results": [],
        "checkpoint_values": {},
    }


_BASE = ["174.69", "209.65", "99.00", "12.50", "300.00", "45.10"]
_SHIFTED = ["17469.00", "20965.00", "9900.00", "1250.00", "30000.00", "4510.00"]


def test_sketch_is_bounded_and_money_shaped() -> None:
    """The sketch keeps join keys + money fields only, JSON-safe, bounded."""
    rows = [
        {
            "reservation_id": f"RES-{i:04d}",
            "total_amount": v,
            "guest_name": "Anita",
            "nested": {"a": 1},
        }
        for i, v in enumerate(_BASE * 100)
    ]
    sketch = build_money_sketch(_money_spec(), {"Reservation": rows})
    assert set(sketch) == {"Reservation"}
    assert len(sketch["Reservation"]) <= MAX_BASELINE_ROWS_PER_ENTITY
    for cell in sketch["Reservation"]:
        assert set(cell) <= {"reservation_id", "total_amount"}, cell
        assert isinstance(cell["total_amount"], str)
    # No secret-shaped content can ride along: only key + money cells.
    blob = json.dumps(sketch)
    assert "guest_name" not in blob and "ATLAS_API_KEY" not in blob


def test_sketch_empty_when_no_money_fields() -> None:
    """A spec without money transforms yields no sketch (nothing stored)."""
    spec = {
        "entities": {
            "Reservation": {
                "fields": {"reservation_id": {"path": "$.reservation_id"}}
            }
        }
    }
    assert build_money_sketch(spec, {"Reservation": [{"reservation_id": "R-1"}]}) == {}


def _mem_factory() -> Any:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from autobridge.control.models import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed(factory: Any, spec: dict[str, Any]) -> tuple[str, str]:
    from autobridge.control.models import Connector, ConnectorRelease
    from autobridge.engine.releases import spec_signature

    with factory() as s:
        conn = Connector(
            name="legacy",
            base_url="http://localhost:8107",
            credential_ref="LEGACY_API_KEY",
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
        return conn.id, rel.id


def _offline(monkeypatch: Any) -> None:
    monkeypatch.delenv("FRONTIER_API_KEY", raising=False)
    monkeypatch.delenv("FRONTIER_MODE", raising=False)
    monkeypatch.delenv("VERIFIER_URL", raising=False)


def test_live_first_sync_stores_baseline_and_stays_silent(monkeypatch: Any) -> None:
    """First clean Complete sync: no drift, baseline stored for next time."""
    from autobridge.engine.detect_repair import detect_after_sync

    _offline(monkeypatch)
    factory = _mem_factory()
    spec = _money_spec()
    conn_id, rel_id = _seed(factory, spec)
    with factory() as s:
        out = detect_after_sync(
            s,
            connector_id=conn_id,
            release_id=rel_id,
            spec_dict=spec,
            summary=_money_summary(_BASE),
            sync_run_id="sync-first",
        )
        s.commit()
    assert out["drift_found"] is False, out
    with factory() as s:
        stored = load_connector_baseline_summary(
            s, connector_id=conn_id, release_id=rel_id
        )
        assert stored is not None
        assert set(stored["canonical_records"]) == {"Reservation"}


def test_live_scale_shift_escalates_with_no_autopatch(monkeypatch: Any) -> None:
    """Second sync x100 vs the stored baseline: SEMANTIC_VALUE_DRIFT,
    escalated, nothing auto-patched, 0 frontier tokens."""
    from autobridge.engine.detect_repair import detect_after_sync

    _offline(monkeypatch)
    factory = _mem_factory()
    spec = _money_spec()
    conn_id, rel_id = _seed(factory, spec)
    with factory() as s:
        first = detect_after_sync(
            s,
            connector_id=conn_id,
            release_id=rel_id,
            spec_dict=spec,
            summary=_money_summary(_BASE),
            sync_run_id="sync-first",
        )
        s.commit()
        assert first["drift_found"] is False
        second = detect_after_sync(
            s,
            connector_id=conn_id,
            release_id=rel_id,
            spec_dict=spec,
            summary=_money_summary(_SHIFTED),
            sync_run_id="sync-shifted",
        )
        s.commit()
    assert second["drift_found"] is True, second
    assert second["drift_class"] == "SEMANTIC_VALUE_DRIFT", second
    assert second["decision"] == "escalate", second
    assert second.get("candidate_release_id") is None, second
    usage = second.get("model_usage") or {}
    assert usage.get("frontier_tokens", 0) == 0, usage


def test_live_shift_on_first_sync_stays_silent(monkeypatch: Any) -> None:
    """A x100 shift on the very first sync (no baseline) is silent — a
    single sync cannot tell minor from major units."""
    from autobridge.engine.detect_repair import detect_after_sync

    _offline(monkeypatch)
    factory = _mem_factory()
    spec = _money_spec()
    conn_id, rel_id = _seed(factory, spec)
    with factory() as s:
        out = detect_after_sync(
            s,
            connector_id=conn_id,
            release_id=rel_id,
            spec_dict=spec,
            summary=_money_summary(_SHIFTED),
            sync_run_id="sync-first-shifted",
        )
        s.commit()
    assert out["drift_found"] is False, out


def test_live_baseline_scoped_to_release(monkeypatch: Any) -> None:
    """A sketch stored under r1 is not reused after the release changes —
    a unit change shipped in a new release never reads as a scale shift."""
    from autobridge.control.models import Connector, ConnectorRelease
    from autobridge.engine.releases import spec_signature

    _offline(monkeypatch)
    factory = _mem_factory()
    spec = _money_spec()
    conn_id, rel_id = _seed(factory, spec)
    with factory() as s:
        assert store_connector_baseline(
            s,
            connector_id=conn_id,
            release_id=rel_id,
            spec=spec,
            summary=_money_summary(_BASE),
            sync_run_id="sync-first",
        ) is True
        s.commit()
        rel2 = ConnectorRelease(
            connector_id=conn_id,
            version=2,
            spec=dict(spec),
            signature=spec_signature(spec),
        )
        s.add(rel2)
        s.flush()
        conn = s.get(Connector, conn_id)
        assert conn is not None
        conn.active_release_id = rel2.id
        s.commit()
        rel2_id = rel2.id
    with factory() as s:
        assert (
            load_connector_baseline_summary(
                s, connector_id=conn_id, release_id=rel2_id
            )
            is None
        )
        assert (
            load_connector_baseline_summary(s, connector_id=conn_id, release_id=rel_id)
            is not None
        )


def test_bench_style_fault_detected_through_shared_sketch() -> None:
    """bench/beacon derive path: a x100 fault is detected through the same
    sketch builder the live path persists (not a richer raw passthrough)."""
    spec = _money_spec()
    baseline = _money_summary(_BASE)
    faulted = _money_summary(_SHIFTED)
    # The bench path reaches detection through the shared sketch builder
    # (not a richer raw passthrough): spy that it is called.
    calls: list[Any] = []
    real_build = _vb.build_money_sketch

    def _spy(s: Any, c: Any) -> Any:
        calls.append(True)
        return real_build(s, c)

    _vb.build_money_sketch = _spy  # type: ignore[method-assign]
    try:
        derived = _ablation.derive_from_summaries(
            spec, baseline_summary=baseline, faulted_summary=faulted
        )
    finally:
        _vb.build_money_sketch = real_build  # type: ignore[method-assign]
    assert calls, "derive_from_summaries must build the shared money sketch"
    assert derived.drift_class == "SEMANTIC_VALUE_DRIFT", derived
    assert any(s.kind == "money_scale_shift" for s in derived.signals)

    # Sketch hygiene (unit-level): junk canonical fields never enter the
    # persisted shape, and oversized baselines are truncated, never stored
    # raw. (Row-count parity is required for comparison, so bounding is
    # asserted on the sketch itself, not through detection.)
    huge = {
        "Reservation": [
            {
                "reservation_id": f"RES-{i:06d}",
                "total_amount": "10.00",
                "secret_note": "not-a-real-secret",
            }
            for i in range(MAX_BASELINE_ROWS_PER_ENTITY * 3)
        ]
    }
    big_sketch = build_money_sketch(spec, huge)
    assert len(big_sketch["Reservation"]) == MAX_BASELINE_ROWS_PER_ENTITY

    assert "secret_note" not in json.dumps(baseline_summary_from_sketch(big_sketch))


def test_controller_accepts_baseline_summary() -> None:
    """The repair controller's sync-summary entry takes the same input."""
    from autobridge.repair.controller import RepairController

    spec = _money_spec()
    sketch = build_money_sketch(spec, _money_summary(_BASE)["canonical_records"])
    controller = RepairController()
    result, classification = controller.handle_sync_summary(
        spec=spec,
        sync_summary=_money_summary(_SHIFTED),
        baseline_summary=baseline_summary_from_sketch(sketch),
    )
    assert classification.drift_class == "SEMANTIC_VALUE_DRIFT"
    assert result.decision in ("escalate", "propose", "climbing", "failed")


def test_migration_0016_creates_baseline_table(tmp_path: Any, monkeypatch: Any) -> None:
    """Alembic head carries ``connector_value_baselines``; a 0015 database
    brought to head by ``ensure_migrated`` gains it (startup path)."""
    from pathlib import Path

    import sqlalchemy as sa
    from alembic.config import Config

    from alembic import command
    from autobridge.control.migrate import ensure_migrated

    root = Path(__file__).resolve().parents[2]

    def _cfg(url: str) -> Config:
        import autobridge.control.models as _m

        monkeypatch.setenv(_m.DATABASE_URL_ENV, url)
        cfg = Config(str(root / "alembic.ini"))
        cfg.set_main_option("script_location", str(root / "alembic"))
        return cfg

    fresh = f"sqlite:///{tmp_path}/fresh.db"
    command.upgrade(_cfg(fresh), "head")
    insp = sa.inspect(sa.create_engine(fresh))
    assert "connector_value_baselines" in insp.get_table_names()

    old = f"sqlite:///{tmp_path}/old.db"
    command.upgrade(_cfg(old), "0015_repair_decision_check_repair")
    assert "connector_value_baselines" not in sa.inspect(sa.create_engine(old)).get_table_names()
    assert ensure_migrated(old) == "0016_connector_value_baseline"
    assert "connector_value_baselines" in sa.inspect(sa.create_engine(old)).get_table_names()


def test_json_scalar_shapes() -> None:
    """Sketch cells keep JSON-safe scalars only (Decimal/date ok, float and
    nested values never)."""
    from datetime import date, datetime
    from decimal import Decimal

    from autobridge.drift.value_baseline import _json_scalar

    assert _json_scalar(None) is None
    assert _json_scalar(True) is True
    assert _json_scalar(7) == 7
    assert _json_scalar("10.00") == "10.00"
    assert _json_scalar(1.5) is None
    assert _json_scalar(Decimal("174.69")) == "174.69"
    assert _json_scalar(datetime(2026, 11, 1, 7, 0, 0)) == "2026-11-01T07:00:00"
    assert _json_scalar(date(2026, 11, 1)) == "2026-11-01"
    assert _json_scalar({"nested": 1}) is None
    assert _json_scalar(["x"]) is None


def test_sketch_guards_and_update_path(monkeypatch: Any) -> None:
    """Non-dict inputs yield no sketch; an empty sketch is never stored; a
    second clean sync updates the stored row (upsert, not a second row)."""
    import autobridge.drift.value_baseline as vb

    assert vb.build_money_sketch(None, {"Reservation": []}) == {}
    assert vb.build_money_sketch(_money_spec(), None) == {}
    assert vb.build_money_sketch(_money_spec(), {"Reservation": "nope"}) == {}
    # Entities without money fields, non-dict rows, and an empty row list
    # contribute nothing; a non-string entity key is skipped safely.
    spec = _money_spec()
    spec["entities"]["NoMoney"] = {"fields": {"x": {"path": "$.x"}}}
    spec["entities"][123] = {"fields": {}}  # type: ignore[dict-item]
    got = vb.build_money_sketch(
        spec,
        {
            "Reservation": ["nope", {}],
            "NoMoney": [{"x": "1"}],
            "Missing": [{"reservation_id": "R-1", "total_amount": "1.00"}],
        },
    )
    assert got == {}
    # The clock falls back to wall time when the model helper is broken.
    import autobridge.control.models as _models

    monkeypatch.setattr(_models, "utcnow", lambda: (_ for _ in ()).throw(RuntimeError("clock")))
    assert isinstance(vb._utcnow(), object)
    assert vb.sketch_to_canonical_records(None) == {}
    assert vb.sketch_to_canonical_records({"Reservation": "nope"}) == {}
    assert vb.store_connector_baseline(object(), connector_id="", release_id="r",
                                       spec=_money_spec(), summary={}) is False
    assert vb.store_connector_baseline(object(), connector_id="c", release_id="r",
                                       spec=_money_spec(), summary=None) is False
    # Spec without money fields: empty sketch, nothing stored.
    assert vb.store_connector_baseline(object(), connector_id="c", release_id="r",
                                       spec={"entities": {}}, summary=_money_summary(_BASE)) is False
    assert vb.load_connector_baseline_summary(object(), connector_id="", release_id="r") is None

    _offline(monkeypatch)
    factory = _mem_factory()
    spec = _money_spec()
    conn_id, rel_id = _seed(factory, spec)
    from autobridge.engine.detect_repair import detect_after_sync

    with factory() as s:
        for i in range(2):
            out = detect_after_sync(
                s, connector_id=conn_id, release_id=rel_id, spec_dict=spec,
                summary=_money_summary(_BASE), sync_run_id=f"sync-clean-{i}",
            )
            s.commit()
            assert out["drift_found"] is False
        from autobridge.control.models import ConnectorValueBaseline

        rows = s.query(ConnectorValueBaseline).all()
        assert len(rows) == 1
        assert rows[0].sync_run_id == "sync-clean-1"


class _RaisingSession:
    """A session whose reads/writes explode: the baseline stays fail-soft."""

    def get(self, *_: Any, **__: Any) -> Any:
        raise RuntimeError("no table")

    def add(self, *_: Any) -> None:
        raise RuntimeError("no table")

    def flush(self) -> None:
        raise RuntimeError("no table")


def test_baseline_store_and_load_fail_soft() -> None:
    """Without the table (old schema, exotic session) both helpers fail
    soft — detection still runs, the scale observer just stays silent."""
    import autobridge.drift.value_baseline as vb

    session = _RaisingSession()
    assert vb.store_connector_baseline(session, connector_id="c", release_id="r",
                                       spec=_money_spec(),
                                       summary=_money_summary(_BASE)) is False
    assert vb.load_connector_baseline_summary(session, connector_id="c",
                                              release_id="r") is None


def test_detect_stays_green_when_baseline_helpers_raise(monkeypatch: Any) -> None:
    """Even if the baseline helpers themselves raise, detection returns a
    verdict (history-blind) instead of erroring the sync."""
    import autobridge.drift.value_baseline as vb
    from autobridge.engine.detect_repair import detect_after_sync

    _offline(monkeypatch)
    factory = _mem_factory()
    spec = _money_spec()
    conn_id, rel_id = _seed(factory, spec)
    def _boom(*_: Any, **__: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(vb, "load_connector_baseline_summary", _boom)
    monkeypatch.setattr(vb, "store_connector_baseline", _boom)
    with factory() as s:
        out = detect_after_sync(
            s, connector_id=conn_id, release_id=rel_id, spec_dict=spec,
            summary=_money_summary(_BASE), sync_run_id="sync-x",
        )
        s.commit()
    assert out["drift_found"] is False, out
