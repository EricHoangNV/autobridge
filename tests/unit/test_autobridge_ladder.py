"""P1.W4.4: RepairController + ladder L0/L1 wiring.

Tests for:
  - L0 knowledge cache keyed by vendor + fingerprint delta
  - L1 rules wired
  - rate_limit_429 / transient_503 -> no patch, backoff only
  - token_expired -> refresh if permitted else escalate
  - CAPABILITY_REMOVED / AUTH_REVOKED escalate with no patch attempted
  - frontier_tokens == 0 for all deterministic paths
"""

from __future__ import annotations

import copy
import pathlib

import yaml
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from autobridge.control.models import Base, RepairRun
from autobridge.drift.fingerprint import FingerprintDelta
from autobridge.repair.context import RepairContext
from autobridge.repair.controller import RepairController
from autobridge.repair.knowledge import KnowledgeCache, cache_key, delta_signature
from autobridge.repair.ladder import run_ladder
from autobridge.spec.patch import apply_patch


def _load_spec(vendor: str = "atlaspms") -> dict:
    path = pathlib.Path(f"generated/connectors/{vendor}/reference/spec.yaml")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _delta_types(entity: str, old_field: str, new_field: str) -> FingerprintDelta:
    return FingerprintDelta(
        fingerprint_changed=True,
        pagination_changed=False,
        keys_changed=False,
        types_changed=True,
        changed_entities=[entity],
        pagination_details={},
        keys_details={},
        types_details={entity: {"previous": {old_field: "str", "other": "str"}, "current": {new_field: "str", "other": "str"}}},
    )


def _delta_pagination(op: str, prev: str, cur: str) -> FingerprintDelta:
    return FingerprintDelta(
        fingerprint_changed=True,
        pagination_changed=True,
        keys_changed=False,
        types_changed=False,
        changed_entities=[],
        pagination_details={op: {"previous": {"strategy": prev}, "current": {"strategy": cur}}},
        keys_details={},
        types_details={},
    )


# ---------------------------------------------------------------------------
# KnowledgeCache
# ---------------------------------------------------------------------------


def test_knowledge_cache_put_and_lookup() -> None:
    cache = KnowledgeCache()
    delta = _delta_types("Reservation", "guest_name", "guest_identifier")
    patch = [{"op": "replace", "path": "/entities/Reservation/fields/guest_name/path", "value": "$.guest_identifier"}]
    entry = cache.put("AtlasPMS", delta, patch, "rename_field", drift_class="SCHEMA_RENAME", confidence=0.92)
    assert entry.vendor == "AtlasPMS"
    assert entry.rule_id == "rename_field"
    assert cache.size() == 1
    hit = cache.lookup("AtlasPMS", delta, drift_class="SCHEMA_RENAME")
    assert hit is not None
    assert hit.patch == patch
    assert hit.rule_id == "rename_field"


def test_knowledge_cache_vendor_case_insensitive() -> None:
    cache = KnowledgeCache()
    delta = _delta_types("Reservation", "guest_name", "guest_identifier")
    patch = [{"op": "replace", "path": "/entities/Reservation/fields/guest_name/path", "value": "$.guest_identifier"}]
    cache.put("AtlasPMS", delta, patch, "rename_field")
    # Different case should hit
    assert cache.lookup("atlaspms", delta) is not None
    assert cache.lookup("ATLASPMS", delta) is not None
    # Different vendor should miss
    assert cache.lookup("HarborPMS", delta) is None


def test_knowledge_cache_delta_signature_deterministic() -> None:
    delta = _delta_types("Reservation", "guest_name", "guest_identifier")
    sig1 = delta_signature(delta)
    sig2 = delta_signature(delta)
    assert sig1 == sig2
    # Different delta -> different signature
    delta2 = _delta_types("Reservation", "guest_name", "other")
    assert delta_signature(delta2) != sig1
    # None delta -> deterministic
    assert delta_signature(None) == delta_signature(None)


def test_knowledge_cache_key_includes_vendor() -> None:
    delta = _delta_types("Reservation", "a", "b")
    k1 = cache_key("AtlasPMS", "SCHEMA_RENAME", delta)
    k2 = cache_key("HarborPMS", "SCHEMA_RENAME", delta)
    assert k1 != k2
    k3 = cache_key("AtlasPMS", "SCHEMA_RENAME", delta)
    assert k1 == k3


def test_knowledge_cache_key_includes_drift_class() -> None:
    """REQ-028: same vendor + delta but different class must miss."""
    delta = _delta_types("Reservation", "a", "b")
    assert cache_key("AtlasPMS", "SCHEMA_RENAME", delta) != cache_key("AtlasPMS", "RATE_LIMIT", delta)
    assert cache_key("AtlasPMS", "SCHEMA_RENAME", delta) == cache_key("atlaspms", "schema_rename", delta)


def test_knowledge_cache_clear() -> None:
    cache = KnowledgeCache()
    delta = _delta_types("Reservation", "a", "b")
    cache.put("v", delta, [], "r")
    assert cache.size() == 1
    cache.clear()
    assert cache.size() == 0
    assert cache.lookup("v", delta) is None


def test_knowledge_cache_evict_vendor_delta() -> None:
    # AR-2-3: targeted eviction drops one (vendor, delta) key only.
    cache = KnowledgeCache()
    delta1 = _delta_types("Reservation", "a", "b")
    delta2 = _delta_types("Reservation", "a", "c")
    patch = [{"op": "replace", "path": "/a", "value": "b"}]
    cache.put("v", delta1, patch, "r")
    cache.put("v", delta2, patch, "r")
    assert cache.evict("v", delta1) is True
    assert cache.lookup("v", delta1) is None
    assert cache.lookup("v", delta2) is not None
    assert cache.evict("v", delta1) is False


def test_knowledge_cache_evict_patch_selective() -> None:
    # AR-2-3: evict_patch drops every key holding the rejected patch,
    # including the same patch cached under other delta keys, and
    # leaves unrelated patches alone.
    cache = KnowledgeCache()
    delta1 = _delta_types("Reservation", "a", "b")
    delta2 = _delta_types("Reservation", "a", "c")
    bad = [{"op": "replace", "path": "/a", "value": "bad"}]
    good = [{"op": "replace", "path": "/a", "value": "good"}]
    cache.put("v", delta1, bad, "r1")
    cache.put("v", delta2, bad, "r1")
    cache.put("v2", delta1, good, "r2")
    assert cache.evict_patch(bad) == 2
    assert cache.lookup("v", delta1) is None
    assert cache.lookup("v", delta2) is None
    assert cache.lookup("v2", delta1) is not None
    assert cache.evict_patch([]) == 0
    assert cache.evict_patch(None) == 0


def test_knowledge_cache_for_spec_helpers() -> None:
    spec = _load_spec()
    delta = _delta_pagination("list_reservations", "offset", "cursor")
    cache = KnowledgeCache()
    patch = [{"op": "replace", "path": "/a", "value": "b"}]
    cache.put_for_spec(spec, delta, patch, "offset_to_cursor")
    hit = cache.lookup_for_spec(spec, delta)
    assert hit is not None
    assert hit.patch == patch


def test_knowledge_cache_dict_delta() -> None:
    cache = KnowledgeCache()
    d = {"fingerprint_changed": True, "pagination_changed": True, "types_changed": False}
    patch = [{"op": "replace", "path": "/a", "value": "b"}]
    cache.put("v", d, patch, "r")
    assert cache.lookup("v", d) is not None
    assert cache.lookup("v", {"fingerprint_changed": False}) is None


# ---------------------------------------------------------------------------
# L0 knowledge lookup via ladder
# ---------------------------------------------------------------------------


def test_ladder_l0_hit_returns_cached_patch() -> None:
    spec = _load_spec()
    cache = KnowledgeCache()
    delta = _delta_types("Reservation", "guest_name", "guest_identifier")
    # Use a valid patch that actually applies to spec
    ctx = RepairContext(spec=spec, drift_class="SCHEMA_RENAME", fingerprint_delta=delta)
    from autobridge.repair.registry import get_rule

    rule = get_rule("rename_field")
    patch = rule.generate_patch(ctx)
    assert patch  # must be valid
    cache.put(spec["connector"]["vendor"], delta, patch, rule.id, drift_class="SCHEMA_RENAME", confidence=rule.confidence)
    res = run_ladder(spec, "SCHEMA_RENAME", fingerprint_delta=delta, knowledge_cache=cache)
    assert res.decision == "propose"
    assert res.ladder_level_reached == 0
    assert res.rule_id == "rename_field"
    assert res.candidate_patch == patch
    assert res.model_usage["frontier_tokens"] == 0
    assert res.ladder_trace[0]["action"] == "knowledge_hit"


def test_ladder_l0_miss_falls_through_to_l1() -> None:
    spec = _load_spec()
    cache = KnowledgeCache()  # empty
    delta = _delta_types("Reservation", "guest_name", "guest_identifier")
    res = run_ladder(spec, "SCHEMA_RENAME", fingerprint_delta=delta, knowledge_cache=cache)
    assert res.decision == "propose"
    assert res.ladder_level_reached == 1
    assert res.rule_id == "rename_field"
    assert res.candidate_patch
    assert res.model_usage["frontier_tokens"] == 0
    # Trace should have miss then propose
    actions = [t["action"] for t in res.ladder_trace]
    assert "knowledge_miss" in actions
    assert "propose" in actions


def test_ladder_l0_invalid_cached_patch_falls_through() -> None:
    spec = _load_spec()
    cache = KnowledgeCache()
    delta = _delta_types("Reservation", "guest_name", "guest_identifier")
    bad_patch = [{"op": "replace", "path": "/nonexistent", "value": "x"}]
    cache.put(spec["connector"]["vendor"], delta, bad_patch, "rename_field", drift_class="SCHEMA_RENAME")
    res = run_ladder(spec, "SCHEMA_RENAME", fingerprint_delta=delta, knowledge_cache=cache)
    # Invalid cached patch should be skipped, L1 should succeed
    assert res.decision == "propose"
    assert res.ladder_level_reached == 1
    assert res.rule_id == "rename_field"
    actions = [t["action"] for t in res.ladder_trace]
    assert "knowledge_hit" in actions
    assert "knowledge_invalid" in actions


def test_ladder_no_cache_uses_l1() -> None:
    spec = _load_spec()
    delta = _delta_types("Reservation", "guest_name", "guest_identifier")
    res = run_ladder(spec, "SCHEMA_RENAME", fingerprint_delta=delta, knowledge_cache=None)
    assert res.decision == "propose"
    assert res.ladder_level_reached == 1
    assert res.rule_id == "rename_field"


def test_ladder_no_delta_no_l0() -> None:
    spec = _load_spec()
    cache = KnowledgeCache()
    res = run_ladder(spec, "SCHEMA_RENAME", knowledge_cache=cache, extra={"entity": "Reservation", "field": "guest_name", "new_field": "guest_identifier"})
    # No delta so L0 not attempted, L1 should still succeed via extra
    assert res.decision == "propose"
    assert res.rule_id == "rename_field"


# ---------------------------------------------------------------------------
# L1 rules wiring
# ---------------------------------------------------------------------------


def test_ladder_l1_rename_field() -> None:
    spec = _load_spec()
    delta = _delta_types("Reservation", "guest_name", "guest_identifier")
    res = run_ladder(spec, "SCHEMA_RENAME", fingerprint_delta=delta)
    assert res.decision == "propose"
    assert res.rule_id == "rename_field"
    assert res.candidate_patch is not None
    assert len(res.candidate_patch) == 1
    assert res.candidate_patch[0]["path"] == "/entities/Reservation/fields/guest_name/path"
    # Verify patch applies and validates
    patched = apply_patch(spec, res.candidate_patch)
    assert patched["entities"]["Reservation"]["fields"]["guest_name"]["path"] == "$.guest_identifier"
    assert res.model_usage["frontier_tokens"] == 0


def test_ladder_l1_page_to_cursor() -> None:
    spec = _load_spec("atlaspms")  # atlas is page
    delta = _delta_pagination("list_reservations", "page", "cursor")
    res = run_ladder(spec, "PAGINATION_CHANGE", fingerprint_delta=delta)
    assert res.decision == "propose"
    assert res.rule_id == "page_to_cursor"
    assert res.candidate_patch is not None
    assert len(res.candidate_patch) == 10  # 2 ops *5
    patched = apply_patch(spec, res.candidate_patch)
    assert patched["operations"]["list_reservations"]["pagination"]["strategy"] == "cursor"
    assert res.model_usage["frontier_tokens"] == 0


def test_ladder_l1_cursor_to_offset() -> None:
    spec = _load_spec("harborpms")
    delta = _delta_pagination("list_reservations", "cursor", "offset")
    res = run_ladder(spec, "PAGINATION_CHANGE", fingerprint_delta=delta)
    assert res.decision == "propose"
    assert res.rule_id == "cursor_to_offset"
    assert res.model_usage["frontier_tokens"] == 0


def test_ladder_l1_nested_path_shift() -> None:
    spec = _load_spec()
    delta = FingerprintDelta(
        fingerprint_changed=True,
        pagination_changed=True,
        keys_changed=False,
        types_changed=False,
        changed_entities=[],
        pagination_details={"list_reservations": {"previous": {"strategy": "cursor", "items_path": "$.data"}, "current": {"strategy": "cursor", "items_path": "$.data.records"}}},
        keys_details={},
        types_details={},
    )
    _ = run_ladder(spec, "PAGINATION_CHANGE", fingerprint_delta=delta)
    # Let's use harbor which is cursor
    spec_h = _load_spec("harborpms")
    res2 = run_ladder(spec_h, "PAGINATION_CHANGE", fingerprint_delta=delta)
    assert res2.decision == "propose"
    assert res2.rule_id == "nested_path_shift"
    assert res2.candidate_patch is not None
    assert res2.candidate_patch[0]["value"] == "$.data.records"


def test_ladder_l1_type_coerce() -> None:
    spec = _load_spec()
    delta = FingerprintDelta(
        fingerprint_changed=True,
        pagination_changed=False,
        keys_changed=False,
        types_changed=True,
        changed_entities=["Reservation"],
        pagination_details={},
        keys_details={},
        types_details={"Reservation": {"previous": {"total_amount": "str"}, "current": {"total_amount": "Decimal"}}},
    )
    res = run_ladder(spec, "SCHEMA_TYPE_CHANGE", fingerprint_delta=delta)
    assert res.decision == "propose"
    assert res.rule_id == "type_coerce_string_number"
    assert res.model_usage["frontier_tokens"] == 0


# ---------------------------------------------------------------------------
# Special drift classes: RATE_LIMIT / TRANSIENT -> backoff only
# ---------------------------------------------------------------------------


def test_ladder_rate_limit_backoff_only_empty_patch() -> None:
    spec = _load_spec()
    # Atlas already covers 429
    res = run_ladder(spec, "RATE_LIMIT", signals=[{"kind": "rate_limited", "details": {"http_status": 429}}])
    assert res.decision == "propose"
    assert res.backoff_only is True
    assert res.candidate_patch == []
    assert res.requires_release is False
    assert res.rule_id == "retry_backoff_only"
    assert res.ladder_level_reached == 1
    assert res.model_usage["frontier_tokens"] == 0
    assert res.model_usage["calls"] == 0


def test_ladder_rate_limit_with_missing_status_produces_patch() -> None:
    spec = _load_spec()
    mod = copy.deepcopy(spec)
    mod["source"]["rate_limit"]["retry"]["statuses"] = [503]  # remove 429
    res = run_ladder(mod, "RATE_LIMIT", extra={"http_status": 429})
    assert res.decision == "propose"
    assert res.backoff_only is False
    assert res.requires_release is True
    assert res.candidate_patch is not None
    assert 429 in res.candidate_patch[0]["value"]
    patched = apply_patch(mod, res.candidate_patch)
    assert 429 in patched["source"]["rate_limit"]["retry"]["statuses"]
    assert res.model_usage["frontier_tokens"] == 0


def test_ladder_transient_backoff_only() -> None:
    spec = _load_spec()
    res = run_ladder(spec, "TRANSIENT", signals=[{"kind": "transient_error", "details": {"http_status": 503}}])
    assert res.decision == "propose"
    assert res.backoff_only is True
    assert res.candidate_patch == []
    assert res.requires_release is False
    assert res.rule_id == "retry_backoff_only"
    assert res.ladder_level_reached == 1
    assert res.model_usage["frontier_tokens"] == 0


def test_ladder_transient_with_extra() -> None:
    spec = _load_spec()
    res = run_ladder(spec, "TRANSIENT", extra={"http_status": 503})
    assert res.decision == "propose"
    assert res.backoff_only is True


def test_ladder_rate_limit_via_extra_http_status() -> None:
    spec = _load_spec()
    res = run_ladder(spec, "RATE_LIMIT", extra={"http_status": 429})
    assert res.decision == "propose"
    assert res.backoff_only is True


# ---------------------------------------------------------------------------
# AUTH_EXPIRED: token_expired -> refresh if permitted else escalate
# ---------------------------------------------------------------------------


def test_ladder_auth_expired_with_oauth_refresh() -> None:
    spec = _load_spec()
    spec_oauth = copy.deepcopy(spec)
    spec_oauth["source"]["auth"] = {
        "type": "oauth2_client_credentials",
        "credential_ref": "TEST_OAUTH",
        "token_url": "https://example.com/token",
        "username": "my_client",
    }
    res = run_ladder(spec_oauth, "AUTH_EXPIRED")
    assert res.decision == "propose"
    assert res.candidate_patch == []
    assert res.rule_id == "token_refresh"
    assert res.backoff_only is True
    assert res.requires_release is False
    assert res.ladder_level_reached == 0
    assert res.model_usage["frontier_tokens"] == 0
    assert res.ladder_trace[0]["action"] == "refresh_token"


def test_ladder_auth_expired_bearer_escalates() -> None:
    spec = _load_spec()  # bearer
    res = run_ladder(spec, "AUTH_EXPIRED")
    assert res.decision == "escalate"
    assert res.candidate_patch is None
    assert res.ladder_level_reached == 4
    assert res.escalation_reason is not None
    assert "refresh not permitted" in res.escalation_reason
    assert res.model_usage["frontier_tokens"] == 0


def test_ladder_auth_expired_oauth_missing_token_url_escalates() -> None:
    spec = _load_spec()
    mod = copy.deepcopy(spec)
    mod["source"]["auth"] = {"type": "oauth2_client_credentials", "credential_ref": "TEST_OAUTH", "username": "cid"}  # no token_url
    res = run_ladder(mod, "AUTH_EXPIRED")
    assert res.decision == "escalate"
    assert res.candidate_patch is None


def test_ladder_auth_expired_api_key_escalates() -> None:
    spec = _load_spec()
    mod = copy.deepcopy(spec)
    mod["source"]["auth"] = {"type": "api_key_header", "credential_ref": "TEST_KEY", "header_name": "X-API-Key"}
    res = run_ladder(mod, "AUTH_EXPIRED")
    assert res.decision == "escalate"


# ---------------------------------------------------------------------------
# Non-fixable: CAPABILITY_REMOVED / AUTH_REVOKED -> escalate no patch
# ---------------------------------------------------------------------------


def test_ladder_capability_removed_escalates_no_patch() -> None:
    spec = _load_spec()
    res = run_ladder(spec, "CAPABILITY_REMOVED")
    assert res.decision == "escalate"
    assert res.candidate_patch is None
    assert res.ladder_level_reached == 4
    assert res.rule_id is None
    assert res.backoff_only is False
    assert res.requires_release is False
    assert res.escalation_reason is not None
    assert "CAPABILITY_REMOVED" in res.escalation_reason
    assert res.model_usage["frontier_tokens"] == 0
    # No rule should have been attempted
    assert all(t["level"] == 4 for t in res.ladder_trace)


def test_ladder_auth_revoked_escalates_no_patch() -> None:
    spec = _load_spec()
    res = run_ladder(spec, "AUTH_REVOKED")
    assert res.decision == "escalate"
    assert res.candidate_patch is None
    assert res.ladder_level_reached == 4
    assert res.rule_id is None
    assert res.escalation_reason is not None
    assert "AUTH_REVOKED" in res.escalation_reason
    assert res.model_usage["frontier_tokens"] == 0


def test_ladder_capability_removed_ignores_cache_and_rules() -> None:
    spec = _load_spec()
    cache = KnowledgeCache()
    delta = _delta_types("Reservation", "guest_name", "guest_identifier")
    # Even if cache has entry, should still escalate
    cache.put(spec["connector"]["vendor"], delta, [{"op": "replace", "path": "/a", "value": "b"}], "rename_field")
    res = run_ladder(spec, "CAPABILITY_REMOVED", fingerprint_delta=delta, knowledge_cache=cache)
    assert res.decision == "escalate"
    assert res.candidate_patch is None


def test_ladder_auth_revoked_with_signals_still_escalates() -> None:
    spec = _load_spec()
    res = run_ladder(spec, "AUTH_REVOKED", signals=[{"kind": "auth_error", "details": {"http_status": 403}}], extra={"http_status": 403})
    assert res.decision == "escalate"
    assert res.candidate_patch is None


# ---------------------------------------------------------------------------
# Escalation for unknown / no matching rule
# ---------------------------------------------------------------------------


def test_ladder_unknown_no_rule_escalates() -> None:
    spec = _load_spec()
    res = run_ladder(spec, "UNKNOWN")
    assert res.decision == "escalate"
    assert res.candidate_patch is None
    assert res.ladder_level_reached == 4
    assert res.model_usage["frontier_tokens"] == 0


def test_ladder_semantic_without_evidence_escalates() -> None:
    spec = _load_spec()
    # ENUM_NEW_VALUE without evidence returns empty -> ladder should escalate
    res = run_ladder(spec, "ENUM_NEW_VALUE", signals=[{"kind": "unknown_enum", "details": {}}])
    assert res.decision == "escalate"
    assert res.candidate_patch is None


# ---------------------------------------------------------------------------
# RepairController persistence
# ---------------------------------------------------------------------------


def _make_controller_engine() -> tuple[KnowledgeCache, RepairController, sessionmaker]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    cache = KnowledgeCache()
    ctrl = RepairController(knowledge_cache=cache)
    return cache, ctrl, SessionLocal


def test_controller_handle_persists_repair_run() -> None:
    _, ctrl, SessionLocal = _make_controller_engine()
    spec = _load_spec()
    s = SessionLocal()
    delta = _delta_types("Reservation", "guest_name", "guest_identifier")
    res = ctrl.handle(spec=spec, drift_class="SCHEMA_RENAME", fingerprint_delta=delta, session=s)
    s.commit()
    assert res.decision == "propose"
    assert res.rule_id == "rename_field"
    assert res.candidate_patch is not None
    runs = s.query(RepairRun).all()
    assert len(runs) == 1
    assert runs[0].decision == "propose"
    assert runs[0].rule_id == "rename_field"
    assert runs[0].candidate_patch == res.candidate_patch
    assert runs[0].ladder_level_reached == 1
    assert runs[0].model_usage["frontier_tokens"] == 0


def test_controller_propose_caches_nothing_until_approved() -> None:
    """REQ-030 2b: proposing caches nothing; approving learns the fix."""
    from autobridge.engine.releases import approve_release

    cache, ctrl, SessionLocal = _make_controller_engine()
    spec = _load_spec()
    delta = _delta_types("Reservation", "guest_name", "guest_identifier")
    s = SessionLocal()
    res1 = ctrl.handle(spec=spec, drift_class="SCHEMA_RENAME", fingerprint_delta=delta, session=s)
    s.commit()
    assert res1.ladder_level_reached == 1  # first time via L1
    assert cache.size() == 0, "proposing must not populate L0"
    # Repeat without approval: L0 miss, L1 re-derives.
    s2 = SessionLocal()
    res2 = ctrl.handle(spec=spec, drift_class="SCHEMA_RENAME", fingerprint_delta=delta, session=s2)
    assert res2.ladder_level_reached == 1
    assert cache.size() == 0
    s2.commit()
    # Approve the first run's patch through the engine path: learned.
    run_id = s2.query(RepairRun).order_by(RepairRun.created_at.desc()).first().id
    from autobridge.control.models import Connector, ConnectorRelease, spec_signature

    conn = Connector(
        name="Atlas", base_url="http://localhost:8105", credential_ref="ATLAS_API_KEY",
        target_entities=["Reservation"], runtime_target={"kind": "local_process"},
    )
    s2.add(conn)
    s2.flush()
    base = ConnectorRelease(connector_id=conn.id, version=1, spec=spec, signature=spec_signature(spec))
    s2.add(base)
    s2.flush()
    candidate = ConnectorRelease(
        connector_id=conn.id, version=2, spec=spec, signature=spec_signature(spec),
        parent_release_id=base.id, provenance_repair_run_id=run_id,
    )
    s2.add(candidate)
    s2.flush()
    approval = approve_release(
        s2, repair_run_id=run_id, actor="tester",
        from_release_id=base.id, to_release_id=candidate.id,
        connector_id=conn.id, knowledge_cache=cache,
    )
    s2.commit()
    assert approval.decision == "approve"
    assert cache.size() == 1
    entry = cache.all_entries()[0]
    assert entry.approval_id == approval.id and entry.actor == "tester"
    # Third call hits L0 with the approved patch.
    s3 = SessionLocal()
    res3 = ctrl.handle(spec=spec, drift_class="SCHEMA_RENAME", fingerprint_delta=delta, session=s3)
    assert res3.ladder_level_reached == 0
    assert res3.rule_id == "rename_field"
    assert res3.candidate_patch == res1.candidate_patch


def test_controller_rate_limit_no_release_but_persists() -> None:
    _, ctrl, SessionLocal = _make_controller_engine()
    spec = _load_spec()
    s = SessionLocal()
    res = ctrl.handle(spec=spec, drift_class="RATE_LIMIT", signals=[{"kind": "rate_limited", "details": {"http_status": 429}}], session=s)
    assert res.decision == "propose"
    assert res.backoff_only is True
    assert res.requires_release is False
    assert res.candidate_patch == []
    s.commit()
    runs = s.query(RepairRun).all()
    assert len(runs) == 1
    assert runs[0].decision == "propose"
    assert runs[0].candidate_patch == []


def test_controller_escalate_persists_no_patch() -> None:
    _, ctrl, SessionLocal = _make_controller_engine()
    spec = _load_spec()
    s = SessionLocal()
    res = ctrl.handle(spec=spec, drift_class="CAPABILITY_REMOVED", session=s)
    assert res.decision == "escalate"
    assert res.candidate_patch is None
    s.commit()
    runs = s.query(RepairRun).all()
    assert len(runs) == 1
    assert runs[0].decision == "escalate"
    assert runs[0].candidate_patch is None
    assert runs[0].ladder_level_reached == 4


def test_controller_auth_expired_refresh_vs_escalate() -> None:
    _, ctrl, SessionLocal = _make_controller_engine()
    spec_bearer = _load_spec()
    s = SessionLocal()
    res = ctrl.handle(spec=spec_bearer, drift_class="AUTH_EXPIRED", session=s)
    assert res.decision == "escalate"
    s.commit()

    spec_oauth = copy.deepcopy(spec_bearer)
    spec_oauth["source"]["auth"] = {"type": "oauth2_client_credentials", "credential_ref": "TEST_OAUTH", "token_url": "https://example.com/token", "username": "cid"}
    s2 = SessionLocal()
    res2 = ctrl.handle(spec=spec_oauth, drift_class="AUTH_EXPIRED", session=s2)
    assert res2.decision == "propose"
    assert res2.rule_id == "token_refresh"
    assert res2.backoff_only is True
    s2.commit()


def test_controller_handle_without_session_no_db_side_effect() -> None:
    _, ctrl, SessionLocal = _make_controller_engine()
    spec = _load_spec()
    delta = _delta_types("Reservation", "guest_name", "guest_identifier")
    res = ctrl.handle(spec=spec, drift_class="SCHEMA_RENAME", fingerprint_delta=delta, session=None)
    assert res.decision == "propose"
    # No row persisted
    s = SessionLocal()
    assert s.query(RepairRun).count() == 0


def test_controller_frontier_tokens_zero_for_all_deterministic() -> None:
    _, ctrl, SessionLocal = _make_controller_engine()
    spec = _load_spec()
    cases = [
        ("RATE_LIMIT", [{"kind": "rate_limited", "details": {"http_status": 429}}], None),
        ("TRANSIENT", [{"kind": "transient_error", "details": {"http_status": 503}}], None),
        ("CAPABILITY_REMOVED", None, None),
        ("AUTH_REVOKED", None, None),
        ("AUTH_EXPIRED", None, None),  # bearer escalates
    ]
    for drift_class, signals, delta in cases:
        s = SessionLocal()
        res = ctrl.handle(spec=spec, drift_class=drift_class, signals=signals, fingerprint_delta=delta, session=s)
        assert res.model_usage["frontier_tokens"] == 0, f"frontier_tokens not zero for {drift_class}"
        assert res.model_usage["calls"] == 0
        s.rollback()


def test_controller_handle_from_signals_classifies() -> None:
    _, ctrl, SessionLocal = _make_controller_engine()
    spec = _load_spec()
    from autobridge.drift.signals import DriftSignal

    signals = [DriftSignal(kind="rate_limited", severity="medium", message="429", details={"http_status": 429})]
    s = SessionLocal()
    res = ctrl.handle_from_signals(spec=spec, signals=signals, session=s)
    # Rate limited should be classified as RATE_LIMIT -> backoff
    assert res.decision == "propose"
    assert res.backoff_only is True


def test_ladder_evidence_ids_propagated() -> None:
    spec = _load_spec()
    signals = [{"kind": "rate_limited", "details": {"http_status": 429}, "evidence_ids": ["ev1", "ev2"]}]
    res = run_ladder(spec, "RATE_LIMIT", signals=signals)
    assert "ev1" in res.evidence_ids
    assert "ev2" in res.evidence_ids


def test_ladder_trace_structure() -> None:
    spec = _load_spec()
    res = run_ladder(spec, "CAPABILITY_REMOVED")
    assert len(res.ladder_trace) == 1
    assert res.ladder_trace[0]["level"] == 4
    assert "drift_class" in res.ladder_trace[0]

    spec2 = _load_spec()
    delta = _delta_types("Reservation", "guest_name", "guest_identifier")
    res2 = run_ladder(spec2, "SCHEMA_RENAME", fingerprint_delta=delta)
    assert any(t["level"] == 1 for t in res2.ladder_trace)
    assert any("rule_id" in t for t in res2.ladder_trace)


def test_controller_handle_classified() -> None:
    _, ctrl, SessionLocal = _make_controller_engine()
    spec = _load_spec()
    from autobridge.drift.classifier import classify
    from autobridge.drift.signals import DriftSignal

    signals = [DriftSignal(kind="rate_limited", severity="medium", message="429", details={"http_status": 429})]
    cls = classify(signals)
    s = SessionLocal()
    res = ctrl.handle_classified(spec=spec, classification=cls, signals=signals, session=s)
    assert res.decision == "propose"
    assert res.backoff_only is True


def test_controller_handle_sync_summary() -> None:
    _, ctrl, SessionLocal = _make_controller_engine()
    spec = _load_spec()
    sync_summary = {
        "outcome": "Degraded",
        "unmet_criteria": ["no_loss"],
        "operation_results": {"list_reservations": {"pages_fetched": 2, "stop_reason": "complete", "repeated_cursor": False, "http_status": 429, "http_errors": [429]}},
        "records_observed": {"Reservation": 10},
        "fingerprint_id": "abc",
    }
    s = SessionLocal()
    res, cls = ctrl.handle_sync_summary(spec=spec, sync_summary=sync_summary, session=s)
    assert res.decision in ("propose", "escalate")
    assert cls.drift_class is not None


def test_controller_repair_job_handler_rate_limit() -> None:
    from autobridge.control.jobs import JobContext
    from autobridge.control.models import Connector, DriftEvent
    from autobridge.control.models import spec_signature as _sig
    from autobridge.repair.controller import repair_job_handler

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    spec = _load_spec()
    session = SessionLocal()
    conn = Connector(id="c1", name="test", base_url="http://example.com", credential_ref="TEST_TOKEN", target_entities=["Reservation"], runtime_target={})
    session.add(conn)
    session.flush()
    from autobridge.control.models import ConnectorRelease

    rel = ConnectorRelease(connector_id=conn.id, version=1, spec=spec, signature=_sig(spec))
    session.add(rel)
    session.flush()
    conn.active_release_id = rel.id
    session.flush()
    ev = DriftEvent(connector_id=conn.id, drift_class="RATE_LIMIT", severity="medium", evidence_ids=[], status="open")
    session.add(ev)
    session.commit()
    ev_id = ev.id
    session2 = SessionLocal()
    ctx = JobContext(job_id="j1", kind="repair", run_id=None, payload={"drift_event_ids": [ev_id], "drift_class": "RATE_LIMIT", "signals": [{"kind": "rate_limited", "details": {"http_status": 429}}]}, attempt=1, session=session2, worker_id="w1")
    out = repair_job_handler(ctx)
    assert out["decision"] == "propose"
    assert out["backoff_only"] is True
    assert "repair_run_id" in out
    session2.commit()
    assert session2.query(RepairRun).count() == 1


def test_controller_repair_job_handler_escalate_unknown() -> None:
    from autobridge.control.jobs import JobContext
    from autobridge.repair.controller import repair_job_handler

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    s = SessionLocal()
    ctx = JobContext(job_id="j2", kind="repair", run_id=None, payload={"drift_event_ids": [], "drift_class": "UNKNOWN"}, attempt=1, session=s, worker_id="w1")
    out = repair_job_handler(ctx)
    assert out["decision"] == "escalate"
    assert out["ladder_level_reached"] == 4

    s2 = SessionLocal()
    ctx2 = JobContext(job_id="j3", kind="repair", run_id=None, payload={}, attempt=1, session=s2, worker_id="w1")
    out2 = repair_job_handler(ctx2)
    assert out2["decision"] == "escalate"


# ---------------------------------------------------------------------------
# REQ-028: L0 scoping — drift class + observed delta, never for
# transport/auth/capability classes or empty deltas
# ---------------------------------------------------------------------------


def _empty_delta() -> FingerprintDelta:
    return FingerprintDelta(
        fingerprint_changed=False,
        pagination_changed=False,
        keys_changed=False,
        types_changed=False,
        changed_entities=[],
        pagination_details={},
        keys_details={},
        types_details={},
    )


def test_delta_has_content() -> None:
    from autobridge.repair.knowledge import delta_has_content

    assert delta_has_content(None) is False
    assert delta_has_content({}) is False
    assert delta_has_content(_empty_delta()) is False
    assert delta_has_content({"fingerprint_changed": False}) is False
    assert delta_has_content(_delta_types("Reservation", "a", "b")) is True
    assert delta_has_content(_delta_pagination("op", "page", "cursor")) is True
    assert delta_has_content({"fingerprint_changed": True}) is True


def test_l0_misses_across_drift_classes() -> None:
    """Same vendor + delta learned under SCHEMA_RENAME must miss for UNKNOWN."""
    from autobridge.repair.knowledge import delta_has_content

    assert delta_has_content(_delta_types("Reservation", "guest_name", "guest_identifier"))
    spec = _load_spec()
    cache = KnowledgeCache()
    delta = _delta_types("Reservation", "guest_name", "guest_identifier")
    patch = [{"op": "replace", "path": "/entities/Reservation/fields/guest_name/path", "value": "$.guest_identifier"}]
    cache.put("AtlasPMS", delta, patch, "rename_field", drift_class="SCHEMA_RENAME")
    res = run_ladder(spec, "UNKNOWN", fingerprint_delta=delta, knowledge_cache=cache)
    assert res.ladder_level_reached != 0, "L0 must not fire across drift classes"
    actions = [t["action"] for t in res.ladder_trace]
    assert "knowledge_hit" not in actions


def test_l0_skips_empty_delta() -> None:
    """An empty delta never consults L0 even when the store holds its key."""
    from autobridge.repair.knowledge import cache_key as _ck

    spec = _load_spec()
    cache = KnowledgeCache()
    delta = _empty_delta()
    patch = [{"op": "replace", "path": "/a", "value": "b"}]
    cache.put("AtlasPMS", delta, patch, "some_rule", drift_class="UNKNOWN")
    assert _ck("AtlasPMS", "UNKNOWN", delta) in [e.delta_key for e in cache.all_entries()]
    res = run_ladder(spec, "UNKNOWN", fingerprint_delta=delta, knowledge_cache=cache)
    assert res.ladder_level_reached != 0, "L0 must not fire on an empty delta"
    actions = [t["action"] for t in res.ladder_trace]
    assert "knowledge_skip" in actions
    assert "knowledge_hit" not in actions


def test_l0_never_for_rate_limit_and_transient() -> None:
    """RATE_LIMIT / TRANSIENT never consult L0 and stay backoff-only."""
    spec = _load_spec()
    cache = KnowledgeCache()
    delta = _delta_pagination("list_reservations", "page", "cursor")
    patch = [{"op": "replace", "path": "/a", "value": "b"}]
    cache.put("AtlasPMS", delta, patch, "page_to_cursor", drift_class="RATE_LIMIT")
    before = cache.size()
    for drift_class in ("RATE_LIMIT", "TRANSIENT"):
        res = run_ladder(
            spec,
            drift_class,
            signals=[{"kind": "rate_limited" if drift_class == "RATE_LIMIT" else "transient_error", "details": {}}],
            fingerprint_delta=delta,
            knowledge_cache=cache,
        )
        assert res.decision == "propose"
        assert res.candidate_patch == [] or res.backoff_only, f"{drift_class} must stay backoff-only"
    assert cache.size() == before, "transport drifts must not populate L0"


def test_controller_never_caches_no_learn_classes_or_empty_delta() -> None:
    """Controller populate guard: NO_LEARN classes and empty deltas store nothing."""
    cache, ctrl, SessionLocal = _make_controller_engine()
    spec = _load_spec()
    # RATE_LIMIT with a contentful delta proposes backoff-only (no patch to cache).
    s = SessionLocal()
    ctrl.handle(spec=spec, drift_class="RATE_LIMIT", fingerprint_delta=_delta_pagination("op", "page", "cursor"), session=s)
    s.commit()
    assert cache.size() == 0
    # UNKNOWN with an empty delta escalates or proposes via rule without caching.
    s2 = SessionLocal()
    res2 = ctrl.handle(spec=spec, drift_class="UNKNOWN", fingerprint_delta=_empty_delta(), session=s2)
    s2.commit()
    assert cache.size() == 0, f"empty delta must not populate L0 (decision={res2.decision})"


def test_controller_handle_persists_honest_derivation() -> None:
    """REQ-032 §2: the deterministic path persists the derivation stages.

    An L1 rule repair that never enumerated hypotheses records the honest
    zero-count stage (never an invented funnel); a climb handed hypotheses
    records the distinct stage with counts and ids.
    """
    from autobridge.control.sse import get_evidence_for_run

    _, ctrl, SessionLocal = _make_controller_engine()
    spec = _load_spec()

    s = SessionLocal()
    ctrl.handle(
        spec=spec,
        drift_class="SCHEMA_RENAME",
        fingerprint_delta=_delta_types("Reservation", "guest_name", "guest_identifier"),
        # Fast-path signals are often never persisted as records: unknown
        # parent ids must not fail the derivation append.
        signals=[{"kind": "keys", "evidence_ids": ["ghost-evidence-id"]}],
        session=s,
    )
    s.commit()
    run = s.query(RepairRun).order_by(RepairRun.created_at.desc()).first()
    assert run is not None
    records = get_evidence_for_run(s, run.id)
    assert len(records) == 1 and records[0].kind == "hypothesis"
    assert records[0].parent_ids == []
    stages = records[0].payload["stages"]
    assert stages == [
        {
            "name": "resolved without enumeration",
            "count": 0,
            "ids": [],
            "source": "ladder resolved at L1 from wire signals; no hypotheses enumerated",
        }
    ]

    s2 = SessionLocal()

    class _H:
        id = "h-1"

    ctrl.handle(
        spec=spec,
        drift_class="SCHEMA_RENAME",
        fingerprint_delta=_delta_types("Reservation", "guest_name", "guest_identifier"),
        hypotheses=[_H()],
        enumerator_yield_count=7,
        session=s2,
    )
    s2.commit()
    run2 = s2.query(RepairRun).order_by(RepairRun.created_at.desc()).first()
    assert run2 is not None
    records2 = get_evidence_for_run(s2, run2.id)
    assert len(records2) == 1
    stages2 = records2[0].payload["stages"]
    assert stages2[0]["name"] == "generated" and stages2[0]["count"] == 7
    assert stages2[1]["name"] == "distinct" and stages2[1]["count"] == 1
    assert stages2[1]["ids"] == ["h-1"]
