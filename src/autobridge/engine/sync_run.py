"""SyncRun execution with outcome determination (P1.W3.3 / P1.W3.5).

Every sync run ends ``Complete | Degraded | Blocked`` with ``unmet_criteria``
named. For this workstream the only ``Degraded`` trigger is a
``repeated_cursor`` pagination stop — it is surfaced as ``no_loss`` so
consumers know records may be missing. Money is ``Decimal`` end-to-end and
dates are property-local business dates; the transform and check layers
(W3.4) flesh that out, so this module keeps the wiring but does not yet
implement them.

Trust boundary: no import of ``verifier`` or ``simulators``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from autobridge.control.models import SyncRun, utcnow
from autobridge.runtime.interpreter import Interpreter, OperationResult


def _fingerprint_id(spec: dict[str, Any], results: dict[str, OperationResult]) -> str:
    """Stable fingerprint id for this run (hash over spec signature + page counts)."""
    payload = {
        "spec_signature": hashlib.sha256(
            json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "pages": {k: v.pages_fetched for k, v in results.items()},
        "stop_reasons": {k: v.stop_reason for k, v in results.items()},
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def determine_outcome(
    results: dict[str, OperationResult],
    *,
    invariant_failures: list[str] | None = None,
) -> tuple[str, list[str]]:
    """Map pagination + invariant results to (outcome, unmet_criteria).

    * ``repeated_cursor`` → ``Degraded`` with ``no_loss``.
    * ``invariant_failures`` non-empty → ``Degraded`` with those names.
    * otherwise → ``Complete``.

    ``Blocked`` is reserved for unknown enum values (W3.4) and auth
    failures (W6) and is not produced here.
    """
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
) -> dict[str, Any]:
    """Data readiness from outcome + pagination stops.

    * Complete → {freshness: current, completeness: complete}
    * Degraded with no_loss → {freshness: current, completeness: partial}
    * Degraded otherwise → {freshness: current, completeness: partial}
    """
    if outcome == "Complete":
        return {"freshness": "current", "completeness": "complete"}
    return {"freshness": "current", "completeness": "partial"}


def execute_sync(
    spec_dict: dict[str, Any],
    http_client: Any,
    *,
    checkpoint: dict[str, Any] | None = None,
    max_pages: int = 1000,
) -> dict[str, Any]:
    """Execute *spec_dict* via the interpreter and return sync summary.

    Returns dict with keys: outcome, unmet_criteria, data_readiness,
    fingerprint_id, operation_results, records_observed, checkpoint_values.

    Does not touch the DB — callers create the SyncRun row from this.
    """
    from autobridge.spec.models import load_spec_from_dict

    spec = load_spec_from_dict(spec_dict)
    interp = Interpreter(spec, http_client=http_client)
    results = interp.execute_all(checkpoint=checkpoint, max_pages=max_pages)

    outcome, unmet = determine_outcome(results)
    data_readiness = compute_data_readiness(outcome, results)
    fingerprint_id = _fingerprint_id(spec_dict, results)

    records_observed: dict[str, int] = {}
    for ent_name, ent_spec in spec.entities.items():
        op_name = ent_spec.from_operation
        res = results.get(op_name)
        if res is not None:
            records_observed[ent_name] = len(res.records)
        else:
            records_observed[ent_name] = 0

    checkpoint_values: dict[str, Any] = {}
    for op_name, res in results.items():
        if res.checkpoint_value is not None:
            checkpoint_values[op_name] = res.checkpoint_value

    return {
        "outcome": outcome,
        "unmet_criteria": unmet,
        "data_readiness": data_readiness,
        "fingerprint_id": fingerprint_id,
        "operation_results": results,
        "records_observed": records_observed,
        "checkpoint_values": checkpoint_values,
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

    # Resolve http client
    if http_client_factory is not None:
        http_client = http_client_factory(spec_dict)
    else:
        # Build from spec.source
        from autobridge.runtime.http import HttpClient
        from autobridge.spec.models import load_spec_from_dict

        spec = load_spec_from_dict(spec_dict)
        http_client = HttpClient(
            base_url=spec.source.base_url,
            auth=spec.source.auth,
            retry=spec.source.rate_limit.retry,
        )

    checkpoint = payload.get("checkpoint", {}) or {}
    summary = execute_sync(spec_dict, http_client, checkpoint=checkpoint)

    run = create_sync_run_row(
        session=ctx.session,
        release_id=release_id,
        outcome=summary["outcome"],
        unmet_criteria=summary["unmet_criteria"],
        data_readiness=summary["data_readiness"],
        records_observed=summary["records_observed"],
        fingerprint_id=summary["fingerprint_id"],
        checkpoint=summary["checkpoint_values"],
        invariant_results=[],
    )
    ctx.run_id = run.id
    return {
        "sync_run_id": run.id,
        "outcome": summary["outcome"],
        "unmet_criteria": summary["unmet_criteria"],
        "fingerprint_id": summary["fingerprint_id"],
    }
