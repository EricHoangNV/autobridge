"""Drift signals from sync runs and the canary (P1.W4.1, plan §2.5).

Signals are the raw observations that the drift classifier (P1.W4.2) will
turn into one of the 12 drift classes.  Each signal carries provenance
(the sync or canary run id, operation name and evidence ids) so later
stages can build an evidence chain.

Signals are collected from:

* **sync runs** — outcome (Complete | Degraded | Blocked), unmet_criteria,
  invariant failures, unknown-enum blocks, HTTP errors, pagination
  stop reasons, repeated_cursor flag, fingerprint drift, zero records,
  checkpoint staleness.
* **canary** — the ``health.canary`` lightweight probe (single operation,
  max_pages=1).  Its signals include http_error, pagination regression,
  fingerprint drift vs last successful sync, and unmet_criteria.

This module is deliberately deterministic: no LLM calls, no hidden
state.  The classifier decides the drift class; signals just name what
was observed.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import Any, Literal

from autobridge.drift.fingerprint import Fingerprint, FingerprintDelta, diff_fingerprints

SignalKind = Literal[
    "outcome_blocked",
    "outcome_degraded",
    "unknown_enum",
    "no_loss",
    "repeated_cursor",
    "pagination_changed",
    "keys_changed",
    "types_changed",
    "fingerprint_changed",
    "zero_records",
    "checkpoint_stale",
    "http_error",
    "auth_error",
    "rate_limited",
    "transient_error",
    "capability_removed",
    "invariant_failure",
    "canary_failed",
    "canary_pagination_regression",
]

Severity = Literal["low", "medium", "high", "critical"]

# HTTP status → signal kind mapping used when operation results carry
# http_errors (added by http client or interpreter on failure).
_HTTP_STATUS_KIND: dict[int, SignalKind] = {
    400: "http_error",
    401: "auth_error",
    402: "http_error",
    403: "auth_error",
    404: "capability_removed",
    405: "http_error",
    409: "http_error",
    429: "rate_limited",
    500: "transient_error",
    502: "transient_error",
    503: "transient_error",
    504: "transient_error",
}


@dataclass(frozen=True, slots=True)
class DriftSignal:
    """One observable drift indicator with provenance."""

    kind: str
    operation: str | None = None
    entity: str | None = None
    severity: Severity = "medium"
    message: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    source_run_id: str | None = None
    fingerprint_delta: FingerprintDelta | None = None


def signal_to_json(sig: Any) -> dict[str, Any]:
    """JSON-safe copy of a drift signal for job payloads (REQ-042).

    The background repair job rebuilds :class:`DriftSignal` objects from
    these dicts (:func:`signal_from_json`), so a deferred semantic repair
    carries the same wire evidence the sync observed — never re-diagnosed
    from a scenario id. ``details`` hold names and strategies only (no
    secrets ever flow through signals). Round-trips through ``json`` with
    ``default=str`` so stray Decimals become strings.
    """
    import json as _json  # noqa: PLC0415

    if isinstance(sig, dict):
        raw: dict[str, Any] = dict(sig)
    else:
        raw = {
            "kind": getattr(sig, "kind", None),
            "operation": getattr(sig, "operation", None),
            "entity": getattr(sig, "entity", None),
            "severity": getattr(sig, "severity", "medium"),
            "message": getattr(sig, "message", ""),
            "evidence_ids": list(getattr(sig, "evidence_ids", None) or []),
            "details": dict(getattr(sig, "details", None) or {}),
            "source_run_id": getattr(sig, "source_run_id", None),
            "fingerprint_delta": getattr(sig, "fingerprint_delta", None),
        }
    fd = raw.get("fingerprint_delta")
    if fd is not None and not isinstance(fd, dict):
        from autobridge.repair.knowledge import _delta_to_dict  # noqa: PLC0415

        try:
            fd = _delta_to_dict(fd)
        except Exception:
            fd = None
    raw["fingerprint_delta"] = fd
    return _json.loads(_json.dumps(raw, sort_keys=True, default=str))


def signal_from_json(data: dict[str, Any]) -> DriftSignal:
    """Rebuild a :class:`DriftSignal` from :func:`signal_to_json` output.

    Unknown/missing fields fall back to the dataclass defaults; an
    unparsable ``fingerprint_delta`` becomes ``None`` (the ladder treats
    a missing delta as "nothing observed", never as evidence).
    """
    fd: FingerprintDelta | None = None
    raw_fd = (data or {}).get("fingerprint_delta")
    if isinstance(raw_fd, dict):
        try:
            fd = FingerprintDelta(
                fingerprint_changed=bool(raw_fd.get("fingerprint_changed", False)),
                pagination_changed=bool(raw_fd.get("pagination_changed", False)),
                keys_changed=bool(raw_fd.get("keys_changed", False)),
                types_changed=bool(raw_fd.get("types_changed", False)),
                changed_entities=list(raw_fd.get("changed_entities") or []),
                pagination_details=dict(raw_fd.get("pagination_details") or {}),
                keys_details=dict(raw_fd.get("keys_details") or {}),
                types_details=dict(raw_fd.get("types_details") or {}),
            )
        except Exception:
            fd = None
    details = (data or {}).get("details")
    return DriftSignal(
        kind=str((data or {}).get("kind") or "unknown"),
        operation=(data or {}).get("operation"),
        entity=(data or {}).get("entity"),
        severity=(data or {}).get("severity") or "medium",
        message=str((data or {}).get("message") or ""),
        evidence_ids=[str(e) for e in ((data or {}).get("evidence_ids") or [])],
        details=dict(details) if isinstance(details, dict) else {},
        source_run_id=(data or {}).get("source_run_id"),
        fingerprint_delta=fd,
    )


def _severity_for_kind(kind: str) -> Severity:
    if kind in {"outcome_blocked", "unknown_enum", "capability_removed", "auth_error"}:
        return "critical"
    if kind in {"outcome_degraded", "no_loss", "repeated_cursor", "pagination_changed"}:
        return "high"
    if kind in {"keys_changed", "types_changed", "fingerprint_changed"}:
        return "medium"
    if kind in {"zero_records", "checkpoint_stale", "invariant_failure"}:
        return "high"
    if kind == "rate_limited":
        return "medium"
    if kind == "transient_error":
        return "low"
    return "medium"


def _extract_http_signals(
    operation_results: dict[str, Any],
    source_run_id: str | None = None,
) -> list[DriftSignal]:
    """Scan operation results for http_error/status signals."""
    signals: list[DriftSignal] = []
    if not isinstance(operation_results, dict):
        return signals
    for op_name, res in operation_results.items():
        # OperationResult may carry http_status or http_errors list or last_error
        http_status: int | None = None
        http_errors: list[Any] = []
        if res is not None:
            http_status = getattr(res, "http_status", None)
            if http_status is None and isinstance(res, dict):
                http_status = res.get("http_status")
            # Some HttpClient implementations attach last_error string containing status
            last_error = getattr(res, "last_error", None)
            if last_error is None and isinstance(res, dict):
                last_error = res.get("last_error")
            if isinstance(last_error, str):
                # Try to extract status code from error message like "429 ..." or "HTTP 429"
                import re

                m = re.search(r"\b(4\d{2}|5\d{2})\b", last_error)
                if m:
                    with contextlib.suppress(ValueError):
                        http_status = int(m.group(1))
            http_errors_raw = getattr(res, "http_errors", None)
            if http_errors_raw is None and isinstance(res, dict):
                http_errors_raw = res.get("http_errors")
            if isinstance(http_errors_raw, list):
                http_errors = http_errors_raw
            elif isinstance(http_status, int):
                http_errors = [http_status]
            # Also check raw_pages for status hints
            raw_pages = getattr(res, "raw_pages", None)
            if raw_pages is None and isinstance(res, dict):
                raw_pages = res.get("raw_pages")
            if isinstance(raw_pages, list):
                for page in raw_pages:
                    if isinstance(page, dict) and "http_status" in page:
                        try:
                            s = int(page["http_status"])
                            if s not in http_errors:
                                http_errors.append(s)
                        except (TypeError, ValueError):
                            pass

        for status in http_errors:
            try:
                code = int(status) if not isinstance(status, int) else status
            except (TypeError, ValueError):
                continue
            kind: SignalKind = _HTTP_STATUS_KIND.get(code, "http_error")
            sev = _severity_for_kind(kind)
            signals.append(
                DriftSignal(
                    kind=kind,
                    operation=op_name,
                    severity=sev,
                    message=f"HTTP {code} on {op_name}",
                    details={"http_status": code},
                    source_run_id=source_run_id,
                )
            )
        # Also handle explicit http_status when no error list but non-2xx
        # (avoid double emission when already handled)
        if http_status is not None and http_status not in http_errors and http_status >= 400:
            kind = _HTTP_STATUS_KIND.get(http_status, "http_error")
            signals.append(
                DriftSignal(
                    kind=kind,
                    operation=op_name,
                    severity=_severity_for_kind(kind),
                    message=f"HTTP {http_status} on {op_name}",
                    details={"http_status": http_status},
                    source_run_id=source_run_id,
                )
            )
    return signals


# ---------------------------------------------------------------------------
# Wire-observed evidence (REQ-027 sub-step 2).
#
# Everything here derives from the observed vendor responses carried in
# ``operation_results`` (``raw_pages`` envelopes + ``records`` extracted via
# the spec's ``items_path``). Never from scenario ids, fault YAML or answer
# keys. The detector attaches this to signals so the classifier and the L1
# rules can use it: the fingerprint compares spec-shaped summaries, so a
# page->cursor flip never shows as a strategy change there — but the wire
# envelope (``next_cursor`` present, ``total`` gone) does.
# ---------------------------------------------------------------------------

# Closed set of known cursor-leaf names (lowercased for comparison).
_CURSOR_LEAF_NAMES: frozenset[str] = frozenset(
    {
        "next_cursor",
        "nextcursor",
        "cursor",
        "next_page_token",
        "nextpagetoken",
        "next_token",
        "nexttoken",
        "continuation_token",
        "continuationtoken",
        "page_token",
        "pagetoken",
        "end_cursor",
        "endcursor",
    }
)

# Closed set of known total-count leaf names (lowercased for comparison).
_TOTAL_LEAF_NAMES: frozenset[str] = frozenset(
    {
        "total",
        "totalcount",
        "total_count",
        "totalrecords",
        "total_records",
        "count_total",
    }
)

_WIRE_SAMPLE_LIMIT = 64
_WIRE_ADDED_SAMPLE_RECORDS = 5


def _wire_truncate(value: Any) -> str:
    text = value if isinstance(value, str) else repr(value)
    return text[:_WIRE_SAMPLE_LIMIT]


def _raw_pages_of(res: Any) -> list[Any]:
    pages = getattr(res, "raw_pages", None)
    if pages is None and isinstance(res, dict):
        pages = res.get("raw_pages")
    return list(pages) if isinstance(pages, list) else []


def _records_of(res: Any) -> list[Any]:
    records = getattr(res, "records", None)
    if records is None and isinstance(res, dict):
        records = res.get("records")
    return list(records) if isinstance(records, list) else []


def _wire_cursor_values(
    raw_pages: list[Any],
) -> dict[str, list[Any]]:
    """Non-null values seen per cursor-like envelope key (REQ-027).

    A null cursor leaf is normal (last page, request echo); only a
    transmitted cursor value contradicts the spec, so the contradiction
    checks below ignore nulls.
    """
    seen: dict[str, list[Any]] = {}
    for page in raw_pages:
        if not isinstance(page, dict):
            continue
        for key, value in page.items():
            if not isinstance(key, str) or value is None:
                continue
            lowered = key.lower()
            if lowered in _CURSOR_LEAF_NAMES or "cursor" in lowered:
                seen.setdefault(key, []).append(value)
    return seen


def _wire_total_present(page: Any) -> bool:
    if not isinstance(page, dict):
        return False
    return any(
        isinstance(key, str) and key.lower() in _TOTAL_LEAF_NAMES for key in page
    )


def _spec_op_pagination(spec: Any, op_name: str) -> dict[str, Any]:
    if not isinstance(spec, dict):
        return {}
    ops = spec.get("operations")
    if not isinstance(ops, dict):
        return {}
    op_spec = ops.get(op_name)
    if not isinstance(op_spec, dict):
        return {}
    pag = op_spec.get("pagination")
    return dict(pag) if isinstance(pag, dict) else {}


def _jsonpath_extracts(data: Any, path: str | None) -> Any:
    if not isinstance(path, str) or not path:
        return None
    try:
        from autobridge.runtime.interpreter import extract_jsonpath  # noqa: PLC0415
    except Exception:  # pragma: no cover - interpreter always present in practice
        return None
    try:
        return extract_jsonpath(data, path)
    except Exception:
        return None


def _array_candidates(page: Any) -> list[str]:
    """Candidate items arrays on a raw envelope (top level + one nesting level).

    Reports ``$.<key>`` (or ``$.<parent>.<key>``) paths whose value is a
    non-empty list of dicts. Depth is capped at two levels so the result
    stays predictable; deeper shifts escalate for the operator.
    """
    if not isinstance(page, dict):
        return []
    out: list[str] = []
    for key, value in page.items():
        if not isinstance(key, str):
            continue
        if (
            isinstance(value, list)
            and value
            and all(isinstance(item, dict) for item in value)
        ):
            out.append(f"$.{key}")
        elif isinstance(value, dict):
            for sub_key, sub_value in value.items():
                if not isinstance(sub_key, str):
                    continue
                if (
                    isinstance(sub_value, list)
                    and sub_value
                    and all(isinstance(item, dict) for item in sub_value)
                ):
                    out.append(f"$.{key}.{sub_key}")
    return out


def _observe_wire_pagination(
    spec: Any,
    operation_results: dict[str, Any],
    *,
    source_run_id: str | None = None,
) -> list[DriftSignal]:
    """Emit ``pagination_changed`` signals from wire-vs-spec contradictions.

    Only fires when the observed envelope contradicts the spec's pagination
    strategy, so a healthy baseline sync (wire matches spec) emits nothing.
    """
    signals: list[DriftSignal] = []
    if not isinstance(operation_results, dict):
        return signals
    for op_name, res in operation_results.items():
        raw_pages = _raw_pages_of(res)
        if not raw_pages:
            continue
        first = raw_pages[0] if isinstance(raw_pages[0], dict) else None
        if first is None:
            continue
        pag = _spec_op_pagination(spec, op_name)
        if not pag:
            continue
        strategy = pag.get("strategy")
        cursor_values = _wire_cursor_values(raw_pages)
        total_present = any(_wire_total_present(page) for page in raw_pages)
        envelope_keys = sorted(k for k in first if isinstance(k, str))
        next_path = pag.get("next_path")
        items_path = pag.get("items_path")

        if strategy in ("page", "offset"):  # noqa: SIM102
            # A transmitted cursor value with no total anywhere contradicts
            # a page/offset spec. Null leaves are normal (last page) and
            # never fire on their own.
            if cursor_values and not total_present:
                cursor_leaf = next(iter(cursor_values))
                sample = cursor_values[cursor_leaf][0]
                signals.append(
                    DriftSignal(
                        kind="pagination_changed",
                        operation=op_name,
                        severity="high",
                        message=(
                            f"Wire serves cursor pagination on {op_name} "
                            f"(spec strategy {strategy})"
                        ),
                        details={
                            "spec_strategy": strategy,
                            "wire_strategy_hint": "cursor",
                            "cursor_field": cursor_leaf,
                            "cursor_sample": _wire_truncate(sample),
                            "total_present": False,
                            "envelope_keys": envelope_keys,
                            "wire_evidence": "raw_pages[0]",
                        },
                        source_run_id=source_run_id,
                    )
                )
                continue
        if strategy in ("cursor", "body_cursor"):
            next_hit = any(
                _jsonpath_extracts(page, next_path) is not None
                for page in raw_pages
                if isinstance(page, dict)
            )
            # The spec's next_path reads null everywhere, yet the wire
            # transmitted a cursor value the spec failed to pick up: the
            # response leaf was renamed (or the strategy flipped to page).
            if not next_hit and cursor_values:
                cursor_leaf = next(iter(cursor_values))
                signals.append(
                    DriftSignal(
                        kind="pagination_changed",
                        operation=op_name,
                        severity="high",
                        message=(
                            f"Spec next_path {next_path!r} reads null on {op_name}; "
                            f"wire carries {cursor_leaf!r}"
                        ),
                        details={
                            "spec_strategy": strategy,
                            "spec_next_path": next_path,
                            "wire_strategy_hint": "page" if total_present else "cursor",
                            "observed_leaf": cursor_leaf,
                            "cursor_sample": _wire_truncate(cursor_values[cursor_leaf][0]),
                            "total_present": total_present,
                            "envelope_keys": envelope_keys,
                            "wire_evidence": "raw_pages",
                        },
                        source_run_id=source_run_id,
                    )
                )
                continue
        # Items-path shift: the spec's items_path extracts nothing but the
        # envelope carries candidate arrays.
        if isinstance(items_path, str) and items_path:
            extracted = _records_of(res)
            if not extracted:
                candidates = _array_candidates(first)
                if candidates:
                    signals.append(
                        DriftSignal(
                            kind="pagination_changed",
                            operation=op_name,
                            severity="high",
                            message=(
                                f"Spec items_path {items_path!r} extracts no "
                                f"records on {op_name}; wire carries {candidates}"
                            ),
                            details={
                                "spec_strategy": strategy,
                                "spec_items_path": items_path,
                                "items_extracted": 0,
                                "array_candidates": candidates,
                                "envelope_keys": envelope_keys,
                                "wire_evidence": "raw_pages[0]",
                            },
                            source_run_id=source_run_id,
                        )
                    )
    return signals


def _spec_field_top_keys(spec: Any, entity: str) -> set[str]:
    """Top-level wire keys the spec's field paths cover for *entity*."""
    covered: set[str] = set()
    if not isinstance(spec, dict):
        return covered
    entities = spec.get("entities")
    if not isinstance(entities, dict):
        return covered
    ent_spec = entities.get(entity)
    if not isinstance(ent_spec, dict):
        return covered
    fields = ent_spec.get("fields")
    if not isinstance(fields, dict):
        return covered
    for fm in fields.values():
        if not isinstance(fm, dict):
            continue
        paths: list[Any] = []
        if fm.get("path") is not None:
            paths.append(fm.get("path"))
        extra_paths = fm.get("paths")
        if isinstance(extra_paths, list):
            paths.extend(extra_paths)
        for path in paths:
            if not isinstance(path, str) or not path.startswith("$."):
                continue
            rest = path[2:]
            if not rest:
                continue
            covered.add(rest.split(".")[0])
    return covered


def _wire_records_for_entity(
    spec: Any,
    operation_results: dict[str, Any],
    entity: str,
) -> tuple[str | None, list[dict[str, Any]]]:
    """Wire records backing *entity* via its ``from_operation`` (if known)."""
    if not isinstance(spec, dict) or not isinstance(operation_results, dict):
        return None, []
    entities = spec.get("entities")
    if not isinstance(entities, dict):
        return None, []
    ent_spec = entities.get(entity)
    op_name: str | None = None
    if isinstance(ent_spec, dict):
        for key in ("from_operation", "from"):
            cand = ent_spec.get(key)
            if isinstance(cand, str):
                op_name = cand
                break
    if op_name is None:
        # Fall back to the single operation when the spec has exactly one.
        ops = spec.get("operations")
        if isinstance(ops, dict) and len(ops) == 1:
            op_name = next(iter(ops))
    if op_name is None:
        return None, []
    res = operation_results.get(op_name)
    records = [r for r in _records_of(res) if isinstance(r, dict)]
    return op_name, records


def _type_name_of(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if value is None:
        return "null"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return type(value).__name__


def _observed_wire_keys(spec: Any, entity: str) -> set[str] | None:
    """Build-probe wire keys for *entity*, or None when unprobed.

    REQ-027: a build that probed the vendor records the served top-level
    wire keys on each entity (``observed_wire_keys``). Detection uses them
    to tell a vendor rename (served-then-gone key) from a build over-mapping
    (spec path the wire never served) and genuinely new keys from perennial
    unmapped ones. Specs without the annotation keep legacy behavior.
    """
    if not isinstance(spec, dict):
        return None
    entities = spec.get("entities")
    if not isinstance(entities, dict):
        return None
    ent_spec = entities.get(entity)
    if not isinstance(ent_spec, dict):
        return None
    observed = ent_spec.get("observed_wire_keys")
    if not isinstance(observed, list) or not observed:
        return None
    return {k for k in observed if isinstance(k, str)}


def _observe_wire_schema(
    spec: Any,
    operation_results: dict[str, Any],
    *,
    source_run_id: str | None = None,
) -> list[DriftSignal]:
    """Emit ``keys_changed`` signals for spec-mapped fields missing on the wire.

    Only fires when at least one spec-covered field is absent from every
    observed wire record, so a healthy baseline (wire carries what the spec
    maps) emits nothing. Added wire keys come with truncated sample values
    so rules can propose the new path deterministically.

    When the entity carries a build-probe ``observed_wire_keys`` annotation,
    coverage is scoped to mappings the vendor actually served at build time
    (phantom mappings never fire), and perennial unmapped keys the probe saw
    are excluded from ``added`` (only genuinely new keys are reported).
    """
    signals: list[DriftSignal] = []
    if not isinstance(spec, dict) or not isinstance(operation_results, dict):
        return signals
    entities = spec.get("entities")
    if not isinstance(entities, dict):
        return signals
    for entity in entities:
        if not isinstance(entity, str):
            continue
        op_name, wire_records = _wire_records_for_entity(
            spec, operation_results, entity
        )
        if not wire_records:
            continue
        covered = _spec_field_top_keys(spec, entity)
        if not covered:
            continue
        observed = _observed_wire_keys(spec, entity)
        effective_covered = covered & observed if observed is not None else covered
        if not effective_covered:
            continue
        wire_keys: set[str] = set()
        for rec in wire_records[:_WIRE_ADDED_SAMPLE_RECORDS]:
            wire_keys.update(k for k in rec if isinstance(k, str))
        for rec in wire_records[_WIRE_ADDED_SAMPLE_RECORDS:]:
            wire_keys.update(k for k in rec if isinstance(k, str))
        missing = sorted(k for k in effective_covered if k not in wire_keys)
        if not missing:
            continue
        novel = wire_keys - covered if observed is None else wire_keys - covered - observed
        added: list[dict[str, Any]] = []
        for key in sorted(novel):
            sample: Any = None
            for rec in wire_records[:_WIRE_ADDED_SAMPLE_RECORDS]:
                if key in rec:
                    sample = rec[key]
                    break
            added.append(
                {
                    "key": key,
                    "sample": _wire_truncate(sample),
                    "type": _type_name_of(sample),
                }
            )
        signals.append(
            DriftSignal(
                kind="keys_changed",
                operation=op_name,
                entity=entity,
                severity="medium",
                message=(
                    f"Wire no longer carries {missing} for {entity}; "
                    f"new keys: {[a['key'] for a in added]}"
                ),
                details={
                    "entity": entity,
                    "missing": missing,
                    "added": added,
                    "wire_evidence": "records",
                    **({"observed_wire_keys": sorted(observed)} if observed is not None else {}),
                },
                source_run_id=source_run_id,
            )
        )
    return signals


def spec_with_baseline_wire_keys(
    spec: Any,
    baseline_operation_results: Any,
) -> Any:
    """Detection-only copy of *spec* annotated with baseline wire keys.

    REQ-028: the bench/beacon derive path observes a faulted sync against a
    baseline sync, but the pre-drift spec it detects with never went
    through a build probe — so ``_observe_wire_schema`` cannot tell a
    vendor rename (served-then-gone key plus one genuinely new key) from
    perennial unmapped keys, and renames classify as REMOVED/TYPE_CHANGE
    instead of SCHEMA_RENAME. Annotating the detection copy with the keys
    the baseline observation actually served gives detection the same
    scoping the live build probe provides (``observed_wire_keys``):
    ``missing`` is scoped to served mappings and ``added`` to genuinely
    new keys. Derived from observed responses only — never from scenario
    labels, catalogs or answer keys. The repair spec itself is untouched;
    callers pass the original spec to the ladder and the verifier.

    Fail-soft: entities with no baseline records stay unannotated (legacy
    behavior), and a non-dict spec is returned unchanged.
    """
    import copy as _copy

    if not isinstance(spec, dict):
        return spec
    entities = spec.get("entities")
    if not isinstance(entities, dict):
        return spec
    if not isinstance(baseline_operation_results, dict):
        return spec
    annotated = 0
    out = _copy.deepcopy(spec)
    out_entities = out.get("entities")
    if not isinstance(out_entities, dict):
        return spec
    for ent_name in entities:
        if not isinstance(ent_name, str):
            continue
        _, wire_records = _wire_records_for_entity(spec, baseline_operation_results, ent_name)
        keys = sorted({k for rec in wire_records for k in rec if isinstance(k, str)})
        if not keys:
            continue
        ent_spec = out_entities.get(ent_name)
        if isinstance(ent_spec, dict):
            ent_spec["observed_wire_keys"] = keys
            annotated += 1
    return out if annotated else spec


def signals_from_sync(
    sync_summary: dict[str, Any],
    *,
    spec: dict[str, Any] | None = None,  # noqa: ARG001 - reserved for pagination expectation enrichment
    previous_fingerprint: Fingerprint | dict[str, Any] | str | None = None,
    current_fingerprint: Fingerprint | dict[str, Any] | None = None,
    source_run_id: str | None = None,
) -> list[DriftSignal]:
    """Extract drift signals from a sync run summary.

    *sync_summary* is the dict returned by ``engine.sync_run.execute_sync``:
    outcome, unmet_criteria, data_readiness, fingerprint_id,
    operation_results, records_observed, canonical_records,
    blocked_records, invariant_results.

    *spec* if provided is used to enrich pagination signals.
    *previous_fingerprint* and *current_fingerprint* enable fingerprint-drift
    signals (when both supplied, a :class:`FingerprintDelta` is computed).
    *source_run_id* is stored as provenance.
    """
    signals: list[DriftSignal] = []
    if not isinstance(sync_summary, dict):
        return signals

    outcome = sync_summary.get("outcome")
    unmet_criteria: list[str] = sync_summary.get("unmet_criteria") or []
    if not isinstance(unmet_criteria, list):
        unmet_criteria = [str(unmet_criteria)]

    records_observed: dict[str, int] = sync_summary.get("records_observed") or {}
    operation_results: dict[str, Any] = sync_summary.get("operation_results") or {}
    blocked_records: dict[str, Any] = sync_summary.get("blocked_records") or {}
    invariant_results: list[dict[str, Any]] = sync_summary.get("invariant_results") or []

    # Outcome signals (Complete → none, Degraded / Blocked → explicit)
    if outcome == "Blocked":
        signals.append(
            DriftSignal(
                kind="outcome_blocked",
                severity="critical",
                message=f"Sync Blocked: {', '.join(unmet_criteria) or 'unknown'}",
                details={"unmet_criteria": list(unmet_criteria), "outcome": outcome},
                source_run_id=source_run_id,
            )
        )
    elif outcome == "Degraded":
        signals.append(
            DriftSignal(
                kind="outcome_degraded",
                severity="high",
                message=f"Sync Degraded: {', '.join(unmet_criteria) or 'degraded'}",
                details={"unmet_criteria": list(unmet_criteria), "outcome": outcome},
                source_run_id=source_run_id,
            )
        )

    # Unmet criteria → specific signals (e.g. unknown_enum:field, no_loss)
    for crit in unmet_criteria:
        if not isinstance(crit, str):
            crit = str(crit)
        if crit.startswith("unknown_enum"):
            # e.g. "unknown_enum:status" or "unknown_enum"
            field = crit.split(":", 1)[1] if ":" in crit else None
            signals.append(
                DriftSignal(
                    kind="unknown_enum",
                    entity=field,
                    severity="critical",
                    message=f"Unknown enum value blocked on {field or 'unknown field'}",
                    details={"unmet_criteria": crit},
                    source_run_id=source_run_id,
                )
            )
        elif crit == "no_loss":
            signals.append(
                DriftSignal(
                    kind="no_loss",
                    severity="high",
                    message="Invariant no_loss failed (record count mismatch)",
                    details={"unmet_criteria": crit},
                    source_run_id=source_run_id,
                )
            )
        elif crit in {
            "unique_key",
            "date_order",
            "transform_error",
            "money_invariant",
            "business_date_invariant",
            "count_invariant",
        }:
            signals.append(
                DriftSignal(
                    kind="invariant_failure",
                    severity="high",
                    message=f"Invariant {crit} failed",
                    details={"unmet_criteria": crit},
                    source_run_id=source_run_id,
                )
            )
        elif crit:
            # Generic invariant
            signals.append(
                DriftSignal(
                    kind="invariant_failure",
                    severity="high",
                    message=f"Invariant {crit} failed",
                    details={"unmet_criteria": crit},
                    source_run_id=source_run_id,
                )
            )

    # Blocked records → unknown_enum signal if not already covered
    if blocked_records:
        for ent, blocked in blocked_records.items():
            if not isinstance(blocked, list):
                continue
            for b in blocked:
                if not isinstance(b, dict):
                    continue
                field = b.get("field")
                # Avoid duplicating if unmet_criteria already named it, but still emit
                # per-entity signal for classifier richness.
                already = any(
                    s.kind == "unknown_enum" and s.entity == field for s in signals
                )
                if not already:
                    signals.append(
                        DriftSignal(
                            kind="unknown_enum",
                            entity=field,
                            operation=None,
                            severity="critical",
                            message=f"Blocked record in {ent} field {field}",
                            details={"entity": ent, "field": field, "blocked": b},
                            source_run_id=source_run_id,
                        )
                    )

    # Invariant results (failed checks) → invariant_failure if not already via unmet
    for inv in invariant_results:
        if not isinstance(inv, dict):
            continue
        check = inv.get("check")
        status = inv.get("status")
        if status == "failed" and check:
            # Check if we already have a signal for this check name
            already = any(
                s.details.get("unmet_criteria") == check or s.details.get("check") == check
                for s in signals
            )
            if not already:
                signals.append(
                    DriftSignal(
                        kind="invariant_failure",
                        severity="high",
                        message=f"Invariant {check} failed",
                        details={"check": check, "status": status},
                        source_run_id=source_run_id,
                    )
                )

    # Pagination signals: repeated_cursor and stop_reason analysis
    if isinstance(operation_results, dict):
        for op_name, res in operation_results.items():
            # repeated_cursor
            repeated = getattr(res, "repeated_cursor", None)
            if repeated is None and isinstance(res, dict):
                repeated = res.get("repeated_cursor")
            if repeated:
                signals.append(
                    DriftSignal(
                        kind="repeated_cursor",
                        operation=op_name,
                        severity="high",
                        message=f"Repeated cursor on {op_name} (pagination bug)",
                        details={"operation": op_name},
                        source_run_id=source_run_id,
                    )
                )
            # stop_reason signals (use spec to detect pagination_changed vs expected)
            stop_reason = getattr(res, "stop_reason", None)
            if stop_reason is None and isinstance(res, dict):
                stop_reason = res.get("stop_reason")
            # pages_fetched change signal handled via fingerprint delta below
            # But we surface stop_reason=repeated_cursor as repeated_cursor (above)
            # and stop_reason anomalies for observability
            if stop_reason in {"empty_page_implicit", "no_next_token"}:
                # Implicit stop → likely drift from expected pagination
                signals.append(
                    DriftSignal(
                        kind="pagination_changed",
                        operation=op_name,
                        severity="medium",
                        message=f"Implicit pagination stop {stop_reason} on {op_name}",
                        details={"stop_reason": stop_reason, "operation": op_name},
                        source_run_id=source_run_id,
                    )
                )

    # Zero records signals
    if isinstance(records_observed, dict):
        for ent, cnt in records_observed.items():
            try:
                c = int(cnt)
            except (TypeError, ValueError):
                continue
            if c == 0:
                # Only flag when outcome was not Complete with zero expected?
                # Always flag as signal; classifier will decide if it's capability removed.
                signals.append(
                    DriftSignal(
                        kind="zero_records",
                        entity=ent,
                        severity="high",
                        message=f"Zero records observed for {ent}",
                        details={"entity": ent, "records_observed": records_observed},
                        source_run_id=source_run_id,
                    )
                )
                # Zero records + Blocked often means endpoint removed
                if outcome == "Blocked":
                    signals.append(
                        DriftSignal(
                            kind="capability_removed",
                            entity=ent,
                            severity="critical",
                            message=f"Capability removed? {ent} returned zero with Blocked",
                            details={"entity": ent},
                            source_run_id=source_run_id,
                        )
                    )

    # HTTP error signals from operation results
    signals.extend(_extract_http_signals(operation_results, source_run_id=source_run_id))

    # Wire-observed evidence (REQ-027): what the vendor actually served on
    # the wire, derived only from raw_pages/records in operation_results.
    # Never from scenario ids, YAML or answer keys.
    signals.extend(
        _observe_wire_pagination(spec, operation_results, source_run_id=source_run_id)
    )
    signals.extend(
        _observe_wire_schema(spec, operation_results, source_run_id=source_run_id)
    )

    # Fingerprint delta signals (when caller supplies both fingerprints)
    if previous_fingerprint is not None and current_fingerprint is not None:
        delta = diff_fingerprints(previous_fingerprint, current_fingerprint)
        if delta.pagination_changed:
            signals.append(
                DriftSignal(
                    kind="pagination_changed",
                    severity="high",
                    message="Pagination shape drift detected",
                    details={"delta": delta.pagination_details},
                    source_run_id=source_run_id,
                    fingerprint_delta=delta,
                )
            )
        if delta.keys_changed:
            signals.append(
                DriftSignal(
                    kind="keys_changed",
                    severity="medium",
                    message="Entity keys fingerprint drift",
                    details={"delta": delta.keys_details},
                    source_run_id=source_run_id,
                    fingerprint_delta=delta,
                )
            )
        if delta.types_changed:
            signals.append(
                DriftSignal(
                    kind="types_changed",
                    severity="medium",
                    message="Field types drift detected",
                    details={"delta": delta.types_details},
                    source_run_id=source_run_id,
                    fingerprint_delta=delta,
                )
            )
        if delta.fingerprint_changed and not (
            delta.pagination_changed or delta.keys_changed or delta.types_changed
        ):
            # Fallback: fingerprint changed but no dimension flagged (e.g. spec_signature)
            signals.append(
                DriftSignal(
                    kind="fingerprint_changed",
                    severity="medium",
                    message="Fingerprint changed (spec or unknown dimension)",
                    details={},
                    source_run_id=source_run_id,
                    fingerprint_delta=delta,
                )
            )

    return signals


def signals_from_canary(
    canary_summary: dict[str, Any] | None,
    *,
    spec: dict[str, Any] | None = None,
    previous_fingerprint: Fingerprint | dict[str, Any] | str | None = None,
    current_fingerprint: Fingerprint | dict[str, Any] | None = None,
    source_run_id: str | None = None,
) -> list[DriftSignal]:
    """Collect signals from a canary probe.

    *canary_summary* is like a sync summary but for the single
    ``health.canary`` operation (max_pages=1).  When the canary failed its
    own ``execute_sync`` equivalent, it will have outcome Blocked/Degraded
    and http_errors.

    When the canary fingerprint is provided, drift vs the last successful
    sync is reported.
    """
    if not canary_summary:
        return []
    # Reuse sync signals extraction but tag kind as canary_failed where appropriate
    raw_signals = signals_from_sync(
        canary_summary,
        spec=spec,
        previous_fingerprint=previous_fingerprint,
        current_fingerprint=current_fingerprint,
        source_run_id=source_run_id,
    )
    # Rewrite critical outcome signals to canary namespace where they would
    # otherwise be ambiguous (keep original kind but add canary context).
    canary_op = None
    if isinstance(spec, dict):
        health = spec.get("health", {})
        if isinstance(health, dict):
            canary = health.get("canary", {})
            if isinstance(canary, dict):
                canary_op = canary.get("operation")

    out: list[DriftSignal] = []
    for sig in raw_signals:
        # If the signal is outcome_blocked/degraded on the canary, upgrade to canary_failed
        if sig.kind in {"outcome_blocked", "outcome_degraded"}:
            out.append(
                DriftSignal(
                    kind="canary_failed",
                    operation=canary_op or sig.operation,
                    severity="high",
                    message=f"Canary failed: {sig.message}",
                    evidence_ids=list(sig.evidence_ids),
                    details={**sig.details, "original_kind": sig.kind},
                    source_run_id=source_run_id,
                )
            )
        else:
            # For pagination_changed from canary, rename to canary_pagination_regression
            if sig.kind == "pagination_changed" and canary_op:
                out.append(
                    DriftSignal(
                        kind="canary_pagination_regression",
                        operation=canary_op,
                        severity="high",
                        message=f"Canary pagination regression on {canary_op}",
                        evidence_ids=list(sig.evidence_ids),
                        details=dict(sig.details),
                        source_run_id=source_run_id,
                        fingerprint_delta=sig.fingerprint_delta,
                    )
                )
            else:
                out.append(sig)

    # If canary had no raw signals but outcome was not Complete, ensure canary_failed
    if not raw_signals:
        outcome = canary_summary.get("outcome") if isinstance(canary_summary, dict) else None
        if outcome and outcome != "Complete":
            out.append(
                DriftSignal(
                    kind="canary_failed",
                    operation=canary_op,
                    severity="high",
                    message=f"Canary outcome {outcome}",
                    details={"outcome": outcome},
                    source_run_id=source_run_id,
                )
            )

    # If canary summary missing fingerprint but has http error, it will already be
    # in raw_signals via _extract_http_signals; no extra needed.

    return out


def collect_signals(
    sync_summary: dict[str, Any] | None,
    canary_summary: dict[str, Any] | None = None,
    *,
    spec: dict[str, Any] | None = None,
    previous_fingerprint: Fingerprint | dict[str, Any] | str | None = None,
    current_fingerprint: Fingerprint | dict[str, Any] | None = None,
    source_run_id: str | None = None,
) -> list[DriftSignal]:
    """Combine sync + canary signals into a single list.

    Deduplicates by (kind, operation, entity).  The caller (classifier) can
    further prioritize by severity.
    """
    signals: list[DriftSignal] = []
    if sync_summary:
        signals.extend(
            signals_from_sync(
                sync_summary,
                spec=spec,
                previous_fingerprint=previous_fingerprint,
                current_fingerprint=current_fingerprint,
                source_run_id=source_run_id,
            )
        )
    if canary_summary:
        canary_sigs = signals_from_canary(
            canary_summary,
            spec=spec,
            previous_fingerprint=previous_fingerprint,
            current_fingerprint=current_fingerprint,
            source_run_id=source_run_id,
        )
        signals.extend(canary_sigs)

    # Simple deduplication: keep first occurrence of (kind, operation, entity)
    seen: set[tuple[str, str | None, str | None]] = set()
    deduped: list[DriftSignal] = []
    for sig in signals:
        key = (sig.kind, sig.operation, sig.entity)
        if key not in seen:
            seen.add(key)
            deduped.append(sig)
    return deduped
