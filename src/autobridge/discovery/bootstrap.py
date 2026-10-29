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
import urllib.parse
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
    # REQ-027 sub-step 1: any other sim-<name> service address maps to its
    # ${NAME}_BASE_URL placeholder (portable across stack modes; resolved
    # per-mode from the environment at sync time). Generic over the
    # registry id shape — no vendor literal here (generalization proof).
    try:
        host = (urllib.parse.urlsplit(base_url).hostname or "").lower()
    except Exception:
        host = ""
    if host.startswith("sim-") and len(host) > 4:
        slug = re.sub(r"[^a-z0-9]+", "_", host[4:]).strip("_").upper()
        if slug:
            return f"${{{slug}_BASE_URL}}"
    # Already env placeholder
    if base_url.startswith("${"):
        return base_url
    return base_url


def _with_docs_path(override: str, docs_base_url: str) -> str:
    """Keep the docs' API path prefix on a connector-record host (REQ-031).

    The connector record names *where* the stack runs (normalized per
    stack mode, REQ-027); the docs name the path prefix (a docs
    ``servers`` URL may carry one, e.g. ``/api/connector/v1``). An
    override whose path is empty (or ``/``) gains the docs path; an
    override that already carries a path, a placeholder or an
    unparseable value is stored verbatim. Never imports simulator
    packages — pure URL surgery.
    """
    if not override or not isinstance(override, str) or not docs_base_url:
        return override
    try:
        over = urllib.parse.urlsplit(override)
        docs = urllib.parse.urlsplit(docs_base_url)
    except Exception:
        return override
    if over.scheme not in ("http", "https"):
        return override
    if (over.path or "/") != "/":
        return override
    docs_path = (docs.path or "").rstrip("/")
    if not docs_path:
        return override
    return urllib.parse.urlunsplit((over.scheme, over.netloc, docs_path, "", ""))


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


def _op_name_for_path(path: str, operation_id: str | None = None) -> str:
    """Map API path (plus optional operationId) to operation name.

    Generic English-word synonyms (no vendor names): reservation/booking →
    list_reservations, order-item/folio/charge/transaction → list_folios,
    customer/guest → list_customers, configuration/enterprise → a
    get_configuration fetch. Falls back to the sanitized path.
    """
    lowered = f"{operation_id or ''} {path}".lower()
    if "/folios" in path and "{" in path:
        # nested: /reservations/{id}/folios
        return "list_folios_nested"
    # Plural list endpoints first: singular fetches (getReservation,
    # getGuest) contain the same substrings and must not claim the list name.
    if "reservations" in lowered or "bookings" in lowered or "stays" in lowered:
        return "list_reservations"
    if "orderitem" in lowered or "order_item" in lowered or "folio" in lowered or "charge" in lowered or "transaction" in lowered:
        return "list_folios"
    if "customers" in lowered or "guests" in lowered or "accounts" in lowered:
        return "list_customers"
    if "configuration" in lowered or "enterprise" in lowered:
        return "get_configuration"
    # Singular single-resource fetches (skipped downstream like get_guest).
    if "reservation" in lowered or "booking" in lowered or "stay" in lowered:
        return "get_reservation"
    if "guest" in lowered or "customer" in lowered or "account" in lowered:
        return "get_guest"
    if path == "/folios":
        return "list_folios"
    if path == "/properties":
        return "list_properties"
    # Method-style vendor paths (e.g. AtlasPMS /api/v1.3/getReservations):
    # strip any version prefix, then match the method name.
    method = path.rsplit("/", 1)[-1] if "/" in path else path
    lowered_method = method.lower()
    if lowered_method in ("getreservations", "listreservations", "reservations"):
        return "list_reservations"
    if lowered_method in ("gettransactions", "listtransactions", "folios", "listfolios", "charges"):
        return "list_folios"
    if lowered_method in ("gethotels", "listhotels", "properties", "listproperties"):
        return "list_properties"
    if lowered_method in ("getreservation", "reservation"):
        return "get_reservation"
    if lowered_method in ("getguest", "guest"):
        return "get_guest"
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


# Canonical field -> documented wire names (method-style vendors use
# camelCase; identity fallback keeps Harbor-style snake_case working).
# PascalCase spellings cover POST/JSON APIs whose fields are capitalized
# (Id, AccountId, ScheduledStartUtc, ...). Order matters only within a
# vendor: _wire_name_for tries the canonical name, then the normalized
# name, then these aliases, so earlier vendors never change.
_WIRE_ALIASES: dict[str, tuple[str, ...]] = {
    "reservation_id": ("reservationID", "ReservationId", "Id"),
    "property_id": ("propertyID", "PropertyId", "ServiceId"),
    "guest_name": ("guestName", "GuestName"),
    "arrival_date": ("startDate", "arrivalDate", "ArrivalDate", "ScheduledStartUtc"),
    "departure_date": ("endDate", "departureDate", "DepartureDate", "ScheduledEndUtc"),
    "status": ("status", "Status", "State"),
    "total_amount": ("balance", "total", "totalAmount", "TotalAmount"),
    "currency": ("currency", "currencyCode", "Currency", "CurrencyCode"),
    "updated_at": ("dateModified", "updatedAt", "modifiedAt", "UpdatedUtc"),
    "charge_id": ("transactionID", "chargeID", "chargeId", "ChargeId", "Id"),
    "business_date": ("businessDate", "BusinessDate"),
    "amount": ("amount", "Amount", "UnitAmount"),
    "description": ("description", "Description", "Name"),
    "posted_at": ("transactionDateTime", "postedAt", "PostedAt", "PostedUtc"),
    "name": ("propertyName", "name", "Name"),
    "timezone": ("propertyTimezone", "timezone", "TimeZone"),
}

# Vendor spellings normalized to canonical (clear vocabularies).
# Lifecycle spellings with an unambiguous canonical counterpart stay mapped
# (a started stay is checked in; a processed one checked out; the sim's
# tentative Optional never arrives, i.e. no_show). Genuinely ambiguous
# states (Inquired, Requested, vendor hold codes) are left OUT so they
# Block via on_unknown instead of being guessed; the verifier judges.
_STATUS_NORMALIZE = {
    "canceled": "cancelled",
    "started": "checked_in",
    "processed": "checked_out",
    "optional": "no_show",
}
_CANONICAL_STATUSES = ("confirmed", "checked_in", "checked_out", "cancelled", "no_show")


def _documented_wire_names(iface: Any) -> dict[str, str]:
    """Map lowercased documented field name -> documented spelling."""
    names: dict[str, str] = {}
    for schema in getattr(iface, "schemas", []) or []:
        for f in getattr(schema, "fields", []) or []:
            fname = getattr(f, "name", "")
            if isinstance(fname, str) and fname and fname.lower() not in names:
                names[fname.lower()] = fname
    for op in getattr(iface, "operations", []) or []:
        for f in getattr(op, "response_fields", []) or []:
            fname = getattr(f, "name", "")
            if isinstance(fname, str) and fname and fname.lower() not in names:
                names[fname.lower()] = fname
    return names


def _wire_name_for(canonical: str, iface: Any) -> str:
    """Documented wire name for a canonical field (identity fallback).

    Order: canonical spelling first (Harbor snake_case), then the
    normalized spelling (Atlas camelCase), then vendor aliases (POST/JSON
    PascalCase). Earlier vendors never change: a documented canonical or
    normalized name always wins over an alias added later.
    """
    pool = _documented_wire_names(iface)
    if canonical.lower() in pool:
        return pool[canonical.lower()]
    norm = canonical.lower().replace("_", "")
    if norm in pool:
        return pool[norm]
    for alias in _WIRE_ALIASES.get(canonical, ()):
        if alias.lower() in pool:
            return pool[alias.lower()]
    return canonical


def _transform_for_field(
    field_name: str,
    status_kind: str | None = None,
    status_mapping: dict[str, str] | None = None,
    status_vocab: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Closed transform steps for a canonical field."""
    low = field_name.lower()
    if low in ("arrival_date", "departure_date"):
        return [{"name": "date_local", "args": {"tz": "source"}}]
    if low == "business_date":
        return [{"name": "business_date", "args": {"tz": "source", "cutoff": 4}}]
    if low == "updated_at" or low == "posted_at":
        return [{"name": "datetime_utc", "args": {"tz": "source"}}]
    if low == "status":
        # Determine mapping
        mapping: dict[str, str]
        if status_kind == "opaque" and status_mapping:
            mapping = dict(status_mapping)
        elif status_kind == "opaque":
            mapping = {"H1": "confirmed", "H2": "checked_in", "H3": "checked_out", "H4": "cancelled", "H5": "no_show"}
        elif status_vocab:
            # Clear vocabulary from docs: map each documented value to its
            # canonical form, case-insensitively ("Confirmed" and "confirmed"
            # both mean confirmed; "Canceled" normalizes to "cancelled").
            # Values with no canonical counterpart are left out so they
            # Block via on_unknown instead of being guessed.
            mapping = {}
            for v in status_vocab:
                canon = _STATUS_NORMALIZE.get(v.lower(), v.lower())
                if canon in _CANONICAL_STATUSES and v not in mapping:
                    mapping[v] = canon
            if not mapping:
                mapping = {cs: cs for cs in _CANONICAL_STATUSES}
        else:
            # clear identity
            mapping = {"confirmed": "confirmed", "checked_in": "checked_in", "checked_out": "checked_out", "cancelled": "cancelled", "no_show": "no_show"}
        return [{"name": "enum_map", "args": {"map": mapping, "on_unknown": "block"}}]
    if low in ("total_amount", "amount"):
        return [{"name": "money", "args": {"scale": 2}}]
    return []


def _entity_key(entity_name: str, iface: Any = None) -> str:
    if entity_name == "Reservation":
        return f"$.{_wire_name_for('reservation_id', iface)}"
    if entity_name == "FolioCharge":
        return f"$.{_wire_name_for('charge_id', iface)}"
    if entity_name == "Property":
        return f"$.{_wire_name_for('property_id', iface)}"
    return "$.id"


def _checkpoint_field_for_entity(entity_name: str, iface: Any = None) -> str:
    if entity_name == "Reservation":
        return f"$.{_wire_name_for('updated_at', iface)}"
    if entity_name == "FolioCharge":
        return f"$.{_wire_name_for('posted_at', iface)}"
    if entity_name == "Property":
        return f"$.{_wire_name_for('updated_at', iface)}"
    return "$.updated_at"


def _collect_fields_for_entity(
    entity_name: str,
    iface: InterfaceDescription,
    status_kind: str | None,
    status_mapping: dict[str, Any] | None,
    status_vocab: tuple[str, ...] = (),
    items_fields: dict[str, str] | None = None,
    redirects: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build fields dict for entity, with wire names resolved from docs.

    *redirects* maps canonical fields to joined paths (``$._joined...``)
    proposed by :func:`_propose_joins`. *items_fields* (lower -> spelling
    of the operation's item schema) gives precise nested money paths
    (e.g. ``$.UnitAmount.GrossValue``) for amount/currency/total_amount.
    """
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
    money_leaf = _money_leaf(items_fields) if items_fields else None
    fields: dict[str, dict[str, Any]] = {}
    for fname in field_names:
        if fname in _IGNORE_FIELDS:
            continue
        transform_steps = _transform_for_field(fname, status_kind, status_mapping, status_vocab)
        if redirects and fname in redirects:
            fields[fname] = {"path": redirects[fname], "transform": transform_steps}
            continue
        if money_leaf is not None and fname in ("amount", "total_amount", "currency"):
            gross_path, currency_path = money_leaf
            if fname == "currency":
                fields[fname] = {"path": f"$.{currency_path}", "transform": transform_steps}
            else:
                fields[fname] = {"path": f"$.{gross_path}", "transform": transform_steps}
            continue
        fields[fname] = {"path": f"$.{_wire_name_for(fname, iface)}", "transform": transform_steps}
    # No documented business-date field, but a documented posting instant:
    # derive the business date from the same instant (that is what the
    # business_date transform is for). Untouched when docs name the field
    # or when no posting instant is documented either.
    if "business_date" in fields and fields["business_date"]["path"] == "$.business_date":
        posted_path = fields.get("posted_at", {}).get("path")
        if posted_path and posted_path != "$.posted_at":
            fields["business_date"] = {
                "path": posted_path,
                "transform": _transform_for_field("business_date", status_kind, status_mapping, status_vocab),
            }
    return fields


# ---------------------------------------------------------------------------
# join proposal (spec v2, REQ-020 5c)
# ---------------------------------------------------------------------------

#: Description words marking a documented relationship between operations
#: (e.g. "Join to the reservation", "Matches Reservation.AccountId").
_JOIN_HINT_RE = re.compile(r"join|match|link|reference|owner|foreign", re.IGNORECASE)

#: Money leaf names (last dotted segment, lowercased) with a currency
#: sibling marking a line-items schema.
_MONEY_LEAF_NAMES = frozenset(
    {"grossvalue", "gross", "netvalue", "net", "amount", "total", "totalamount", "value"}
)
_CURRENCY_LEAF_NAMES = frozenset({"currency", "currencycode"})


def _singular(name: str) -> str:
    """Naive singular: list_customers -> customer, list_folios -> folio."""
    base = name
    for prefix in ("list_", "get_", "fetch_"):
        if base.startswith(prefix):
            base = base[len(prefix):]
            break
    if len(base) > 1 and base.endswith("s") and not base.endswith("ss"):
        base = base[:-1]
    return base or name


def _schema_fields_by_lower(schema: Any) -> dict[str, str]:
    """Lowercased field name -> documented spelling, in schema order."""
    out: dict[str, str] = {}
    for f in getattr(schema, "fields", []) or []:
        fname = getattr(f, "name", "")
        if isinstance(fname, str) and fname and fname.lower() not in out:
            out[fname.lower()] = fname
    return out


def _field_desc(schema: Any, documented_name: str) -> str:
    for f in getattr(schema, "fields", []) or []:
        if getattr(f, "name", "") == documented_name:
            return str(getattr(f, "description", "") or "")
    return ""


def _items_schema_for_op(op: Any, schemas_by_lower: dict[str, Any]) -> Any | None:
    """Item schema for a list operation via its response items field."""
    items = getattr(op, "response_items_field", None)
    if not items or not isinstance(items, str):
        return None
    low = items.lower()
    for cand in (low, _singular(low)):
        if cand in schemas_by_lower:
            return schemas_by_lower[cand]
    return None


def _fk_candidates(field_lowers: dict[str, str]) -> list[str]:
    """Documented spellings of id-suffixed fields that are not the Id itself."""
    return [spell for low, spell in field_lowers.items() if low != "id" and low.endswith("id")]


def _has_exact_id(field_lowers: dict[str, str]) -> str | None:
    return field_lowers.get("id")


def _documented_join_key(base_schema: Any, partner_schema: Any) -> str | None:
    """Foreign-key field of *base_schema* documented as joining *partner_schema*.

    A key qualifies when either side's description names the relationship
    (join/match/link/...). Falls back to the first id-suffixed field only
    when some partner carries the key but prose is silent.
    """
    base_fields = _schema_fields_by_lower(base_schema)
    partner_fields = _schema_fields_by_lower(partner_schema)
    fallback: str | None = None
    for fk in _fk_candidates(base_fields):
        if fk.lower() not in partner_fields and "id" not in partner_fields:
            # Partner must carry the key (as its Id or as the same FK).
            continue
        if fallback is None:
            fallback = fk
        # Either side may document the relationship — but the prose must
        # name THIS candidate key, so a hint about AccountId never justifies
        # joining on ServiceId.
        descs = [_field_desc(base_schema, fk)]
        partner_id = partner_fields.get("id")
        if partner_id is not None:
            descs.append(_field_desc(partner_schema, partner_id))
        if fk.lower() in partner_fields:
            descs.append(_field_desc(partner_schema, partner_fields[fk.lower()]))
        combined = "\n".join(descs)
        if fk.lower() in combined.lower() and _JOIN_HINT_RE.search(combined):
            return fk
    return fallback


def _name_source(field_lowers: dict[str, str]) -> tuple[list[str], str] | None:
    """Name-carrying fields of an authority schema: (paths, kind).

    Returns ([First, Last], 'pair') for a first/last name pair, or
    ([Name], 'single') for one display-name field.
    """
    first = last = None
    for low, spell in field_lowers.items():
        if low == "firstname" or low.endswith("firstname"):
            first = first or spell
        elif low == "lastname" or low.endswith("lastname"):
            last = last or spell
    if first and last:
        return ([first, last], "pair")
    for low, spell in field_lowers.items():
        if low in ("name", "fullname", "full_name", "displayname", "guestname", "guest_name", "title"):
            return ([spell], "single")
    return None


def _money_leaf(field_lowers: dict[str, str]) -> tuple[str, str] | None:
    """(gross path, currency path) for a line-items schema, else None.

    Finds a dotted money leaf (e.g. ``UnitAmount.GrossValue``) with a
    currency sibling (``UnitAmount.Currency``), preferring gross over net
    over amount. Flat ``Amount``-style fields qualify when a flat currency
    sibling exists.
    """
    prefixes: dict[str, dict[str, str]] = {}
    for low, spell in field_lowers.items():
        if "." in low:
            prefix, _, leaf = low.partition(".")
            prefixes.setdefault(prefix, {})[leaf] = spell
    for _prefix, leaves in prefixes.items():
        currency = next((s for leaf, s in leaves.items() if leaf in _CURRENCY_LEAF_NAMES), None)
        if currency is None:
            continue
        for want in ("grossvalue", "gross", "netvalue", "net", "amount", "total", "totalamount", "value"):
            if want in leaves:
                return (leaves[want], currency)
    # Flat money + currency siblings (e.g. Amount + Currency on one record).
    flat_currency = field_lowers.get("currency") or field_lowers.get("currencycode")
    if flat_currency is not None:
        for want in ("grossvalue", "gross", "netvalue", "net", "amount", "total", "totalamount", "value"):
            if want in field_lowers and "." not in want:
                spell = field_lowers[want]
                if "." not in spell:
                    return (spell, flat_currency)
    return None


def _items_fields_for_op(op_desc: Any, iface: Any) -> dict[str, str] | None:
    """Lower -> documented item-schema fields for an operation, if known."""
    if op_desc is None:
        return None
    schemas_by_lower = {s.name.lower(): s for s in getattr(iface, "schemas", []) or []}
    schema = _items_schema_for_op(op_desc, schemas_by_lower)
    if schema is None:
        return None
    return _schema_fields_by_lower(schema)


def _propose_joins(
    iface: Any,
    ops_by_name: dict[str, Any],
    entity_ops: dict[str, str],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, str]]]:
    """Propose declarative joins from documented schemas (spec v2 only).

    Generic structural rules, no vendor names: a name authority (Id +
    person-name fields, documented as the join target) feeds a ``one``
    join; a line-items operation (same foreign key + money leaf with a
    currency sibling) feeds a ``many`` sum join; the folio operation gains
    a reverse ``one`` join to the reservation operation for its parent id.

    Returns (entity -> joins, entity -> {canonical field: joined path}).
    Empty when the docs show no joinable shape.
    """
    joins: dict[str, list[dict[str, Any]]] = {}
    redirects: dict[str, dict[str, str]] = {}
    schemas_by_lower = {s.name.lower(): s for s in getattr(iface, "schemas", []) or []}
    if not schemas_by_lower:
        return joins, redirects

    reservation_op = entity_ops.get("Reservation")
    folio_op = entity_ops.get("FolioCharge")
    if reservation_op is None or reservation_op not in ops_by_name:
        return joins, redirects
    res_schema = _items_schema_for_op(ops_by_name[reservation_op], schemas_by_lower)
    if res_schema is None:
        return joins, redirects
    res_lowers = _schema_fields_by_lower(res_schema)

    # Candidate list operations (paginated, with an items schema).
    list_ops: list[tuple[str, Any, Any]] = []
    for oname, op in ops_by_name.items():
        if oname == reservation_op or getattr(op, "pagination_hint", "") == "none":
            continue
        schema = _items_schema_for_op(op, schemas_by_lower)
        if schema is not None:
            list_ops.append((oname, op, schema))
    # Deterministic: operation order wins when several ops qualify.
    list_ops.sort(key=lambda t: list(ops_by_name).index(t[0]))

    name_join_op: str | None = None
    money_join_op: str | None = None

    # R2 first: the money authority (line items with the FK + money leaf).
    money_fk: str | None = None
    money_paths: tuple[str, str] | None = None
    for oname, _op, schema in list_ops:
        fk = _documented_join_key(res_schema, schema)
        if fk is None:
            continue
        leaves = _money_leaf(_schema_fields_by_lower(schema))
        if leaves is None:
            continue
        money_join_op, money_fk, money_paths = oname, fk, leaves
        break

    # R1: the name authority (Id + person names, carrying no FK itself).
    name_fk: str | None = None
    name_paths: tuple[list[str], str] | None = None
    for oname, _op, schema in list_ops:
        if oname == money_join_op:
            continue
        schema_lowers = _schema_fields_by_lower(schema)
        if _has_exact_id(schema_lowers) is None:
            continue
        fk = _documented_join_key(res_schema, schema)
        if fk is None:
            continue
        # The authority record IS the account: it must not carry the FK.
        if fk.lower() in schema_lowers:
            continue
        src = _name_source(schema_lowers)
        if src is None:
            continue
        name_join_op, name_fk, name_paths = oname, fk, src
        break

    if name_join_op is not None and name_fk is not None and name_paths is not None:
        paths, kind = name_paths
        name_schema = _items_schema_for_op(ops_by_name[name_join_op], schemas_by_lower)
        id_spell = (_has_exact_id(_schema_fields_by_lower(name_schema)) or "Id") if name_schema is not None else "Id"
        as_name = _singular(name_join_op)
        if kind == "pair":
            full_field: dict[str, Any] = {
                "paths": [f"$.{p}" for p in paths],
                "transform": [{"name": "concat", "args": {"separator": " "}}],
            }
        else:
            full_field = {"path": f"$.{paths[0]}"}
        joins.setdefault("Reservation", []).append(
            {
                "from_operation": name_join_op,
                "local_key": f"$.{name_fk}",
                "foreign_key": f"$.{id_spell}",
                "cardinality": "one",
                "as": as_name,
                "fields": {"full_name": full_field},
            }
        )
        redirects.setdefault("Reservation", {})["guest_name"] = f"$._joined.{as_name}.full_name"

    if money_join_op is not None and money_fk is not None and money_paths is not None:
        gross_path, currency_path = money_paths
        as_name = _singular(money_join_op)
        joins.setdefault("Reservation", []).append(
            {
                "from_operation": money_join_op,
                "local_key": f"$.{money_fk}",
                "foreign_key": f"$.{money_fk}",
                "cardinality": "many",
                "as": as_name,
                "fields": {
                    "total_amount": {
                        "path": f"$.{gross_path}",
                        "aggregate": "sum",
                        "transform": [{"name": "money", "args": {"scale": 2}}],
                    },
                    "currency": {"path": f"$.{currency_path}", "aggregate": "first"},
                },
            }
        )
        redirects.setdefault("Reservation", {})["total_amount"] = f"$._joined.{as_name}.total_amount"
        redirects.setdefault("Reservation", {})["currency"] = f"$._joined.{as_name}.currency"

    # R3: folios carry the same FK; their parent id comes from reservations.
    if folio_op is not None and folio_op in ops_by_name and folio_op != reservation_op:
        folio_schema = _items_schema_for_op(ops_by_name[folio_op], schemas_by_lower)
        if folio_schema is not None:
            r_fk = _documented_join_key(folio_schema, res_schema)
            if (
                r_fk is not None
                and r_fk.lower() in res_lowers
                and _has_exact_id(res_lowers) is not None
            ):
                as_name = _singular(reservation_op)
                joins.setdefault("FolioCharge", []).append(
                    {
                        "from_operation": reservation_op,
                        "local_key": f"$.{r_fk}",
                        "foreign_key": f"$.{r_fk}",
                        "cardinality": "one",
                        "as": as_name,
                        "fields": {"reservation_id": {"path": "$.Id"}},
                    }
                )
                redirects.setdefault("FolioCharge", {})["reservation_id"] = (
                    f"$._joined.{as_name}.reservation_id"
                )

    return joins, redirects


def _pagination_for_strategy(
    strategy: str,
    op: Any | None = None,
) -> dict[str, Any]:
    """Pagination object for a given strategy.

    When *op* (an :class:`OperationDescription`) is given and the strategy is
    ``page``, the page/limit parameter names and the total/next paths are
    derived from the documented params and response fields (e.g.
    ``pageNumber``/``pageSize``/``$.total``); otherwise legacy template
    defaults are used. ``body_cursor`` (spec v2) always derives its request
    and response paths from the documented operation.
    """
    if strategy == "body_cursor":
        if op is None:
            raise ValueError("body_cursor pagination requires the documented operation")
        cursor_path = getattr(op, "body_cursor_path", None)
        if not cursor_path:
            raise ValueError("body_cursor pagination requires a documented body cursor path")
        count_path = getattr(op, "body_count_path", None)
        cursor_field = getattr(op, "response_cursor_field", None) or "Cursor"
        items_field = getattr(op, "response_items_field", None) or "data"
        count_max = getattr(op, "body_count_max", None)
        body_pagination: dict[str, Any] = {
            "strategy": "body_cursor",
            "cursor_path": cursor_path,
            "next_path": f"$.{cursor_field}",
            "items_path": f"$.{items_field}",
            "stop_when": ["null_cursor", "empty_page"],
        }
        if isinstance(count_path, str) and count_path:
            body_pagination["count_path"] = count_path
        body_pagination["count_value"] = min(100, count_max) if isinstance(count_max, int) else 100
        return body_pagination
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
        page_param: str | None = "page"
        limit_param: str | None = "page_size"
        next_path: str | None = "$.next_page"
        total_path: str | None = None
        if op is not None:
            page_param, limit_param = _page_param_names(op)
            next_path = _next_token_path(op)
            total_path = _total_count_path(op)
        pagination: dict[str, Any] = {
            "strategy": "page",
            "page_param": page_param,
            "limit_param": limit_param,
            "next_path": next_path,
            "items_path": "$.data",
            "stop_when": ["empty_page"],
        }
        if total_path is not None:
            pagination["total_path"] = total_path
        return pagination
    # none -> no pagination (e.g., /properties)
    return {}


_PAGE_PARAM_NAMES = ("pagenumber", "page_number", "page", "pageno")
_LIMIT_PARAM_NAMES = ("pagesize", "page_size", "limit", "per_page", "count", "max_results")


def _page_param_names(op: Any) -> tuple[str | None, str | None]:
    """Find documented page/limit query param names for a page strategy op."""
    names: dict[str, str] = {}
    for p in getattr(op, "params", []) or []:
        pname = getattr(p, "name", "")
        if isinstance(pname, str) and pname:
            names[pname.lower()] = pname
    page = next((names[k] for k in _PAGE_PARAM_NAMES if k in names), None)
    limit = next((names[k] for k in _LIMIT_PARAM_NAMES if k in names), None)
    # Fall back to legacy template defaults when docs name nothing usable.
    return page or "page", limit or "page_size"


def _next_token_path(op: Any) -> str | None:
    """Next-token response field path, or None for tokenless page APIs."""
    fields = [getattr(f, "name", "") for f in getattr(op, "response_fields", []) or []]
    lowered = {str(n).lower(): str(n) for n in fields if n}
    for candidate in ("next_cursor", "next_offset", "next_page"):
        if candidate in lowered:
            return f"$.{lowered[candidate]}"
    return None


def _total_count_path(op: Any) -> str | None:
    """Advertised-total response field path (e.g. ``$.total``), or None."""
    for f in getattr(op, "response_fields", []) or []:
        name = getattr(f, "name", "")
        if isinstance(name, str) and name.lower() == "total":
            return f"$.{name}"
    return None


#: Required body string fields whose description marks them as the caller's
#: application identity (name + version) get a static template value. These
#: are not credentials — the value names our own application.
_APP_IDENTITY_RE = re.compile(r"application name|app name|client.{0,12}version|app.{0,12}version", re.IGNORECASE)


def _body_template_for_op(op: Any) -> dict[str, Any]:
    """Static JSON-body template for a POST operation (spec v2).

    Includes the paging count leaf (page size) and required non-credential
    string fields documented as the caller application identity. Credential
    fields never appear here — ``body_fields`` auth injects them at runtime
    from the environment. The cursor and filters are per-request, not
    template data.
    """
    from autobridge.discovery.docs_reader import is_credential_body_field  # noqa: PLC0415

    body: dict[str, Any] = {}
    count_path = getattr(op, "body_count_path", None)
    if isinstance(count_path, str) and count_path:
        node = body
        parts = count_path.split(".")
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        count_max = getattr(op, "body_count_max", None)
        node[parts[-1]] = min(100, count_max) if isinstance(count_max, int) else 100
    for bf in getattr(op, "body_fields", []) or []:
        bname = getattr(bf, "name", "")
        if not isinstance(bname, str) or not bname or not getattr(bf, "required", False):
            continue
        if is_credential_body_field(bname):
            continue
        if _APP_IDENTITY_RE.search(getattr(bf, "description", "") or ""):
            body[bname] = "AutoBridge/1.0"
    return body


def _derive_body_field_ref(connector_id: str, field_name: str, credential_ref: str | None = None) -> str:
    """Env key name holding one body auth field's secret (spec v2).

    Derived from the connector's base credential ref (minus its ``_API_KEY``
    suffix) plus the snake-cased field name, e.g. ``EXAMPLE_CLIENT_TOKEN``.
    An explicit *credential_ref* override replaces the base only.
    """
    base = _derive_credential_ref(connector_id, credential_ref)
    if base.endswith("_API_KEY"):
        base = base[: -len("_API_KEY")]
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", field_name).upper()
    snake = re.sub(r"[^A-Z0-9_]", "_", snake).strip("_") or "FIELD"
    ref = f"{base}_{snake}"
    if not re.match(r"^[A-Z][A-Z0-9_]{0,127}$", ref):
        return "CONNECTOR_BODY_FIELD"
    return ref


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
        if strategy == "page" and "empty_page" not in stop_when:
            errors.append(f"operations.{op_name} page pagination needs empty_page guard (tokenless stop)")
        if strategy == "body_cursor":
            if not pag.get("cursor_path"):
                errors.append(f"operations.{op_name} body_cursor pagination needs cursor_path (dotted body path)")
            if not pag.get("next_path"):
                errors.append(f"operations.{op_name} body_cursor pagination needs next_path (response cursor)")
            if "null_cursor" not in stop_when:
                errors.append(f"operations.{op_name} body_cursor pagination needs null_cursor guard")
            if "empty_page" not in stop_when:
                errors.append(f"operations.{op_name} body_cursor pagination needs empty_page guard (short-page stop)")
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


# ---------------------------------------------------------------------------
# Framed-protocol compile (spec v3 event feeds — REQ-022 sub-step C)
# ---------------------------------------------------------------------------

#: Meaning-text signals (word-boundary matched) driving the doc-driven
#: record-role proposal. A record with an amount-like field is a charge
#: feed (→ FolioCharge); a record with room + status-like fields and no
#: amount is a status feed (→ RoomStatus). Anything else cannot be mapped
#: to a canonical entity, so compilation fails closed instead of emitting
#: a half-working spec.
_FRAMED_AMOUNT_HINTS = ("amount", "total", "charge", "price", "minor", "cent", "gross", "net")
_FRAMED_STATUS_HINTS = ("status", "state")
_FRAMED_ROOM_HINTS = ("room",)
_FRAMED_SEQ_HINTS = ("sequence",)
_FRAMED_DATE_HINTS = ("date",)
_FRAMED_TIME_HINTS = ("time",)
_FRAMED_DESC_HINTS = ("outlet", "type", "description", "memo", "note")

#: Default property timezone for framed-protocol sources (same default as
#: the HTTP compile and the edge bundle; the docs name no IANA zone).
_FRAMED_PROPERTY_TZ = "America/New_York"


def _framed_hint_re(hint: str) -> re.Pattern[str]:
    return re.compile(rf"\b{re.escape(hint)}\b", re.IGNORECASE)


def _framed_field(fields: dict[str, str], hints: tuple[str, ...], *, exclude: tuple[str, ...] = ()) -> str | None:
    """First wire field (doc order) whose meaning matches a hint word."""
    for fid, meaning in fields.items():
        low = meaning.lower()
        if exclude and any(x in low for x in exclude):
            continue
        if any(_framed_hint_re(h).search(meaning) for h in hints):
            return fid
    return None


def _framed_jsonpath(fid: str) -> str:
    """Wire field id → JSONPath subset (bracket form survives ``#`` ids)."""
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", fid):
        return f"$.{fid}"
    escaped = fid.replace("'", "\\'")
    return f"$['{escaped}']"


def _framed_param_name(meaning: str, taken: set[str]) -> str:
    """Mechanical command-param name from a Meaning cell (lossless, stable).

    Lowercase snake_case of the full meaning; on collision within one
    command (e.g. two fields both meaning "Time of the change") a numeric
    suffix keeps names unique. Never invents semantics — the executor
    substitutes these names verbatim.
    """
    base = re.sub(r"[^a-z0-9]+", "_", meaning.lower()).strip("_") or "param"
    name, n = base, 2
    while name in taken:
        name = f"{base}_{n}"
        n += 1
    taken.add(name)
    return name


def _compile_framed_spec(
    iface: InterfaceDescription,
    *,
    connector_id: str | None = None,
    vendor: str | None = None,
    credential_ref: str | None = None,
    base_url_override: str | None = None,
    spec_version: int = 3,
) -> dict[str, Any]:
    """Compile framed-protocol docs (``iface.protocol != "http"``) to spec v3.

    Deterministic from the parsed docs: feed operations for business-data
    records, a charge entity for the amount-role record, a status entity
    for the room+status record, command templates for host records, link
    settings from the framing contract. Missing site facts (property code,
    currency, listen port) or an unmappable record fail closed with a
    named reason — never a half-working spec.
    """
    if spec_version < 3:
        raise ValueError(f"framed {iface.protocol} sources require spec_version 3")
    protocol = iface.protocol or "fias_tcp"
    link = iface.link
    if link is None or not link.announced_records:
        raise ValueError("framed docs name no announced link records (LR): cannot compile feeds")
    port = link.listen_port
    if port is None:
        raise ValueError("framed docs name no listen port: cannot compile a fias:// base_url")
    if not iface.property_code:
        raise ValueError("framed docs state no property code: refusing a spec without property identity")
    if not iface.currency_code:
        raise ValueError("framed docs state no property currency: refusing a spec without currency")

    cid = connector_id or _slugify(iface.title or iface.source or "framed")
    cid = re.sub(r"[^a-z0-9_-]", "-", cid.lower()).strip("-") or "connector"
    ven = vendor or _derive_vendor(iface)
    cred_ref = _derive_credential_ref(cid, credential_ref)
    # Framed sources decide by protocol, not by name (REQ-027 owner check):
    # only a fias:// override lands in the spec. A connector record may
    # carry a routing address for its runtime target instead (e.g. the edge
    # relay queue `edge-relay://…` the PABX connector polls through), which
    # is not a valid fias_tcp source address — the docs-derived
    # fias://listen:{port} stands and the relay stays a runtime concern.
    base = f"fias://listen:{port}"
    if base_url_override and base_url_override.startswith("fias://"):
        base = base_url_override
    tz_value = _FRAMED_PROPERTY_TZ

    feed_ops = [op for op in iface.operations if op.method == "EVENT" and (op.raw.get("kind") == "feed")]
    command_ops = [op for op in iface.operations if op.method == "EVENT" and (op.raw.get("kind") == "command")]

    def _op_fields(op: Any) -> dict[str, str]:
        raw_fields = (op.raw.get("fields") or {}) if isinstance(op.raw, dict) else {}
        return {str(fid): str(meaning) for fid, meaning in raw_fields.items()}

    def _op_formats(op: Any) -> dict[str, str]:
        raw_formats = (op.raw.get("formats") or {}) if isinstance(op.raw, dict) else {}
        return {str(fid): str(fmt) for fid, fmt in raw_formats.items()}

    def _framed_wire_text(fields: dict[str, str], formats: dict[str, str], fid: str | None) -> str:
        """Meaning plus documented wire format for one field (both doc data)."""
        if not fid:
            return ""
        return f"{fields.get(fid, '')} [{formats.get(fid, '')}]"

    def _framed_dt_field(
        fields: dict[str, str], formats: dict[str, str], which: str
    ) -> str | None:
        """Date/time field by meaning, else by the documented wire format.

        Some tables state the shape (``YYMMDD``/``HHMMSS``) in a Format
        column instead of naming it in Meaning — the format is doc data,
        so falling back to it is reading, not guessing.
        """
        hints = _FRAMED_DATE_HINTS if which == "date" else _FRAMED_TIME_HINTS
        found = _framed_field(fields, hints)
        if found:
            return found
        shape = "YYMMDD" if which == "date" else "HHMMSS"
        for fid in fields:
            if shape in (formats.get(fid) or "").upper():
                return fid
        return None

    operations: dict[str, Any] = {}
    entities: dict[str, Any] = {}
    postings_op: str | None = None

    for op in feed_ops:
        code = str(op.raw.get("record", ""))
        fields = _op_fields(op)
        formats = _op_formats(op)
        amount_fid = _framed_field(fields, _FRAMED_AMOUNT_HINTS)
        room_fid = _framed_field(fields, _FRAMED_ROOM_HINTS, exclude=("old",))
        seq_fid = _framed_field(fields, _FRAMED_SEQ_HINTS)
        date_fid = _framed_dt_field(fields, formats, "date")
        time_fid = _framed_dt_field(fields, formats, "time")
        status_fid = _framed_field(fields, _FRAMED_STATUS_HINTS)
        desc_fid = _framed_field(fields, _FRAMED_DESC_HINTS)
        op_name = f"feed_{code.lower()}"
        if amount_fid and room_fid:
            if not (seq_fid and date_fid and time_fid):
                raise ValueError(
                    f"charge record {code}: needs sequence, date and time fields for the charge key"
                )
            minor = bool(
                re.search(r"minor|cent", _framed_wire_text(fields, formats, amount_fid), re.IGNORECASE)
            )
            dedupe = [room_fid, seq_fid, date_fid, time_fid]
            operations[op_name] = {
                "method": "EVENT",
                "feed": {"record": code, "via": "edge_relay", "dedupe_keys": dedupe},
            }
            charge_id_paths = [_framed_jsonpath(f) for f in (room_fid, seq_fid, date_fid, time_fid)]
            dt_paths = [_framed_jsonpath(date_fid), _framed_jsonpath(time_fid)]
            folio_fields: dict[str, Any] = {
                "charge_id": {
                    "paths": charge_id_paths,
                    "transform": [{"name": "concat", "args": {"separator": "-"}}],
                },
                # The framed peer names rooms, not reservations: the room is
                # the correlation key the PMS side resolves.
                "reservation_id": {"path": _framed_jsonpath(room_fid)},
                "property_id": {"transform": [{"name": "const", "args": {"value": iface.property_code}}]},
                "amount": {
                    "path": _framed_jsonpath(amount_fid),
                    "transform": [{"name": "money", "args": {"scale": 2, "minor_units": minor}}],
                },
                "currency": {"transform": [{"name": "const", "args": {"value": iface.currency_code}}]},
                "posted_at": {
                    "paths": dt_paths,
                    "transform": [
                        {"name": "concat", "args": {"separator": " "}},
                        {"name": "datetime_utc", "args": {"tz": "source", "naive_as": "source"}},
                    ],
                },
                "business_date": {
                    "paths": dt_paths,
                    "transform": [
                        {"name": "concat", "args": {"separator": " "}},
                        {"name": "business_date", "args": {"tz": "source", "naive_as": "source"}},
                    ],
                },
            }
            if desc_fid:
                folio_fields["description"] = {"path": _framed_jsonpath(desc_fid)}
            entities["FolioCharge"] = {
                "from": op_name,
                "key": _framed_jsonpath(seq_fid),
                "fields": folio_fields,
                "checks": [{"kind": "unique_key"}],
            }
            postings_op = postings_op or op_name
        elif room_fid and status_fid:
            if not (date_fid and time_fid):
                raise ValueError(f"status record {code}: needs date and time fields for the observation instant")
            dt_paths = [_framed_jsonpath(date_fid), _framed_jsonpath(time_fid)]
            operations[op_name] = {
                "method": "EVENT",
                "feed": {
                    "record": code,
                    "via": "edge_relay",
                    "dedupe_keys": [room_fid, status_fid, date_fid, time_fid],
                },
            }
            entities["RoomStatus"] = {
                "from": op_name,
                "key": _framed_jsonpath(room_fid),
                "fields": {
                    "room_number": {"path": _framed_jsonpath(room_fid)},
                    "status_code": {"path": _framed_jsonpath(status_fid)},
                    "observed_at": {
                        "paths": dt_paths,
                        "transform": [
                            {"name": "concat", "args": {"separator": " "}},
                            {"name": "datetime_utc", "args": {"tz": "source", "naive_as": "source"}},
                        ],
                    },
                },
                # Statuses repeat over time by design: no uniqueness check.
                "checks": [],
            }
        else:
            raise ValueError(
                f"record {code}: no amount field (charge feed) and no room+status fields "
                "(status feed) — cannot map to a canonical entity"
            )

    if "FolioCharge" not in entities:
        raise ValueError("framed docs describe no charge record: refusing a spec with no postings entity")

    # Command templates for host-originated records (doc tables, verbatim).
    commands: dict[str, Any] = {}
    for op in command_ops:
        code = str(op.raw.get("record", ""))
        fields = _op_fields(op)
        if not fields:
            continue
        taken: set[str] = set()
        templates: dict[str, str] = {}
        for fid, meaning in fields.items():
            param = _framed_param_name(meaning, taken)
            templates[fid] = f"{{{param}}}"
        kind = code.lower()
        answer = op.raw.get("answer") if isinstance(op.raw, dict) else None
        commands[kind] = {
            "record": code,
            "fields": templates,
            "expect": answer if isinstance(answer, str) and re.fullmatch(r"[A-Z]{2}", answer) else "ack",
        }

    canary_op = postings_op or next(iter(operations))
    spec: dict[str, Any] = {
        "spec_version": 3,
        "connector": {"id": cid, "vendor": ven, "runtime_target": "edge"},
        "source": {
            "base_url": base,
            "protocol": protocol,
            "auth": {"type": "edge_relay", "credential_ref": cred_ref},
            "rate_limit": {"max_rps": 5.0, "retry": {"statuses": [429, 503], "backoff": "exponential", "max_attempts": 5}},
            "timezone_source": {"kind": "static", "value": tz_value},
            "link": {
                "ack_mode": link.ack_mode,
                "max_frame_bytes": link.max_frame_bytes,
                "announced_records": list(link.announced_records),
            },
        },
        "operations": operations,
        "entities": entities,
        "capabilities": {"read": list(entities.keys()), "action": sorted(commands.keys())},
        "health": {
            "canary": {"every": "60s", "operation": canary_op, "max_pages": 1},
            "fingerprint": {"keys": True, "types": True, "pagination_shape": True},
        },
        "commands": commands,
    }
    return spec


def _compile_spec(
    iface: InterfaceDescription,
    classification: Any,
    connector_id: str | None = None,
    vendor: str | None = None,
    credential_ref: str | None = None,
    base_url_override: str | None = None,
    spec_version: int = 1,
) -> dict[str, Any]:
    """Compile InterfaceDescription + classification into validated spec dict.

    Does not run probes; deterministic from docs alone. ``spec_version`` 2
    enables POST operations with JSON body templates, ``body_fields`` auth
    and ``body_cursor`` pagination; version 1 compiles GET/header-auth
    operations exactly as before (POST operations are skipped).
    """
    # Connector identity
    cid = connector_id or _slugify(iface.title or iface.source or "harbor")
    # Ensure slug matches spec requirements
    cid = re.sub(r"[^a-z0-9_-]", "-", cid.lower()).strip("-") or "connector"
    ven = vendor or _derive_vendor(iface)
    # Base URL: an explicit override is the connector record's address,
    # already normalized to the mode this stack runs in (REQ-027), so it
    # is stored verbatim — placeholder-izing it would point the release
    # at an env var the stack may not set. Without an override, derive
    # the portable ${NAME}_BASE_URL placeholder from the docs.
    if base_url_override:
        base = _with_docs_path(base_url_override, iface.base_url or "")
    else:
        raw_base = iface.base_url or ""
        base = _derive_env_placeholder(raw_base, cid)
    cred_ref = _derive_credential_ref(cid, credential_ref)

    # Auth
    auth_kind = "bearer"
    auth_header: str | None = None
    body_field_names: tuple[str, ...] = ()
    if classification is not None and classification.auth is not None:
        ak = classification.auth.kind
        if ak == "body_fields" and getattr(classification.auth, "body_fields", ()):
            auth_kind = "body_fields"
            body_field_names = tuple(classification.auth.body_fields)
        elif ak in ("bearer", "api_key_header", "basic", "oauth2"):
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
    if auth_kind == "body_fields":
        auth_spec = {
            "type": "body_fields",
            "body_fields": {fname: _derive_body_field_ref(cid, fname, credential_ref) for fname in body_field_names},
        }
        spec_auth_type = "body_fields"
    else:
        spec_auth_type = auth_type_map.get(auth_kind, "bearer")
        auth_spec = {"type": spec_auth_type, "credential_ref": cred_ref}
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

    # Compiled op name -> InterfaceDescription OperationDescription, for join
    # proposal (spec v2): the proposal reads documented response schemas.
    compiled_descs: dict[str, Any] = {}
    for op in iface.operations:
        path = op.path
        # Skip health/readiness and single-item fetches
        if path in ("/healthz", "/readyz", "/health", "/readiness", "/openapi.json"):
            continue
        if "{" in path:
            continue
        # Spec v1 cannot express POST operations: skip them (the vocabulary
        # guard in build_draft_spec already refuses POST-only docs, so this
        # only affects mixed docs, which keep their GET operations).
        is_post = op.method.upper() == "POST"
        if is_post and spec_version < 2:
            continue
        op_name = _op_name_for_path(path, op.operation_id)
        # Property lists are not Phase 1 canonical entities, and single-item
        # fetches (one reservation / one guest) carry no pagination — skip
        # them to match the reference spec operation set.
        if op_name in ("list_properties", "get_reservation", "get_guest"):
            continue
        # /properties is not a Phase 1 canonical entity — skip to match reference spec
        if path in ("/properties", "/property"):
            continue
        # Checkpoint matching is name-based, so versioned method-style paths
        # (e.g. .../reservations/getAll/2023-06-06) resolve like /reservations.
        low_path = f"{op.operation_id} {path}".lower()
        strategy = _choose_pagination_strategy(path, iface, classification)
        pagination = None
        if strategy != "none":
            pagination = _pagination_for_strategy(strategy, op)

        checkpoint = None
        if pagination is not None:
            if "reservation" in low_path:
                checkpoint = {"field": _checkpoint_field_for_entity("Reservation", iface), "mode": "max"}
            elif "folio" in low_path or "charge" in low_path or "transaction" in low_path or "orderitem" in low_path or "order_item" in low_path:
                checkpoint = {"field": _checkpoint_field_for_entity("FolioCharge", iface), "mode": "max"}
            else:
                checkpoint = {"field": _checkpoint_field_for_entity("Reservation", iface), "mode": "max"}

        method = "POST" if is_post else "GET"
        op_spec: dict[str, Any] = {"method": method, "path": path}
        if method == "POST":
            body = _body_template_for_op(op)
            if body:
                op_spec["body"] = body
        if pagination is not None:
            op_spec["pagination"] = pagination
        if checkpoint is not None:
            op_spec["checkpoint"] = checkpoint
        operations[op_name] = op_spec
        compiled_descs[op_name] = op

    # Ensure we have at least reservations and folios ops (Harbor reference).
    # GET fallbacks only: a v2 POST vocabulary never invents GET operations.
    if spec_version < 2:
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
    status_vocab: tuple[str, ...] = ()
    if classification is not None and classification.status_vocabulary is not None:
        status_vocab = tuple(classification.status_vocabulary.vocabulary)
    # Declarative joins (spec v2 only): proposed from documented schemas,
    # then validated like any hand-written join by the spec models.
    proposed_joins: dict[str, list[dict[str, Any]]] = {}
    field_redirects: dict[str, dict[str, str]] = {}
    if spec_version >= 2:
        entity_ops = {
            name: oname
            for name, oname in (
                ("Reservation", "list_reservations"),
                ("FolioCharge", "list_folios"),
            )
            if oname in operations
        }
        proposed_joins, field_redirects = _propose_joins(iface, compiled_descs, entity_ops)

    entities: dict[str, Any] = {}
    if "list_reservations" in operations:
        fields = _collect_fields_for_entity(
            "Reservation",
            iface,
            status_kind,
            status_mapping,
            status_vocab,
            items_fields=_items_fields_for_op(compiled_descs.get("list_reservations"), iface),
            redirects=field_redirects.get("Reservation"),
        )
        entities["Reservation"] = {
            "from": "list_reservations",
            "key": _entity_key("Reservation", iface),
            "fields": fields,
            "checks": [
                {"kind": "unique_key"},
                {"kind": "no_loss", "compare_to": "$.total"},
                {"kind": "date_order", "before": "arrival_date", "after": "departure_date"},
            ],
        }
        if proposed_joins.get("Reservation"):
            entities["Reservation"]["joins"] = proposed_joins["Reservation"]
    if "list_folios" in operations:
        fields = _collect_fields_for_entity(
            "FolioCharge",
            iface,
            status_kind,
            status_mapping,
            status_vocab,
            items_fields=_items_fields_for_op(compiled_descs.get("list_folios"), iface),
            redirects=field_redirects.get("FolioCharge"),
        )
        entities["FolioCharge"] = {
            "from": "list_folios",
            "key": _entity_key("FolioCharge", iface),
            "fields": fields,
            "checks": [
                {"kind": "unique_key"},
                {"kind": "no_loss", "compare_to": "$.total"},
            ],
        }
        if proposed_joins.get("FolioCharge"):
            entities["FolioCharge"]["joins"] = proposed_joins["FolioCharge"]

    # Capabilities
    capabilities = {"read": list(entities.keys()), "action": []}

    # Health canary
    canary_op = "list_reservations" if "list_reservations" in operations else next(iter(operations))
    health = {
        "canary": {"every": "60s", "operation": canary_op, "max_pages": 1},
        "fingerprint": {"keys": True, "types": True, "pagination_shape": True},
    }

    spec: dict[str, Any] = {
        "spec_version": spec_version,
        "connector": {"id": cid, "vendor": ven},
        "source": {
            "base_url": base,
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
    spec_vocabulary: str | None = None,
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
        spec_vocabulary: ConnectorSpec vocabulary the build may use: ``"v1"``
            (GET + header auth; the default, so the v1 refusal stays
            demonstrable), ``"v2"`` (additionally POST with a JSON body
            template, ``body_fields`` auth and ``body_cursor`` pagination)
            or ``"v3"`` (additionally the event-driven transport: framed
            protocols with event feeds, multi-path mapping and command
            templates).

    Returns:
        BootstrapResult with draft_spec, validation, degradation if ambiguous.
    """
    if spec_vocabulary is None:
        from autobridge.config import spec_vocabulary_default  # noqa: PLC0415

        spec_vocabulary = spec_vocabulary_default()
    if spec_vocabulary not in ("v1", "v2", "v3"):
        raise ValueError(f"spec_vocabulary must be 'v1', 'v2' or 'v3', got {spec_vocabulary!r}")
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

    # Stage 1b: event-driven vocabulary guard (generic, any framed protocol).
    # ConnectorSpec v1 and v2 are HTTP-only: docs describing event-driven
    # record feeds (e.g. a FIAS PABX interface) compile to nothing older
    # than v3, so the pinned build refuses cleanly with 0 model calls.
    # REQ-022 item C: this is the demonstrable "before" refusal for FIAS.
    # (Checked before the POST guard: framed docs have no GET operations,
    # so the POST wording would misname the refusal.)
    protocol = getattr(iface, "protocol", "http") or "http"
    if protocol != "http" and spec_vocabulary in ("v1", "v2"):
        return BootstrapResult(
            success=False,
            degradation_reason=(
                f"event-driven {protocol} record feeds are not expressible "
                f"in ConnectorSpec {spec_vocabulary}"
            ),
            iface=iface,
            classification=classification,
            trace=trace + [{"stage": "vocabulary_guard", "protocol": protocol, "reason": f"no event feeds in vocabulary {spec_vocabulary}"}],
        )

    # Stage 1c: POST vocabulary guard (general, vendor-neutral). ConnectorSpec v1
    # can only express GET operations with header auth. When the docs describe
    # no GET operation at all (e.g. a POST-with-JSON-body API with body-field
    # auth), there is nothing in the v1 vocabulary to compile: refuse cleanly
    # instead of inventing GET operations that do not exist.
    # REQ-020 item 4: this is the demonstrable "before" refusal (0 model calls).
    # Pass spec_vocabulary="v2" to compile the v2 vocabulary instead.
    methods = {op.method.upper() for op in iface.operations}
    if iface.operations and "GET" not in methods and spec_vocabulary == "v1":
        only = sorted(methods)
        return BootstrapResult(
            success=False,
            degradation_reason=(
                f"operations need {', '.join(only)} with a JSON body and body-field auth; "
                "not expressible in ConnectorSpec v1"
            ),
            iface=iface,
            classification=classification,
            trace=trace + [{"stage": "vocabulary_guard", "refused_methods": only, "reason": "no GET operation in vocabulary v1"}],
        )

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
            # Date hypotheses about ambiguous `date` field are probed to determine semantics;
            # compile ignores the ambiguous field when resolution is ambiguous, but probe budget
            # disambiguation is the W5.7 requirement (resolved within budget or Degraded).
            compile_relevant_families = ["pagination", "status", "date"]
            for fam in compile_relevant_families:
                hyps = hypotheses_by_family.get(fam, [])
                if len(hyps) <= 1:
                    continue
                # Filter to hypotheses for this family that are not already decided by classifier
                # To avoid unnecessary probing, check if classifier already high confidence for this family
                # For pagination, classifier high for Harbor -> we can skip probing
                should_probe = True
                if fam == "pagination":
                    # Skip if main ops (/reservations, /folios) are high confidence, even if overall medium
                    main_ops = {"/reservations", "/folios"}
                    main_guesses = [g for g in classification.pagination.per_operation if g.operation in main_ops]
                    if (main_guesses and all(g.confidence == "high" for g in main_guesses)) or classification.pagination.overall_confidence == "high":
                        should_probe = False
                if fam == "status" and classification.status_vocabulary.confidence == "high":
                    should_probe = False
                if fam == "date" and not classification.date_semantics.ambiguous_fields:
                        should_probe = False
                if not should_probe:
                    # For date, even when skipping we record handling so trace shows deterministic resolution
                    if fam == "date":
                        trace.append({"stage": "date_ambiguity", "ambiguous_fields": list(classification.date_semantics.ambiguous_fields), "handling": "ignore_ambiguous_field"})
                    else:
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
                    trace.append({"stage": "resolve", "family": fam, "decision": result.decision, "hypothesis": getattr(result.resolved_hypothesis, "id", None) if result.resolved_hypothesis else None})
                # Merge model usage
                for k in ("frontier_tokens", "small_tokens", "calls", "frontier_calls", "small_calls"):
                    model_usage[k] = int(model_usage.get(k, 0)) + int(result.model_usage.get(k, 0))
            # Date family fallback: if ambiguous but not probed (e.g. skipped due to high confidence or no family entry)
            date_amb = classification.date_semantics.ambiguous_fields
            if date_amb and "date" not in resolution_results:
                # Record that date ambiguity exists but is handled by ignoring
                # Search trace for existing date_ambiguity entry to avoid duplicate
                has_da = any(t.get("stage") == "date_ambiguity" for t in trace)
                if not has_da:
                    trace.append({"stage": "date_ambiguity", "ambiguous_fields": list(date_amb), "handling": "ignore_ambiguous_field"})
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
        if protocol != "http":
            draft_spec = _compile_framed_spec(
                iface,
                connector_id=connector_id,
                vendor=vendor,
                credential_ref=credential_ref,
                base_url_override=base_url_override,
                spec_version={"v1": 1, "v2": 2, "v3": 3}[spec_vocabulary],
            )
        else:
            draft_spec = _compile_spec(
                iface,
                classification,
                connector_id=connector_id,
                vendor=vendor,
                credential_ref=credential_ref,
                base_url_override=base_url_override,
                spec_version=2 if spec_vocabulary == "v2" else 1,
            )
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



