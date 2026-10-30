"""REQ-055 item 1: every existing database ends with the 0013 value set.

Owner keyed check (2026-10-30): a live database at ``alembic_version 0014``
answered ``POST /repairs/{id}/approve-proposal`` with 422
(``CHECK constraint failed ... SET decision='approved'``) — its
``repair_runs`` CHECK still held the 0011 value set because 0013's upgrade
was stamped over, never run (``_REVISION_EFFECTS`` could not see
CHECK-only migrations).

* A database upgraded from each earlier revision, then brought to head by
  ``ensure_migrated`` (the startup/reset path, not raw ``upgrade head``),
  must approve a proposal (escalated path) and a fix (propose path).
* The exact stuck state — version row 0014, 5-value CHECK — is repaired.
* The approve routes run against the migrated schema, not only
  ``create_all``.
* Every CHECK in the migrated schema matches a fresh ``create_all``
  (audit for the doubled-name problem on every other CHECK).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from sqlalchemy import create_engine

from alembic import command
from autobridge.control import models as m
from autobridge.control.migrate import _REVISION_EFFECTS, ensure_migrated

ROOT = Path(__file__).resolve().parents[2]
REVISIONS = [rev for rev, _ in _REVISION_EFFECTS]
TERMINAL_VALUES = {"propose", "escalate", "reject", "climbing", "failed", "approved", "rolled_back"}


def _cfg(url: str, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv(m.DATABASE_URL_ENV, url)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    return cfg


def _check_sql(url: str) -> str:
    eng = create_engine(url)
    try:
        with eng.connect() as conn:
            row = conn.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE tbl_name='repair_runs'"
            ).fetchone()
    finally:
        eng.dispose()
    assert row is not None
    return str(row[0])


def _check_values(url: str) -> set[str]:
    return set(re.findall(r"'([^']*)'", _check_sql(url)))


def _factory(url: str) -> Any:
    return m.session_factory(create_engine(url))


def _harbor_like_spec() -> dict[str, Any]:
    return {
        "spec_version": 1,
        "connector": {"id": "harbor", "vendor": "harbor", "runtime_target": "cloud"},
        "source": {
            "base_url": "https://example.com",
            "auth": {"type": "bearer", "credential_ref": "TEST_TOKEN"},
            "rate_limit": {
                "max_rps": 5,
                "retry": {"statuses": [429, 503], "backoff": "exponential", "max_attempts": 3},
            },
            "timezone_source": {"kind": "static", "value": "UTC"},
        },
        "operations": {
            "list_reservations": {
                "method": "GET",
                "path": "/reservations",
                "params": {},
                "pagination": {
                    "strategy": "cursor",
                    "cursor_param": "cursor",
                    "next_path": "$.meta.next_cursor",
                    "items_path": "$.data",
                    "stop_when": ["null_cursor"],
                },
            }
        },
        "entities": {
            "Reservation": {
                "from": "list_reservations",
                "key": "$.reservation_id",
                "fields": {
                    "reservation_id": {"path": "$.reservation_id", "transform": []},
                    "total_amount": {
                        "path": "$.total_amount",
                        "transform": [{"name": "money", "args": {"scale": 2}}],
                    },
                },
                "checks": [
                    {"kind": "unique_key"},
                    {"kind": "no_loss", "compare_to": "$.total"},
                    {"kind": "money_invariant", "field": "total_amount", "compare_to": "$.gross_sum"},
                ],
            },
        },
        "capabilities": {"read": ["Reservation"]},
        "health": {"canary": {"every": "60s", "operation": "list_reservations"}, "fingerprint": {}},
    }


def _seed_connector(factory: Any) -> dict[str, str]:
    from autobridge.engine.releases import create_release

    with factory() as s:
        conn = m.Connector(
            name="harborpms",
            base_url="http://127.0.0.1:8106",
            credential_ref="HARBOR_API_KEY",
            target_entities=["Reservation"],
            runtime_target={"kind": "local_process"},
        )
        s.add(conn)
        s.commit()
        r1 = create_release(s, connector_id=conn.id, spec=_harbor_like_spec())
        s.commit()
        conn.active_release_id = r1.id
        drift = m.DriftEvent(
            connector_id=conn.id,
            drift_class="SEMANTIC_VALUE_DRIFT",
            severity="high",
            source_sync_run_id=None,
            evidence_ids=[],
        )
        s.add(drift)
        s.commit()
        return {"connector_id": conn.id, "release_id": r1.id, "drift_id": drift.id}


def _seed_escalated(factory: Any, ids: dict[str, str]) -> str:
    proposal = {
        "label": "Accept net",
        "effect": "Retarget.",
        "patch": [
            {"op": "replace", "path": "/entities/Reservation/checks/2/compare_to", "value": "$.total"},
        ],
    }
    with factory() as s:
        run = m.RepairRun(
            drift_event_ids=[ids["drift_id"]],
            decision="escalate",
            model_usage={"frontier_tokens": 10, "small_tokens": 0},
            vendor="harbor",
            drift_class="SEMANTIC_VALUE_DRIFT",
            escalation_bundle={
                "reason": "semantic drift, no verified fix",
                "question": "Should the new behaviour stand?",
                "evidence_ids": [],
                "suggested_action": "manual review",
                "proposal": proposal,
            },
        )
        s.add(run)
        s.commit()
        return run.id


def _approve_proposal(factory: Any, run_id: str) -> str:
    from autobridge.engine.releases import approve_business_decision

    with factory() as s:
        _candidate, approval = approve_business_decision(
            s, repair_run_id=run_id, actor="operator"
        )
        s.commit()
        assert approval.decision == "approve"
        run = s.get(m.RepairRun, run_id)
        assert run is not None
        return str(run.decision)


def _approve_fix(factory: Any, ids: dict[str, str]) -> str:
    from autobridge.engine.releases import approve_release, create_release_from_patch

    with factory() as s:
        run = m.RepairRun(
            drift_event_ids=[ids["drift_id"]],
            decision="propose",
            model_usage={},
            vendor="harbor",
            drift_class="PAGE_TO_CURSOR",
            candidate_patch=[
                {
                    "op": "replace",
                    "path": "/operations/list_reservations/pagination/strategy",
                    "value": "cursor",
                }
            ],
        )
        s.add(run)
        s.commit()
        run_id = run.id
        candidate = create_release_from_patch(
            s,
            connector_id=ids["connector_id"],
            base_release_id=ids["release_id"],
            patch=list(run.candidate_patch or []),
            provenance_repair_run_id=run_id,
        )
        s.commit()
        approve_release(
            s,
            repair_run_id=run_id,
            actor="operator",
            from_release_id=ids["release_id"],
            to_release_id=candidate.id,
            connector_id=ids["connector_id"],
        )
        s.commit()
        run = s.get(m.RepairRun, run_id)
        assert run is not None
        return str(run.decision)


@pytest.mark.parametrize("revision", REVISIONS)
def test_upgrade_from_each_revision_then_approve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, revision: str
) -> None:
    """REQ-055 item 1: a DB created at each earlier revision approves cleanly.

    The database is built at *revision*, then brought to head through
    ``ensure_migrated`` (the startup path that once skipped 0013) — never
    a raw ``upgrade head``. Both approve paths must commit
    ``decision='approved'`` (the owner's 422 is the CHECK rejecting it).
    """
    url = f"sqlite:///{tmp_path}/from_{revision}.db"
    cfg = _cfg(url, monkeypatch)
    command.upgrade(cfg, revision)
    assert ensure_migrated(url) == REVISIONS[-1]
    assert _check_values(url) >= TERMINAL_VALUES, _check_sql(url)
    factory = _factory(url)
    ids = _seed_connector(factory)
    assert _approve_proposal(factory, _seed_escalated(factory, ids)) == "approved"
    assert _approve_fix(factory, ids) == "approved"


def test_stuck_0014_database_is_repaired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-055 item 1: the owner's live state (0014 stamp, 0011 CHECK) heals.

    Version row 0014 with the 5-value CHECK is what the old stamp-skipping
    detection produced. ``ensure_migrated`` must now run the repair
    migration and both approve paths must commit.
    """
    url = f"sqlite:///{tmp_path}/stuck_0014.db"
    cfg = _cfg(url, monkeypatch)
    command.upgrade(cfg, "0012_model_call_prompt_ref")
    command.stamp(cfg, "0014_refused_build_attempts")
    assert TERMINAL_VALUES - _check_values(url) == {"approved", "rolled_back"}
    assert ensure_migrated(url) == REVISIONS[-1]
    assert _check_values(url) >= TERMINAL_VALUES, _check_sql(url)
    factory = _factory(url)
    ids = _seed_connector(factory)
    assert _approve_proposal(factory, _seed_escalated(factory, ids)) == "approved"
    assert _approve_fix(factory, ids) == "approved"


def test_approve_routes_on_migrated_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-055 item 1: the approve routes run on the migrated schema.

    Mirrors the REQ-049 route coverage but against an Alembic-migrated
    database instead of ``create_all`` — the schema the live stack serves.
    """
    from fastapi.testclient import TestClient

    from autobridge.control.app import app
    from autobridge.control.deps import get_db

    monkeypatch.setenv("SIMULATOR_ADMIN_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("AUTOBRIDGE_SYNC_INTERVAL_S", "10")
    url = f"sqlite:///{tmp_path}/migrated_routes.db"
    cfg = _cfg(url, monkeypatch)
    command.upgrade(cfg, "head")
    assert ensure_migrated(url) == REVISIONS[-1]
    factory = _factory(url)

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
    try:
        client = TestClient(app)
        ids = _seed_connector(factory)
        # Propose path: candidate release + POST /repairs/{id}/approve.
        with factory() as s:
            run = m.RepairRun(
                drift_event_ids=[ids["drift_id"]],
                decision="propose",
                model_usage={},
                vendor="harbor",
                drift_class="PAGE_TO_CURSOR",
                candidate_patch=[
                    {
                        "op": "replace",
                        "path": "/operations/list_reservations/pagination/strategy",
                        "value": "cursor",
                    }
                ],
            )
            s.add(run)
            s.commit()
            propose_id = run.id
        from autobridge.engine.releases import create_release_from_patch

        with factory() as s:
            run = s.get(m.RepairRun, propose_id)
            assert run is not None
            candidate = create_release_from_patch(
                s,
                connector_id=ids["connector_id"],
                base_release_id=ids["release_id"],
                patch=list(run.candidate_patch or []),
                provenance_repair_run_id=propose_id,
            )
            s.commit()
            candidate_id = candidate.id
        r = client.post(
            f"/repairs/{propose_id}/approve",
            json={"connector_id": ids["connector_id"], "from_release_id": ids["release_id"], "to_release_id": candidate_id},
        )
        assert r.status_code == 200, r.text
        # Proposal path: escalated repair + POST /repairs/{id}/approve-proposal.
        run_id = _seed_escalated(factory, ids)
        r = client.post(f"/repairs/{run_id}/approve-proposal", json={"connector_id": ids["connector_id"]})
        assert r.status_code == 200, r.text
        with factory() as s:
            assert str(s.get(m.RepairRun, run_id).decision) == "approved"  # type: ignore[union-attr]
    finally:
        app.dependency_overrides.clear()


def test_migrated_checks_match_create_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-055 item 1: no other CHECK diverged (doubled-name audit).

    Every CHECK constraint in a head-migrated database must equal the one
    a fresh ``create_all`` produces — same name, same expression. This
    fails if any migration touched a CHECK under a name the convention
    rewrites.
    """

    def _checks(url: str) -> dict[str, dict[str, str]]:
        eng = create_engine(url)
        try:
            with eng.connect() as conn:
                import sqlalchemy as sa

                insp = sa.inspect(conn)
                out: dict[str, dict[str, str]] = {}
                for table in insp.get_table_names():
                    try:
                        constraints = insp.get_check_constraints(table)
                    except Exception:
                        continue
                    for chk in constraints or []:
                        out.setdefault(table, {})[str(chk.get("name"))] = str(chk.get("sqltext"))
        finally:
            eng.dispose()
        return out

    migrated_url = f"sqlite:///{tmp_path}/migrated.db"
    cfg = _cfg(migrated_url, monkeypatch)
    command.upgrade(cfg, "head")
    fresh_url = f"sqlite:///{tmp_path}/fresh.db"
    m.Base.metadata.create_all(create_engine(fresh_url))
    migrated, fresh = _checks(migrated_url), _checks(fresh_url)
    assert set(migrated) == set(fresh)
    for table in fresh:
        # Names must match exactly (the doubled-name audit); expressions
        # compare as value sets — migrations list values in file order
        # while ``_check()`` sorts them.
        assert set(migrated[table]) == set(fresh[table]), table
        for name in fresh[table]:
            assert set(re.findall(r"'([^']*)'", migrated[table][name])) == set(
                re.findall(r"'([^']*)'", fresh[table][name])
            ), (table, name)
    assert set(re.findall(r"'([^']*)'", json.dumps(migrated))) >= TERMINAL_VALUES


def test_check_values_helper_handles_missing_tables_and_dialects(tmp_path: Path) -> None:
    """REQ-055 item 1: CHECK detection degrades safely, never loops upgrades."""
    from sqlalchemy import inspect as _inspect

    from autobridge.control.migrate import _check_values

    url = f"sqlite:///{tmp_path}/helper.db"
    eng = create_engine(url)
    try:
        with eng.connect() as conn:
            insp = _inspect(conn)
            # No tables yet: reflection fails open (the tables check below
            # already rejects the revision; CHECKs never decide alone).
            assert _check_values(insp, "repair_runs", "decision") is None
            m.Base.metadata.create_all(eng)
        with eng.connect() as conn:
            insp = _inspect(conn)
            assert (_check_values(insp, "repair_runs", "decision") or set()) >= TERMINAL_VALUES
            # A table without a decision CHECK reads empty.
            assert _check_values(insp, "jobs", "decision") == set()

            class _Boom:
                def get_check_constraints(self, _table: str) -> list[dict[str, str]]:
                    raise RuntimeError("no reflection here")

            # Unreflectable dialects fail open (treated as satisfied).
            assert _check_values(_Boom(), "repair_runs", "decision") is None
    finally:
        eng.dispose()


def test_detect_revision_sees_check_contents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-055 item 1: detection stops below a CHECK-only migration whose
    values are absent — the stamp can no longer skip its DDL."""
    from autobridge.control.migrate import _detect_revision

    url = f"sqlite:///{tmp_path}/detect.db"
    cfg = _cfg(url, monkeypatch)
    command.upgrade(cfg, "0012_model_call_prompt_ref")
    # 5-value CHECK: detection must stop at 0012, not sail through 0013.
    assert _detect_revision(url) == "0012_model_call_prompt_ref"
    command.upgrade(cfg, "head")
    assert _detect_revision(url) == REVISIONS[-1]


def test_model_validators_and_helpers_cover_branches() -> None:
    """REQ-055: incidental 90%-gate coverage — validators reject, helpers degrade."""
    import sys as _sys

    # Non-sqlite URLs pass through untouched (no pragmas branch).
    from unittest import mock as _mock

    import pytest as _pytest
    import sqlalchemy as _sa

    with _mock.patch.object(_sa, "create_engine", return_value="ENGINE") as _ce:
        assert m.engine_from_url("postgresql://u:p@127.0.0.1/db") == "ENGINE"
        _ce.assert_called_once()
    # Validators reject unknown enum values (never guessed, plan §4).
    with _pytest.raises(ValueError, match="tier"):
        m.ModelCall(run_id="r", provider="p", model="m", tier="bogus", purpose="p")
    with _pytest.raises(ValueError, match="status"):
        m.DiscoveryRecord(discovery_id="d", vendor="v", status="bogus")
    with _pytest.raises(ValueError, match="kind"):
        m.PabxLinkEvent(connector_id="c", kind="bogus")
    # The OpenAPI description helper degrades when the registry is missing.
    from autobridge.control.schemas import _demo_target_ids as _ids
    from autobridge.control.schemas import _reject_non_string_instant as _rej

    assert isinstance(_ids(), list)
    monkey_broken = _sys.modules.pop("autobridge.control.demo_targets", None)
    try:
        _sys.modules["autobridge.control.demo_targets"] = None  # type: ignore[assignment]
        assert _ids() == []
    finally:
        if monkey_broken is not None:
            _sys.modules["autobridge.control.demo_targets"] = monkey_broken
        else:
            _sys.modules.pop("autobridge.control.demo_targets", None)
    assert _rej(None) is None
    with _pytest.raises(ValueError, match="RFC3339"):
        _rej(123)


def test_check_values_parses_postgres_any_array() -> None:
    """REQ-055 item 1: Postgres reflects the CHECK as ANY(ARRAY[...])."""
    from autobridge.control.migrate import _check_values

    class _FakeInspector:
        def get_check_constraints(self, _table: str) -> list[dict[str, str]]:
            return [
                {
                    "name": "ck_repair_runs_ck_repair_runs_decision",
                    "sqltext": (
                        "CHECK ((decision)::text = ANY "
                        "((ARRAY['propose'::character varying, 'escalate'::character varying, "
                        "'reject'::character varying, 'climbing'::character varying, "
                        "'failed'::character varying, 'approved'::character varying, "
                        "'rolled_back'::character varying])::text[]))"
                    ),
                }
            ]

    assert _check_values(_FakeInspector(), "repair_runs", "decision") == TERMINAL_VALUES
    assert _check_values(_FakeInspector(), "repair_runs", "other") == set()
