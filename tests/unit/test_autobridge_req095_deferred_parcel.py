"""REQ-095 item 1: the deferred repair parcel survives Decimal evidence.

Live failure (2026-11-03, keyed demo recording): every sync detected the
Atlas pagination drift, then failed to queue the deferred repair job with
``Object of type Decimal is not JSON serializable`` (jobs table, JSON
payload), so the drift stayed open with no repair. Since REQ-078 every
repairable class defers to a background job when the provider is live,
and REQ-078/REQ-080 put canonical record samples (Decimal money) into
that job's evidence — including raw added-key samples on
``new_wire_keys[].sample/samples``.

The hot-fix (``_submit_deferred_model_repair`` makes the parcel JSON-safe
before insert) stays; this test pins it: a sync summary whose canonical
records carry Decimal amounts and whose evidence includes the record
sample must queue a job whose payload round-trips through strict
``json.dumps`` and through ``repair_job_handler`` (rule-level proposal,
no model call).
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any


def _atlas_spec() -> dict[str, Any]:
    return {
        "spec_version": 3,
        "connector": {"id": "atlas", "vendor": "atlas"},
        "source": {
            "base_url": "http://atlas:8000",
            "auth": {"type": "bearer", "credential_ref": "ATLAS_API_KEY"},
            "timezone_source": {"kind": "static", "value": "UTC"},
        },
        "operations": {
            "list_reservations": {
                "method": "GET",
                "path": "/reservations",
                "pagination": {
                    "strategy": "page",
                    "page_param": "page",
                    "items_path": "$.data",
                    "stop_when": ["empty_page"],
                    # All strategy params present so a strategy-flip
                    # patch stays schema-valid from this base.
                    "cursor_param": "cursor",
                    "next_path": "$.next",
                    "offset_param": "offset",
                },
            }
        },
        "entities": {
            "Reservation": {
                "from": "list_reservations",
                "key": "$.reservation_id",
                "fields": {
                    "reservation_id": {"path": "$.reservation_id"},
                    "total_amount": {"path": "$.total_amount"},
                },
                "checks": [],
            },
        },
        "capabilities": {"read": ["Reservation"]},
        "health": {"canary": {"every": "60s", "operation": "list_reservations"}},
    }


def _faulted_summary() -> dict[str, Any]:
    """The live shape: Decimal money in canonical AND wire records."""
    return {
        "outcome": "Degraded",
        "unmet_criteria": ["pagination_changed"],
        "operation_results": {
            "list_reservations": {
                "records": [
                    {
                        "reservation_id": "r1",
                        "total_amount": Decimal("100.00"),
                    }
                ],
                "pages_fetched": 2,
                "raw_pages": [
                    {
                        "gross_sum": Decimal("300.00"),
                        "data": [
                            {
                                "reservation_id": "r1",
                                "total_amount": Decimal("100.00"),
                            }
                        ],
                    }
                ],
                "stop_reason": "complete",
            }
        },
        "records_observed": {"Reservation": 1},
        "canonical_records": {
            "Reservation": [
                {"reservation_id": "r1", "total_amount": Decimal("100.00")}
            ],
        },
        "invariant_results": [],
        "checkpoint_values": {},
    }


def _mem_factory() -> Any:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from autobridge.control.models import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed(factory: Any, spec: dict[str, Any]) -> tuple[str, str]:
    from autobridge.control.models import Connector, ConnectorRelease
    from autobridge.control.models import spec_signature as _sig

    with factory() as s:
        conn = Connector(
            name="atlaspms",
            base_url="http://localhost:8105",
            credential_ref="ATLAS_API_KEY",
            target_entities=["Reservation"],
            runtime_target={"kind": "local_process"},
        )
        s.add(conn)
        s.flush()
        rel = ConnectorRelease(
            connector_id=conn.id, version=1, spec=spec, signature=_sig(spec)
        )
        s.add(rel)
        s.flush()
        conn.active_release_id = rel.id
        s.commit()
        return conn.id, rel.id


def test_deferred_parcel_with_decimal_evidence_round_trips_and_climbs() -> None:
    """Decimal canonical records + record sample: queue, strict dumps, climb."""
    from autobridge.control.jobs import JobContext
    from autobridge.control.models import DriftEvent
    from autobridge.drift.signals import DriftSignal
    from autobridge.engine.detect_repair import _submit_deferred_model_repair
    from autobridge.repair.controller import prepare_climb_inputs, repair_job_handler
    from autobridge.repair.dryrun import build_dry_run_fault

    spec = _atlas_spec()
    summary = _faulted_summary()
    signals = [
        DriftSignal(
            kind="pagination_changed",
            operation="list_reservations",
            entity="Reservation",
            severity="high",
            message="page to cursor",
            details={
                "entity": "Reservation",
                "spec_strategy": "page",
                "wire_strategy_hint": "cursor",
                "cursor_field": "next_cursor",
                "cursor_sample": "abc123",
            },
        ),
        # REQ-080 added-key sample, raw from the detector: Decimal,
        # exactly what made the live insert raise before the hot-fix.
        DriftSignal(
            kind="keys_changed",
            operation="list_reservations",
            entity="Reservation",
            severity="medium",
            message="new wire keys",
            details={
                "entity": "Reservation",
                "added": [
                    {
                        "key": "next_cursor",
                        "sample": Decimal("5"),
                        "samples": [Decimal("5"), Decimal("6")],
                    }
                ],
            },
        ),
    ]
    prepared = prepare_climb_inputs(
        spec=spec,
        signals=list(signals),
        summary=summary,
        sync_run_id="sync-req095-1",
        drift_class="PAGINATION_CHANGE",
    )
    evidence = prepared.get("evidence") or {}
    assert (evidence.get("new_wire_keys") or []), (
        "the test must carry the record sample that broke the live parcel"
    )
    dry_fault = build_dry_run_fault(summary)
    assert dry_fault is not None

    factory = _mem_factory()
    conn_id, rel_id = _seed(factory, spec)
    with factory() as s:
        drift = DriftEvent(
            connector_id=conn_id,
            drift_class="PAGINATION_CHANGE",
            severity="high",
            source_sync_run_id="sync-req095-1",
            status="open",
        )
        s.add(drift)
        s.commit()
        drift_id = drift.id

    # The live failure raised here (SQLite JSON insert of Decimals).
    with factory() as s:
        job_id = _submit_deferred_model_repair(
            s,
            drift_id=drift_id,
            drift_class="PAGINATION_CHANGE",
            connector_id=conn_id,
            release_id=rel_id,
            prepared=prepared,
            signals=list(signals),
            delta={
                "pagination_details": {
                    "list_reservations": {
                        "previous": {"strategy": "page"},
                        "current": {"strategy": "cursor"},
                    }
                }
            },
            dry_run_fault=dry_fault,
        )
        s.commit()

    from autobridge.control.models import Job

    with factory() as s:
        job = s.get(Job, job_id)
        assert job is not None and job.state == "queued" and job.kind == "repair"
        # Strict: no default=str — the stored parcel itself must be clean.
        strict = json.dumps(job.payload)
        payload = json.loads(strict)

    # The parcel the job stored must drive the real handler: strict
    # round-trip, then the climb (L1 page_to_cursor, 0 model calls).
    import autobridge.llm.provider as provider_mod
    from autobridge.llm.provider import Completion, FakeProvider, Usage

    fake = FakeProvider(
        responses={
            "frontier": Completion(
                text='{"escalate": "unreachable: L1 must answer first"}',
                usage=Usage(input_tokens=10, output_tokens=5),
            )
        }
    )
    orig = provider_mod.resolve_frontier_provider
    provider_mod.resolve_frontier_provider = lambda: (fake, "replay")  # type: ignore[assignment]
    try:
        with factory() as s:
            ctx = JobContext(
                job_id=job_id,
                kind="repair",
                run_id=None,
                payload=payload,
                attempt=1,
                session=s,
                worker_id="w-req095",
            )
            out = repair_job_handler(ctx)
            s.commit()
    finally:
        provider_mod.resolve_frontier_provider = orig
    assert out["decision"] == "propose", out
    assert out["rule_id"] == "page_to_cursor", out
    assert out["ladder_level_reached"] == 1, out
    assert len(fake.calls) == 0, "the L1 rule answers; no model call may run"


def test_dry_run_fault_is_strict_json_safe_with_decimals() -> None:
    """The dry-run half of the parcel: Decimal summary in, strict JSON out."""
    from autobridge.repair.dryrun import build_dry_run_fault

    fault = build_dry_run_fault(_faulted_summary())
    assert fault is not None
    json.dumps(fault)  # strict: raises before the REQ-095 parcel fix
