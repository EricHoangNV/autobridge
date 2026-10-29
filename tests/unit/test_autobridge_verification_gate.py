"""P1.W6.4 — Regression gate on every candidate release.

A release that breaks any previously verified scenario is blocked; the
plausible-but-wrong patch is rejected by the verifier.

Covers:
- verifier client parse logic (allowed vs blocked, invalid_patch_trap)
- HTTP success / error / unreachable handling
- regression_suite propagation
- create_release / create_release_from_patch with verifier_url gate
- gate skipped when verifier_url None (backward compat)
"""

from __future__ import annotations

import pathlib
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine

from autobridge.control.models import Connector, RepairRun, init_db, session_factory, spec_signature
from autobridge.engine.releases import ReleaseError, create_release, create_release_from_patch
from autobridge.engine.verification import (
    GateBlockedError,
    check_regression_gate,
    ensure_gate_passes,
    verify_spec,
)

# Save original httpx.Client before any monkeypatching (to avoid recursion)
_REAL_HTTPX_CLIENT = httpx.Client


def _session(tmp_path: pathlib.Path):  # type: ignore[no-untyped-def]
    engine = create_engine(f"sqlite:///{tmp_path}/gate.db")
    init_db(engine)
    return session_factory(engine)


def _minimal_spec(connector_id: str = "testconn", vendor: str = "Test") -> dict[str, Any]:
    return {
        "spec_version": 1,
        "connector": {"id": connector_id, "vendor": vendor, "runtime_target": "cloud"},
        "source": {
            "base_url": "https://example.com",
            "auth": {"type": "bearer", "credential_ref": "TEST_TOKEN"},
            "rate_limit": {"max_rps": 5, "retry": {"statuses": [429, 503], "backoff": "exponential", "max_attempts": 3}},
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
                "fields": {"reservation_id": {"path": "$.reservation_id"}},
            }
        },
        "capabilities": {"read": ["Reservation"]},
        "health": {"canary": {"every": "60s", "operation": "list_reservations"}, "fingerprint": {}},
    }


def _make_connector(session, **over: Any) -> Connector:
    kw: dict[str, Any] = {
        "name": "TestConn",
        "base_url": "https://example.com",
        "credential_ref": "TEST_TOKEN",
        "target_entities": ["Reservation"],
        "runtime_target": {"kind": "local_process"},
    }
    kw.update(over)
    c = Connector(**kw)
    session.add(c)
    session.commit()
    return c


def _repair_run(session) -> RepairRun:
    rr = RepairRun(drift_event_ids=[], decision="propose", model_usage={})
    session.add(rr)
    session.commit()
    return rr


def _mock_verifier(
    monkeypatch: pytest.MonkeyPatch,
    *,
    verdict: str = "pass",
    regression: str = "pass",
    failed_checks: list[dict[str, Any]] | None = None,
    regression_failures: list[dict[str, Any]] | None = None,
    scenario_id: str | None = None,
    response_mode: str | None = None,
    http_status: int = 200,
    raise_exc: Exception | None = None,
    capture_payload: dict[str, Any] | None = None,
):
    """Install httpx.Client mock that returns verifier response.

    Like the real verifier, the mock echoes the request's scenario_id/mode
    unless *scenario_id* / *response_mode* explicitly override the response
    (AR-1-5: overrides simulate a verdict bound to the wrong scenario).
    """
    fc = failed_checks or []
    if verdict == "fail" and not fc:
        fc = [{"name": "field_missing", "count": 1}]

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        if raise_exc is not None:
            raise raise_exc
        req_scenario: str | None = None
        req_mode: str | None = None
        if capture_payload is not None or scenario_id is None or response_mode is None:
            try:
                import json as _json

                req_body = _json.loads(request.content.decode())
                req_scenario = req_body.get("scenario_id")
                req_mode = req_body.get("mode")
                if capture_payload is not None:
                    capture_payload.update(req_body)
            except Exception:
                if capture_payload is not None:
                    capture_payload["raw"] = request.content.decode(errors="ignore")[:500]
        body = {
            "verdict": verdict,
            "score": 1.0 if verdict == "pass" else 0.5,
            "failed_checks": fc,
            "regression": regression,
            "scenario_id": scenario_id if scenario_id is not None else (req_scenario or "baseline"),
            "mode": response_mode if response_mode is not None else (req_mode or "visible"),
        }
        if regression_failures is not None:
            body["regression_failures"] = regression_failures
        # For regression fail we need to include failed scenario
        # Use caller-provided regression_failures

        if http_status != 200:
            return httpx.Response(http_status, json={"detail": "verifier error"})
        return httpx.Response(200, json=body)

    transport = httpx.MockTransport(handler)

    class FakeClient:
        def __init__(self, *a: Any, **kw: Any) -> None:  # noqa: ARG001, ARG002
            # Use real client bypassing monkeypatch
            self._client = _REAL_HTTPX_CLIENT(transport=transport)

        def __enter__(self) -> httpx.Client:
            return self._client

        def __exit__(self, *a: Any) -> None:  # noqa: ARG001, ARG002
            self._client.close()

        def post(self, *a: Any, **kw: Any) -> httpx.Response:
            return self._client.post(*a, **kw)

    # Patch httpx.Client to return our fake
    monkeypatch.setattr(httpx, "Client", FakeClient)


# ---------------------------------------------------------------------------
# Parser and verify_spec unit
# ---------------------------------------------------------------------------


def test_verify_spec_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_verifier(monkeypatch, verdict="pass", regression="pass")
    out = verify_spec(_minimal_spec(), "baseline", "http://verifier:8000")
    assert out.allowed is True
    assert out.verdict == "pass"
    assert out.regression == "pass"
    assert out.block_reason is None


def test_verify_spec_verdict_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_verifier(monkeypatch, verdict="fail", regression="pass", failed_checks=[{"name": "field_missing", "count": 1}])
    out = verify_spec(_minimal_spec(), "rename_guest_id", "http://verifier:8000")
    assert out.allowed is False
    assert out.verdict == "fail"
    assert "field_missing" in (out.block_reason or "")


def test_verify_spec_invalid_patch_trap(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_verifier(
        monkeypatch,
        verdict="fail",
        regression="pass",
        failed_checks=[{"name": "invalid_patch_trap", "count": 1}],
    )
    out = verify_spec(_minimal_spec(), "invalid_patch_trap", "http://verifier:8000")
    assert out.allowed is False
    assert "invalid_patch_trap" in (out.block_reason or "")
    assert "plausible-but-wrong" in (out.block_reason or "")


def test_verify_spec_regression_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_verifier(
        monkeypatch,
        verdict="pass",
        regression="fail",
        regression_failures=[{"scenario_id": "baseline", "failed_checks": [{"name": "field_missing", "count": 1}]}],
    )
    out = verify_spec(_minimal_spec(), "rename_guest_id", "http://verifier:8000", regression_suite=["baseline", "rename_guest_id"])
    assert out.allowed is False
    assert out.regression == "fail"
    assert "baseline" in (out.block_reason or "")
    assert out.regression_failures is not None
    assert out.regression_failures[0]["scenario_id"] == "baseline"


def test_verify_spec_regression_suite_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    capture: dict[str, Any] = {}
    _mock_verifier(monkeypatch, verdict="pass", regression="pass", capture_payload=capture)
    verify_spec(_minimal_spec(), "baseline", "http://verifier:8000", regression_suite=["baseline", "rename_guest_id"])
    assert capture.get("regression_suite") == ["baseline", "rename_guest_id"]
    assert capture.get("scenario_id") == "baseline"


def test_verify_spec_no_regression_suite_not_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    capture: dict[str, Any] = {}
    _mock_verifier(monkeypatch, capture_payload=capture)
    verify_spec(_minimal_spec(), "baseline", "http://verifier:8000")
    assert "regression_suite" not in capture


def test_verify_spec_verifier_url_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    # url already ending with /verify should not double
    _mock_verifier(monkeypatch)
    out = verify_spec(_minimal_spec(), "baseline", "http://verifier:8000/verify")
    assert out.allowed is True
    # trailing slash
    _mock_verifier(monkeypatch)
    out2 = verify_spec(_minimal_spec(), "baseline", "http://verifier:8000/")
    assert out2.allowed is True


def test_verify_spec_empty_url_raises() -> None:
    with pytest.raises(ValueError, match="verifier_url"):
        verify_spec(_minimal_spec(), "baseline", "")


def test_verify_spec_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_verifier(monkeypatch, http_status=500)
    with pytest.raises(GateBlockedError, match="HTTP 500"):
        verify_spec(_minimal_spec(), "baseline", "http://verifier:8000")


def test_verify_spec_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_verifier(monkeypatch, raise_exc=ConnectionError("refused"))
    with pytest.raises(GateBlockedError, match="verifier unreachable"):
        verify_spec(_minimal_spec(), "baseline", "http://verifier:8000")


def test_verify_spec_non_json(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(200, content=b"not json")

    transport = httpx.MockTransport(handler)

    class FakeClient:
        def __init__(self, *a: Any, **kw: Any) -> None:  # noqa: ARG001, ARG002
            self._client = _REAL_HTTPX_CLIENT(transport=transport)

        def __enter__(self) -> httpx.Client:
            return self._client

        def __exit__(self, *a: Any, **kw: Any) -> None:  # noqa: ARG001, ARG002
            self._client.close()

        def post(self, *a: Any, **kw: Any) -> httpx.Response:
            return self._client.post(*a, **kw)

    monkeypatch.setattr(httpx, "Client", FakeClient)
    with pytest.raises(GateBlockedError, match="non-JSON"):
        verify_spec(_minimal_spec(), "baseline", "http://verifier:8000")


# ---------------------------------------------------------------------------
# check_regression_gate / ensure_gate_passes
# ---------------------------------------------------------------------------


def test_check_gate_skip_when_no_url() -> None:
    allowed, outcome = check_regression_gate(_minimal_spec(), "baseline", None)
    assert allowed is True
    assert outcome is None
    allowed2, outcome2 = check_regression_gate(_minimal_spec(), "baseline", "")
    assert allowed2 is True
    assert outcome2 is None


def test_check_gate_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_verifier(monkeypatch, verdict="pass", regression="pass")
    allowed, outcome = check_regression_gate(_minimal_spec(), "baseline", "http://verifier:8000")
    assert allowed is True
    assert outcome is not None
    assert outcome.allowed is True


def test_check_gate_fail_regression(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_verifier(
        monkeypatch,
        verdict="pass",
        regression="fail",
        regression_failures=[{"scenario_id": "baseline", "failed_checks": [{"name": "field_missing", "count": 1}]}],
    )
    allowed, outcome = check_regression_gate(_minimal_spec(), "rename_guest_id", "http://verifier:8000", verified_scenarios=["baseline"])
    assert allowed is False
    assert outcome is not None
    assert "baseline" in (outcome.block_reason or "")


def test_ensure_gate_passes_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_verifier(monkeypatch, verdict="fail", failed_checks=[{"name": "field_missing", "count": 1}])
    with pytest.raises(GateBlockedError, match="verdict fail"):
        ensure_gate_passes(_minimal_spec(), "rename_guest_id", "http://verifier:8000")


def test_ensure_gate_passes_ok_returns_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_verifier(monkeypatch, verdict="pass", regression="pass")
    out = ensure_gate_passes(_minimal_spec(), "baseline", "http://verifier:8000")
    assert out is not None
    assert out.allowed is True


def test_ensure_gate_skipped_when_no_url() -> None:
    out = ensure_gate_passes(_minimal_spec(), "baseline", None)
    assert out is None


# ---------------------------------------------------------------------------
# Releases gate integration
# ---------------------------------------------------------------------------


def test_create_release_blocks_on_regression(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        _mock_verifier(
            monkeypatch,
            verdict="pass",
            regression="fail",
            regression_failures=[{"scenario_id": "baseline", "failed_checks": [{"name": "field_missing", "count": 1}]}],
            scenario_id="rename_guest_id",
        )
        # Baseline release exists (no gate needed for it)
        r1 = create_release(s, connector_id=conn.id, spec=_minimal_spec())
        s.commit()
        assert r1.version == 1
        # Candidate that breaks baseline should be blocked
        with pytest.raises(GateBlockedError, match="regression fail"):
            create_release(
                s,
                connector_id=conn.id,
                spec=_minimal_spec(),
                parent_release_id=r1.id,
                verifier_url="http://verifier:8000",
                scenario_id="rename_guest_id",
                verified_scenarios=["baseline", "rename_guest_id"],
            )
        # No new release was created — version still 1
        from autobridge.engine.releases import next_version

        assert next_version(s, conn.id) == 2  # next would be 2, meaning nothing inserted


def test_create_release_allows_when_pass(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        _mock_verifier(monkeypatch, verdict="pass", regression="pass")
        r1 = create_release(s, connector_id=conn.id, spec=_minimal_spec())
        s.commit()
        r2 = create_release(
            s,
            connector_id=conn.id,
            spec=_minimal_spec(),
            parent_release_id=r1.id,
            verifier_url="http://verifier:8000",
            scenario_id="rename_guest_id",
            verified_scenarios=["baseline"],
        )
        s.commit()
        assert r2.version == 2
        assert r2.parent_release_id == r1.id


def test_create_release_skips_gate_when_no_url(tmp_path: pathlib.Path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        # No verifier_url → allowed even though spec would otherwise be invalid? Minimal spec always passes verifier, but we test skip
        r1 = create_release(s, connector_id=conn.id, spec=_minimal_spec())
        s.commit()
        r2 = create_release(s, connector_id=conn.id, spec=_minimal_spec(), parent_release_id=r1.id)
        s.commit()
        assert r2.version == 2


def test_create_release_from_patch_blocks_on_invalid_patch_trap(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        base = create_release(s, connector_id=conn.id, spec=_minimal_spec())
        s.commit()
        # Mock verifier to reject trap (invalid_patch_trap)
        _mock_verifier(
            monkeypatch,
            verdict="fail",
            regression="pass",
            failed_checks=[{"name": "invalid_patch_trap", "count": 1}],
            scenario_id="invalid_patch_trap",
        )
        patch = [{"op": "replace", "path": "/source/rate_limit/max_rps", "value": 10}]
        with pytest.raises(GateBlockedError, match="invalid_patch_trap"):
            create_release_from_patch(
                s,
                connector_id=conn.id,
                base_release_id=base.id,
                patch=patch,
                verifier_url="http://verifier:8000",
                scenario_id="invalid_patch_trap",
                verified_scenarios=["baseline"],
            )
        # No release created
        from autobridge.engine.releases import next_version

        assert next_version(s, conn.id) == 2


def test_create_release_from_patch_blocks_on_regression(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        base = create_release(s, connector_id=conn.id, spec=_minimal_spec())
        s.commit()
        _mock_verifier(
            monkeypatch,
            verdict="pass",
            regression="fail",
            regression_failures=[{"scenario_id": "baseline", "failed_checks": [{"name": "field_missing", "count": 1}]}],
        )
        patch = [{"op": "replace", "path": "/source/rate_limit/max_rps", "value": 10}]
        with pytest.raises(GateBlockedError, match="regression fail"):
            create_release_from_patch(
                s,
                connector_id=conn.id,
                base_release_id=base.id,
                patch=patch,
                verifier_url="http://verifier:8000",
                scenario_id="offset_to_cursor",
                verified_scenarios=["baseline", "offset_to_cursor"],
            )


def test_create_release_from_patch_allows_pass(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        base = create_release(s, connector_id=conn.id, spec=_minimal_spec())
        s.commit()
        _mock_verifier(monkeypatch, verdict="pass", regression="pass")
        patch = [{"op": "replace", "path": "/source/rate_limit/max_rps", "value": 10}]
        r2 = create_release_from_patch(
            s,
            connector_id=conn.id,
            base_release_id=base.id,
            patch=patch,
            verifier_url="http://verifier:8000",
            scenario_id="rename_guest_id",
        )
        s.commit()
        assert r2.version == 2


def test_create_release_from_patch_skips_when_no_url(tmp_path: pathlib.Path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        base = create_release(s, connector_id=conn.id, spec=_minimal_spec())
        s.commit()
        patch = [{"op": "replace", "path": "/source/rate_limit/max_rps", "value": 10}]
        r2 = create_release_from_patch(s, connector_id=conn.id, base_release_id=base.id, patch=patch)
        s.commit()
        assert r2.version == 2


def test_create_release_verifier_error_propagates_as_gate_blocked(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        _mock_verifier(monkeypatch, http_status=500)
        with pytest.raises(GateBlockedError, match="HTTP 500"):
            create_release(
                s,
                connector_id=conn.id,
                spec=_minimal_spec(),
                verifier_url="http://verifier:8000",
                scenario_id="baseline",
            )


def test_plausible_but_wrong_patch_rejected_via_real_verifier() -> None:
    """Real verifier (no mock) must reject the gross_to_net trap as invalid_patch_trap.

    This proves the trap set is wired: a spec that maps net_amount to total_amount
    is rejected for invalid_patch_trap scenario.
    """
    from verifier.invalid_patches import apply_trap_to_spec
    from verifier.oracle import oracle_verdict

    # Use a harbor-like spec that has total_amount field
    base_spec = {
        "spec_version": 1,
        "connector": {"id": "test", "vendor": "Test", "runtime_target": "cloud"},
        "source": {
            "base_url": "https://example.com",
            "auth": {"type": "bearer", "credential_ref": "TEST_TOKEN"},
            "timezone_source": {"kind": "static", "value": "UTC"},
        },
        "operations": {
            "list_folios": {
                "method": "GET",
                "path": "/folios",
                "params": {},
                "pagination": {"strategy": "cursor", "cursor_param": "cursor", "next_path": "$.meta.next_cursor", "items_path": "$.data", "stop_when": ["null_cursor"]},
            }
        },
        "entities": {
            "FolioCharge": {
                "from": "list_folios",
                "key": "$.charge_id",
                "fields": {
                    "charge_id": {"path": "$.charge_id"},
                    "amount": {"path": "$.amount", "transform": [{"name": "money", "args": {"currency": "USD"}}]},
                    "total_amount": {"path": "$.total_amount", "transform": [{"name": "money", "args": {"currency": "USD"}}]},
                },
            }
        },
        "capabilities": {"read": ["FolioCharge"]},
        "health": {"canary": {"every": "60s", "operation": "list_folios"}, "fingerprint": {}},
    }
    trapped = apply_trap_to_spec(base_spec, "gross_to_net_net_to_total_amount")
    assert trapped["entities"]["FolioCharge"]["fields"]["total_amount"]["path"] == "$.net_amount"
    verdict, _, failed = oracle_verdict("invalid_patch_trap", trapped)
    assert verdict == "fail", f"expected fail but got pass with {failed}"
    assert any(f["name"] == "invalid_patch_trap" for f in failed)


def test_regression_blocks_breaking_baseline_via_real_verifier() -> None:
    """Real verifier: candidate that would break baseline should be flagged via regression.

    We create a spec that is valid for baseline but then mutate it to break baseline
    (e.g., change pagination strategy from cursor to offset for offset_to_cursor scenario).
    When we verify that candidate against offset_to_cursor with regression_suite=[baseline],
    the regression check already happens inside the verifier for baseline-only case.
    We test that a spec with wrong pagination for offset_to_cursor is rejected (pagination_shape)
    and therefore would be blocked.
    """
    from verifier.oracle import oracle_verdict

    # Minimal valid spec for offset_to_cursor scenario that has wrong pagination (offset)
    wrong_spec = {
        "spec_version": 1,
        "connector": {"id": "test", "vendor": "Test", "runtime_target": "cloud"},
        "source": {
            "base_url": "https://example.com",
            "auth": {"type": "bearer", "credential_ref": "TEST_TOKEN"},
            "timezone_source": {"kind": "static", "value": "UTC"},
        },
        "operations": {
            "list_reservations": {
                "method": "GET",
                "path": "/reservations",
                "params": {},
                "pagination": {"strategy": "offset", "offset_param": "offset", "limit_param": "limit", "items_path": "$.data", "stop_when": ["empty_page"]},
            }
        },
        "entities": {
            "Reservation": {
                "from": "list_reservations",
                "key": "$.reservation_id",
                "fields": {"reservation_id": {"path": "$.reservation_id"}},
            }
        },
        "capabilities": {"read": ["Reservation"]},
        "health": {"canary": {"every": "60s", "operation": "list_reservations"}, "fingerprint": {}},
    }
    verdict, _, failed = oracle_verdict("offset_to_cursor", wrong_spec)
    assert verdict == "fail"
    assert any(f["name"] == "pagination_shape" for f in failed)


def test_verification_module_no_verifier_import() -> None:
    """Engine verification client never imports verifier/simulators."""
    src = pathlib.Path("src/autobridge/engine/verification.py").read_text()
    assert "import verifier" not in src
    assert "from verifier" not in src
    assert "import simulators" not in src
    assert "from simulators" not in src


def test_verify_spec_mode_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    capture: dict[str, Any] = {}
    _mock_verifier(monkeypatch, capture_payload=capture)
    out = verify_spec(_minimal_spec(), "baseline", "http://verifier:8000", mode="hidden")
    assert capture.get("mode") == "hidden"
    # The mock echoes the requested mode, so a hidden request binds cleanly
    assert out.allowed is True
    assert out.mode == "hidden"


# ---------------------------------------------------------------------------
# AR-1-5 — verdict bound to the requested scenario/mode; fail closed
# ---------------------------------------------------------------------------


def test_verify_spec_mismatched_scenario_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pass verdict for a different scenario must not count as allowed."""
    _mock_verifier(monkeypatch, verdict="pass", regression="pass", scenario_id="baseline")
    with pytest.raises(GateBlockedError, match="scenario mismatch"):
        verify_spec(_minimal_spec(), "rename_guest_id", "http://verifier:8000")


def test_verify_spec_mismatched_mode_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pass verdict in the wrong mode must not count as allowed."""
    _mock_verifier(monkeypatch, verdict="pass", regression="pass", response_mode="visible")
    with pytest.raises(GateBlockedError, match="mode mismatch"):
        verify_spec(_minimal_spec(), "baseline", "http://verifier:8000", mode="hidden")


def test_verify_spec_records_spec_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    """The outcome carries the signature of exactly the spec verified."""
    _mock_verifier(monkeypatch, verdict="pass", regression="pass")
    spec = _minimal_spec()
    out = verify_spec(spec, "baseline", "http://verifier:8000")
    assert out.allowed is True
    assert out.spec_signature == spec_signature(spec)
    assert out.scenario_id == "baseline"
    assert out.mode == "visible"


def test_create_release_blocks_on_scenario_mismatch(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """Gate-before-persist refuses a response bound to the wrong scenario."""
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        _mock_verifier(monkeypatch, verdict="pass", regression="pass", scenario_id="baseline")
        with pytest.raises(GateBlockedError, match="scenario mismatch"):
            create_release(
                s,
                connector_id=conn.id,
                spec=_minimal_spec(),
                verifier_url="http://verifier:8000",
                scenario_id="rename_guest_id",
            )
        from autobridge.engine.releases import next_version

        assert next_version(s, conn.id) == 1  # nothing persisted


def test_maybe_verify_import_failure_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """If the gate module cannot be imported, creation raises instead of skipping."""
    import sys

    monkeypatch.setitem(sys.modules, "autobridge.engine.verification", None)
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        with pytest.raises(ReleaseError, match="gate unavailable"):
            create_release(
                s,
                connector_id=conn.id,
                spec=_minimal_spec(),
                verifier_url="http://verifier:8000",
                scenario_id="baseline",
            )


# ---------------------------------------------------------------------------
# REQ-029: worker gate via control proxy + fail-closed (AR-1-2 residual)
# ---------------------------------------------------------------------------


def test_verify_via_control_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """The proxy verdict parses like a direct one (scenario/mode bound)."""
    from autobridge.engine.verification import verify_via_control

    _mock_verifier(monkeypatch, verdict="pass", regression="pass")
    out = verify_via_control(_minimal_spec(), "rename_guest_id", "http://control:8000")
    assert out.allowed is True
    assert out.verdict == "pass"
    assert out.scenario_id == "rename_guest_id"


def test_verify_via_control_fail_verdict_returned_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fail verdict returns allowed=False (ensure_* raises, mirroring direct)."""
    from autobridge.engine.verification import ensure_control_gate_passes, verify_via_control

    _mock_verifier(monkeypatch, verdict="fail", regression="pass")
    out = verify_via_control(_minimal_spec(), "baseline", "http://control:8000")
    assert out.allowed is False
    assert "field_missing" in (out.block_reason or "")
    with pytest.raises(GateBlockedError, match="field_missing"):
        ensure_control_gate_passes(_minimal_spec(), "baseline", "http://control:8000")


def test_verify_via_control_scenario_mismatch_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A proxy verdict bound to another scenario never counts."""
    from autobridge.engine.verification import verify_via_control

    _mock_verifier(monkeypatch, verdict="pass", regression="pass", scenario_id="baseline")
    with pytest.raises(GateBlockedError, match="scenario mismatch"):
        verify_via_control(_minimal_spec(), "rename_guest_id", "http://control:8000")


def test_verify_via_control_unreachable_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unreachable proxy raises (the worker persists nothing)."""
    from autobridge.engine.verification import verify_via_control

    _mock_verifier(monkeypatch, raise_exc=httpx.ConnectError("refused"))
    with pytest.raises(GateBlockedError, match="proxy unreachable"):
        verify_via_control(_minimal_spec(), "baseline", "http://control:8000")


def test_verify_via_control_sends_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """The proxy call presents AUTOBRIDGE_CONTROL_TOKEN when set."""
    import json as _json

    from autobridge.engine.verification import verify_via_control

    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        body = _json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "verdict": "pass",
                "score": 1.0,
                "failed_checks": [],
                "regression": "pass",
                "scenario_id": body.get("scenario_id", "baseline"),
                "mode": body.get("mode", "visible"),
            },
        )

    transport = httpx.MockTransport(handler)

    class FakeClient:
        def __init__(self, *a: Any, **kw: Any) -> None:  # noqa: ARG002
            self._client = _REAL_HTTPX_CLIENT(transport=transport)

        def __enter__(self) -> httpx.Client:
            return self._client

        def __exit__(self, *a: Any) -> None:
            self._client.close()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    out = verify_via_control(_minimal_spec(), "baseline", "http://control:8000", token="tok-123")
    assert out.allowed is True
    assert seen["auth"] == "Bearer tok-123"


def test_create_release_via_proxy_mints_report_id(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """Worker path (no VERIFIER_URL, CONTROL_URL set): gate passes, report id stored."""
    monkeypatch.delenv("VERIFIER_URL", raising=False)
    monkeypatch.setenv("CONTROL_URL", "http://control:8000")
    monkeypatch.delenv("AUTOBRIDGE_CONTROL_TOKEN", raising=False)
    _mock_verifier(monkeypatch, verdict="pass", regression="pass")
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        rel = create_release(s, connector_id=conn.id, spec=_minimal_spec())
        s.commit()
        assert rel.verification_report_id


def test_create_release_require_gate_no_verifier_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """Fail-closed: require-gate set, nothing reachable → GateBlockedError, nothing persists."""
    monkeypatch.delenv("VERIFIER_URL", raising=False)
    monkeypatch.delenv("CONTROL_URL", raising=False)
    monkeypatch.setenv("AUTOBRIDGE_REQUIRE_VERIFIER_GATE", "1")
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        with pytest.raises(GateBlockedError, match="no verifier reachable"):
            create_release(s, connector_id=conn.id, spec=_minimal_spec())
        from autobridge.engine.releases import next_version

        assert next_version(s, conn.id) == 1  # nothing persisted


def test_create_release_skips_without_verifier_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """Backward compat: without the require-gate flag, no verifier still skips."""
    monkeypatch.delenv("VERIFIER_URL", raising=False)
    monkeypatch.delenv("CONTROL_URL", raising=False)
    monkeypatch.delenv("AUTOBRIDGE_REQUIRE_VERIFIER_GATE", raising=False)
    factory = _session(tmp_path)
    with factory() as s:
        conn = _make_connector(s)
        rel = create_release(s, connector_id=conn.id, spec=_minimal_spec())
        s.commit()
        assert rel.verification_report_id is None
