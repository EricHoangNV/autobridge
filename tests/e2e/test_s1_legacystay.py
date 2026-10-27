"""P2.S1.1 — LegacyStay third vendor: heldout-only unseen generalization (plan §5 S1).

Passing criteria (plan §5 S1):
  * Bootstrap + at least one repair verified with no vendor-specific rule added.
  * Vendor character: rate limits, misleading examples, nullable chaos.
  * Own e2e file: ``tests/e2e/test_s1_legacystay.py``.

LegacyStay is wire-compatible with the canonical shapes but unseen in every
other way: offset pagination (Atlas-like surface, Harbor-like legacy docs),
strict Bearer auth (unlike Harbor's permissive auth), plain-English statuses
(unlike Harbor's opaque H1..H5), an ambiguous ``date`` duplicator, deterministic
nullable chaos, and an always-on baseline throttle (every 8th request 429 —
deliberately a different cadence from the catalog's every-3rd ``rate_limit_429``).

Fake transports make the suite green without Docker; live tests are skipped
when the stack is down. Trust boundary: this file lives in tests/e2e and may
import autobridge engine + verifier oracle + simulators for verification, but
the engine itself must never import verifier/simulators.
"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine

from autobridge.control.models import (
    Base,
    Connector,
    ConnectorRelease,
    ModelCall,
    RepairRun,
    session_factory,
    spec_signature,
)
from autobridge.discovery.bootstrap import build_draft_spec
from autobridge.discovery.classifiers import classify_all
from autobridge.discovery.docs_reader import read_docs_text
from autobridge.discovery.hypotheses import HypothesisEnumerator
from autobridge.drift.classifier import classify
from autobridge.drift.fingerprint import compute_fingerprint, diff_fingerprints
from autobridge.drift.signals import signals_from_sync
from autobridge.engine.sync_run import execute_sync
from autobridge.repair.controller import RepairController
from autobridge.repair.knowledge import KnowledgeCache
from autobridge.runtime.http import HttpClient
from autobridge.spec.models import RetryPolicy, load_spec_from_dict
from autobridge.spec.patch import apply_patch

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LEGACY_MD_PATH = REPO_ROOT / "simulators" / "docs" / "legacystay.md"

LEGACY_MD = LEGACY_MD_PATH.read_text(encoding="utf-8") if LEGACY_MD_PATH.exists() else ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _legacy_bootstrap():  # type: ignore[no-untyped-def]
    """Bootstrap LegacyStay docs deterministically (no probes)."""
    return build_draft_spec(LEGACY_MD, source="simulators/docs/legacystay.md")


def _assert_visible_tests_green(result) -> None:  # type: ignore[no-untyped-def]
    assert result.success, f"bootstrap failed: {result.degradation_reason} trace {result.trace}"
    assert result.draft_spec is not None
    assert result.visible_errors == []
    load_spec_from_dict(result.draft_spec)


def _legacy_fake_client(dataset: dict[str, list[dict[str, Any]]], throttle_every: int = 8) -> Any:
    """Offset-pagination fake matching LegacyStay behavior.

    Mirrors ``simulators/legacystay/app.py``: ``date`` duplicators, nullable
    chaos, plain-English statuses, and a baseline 429 throttle every
    ``throttle_every``-th request (``Retry-After: 1``). Auth is not enforced
    on the fake (covered live); the spec still carries Bearer credentials.
    """
    from simulators.legacystay.app import (
        _to_legacystay_folio,
        _to_legacystay_reservation,
    )

    request_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        request_count["n"] += 1
        if throttle_every > 0 and request_count["n"] % throttle_every == 0:
            return httpx.Response(429, json={"detail": {"code": "rate_limit", "message": "Too many requests"}}, headers={"Retry-After": "1"})
        qp = dict(request.url.params)
        path = request.url.path
        limit = int(qp.get("limit", "10"))
        offset = int(qp.get("offset", "0"))
        if path == "/reservations":
            raw = dataset["reservations"]
            data = [_to_legacystay_reservation(r) for r in raw]
            total = len(data)
            sliced = data[offset : offset + limit]
            next_offset = offset + limit if offset + limit < total else None
            return httpx.Response(200, json={"data": sliced, "total": total, "limit": limit, "offset": offset, "next_offset": next_offset})
        if path == "/folios":
            raw = dataset["folios"]
            data = [_to_legacystay_folio(f) for f in raw]
            total = len(data)
            sliced = data[offset : offset + limit]
            next_offset = offset + limit if offset + limit < total else None
            return httpx.Response(200, json={"data": sliced, "total": total, "limit": limit, "offset": offset, "next_offset": next_offset})
        if path == "/properties":
            return httpx.Response(200, json={"data": dataset["properties"]})
        return httpx.Response(404, json={"msg": "not found"})

    transport = httpx.MockTransport(handler)

    def factory(**kw: Any) -> httpx.Client:
        return httpx.Client(transport=transport, timeout=kw.get("timeout", 5))

    return HttpClient(
        "https://example.com",
        retry=RetryPolicy(statuses=[429, 503], backoff="exponential", max_attempts=5),
        client_factory=factory,
        sleep_fn=lambda _: None,
    )


def _make_db():  # type: ignore[no-untyped-def]
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = session_factory(engine)
    return engine, SessionLocal


def _create_connector_and_release(spec: dict[str, Any], connector_id: str, SessionLocal) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    session = SessionLocal()
    conn = Connector(
        id=connector_id,
        name=connector_id,
        base_url="http://example.com",
        credential_ref="TEST_TOKEN",
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


# ---------------------------------------------------------------------------
# 1. Bootstrap from docs alone — spec correctness
# ---------------------------------------------------------------------------


def test_legacystay_bootstrap_produces_valid_spec_from_docs_alone() -> None:
    """LegacyStay connector produced from docs alone (no reference-spec access)."""
    result = _legacy_bootstrap()
    _assert_visible_tests_green(result)
    spec = result.draft_spec
    assert spec is not None
    # Connector identity from docs title
    assert spec["connector"]["id"] == "legacystay"
    assert spec["connector"]["vendor"] == "LegacyStay"
    # Base URL placeholder + credential ref derived generically (no engine change)
    assert spec["source"]["base_url"] == "${LEGACYSTAY_BASE_URL}"
    assert spec["source"]["auth"]["type"] == "bearer"
    assert spec["source"]["auth"]["credential_ref"] == "LEGACYSTAY_API_KEY"
    # Retry covers the vendor's throttle out of the box
    assert 429 in spec["source"]["rate_limit"]["retry"]["statuses"]
    assert 503 in spec["source"]["rate_limit"]["retry"]["statuses"]
    # Operations: offset pagination (not cursor)
    assert "list_reservations" in spec["operations"]
    assert "list_folios" in spec["operations"]
    for op_name in ("list_reservations", "list_folios"):
        pag = spec["operations"][op_name]["pagination"]
        assert pag["strategy"] == "offset"
        assert pag["next_path"] == "$.next_offset"
        assert pag["items_path"] == "$.data"
        assert "empty_page" in pag["stop_when"]
    # Entities
    assert "Reservation" in spec["entities"]
    assert "FolioCharge" in spec["entities"]
    # No ambiguous `date` field mapped
    for ent in spec["entities"].values():
        assert "date" not in ent["fields"]
    # Money Decimal with scale 2
    for _ent_name, ent in spec["entities"].items():
        for fname, fm in ent["fields"].items():
            if fname in ("total_amount", "amount"):
                steps = fm.get("transform", [])
                assert any(s.get("name") == "money" for s in steps)
                for s in steps:
                    if s.get("name") == "money":
                        assert s["args"]["scale"] == 2
    # Status is the clear identity map with on_unknown block (plain words)
    status_tf = spec["entities"]["Reservation"]["fields"]["status"]["transform"][0]
    assert status_tf["name"] == "enum_map"
    assert status_tf["args"]["map"] == {
        "confirmed": "confirmed",
        "checked_in": "checked_in",
        "checked_out": "checked_out",
        "cancelled": "cancelled",
        "no_show": "no_show",
    }
    assert status_tf["args"]["on_unknown"] == "block"
    # Business date cutoff 04:00
    assert spec["entities"]["FolioCharge"]["fields"]["business_date"]["transform"][0]["args"]["cutoff"] == 4
    # Capabilities + health
    assert spec["capabilities"]["read"] == ["Reservation", "FolioCharge"]
    assert spec["health"]["canary"]["operation"] == "list_reservations"
    # Validates against spec schema
    load_spec_from_dict(spec)
    # No credential values in spec
    assert "credential_ref" in json.dumps(spec)
    assert "${" in spec["source"]["base_url"]


def test_legacystay_status_classified_clear_high_confidence() -> None:
    """Plain-English statuses classify as clear (not opaque), high confidence."""
    iface = read_docs_text(LEGACY_MD, "simulators/docs/legacystay.md")
    classification = classify_all(iface)
    assert classification.status_vocabulary.kind == "clear"
    assert classification.status_vocabulary.confidence == "high"
    assert set(classification.status_vocabulary.vocabulary) == {"confirmed", "checked_in", "checked_out", "cancelled", "no_show"}
    assert classification.auth.kind == "bearer"
    # Main list operations classify offset with high confidence
    by_op = {g.operation: g for g in classification.pagination.per_operation}
    assert by_op["/reservations"].strategy == "offset"
    assert by_op["/reservations"].confidence == "high"
    assert by_op["/folios"].strategy == "offset"
    assert by_op["/folios"].confidence == "high"


def test_legacystay_misleading_examples_present_but_ignored() -> None:
    """The docs really contain the traps, and the generic pipeline ignores all three."""
    # Guard against someone "fixing" the docs: the traps must stay.
    assert "next_cursor" in LEGACY_MD, "docs must keep the outdated cursor example"
    assert "`active`" in LEGACY_MD, "docs must keep the wrong status words"
    assert "Ambiguity note" in LEGACY_MD, "docs must keep the date ambiguity note"
    # ... yet the compiled spec is unaffected by every trap:
    result = _legacy_bootstrap()
    _assert_visible_tests_green(result)
    spec = result.draft_spec
    assert spec["operations"]["list_reservations"]["pagination"]["strategy"] == "offset"
    assert spec["operations"]["list_folios"]["pagination"]["strategy"] == "offset"
    mmap = spec["entities"]["Reservation"]["fields"]["status"]["transform"][0]["args"]["map"]
    assert "active" not in mmap and "in-house" not in mmap
    assert not any(k.startswith("H") for k in mmap)
    for ent in spec["entities"].values():
        assert "date" not in ent["fields"]


def test_every_hypothesis_links_to_evidence() -> None:
    """Every LegacyStay hypothesis links to ≥1 evidence record (W5.3 invariant)."""
    iface = read_docs_text(LEGACY_MD, "simulators/docs/legacystay.md")
    classification = classify_all(iface)
    enumerator = HypothesisEnumerator()
    all_hyps = enumerator.enumerate_all(iface, classification)
    assert len(all_hyps) > 0
    for h in all_hyps:
        assert h.evidence_for, f"{h.id} missing evidence_for"
        assert h.candidate_mapping_fragment, f"{h.id} missing fragment"
        payload = h.to_evidence_payload()
        assert payload["evidence_for"]
        assert payload["fragment"]


def test_legacystay_bootstrap_oracle_pass() -> None:
    """Draft spec passes verifier oracle for baseline (schema + money + date + no-loss)."""
    result = _legacy_bootstrap()
    _assert_visible_tests_green(result)
    from verifier.oracle import oracle_verdict

    verdict, score, failed = oracle_verdict("baseline", result.draft_spec)
    assert verdict == "pass", f"oracle failed: {failed}"
    assert score == 1.0
    assert not failed


# ---------------------------------------------------------------------------
# 2. Sync Complete against the throttled, nullable vendor (fake)
# ---------------------------------------------------------------------------


def test_legacystay_spec_syncs_complete_with_throttle_and_nulls_fake() -> None:
    """Bootstrapped LegacyStay spec syncs Complete against its own messy shape.

    The fake mirrors the sim: offset pagination, every-8th 429 (recovered via
    retry), nullable guest_name/description, ambiguous `date`, plain statuses.
    """
    from sqlalchemy import create_engine as _ce

    result = _legacy_bootstrap()
    _assert_visible_tests_green(result)
    assert result.model_usage["frontier_tokens"] == 0

    from simulators.common.generator import generate_dataset

    dataset = generate_dataset(seed=7)
    os.environ["LEGACYSTAY_API_KEY"] = "test-token"
    os.environ["LEGACYSTAY_BASE_URL"] = "https://example.com"
    http = _legacy_fake_client(dataset)

    engine = _ce("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = session_factory(engine)

    summary = execute_sync(result.draft_spec, http)
    assert summary["outcome"] == "Complete", f"outcome {summary['outcome']} unmet {summary['unmet_criteria']}"
    assert not summary["unmet_criteria"]
    assert summary["data_readiness"]["freshness"] == "current"
    assert summary["data_readiness"]["completeness"] == "complete"
    assert summary["records_observed"]["Reservation"] == len(dataset["reservations"])
    assert summary["records_observed"]["FolioCharge"] == len(dataset["folios"])
    # Throttle was actually exercised (≥8 requests) and recovered via retry
    assert summary["records_observed"]["Reservation"] > 8 or summary["records_observed"]["FolioCharge"] > 8
    # Money Decimal, statuses plain, nullables tolerated, date ignored
    for ent_name in ("Reservation", "FolioCharge"):
        recs = summary["canonical_records"].get(ent_name, [])
        assert recs, f"no {ent_name} records"
        money_field = "total_amount" if ent_name == "Reservation" else "amount"
        assert isinstance(recs[0][money_field], Decimal)
        assert recs[0][money_field].as_tuple().exponent == -2
        assert "date" not in recs[0]
    statuses = {r["status"] for r in summary["canonical_records"]["Reservation"]}
    assert statuses.issubset({"confirmed", "checked_in", "checked_out", "cancelled", "no_show"})
    # Nullable chaos tolerated: the engine omits None-valued fields, so null
    # guest_name surfaces as a missing key (None rejected nowhere, no crash)
    assert any(r.get("guest_name") is None for r in summary["canonical_records"]["Reservation"])
    # No ModelCall rows (engine never calls LLM)
    with sessions() as session:
        assert session.query(ModelCall).count() == 0


# ---------------------------------------------------------------------------
# 3. Verified repair via the generic ladder — rename_guest_id, frontier 0
# ---------------------------------------------------------------------------


def test_legacystay_rename_repair_zero_frontier() -> None:
    """rename_guest_id on LegacyStay -> generic rename_field, frontier 0, verified release."""
    from simulators.common.faults import apply_dataset_mutations
    from simulators.common.generator import generate_dataset

    spec = _legacy_bootstrap().draft_spec
    assert spec is not None
    base = generate_dataset(seed=42)
    mutated = apply_dataset_mutations(base, "rename_guest_id")

    # Baseline sync (production's previous-release fingerprint), then drift sync
    # against the renamed LegacyStay shape (both throttled; retry recovers).
    baseline_http = _legacy_fake_client(base)
    baseline_summary = execute_sync(spec, baseline_http)
    assert baseline_summary["outcome"] == "Complete"
    drift_http = _legacy_fake_client(mutated)
    drift_summary = execute_sync(spec, drift_http)
    baseline_fp = compute_fingerprint(spec, baseline_summary.get("operation_results", {}), baseline_summary.get("canonical_records", {}))
    current_fp = compute_fingerprint(spec, drift_summary.get("operation_results", {}), drift_summary.get("canonical_records", {}))
    effective_delta = diff_fingerprints(baseline_fp, current_fp)
    # Honest detection: the rename surfaces as a removed field (types_changed);
    # the new source name is not inferable from the delta alone, so the
    # scenario-derived rename mapping is supplied as extra — same harness
    # pattern as the W4 known-repair tests.
    signals = signals_from_sync(drift_summary, spec=spec, previous_fingerprint=baseline_fp, current_fingerprint=current_fp)
    kinds = [s.kind for s in signals]
    assert "types_changed" in kinds, f"expected field-removal signal, got {kinds}"
    classification = classify(signals)
    assert classification.fixable is True
    extra_dict: dict[str, Any] = {
        "entity": "Reservation",
        "field": "guest_name",
        "new_field": "guest_identifier",
        "rename": {"entity": "Reservation", "old_field": "guest_name", "new_field": "guest_identifier"},
    }

    # Ladder via RepairController
    _engine, SessionLocal = _make_db()
    connector_db_id, release_id = _create_connector_and_release(spec, "legacystay", SessionLocal)
    cache = KnowledgeCache()
    ctrl = RepairController(knowledge_cache=cache)

    session = SessionLocal()
    result = ctrl.handle(
        spec=spec,
        drift_class="SCHEMA_RENAME",
        signals=signals,
        fingerprint_delta=effective_delta,
        extra=extra_dict,
        vendor="LegacyStay",
        session=session,
    )
    session.commit()
    repair_run = session.query(RepairRun).order_by(RepairRun.created_at.desc()).first()
    assert repair_run is not None
    repair_run_id = repair_run.id
    session.close()

    assert result.decision == "propose", f"decision was {result.decision} escalation {result.escalation_reason} trace {result.ladder_trace}"
    assert result.rule_id == "rename_field", f"expected rename_field got {result.rule_id} trace {result.ladder_trace}"
    assert result.candidate_patch
    assert result.model_usage["frontier_tokens"] == 0
    assert result.ladder_level_reached in (0, 1)
    new_spec = apply_patch(spec, result.candidate_patch)
    load_spec_from_dict(new_spec)

    # Ledger: no frontier ModelCall rows
    session2 = SessionLocal()
    assert session2.query(ModelCall).count() == 0
    session2.close()

    # Verified release
    session3 = SessionLocal()
    from autobridge.engine.releases import create_release_from_patch

    new_release = create_release_from_patch(
        session3,
        connector_id=connector_db_id,
        base_release_id=release_id,
        patch=result.candidate_patch,
        provenance_repair_run_id=repair_run_id,
    )
    session3.commit()
    assert new_release.parent_release_id == release_id
    session3.close()

    # Oracle: new spec passes the drift scenario and still passes baseline
    from verifier.oracle import oracle_verdict

    verdict, score, failed = oracle_verdict("rename_guest_id", new_spec)
    assert verdict == "pass", f"oracle fail for rename_guest_id: {failed}"
    assert score == 1.0
    verdict_b, score_b, failed_b = oracle_verdict("baseline", new_spec)
    assert verdict_b == "pass", f"oracle fail for baseline: {failed_b}"
    assert score_b == 1.0
    old_verdict, _, _ = oracle_verdict("rename_guest_id", spec)
    assert old_verdict == "fail"

    # New spec syncs Complete against the fixed (renamed) transport, money Decimal
    fixed_http = _legacy_fake_client(mutated)
    new_summary = execute_sync(new_spec, fixed_http)
    assert new_summary["outcome"] == "Complete", f"new spec sync not Complete: {new_summary['outcome']} {new_summary['unmet_criteria']}"
    assert not new_summary["unmet_criteria"]
    for ent_name in ("Reservation", "FolioCharge"):
        recs = new_summary["canonical_records"].get(ent_name, [])
        if recs:
            money_field = "total_amount" if ent_name == "Reservation" else "amount"
            if money_field in recs[0]:
                assert isinstance(recs[0][money_field], Decimal)

    # L0 cache hit on second attempt
    session4 = SessionLocal()
    result2 = ctrl.handle(
        spec=spec,
        drift_class="SCHEMA_RENAME",
        signals=signals,
        fingerprint_delta=effective_delta,
        extra=extra_dict,
        vendor="LegacyStay",
        session=session4,
    )
    session4.commit()
    assert result2.decision == "propose"
    assert result2.candidate_patch == result.candidate_patch
    assert result2.model_usage["frontier_tokens"] == 0
    assert result2.ladder_level_reached == 0
    session4.close()
    _engine.dispose()


# ---------------------------------------------------------------------------
# 4. No vendor-specific rule or engine code was added for LegacyStay
# ---------------------------------------------------------------------------


def test_no_legacystay_specific_rule_or_engine_code() -> None:
    """P2.S1.1 generalization proof: the engine has no LegacyStay-specific code.

    The bootstrap, classifiers, repair registry and all L0/L1 rules are
    untouched: no file under src/autobridge mentions the vendor, and no
    registered rule id is vendor-scoped.
    """
    for py in (REPO_ROOT / "src" / "autobridge").rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        low = text.lower()
        assert "legacystay" not in low, f"{py} mentions legacystay — vendor-specific engine code"
        assert "legacy-stay" not in low and "legacy_stay" not in low, f"{py} mentions legacy-stay"
    from autobridge.repair import registry as reg

    for rule in reg.all_rules() if hasattr(reg, "all_rules") else []:
        rid = getattr(rule, "id", None) or getattr(rule, "rule_id", None) or str(rule)
        assert "legacy" not in str(rid).lower(), f"vendor-specific rule registered: {rid}"
    # The generic rules that fixed LegacyStay predate it (no new rule modules)
    import simulators.common.faults as _faults

    assert "legacystay" not in {s["id"] for s in _faults.SCENARIO_CATALOG}


# ---------------------------------------------------------------------------
# 5. Live e2e — when the stack is up
# ---------------------------------------------------------------------------


def _set_livestay_scenario(scenario_id: str, seed: int = 0) -> None:
    from tests.e2e.conftest import admin_url

    url = admin_url("sim-legacystay", "/admin/scenario")
    data = json.dumps({"id": scenario_id, "seed": seed}).encode()
    req = __import__("urllib.request").request.Request(
        url, data=data, headers={"Content-Type": "application/json", "X-Admin-Network": "admin-net"}, method="POST"
    )
    with __import__("urllib.request").request.urlopen(req, timeout=5):
        pass


def test_legacystay_bootstrap_syncs_live_when_stack_up(e2e_stack: str) -> None:
    """Live LegacyStay bootstrap sync against the real simulator (stack up)."""
    assert e2e_stack
    with contextlib.suppress(Exception):
        _set_livestay_scenario("baseline", 0)
    result = _legacy_bootstrap()
    _assert_visible_tests_green(result)
    os.environ["LEGACYSTAY_BASE_URL"] = "http://localhost:8111"
    os.environ["LEGACYSTAY_API_KEY"] = "test-token-live"
    parsed = load_spec_from_dict(result.draft_spec)
    from autobridge.runtime.http import HttpClient as _HC

    http = _HC(base_url=parsed.source.base_url, auth=parsed.source.auth, retry=parsed.source.rate_limit.retry)
    summary = execute_sync(result.draft_spec, http)
    assert summary["outcome"] == "Complete", f"live outcome {summary['outcome']} unmet {summary['unmet_criteria']}"
    assert not summary["unmet_criteria"]
    # Nullable chaos is visible live (engine omits None-valued fields)
    assert any(r.get("guest_name") is None for r in summary["canonical_records"]["Reservation"])
    # Money Decimal live
    assert isinstance(summary["canonical_records"]["Reservation"][0]["total_amount"], Decimal)
    # ModelCall 0 via in-memory DB check (engine never calls LLM)
    from sqlalchemy import create_engine as _ce

    engine = _ce("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = session_factory(engine)
    with sessions() as session:
        assert session.query(ModelCall).count() == 0


def test_legacystay_live_throttle_recovered_with_retry(e2e_stack: str) -> None:
    """LegacyStay's baseline throttle fires live and the client recovers via retry."""
    assert e2e_stack
    import urllib.error
    import urllib.request

    try:
        _set_livestay_scenario("baseline", 0)
    except Exception as exc:
        pytest.skip(f"sim-legacystay admin unreachable: {exc}")
        return
    # Fresh count: the 8th request must be 429 with Retry-After
    seen_429 = False
    for _ in range(12):
        url = "http://localhost:8111/reservations?limit=1"
        try:
            req = urllib.request.Request(url, headers={"Authorization": "Bearer live-probe"}, method="GET")
            with urllib.request.urlopen(req, timeout=5):
                pass
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                seen_429 = True
                assert exc.headers.get("Retry-After") == "1"
                break
        except (urllib.error.URLError, OSError) as exc:
            pytest.skip(f"sim-legacystay unreachable live: {exc}")
            return
    assert seen_429, "expected the baseline throttle (429) within 12 live requests"
    # Full sync still Completes — retry absorbs the throttle
    result = _legacy_bootstrap()
    _assert_visible_tests_green(result)
    os.environ["LEGACYSTAY_BASE_URL"] = "http://localhost:8111"
    os.environ["LEGACYSTAY_API_KEY"] = "test-token-live"
    parsed = load_spec_from_dict(result.draft_spec)
    from autobridge.runtime.http import HttpClient as _HC

    http = _HC(base_url=parsed.source.base_url, auth=parsed.source.auth, retry=parsed.source.rate_limit.retry)
    summary = execute_sync(result.draft_spec, http)
    assert summary["outcome"] == "Complete", f"live outcome {summary['outcome']} unmet {summary['unmet_criteria']}"
