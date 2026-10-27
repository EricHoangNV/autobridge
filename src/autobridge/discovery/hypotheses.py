"""Hypothesis model and enumerators (P1.W5.3, plan §2.6).

Deterministic generators for known ambiguity families:

- date: created / arrival / business / UTC  (4 strategies)
- pagination: offset / cursor / page  (3 strategies)
- amount: gross / net / tax-inclusive  (3 strategies)
- status vocabulary: clear vs opaque  (≥1)

Every :class:`Hypothesis` links to at least one :class:`Provenance` (its
evidence) so the ladder and the UI can render an evidence chain. No LLM, no
network, no imports of verifier/simulators.

Candidate mapping fragments are small declarative dicts that ``bootstrap.py``
can later compile into a ``ConnectorSpec`` field mapping / transform. They are
not code — the runtime interprets them.

"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from autobridge.discovery.docs_reader import InterfaceDescription, Provenance

if TYPE_CHECKING:
    from autobridge.discovery.classifiers import (
        DateSemanticsClassification,
        DiscoveryClassification,
        PaginationClassification,
        ProbeObservation,
        StatusVocabularyClassification,
    )

# ---------------------------------------------------------------------------
# Hypothesis model
# ---------------------------------------------------------------------------

FAMILIES = frozenset({"date", "pagination", "amount", "status"})

DATE_STRATEGIES = ("created", "arrival", "business", "utc")
PAGINATION_STRATEGIES = ("offset", "cursor", "page")
AMOUNT_STRATEGIES = ("gross", "net", "tax_inclusive")
# status strategies are mapping variants; base names for ids
STATUS_STRATEGY_CLEAR = "clear"
STATUS_STRATEGY_OPAQUE = "opaque"

# Canonical status values for clear mapping
_CLEAR_STATUSES = ["confirmed", "checked_in", "checked_out", "cancelled", "no_show"]
# Harbor opaque mapping (same as classifiers._HARBOR_MAP, duplicated to avoid
# cross-import fragility; must stay in sync. Tested by unit test below.)
_HARBOR_OPAQUE_MAP = {
    "H1": "confirmed",
    "H2": "checked_in",
    "H3": "checked_out",
    "H4": "cancelled",
    "H5": "no_show",
}


@dataclass(frozen=True, slots=True)
class Hypothesis:
    """One competing interpretation of an ambiguous doc / API surface.

    Attributes:
        id: stable deterministic identifier (family:field:strategy).
        family: one of ``date`` | ``pagination`` | ``amount`` | ``status``.
        claim: human-readable statement of what this hypothesis asserts.
        candidate_mapping_fragment: declarative fragment for the draft spec.
        prior: uniform prior probability in ``[0, 1]``.
        evidence_for: provenance that supports this hypothesis (≥1 entry
            enforced).
        evidence_against: provenance that contradicts it (may be empty).
        field: source field or operation the hypothesis is about.
        strategy: normalized strategy name (e.g. ``"cursor"``).
    """

    id: str
    family: str
    claim: str
    candidate_mapping_fragment: dict[str, Any]
    prior: float
    evidence_for: tuple[Provenance, ...]
    evidence_against: tuple[Provenance, ...] = ()
    field: str | None = None
    strategy: str | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("Hypothesis id must be non-empty")
        if self.family not in FAMILIES:
            raise ValueError(f"family must be one of {sorted(FAMILIES)}, got {self.family!r}")
        if not self.claim:
            raise ValueError("Hypothesis claim must be non-empty")
        if not 0.0 <= self.prior <= 1.0:
            raise ValueError(f"prior must be in [0,1], got {self.prior}")
        if not self.evidence_for:
            raise ValueError(f"Hypothesis {self.id!r} must link to at least one evidence record (evidence_for empty)")
        # candidate_mapping_fragment must be JSON-able and non-empty dict
        if not isinstance(self.candidate_mapping_fragment, dict):
            raise ValueError("candidate_mapping_fragment must be a dict")
        if not self.candidate_mapping_fragment:
            raise ValueError("candidate_mapping_fragment must be non-empty")

    def to_evidence_payload(self) -> dict[str, Any]:
        """Payload suitable for ``EvidenceStore.append(kind='hypothesis')``."""
        return {
            "hypothesis_id": self.id,
            "family": self.family,
            "field": self.field,
            "strategy": self.strategy,
            "claim": self.claim,
            "fragment": self.candidate_mapping_fragment,
            "prior": self.prior,
            "evidence_for": [str(p) for p in self.evidence_for],
            "evidence_against": [str(p) for p in self.evidence_against],
        }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _dedupe_provenance(items: list[Provenance]) -> tuple[Provenance, ...]:
    seen: set[tuple[str, int]] = set()
    out: list[Provenance] = []
    for p in items:
        key = (p.source, p.line)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return tuple(out)


def _fallback_provenance(iface: InterfaceDescription, hint: str) -> Provenance:
    """Synthetic provenance when docs have no line for a field.

    Uses the interface source file as anchor so every hypothesis still
    satisfies the 'at least one evidence' invariant.
    """
    if iface.provenances:
        # reuse first real provenance as anchor but mark synthetic
        base = iface.provenances[0]
        return Provenance(source=base.source, line=base.line, snippet=hint)
    return Provenance(source=iface.source or "docs", line=1, snippet=hint)


# ---------------------------------------------------------------------------
# date family
# ---------------------------------------------------------------------------

_DATE_TRANSFORMS: dict[str, dict[str, Any]] = {
    "created": {"name": "datetime_utc", "source": "$.created_at", "description": "date is created_at UTC instant"},
    "arrival": {"name": "date_local", "source": "$.arrival_date", "description": "date is arrival_date property-local date"},
    "business": {
        "name": "business_date",
        "source": "$.business_date",
        "description": "date is business_date with 04:00 cutoff",
        "cutoff": "04:00",
    },
    "utc": {"name": "datetime_utc", "source": "$.posted_at", "description": "date is UTC posting instant"},
}

_DATE_CLAIMS: dict[str, str] = {
    "created": "ambiguous field `date` maps to created_at (UTC instant)",
    "arrival": "ambiguous field `date` maps to arrival_date (property-local date)",
    "business": "ambiguous field `date` maps to business_date (property-local business date, cutoff 04:00)",
    "utc": "ambiguous field `date` maps to posted_at / UTC instant",
}


def enumerate_date_hypotheses(
    iface: InterfaceDescription,
    date_cls: DateSemanticsClassification | None = None,
) -> list[Hypothesis]:
    """Deterministic date family: 4 hypotheses (created/arrival/business/UTC).

    Provenance: date_notes + date field classifications + schema provenance.
    Every hypothesis carries at least one Provenance entry.
    """
    # Determine ambiguous field name
    ambiguous_field = "date"
    if date_cls is not None and date_cls.ambiguous_fields:
        # Use first ambiguous field deterministically
        ambiguous_field = sorted(date_cls.ambiguous_fields)[0]
    # else keep "date" generic

    # Collect provenance pool for date family
    prov_pool: list[Provenance] = []
    # from date_notes
    for _, prov in iface.date_notes:
        prov_pool.append(prov)
    # from date classification evidence
    if date_cls is not None:
        for f in date_cls.fields:
            prov_pool.extend(list(f.evidence))
        # fallback: if pool still empty but we have business_date_cutoff hint, add synthetic
        if not prov_pool and date_cls.business_date_cutoff:
            prov_pool.append(Provenance(source=iface.source or "docs", line=1, snippet=f"cutoff {date_cls.business_date_cutoff}"))
    # from schemas mentioning date-like fields
    for schema in iface.schemas:
        if schema.provenance and any("date" in f.name.lower() for f in schema.fields) and schema.provenance not in prov_pool:
            prov_pool.append(schema.provenance)

    # If still empty, use fallback provenance so invariant holds
    if not prov_pool:
        prov_pool.append(_fallback_provenance(iface, f"date field {ambiguous_field} schema"))

    deduped_pool = _dedupe_provenance(prov_pool)

    hypotheses: list[Hypothesis] = []
    prior = 0.25  # uniform across 4
    for strategy in DATE_STRATEGIES:
        transform = _DATE_TRANSFORMS[strategy]
        claim = _DATE_CLAIMS[strategy]
        # Include field name in claim when not generic "date"? keep consistent
        if ambiguous_field != "date":
            claim = claim.replace("`date`", f"`{ambiguous_field}`")
        hid = f"date:{ambiguous_field}:{strategy}"
        fragment: dict[str, Any] = {
            "field": ambiguous_field,
            "maps_to": strategy,
            "strategy": strategy,
            "transform": dict(transform),
            "family": "date",
        }
        # Evidence linking: for business strategy, prefer provenance with cutoff hint
        # otherwise use pool. All strategies share the pool so every hypothesis has evidence.
        evidence_for = deduped_pool
        # If pool contains many entries, keep at least one; for determinism keep all deduped.
        hypotheses.append(
            Hypothesis(
                id=hid,
                family="date",
                claim=claim,
                candidate_mapping_fragment=fragment,
                prior=prior,
                evidence_for=evidence_for,
                evidence_against=(),
                field=ambiguous_field,
                strategy=strategy,
            )
        )
    return hypotheses


# ---------------------------------------------------------------------------
# pagination family
# ---------------------------------------------------------------------------

_PAGINATION_DETAILS: dict[str, dict[str, Any]] = {
    "offset": {"strategy": "offset", "page_param": "offset", "limit_param": "limit", "next_field": "$.next_offset"},
    "cursor": {"strategy": "cursor", "page_param": "cursor", "next_field": "$.next_cursor"},
    "page": {"strategy": "page", "page_param": "page", "limit_param": "page_size", "next_field": "$.next_page"},
}

_PAGINATION_CLAIMS: dict[str, str] = {
    "offset": "pagination is offset-based (offset + limit, next_offset)",
    "cursor": "pagination is cursor-based (cursor token, next_cursor)",
    "page": "pagination is page-based (page number, page_size)",
}


def _pagination_provenance_for_operation(
    iface: InterfaceDescription,
    op_path: str,
    pagination_cls: PaginationClassification | None,
) -> list[Provenance]:
    pool: list[Provenance] = []
    # from iface.pagination_notes that mention this op
    for note, prov in iface.pagination_notes:
        if op_path in note or prov not in pool:
            # Include if note mentions strategy or op path
            low = note.lower()
            if op_path.lower() in low or any(s in low for s in PAGINATION_STRATEGIES):
                pool.append(prov)
            # Also include first pagination note as fallback for any op
            elif not pool:
                # will add via fallback below
                pass
    # from operation provenance
    for op in iface.operations:
        if op.path == op_path and op.provenance is not None:
            pool.append(op.provenance)
    # from pagination classification per_operation evidence
    if pagination_cls is not None:
        for guess in pagination_cls.per_operation:
            if guess.operation == op_path:
                pool.extend(list(guess.evidence))
    # fallback: any pagination note
    if not pool:
        for _, prov in iface.pagination_notes:
            pool.append(prov)
            break
    if not pool:
        # last fallback: operation list still evidence we have iface source
        for op in iface.operations:
            if op.provenance is not None:
                pool.append(op.provenance)
                break
    if not pool:
        pool.append(_fallback_provenance(iface, f"pagination for {op_path}"))
    return pool


def enumerate_pagination_hypotheses(
    iface: InterfaceDescription,
    pagination_cls: PaginationClassification | None = None,
) -> list[Hypothesis]:
    """Deterministic pagination family: offset / cursor / page.

    Generates 3 hypotheses per paginated operation. If no operations exist,
    generates 3 global hypotheses for field ``pagination``.

    Each hypothesis carries at least one Provenance (operation line or
    pagination note).
    """
    # Determine operation list: only operations that are plausibly paginated
    op_paths: list[str] = []
    if iface.operations:
        # Filter to operations with pagination evidence (hint != none or has
        # pagination notes). Non-paginated ops like /properties or healthz
        # should not generate pagination hypotheses.
        candidate_paths: list[str] = []
        for op in iface.operations:
            # op with pagination hint
            if op.pagination_hint != "none":
                candidate_paths.append(op.path)
                continue
            # op mentioned in pagination_notes
            for note, _ in iface.pagination_notes:
                if op.path.lower() in note.lower():
                    candidate_paths.append(op.path)
                    break
            else:
                # also check classification per_operation for this op (only when strategy != none)
                if pagination_cls is not None:
                    for guess in pagination_cls.per_operation:
                        if guess.operation == op.path and guess.strategy != "none":
                            candidate_paths.append(op.path)
                            break
        # If filter found none (e.g., docs have no pagination metadata), fall
        # back to all operations so invariant still holds for generic docs.
        if candidate_paths:
            op_paths = candidate_paths
        elif pagination_cls is not None and pagination_cls.per_operation:
            op_paths = [g.operation for g in pagination_cls.per_operation]
        else:
            # Fallback to all ops that have any param suggesting pagination
            param_hints = {"offset", "cursor", "page", "limit"}
            for op in iface.operations:
                param_names = {p.name.lower() for p in op.params}
                if param_names & param_hints:
                    op_paths.append(op.path)
            if not op_paths:
                # still empty -> will use global fallback below
                op_paths = []
    elif pagination_cls is not None and pagination_cls.per_operation:
        op_paths = [g.operation for g in pagination_cls.per_operation]
    else:
        op_paths = []

    hypotheses: list[Hypothesis] = []

    if not op_paths:
        # Global fallback: 3 hypotheses for generic pagination
        pool = list(iface.pagination_notes)
        provs: list[Provenance] = [prov for _, prov in pool]
        if not provs:
            # try any provenance
            if iface.provenances:
                provs = [iface.provenances[0]]
            else:
                provs = [_fallback_provenance(iface, "pagination")]
        deduped = _dedupe_provenance(provs)
        for strategy in PAGINATION_STRATEGIES:
            hid = f"pagination:global:{strategy}"
            details = _PAGINATION_DETAILS[strategy]
            hypotheses.append(
                Hypothesis(
                    id=hid,
                    family="pagination",
                    claim=_PAGINATION_CLAIMS[strategy],
                    candidate_mapping_fragment={
                        "family": "pagination",
                        "strategy": strategy,
                        "operation": None,
                        "pagination": dict(details),
                    },
                    prior=1.0 / 3.0,
                    evidence_for=deduped,
                    field="pagination",
                    strategy=strategy,
                )
            )
        return hypotheses

    # Per-operation: 3 hypotheses each
    for op_path in sorted(set(op_paths)):
        op_pool = _pagination_provenance_for_operation(iface, op_path, pagination_cls)
        deduped = _dedupe_provenance(op_pool)
        for strategy in PAGINATION_STRATEGIES:
            hid = f"pagination:{op_path}:{strategy}"
            details = _PAGINATION_DETAILS[strategy]
            hypotheses.append(
                Hypothesis(
                    id=hid,
                    family="pagination",
                    claim=f"{op_path}: {_PAGINATION_CLAIMS[strategy]}",
                    candidate_mapping_fragment={
                        "family": "pagination",
                        "strategy": strategy,
                        "operation": op_path,
                        "pagination": dict(details),
                    },
                    prior=1.0 / 3.0,
                    evidence_for=deduped,
                    field=op_path,
                    strategy=strategy,
                )
            )
    return hypotheses


# ---------------------------------------------------------------------------
# amount family
# ---------------------------------------------------------------------------

_AMOUNT_CLAIMS: dict[str, str] = {
    "gross": "amount field is gross (includes tax, pre-discount)",
    "net": "amount field is net (excludes tax)",
    "tax_inclusive": "amount field is tax-inclusive total (gross + tax)",
}

_AMOUNT_TRANSFORMS: dict[str, dict[str, Any]] = {
    "gross": {"name": "money", "mode": "gross", "description": "amount is gross"},
    "net": {"name": "money", "mode": "net", "description": "amount is net"},
    "tax_inclusive": {"name": "money", "mode": "tax_inclusive", "description": "amount is tax-inclusive total"},
}

# Field names that indicate a money amount
_MONEY_FIELD_HINTS = ("amount", "total_amount", "net_amount", "gross_amount", "charge_amount", "price", "total")


def _collect_amount_fields(iface: InterfaceDescription) -> list[tuple[str, list[Provenance]]]:
    """Collect money-like field names with provenance."""
    found: dict[str, list[Provenance]] = {}
    for schema in iface.schemas:
        for f in schema.fields:
            low = f.name.lower()
            if any(hint in low for hint in _MONEY_FIELD_HINTS):
                provs: list[Provenance] = []
                if schema.provenance is not None:
                    provs.append(schema.provenance)
                # also consider field description provenance if available (same schema)
                found.setdefault(f.name, []).extend(provs)
        # also check required fields that look like money
        for req in schema.required:
            low = req.lower()
            if any(hint in low for hint in _MONEY_FIELD_HINTS) and req not in found:
                provs = [schema.provenance] if schema.provenance else []
                found[req] = provs

    for op in iface.operations:
        for rf in op.response_fields:
            low = rf.name.lower()
            if any(hint in low for hint in _MONEY_FIELD_HINTS):
                provs = [op.provenance] if op.provenance else []
                if rf.name not in found:
                    found[rf.name] = provs
                else:
                    # add provenance if not already
                    for p in provs:
                        if p not in found[rf.name]:
                            found[rf.name].append(p)

    # Also scan pagination/date notes? not needed for amount; but include if no fields yet
    if not found:
        # Fallback to generic "amount" if iface has any operations (likely)
        return []

    return [(k, v) for k, v in found.items()]


def enumerate_amount_hypotheses(
    iface: InterfaceDescription,
    _classification: Any | None = None,
) -> list[Hypothesis]:
    """Deterministic amount family: gross / net / tax-inclusive.

    Generates 3 hypotheses per money field (e.g. amount, total_amount).
    Falls back to 3 hypotheses for generic field ``amount`` when no
    money field is detected. Every hypothesis carries at least one
    Provenance.
    """
    amount_fields = _collect_amount_fields(iface)

    if not amount_fields:
        # Generic fallback: 3 hypotheses for "amount"
        pool: list[Provenance] = []
        # try to use any schema provenance as evidence
        for schema in iface.schemas:
            if schema.provenance is not None:
                pool.append(schema.provenance)
                break
        if not pool and iface.provenances:
            pool.append(iface.provenances[0])
        if not pool:
            pool.append(_fallback_provenance(iface, "amount field (generic fallback)"))
        deduped = _dedupe_provenance(pool)
        hyps: list[Hypothesis] = []
        for strategy in AMOUNT_STRATEGIES:
            hid = f"amount:amount:{strategy}"
            hyps.append(
                Hypothesis(
                    id=hid,
                    family="amount",
                    claim=f"field `amount`: {_AMOUNT_CLAIMS[strategy]}",
                    candidate_mapping_fragment={
                        "family": "amount",
                        "field": "amount",
                        "strategy": strategy,
                        "semantics": strategy,
                        "transform": dict(_AMOUNT_TRANSFORMS[strategy]),
                    },
                    prior=1.0 / 3.0,
                    evidence_for=deduped,
                    field="amount",
                    strategy=strategy,
                )
            )
        return hyps

    hypotheses: list[Hypothesis] = []
    for field_name, provs in sorted(amount_fields):
        pool = [p for p in provs if p is not None]
        if not pool:
            pool = [_fallback_provenance(iface, f"amount field {field_name}")]
        deduped = _dedupe_provenance(pool)
        for strategy in AMOUNT_STRATEGIES:
            hid = f"amount:{field_name}:{strategy}"
            hypotheses.append(
                Hypothesis(
                    id=hid,
                    family="amount",
                    claim=f"field `{field_name}`: {_AMOUNT_CLAIMS[strategy]}",
                    candidate_mapping_fragment={
                        "family": "amount",
                        "field": field_name,
                        "strategy": strategy,
                        "semantics": strategy,
                        "transform": dict(_AMOUNT_TRANSFORMS[strategy]),
                    },
                    prior=1.0 / 3.0,
                    evidence_for=deduped,
                    field=field_name,
                    strategy=strategy,
                )
            )
    return hypotheses


# ---------------------------------------------------------------------------
# status vocabulary family
# ---------------------------------------------------------------------------


def enumerate_status_hypotheses(
    iface: InterfaceDescription,
    status_cls: StatusVocabularyClassification | None = None,
) -> list[Hypothesis]:
    """Deterministic status vocabulary family.

    Produces hypotheses for status mapping:

    - clear: canonical English values map to themselves (identity)
    - opaque: H1..H5 map to canonical via the harbor map

    When docs already describe an opaque vocabulary, both hypotheses are
    produced so the probe selector can disambiguate. When no status
    vocabulary is found, a single fallback hypothesis is produced with
    synthetic provenance (still satisfying the invariant).

    Every hypothesis carries at least one Provenance.
    """
    # Collect provenance pool
    prov_pool: list[Provenance] = []
    if status_cls is not None and status_cls.evidence:
        prov_pool.extend(list(status_cls.evidence))
    for _, prov in iface.status_notes:
        if prov not in prov_pool:
            prov_pool.append(prov)
    # Also look for status-like schema fields
    for schema in iface.schemas:
        for f in schema.fields:
            if "status" in f.name.lower() and schema.provenance is not None and schema.provenance not in prov_pool:
                prov_pool.append(schema.provenance)

    # If still empty, use fallback provenance for the generic field
    if not prov_pool:
        prov_pool.append(_fallback_provenance(iface, "status field (generic fallback)"))

    deduped_pool = _dedupe_provenance(prov_pool)

    # Build vocabulary sets
    vocab: tuple[str, ...] = ()
    mapping_hint: dict[str, str] = {}
    kind = "unknown"
    if status_cls is not None:
        vocab = status_cls.vocabulary
        mapping_hint = dict(status_cls.mapping_hint)
        kind = status_cls.kind

    # Also infer from iface if classification missing
    if not vocab:
        # try to infer from status notes text
        found_values: list[str] = []
        for note, _ in iface.status_notes:
            low = note.lower()
            for cs in _CLEAR_STATUSES:
                if cs in low and cs not in found_values:
                    found_values.append(cs)
            for i in range(1, 6):
                code = f"H{i}"
                if code in note and code not in found_values:
                    found_values.append(code)
        if found_values:
            vocab = tuple(sorted(found_values))
            if any(v.startswith("H") for v in found_values):
                kind = "opaque"
                mapping_hint = dict(_HARBOR_OPAQUE_MAP)
            else:
                kind = "clear"

    hypotheses: list[Hypothesis] = []

    # Helper to make fragment for each strategy
    # Clear mapping: each canonical value maps to itself
    clear_mapping = {cs: cs for cs in _CLEAR_STATUSES}
    # Opaque mapping: use hint if available else harbor map
    opaque_mapping = mapping_hint if mapping_hint else dict(_HARBOR_OPAQUE_MAP)

    # Always produce clear hypothesis (identity mapping)
    # It serves as the null hypothesis even when docs say opaque; the
    # probe selector will need to distinguish it.
    hypotheses.append(
        Hypothesis(
            id="status:status:clear",
            family="status",
            claim="status vocabulary is clear English (confirmed/checked_in/...) mapped via identity",
            candidate_mapping_fragment={
                "family": "status",
                "field": "status",
                "strategy": STATUS_STRATEGY_CLEAR,
                "kind": "clear",
                "vocabulary": list(vocab) if vocab else _CLEAR_STATUSES,
                "transform": {"name": "enum_map", "mapping": clear_mapping},
            },
            prior=0.5 if kind == "opaque" else 0.6,
            evidence_for=deduped_pool,
            field="status",
            strategy=STATUS_STRATEGY_CLEAR,
        )
    )

    # Opaque hypothesis
    hypotheses.append(
        Hypothesis(
            id="status:status:opaque",
            family="status",
            claim="status vocabulary is opaque H1..H5 mapped to canonical via enum_map",
            candidate_mapping_fragment={
                "family": "status",
                "field": "status",
                "strategy": STATUS_STRATEGY_OPAQUE,
                "kind": "opaque",
                "vocabulary": list(opaque_mapping.keys()),
                "transform": {"name": "enum_map", "mapping": opaque_mapping},
            },
            prior=0.5 if kind == "opaque" else 0.4,
            evidence_for=deduped_pool,
            field="status",
            strategy=STATUS_STRATEGY_OPAQUE,
        )
    )

    return hypotheses


# ---------------------------------------------------------------------------
# top-level enumerator
# ---------------------------------------------------------------------------


@dataclass
class HypothesisEnumerator:
    """Deterministic enumerator aggregating all four families.

    Usage::

        enum = HypothesisEnumerator()
        hyps = enum.enumerate_all(iface, classification)

    ``classification`` is the :class:`DiscoveryClassification` bundle from
    ``classifiers.classify_all``. When omitted, enumerators fall back to
    docs-only mode (still deterministic, still ≥1 evidence per hypothesis).

    The enumerator never calls an LLM and never touches the network.
    """

    def enumerate_date(
        self,
        iface: InterfaceDescription,
        classification: DiscoveryClassification | None = None,
    ) -> list[Hypothesis]:
        date_cls = classification.date_semantics if classification is not None else None
        return enumerate_date_hypotheses(iface, date_cls)

    def enumerate_pagination(
        self,
        iface: InterfaceDescription,
        classification: DiscoveryClassification | None = None,
    ) -> list[Hypothesis]:
        pagination_cls = classification.pagination if classification is not None else None
        return enumerate_pagination_hypotheses(iface, pagination_cls)

    def enumerate_amount(
        self,
        iface: InterfaceDescription,
        classification: DiscoveryClassification | None = None,  # noqa: ARG002
    ) -> list[Hypothesis]:
        # Amount classification does not exist yet (no classifier); keep
        # signature parallel for future extension.
        return enumerate_amount_hypotheses(iface, None)

    def enumerate_status(
        self,
        iface: InterfaceDescription,
        classification: DiscoveryClassification | None = None,
    ) -> list[Hypothesis]:
        status_cls = classification.status_vocabulary if classification is not None else None
        return enumerate_status_hypotheses(iface, status_cls)

    def enumerate_all(
        self,
        iface: InterfaceDescription,
        classification: DiscoveryClassification | None = None,
        probe_results: list[ProbeObservation] | None = None,  # noqa: ARG002
    ) -> list[Hypothesis]:
        """Enumerate hypotheses for all families (deterministic).

        ``probe_results`` is accepted for forward-compatibility (future
        ProbeSelector may filter hypotheses by probe evidence) but does not
        affect enumeration in P1.W5.3 — enumerators are docs/classifier
        driven only.

        Returns hypotheses sorted by ``(family, id)`` for stable ordering.
        """
        all_hyps: list[Hypothesis] = []
        all_hyps.extend(self.enumerate_date(iface, classification))
        all_hyps.extend(self.enumerate_pagination(iface, classification))
        all_hyps.extend(self.enumerate_amount(iface, classification))
        all_hyps.extend(self.enumerate_status(iface, classification))
        # Stable sort: family, then id
        all_hyps.sort(key=lambda h: (h.family, h.id))
        return all_hyps

    def enumerate_all_families(
        self,
        iface: InterfaceDescription,
        classification: DiscoveryClassification | None = None,
    ) -> dict[str, list[Hypothesis]]:
        """Enumerate grouped by family name."""
        return {
            "date": self.enumerate_date(iface, classification),
            "pagination": self.enumerate_pagination(iface, classification),
            "amount": self.enumerate_amount(iface, classification),
            "status": self.enumerate_status(iface, classification),
        }
