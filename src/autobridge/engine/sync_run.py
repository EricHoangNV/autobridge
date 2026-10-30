"""SyncRun execution with outcome determination (P1.W3.3 / P1.W3.4 / P1.W3.5).

Every sync run ends ``Complete | Degraded | Blocked`` with ``unmet_criteria``
named. Triggers:

* ``repeated_cursor`` → ``Degraded`` with ``no_loss``.
* invariant failures from checks → ``Degraded``.
* unknown enum (``enum_map`` on_unknown block) → ``Blocked`` with ``unknown_enum``.

Money is ``Decimal`` end-to-end and dates are property-local business dates via
the closed transform registry (``runtime/transforms``). Checks (``unique_key``,
``no_loss``, ``date_order``) and checkpoint extraction are wired here.

Trust boundary: no import of ``verifier`` or ``simulators``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from autobridge.control.models import SyncRun, utcnow
from autobridge.drift import fingerprint as _fp
from autobridge.runtime.checkpoint import compute_checkpoint_for_operation
from autobridge.runtime.checks import evaluate_entity_checks
from autobridge.runtime.http import RetryDeadlineExceeded
from autobridge.runtime.interpreter import Interpreter, OperationResult
from autobridge.runtime.mapping import map_records_to_entities

#: Default cumulative vendor-delay budget for one sync job (AR-2-1). The
#: sync handler passes this to the default ``HttpClient`` so a hostile or
#: misconfigured vendor answering every page with a huge ``Retry-After``
#: cannot stall the inline job (and the control request driving it)
#: indefinitely; the run ends ``Blocked`` with ``sync_deadline_exceeded``.
DEFAULT_SYNC_MAX_TOTAL_DELAY_S = 300.0


def _vocabulary_number(value: Any) -> int | None:
    """Parse a vocabulary/spec version (``"v1"``/``"V2"``/``1``/``2``) to a number."""
    text = str(value or "").strip().lower().removeprefix("v")
    return int(text) if text.isdigit() else None


def resolve_sync_vocabulary(
    session: Any,
    release: Any,
    requested: str | None = None,
) -> str:
    """Pin capping the runtime vocabulary for a sync (REQ-021 item 2).

    Precedence: explicit request pin, then the release's provenance build
    vocabulary (``BuildRun.inputs["spec_vocabulary"]`` — the pin the spec
    was built with), then the runtime maximum (everything the engine
    understands). The ``SPEC_VOCABULARY`` env default governs *builds*;
    a sync never narrows an existing release below what it was built with
    unless the operator pins it explicitly.
    """
    if requested in ("v1", "v2", "v3"):
        return requested
    build_vocab: str | None = None
    try:
        provenance_id = getattr(release, "provenance_build_run_id", None)
        if provenance_id is not None:
            from autobridge.control.models import BuildRun  # noqa: PLC0415

            build = session.get(BuildRun, provenance_id)
            if build is not None and isinstance(build.inputs, dict):
                candidate = build.inputs.get("spec_vocabulary")
                if candidate in ("v1", "v2", "v3"):
                    build_vocab = candidate
    except Exception:
        build_vocab = None
    if build_vocab is not None:
        return build_vocab
    from autobridge.config import runtime_max_vocabulary  # noqa: PLC0415

    return runtime_max_vocabulary()


def _fingerprint_config(spec: dict[str, Any]) -> dict[str, bool]:
    """Re-export for back-compat tests (delegates to drift.fingerprint)."""
    return _fp.fingerprint_config(spec)


def _hash_string(value: str) -> str:
    """Re-export for back-compat tests (delegates to drift.fingerprint)."""
    return _fp._hash_string(value)


def _fingerprint_id(
    spec: dict[str, Any],
    results: dict[str, OperationResult],
    canonical_records: dict[str, list[dict[str, Any]]] | None = None,
) -> str:
    """Re-export for back-compat tests (delegates to drift.fingerprint)."""
    return _fp.compute_fingerprint_id(spec, results, canonical_records)


def determine_outcome(
    results: dict[str, OperationResult],
    *,
    invariant_failures: list[str] | None = None,
    blocked_failures: list[str] | None = None,
) -> tuple[str, list[str]]:
    """Map pagination + invariant results to (outcome, unmet_criteria).

    * ``blocked_failures`` non-empty → ``Blocked`` (unknown enum, etc.).
    * ``repeated_cursor`` → ``Degraded`` with ``no_loss``.
    * ``invariant_failures`` non-empty → ``Degraded`` with those names.
    * otherwise → ``Complete``.

    Blocked takes precedence over Degraded.
    """
    blocked = list(blocked_failures or [])
    if blocked:
        return "Blocked", blocked

    unmet: list[str] = list(invariant_failures or [])

    # repeated_cursor is the W3.3 Degraded trigger
    for res in results.values():
        if (res.repeated_cursor or res.stop_reason == "repeated_cursor") and "no_loss" not in unmet:
            unmet.append("no_loss")

    if unmet:
        return "Degraded", unmet
    return "Complete", []


def compute_data_readiness(
    outcome: str,
    results: dict[str, OperationResult] | None = None,  # noqa: ARG001
    records_observed: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Data readiness from outcome + pagination results.

    * Complete → {freshness: current, completeness: complete}
    * Degraded → {freshness: current, completeness: partial} (some data, invariant failed)
    * Blocked → {freshness: stale, completeness: incomplete} (cannot get current data)
    * Degraded with no records → stale (edge case for empty degraded)
    """
    if outcome == "Complete":
        return {"freshness": "current", "completeness": "complete"}
    if outcome == "Degraded":
        total = sum(records_observed.values()) if records_observed else None
        if total == 0:
            return {"freshness": "stale", "completeness": "partial"}
        # If any pagination stop was repeated_cursor, still current/partial (we stopped early)
        return {"freshness": "current", "completeness": "partial"}
    if outcome == "Blocked":
        return {"freshness": "stale", "completeness": "incomplete"}
    # Fallback for unknown
    return {"freshness": "current", "completeness": "partial"}


def _extract_compare_to_value(
    results: dict[str, OperationResult],
    operation_name: str,
    compare_to: str | None,
) -> Any | None:
    """Extract compare_to value from the first raw page of an operation."""
    if not compare_to:
        return None
    res = results.get(operation_name)
    if res is None or not res.raw_pages:
        return None
    # Try first page
    for page in res.raw_pages:
        try:
            from autobridge.runtime.interpreter import extract_jsonpath

            val = extract_jsonpath(page, compare_to)
            if val is not None:
                return val
        except Exception:
            continue
    return None


def execute_sync(
    spec_dict: dict[str, Any],
    http_client: Any,
    *,
    checkpoint: dict[str, Any] | None = None,
    max_pages: int = 1000,
    feed_reader: Any | None = None,
) -> dict[str, Any]:
    """Execute *spec_dict* via the interpreter and return a sync summary.

    Steps:
      1. Fetch raw pages per operation via :class:`Interpreter` (HTTP ops
         via *http_client*, EVENT feed ops via *feed_reader*).
      2. Map raw records to canonical entities via transforms (may Block on unknown enum).
      3. Run entity checks (unique_key, no_loss, date_order).
      4. Determine outcome (Complete | Degraded | Blocked) and data_readiness.

    Returns dict with keys: outcome, unmet_criteria, data_readiness,
    fingerprint_id, operation_results, records_observed, canonical_records,
    blocked_records, invariant_results, checkpoint_values.

    Does not touch the DB — callers create the SyncRun row from this.
    """
    from autobridge.runtime.feeds import FeedError
    from autobridge.spec.models import load_spec_from_dict

    spec = load_spec_from_dict(spec_dict)
    interp = Interpreter(spec, http_client=http_client, feed_reader=feed_reader)
    try:
        results = interp.execute_all(checkpoint=checkpoint, max_pages=max_pages)
    except FeedError as exc:
        # Event-feed transport failure (no reader, relay unreachable, token
        # missing): Blocked with the feed criterion named, never guessed.
        return {
            "outcome": "Blocked",
            "unmet_criteria": ["feed_unavailable"],
            "data_readiness": {"freshness": "stale", "completeness": "incomplete"},
            "fingerprint_id": _fp.compute_fingerprint_id(spec_dict, {}, {}),
            "operation_results": {},
            "records_observed": {},
            "checkpoint_values": {},
            "canonical_records": {},
            "blocked_records": {},
            "invariant_results": [{"check": "feed_unavailable", "status": "failed"}],
            "error": str(exc),
            "http_status": None,
        }
    except Exception as exc:
        # Transport / HTTP failure (404 endpoint_removed, 403 token_revoked, etc.)
        # → Blocked with named criterion for classifier.

        status = getattr(exc, "status_code", None)
        msg = str(exc).lower()
        unmet: list[str] = []
        # A vendor that stalls via huge Retry-After values trips the
        # cumulative delay budget first (AR-2-1): name it explicitly.
        if isinstance(exc, RetryDeadlineExceeded):
            unmet = ["sync_deadline_exceeded"]
        # Infer Blocked reason from status or message
        elif status == 404 or "404" in msg or "not found" in msg or "endpoint" in msg:
            unmet = ["endpoint_not_found"]
        elif status == 403 or "403" in msg or "revoked" in msg:
            unmet = ["auth_revoked"]
            if "token_revoked" not in unmet:
                unmet.append("token_revoked")
        elif status == 401 or "401" in msg or "expired" in msg:
            unmet = ["auth_expired"]
        elif status == 429 or "429" in msg:
            unmet = ["rate_limited"]
        elif status == 503 or "503" in msg:
            unmet = ["transient_error"]
        else:
            unmet = ["http_error"]
        # Also add generic auth/capability hints for classifier
        if "revoked" in msg and "auth_revoked" not in unmet:
            unmet.append("auth_revoked")
        if "capability" in msg and "endpoint_not_found" not in unmet:
            unmet.append("endpoint_not_found")
        return {
            "outcome": "Blocked",
            "unmet_criteria": unmet,
            "data_readiness": {"freshness": "stale", "completeness": "incomplete"},
            "fingerprint_id": _fp.compute_fingerprint_id(spec_dict, {}, {}),
            "operation_results": {},
            "records_observed": {},
            "checkpoint_values": {},
            "canonical_records": {},
            "blocked_records": {},
            "invariant_results": [{"check": c, "status": "failed"} for c in unmet],
            "error": str(exc),
            "http_status": status,
        }

    # Phase: mapping raw → canonical per entity
    canonical_records: dict[str, list[dict[str, Any]]] = {}
    blocked_records: dict[str, list[dict[str, Any]]] = {}
    all_blocked_failures: list[str] = []
    invariant_failures: list[str] = []

    # Declarative joins (spec v2): enrich base records from other
    # operations before field mapping; a no-op for entities without joins.
    from autobridge.runtime.joins import resolve_joins

    enriched_by_entity = resolve_joins(spec, results)

    for ent_name, ent_spec in spec.entities.items():
        op_name = ent_spec.from_operation
        res = results.get(op_name)
        if res is None:
            canonical_records[ent_name] = []
            continue
        raw_records = enriched_by_entity.get(ent_name, res.records)

        # Map via transforms
        canonical, blocked = map_records_to_entities(
            raw_records,
            ent_spec,
            source_spec=spec.source,
            entity_name=ent_name,
        )
        canonical_records[ent_name] = canonical
        if blocked:
            blocked_records[ent_name] = blocked
            # Unknown enum → Blocked with unmet_criteria naming the failure
            # Collect distinct blocked reasons
            for b in blocked:
                # b may contain 'field' for enum
                field = b.get("field")
                if field:
                    tag = f"unknown_enum:{field}"
                    if tag not in all_blocked_failures:
                        all_blocked_failures.append(tag)
                    # Also add generic for verifier parity
                    if "unknown_enum" not in all_blocked_failures:
                        all_blocked_failures.append("unknown_enum")
                elif b.get("blocked") is False:
                    # Non-blocking transform error → degrade
                    if "transform_error" not in invariant_failures:
                        invariant_failures.append("transform_error")
                else:
                    if "unknown_enum" not in all_blocked_failures:
                        all_blocked_failures.append("unknown_enum")

        # Run checks for this entity
        # Gather compare_to extractions (no_loss count + money sum)
        compare_to_val: Any | None = None
        money_expected_sum: Any | None = None
        for chk in ent_spec.checks:
            # chk may be pydantic model
            kind = getattr(chk, "kind", None) if not isinstance(chk, dict) else chk.get("kind")
            compare_to = getattr(chk, "compare_to", None) if not isinstance(chk, dict) else chk.get("compare_to")
            if compare_to:
                extracted = _extract_compare_to_value(results, op_name, compare_to)
                if kind == "money_invariant":
                    money_expected_sum = extracted
                elif compare_to_val is None:
                    compare_to_val = extracted
                # For money, also keep as fallback in compare_to_val if no other
                if money_expected_sum is None and kind == "money_invariant" and extracted is not None:
                    money_expected_sum = extracted

        # Evaluate checks against canonical records
        # Pass money sum via compare_to_values dict keyed by entity
        if money_expected_sum is not None or compare_to_val is not None:
            pass

        check_results = evaluate_entity_checks(
            ent_name,
            canonical,
            ent_spec.checks,
            expected_count=compare_to_val if isinstance(compare_to_val, int) else None,
            compare_to_value=compare_to_val,
        )
        # If money expected sum was separate, inject via run_checks directly
        if money_expected_sum is not None:

            # Re-run money check with correct expected sum (avoid double count for no_loss)
            # We already evaluated all checks, but money with gross_sum may have used wrong compare_to
            # So we patch: if money_invariant check used expected_sum from no_loss, fix it
            for chk in ent_spec.checks:
                k = getattr(chk, "kind", None) if not isinstance(chk, dict) else chk.get("kind")
                if k == "money_invariant":
                    field = getattr(chk, "field", None) if not isinstance(chk, dict) else chk.get("field")
                    if field:
                        from autobridge.runtime.checks import check_money_invariant as _cmi

                        m_res = _cmi(canonical, field=field, entity_name=ent_name, expected_sum=str(money_expected_sum) if money_expected_sum is not None else None, tolerance_pct=5.0)
                        for cr in m_res:
                            if cr not in check_results and cr not in invariant_failures and cr not in all_blocked_failures:
                                check_results.append(cr)
        for cr in check_results:
            if cr not in invariant_failures and cr not in all_blocked_failures:
                invariant_failures.append(cr)

        # Update checkpoint via checkpoint.py (override interpreter's if needed)
        # Interpreter already computed checkpoint_value per operation, but we recompute from canonical if field differs
        # Keep existing but also recompute from canonical for correctness
        if ent_spec and canonical:
            # Checkpoint is per operation, not entity; we use operation's checkpoint spec
            op = spec.operations.get(op_name)
            if op and op.checkpoint:
                canon_cp = compute_checkpoint_for_operation(canonical, op.checkpoint)
                # Prefer canonical-derived if present
                if canon_cp is not None:
                    # Store per-operation canonical checkpoint later
                    pass

    # Determine outcome
    if all_blocked_failures:
        outcome, unmet = determine_outcome(results, invariant_failures=invariant_failures, blocked_failures=all_blocked_failures)
    else:
        outcome, unmet = determine_outcome(results, invariant_failures=invariant_failures)

    # Records observed: canonical counts (post-transform)
    records_observed: dict[str, int] = {}
    for ent_name in spec.entities:
        records_observed[ent_name] = len(canonical_records.get(ent_name, []))

    data_readiness = compute_data_readiness(outcome, results, records_observed)
    fingerprint_id = _fingerprint_id(spec_dict, results, canonical_records)

    # Checkpoint values: prefer interpreter's but also include canonical
    checkpoint_values: dict[str, Any] = {}
    for op_name, res in results.items():
        if res.checkpoint_value is not None:
            checkpoint_values[op_name] = res.checkpoint_value
    # Recompute from canonical per operation (overrides if present)
    for ent_name, ent_spec in spec.entities.items():
        op_name = ent_spec.from_operation
        op = spec.operations.get(op_name)
        if op and op.checkpoint and canonical_records.get(ent_name):
            canon_cp = compute_checkpoint_for_operation(canonical_records[ent_name], op.checkpoint)
            if canon_cp is not None:
                checkpoint_values[op_name] = canon_cp

    # Build invariant_results for DB (list of dicts)
    invariant_results: list[dict[str, Any]] = []
    for name in unmet:
        invariant_results.append({"check": name, "status": "failed"})

    return {
        "outcome": outcome,
        "unmet_criteria": unmet,
        "data_readiness": data_readiness,
        "fingerprint_id": fingerprint_id,
        "operation_results": results,
        "records_observed": records_observed,
        "checkpoint_values": checkpoint_values,
        "canonical_records": canonical_records,
        "blocked_records": blocked_records,
        "invariant_results": invariant_results,
    }


def create_sync_run_row(
    *,
    session: Any,
    release_id: str,
    outcome: str,
    unmet_criteria: list[str],
    data_readiness: dict[str, Any],
    records_observed: dict[str, int],
    fingerprint_id: str,
    checkpoint: dict[str, Any] | None = None,
    invariant_results: list[dict[str, Any]] | None = None,
) -> SyncRun:
    """Insert a SyncRun row with the computed outcome."""
    now = utcnow()
    run = SyncRun(
        release_id=release_id,
        started_at=now,
        finished_at=datetime.now(UTC),
        outcome=outcome,
        unmet_criteria=list(unmet_criteria),
        data_readiness=dict(data_readiness),
        records_observed=dict(records_observed),
        records_emitted=dict(records_observed),
        checkpoint=dict(checkpoint or {}),
        invariant_results=list(invariant_results or []),
        fingerprint_id=fingerprint_id,
    )
    session.add(run)
    session.flush()
    return run


def run_sync_handler(
    ctx: Any,
    *,
    http_client_factory: Any | None = None,
    spec_loader: Any | None = None,
) -> dict[str, Any]:
    """JobRunner handler for sync jobs (wires execute_sync to DB).

    Expected payload: {release_id, checkpoint?}
    Uses the release's stored spec; http_client_factory lets tests inject a fake.
    """
    from autobridge.control.models import ConnectorRelease

    payload = ctx.payload or {}
    release_id = payload.get("release_id")
    if not release_id:
        raise ValueError("sync job payload needs 'release_id'")
    release = ctx.session.get(ConnectorRelease, release_id)
    if release is None:
        raise ValueError(f"ConnectorRelease {release_id!r} not found")

    spec_dict = release.spec
    if spec_loader is not None:
        # Test hook: allow spec override
        spec_dict = spec_loader(spec_dict)

    # REQ-021 item 2: pinned vocabulary caps the runtime. A spec whose
    # spec_version exceeds the pin refuses deterministically — Blocked with
    # a named reason, before any HTTP — so no v2 construct executes under a
    # v1 pin. Unknown/missing versions fail closed (DIRECTIVES §4).
    pin = resolve_sync_vocabulary(ctx.session, release, payload.get("spec_vocabulary"))
    pin_number = _vocabulary_number(pin)
    spec_number = _vocabulary_number(
        spec_dict.get("spec_version") if isinstance(spec_dict, dict) else None
    )
    if spec_number is None:
        run = create_sync_run_row(
            session=ctx.session,
            release_id=release_id,
            outcome="Blocked",
            unmet_criteria=["unknown_spec_version"],
            data_readiness={},
            records_observed={},
            fingerprint_id="",
            checkpoint={},
            invariant_results=[],
        )
        ctx.run_id = run.id
        return {
            "sync_run_id": run.id,
            "outcome": "Blocked",
            "unmet_criteria": ["unknown_spec_version"],
            "fingerprint_id": "",
        }
    if pin_number is not None and spec_number > pin_number:
        reason = f"vocabulary_{pin}_cannot_execute_spec_v{spec_number}"
        run = create_sync_run_row(
            session=ctx.session,
            release_id=release_id,
            outcome="Blocked",
            unmet_criteria=[reason],
            data_readiness={},
            records_observed={},
            fingerprint_id="",
            checkpoint={},
            invariant_results=[],
        )
        ctx.run_id = run.id
        return {
            "sync_run_id": run.id,
            "outcome": "Blocked",
            "unmet_criteria": [reason],
            "fingerprint_id": "",
        }

    # Resolve http client
    feed_reader = None
    if http_client_factory is not None:
        http_client = http_client_factory(spec_dict)
    elif isinstance(spec_dict, dict) and isinstance(spec_dict.get("source"), dict) and spec_dict["source"].get("protocol") == "fias_tcp":
        # Event-driven transport: no vendor HTTP client exists. The control
        # plane drains pushed records through the edge relay outbox with
        # the relay operator token the spec's edge_relay auth names.
        from autobridge.runtime.feeds import RelayFeedReader  # noqa: PLC0415
        from autobridge.spec.models import load_spec_from_dict as _load

        http_client = None
        feed_reader = RelayFeedReader.from_env(_load(spec_dict).source.auth)
    else:
        # Build from spec.source, with a cumulative vendor-delay budget so a
        # hostile Retry-After cannot stall this (inline) job (AR-2-1).
        from autobridge.runtime.http import HttpClient
        from autobridge.spec.models import load_spec_from_dict

        spec = load_spec_from_dict(spec_dict)
        http_client = HttpClient(
            base_url=spec.source.base_url,
            auth=spec.source.auth,
            retry=spec.source.rate_limit.retry,
            max_total_delay_s=DEFAULT_SYNC_MAX_TOTAL_DELAY_S,
        )

    checkpoint = payload.get("checkpoint", {}) or {}
    summary = execute_sync(spec_dict, http_client, checkpoint=checkpoint, feed_reader=feed_reader)

    run = create_sync_run_row(
        session=ctx.session,
        release_id=release_id,
        outcome=summary["outcome"],
        unmet_criteria=summary["unmet_criteria"],
        data_readiness=summary["data_readiness"],
        records_observed=summary["records_observed"],
        fingerprint_id=summary["fingerprint_id"],
        checkpoint=summary["checkpoint_values"],
        invariant_results=summary.get("invariant_results", []),
    )
    ctx.run_id = run.id

    # REQ-027 sub-step 3 (REQ-042 revision): the live chain. Every sync
    # detects drift from its own summary (observed responses only) and
    # arranges its repair: deterministic drifts repair inline here, while
    # a semantic drift needing a live frontier call is queued as a
    # background repair job — no model call ever runs inside this handler
    # (which executes inline in POST /sync) or across its transaction.
    # DriftEvent + RepairRun (+ candidate) rows are persisted here, so
    # GET /drift and GET /repairs serve them; detection never fails sync.
    detection: dict[str, Any] = {"drift_found": False}
    try:
        from autobridge.engine.detect_repair import detect_after_sync  # noqa: PLC0415

        connector_id = release.connector_id if release is not None else ""
        detection = detect_after_sync(
            ctx.session,
            connector_id=connector_id,
            release_id=release_id,
            spec_dict=spec_dict,
            summary=summary,
            sync_run_id=run.id,
        )
    except Exception as exc:  # noqa: BLE001 — detection failures are row data
        detection = {"drift_found": False, "detection_error": f"{type(exc).__name__}: {exc}"[:500]}

    out: dict[str, Any] = {
        "sync_run_id": run.id,
        "outcome": summary["outcome"],
        "unmet_criteria": summary["unmet_criteria"],
        "fingerprint_id": summary["fingerprint_id"],
    }
    out.update(detection)
    return out
