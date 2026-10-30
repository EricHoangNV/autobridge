"""Core data-model tests (P1.W1.5, plan §2.3).

Covers all ten tables: round-trips, closed vocabularies, money-as-Decimal,
tz-aware timestamps, ``ConnectorRelease`` immutability, the Alembic
``upgrade head → insert → downgrade → upgrade`` cycle, and a Postgres-dialect
DDL compile proving no SQLite-only types leaked in.
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from alembic import command
from autobridge.control import models as m
from autobridge.control.models import (
    Approval,
    Base,
    BuildRun,
    Connector,
    ConnectorRelease,
    DependencyRegistration,
    DriftEvent,
    EvidenceRecord,
    ImmutableReleaseError,
    ModelCall,
    RepairRun,
    SyncRun,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2]

PORTABLE_TYPES = (
    sa.String,
    sa.Text,
    sa.Integer,
    sa.Numeric,
    sa.Float,
    sa.Boolean,
    sa.DateTime,
    sa.JSON,
)

SPEC: dict[str, Any] = {
    "spec_version": 1,
    "connector": {"id": "harborpms", "vendor": "HarborPMS"},
}


@pytest.fixture()
def session(tmp_path: Path) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    m.init_db(engine)
    factory = m.session_factory(engine)
    with factory() as s:
        yield s


def make_connector(**over: Any) -> Connector:
    kwargs: dict[str, Any] = {
        "name": "HarborPMS",
        "base_url": "http://sim-harbor:8000",
        "credential_ref": "HARBOR_API_KEY",
        "target_entities": ["Reservation", "FolioCharge"],
        "runtime_target": {"kind": "local_process"},
    }
    kwargs.update(over)
    return Connector(**kwargs)


def make_release(connector_id: str, version: int = 1) -> ConnectorRelease:
    return ConnectorRelease(
        connector_id=connector_id,
        version=version,
        spec=dict(SPEC),
        signature=m.spec_signature(SPEC),
    )


def test_connector_round_trip_defaults(session: Session) -> None:
    session.add(make_connector())
    session.commit()
    row = session.query(Connector).one()
    assert row.health_status == "healthy"
    assert row.active_release_id is None
    assert row.target_entities == ["Reservation", "FolioCharge"]
    # SQLite does not store the tz; the Python-side default is tz-aware and
    # Postgres round-trips it (DateTime(timezone=True) on both).
    assert row.created_at is not None
    assert m.utcnow().tzinfo is not None


def test_connector_rejects_unknown_health() -> None:
    with pytest.raises(ValueError, match="health_status"):
        make_connector(health_status="melted")


def test_connector_stores_ref_not_secret(session: Session) -> None:
    """credential_ref names an env key; no column holds a secret value."""
    session.add(make_connector())
    session.commit()
    columns = {c.name for c in Connector.__table__.columns}
    assert "credential_ref" in columns
    assert not {c for c in columns if "secret" in c or "password" in c or "token" in c}


def test_all_health_statuses_accepted(session: Session) -> None:
    for status in sorted(m.HEALTH_STATUSES):
        session.add(make_connector(name=f"c-{status}", health_status=status))
    session.commit()
    assert session.query(Connector).count() == len(m.HEALTH_STATUSES)


def test_release_signature_matches_spec(session: Session) -> None:
    session.add(make_connector())
    session.commit()
    connector_id = session.query(Connector).one().id
    release = make_release(connector_id)
    session.add(release)
    session.commit()
    assert release.signature == m.spec_signature(SPEC)
    assert len(release.signature) == 64


def test_spec_signature_is_key_order_independent() -> None:
    assert m.spec_signature({"a": 1, "b": 2}) == m.spec_signature({"b": 2, "a": 1})


def test_release_version_unique_per_connector(session: Session) -> None:
    session.add(make_connector())
    session.commit()
    connector_id = session.query(Connector).one().id
    session.add(make_release(connector_id, version=1))
    session.commit()
    session.add(make_release(connector_id, version=1))
    with pytest.raises(IntegrityError):
        session.commit()


def test_release_update_is_blocked(session: Session) -> None:
    session.add(make_connector())
    session.commit()
    connector_id = session.query(Connector).one().id
    release = make_release(connector_id)
    session.add(release)
    session.commit()
    release.version = 2
    with pytest.raises(ImmutableReleaseError):
        session.flush()
    session.rollback()


def test_release_delete_is_blocked(session: Session) -> None:
    session.add(make_connector())
    session.commit()
    connector_id = session.query(Connector).one().id
    release = make_release(connector_id)
    session.add(release)
    session.commit()
    session.delete(release)
    with pytest.raises(ImmutableReleaseError):
        session.flush()
    session.rollback()


def test_rollback_is_a_new_release(session: Session) -> None:
    session.add(make_connector())
    session.commit()
    connector = session.query(Connector).one()
    session.add(make_release(connector.id, version=1))
    session.add(make_release(connector.id, version=2))
    session.commit()
    v1 = session.query(ConnectorRelease).filter_by(version=1).one()
    v3 = ConnectorRelease(
        connector_id=connector.id,
        version=3,
        spec=dict(v1.spec),
        parent_release_id=v1.id,
        signature=m.spec_signature(v1.spec),
    )
    session.add(v3)
    session.commit()
    assert v3.spec == v1.spec
    assert v1.spec == SPEC  # the old row was never touched


def test_active_release_wiring(session: Session) -> None:
    connector = make_connector()
    session.add(connector)
    session.commit()
    release = make_release(connector.id)
    session.add(release)
    session.commit()
    connector.active_release_id = release.id
    session.commit()
    assert connector.active_release is not None
    assert connector.active_release.id == release.id


def test_build_run_round_trip(session: Session) -> None:
    session.add(make_connector())
    session.commit()
    connector_id = session.query(Connector).one().id
    run = BuildRun(
        connector_id=connector_id,
        inputs={"docs_ref": "simulators/docs/harbor.md"},
        draft_spec=dict(SPEC),
        evidence_ids=["ev-1"],
    )
    session.add(run)
    session.commit()
    assert run.state == "queued"
    assert run.draft_spec == SPEC
    assert run.result is None


def test_sync_run_outcomes(session: Session) -> None:
    session.add(make_connector())
    session.commit()
    connector_id = session.query(Connector).one().id
    release = make_release(connector_id)
    session.add(release)
    session.commit()
    for outcome in ("Complete", "Degraded", "Blocked"):
        session.add(
            SyncRun(
                release_id=release.id,
                outcome=outcome,
                records_observed={"Reservation": 10},
                records_emitted={"Reservation": 10},
                unmet_criteria=[] if outcome == "Complete" else ["no_loss"],
                data_readiness={"freshness": "fresh", "completeness": 1.0},
                fingerprint_id="fp-1",
            )
        )
    session.commit()
    assert session.query(SyncRun).count() == 3


def test_sync_run_rejects_unknown_outcome() -> None:
    with pytest.raises(ValueError, match="outcome"):
        SyncRun(release_id="r", outcome="MostlyFine")


def test_drift_event_lifecycle(session: Session) -> None:
    session.add(make_connector())
    session.commit()
    connector_id = session.query(Connector).one().id
    event = DriftEvent(
        connector_id=connector_id,
        drift_class="rename_field",
        severity="high",
        evidence_ids=["ev-1", "ev-2"],
    )
    session.add(event)
    session.commit()
    assert event.status == "open"
    event.status = "investigating"
    session.commit()
    assert event.status == "investigating"
    with pytest.raises(ValueError, match="status"):
        event.status = "vibes"


def test_repair_run_patch_round_trip(session: Session) -> None:
    patch = [{"op": "replace", "path": "/operations/x/path", "value": "/v3/items"}]
    run = RepairRun(
        drift_event_ids=["de-1"],
        ladder_level_reached=1,
        candidate_patch=patch,
        rule_id="rename_field",
        decision="propose",
        model_usage={"frontier_tokens": 0},
    )
    session.add(run)
    session.commit()
    assert run.candidate_patch == patch
    assert run.hypothesis_ids == []


def test_repair_run_rejects_unknown_decision() -> None:
    with pytest.raises(ValueError, match="decision"):
        RepairRun(decision="yolo")


def test_approval_chain(session: Session) -> None:
    session.add(make_connector())
    session.commit()
    connector_id = session.query(Connector).one().id
    session.add(make_release(connector_id, version=1))
    session.add(make_release(connector_id, version=2))
    session.commit()
    v1 = session.query(ConnectorRelease).filter_by(version=1).one()
    v2 = session.query(ConnectorRelease).filter_by(version=2).one()
    repair = RepairRun(decision="propose")
    session.add(repair)
    session.commit()
    approval = Approval(
        repair_run_id=repair.id,
        actor="owner",
        decision="approve",
        from_release_id=v1.id,
        to_release_id=v2.id,
    )
    session.add(approval)
    session.commit()
    rollback = Approval(
        repair_run_id=repair.id,
        actor="owner",
        decision="rollback",
        from_release_id=v2.id,
        to_release_id=v1.id,
        rollback_of_release_id=v2.id,
    )
    session.add(rollback)
    session.commit()
    assert rollback.rollback_of_release_id == v2.id
    with pytest.raises(ValueError, match="decision"):
        Approval(repair_run_id=repair.id, actor="owner", decision="maybe")


def test_evidence_chain(session: Session) -> None:
    root = EvidenceRecord(kind="probe", payload={"url": "/v2/bookings"}, run_id="sync:1")
    session.add(root)
    session.commit()
    child = EvidenceRecord(
        kind="observation",
        payload={"count": 10},
        parent_ids=[root.id],
        run_id="sync:1",
    )
    session.add(child)
    session.commit()
    assert child.parent_ids == [root.id]
    with pytest.raises(ValueError, match="kind"):
        EvidenceRecord(kind="rumor", run_id="sync:1")


def test_model_call_ledger_decimal_money(session: Session) -> None:
    call = ModelCall(
        run_id="repair:1",
        provider="anthropic",
        model="claude-test-small",
        tier="small",
        input_tokens=120,
        output_tokens=45,
        latency_ms=812.5,
        cost_estimate=Decimal("0.001234"),
        purpose="hypothesis ranking",
    )
    session.add(call)
    session.commit()
    row = session.query(ModelCall).one()
    assert row.cost_estimate == Decimal("0.001234")
    assert isinstance(row.cost_estimate, Decimal)
    with pytest.raises(ValueError, match="tier"):
        ModelCall(run_id="x", provider="p", model="m", tier="huge")


def test_dependency_registration(session: Session) -> None:
    session.add(make_connector())
    session.commit()
    connector_id = session.query(Connector).one().id
    reg = DependencyRegistration(
        consumer_id="demo-dashboard",
        connector_id=connector_id,
        entity="Reservation",
        policy="pause_on_release_change",
    )
    session.add(reg)
    session.commit()
    assert reg.paused is False
    reg.paused = True
    session.commit()
    assert reg.paused is True
    session.add(
        DependencyRegistration(
            consumer_id="demo-dashboard",
            connector_id=connector_id,
            entity="Reservation",
            policy="notify",
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    with pytest.raises(ValueError, match="policy"):
        DependencyRegistration(
            consumer_id="c", connector_id=connector_id, entity="E", policy="ignore"
        )


def _all_tables() -> list[sa.Table]:
    # sorted_tables warns on the connectors ↔ releases cycle (resolved in the
    # migration by adding the active-release FK last), so sort by name here.
    return sorted(Base.metadata.tables.values(), key=lambda t: t.name)


def test_only_portable_column_types() -> None:
    for table in _all_tables():
        for column in table.columns:
            assert isinstance(column.type, PORTABLE_TYPES), (
                f"{table.name}.{column.name}: {type(column.type).__name__} "
                "is not Postgres-ready"
            )


def test_ddl_compiles_on_postgres() -> None:
    dialect = postgresql.dialect()
    for table in _all_tables():
        ddl = str(sa.schema.CreateTable(table).compile(dialect=dialect))
        assert f"CREATE TABLE {table.name}" in ddl


def test_index_names_unique() -> None:
    names = [
        index.name
        for table in Base.metadata.sorted_tables
        for index in table.indexes
        if index.name
    ]
    assert len(names) == len(set(names))


def test_helpers_and_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = f"sqlite:///{tmp_path}/helper.db"
    monkeypatch.setenv(m.DATABASE_URL_ENV, url)
    assert m.default_database_url() == url
    engine = m.engine_from_url(m.default_database_url())
    m.init_db(engine)
    with m.session_factory(engine)() as s:
        s.add(make_connector())
        s.commit()
        assert s.query(Connector).count() == 1
    monkeypatch.delenv(m.DATABASE_URL_ENV)
    assert m.default_database_url() == m.DEFAULT_SQLITE_URL


def test_alembic_upgrade_downgrade_cycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "mig.db"
    monkeypatch.setenv(m.DATABASE_URL_ENV, f"sqlite:///{db}")
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(cfg, "head")
    engine = create_engine(f"sqlite:///{db}")
    with m.session_factory(engine)() as s:
        connector = make_connector()
        s.add(connector)
        s.commit()
        s.add(make_release(connector.id))
        s.commit()
        assert s.query(ConnectorRelease).count() == 1
    command.downgrade(cfg, "base")
    with engine.connect() as conn:
        tables = sa.inspect(conn).get_table_names()
    assert [t for t in tables if t != "alembic_version"] == []
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        tables = sa.inspect(conn).get_table_names()
    tables = [t for t in tables if t != "alembic_version"]
    assert len(tables) == len(Base.metadata.tables)
    assert set(tables) == set(Base.metadata.tables)
    # Silence unused-import guard for os-level env handling.
    assert os.environ[m.DATABASE_URL_ENV].startswith("sqlite:///")


def test_alembic_0009_adds_req030_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pre-0009 database (at 0008) upgrades to head: repair delta/class,
    model-call serving columns on both ledger tables, and the PABX table."""
    db = tmp_path / "pre0009.db"
    monkeypatch.setenv(m.DATABASE_URL_ENV, f"sqlite:///{db}")
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(cfg, "0008_model_call_reasoning_tokens")
    command.upgrade(cfg, "head")
    engine = create_engine(f"sqlite:///{db}")
    with engine.connect() as conn:
        insp = sa.inspect(conn)
        repair_cols = {c["name"] for c in insp.get_columns("repair_runs")}
        assert {"drift_class", "fingerprint_delta"} <= repair_cols
        for table in ("model_calls", "model_call_archive"):
            cols = {c["name"] for c in insp.get_columns(table)}
            assert {"mode", "captured_at", "outcome"} <= cols
        assert "pabx_link_events" in insp.get_table_names()


def test_ensure_migrated_repairs_stale_version_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-030: create_all raced ahead of the version stamp (new schema,
    old row) — ensure_migrated trusts the schema instead of re-adding
    columns and failing."""
    from autobridge.control.migrate import ensure_migrated  # noqa: PLC0415

    db = tmp_path / "stale.db"
    url = f"sqlite:///{db}"
    monkeypatch.setenv(m.DATABASE_URL_ENV, url)
    engine = create_engine(url)
    m.init_db(engine)  # current models: full schema incl. scheduler_state, no version row
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    command.stamp(cfg, "0008_model_call_reasoning_tokens")
    assert ensure_migrated(url) == "0015_repair_decision_check_repair"


def test_model_call_ledger_stores_reasoning_split(session: Session) -> None:
    call = ModelCall(
        run_id="repair:reason",
        provider="muse",
        model="muse-spark-1.3",
        tier="frontier",
        input_tokens=319,
        output_tokens=4319,
        reasoning_tokens=4000,
        purpose="l3-generate-patch",
    )
    session.add(call)
    session.commit()
    row = session.query(ModelCall).one()
    assert row.reasoning_tokens == 4000
    # Reasoning is a sub-count of output, never on top of it.
    assert row.output_tokens == 4319


def test_alembic_0008_adds_reasoning_tokens_to_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pre-0008 database (at 0007) upgrades to head with the reasoning
    split present on both ledger tables and old rows reading back as 0."""
    db = tmp_path / "pre0008.db"
    monkeypatch.setenv(m.DATABASE_URL_ENV, f"sqlite:///{db}")
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(cfg, "0007")
    engine = create_engine(f"sqlite:///{db}")
    with engine.connect() as conn:
        insp = sa.inspect(conn)
        assert "reasoning_tokens" not in {c["name"] for c in insp.get_columns("model_calls")}
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        insp = sa.inspect(conn)
        for table in ("model_calls", "model_call_archive"):
            assert "reasoning_tokens" in {c["name"] for c in insp.get_columns(table)}
    with m.session_factory(engine)() as s:
        s.add(
            ModelCall(
                run_id="repair:old",
                provider="muse",
                model="muse-spark-1.3",
                tier="frontier",
                input_tokens=10,
                output_tokens=20,
                reasoning_tokens=15,
                purpose="l3-generate-patch",
            )
        )
        s.commit()
        assert s.query(ModelCall).one().reasoning_tokens == 15
