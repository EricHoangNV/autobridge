# ruff: noqa: B008, TC002, TC003, ARG001, SIM102, SIM105, SIM118, UP035, I001, UP006, UP007, RUF012, B027
"""Metrics endpoints (P1.W7.1)."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from autobridge.control.deps import get_db
from autobridge.control.models import ModelCall
from autobridge.control.schemas import ModelUsageEntry, ModelUsageResponse

router = APIRouter(tags=["metrics"])


@router.get("/metrics/model-usage", response_model=ModelUsageResponse)
def get_model_usage(db: Session = Depends(get_db)) -> ModelUsageResponse:
    rows = db.execute(select(ModelCall)).scalars().all()

    total_calls = len(rows)
    total_input = sum(r.input_tokens for r in rows)
    total_output = sum(r.output_tokens for r in rows)
    total_cost = sum((r.cost_estimate or Decimal("0") for r in rows), Decimal("0"))
    frontier_tokens = sum(r.input_tokens + r.output_tokens for r in rows if r.tier == "frontier")
    small_tokens = sum(r.input_tokens + r.output_tokens for r in rows if r.tier == "small")

    by_tier: dict[str, int] = {}
    by_provider: dict[str, int] = {}
    by_purpose: dict[str, int] = {}
    by_model: dict[tuple[str, str, str], list[ModelCall]] = {}
    for r in rows:
        by_tier[r.tier] = by_tier.get(r.tier, 0) + r.input_tokens + r.output_tokens
        by_provider[r.provider] = by_provider.get(r.provider, 0) + 1
        purpose = r.purpose or "unknown"
        by_purpose[purpose] = by_purpose.get(purpose, 0) + 1
        key = (r.provider, r.model, r.tier)
        by_model.setdefault(key, []).append(r)

    entries: list[ModelUsageEntry] = []
    for (provider, model, tier), calls in by_model.items():
        entries.append(
            ModelUsageEntry(
                provider=provider,
                model=model,
                tier=tier,
                total_calls=len(calls),
                total_input_tokens=sum(c.input_tokens for c in calls),
                total_output_tokens=sum(c.output_tokens for c in calls),
                total_cost=str(sum((c.cost_estimate or Decimal("0") for c in calls), Decimal("0"))),
                by_run=[{"run_id": c.run_id, "purpose": c.purpose, "tokens": c.input_tokens + c.output_tokens} for c in calls],
            )
        )

    return ModelUsageResponse(
        total_calls=total_calls,
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        total_cost=str(total_cost),
        frontier_tokens=frontier_tokens,
        small_tokens=small_tokens,
        by_tier=by_tier,
        by_provider=by_provider,
        entries=entries,
        by_purpose=by_purpose,
    )


@router.get("/metrics/sync-outcomes", response_model=dict)
def get_sync_metrics(db: Session = Depends(get_db)) -> dict:
    from autobridge.control.models import SyncRun

    rows = db.execute(select(SyncRun)).scalars().all()
    by_outcome: dict[str, int] = {}
    for r in rows:
        by_outcome[r.outcome] = by_outcome.get(r.outcome, 0) + 1
    return {"total": len(rows), "by_outcome": by_outcome}
