"""P2.S7.1 — thin ``ab`` CLI wrapper over the control API (plan §5 S7).

Passing criterion (plan §5 S7): ``ab build / sync / repairs approve``
drive the same journey as the UI; own e2e file
``tests/e2e/test_s7_cli.py``.

Fake transports (control TestClient as the CLI HTTP client, SQLite) keep
the suite green without Docker; live tests use the ``e2e_stack`` fixture
and skip when the stack is down. Trust boundary: this file lives in
tests/e2e and talks to the control plane over HTTP when live; the CLI
itself (``src/autobridge/cli.py``) imports no engine, verifier or
simulator code.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from autobridge import cli as cli_mod

if TYPE_CHECKING:
    import pathlib

CONTROL_URL = "http://localhost:8101"


@contextmanager
def _fake_cli_client(tmp_path: pathlib.Path, monkeypatch: Any) -> Any:  # type: ignore[no-untyped-def]
    """Yield a TestClient-as-CLI-client: ``ab`` talks HTTP, no network."""
    from autobridge.control.app import app
    from autobridge.control.deps import get_db
    from autobridge.control.models import Base, session_factory

    engine = create_engine(f"sqlite:///{tmp_path}/control.db")
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
    try:
        probe = TestClient(app)

        def _factory(_base_url: str, _timeout: float) -> TestClient:  # type: ignore[type-arg]
            # Fresh TestClient per CLI invocation: main() closes the
            # client it created, and a closed client rejects requests.
            return TestClient(app)

        monkeypatch.setattr(cli_mod, "_make_client", _factory)
        yield probe
    finally:
        app.dependency_overrides.clear()


def _run(argv: list[str], capsys: Any) -> tuple[int, str, str]:
    rc = cli_mod.main(argv)
    out = capsys.readouterr()
    return rc, out.out, out.err


# ---------------------------------------------------------------------------
# Fake: the full CI journey through the CLI
# ---------------------------------------------------------------------------


def test_s7_cli_same_journey_as_ui_fake(tmp_path: pathlib.Path, monkeypatch: Any, capsys: Any) -> None:
    """create → build → sync → runs get all exit 0 against a fake control plane."""
    with _fake_cli_client(tmp_path, monkeypatch):
        rc, out, _ = _run(["--output", "json", "connectors", "create",
                           "--name", "CliPMS", "--base-url", "https://clipms.example.com",
                           "--credential-ref", "CLIPMS_API_KEY",
                           "--target-entities", "Reservation"], capsys)
        assert rc == 0, out
        connector_id = json.loads(out)["id"]

        rc, out, _ = _run(["--output", "json", "build", connector_id,
                           "--docs-text", "GET /reservations with cursor pagination.",
                           "--wait", "--wait-timeout", "60"], capsys)
        assert rc == 0, out
        build_body = json.loads(out)
        assert build_body["build_run_id"], build_body

        rc, out, _ = _run(["--output", "json", "runs", "get", build_body["build_run_id"]], capsys)
        assert rc == 0, out
        assert json.loads(out)["kind"] == "build"

        # REQ-013: create → build → (activate) → sync. The first build
        # auto-activates, so this is idempotent — but the journey names the
        # step explicitly instead of relying on the fallback.
        rc, out, _ = _run(["--output", "json", "connectors", "get", connector_id], capsys)
        assert rc == 0, out
        newest = max(json.loads(out)["releases"], key=lambda r: r["version"])
        rc, out, _ = _run(["--output", "json", "activate", connector_id, newest["id"]], capsys)
        assert rc == 0, out

        rc, out, _ = _run(["--output", "json", "sync", connector_id, "--wait",
                           "--wait-timeout", "60"], capsys)
        assert rc == 0, out
        sync_body = json.loads(out)
        assert sync_body.get("sync_run_id") or sync_body.get("job_id"), sync_body

        rc, out, _ = _run(["drift", "list", "--connector", connector_id], capsys)
        assert rc == 0, out
        rc, out, _ = _run(["repairs", "list", "--connector", connector_id], capsys)
        assert rc == 0, out


def test_s7_cli_repairs_approve_and_reject_fake(
    tmp_path: pathlib.Path, monkeypatch: Any, capsys: Any
) -> None:
    """Approve/reject via the CLI reach the same endpoints as the UI buttons."""
    with _fake_cli_client(tmp_path, monkeypatch) as client:
        from autobridge.control.models import (
            ConnectorRelease,
            DriftEvent,
            RepairRun,
            session_factory,
        )
        # Seed through the public create endpoint so FK targets are real.
        r = client.post("/connectors", json={"name": "CliRepair", "base_url": "https://r.example.com",
                                             "credential_ref": "CLIR_API_KEY",
                                             "target_entities": ["Reservation"]})
        assert r.status_code == 201, r.text
        connector_id = r.json()["id"]
        detail = client.get(f"/connectors/{connector_id}").json()
        release_id = (detail.get("releases") or [{}])[0].get("id")

        eng = create_engine(f"sqlite:///{tmp_path}/control.db")
        fac = session_factory(eng)
        s = fac()
        try:
            drift = DriftEvent(connector_id=connector_id, drift_class="offset_to_cursor",
                               severity="medium", evidence_ids=[], status="open")
            s.add(drift)
            s.flush()
            if release_id is None:
                rel = ConnectorRelease(connector_id=connector_id, version=1,
                                       spec={"connector": "seed"}, signature="0" * 64)
                s.add(rel)
                s.flush()
                release_id = rel.id
            # Candidate release for the approve path (provenance set at
            # insert: release rows are immutable, updates raise).
            rep = RepairRun(drift_event_ids=[drift.id], ladder_level_reached=1,
                            rule_id="offset_to_cursor", decision="propose",
                            model_usage={"frontier_tokens": 0})
            s.add(rep)
            s.flush()
            cand = ConnectorRelease(connector_id=connector_id, version=2,
                                    spec={"connector": "seed"},
                                    parent_release_id=release_id, signature="1" * 64,
                                    provenance_repair_run_id=rep.id)
            s.add(cand)
            s.flush()
            rep_id, cand_id, drift_id = rep.id, cand.id, drift.id
            s.commit()
        finally:
            s.close()

        rc, out, err = _run(["--output", "json", "repairs", "approve", rep_id,
                             "--connector", connector_id,
                             "--from-release", release_id,
                             "--to-release", cand_id], capsys)
        assert rc == 0, err
        assert json.loads(out)["repair_run_id"] == rep_id

        rc, out, _ = _run(["repairs", "get", rep_id], capsys)
        assert rc == 0, out
        assert "propose" in out or "approve" in out

        # Reject path: a second propose run rejected from the CLI.
        s2 = fac()
        try:
            rep2 = RepairRun(drift_event_ids=[drift_id], ladder_level_reached=1,
                             decision="propose", model_usage={"frontier_tokens": 0})
            s2.add(rep2)
            s2.commit()
            rep2_id = rep2.id
        finally:
            s2.close()
        rc, out, err = _run(["--output", "json", "repairs", "reject", rep2_id], capsys)
        assert rc == 0, err

        # Approving a missing repair is a clean CLI failure, not a traceback.
        rc, _, err = _run(["repairs", "approve", "rep-does-not-exist"], capsys)
        assert rc == 1
        assert "HTTP 404" in err


def test_s7_cli_never_takes_credential_values() -> None:
    """Pin the secrets rule: no flag accepts a credential value."""
    parser = cli_mod.build_parser()
    import argparse as _ap

    seen: set[str] = set()

    def _walk(p: _ap.ArgumentParser) -> None:
        for a in p._actions:
            seen.update(a.option_strings)
        for sp in [a for a in p._actions if isinstance(a, _ap._SubParsersAction)]:
            for sub_p in sp.choices.values():
                _walk(sub_p)

    _walk(parser)
    for banned in ("--credential-value", "--api-key", "--secret", "--token", "--password"):
        assert banned not in seen, banned


# ---------------------------------------------------------------------------
# Live: same journey against the Compose stack (skips when down)
# ---------------------------------------------------------------------------


def test_s7_cli_live_against_stack(e2e_stack: str, capsys: Any) -> None:
    if os.getenv("E2E_LIVE") != "1" and e2e_stack not in ("managed", "already_up", "external"):
        pytest.skip("live CLI journey needs the Compose stack (E2E_LIVE=1)")
    if e2e_stack == "external" and os.getenv("E2E_LIVE") != "1":
        pytest.skip("live CLI journey needs the Compose stack (E2E_LIVE=1)")
    base = CONTROL_URL
    rc, out, err = _run(["--control-url", base, "--output", "json", "health"], capsys)
    assert rc == 0, err
    body = json.loads(out)
    assert body["healthz"]["status"] == "ok"
    assert body["readyz"]["status"] == "ok"

    rc, out, err = _run(["--control-url", base, "connectors", "list"], capsys)
    assert rc == 0, err
    assert "count:" in out
