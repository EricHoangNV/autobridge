"""Classifiers for discovery (P1.W5.2, plan §2.6/§4 W5).

Deterministic classifiers that turn an :class:`InterfaceDescription` (from
``docs_reader``) + optional first probe observations into structured
classification results for:

- auth mechanism
- pagination strategy per operation
- date field semantics (including the Harbor ``date`` ambiguity)
- status vocabulary (clear vs opaque)

No LLM, no network, no imports of verifier/simulators. Every result carries
the provenance that supports it so later hypotheses can link to at least one
evidence record (W5.3 requirement).

Probes are lightweight ``ProbeObservation`` objects (operation path + response
payload). When provided they refine — never contradict — the docs signal;
probe-derived evidence is appended to the docs evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from autobridge.discovery.docs_reader import InterfaceDescription, Provenance

# ---------------------------------------------------------------------------
# shared types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProbeObservation:
    """Single GET probe result (from ``discovery/probes.py`` or manual)."""

    operation: str  # path e.g. "/reservations"
    params: dict[str, Any] = field(default_factory=dict)
    response: dict[str, Any] = field(default_factory=dict)
    status_code: int = 200
    headers: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuthClassification:
    kind: str  # bearer | api_key_header | basic | oauth2 | none | unknown
    header_name: str | None = None
    confidence: str = "low"  # high | medium | low
    evidence: tuple[Provenance, ...] = ()
    reasoning: str = ""
    # probe-derived refinement flag
    probe_refined: bool = False


def classify_auth(
    iface: InterfaceDescription,
    probe_results: list[ProbeObservation] | None = None,
) -> AuthClassification:
    """Classify auth mechanism from docs + optional first probes.

    Docs are the primary source; probes refine:
    - probe without auth returning 401 => confirms bearer/api-key required
    - probe without auth returning 200 => confirms permissive/none
    """
    base_kind = "unknown"
    header_name: str | None = None
    evidence: list[Provenance] = []
    reasoning_parts: list[str] = []
    confidence = "low"
    probe_refined = False

    if iface.auth is not None:
        base_kind = iface.auth.kind
        header_name = iface.auth.header_name
        if iface.auth.provenance is not None:
            evidence.append(iface.auth.provenance)
        desc_low = (iface.auth.description or "").lower()
        # confidence from explicitness
        if (base_kind == "bearer" and "bearer" in desc_low) or (base_kind == "api_key_header" and header_name) or base_kind == "none":
            confidence = "high"
        elif base_kind == "unknown":
            confidence = "low"
        elif base_kind in ("basic", "oauth2"):
            confidence = "medium"
        else:
            confidence = "medium"
        reasoning_parts.append(f"docs auth kind={base_kind} from {iface.auth.provenance}")
        if header_name:
            reasoning_parts.append(f"header={header_name}")
    else:
        reasoning_parts.append("no auth section in docs")
        confidence = "low"

    # probe refinement
    if probe_results:
        # look for auth probe: 401 vs 200 without auth header
        for obs in probe_results:
            # heuristic: probe with no auth header; status_code signals requirement
            if obs.status_code == 401:
                # docs said none but probe says need auth -> upgrade to bearer
                if base_kind == "none":
                    reasoning_parts.append("probe 401 without auth contradicts permissive docs -> bearer required in prod")
                    # keep kind as bearer? but docs say permissive; we keep docs kind but note probe
                    # For classifier, we treat probe as confirming requirement; keep high but mark refined
                    probe_refined = True
                elif base_kind == "unknown":
                    base_kind = "bearer"
                    confidence = "medium"
                    probe_refined = True
                    reasoning_parts.append("probe 401 suggests bearer auth")
            elif obs.status_code == 200:
                if base_kind == "unknown":
                    base_kind = "none"
                    confidence = "medium"
                    probe_refined = True
                    reasoning_parts.append("probe 200 without auth suggests none/permissive")
                elif base_kind in ("bearer", "api_key_header"):
                    # maybe probe used auth; ambiguous, don't downgrade
                    pass

        # also check if any probe response indicates auth error payload
        for obs in probe_results:
            body_low = str(obs.response).lower()
            if "unauthorized" in body_low or "bearer" in body_low:
                probe_refined = True

    reasoning = "; ".join(reasoning_parts) if reasoning_parts else "no reasoning"

    return AuthClassification(
        kind=base_kind,
        header_name=header_name,
        confidence=confidence,
        evidence=tuple(evidence),
        reasoning=reasoning,
        probe_refined=probe_refined,
    )


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PaginationGuess:
    operation: str
    strategy: str  # offset | cursor | page | none
    confidence: str  # high | medium | low
    evidence: tuple[Provenance, ...] = ()
    reasoning: str = ""


@dataclass(frozen=True, slots=True)
class PaginationClassification:
    per_operation: tuple[PaginationGuess, ...] = ()
    overall_confidence: str = "low"
    reasoning: str = ""


def _strategy_from_response_keys(response: dict[str, Any]) -> str | None:
    """Detect pagination from response keys (probe)."""
    keys_low = {k.lower() for k in response}
    if "next_cursor" in keys_low or "cursor" in keys_low:
        return "cursor"
    if "next_offset" in keys_low:
        return "offset"
    if ("next_page" in keys_low or "page" in keys_low) and "next_cursor" not in keys_low and "next_offset" not in keys_low:
        return "page"
    return None


def classify_pagination(
    iface: InterfaceDescription,
    probe_results: list[ProbeObservation] | None = None,
) -> PaginationClassification:
    """Classify pagination strategy per operation from docs + probes.

    Docs pagination_hint is primary; probes override when response keys are
    definitive (e.g. Harbor's wrong offset example vs actual cursor).
    """
    guesses: list[PaginationGuess] = []
    any_low = False
    all_high = True

    # index probes by operation path (normalized)
    probe_by_op: dict[str, ProbeObservation] = {}
    if probe_results:
        for obs in probe_results:
            # normalize operation: ensure leading slash
            op_norm = obs.operation if obs.operation.startswith("/") else f"/{obs.operation}"
            # keep last probe per op
            probe_by_op[op_norm] = obs

    for op in iface.operations:
        hint = op.pagination_hint  # offset | cursor | page | none
        evidence: list[Provenance] = []
        if op.provenance is not None:
            evidence.append(op.provenance)
        # also include pagination_notes provenance for this op
        for note, prov in iface.pagination_notes:
            if (op.path in note or hint in note.lower()) and prov not in evidence:
                evidence.append(prov)
        reasoning_parts: list[str] = []
        confidence = "low"
        strategy = hint

        # baseline confidence from docs
        if hint == "cursor":
            # cursor is explicit via param names
            param_names = {p.name.lower() for p in op.params}
            if "cursor" in param_names or "next_cursor" in param_names:
                confidence = "high"
                reasoning_parts.append(f"param cursor present for {op.path}")
            else:
                confidence = "medium"
                reasoning_parts.append(f"hint cursor from context for {op.path}")
        elif hint == "offset":
            param_names = {p.name.lower() for p in op.params}
            if "offset" in param_names:
                confidence = "high"
                reasoning_parts.append(f"param offset present for {op.path}")
            else:
                confidence = "medium"
                reasoning_parts.append(f"hint offset from context for {op.path}")
        elif hint == "page":
            confidence = "medium"
            reasoning_parts.append(f"hint page for {op.path}")
        else:  # none
            # check if operation is likely non-paginated (e.g. /properties has no pagination)
            reasoning_parts.append(f"no pagination hint for {op.path}")
            confidence = "high" if not op.params else "medium"

        # probe refinement: response keys authoritative
        probe = probe_by_op.get(op.path)
        if probe is not None:
            detected = _strategy_from_response_keys(probe.response)
            if detected is not None and detected != strategy:
                # Harbor case: docs example says offset but actual is cursor
                evidence_probe = Provenance(source="probe", line=1, snippet=f"probe {op.path} response keys {list(probe.response.keys())}")
                evidence.append(evidence_probe)
                reasoning_parts.append(f"probe response shows {detected} (keys {list(probe.response.keys())})")
                strategy = detected
                confidence = "high"
            elif detected == strategy:
                reasoning_parts.append(f"probe confirms {strategy}")
                if confidence == "medium":
                    confidence = "high"

        if confidence == "low":
            any_low = True
        if confidence != "high":
            all_high = False

        guesses.append(
            PaginationGuess(
                operation=op.path,
                strategy=strategy,
                confidence=confidence,
                evidence=tuple(evidence),
                reasoning="; ".join(reasoning_parts),
            )
        )

    # overall confidence
    overall = "high" if all_high and guesses else "low" if any_low else "medium"
    if not guesses:
        overall = "low"

    return PaginationClassification(
        per_operation=tuple(guesses),
        overall_confidence=overall,
        reasoning=f"classified {len(guesses)} operations",
    )


# ---------------------------------------------------------------------------
# Date semantics
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DateFieldClassification:
    field: str  # e.g. "reservation.arrival_date" or just "arrival_date"
    semantics: str  # property_local_date | business_date | utc_instant | ambiguous_date | unknown
    confidence: str  # high | medium | low
    evidence: tuple[Provenance, ...] = ()
    description: str = ""


@dataclass(frozen=True, slots=True)
class DateSemanticsClassification:
    fields: tuple[DateFieldClassification, ...] = ()
    ambiguous_fields: tuple[str, ...] = ()
    business_date_cutoff: str | None = None  # e.g. "04:00"
    reasoning: str = ""


# heuristics for date semantics
_DATE_FIELD_SEMANTICS: dict[str, str] = {
    "arrival_date": "property_local_date",
    "departure_date": "property_local_date",
    "business_date": "business_date",
    "posted_at": "utc_instant",
    "updated_at": "utc_instant",
    "created_at": "utc_instant",
}

_AMBIGUOUS_FIELD_NAMES = {"date"}


def _collect_date_field_candidates(iface: InterfaceDescription) -> dict[str, list[Provenance]]:
    """Collect unique date-like field names with provenance from schemas/notes."""
    candidates: dict[str, list[Provenance]] = {}
    # from schemas
    for schema in iface.schemas:
        for f in schema.fields:
            low = f.name.lower()
            if "date" in low or "posted_at" in low or "updated_at" in low or low == "date":
                provs = [schema.provenance] if schema.provenance else []
                candidates.setdefault(f.name, []).extend(provs)
                # also add description hint
                if f.description:
                    # synthetic provenance from description already covered
                    pass
        # also include required fields that are date-like
        for req in schema.required:
            if req.lower() in ("arrival_date", "business_date", "posted_at", "updated_at", "date") and req not in candidates:
                candidates[req] = [schema.provenance] if schema.provenance else []
    # from operations response fields
    for op in iface.operations:
        for rf in op.response_fields:
            low = rf.name.lower()
            if "date" in low or "posted_at" in low or "updated_at" in low or low == "date":
                candidates.setdefault(rf.name, []).append(op.provenance) if op.provenance else None

    # from explicit date_notes (every entry is a field hint)
    for note, prov in iface.date_notes:
        # Also add note provenance to ambiguous "date" if mentioned
        if "ambiguous" in note.lower() or "field named `date`" in note:
            candidates.setdefault("date", []).append(prov)
            # ensure arrival_date/business_date also have provenance
            if "arrival_date" not in candidates:
                candidates.setdefault("arrival_date", []).append(prov)
            if "business_date" not in candidates:
                candidates.setdefault("business_date", []).append(prov)
        # cutoff hint
        if "cutoff" in note.lower() or "04:00" in note:
            for fn in ["business_date", "arrival_date", "date"]:
                if fn in candidates and prov not in candidates[fn]:
                    candidates[fn].append(prov)

    # Also ensure fields mentioned in date_notes text are represented
    # Parse backtick field names from notes
    for note, prov in iface.date_notes:
        for match in re.findall(r"`(\w+)`", note):
            if ("date" in match.lower() or match in ("posted_at", "updated_at", "date")) and prov not in candidates.get(match, []):
                candidates.setdefault(match, []).append(prov)

    # also include known date fields from schema definitions even if not yet in candidates
    # but mentioned in operations params? fallback: add known if iface has any date_notes
    if iface.date_notes and not candidates:
        for fn in ["arrival_date", "business_date", "date"]:
            candidates[fn] = [prov for _, prov in iface.date_notes]

    return candidates


def classify_date_semantics(
    iface: InterfaceDescription,
    probe_results: list[ProbeObservation] | None = None,
) -> DateSemanticsClassification:
    """Classify date field semantics from docs + optional probes.

    Probes help disambiguate the ``date`` field: fetching a record near
    midnight and comparing ``date`` with ``arrival_date``/``business_date``
    shows duplication. The classifier flags ``date`` as ambiguous when docs say
    so, regardless of probes.
    """
    candidates = _collect_date_field_candidates(iface)
    # cutoff detection
    cutoff: str | None = None
    reasoning_parts: list[str] = []
    ambiguous: list[str] = []

    for note, _ in iface.date_notes:
        if "04:00" in note:
            cutoff = "04:00"
            reasoning_parts.append("cutoff 04:00 from docs")
            break
        if "cutoff" in note.lower():
            cutoff = "04:00"
            reasoning_parts.append("cutoff hint from docs")

    # fallback: if any business_date field exists, assume cutoff present for Harbor
    if cutoff is None and any("business_date" in k for k in candidates):
        # Atlas also has business_date but docs may not mention cutoff; keep None
        pass

    fields: list[DateFieldClassification] = []
    for field_name, provs in candidates.items():
        low = field_name.lower()
        evidence = tuple(p for p in provs if p is not None)
        # determine semantics
        if low in _AMBIGUOUS_FIELD_NAMES:
            semantics = "ambiguous_date"
            confidence = "high"
            ambiguous.append(field_name)
            desc = "duplicates arrival_date/business_date, semantics unclear from docs"
            reasoning_parts.append(f"field {field_name} flagged ambiguous")
        elif low in _DATE_FIELD_SEMANTICS:
            semantics = _DATE_FIELD_SEMANTICS[low]
            # confidence high if docs explicitly mention property-local vs UTC
            desc = ""
            if semantics == "property_local_date":
                desc = "property-local calendar date"
                confidence = "high" if iface.date_notes else "medium"
            elif semantics == "business_date":
                desc = "property-local business date with cutoff 04:00"
                confidence = "high" if cutoff else "medium"
            elif semantics == "utc_instant":
                desc = "UTC instant"
                confidence = "high"
            else:
                desc = semantics
                confidence = "medium"
        else:
            semantics = "unknown"
            confidence = "low"
            desc = "unknown date field"

        # probe refinement for ambiguous field
        if probe_results and low == "date":
            for obs in probe_results:
                raw_data = obs.response.get("data")
                data = raw_data if isinstance(raw_data, list) else raw_data
                sample = None
                if isinstance(data, list) and data:
                    sample = data[0]
                elif isinstance(obs.response, dict) and "arrival_date" in obs.response:
                    sample = obs.response
                if isinstance(sample, dict) and "date" in sample and (sample.get("date") == sample.get("arrival_date") or sample.get("date") == sample.get("business_date")):
                    confidence = "high"
                    reasoning_parts.append(f"probe shows date duplicates arrival_date/business_date for {obs.operation}")

        # deduplicate evidence (keep order, unique)
        uniq_evidence: list[Provenance] = []
        seen = set()
        for p in evidence:
            key = (p.source, p.line)
            if key not in seen:
                seen.add(key)
                uniq_evidence.append(p)

        # if no evidence, create synthetic from date_notes
        if not uniq_evidence and iface.date_notes:
            uniq_evidence = [prov for _, prov in iface.date_notes[:1]]

        fields.append(
            DateFieldClassification(
                field=field_name,
                semantics=semantics,
                confidence=confidence,
                evidence=tuple(uniq_evidence),
                description=desc,
            )
        )

    # ensure canonical date fields are represented even if not in candidates
    # (e.g., Atlas has arrival_date/departure_date but date_notes may not list them individually)
    # Add from schemas if missing
    canonical_expected = {"arrival_date", "departure_date", "business_date", "posted_at", "updated_at"}
    for exp in canonical_expected:
        if exp not in {f.field for f in fields}:
            # check if any schema mentions it
            found = False
            for schema in iface.schemas:
                if any(rf.name == exp for rf in schema.fields):
                    found = True
                    break
                if exp in schema.required:
                    found = True
                    break
            if found:
                # infer semantics
                sem = _DATE_FIELD_SEMANTICS.get(exp, "unknown")
                evidence = tuple([schema.provenance for schema in iface.schemas if schema.provenance][:1])
                desc = "property-local date" if sem == "property_local_date" else ("UTC instant" if sem == "utc_instant" else sem)
                fields.append(
                    DateFieldClassification(
                        field=exp,
                        semantics=sem,
                        confidence="medium",
                        evidence=evidence,
                        description=desc,
                    )
                )

    # sort fields for determinism
    fields.sort(key=lambda x: x.field)

    # overall ambiguous tuple
    amb_tuple = tuple(sorted(set(ambiguous)))

    return DateSemanticsClassification(
        fields=tuple(fields),
        ambiguous_fields=amb_tuple,
        business_date_cutoff=cutoff,
        reasoning="; ".join(reasoning_parts) if reasoning_parts else "no date notes" if not iface.date_notes else "date notes present",
    )


# ---------------------------------------------------------------------------
# Status vocabulary
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StatusVocabularyClassification:
    vocabulary: tuple[str, ...] = ()
    kind: str = "unknown"  # clear | opaque | unknown
    mapping_hint: dict[str, str] = field(default_factory=dict)
    confidence: str = "low"
    evidence: tuple[Provenance, ...] = ()
    reasoning: str = ""


_OPAQUE_STATUS_RE = re.compile(r"\bH[1-5]\b")
_CLEAR_STATUS_VALUES = {"confirmed", "checked_in", "checked_out", "cancelled", "no_show"}

# Harbor mapping for opaque codes (from docs)
_HARBOR_MAP = {
    "H1": "confirmed",
    "H2": "checked_in",
    "H3": "checked_out",
    "H4": "cancelled",
    "H5": "no_show",
}


def _extract_status_values_from_notes(iface: InterfaceDescription) -> tuple[list[str], list[Provenance]]:
    values: list[str] = []
    provs: list[Provenance] = []
    for note, prov in iface.status_notes:
        # extract H1..H5
        for m in _OPAQUE_STATUS_RE.findall(note):
            if m not in values:
                values.append(m)
            if prov not in provs:
                provs.append(prov)
        # extract enum list pattern: ["confirmed", "cancelled"] or `confirmed`
        # also look for enum=... in note
        low = note.lower()
        for clear in _CLEAR_STATUS_VALUES:
            if clear in low:
                if clear not in values:
                    values.append(clear)
                if prov not in provs:
                    provs.append(prov)
        # also try to extract quoted enum values from note text
        for quoted in re.findall(r"[\"'`]?([A-Za-z_]+)[\"'`]?\s*[,|\]]", note):
            qlow = quoted.lower()
            if qlow in _CLEAR_STATUS_VALUES or re.fullmatch(r"H[1-5]", quoted):
                if quoted not in values:
                    values.append(quoted)
                if prov not in provs:
                    provs.append(prov)
    return values, provs


def _extract_status_from_schemas(iface: InterfaceDescription) -> tuple[list[str], list[Provenance]]:
    values: list[str] = []
    provs: list[Provenance] = []
    for schema in iface.schemas:
        for f in schema.fields:
            if "status" in f.name.lower() or "enum" in f.description.lower():
                # description contains enum=[...]
                desc = f.description
                # parse enum=... list
                for m in re.findall(r"'([^']+)'", desc):
                    if m not in values:
                        values.append(m)
                    if schema.provenance and schema.provenance not in provs:
                        provs.append(schema.provenance)
                for m in re.findall(r'"([^"]+)"', desc):
                    if m not in values:
                        values.append(m)
                    if schema.provenance and schema.provenance not in provs:
                        provs.append(schema.provenance)
                # also check type enum via raw not available, but we have enum in description
                if not values and f.type:
                    # check if type is string and description mentions enum
                    pass
        # also check required status? not needed
    return values, provs


def classify_status_vocabulary(
    iface: InterfaceDescription,
    probe_results: list[ProbeObservation] | None = None,
) -> StatusVocabularyClassification:
    """Classify status vocabulary from docs + optional probes.

    Probes can reveal actual status values when docs are wrong or incomplete
    (Harbor's early draft showed Atlas vocabulary).
    """
    values: list[str] = []
    evidence: list[Provenance] = []
    reasoning_parts: list[str] = []

    # from notes
    note_vals, note_provs = _extract_status_values_from_notes(iface)
    for v in note_vals:
        if v not in values:
            values.append(v)
    evidence.extend(note_provs)
    if note_vals:
        reasoning_parts.append(f"notes vocab {note_vals}")

    # from schemas
    schema_vals, schema_provs = _extract_status_from_schemas(iface)
    for v in schema_vals:
        if v not in values:
            values.append(v)
    for p in schema_provs:
        if p not in evidence:
            evidence.append(p)
    if schema_vals:
        reasoning_parts.append(f"schema vocab {schema_vals}")

    # probe refinement
    if probe_results:
        probe_vals: list[str] = []
        for obs in probe_results:
            # extract status values from probe response samples
            data = obs.response.get("data")
            samples: list[dict[str, Any]] = []
            if isinstance(data, list):
                samples = [r for r in data if isinstance(r, dict)]
            elif isinstance(data, dict):
                samples = [data]
            elif "status" in obs.response:
                samples = [obs.response]
            for s in samples:
                if "status" in s:
                    v = str(s["status"])
                    if v not in probe_vals:
                        probe_vals.append(v)
        if probe_vals:
            reasoning_parts.append(f"probe vocab {probe_vals}")
            for pv in probe_vals:
                if pv not in values:
                    values.append(pv)
            # add probe evidence provenance
            evidence.append(Provenance(source="probe", line=1, snippet=f"probe status values {probe_vals}"))

    # determine kind
    kind = "unknown"
    mapping_hint: dict[str, str] = {}
    if any(re.fullmatch(r"H[1-5]", v) for v in values):
        kind = "opaque"
        # map if Harbor harbor md provides mapping
        for v in values:
            if v in _HARBOR_MAP:
                mapping_hint[v] = _HARBOR_MAP[v]
        reasoning_parts.append("opaque H1..H5 codes")
    elif any(v.lower() in _CLEAR_STATUS_VALUES for v in values):
        kind = "clear"
        reasoning_parts.append("clear English vocab")
    # confidence
    if (kind == "opaque" and len(values) >= 3) or (kind == "clear" and len(values) >= 3):
        confidence = "high"
    elif values:
        confidence = "medium"
    else:
        confidence = "low"

    # overall evidence deduplication
    uniq_evidence: list[Provenance] = []
    seen = set()
    for p in evidence:
        if p is None:
            continue
        key = (p.source, p.line)
        if key not in seen:
            seen.add(key)
            uniq_evidence.append(p)

    return StatusVocabularyClassification(
        vocabulary=tuple(sorted(values)),
        kind=kind,
        mapping_hint=mapping_hint,
        confidence=confidence,
        evidence=tuple(uniq_evidence),
        reasoning="; ".join(reasoning_parts) if reasoning_parts else "no status vocab found",
    )


# ---------------------------------------------------------------------------
# Top-level bundle
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DiscoveryClassification:
    """Bundle of all four classifiers."""

    auth: AuthClassification
    pagination: PaginationClassification
    date_semantics: DateSemanticsClassification
    status_vocabulary: StatusVocabularyClassification


def classify_all(
    iface: InterfaceDescription,
    probe_results: list[ProbeObservation] | None = None,
) -> DiscoveryClassification:
    """Run all four classifiers."""
    return DiscoveryClassification(
        auth=classify_auth(iface, probe_results),
        pagination=classify_pagination(iface, probe_results),
        date_semantics=classify_date_semantics(iface, probe_results),
        status_vocabulary=classify_status_vocabulary(iface, probe_results),
    )
