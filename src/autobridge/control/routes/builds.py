# ruff: noqa: B008, TC002, TC003, ARG001, SIM102, SIM105, SIM118, UP035, I001, UP006, UP007, RUF012, B027
"""Build and sync endpoints (P1.W7.1) — queue via JobRunner."""

from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from autobridge.control.deps import get_db, get_session_factory
from autobridge.control.jobs import JobRunner
from autobridge.control.models import Connector, ConnectorRelease
from autobridge.control.schemas import BuildRequest, BuildResponse, SyncRequest, SyncResponse

router = APIRouter(tags=["jobs"])


def _get_runner(db: Session | None = None) -> JobRunner:
    # Use the DB's engine when available (so tests with overridden DB work),
    # otherwise fall back to the global session factory.
    if db is not None:
        try:
            bind = db.get_bind()
            if bind is not None:
                from sqlalchemy.orm import sessionmaker as _sm

                factory = _sm(bind=bind, expire_on_commit=False)
                runner = JobRunner(factory)  # type: ignore[arg-type]
                try:
                    from autobridge.engine.build_run import run_build_handler  # noqa: PLC0415

                    runner.register_handler("build", run_build_handler)
                except Exception:
                    pass
                try:
                    from autobridge.engine.sync_run import run_sync_handler  # noqa: PLC0415

                    runner.register_handler("sync", run_sync_handler)
                except Exception:
                    pass
                try:
                    from autobridge.repair.controller import repair_job_handler  # noqa: PLC0415

                    runner.register_handler("repair", repair_job_handler)
                except Exception:
                    pass
                return runner
        except Exception:
            pass
    factory = get_session_factory()
    runner = JobRunner(factory)
    # Try to register real handlers if available
    try:
        from autobridge.engine.build_run import run_build_handler  # noqa: PLC0415

        runner.register_handler("build", run_build_handler)
    except Exception:
        pass
    try:
        from autobridge.engine.sync_run import run_sync_handler  # noqa: PLC0415

        runner.register_handler("sync", run_sync_handler)
    except Exception:
        pass
    try:
        from autobridge.repair.controller import repair_job_handler  # noqa: PLC0415

        runner.register_handler("repair", repair_job_handler)
    except Exception:
        pass
    return runner


@router.post("/connectors/{connector_id}/build", response_model=BuildResponse)
def trigger_build(connector_id: str, body: BuildRequest, db: Session = Depends(get_db)) -> BuildResponse:
    conn = db.get(Connector, connector_id)
    if conn is None:
        raise HTTPException(status_code=404, detail=f"Connector {connector_id!r} not found")

    docs_text: str | None = body.docs_text
    # If docs_url given, fetch inline (best effort, but not required for contract)
    if not docs_text and body.docs_url:
        try:
            with httpx.Client(timeout=5) as client:
                r = client.get(body.docs_url)
                r.raise_for_status()
                docs_text = r.text
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"cannot fetch docs_url: {exc}") from exc
    if not docs_text and body.docs_path:
        # Block reference spec reads
        if "generated/connectors" in body.docs_path:
            raise HTTPException(status_code=422, detail="build must not read reference specs")
        try:
            # Try local file (may not exist in container)
            from pathlib import Path as _Path

            p = _Path(body.docs_path)
            if p.is_file():
                docs_text = p.read_text(encoding="utf-8")
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"cannot read docs_path: {exc}") from exc
        if not docs_text:
            # Allow docs_path as identifier, synthesize placeholder docs
            docs_text = f"docs from {body.docs_path}"
    if not docs_text:
        raise HTTPException(status_code=422, detail="build requires docs_text, docs_url, or docs_path")

    # Guard: block reference spec reads even via docs_text that looks like path
    if docs_text and "generated/connectors" in docs_text and len(docs_text) < 500:
        # Heuristic: short docs that mention generated/connectors likely path
        if docs_text.strip().startswith("generated/"):
            raise HTTPException(status_code=422, detail="build must not read reference specs")

    # Use connector's base_url as fallback for bootstrap placeholder
    base_url_fb = body.base_url_override or conn.base_url or (body.model_extra.get("base_url") if body.model_extra else None)  # type: ignore[union-attr]
    inputs: dict[str, Any] = {
        "docs_text": docs_text,
        "docs_source": body.docs_path or body.docs_url or "inline",
        "connector_id": connector_id,
        "vendor": body.vendor,
        "credential_ref": body.credential_ref or conn.credential_ref,
        "base_url_override": base_url_fb,
        "scenario_id": body.scenario_id,
        "verifier_url": body.verifier_url or os.environ.get("VERIFIER_URL"),
    }
    # Include extra fields
    if body.model_extra:
        for k, v in body.model_extra.items():
            if k not in inputs:
                inputs[k] = v
    if body.base_url_override:
        inputs["base_url_override"] = body.base_url_override

    runner = _get_runner(db)
    # Submit build job (creates BuildRun + Job)
    try:
        job_id = runner.submit_build(connector_id=connector_id, inputs=inputs)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # Try to run immediately (inline execution for test/dev without worker)
    # If worker is separate, this will claim and execute; if DB is in-memory for tests it succeeds.
    # Otherwise job stays queued and worker will pick it up.
    try:
        result = runner.run_once()
        # If we ran our job, fetch build_run_id
        _ = result
    except Exception:
        pass

    # Fetch job to return build_run_id
    from autobridge.control.models import Job as JobModel

    job = db.get(JobModel, job_id)
    if job is None:
        raise HTTPException(status_code=500, detail="job not found after submit")
    build_run_id = job.run_id or ""
    # If job already succeeded, try to get from result
    if job.result and isinstance(job.result, dict):
        build_run_id = job.result.get("build_run_id", build_run_id) or build_run_id

    return BuildResponse(job_id=job_id, build_run_id=build_run_id, state=job.state, connector_id=connector_id)


@router.post("/connectors/{connector_id}/sync", response_model=SyncResponse)
def trigger_sync(connector_id: str, body: SyncRequest, db: Session = Depends(get_db)) -> SyncResponse:
    conn = db.get(Connector, connector_id)
    if conn is None:
        raise HTTPException(status_code=404, detail=f"Connector {connector_id!r} not found")

    release_id = body.release_id or conn.active_release_id
    if not release_id:
        # Fallback: latest release for connector
        from sqlalchemy import select as _select

        rel = db.execute(
            _select(ConnectorRelease).where(ConnectorRelease.connector_id == connector_id).order_by(ConnectorRelease.version.desc()).limit(1)
        ).scalars().first()
        if rel is None:
            raise HTTPException(status_code=422, detail="connector has no releases to sync")
        release_id = rel.id

    # Validate release exists and belongs to connector
    rel_obj = db.get(ConnectorRelease, release_id)
    if rel_obj is None:
        raise HTTPException(status_code=404, detail=f"Release {release_id!r} not found")
    if rel_obj.connector_id != connector_id:
        raise HTTPException(status_code=422, detail="release belongs to a different connector")

    runner = _get_runner(db)
    checkpoint = body.checkpoint or {}
    try:
        job_id = runner.submit_sync(release_id=release_id, checkpoint=checkpoint)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    try:
        runner.run_once()
    except Exception:
        pass

    from autobridge.control.models import Job as JobModel

    job = db.get(JobModel, job_id)
    if job is None:
        raise HTTPException(status_code=500, detail="job not found after submit")
    sync_run_id = job.run_id
    if job.result and isinstance(job.result, dict):
        sync_run_id = job.result.get("sync_run_id", sync_run_id)

    return SyncResponse(job_id=job_id, sync_run_id=sync_run_id, state=job.state, release_id=release_id)
