"""Engine-side semantic enumeration for the live repair path (REQ-039).

A scheduler/worker sync that degrades on a money invariant produces a
``SEMANTIC_VALUE_DRIFT`` whose wire shape is intact — there is no
fingerprint delta for L0 and no rule for L1. The repair still climbs:
this module enumerates the engine's own amount readings from the wire
signals and the spec (no docs, no answer keys, no simulator reads),
filters them against the observed outcome, and builds the redacted L3
evidence from the same faulted sync. The ladder (L2 when >= 2, L3 when
< 2), the verifier judge and the escalation live in
:mod:`autobridge.repair.controller` / :mod:`autobridge.engine.detect_repair`;
both the live path and the demo's instrumented climb build their inputs
here so the two climbs are the same climb.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from autobridge.discovery.docs_reader import Provenance
from autobridge.discovery.hypotheses import Hypothesis

#: Amount strategies the engine understands (mirrors the enumerator's
#: ``AMOUNT_STRATEGIES`` in :mod:`autobridge.discovery.hypotheses`; kept as
#: literals so this module never imports that module's privates — a unit
#: test pins the two tables together).
AMOUNT_STRATEGIES: tuple[str, ...] = ("gross", "net", "tax_inclusive")

#: Human claim per strategy (same wording as the docs-driven enumerator).
_AMOUNT_CLAIMS: dict[str, str] = {
    "gross": "amount field is gross (includes tax, pre-discount)",
    "net": "amount field is net (excludes tax)",
    "tax_inclusive": "amount field is tax-inclusive total (gross + tax)",
}

#: Declarative mapping fragment per strategy (same shape as the enumerator's).
_AMOUNT_TRANSFORMS: dict[str, dict[str, Any]] = {
    "gross": {"name": "money", "mode": "gross", "description": "amount is gross"},
    "net": {"name": "money", "mode": "net", "description": "amount is net"},
    "tax_inclusive": {
        "name": "money",
        "mode": "tax_inclusive",
        "description": "amount is tax-inclusive total",
    },
}

#: Readings that predict a money invariant HOLDS. When the faulted sync
#: actually failed the invariant, the wire contradicts these readings, so
#: the wire filter drops them and the surviving (net) readings climb.
HOLD_PREDICTING_STRATEGIES: frozenset[str] = frozenset({"gross", "tax_inclusive"})

#: Key-name fragments whose values are never shown in an L3 prompt
#: (REQ-044). Credentials and PII travel by shape only: the model sees the
#: field names (so it knows which wire fields exist) but never the values.
SENSITIVE_KEY_FRAGMENTS: tuple[str, ...] = (
    "token",
    "secret",
    "password",
    "passwd",
    "auth",
    "credential",
    "apikey",
    "api_key",
    "access_key",
    "private_key",
    "session_key",
    "ssn",
    "passport",
    "cvv",
    "card_number",
    "cardnumber",
    "email",
    "e-mail",
    "phone",
    "telephone",
    "mobile",
)

#: Patchable members of one spec field, as exact JSON Pointers (REQ-044).
#: A semantic fix remaps a field's wire path (or its transform); nothing
#: else in the spec is patchable at L3.
PATCHABLE_FIELD_MEMBERS: tuple[str, ...] = ("path", "transform")

#: Shape placeholders for redacted values (type shape only, never content).
_SHAPE_PLACEHOLDERS: dict[str, Any] = {
    "str": "<redacted>",
    "int": 0,
    "float": 0.0,
    "bool": False,
    "none": None,
}


def _shape_placeholder(value: Any) -> Any:
    """Type shape of a redacted value (REQ-044: names stay, values go)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return _SHAPE_PLACEHOLDERS["bool"]
    if isinstance(value, int):
        return _SHAPE_PLACEHOLDERS["int"]
    if isinstance(value, float):
        return _SHAPE_PLACEHOLDERS["float"]
    if isinstance(value, str):
        return _SHAPE_PLACEHOLDERS["str"]
    if isinstance(value, dict):
        return {str(k): _shape_placeholder(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_shape_placeholder(v) for v in list(value)[:2]]
    return _SHAPE_PLACEHOLDERS["str"]


def _is_sensitive_key(name: str) -> bool:
    lowered = str(name).lower()
    return any(frag in lowered for frag in SENSITIVE_KEY_FRAGMENTS)


def redact_raw_record(rec: dict[str, Any], *, money_fields: set[str]) -> dict[str, Any]:
    """Redact one raw vendor record for the L3 prompt (REQ-044).

    Field names and value shapes stay (the model must see which wire
    fields exist); credential/PII values become type-shape placeholders
    via the existing redaction idea (names, never values, in the prompt);
    money fields render as strings (Decimal end to end, never float).
    Long strings truncate; nested shapes recurse with a two-item cap.
    """
    out: dict[str, Any] = {}
    for key, value in rec.items():
        name = str(key)
        if _is_sensitive_key(name):
            out[name] = _shape_placeholder(value)
            continue
        if name in money_fields:
            out[name] = None if value is None else str(value)
            continue
        if isinstance(value, dict):
            out[name] = {
                str(k): (
                    _shape_placeholder(v)
                    if _is_sensitive_key(k)
                    else (str(v)[:64] + "…" if isinstance(v, str) and len(v) > 64 else v)
                )
                for k, v in list(value.items())[:12]
            }
            continue
        if isinstance(value, list):
            out[name] = [
                str(v)[:64] + "…" if isinstance(v, str) and len(v) > 64 else v
                for v in value[:3]
            ]
            continue
        if isinstance(value, str) and len(value) > 64:
            out[name] = value[:64] + "…"
            continue
        out[name] = value
    return out


def raw_records_of_summary(
    summary: dict[str, Any] | None,
    *,
    money_fields: set[str] | None = None,
    limit: int = 2,
) -> list[dict[str, Any]]:
    """First raw vendor items from the faulted sync's raw pages (REQ-044).

    The sync summary's ``operation_results.<op>.raw_pages`` hold the actual
    wire responses; the entity items live in the first list-of-dicts found
    in each page (operations are visited in name order, pages in order, so
    the sample is deterministic). At most *limit* records, redacted by
    :func:`redact_raw_record`. Empty when the sync never reached the wire.
    """
    summary = summary if isinstance(summary, dict) else {}
    op_results = summary.get("operation_results") or {}
    if not isinstance(op_results, dict):
        return []
    money = set(money_fields or ())
    found: list[dict[str, Any]] = []

    def _first_item_lists(page: Any) -> list[list[dict[str, Any]]]:
        if isinstance(page, dict):
            return [v for k, v in sorted(page.items()) if isinstance(v, list) and v and all(isinstance(i, dict) for i in v)]
        if isinstance(page, list) and page and all(isinstance(i, dict) for i in page):
            return [page]
        return []

    ops = sorted(op_results) if isinstance(op_results, dict) else []
    for op_name in ops:
        pages = op_results.get(op_name)
        raw_pages = pages.get("raw_pages") if isinstance(pages, dict) else getattr(pages, "raw_pages", None)
        if not isinstance(raw_pages, list):
            continue
        for page in raw_pages:
            for items in _first_item_lists(page):
                for item in items:
                    if isinstance(item, dict):
                        found.append(redact_raw_record(item, money_fields=money))
                        if len(found) >= limit:
                            return found
    return found


def allowed_l3_pointers(spec: dict[str, Any], entity: str) -> list[str]:
    """Exact JSON Pointers the L3 patch may touch (REQ-044, REQ-049).

    One entry per patchable member of the climbed entity's fields —
    e.g. ``/entities/Reservation/fields/total_amount/path`` — plus the
    field object itself (replacing the object is the same change). Empty
    when the entity has no fields in the spec.

    REQ-049: also the ``compare_to`` member of every ``money_invariant``
    check in the spec (any entity). A money check asserts a documented
    external total; when the vendor's behaviour changes, accepting it
    means retargeting that assertion — a change only a human can approve
    (the semantic path never applies it on its own). The check object and
    its ``compare_to`` are the only check members listed: kinds, fields
    and thresholds are never patchable.
    """
    entities = spec.get("entities") or {}
    ent_spec = entities.get(entity) if isinstance(entities, dict) else None
    if not isinstance(ent_spec, dict):
        return []
    fields = ent_spec.get("fields") or {}
    if not isinstance(fields, dict):
        return []
    pointers: list[str] = []
    for field_name in sorted(fields):
        base = f"/entities/{entity}/fields/{field_name}"
        pointers.append(base)
        for member in PATCHABLE_FIELD_MEMBERS:
            pointers.append(f"{base}/{member}")
    if isinstance(entities, dict):
        for ent_name in sorted(entities):
            ent = entities[ent_name]
            if not isinstance(ent, dict):
                continue
            checks = ent.get("checks")
            if not isinstance(checks, list):
                continue
            for idx, check in enumerate(checks):
                if not isinstance(check, dict):
                    continue
                if check.get("kind") != "money_invariant":
                    continue
                base = f"/entities/{ent_name}/checks/{idx}"
                pointers.append(base)
                pointers.append(f"{base}/compare_to")
    return pointers


def spec_subtree_for_entity(spec: dict[str, Any], entity: str) -> dict[str, Any]:
    """The climbed entity's patchable spec subtree (REQ-044).

    Field names with their exact JSON Pointer and current value, plus the
    entity's checks/transforms as relevant — the model sees the layout it
    is asked to patch, never the whole spec and never secrets, base URLs
    or auth (source/auth are not part of the subtree by construction).
    """
    entities = spec.get("entities") or {}
    ent_spec = entities.get(entity) if isinstance(entities, dict) else None
    if not isinstance(ent_spec, dict):
        return {"entity": entity, "fields": {}, "checks": []}
    fields = ent_spec.get("fields") or {}
    subtree_fields: dict[str, Any] = {}
    if isinstance(fields, dict):
        for field_name in sorted(fields):
            field_spec = fields.get(field_name)
            base = f"/entities/{entity}/fields/{field_name}"
            subtree_fields[str(field_name)] = {
                "pointer": base,
                "path": field_spec.get("path") if isinstance(field_spec, dict) else None,
                "transform": field_spec.get("transform") if isinstance(field_spec, dict) else None,
            }
    checks = ent_spec.get("checks") or []
    return {
        "entity": entity,
        "key": ent_spec.get("key"),
        "fields": subtree_fields,
        "checks": list(checks) if isinstance(checks, list) else [],
    }


def money_fields_of_spec(spec: dict[str, Any]) -> dict[str, str]:
    """Entity -> money field for every money_invariant check in the spec."""
    out: dict[str, str] = {}
    for entity, ent_spec in (spec.get("entities") or {}).items():
        if not isinstance(ent_spec, dict):
            continue
        for check in ent_spec.get("checks") or []:
            kind = check.get("kind") if isinstance(check, dict) else getattr(check, "kind", None)
            field = check.get("field") if isinstance(check, dict) else getattr(check, "field", None)
            if kind == "money_invariant" and isinstance(field, str) and entity not in out:
                out[str(entity)] = field
    return out


def _check_compare_to(spec: dict[str, Any], entity: str, field: str) -> Any:
    for check in ((spec.get("entities") or {}).get(entity) or {}).get("checks") or []:
        if not isinstance(check, dict):
            continue
        if check.get("kind") == "money_invariant" and check.get("field") == field:
            return check.get("compare_to")
    return None


def money_invariant_failed(
    summary: dict[str, Any] | None, signals: list[Any] | None = None
) -> bool:
    """True when the faulted sync failed the money invariant (observed, never assumed)."""
    summary = summary if isinstance(summary, dict) else {}
    unmet = summary.get("unmet_criteria") or []
    if any(str(c) == "money_invariant" for c in unmet):
        return True
    for sig in signals or []:
        details = sig.get("details") if isinstance(sig, dict) else getattr(sig, "details", None)
        if not isinstance(details, dict):
            continue
        if details.get("unmet_criteria") == "money_invariant" or details.get("check") == "money_invariant":
            return True
    for inv in summary.get("invariant_results") or []:
        if isinstance(inv, dict) and inv.get("check") == "money_invariant" and inv.get("status") == "failed":
            return True
    return False


def enumerate_money_hypotheses(
    spec: dict[str, Any], *, source: str
) -> tuple[list[Hypothesis], dict[str, str]]:
    """Enumerate amount readings for the spec's money fields (deterministic).

    Three strategies per money field (gross / net / tax_inclusive), ids
    ``amount:<field>:<strategy>`` — the same id scheme as the docs-driven
    enumerator, so a recording captured for one climb serves the other.
    Provenance anchors on *source* (the faulted sync run id): every
    hypothesis links to at least one observed evidence record.
    """
    fields = money_fields_of_spec(spec)
    hyps: list[Hypothesis] = []
    for field in sorted(set(fields.values())):
        for strategy in AMOUNT_STRATEGIES:
            hyps.append(
                Hypothesis(
                    id=f"amount:{field}:{strategy}",
                    family="amount",
                    claim=f"field `{field}`: {_AMOUNT_CLAIMS[strategy]}",
                    candidate_mapping_fragment={
                        "family": "amount",
                        "field": field,
                        "strategy": strategy,
                        "semantics": strategy,
                        "transform": dict(_AMOUNT_TRANSFORMS[strategy]),
                    },
                    prior=1.0 / 3.0,
                    evidence_for=(
                        Provenance(
                            source=source,
                            line=1,
                            snippet=f"money invariant observed on field `{field}`",
                        ),
                    ),
                    field=field,
                    strategy=strategy,
                )
            )
    return hyps, fields


def filter_wire_consistent(
    hypotheses: list[Hypothesis], *, invariant_failed: bool
) -> list[Hypothesis]:
    """Drop readings the observed outcome contradicts.

    A failed money invariant contradicts every HOLD-predicting reading
    (gross, tax-inclusive: they predict the totals match). Without an
    observed failure there is nothing to contradict, so all readings
    survive and the distinct selection below still funnels to one.
    """
    if not invariant_failed:
        return list(hypotheses)
    return [h for h in hypotheses if (h.strategy or "") not in HOLD_PREDICTING_STRATEGIES]


def select_climbed(
    survivors: list[Hypothesis],
) -> tuple[Hypothesis | None, list[str]]:
    """Pick the single reading the climb demonstrates (deterministic).

    The most specific surviving field (longest field name; ties break by
    id) climbs; the rest are recorded as siblings, never hidden. The L3
    gate fires on exactly one yielded hypothesis, so L2 is skipped with
    its reason and L3 is always attempted-or-explicitly-skipped.
    """
    if not survivors:
        return None, []
    ordered = sorted(survivors, key=lambda h: (-len(h.field or ""), h.id))
    climbed = ordered[0]
    return climbed, [h.id for h in ordered[1:]]


def funnel_stages(
    enumerated: list[Hypothesis],
    survivors: list[Hypothesis],
    climbed: Hypothesis | None,
    sibling_ids: list[str],
    *,
    source: str,
) -> list[dict[str, Any]]:
    """Hypothesis-derivation stages for the repair page funnel (persisted rows)."""
    return [
        {
            "name": "generated",
            "count": len(enumerated),
            "ids": [h.id for h in enumerated],
            "families": sorted({h.family for h in enumerated}),
            "source": f"engine amount enumeration from the spec's money fields ({source})",
        },
        {
            "name": "amount",
            "count": len(enumerated),
            "ids": [h.id for h in enumerated],
            "families": ["amount"],
            "source": "all generated readings are amount-family (money semantics)",
        },
        {
            "name": "wire_consistent",
            "count": len(survivors),
            "ids": [h.id for h in survivors],
            "source": (
                "readings predicting the invariant holds are contradicted "
                "by the observed failure and dropped"
            ),
        },
        {
            "name": "distinct",
            "count": 1 if climbed is not None else 0,
            "ids": [climbed.id] if climbed is not None else [],
            "siblings": list(sibling_ids),
            "source": "most specific surviving field climbs; siblings recorded, not hidden",
        },
    ]


def _decimal_sum(rows: list[Any], field: str) -> Decimal:
    total = Decimal("0")
    for rec in rows:
        if not isinstance(rec, dict):
            continue
        try:
            total += Decimal(str(rec.get(field)))
        except (InvalidOperation, ValueError, TypeError):
            continue
    return total


def build_l3_evidence(
    spec: dict[str, Any], summary: dict[str, Any] | None, climbed: Hypothesis | None
) -> dict[str, Any]:
    """Redacted L3 evidence from the faulted sync (untrusted data, never secrets).

    Spec excerpt (entity, field, path, money check) + failing-records
    sample (first two rows, key + money field only, as strings) + the
    observed and baseline sums. Money values stay Decimal end to end;
    only the two sampled rows are rendered, as strings.

    REQ-044: the evidence also names the patch target (the connector spec
    document, with the exact JSON Pointer of every patchable member of the
    climbed entity), the entity's spec subtree (fields with pointers and
    current values, checks — never secrets, base URLs or auth), and one or
    two redacted raw vendor records (field names and value shapes; money
    as strings; credentials/PII by shape only) so the model sees which
    wire fields exist instead of inventing paths.
    """
    summary = summary if isinstance(summary, dict) else {}
    if climbed is None or not climbed.field:
        return {}
    field = climbed.field
    entities = spec.get("entities") or {}
    ent_name = next(
        (
            name
            for name, ent in entities.items()
            if isinstance(ent, dict) and money_fields_of_spec(spec).get(name) == field
        ),
        "",
    )
    ent_spec = entities.get(ent_name) if isinstance(entities, dict) else None
    field_path = ""
    key_field = ""
    compare_to: Any = None
    if isinstance(ent_spec, dict):
        fields = ent_spec.get("fields") or {}
        field_spec = fields.get(field)
        if isinstance(field_spec, dict):
            field_path = str(field_spec.get("path") or "")
        key_path = str(ent_spec.get("key") or "")
        key_field = key_path.removeprefix("$.")
        compare_to = _check_compare_to(spec, ent_name, field)
    canonical = (summary.get("canonical_records") or {}).get(ent_name) or []
    sample_rows = []
    for rec in list(canonical)[:2]:
        if isinstance(rec, dict):
            sample_rows.append(
                {k: str(rec.get(k)) for k in (key_field, field) if k and k in rec}
            )
    observed_sum = _decimal_sum(list(canonical), field)
    baseline_sum = ""
    for pages in (summary.get("operation_results") or {}).values():
        raw_pages = pages.get("raw_pages") if isinstance(pages, dict) else getattr(pages, "raw_pages", None)
        if raw_pages:
            first = raw_pages[0] if isinstance(raw_pages[0], dict) else None
            if isinstance(first, dict) and first.get("gross_sum") is not None:
                baseline_sum = str(first["gross_sum"])
                break
    pointers = allowed_l3_pointers(spec, ent_name)
    return {
        "spec_excerpt": {
            "entity": ent_name,
            "field": field,
            "path": field_path,
            "money_check": {"kind": "money_invariant", "compare_to": compare_to},
        },
        "records_sample": {
            "rows": sample_rows,
            "observed_sum": str(observed_sum),
            "baseline_gross_sum": baseline_sum,
        },
        "patch_target": {
            "document": "connector spec",
            "entity": ent_name,
            "allowed_ops": ["replace", "add"],
            "allowed_pointers": pointers,
            "transforms": [
                "date_local",
                "datetime_utc",
                "money",
                "enum_map",
                "flatten",
                "coalesce",
                "const",
                "concat",
                "business_date",
            ],
        },
        "spec_subtree": spec_subtree_for_entity(spec, ent_name),
        "raw_records": raw_records_of_summary(
            summary, money_fields={field}, limit=2
        ),
    }


#: Closed transform set the L3 patch may name (same list as
#: :func:`build_l3_evidence`; the ladder never invents transforms).
_L3_TRANSFORMS: tuple[str, ...] = (
    "date_local",
    "datetime_utc",
    "money",
    "enum_map",
    "flatten",
    "coalesce",
    "const",
    "concat",
    "business_date",
)


def _detected_entity_field(signals: list[Any] | tuple[Any, ...] | None) -> tuple[str | None, str | None]:
    """Best (entity, canonical field) for a detected drift's L3 evidence.

    Prefers the silent-corruption and wire signals that name both, then
    any signal carrying an entity. ``(None, None)`` when nothing names an
    entity — the L3 prompt still runs on the drift class alone.
    """
    ordered: list[Any] = list(signals or [])
    by_kind: dict[str, list[Any]] = {}
    for sig in ordered:
        kind = sig.kind if not isinstance(sig, dict) else sig.get("kind")
        if isinstance(kind, str):
            by_kind.setdefault(kind, []).append(sig)

    def _details(sig: Any) -> dict[str, Any]:
        raw = sig.get("details") if isinstance(sig, dict) else getattr(sig, "details", None)
        return raw if isinstance(raw, dict) else {}

    for kind in ("mapped_field_emptied", "money_scale_shift", "unknown_enum", "keys_changed"):
        for sig in by_kind.get(kind, []):
            det = _details(sig)
            entity = det.get("entity")
            if not isinstance(entity, str) or not entity:
                if kind == "unknown_enum":
                    continue
                entity = sig.entity if not isinstance(sig, dict) else sig.get("entity")
                if not isinstance(entity, str) or not entity:
                    continue
            field: Any = None
            if kind == "mapped_field_emptied":
                cfs = det.get("canonical_fields")
                field = cfs[0] if isinstance(cfs, list) and cfs and isinstance(cfs[0], str) else det.get("wire_key")
            elif kind == "money_scale_shift":
                field = det.get("money_field")
            elif kind == "unknown_enum":
                field = det.get("field") or (sig.entity if not isinstance(sig, dict) else sig.get("entity"))
            if not isinstance(field, str) or not field:
                field = None
            return entity, field
    for sig in ordered:
        entity = sig.entity if not isinstance(sig, dict) else sig.get("entity")
        if isinstance(entity, str) and entity:
            return entity, None
    return None, None


def build_detected_l3_evidence(
    spec: dict[str, Any],
    summary: dict[str, Any] | None,
    signals: list[Any] | tuple[Any, ...] | None,
) -> dict[str, Any]:
    """Redacted L3 evidence for a detected drift with no deterministic fix (REQ-064 B2).

    The generic counterpart of :func:`build_l3_evidence` (which is
    money-climb specific): the affected entity's patchable spec subtree
    with exact JSON Pointers, one or two redacted raw vendor records, and
    the observed detector facts (signal kinds + one-line messages). Never
    secrets, base URLs or auth (the subtree carries fields only), never
    instance parameters or answer keys — only the detector's signals and
    the faulted sync's own records. ``{}`` when no signal names an
    entity; the L3 prompt then renders "(none recorded)" blocks.
    """
    if not isinstance(spec, dict):
        return {}
    entity, field = _detected_entity_field(signals)
    if entity is None:
        return {}
    signal_facts: list[dict[str, str]] = []
    for sig in list(signals or [])[:8]:
        kind = sig.get("kind") if isinstance(sig, dict) else getattr(sig, "kind", "?")
        message = sig.get("message") if isinstance(sig, dict) else getattr(sig, "message", "")
        signal_facts.append(
            {"kind": str(kind), "message": str(message or "")[:300]}
        )
    money_fields = {field} if field else set()
    return {
        "detected": {
            "entity": entity,
            **({"field": field} if field else {}),
            "signals": signal_facts,
        },
        "patch_target": {
            "document": "connector spec",
            "entity": entity,
            "allowed_ops": ["replace", "add"],
            "allowed_pointers": allowed_l3_pointers(spec, entity),
            "transforms": list(_L3_TRANSFORMS),
        },
        "spec_subtree": spec_subtree_for_entity(spec, entity),
        "raw_records": raw_records_of_summary(summary, money_fields=money_fields, limit=2),
    }
