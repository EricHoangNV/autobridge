# ruff: noqa: TC003, ARG001, F401, I001, F541
"""Schemathesis OpenAPI contract tests for the control plane (P1.W7.9, plan §4 W7, DIRECTIVES §7).

Cases are generated from the live OpenAPI schema served by the FastAPI app
object — the same schema ``scripts/export_openapi.py`` snapshots to
``ui/openapi.json`` — so the contract under test can never drift from the
implementation. Every generated response must satisfy schemathesis' default
checks, in particular ``not_a_server_error``: schema-valid input may be
rejected with 4xx, but must never crash the API with a 5xx.

Determinism: ``Config(seed=...)`` pins case generation so CI is
reproducible; the seed is fixed, the cases still come from the live schema.

Fuzzing notes:
- ``GET /runs/{run_id}/events`` is an SSE endpoint, but its generator is
  finite (stored evidence rows, then a ``done`` marker) with 404 for unknown
  runs, so it is safe to fuzz.
- ``POST /demo/inject-fault`` and ``POST /demo/reset`` degrade to a labelled
  200 payload when the simulator admin is unreachable (connection-refused on
  localhost is instant), so they are safe to fuzz without the Compose stack.
- ``POST /connectors/{id}/build`` and ``.../sync`` queue real engine jobs
  against the isolated per-test SQLite DB via the fakes, as the existing
  control API tests do.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine

import schemathesis
from schemathesis import Config
from schemathesis.checks import allow_header_conformance

from autobridge.control.app import app
from autobridge.control.deps import get_db
from autobridge.control.models import (
    Base,
    session_factory,
)

# Live schema: built from the app object at import time, pinned seed for CI.
schema = schemathesis.openapi.from_asgi("/openapi.json", app, config=Config(seed=20261027))


@pytest.fixture(autouse=True)
def _isolated_control_db(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """Isolate each test on its own file SQLite DB (mirrors test_autobridge_control_api).

    Sets ``AUTOBRIDGE_CONTROL_TOKEN`` so the write routes enforce auth while
    fuzzing: schemathesis generates unauthenticated cases and expects the
    documented 401 (REQ-012). The 2xx paths are covered by the functional
    control API tests.
    """
    monkeypatch.setenv("AUTOBRIDGE_CONTROL_TOKEN", "contract-fuzz-token")
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
    yield
    app.dependency_overrides.clear()


@schema.parametrize()
def test_control_plane_contract(case):  # type: ignore[no-untyped-def]
    """Every generated case: no 5xx, and documented responses match the schema.

    Only ``allow_header_conformance`` is excluded: it probes the implicit
    OPTIONS method (absent from our OpenAPI and from plan §2.11) whose
    ``Allow`` header is computed by Starlette, not by our code.
    """
    response = case.call_and_validate(excluded_checks=[allow_header_conformance])
    assert response.status_code < 500, f"{case.operation.label} {case.operation.path} -> {response.status_code}"
