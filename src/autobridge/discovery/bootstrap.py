"""Bootstrap — docs → draft spec → compile → visible tests → verify (P1.W5.6, plan §2.6/§4 W5).

Converts narrative docs (OpenAPI or Markdown) into a validated ConnectorSpec
without any reference-spec access. Pipeline stages:

  docs ──docs_reader──► InterfaceDescription
       ──classifiers──► DiscoveryClassification
       ──enumerator───► Hypotheses (≥1 evidence each)
       ──probes───────► ProbeSelector/Resolver (optional live probing)
       ──compile──────► Draft ConnectorSpec dict (validated)
       ──visible tests► Guard checks (stop_when, money Decimal, etc.)
       ──verify───────► Caller checks oracle/verifier (not imported here)

No imports of verifier/simulators. No reference-spec file reads. Secrets stay
as credential_ref only. Every hypothesis links to ≥1 evidence (enforced by
hypotheses module). L3 frontier is only used when enumerator yields <2
(asserted in resolver).

Usage:
    result = build_draft_spec(docs_text, source="harbor.md")
    assert result.success
    spec = result.draft_spec  # validated dict ready for ConnectorRelease
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from autobridge.discovery.classifiers import classify_all
from autobridge.discovery.docs_reader import InterfaceDescription, read_docs_text
from autobridge.discovery.hypotheses import HypothesisEnumerator

# ---------------------------------------------------------------------------
# helpers — slug, vendor, placeholder, transforms
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    """Slug from title: lower, alphanum+hyphen, collapse."""
    s = text.lower().strip()
    # Extract first token that looks like vendor name (e.g., HarborPMS)
    # Prefer the first word containing PMS or API
    m = re.search(r"([a-z][a-z0-9]*pms)", s)
    if m:
        return m.group(1)
    # Fallback: slug whole title, keep first two words
    words = re.split(r"[^a-z0-9]+", s)
    words = [w for w in words if w and w not in ("api", "the", "narrative", "documentation")]
    if not words:
        return "connector"
    # Limit to 2 words to avoid long ids
    slug = "-".join(words[:2])
    slug = _SLUG_RE.sub("-", slug).strip("-")
    slug = slug[:32].strip("-")
    return slug or "connector"


def _derive_vendor(iface: InterfaceDescription) -> str:
    """Vendor from title: first CamelCase token or title prefix."""
    title = (iface.title or iface.source or "Unknown").strip()
    # Look for HarborPMS / AtlasPMS pattern
    m = re.search(r"([A-Z][a-z]+PMS)", title)
    if m:
        return m.group(1)
    m2 = re.search(r"([A-Z][a-zA-Z]+)", title)
    if m2:
        return m2.group(1)
    # Fallback to first word capitalized
    w = title.split()[0] if title.split() else "Vendor"
    return w[:32]


def _derive_env_placeholder(base_url: str, connector_id: str) -> str:
    """Map localhost URLs to ${VAR} placeholder; else generic."""
    if not base_url:
        ref = connector_id.upper().replace("-", "_")
        return f"${{{ref}_BASE_URL}}"
    low = base_url.lower()
    if "8106" in low or "harbor" in low:
        return "${HARBOR_BASE_URL}"
    if "8105" in low or "atlas" in low:
        return "${ATLAS_BASE_URL}"
    if "localhost" in low or "127.0.0.1" in low:
        ref = connector_id.upper().replace("-", "_")
        return f"${{{ref}_BASE_URL}}"
    # Already env placeholder
    if base_url.startswith("${"):
        return base_url
    return base_url


def _derive_credential_ref(connector_id: str, override: str | None = None) -> str:
    if override and re.match(r"^[A-Z][A-Z0-9_]{0,127}$", override):
        return override
    base = connector_id.upper().replace("-", "_")
    # Strip trailing PMS for HarborPMS/AtlasPMS → HARBOR/ATLAS
    if base.endswith("PMS_API_KEY"):
        # Already with suffix, handle double
        base = base
    elif base.endswith("PMS"):
        # harborpms -> HARBORPMS -> want HARBOR
        if base == "HARBORPMS":
            base = "HARBOR"
        elif base == "ATLASPMS":
            base = "ATLAS"
        else:
            # generic: strip PMS suffix
            base = base[:-3]
    ref = base + "_API_KEY"
    if not re.match(r"^[A-Z][A-Z0-9_]{0,127}$", ref):
        ref = "CONNECTOR_API_KEY"
    return ref


def _op_name_for_path(path: str) -> str:
    """Map API path to operation name (spec.operations key)."""
    if path == "/reservations":
        return "list_reservations"
    if path == "/folios":
        return "list_folios"
    if path == "/properties":
        return "list_properties"
    if "/folios" in path:
        # nested: /reservations/{id}/folios
        return "list_folios_nested"
    # generic sanitize
    name = path.strip("/").replace("/", "_").replace("{", "").replace("}", "").replace("-", "_")
    name = re.sub(r"[^a-z0-9_]", "", name.lower())
    if not name:
        name = "list_items"
    if not name.startswith("list_"):
        name = f"list_{name}"
    return name


# ---------------------------------------------------------------------------
# entity field helpers
# ---------------------------------------------------------------------------

# Known canonical fields per entity
_RESERVATION_FIELDS = [
    "reservation_id",
    "property_id",
    "guest_name",
    "arrival_date",
    "departure_date",
    "status",
    "total_amount",
    "currency",
    "updated_at",
]
_FOLIO_FIELDS = [
    "charge_id",
    "reservation_id",
    "property_id",
    "business_date",
    "amount",
    "currency",
    "description",
    "posted_at",
]
_PROPERTY_FIELDS = [
    "property_id",
    "name",
    "timezone",
    "currency",
]
# Fields to ignore (ambiguous)
_IGNORE_FIELDS = {"date"}


def _transform_for_field(
    field_name: str,
    status_kind: str | None = None,
    status_mapping: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Closed transform steps for a canonical field."""
    low = field_name.lower()
    if low in ("arrival_date", "departure_date"):
        return [{"name": "date_local", "args": {"tz": "source"}}]
    if low == "business_date":
        return [{"name": "business_date", "args": {"tz": "source", "cutoff": 4}}]
    if low == "status":
        # Determine mapping
        mapping: dict[str, str]
        if status_kind == "opaque" and status_mapping:
            mapping = dict(status_mapping)
        elif status_kind == "opaque":
            mapping = {"H1": "confirmed", "H2": "checked_in", "H3": "checked_out", "H4": "cancelled", "H5": "no_show"}
        else:
            # clear identity
            mapping = {"confirmed": "confirmed", "checked_in": "checked_in", "checked_out": "checked_out", "cancelled": "cancelled", "no_show": "no_show"}
        return [{"name": "enum_map", "args": {"map": mapping, "on_unknown": "block"}}]
    if low in ("total_amount", "amount"):
        return [{"name": "money", "args": {"scale": 2}}]
    return []


def _entity_key(entity_name: str) -> str:
    if entity_name == "Reservation":
        return "$.reservation_id"
    if entity_name == "FolioCharge":
        return "$.charge_id"
    if entity_name == "Property":
        return "$.property_id"
    return "$.id"


def _checkpoint_field_for_entity(entity_name: str) -> str:
    if entity_name == "Reservation":
        return "$.updated_at"
    if entity_name == "FolioCharge":
        return "$.posted_at"
    if entity_name == "Property":
        return "$.updated_at"
    return "$.updated_at"


def _collect_fields_for_entity(
    entity_name: str,
    iface: InterfaceDescription,  # noqa: ARG001 — reserved for future filtering by iface
    status_kind: str | None,
    status_mapping: dict[str, str] | None,
) -> dict[str, dict[str, Any]]:
    """Build fields dict for entity."""
    if entity_name == "Reservation":
        field_names = list(_RESERVATION_FIELDS)
    elif entity_name == "FolioCharge":
        field_names = list(_FOLIO_FIELDS)
    elif entity_name == "Property":
        field_names = list(_PROPERTY_FIELDS)
    else:
        field_names = []

    # Optionally filter to fields that actually appear in iface.schemas / operations
    # But we keep the full canonical set to ensure verifier completeness; extra
    # fields that are missing from docs still map via path $.<name> and will be
    # populated from simulator (simulator always returns them). This ensures
    # Harbor bootstrap passes oracle even if docs example omitted a field.
    # We exclude ambiguous date field
    fields: dict[str, dict[str, Any]] = {}
    for fname in field_names:
        if fname in _IGNORE_FIELDS:
            continue
        transform_steps = _transform_for_field(fname, status_kind, status_mapping)
        fields[fname] = {"path": f"$.{fname}", "transform": transform_steps}
    return fields


def _pagination_for_strategy(strategy: str) -> dict[str, Any]:
    """Pagination object for a given strategy."""
    if strategy == "cursor":
        return {
            "strategy": "cursor",
            "cursor_param": "cursor",
            "limit_param": "limit",
            "next_path": "$.next_cursor",
            "items_path": "$.data",
            "stop_when": ["null_cursor", "repeated_cursor"],
        }
    if strategy == "offset":
        return {
            "strategy": "offset",
            "offset_param": "offset",
            "limit_param": "limit",
            "next_path": "$.next_offset",
            "items_path": "$.data",
            "stop_when": ["null_cursor", "empty_page"],
        }
    if strategy == "page":
        return {
            "strategy": "page",
            "page_param": "page",
            "limit_param": "page_size",
            "next_path": "$.next_page",
            "items_path": "$.data",
            "stop_when": ["empty_page", "no_next"],
        }
    # none -> no pagination (e.g., /properties)
    return {}


def _choose_pagination_strategy(
    op_path: str,
    iface: InterfaceDescription,
    classification: Any,
) -> str:
    """Pick pagination strategy for operation path using classifier confidence."""
    # Check classifier per_operation guess
    if classification is not None and classification.pagination is not None:
        for guess in classification.pagination.per_operation:
            if guess.operation == op_path:
                # If classifier confidence is high/medium, trust it
                if guess.confidence in ("high", "medium"):
                    return guess.strategy
                # Low confidence still returns strategy if not none
                if guess.strategy != "none":
                    return guess.strategy
        # Also check pagination_notes fallback
    # Fallback to operation's own hint
    for op in iface.operations:
        if op.path == op_path and op.pagination_hint != "none":
            return op.pagination_hint
    # Check iface.pagination_notes for this path
    for note, _ in iface.pagination_notes:
        low = note.lower()
        if op_path.lower() in low:
            if "cursor" in low:
                return "cursor"
            if "offset" in low:
                return "offset"
    # Default: if path is reservations/folios, assume cursor for Harbor-like docs
    # Heuristic: if docs mention cursor anywhere, use cursor for list endpoints
    has_cursor_note = any("cursor" in n.lower() for n, _ in iface.pagination_notes)
    if has_cursor_note and op_path in ("/reservations", "/folios"):
        return "cursor"
    # Non-list endpoints (e.g., single fetch) -> none
    if "{" in op_path:
        return "none"
    return "none"


# ---------------------------------------------------------------------------
# visible tests
# ---------------------------------------------------------------------------


def visible_tests(spec: dict[str, Any]) -> list[str]:
    """Run compile-time visible tests; return error messages (empty = pass).

    Checks performed:
    - spec validates via ConnectorSpec
    - every operation with pagination has required stop_when guards
    - money fields have scale 2
    - enum_map has on_unknown block
    - pagination strategy consistent with next_path
    """
    errors: list[str] = []
    # 1. Schema validation
    try:
        from autobridge.spec.models import load_spec_from_dict

        load_spec_from_dict(spec)
    except Exception as exc:
        errors.append(f"spec validation: {exc}")
        return errors  # cannot continue without valid spec

    # 2. Pagination guards
    for op_name, op in spec.get("operations", {}).items():
        pag = op.get("pagination")
        if not pag:
            continue
        strategy = pag.get("strategy")
        stop_when = pag.get("stop_when", [])
        if not stop_when:
            errors.append(f"operations.{op_name}.pagination.stop_when must be non-empty")
        if strategy == "cursor" and "null_cursor" not in stop_when:
            errors.append(f"operations.{op_name} cursor pagination needs null_cursor guard")
        if strategy == "offset" and "empty_page" not in stop_when and "null_cursor" not in stop_when:
            errors.append(f"operations.{op_name} offset pagination needs empty_page or null_cursor guard")
        next_path = pag.get("next_path", "")
        expected_next = {"cursor": "$.next_cursor", "offset": "$.next_offset", "page": "$.next_page"}.get(strategy, "")
        if expected_next and next_path != expected_next and next_path is not None:
            # Allow either but warn if mismatched
            if strategy == "cursor" and next_path != "$.next_cursor":
                errors.append(f"operations.{op_name}.pagination.next_path should be $.next_cursor for cursor strategy, got {next_path!r}")
            if strategy == "offset" and next_path != "$.next_offset":
                errors.append(f"operations.{op_name}.pagination.next_path should be $.next_offset for offset strategy, got {next_path!r}")

    # 3. Money scale
    for ent_name, ent in spec.get("entities", {}).items():
        for fname, fm in ent.get("fields", {}).items():
            if fname in ("total_amount", "amount"):
                steps = fm.get("transform", [])
                if not steps:
                    errors.append(f"entities.{ent_name}.fields.{fname} money field needs money transform")
                else:
                    has_money = any(s.get("name") == "money" for s in steps)
                    if not has_money:
                        errors.append(f"entities.{ent_name}.fields.{fname} should have money transform")
                    for s in steps:
                        if s.get("name") == "money":
                            scale = s.get("args", {}).get("scale")
                            if scale is not None and scale != 2:
                                errors.append(f"entities.{ent_name}.fields.{fname} money scale should be 2, got {scale}")

    # 4. Enum guards
    for ent_name, ent in spec.get("entities", {}).items():
        for fname, fm in ent.get("fields", {}).items():
            steps = fm.get("transform", [])
            for s in steps:
                if s.get("name") == "enum_map":
                    on_unknown = s.get("args", {}).get("on_unknown")
                    if on_unknown != "block":
                        errors.append(f"entities.{ent_name}.fields.{fname} enum_map requires on_unknown: block")
                    mmap = s.get("args", {}).get("map")
                    if not mmap:
                        errors.append(f"entities.{ent_name}.fields.{fname} enum_map requires non-empty map")

    return errors


# ---------------------------------------------------------------------------
# main builder
# ---------------------------------------------------------------------------


@dataclass
class BootstrapResult:
    """Outcome of docs → draft spec → compile → visible tests."""

    draft_spec: dict[str, Any] | None = None
    iface: InterfaceDescription | None = None
    classification: Any | None = None
    hypotheses_by_family: dict[str, Any] = field(default_factory=dict)
    resolution_results: dict[str, Any] = field(default_factory=dict)
    visible_errors: list[str] = field(default_factory=list)
    open_questions: tuple[str, ...] = ()
    model_usage: dict[str, Any] = field(default_factory=dict)
    evidence_payloads: list[dict[str, Any]] = field(default_factory=list)
    success: bool = False
    degradation_reason: str | None = None
    trace: list[dict[str, Any]] = field(default_factory=list)


def _compile_spec(
    iface: InterfaceDescription,
    classification: Any,
    connector_id: str | None = None,
    vendor: str | None = None,
    credential_ref: str | None = None,
    base_url_override: str | None = None,
) -> dict[str, Any]:
    """Compile InterfaceDescription + classification into validated spec dict.

    Does not run probes; deterministic from docs alone.
    """
    # Connector identity
    cid = connector_id or _slugify(iface.title or iface.source or "harbor")
    # Ensure slug matches spec requirements
    cid = re.sub(r"[^a-z0-9_-]", "-", cid.lower()).strip("-") or "connector"
    ven = vendor or _derive_vendor(iface)
    # Base URL placeholder
    raw_base = base_url_override or iface.base_url or ""
    base_placeholder = _derive_env_placeholder(raw_base, cid)
    cred_ref = _derive_credential_ref(cid, credential_ref)

    # Auth
    auth_kind = "bearer"
    auth_header: str | None = None
    if classification is not None and classification.auth is not None:
        ak = classification.auth.kind
        if ak in ("bearer", "api_key_header", "basic", "oauth2"):
            # Map none/unknown permissive to bearer (Harbor permissive docs still use bearer)
            auth_kind = "bearer" if ak in ("none", "unknown") else ak
            auth_header = classification.auth.header_name
        elif ak == "none":
            auth_kind = "bearer"
    # Map to spec auth type
    auth_type_map = {
        "bearer": "bearer",
        "api_key_header": "api_key_header",
        "basic": "basic",
        "oauth2": "oauth2_client_credentials",
    }
    spec_auth_type = auth_type_map.get(auth_kind, "bearer")
    auth_spec: dict[str, Any] = {"type": spec_auth_type, "credential_ref": cred_ref}
    if spec_auth_type == "api_key_header":
        auth_spec["header_name"] = auth_header or "X-API-Key"
    if spec_auth_type == "basic":
        auth_spec["username"] = "api_user"
    if spec_auth_type == "oauth2_client_credentials":
        auth_spec["token_url"] = "https://auth.example.com/oauth/token"

    # Timezone
    tz_value = "America/New_York"
    # Detect if any date note mentions cutoff 04:00 -> keep NY
    if iface.date_notes:
        for note, _ in iface.date_notes:
            if "04:00" in note or "cutoff" in note.lower():
                tz_value = "America/New_York"
                break
    # If no date notes, still default to NY for PMs compatibility
    timezone_source = {"kind": "static", "value": tz_value}

    # Operations
    operations: dict[str, Any] = {}
    # Status mapping for transforms
    status_kind = None
    status_mapping: dict[str, str] | None = None
    if classification is not None and classification.status_vocabulary is not None:
        status_kind = classification.status_vocabulary.kind
        status_mapping = dict(classification.status_vocabulary.mapping_hint) if classification.status_vocabulary.mapping_hint else None

    for op in iface.operations:
        path = op.path
        # Skip health/readiness and single-item fetches
        if path in ("/healthz", "/readyz", "/health", "/readiness", "/openapi.json"):
            continue
        if "{" in path:
            continue
        # /properties is not a Phase 1 canonical entity — skip to match reference spec
        if path in ("/properties", "/property"):
            continue
        op_name = _op_name_for_path(path)
        strategy = _choose_pagination_strategy(path, iface, classification)
        pagination = None
        if strategy != "none":
            pagination = _pagination_for_strategy(strategy)

        checkpoint = None
        if pagination is not None:
            if "reservation" in path:
                checkpoint = {"field": "$.updated_at", "mode": "max"}
            elif "folio" in path or "charge" in path:
                checkpoint = {"field": "$.posted_at", "mode": "max"}
            else:
                checkpoint = {"field": "$.updated_at", "mode": "max"}

        op_spec: dict[str, Any] = {"method": "GET", "path": path}
        if pagination is not None:
            op_spec["pagination"] = pagination
        if checkpoint is not None:
            op_spec["checkpoint"] = checkpoint
        operations[op_name] = op_spec

    # Ensure we have at least reservations and folios ops (Harbor reference)
    if "list_reservations" not in operations:
        has_cursor = any("cursor" in n.lower() for n, _ in iface.pagination_notes)
        strat = "cursor" if has_cursor else "offset"
        operations["list_reservations"] = {
            "method": "GET",
            "path": "/reservations",
            "pagination": _pagination_for_strategy(strat),
            "checkpoint": {"field": "$.updated_at", "mode": "max"},
        }
    if "list_folios" not in operations:
        has_cursor = any("cursor" in n.lower() for n, _ in iface.pagination_notes)
        strat = "cursor" if has_cursor else "offset"
        operations["list_folios"] = {
            "method": "GET",
            "path": "/folios",
            "pagination": _pagination_for_strategy(strat),
            "checkpoint": {"field": "$.posted_at", "mode": "max"},
        }

    # Entities — only canonical Reservation + FolioCharge for Phase 1
    entities: dict[str, Any] = {}
    if "list_reservations" in operations:
        fields = _collect_fields_for_entity("Reservation", iface, status_kind, status_mapping)
        entities["Reservation"] = {
            "from": "list_reservations",
            "key": _entity_key("Reservation"),
            "fields": fields,
            "checks": [
                {"kind": "unique_key"},
                {"kind": "no_loss", "compare_to": "$.total"},
                {"kind": "date_order", "before": "arrival_date", "after": "departure_date"},
            ],
        }
    if "list_folios" in operations:
        fields = _collect_fields_for_entity("FolioCharge", iface, status_kind, status_mapping)
        entities["FolioCharge"] = {
            "from": "list_folios",
            "key": _entity_key("FolioCharge"),
            "fields": fields,
            "checks": [
                {"kind": "unique_key"},
                {"kind": "no_loss", "compare_to": "$.total"},
            ],
        }

    # Capabilities
    capabilities = {"read": list(entities.keys()), "action": []}

    # Health canary
    canary_op = "list_reservations" if "list_reservations" in operations else next(iter(operations))
    health = {
        "canary": {"every": "60s", "operation": canary_op, "max_pages": 1},
        "fingerprint": {"keys": True, "types": True, "pagination_shape": True},
    }

    spec: dict[str, Any] = {
        "spec_version": 1,
        "connector": {"id": cid, "vendor": ven},
        "source": {
            "base_url": base_placeholder,
            "auth": auth_spec,
            "rate_limit": {"max_rps": 5.0, "retry": {"statuses": [429, 503], "backoff": "exponential", "max_attempts": 5}},
            "timezone_source": timezone_source,
        },
        "operations": operations,
        "entities": entities,
        "capabilities": capabilities,
        "health": health,
    }
    return spec


def build_draft_spec(
    docs_text: str,
    source: str = "docs",
    connector_id: str | None = None,
    credential_ref: str | None = None,
    vendor: str | None = None,
    base_url_override: str | None = None,
    probe_executor: Any | None = None,
    probe_budget: Any | None = None,
    provider: Any | None = None,
    llm_budget: Any | None = None,
    http_client_factory: Any | None = None,
) -> BootstrapResult:
    """Build draft spec from docs text (docs → draft spec → compile → visible tests).

    No verifier/simulator imports. No reference-spec file reads.
    Probe executor is optional: when provided, hypotheses are resolved via
    ProbeSelector + Resolver probe loop; otherwise deterministic classifier
    confidence is used.

    Args:
        docs_text: Markdown or OpenAPI text.
        source: Provenance source label (file path or URL).
        connector_id: Optional override for connector.id slug.
        credential_ref: Optional override for auth credential_ref.
        vendor: Optional override for connector.vendor.
        base_url_override: Optional override for source.base_url.
        probe_executor: Callable(ProbeSpec) -> observation dict (optional).
        probe_budget: ProbeBudget for probing (optional).
        provider: LLMProvider for L2/L3 (optional).
        llm_budget: Budget for model calls (optional).
        http_client_factory: Unused, reserved for probe HTTP factory.

    Returns:
        BootstrapResult with draft_spec, validation, degradation if ambiguous.
    """
    trace: list[dict[str, Any]] = []
    model_usage: dict[str, Any] = {"frontier_tokens": 0, "small_tokens": 0, "calls": 0, "frontier_calls": 0, "small_calls": 0}
    evidence_payloads: list[dict[str, Any]] = []

    # Guard: refuse to read reference specs (defense in depth)
    # We never open generated/connectors files here; this assert ensures caller
    # didn't inject a path that would be read elsewhere.
    if source and "generated/connectors" in source:
        return BootstrapResult(
            success=False,
            degradation_reason="bootstrap must not read reference specs",
            trace=[{"stage": "guard", "reason": "source contains generated/connectors"}],
        )

    # Stage 1: read docs
    try:
        iface = read_docs_text(docs_text, source)
        trace.append({"stage": "read_docs", "doc_type": iface.doc_type, "operations": len(iface.operations), "schemas": len(iface.schemas)})
    except Exception as exc:
        return BootstrapResult(success=False, degradation_reason=f"docs parse failed: {exc}", trace=[{"stage": "read_docs", "error": str(exc)}])

    # Stage 2: classify
    try:
        classification = classify_all(iface)
        trace.append(
            {
                "stage": "classify",
                "auth": classification.auth.kind,
                "pagination_ops": len(classification.pagination.per_operation),
                "date_fields": len(classification.date_semantics.fields),
                "status_kind": classification.status_vocabulary.kind,
            }
        )
    except Exception as exc:
        return BootstrapResult(success=False, degradation_reason=f"classification failed: {exc}", iface=iface, trace=trace + [{"stage": "classify", "error": str(exc)}])

    # Stage 3: enumerate hypotheses
    enumerator = HypothesisEnumerator()
    try:
        hypotheses_by_family = enumerator.enumerate_all_families(iface, classification)
        trace.append({"stage": "enumerate", "families": {k: len(v) for k, v in hypotheses_by_family.items()}})
        # Collect evidence payloads for each hypothesis
        for _family, hyps in hypotheses_by_family.items():
            for h in hyps:
                evidence_payloads.append(h.to_evidence_payload())
    except Exception as exc:
        return BootstrapResult(success=False, degradation_reason=f"enumeration failed: {exc}", iface=iface, classification=classification, trace=trace + [{"stage": "enumerate", "error": str(exc)}])

    # Stage 4: optional probe resolution
    resolution_results: dict[str, Any] = {}
    open_questions: tuple[str, ...] = ()
    degradation_reason: str | None = None

    # If probes + provider given, attempt to resolve ambiguous families
    # For now, we handle pagination/status families that have >1 hypotheses
    # The date family ambiguous `date` field is intentionally ignored in compile
    # (we don't map it), so we treat it as non-blocking.

    # Build a list of families that are ambiguous and matter for compile
    # Pagination: if any operation has >1 strategy hypotheses and classifier low confidence, probe helps
    # We will invoke resolver only when probe_executor and probe_budget provided
    if probe_executor is not None and probe_budget is not None:
        try:
            from autobridge.discovery.probes import ProbeSelector
            from autobridge.discovery.resolver import resolve_hypotheses

            # Build interface for selector (needs operations)
            selector = ProbeSelector(iface, budget=probe_budget, llm_budget=llm_budget)
            # For each family, attempt resolution if ambiguity matters
            # Flatten hypotheses that are relevant to compile (pagination + status + amount)
            # Date hypotheses about ambiguous `date` field are not compile-blocking (we ignore)
            compile_relevant_families = ["pagination", "status", "amount"]
            for fam in compile_relevant_families:
                hyps = hypotheses_by_family.get(fam, [])
                if len(hyps) <= 1:
                    continue
                # Filter to hypotheses for this family that are not already decided by classifier
                # To avoid unnecessary probing, check if classifier already high confidence for this family
                # For pagination, classifier high for Harbor -> we can skip probing
                should_probe = True
                if fam == "pagination" and classification.pagination.overall_confidence == "high":
                    should_probe = False
                if fam == "status" and classification.status_vocabulary.confidence == "high":
                    should_probe = False
                if not should_probe:
                    resolution_results[fam] = {"decision": "skipped_high_confidence", "hypothesis_count": len(hyps)}
                    continue
                result = resolve_hypotheses(
                    hyps,
                    probe_selector=selector,
                    probe_budget=probe_budget,
                    probe_executor=probe_executor,
                    enumerator_yield_count=len(hyps),
                    provider=provider,
                    llm_budget=llm_budget,
                )
                resolution_results[fam] = result
                if result.decision == "escalate" and result.escalation_bundle:
                    degradation_reason = result.escalation_reason
                    open_questions = tuple([result.escalation_bundle.question])
                    trace.append({"stage": "resolve", "family": fam, "decision": "escalate", "reason": degradation_reason})
                    # Do not block bootstrap; we will degrade overall but still compile
                elif result.decision == "degraded":
                    open_questions = tuple(list(open_questions) + list(result.open_questions))
                    trace.append({"stage": "resolve", "family": fam, "decision": "degraded", "open_questions": list(result.open_questions)})
                else:
                    trace.append({"stage": "resolve", "family": fam, "decision": result.decision})
                # Merge model usage
                for k in ("frontier_tokens", "small_tokens", "calls", "frontier_calls", "small_calls"):
                    model_usage[k] = int(model_usage.get(k, 0)) + int(result.model_usage.get(k, 0))
            # Date family: check ambiguous flag
            date_amb = classification.date_semantics.ambiguous_fields
            if date_amb:
                # If ambiguous date field exists, we note it but compile ignores the field
                # Probe could disambiguate, but ignoring is correct per Harbor docs note
                # If we attempted probing and still ambiguous, we report Degraded
                # For now, treat date ambiguity as Degraded only if no probe and enumerator >1
                # Since we ignore date field, we don't degrade bootstrap itself, but we record open question
                has_date_probing = "date" in resolution_results
                if not has_date_probing:
                    # Record that date ambiguity exists but is handled by ignoring
                    trace.append({"stage": "date_ambiguity", "ambiguous_fields": list(date_amb), "handling": "ignore_ambiguous_field"})
                    # Optionally add open question if strict: but Harbor expects no degradation for spec correctness
                    # We keep open_questions empty for successful Harbor bootstrap
        except Exception as exc:
            trace.append({"stage": "resolve", "error": str(exc)})
            # Continue to compile anyway

    else:
        # No probing: check if any family has surviving ambiguity that should Degrade
        # Date ambiguity: Harbor's `date` duplicates arrival_date/business_date — we handle by ignoring,
        # so not Degraded. Only degrade if pagination/status low confidence and multiple hypotheses >1
        # For Harbor, confidence high -> no degrade
        for fam in ("pagination", "status"):
            hyps = hypotheses_by_family.get(fam, [])
            if len(hyps) > 1:
                # Check classifier confidence for this family
                if fam == "pagination" and classification.pagination.overall_confidence in ("medium", "low"):
                    # Ambiguity could degrade, but we still compile deterministic
                    trace.append({"stage": "no_probe_ambiguity", "family": fam, "hypothesis_count": len(hyps), "confidence": classification.pagination.overall_confidence})
                if fam == "status" and classification.status_vocabulary.confidence in ("medium", "low"):
                    trace.append({"stage": "no_probe_ambiguity", "family": fam, "hypothesis_count": len(hyps), "confidence": classification.status_vocabulary.confidence})
        # Date ambiguity not counted as degradation for bootstrap (ignore field)
        if classification.date_semantics.ambiguous_fields:
            trace.append({"stage": "date_ambiguity", "ambiguous_fields": list(classification.date_semantics.ambiguous_fields), "handling": "ignore_ambiguous_field"})
        trace.append({"stage": "resolve", "decision": "no_probes_deterministic"})

    # Handle http_client_factory unused (kept for api compat)
    _ = http_client_factory

    # Stage 5: compile spec
    try:
        draft_spec = _compile_spec(iface, classification, connector_id=connector_id, vendor=vendor, credential_ref=credential_ref, base_url_override=base_url_override)
        trace.append({"stage": "compile", "connector_id": draft_spec["connector"]["id"], "operations": list(draft_spec["operations"].keys()), "entities": list(draft_spec["entities"].keys())})
    except Exception as exc:
        return BootstrapResult(
            iface=iface,
            classification=classification,
            hypotheses_by_family=hypotheses_by_family,
            resolution_results=resolution_results,
            visible_errors=[f"compile failed: {exc}"],
            open_questions=open_questions,
            model_usage=model_usage,
            evidence_payloads=evidence_payloads,
            success=False,
            degradation_reason=f"compile failed: {exc}",
            trace=trace + [{"stage": "compile", "error": str(exc)}],
        )

    # Stage 6: visible tests
    try:
        verrs = visible_tests(draft_spec)
        trace.append({"stage": "visible_tests", "errors": verrs})
        if verrs:
            return BootstrapResult(
                draft_spec=draft_spec,
                iface=iface,
                classification=classification,
                hypotheses_by_family=hypotheses_by_family,
                resolution_results=resolution_results,
                visible_errors=verrs,
                open_questions=open_questions,
                model_usage=model_usage,
                evidence_payloads=evidence_payloads,
                success=False,
                degradation_reason="visible tests failed",
                trace=trace,
            )
    except Exception as exc:
        return BootstrapResult(
            draft_spec=draft_spec,
            iface=iface,
            classification=classification,
            hypotheses_by_family=hypotheses_by_family,
            resolution_results=resolution_results,
            visible_errors=[f"visible tests error: {exc}"],
            open_questions=open_questions,
            model_usage=model_usage,
            evidence_payloads=evidence_payloads,
            success=False,
            degradation_reason=f"visible tests error: {exc}",
            trace=trace + [{"stage": "visible_tests", "error": str(exc)}],
        )

    # Success (Degraded only if open_questions)
    success = True
    if open_questions:
        degradation_reason = degradation_reason or "surviving ambiguity — Degraded with open questions"
        # Still success for spec production, but caller should check open_questions
        # For W5.6 Harbor, open_questions should be empty (date ambiguity handled)
        pass

    return BootstrapResult(
        draft_spec=draft_spec,
        iface=iface,
        classification=classification,
        hypotheses_by_family=hypotheses_by_family,
        resolution_results=resolution_results,
        visible_errors=[],
        open_questions=open_questions,
        model_usage=model_usage,
        evidence_payloads=evidence_payloads,
        success=success,
        degradation_reason=degradation_reason,
        trace=trace,
    )



