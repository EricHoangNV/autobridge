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
    }
