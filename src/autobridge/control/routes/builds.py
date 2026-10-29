# ruff: noqa: B008, TC002, TC003, ARG001, SIM102, SIM105, SIM118, UP035, I001, UP006, UP007, RUF012, B027
"""Build and sync endpoints (P1.W7.1) — queue via JobRunner."""

from __future__ import annotations

import concurrent.futures
import ipaddress
import os
import socket
import urllib.parse
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Security
from sqlalchemy import select
from sqlalchemy.orm import Session

from autobridge.control.demo_targets import normalize_connector_base_url
from autobridge.control.deps import get_db, get_session_factory, require_control_token
from autobridge.control.jobs import JobRunner
from autobridge.control.models import Approval, BuildRun, Connector, ConnectorRelease
from autobridge.control.schemas import AUTH_401, BuildRequest, BuildResponse, SyncRequest, SyncResponse

router = APIRouter(tags=["jobs"])


# AR-2-2: docs_url allowlist. The build route fetches a caller-supplied URL
# from the control process, which sits on verifier-net + admin-net — without
# guards it becomes a read proxy for internal services. Only public http(s)
# targets are fetched; internal hostnames, non-public IPs (literal or
# resolved) and non-http(s) schemes are rejected with 422.
_INTERNAL_DOCS_HOSTS = frozenset(
    {
        "localhost",
        "verifier",
        "control",
        "worker",
        "runtime-sandbox",
        "bench",
        "host.docker.internal",
        "host.containers.internal",
    }
)
_INTERNAL_DOCS_PREFIXES = ("sim-",)
_INTERNAL_DOCS_SUFFIXES = (".local", ".internal", ".localhost", ".docker")


def _docs_url_host_blocked(host: str) -> str | None:
    """Return a reason when a docs_url host is internal-only, else None."""
    h = host.lower().rstrip(".")
    if h in _INTERNAL_DOCS_HOSTS:
        return f"internal host {host!r}"
    if h.startswith(_INTERNAL_DOCS_PREFIXES):
        return f"internal host {host!r}"
    if h.endswith(_INTERNAL_DOCS_SUFFIXES):
        return f"internal host {host!r}"
    try:
        if not ipaddress.ip_address(h).is_global:
            return f"non-public IP {host!r}"
    except ValueError:
        pass
    return None


def _docs_url_resolves_internal(host: str, port: int | None) -> bool:
    """Best-effort DNS check: True when the host resolves to a non-public IP."""
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(socket.getaddrinfo, host, port or 80, socket.AF_UNSPEC, socket.SOCK_STREAM)
            infos = fut.result(timeout=2)
    except Exception:
        return False  # fail open: denylist + literal-IP checks above still apply
    for info in infos:
        try:
            if not ipaddress.ip_address(info[4][0]).is_global:
                return True
        except ValueError:
            return True
    return False


def _redact_url(url: str) -> str:
    """Strip userinfo from a URL for safe inclusion in error details (§6)."""
    try:
        parts = urllib.parse.urlsplit(url)
        if parts.username or parts.password:
            netloc = parts.hostname or ""
            if parts.port:
                netloc += f":{parts.port}"
            return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    except Exception:
        pass
    return url


def validate_docs_url(url: str) -> None:
    """Reject docs_url values the server must not fetch; raises HTTPException(422)."""
    try:
        parsed = urllib.parse.urlsplit(url)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"docs_url is not a valid URL: {exc}") from exc
    if parsed.scheme.lower() not in ("http", "https"):
        raise HTTPException(status_code=422, detail=f"docs_url scheme must be http or https, got {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise HTTPException(status_code=422, detail="docs_url must include a host")
    try:
        port = parsed.port
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"docs_url has an invalid port: {exc}") from exc
    reason = _docs_url_host_blocked(host)
    if reason is not None:
        raise HTTPException(status_code=422, detail=f"docs_url target is not allowed: {reason}")
    if _docs_url_resolves_internal(host, port):
        raise HTTPException(status_code=422, detail=f"docs_url target is not allowed: {host!r} resolves to a non-public IP")


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


@router.post("/connectors/{connector_id}/build", response_model=BuildResponse, responses=AUTH_401)
def trigger_build(connector_id: str, body: BuildRequest, db: Session = Depends(get_db), _: None = Security(require_control_token)) -> BuildResponse:
    conn = db.get(Connector, connector_id)
    if conn is None:
        raise HTTPException(status_code=404, detail=f"Connector {connector_id!r} not found")

    docs_text: str | None = body.docs_text
    # If docs_url given, fetch inline (best effort, but not required for contract)
    if not docs_text and body.docs_url:
        validate_docs_url(body.docs_url)
        try:
            with httpx.Client(timeout=5) as client:
                r = client.get(body.docs_url)
                r.raise_for_status()
                docs_text = r.text
        except Exception as exc:
            safe_url = _redact_url(body.docs_url)
            # Scrub userinfo if the library error echoes the request URL.
            safe_err = str(exc).replace(body.docs_url, safe_url)
            raise HTTPException(status_code=422, detail=f"cannot fetch docs_url {safe_url}: {safe_err}") from exc
    if not docs_text and body.docs_path:
        # AR-2-2: the server never reads local files. docs_path is an opaque
        # identifier (callers read files client-side and send docs_text);
        # only the reference-spec guard below still inspects the value.
        if "generated/connectors" in body.docs_path:
            raise HTTPException(status_code=422, detail="build must not read reference specs")
        # Synthesize placeholder docs from the identifier.
        docs_text = f"docs from {body.docs_path}"
    if not docs_text:
        raise HTTPException(status_code=422, detail="build requires docs_text, docs_url, or docs_path")

    # Guard: block reference spec reads even via docs_text that looks like path
    if docs_text and "generated/connectors" in docs_text and len(docs_text) < 500:
        # Heuristic: short docs that mention generated/connectors likely path
        if docs_text.strip().startswith("generated/"):
            raise HTTPException(status_code=422, detail="build must not read reference specs")

    # Use connector's base_url as fallback for bootstrap placeholder.
    # REQ-027 sub-step 1: simulator-shaped values resolve to the address
    # that works in this stack's mode (service DNS vs host loopback);
    # real vendor hosts and ${VAR} placeholders pass through untouched.
    _raw_fb = body.base_url_override or conn.base_url or (body.model_extra.get("base_url") if body.model_extra else None)  # type: ignore[union-attr]
    base_url_fb = normalize_connector_base_url(_raw_fb) or _raw_fb
    # REQ-021 item 2: pinned vocabulary — request field, else SPEC_VOCABULARY
    # env, else v1 (safe refusal of POST/body-auth docs at 0 model calls).
    from autobridge.config import spec_vocabulary_default  # noqa: PLC0415

    spec_vocabulary = body.spec_vocabulary or spec_vocabulary_default()
    inputs: dict[str, Any] = {
        "docs_text": docs_text,
        "docs_source": body.docs_path or body.docs_url or "inline",
        "connector_id": connector_id,
        # REQ-027 owner check: the built spec keeps the connector's name
        # as vendor (derived titles truncate and drift from the record).
        "vendor": body.vendor or conn.name,
        "credential_ref": body.credential_ref or conn.credential_ref,
        "base_url_override": base_url_fb,
        "scenario_id": body.scenario_id,
        "verifier_url": body.verifier_url or os.environ.get("VERIFIER_URL"),
        "spec_vocabulary": spec_vocabulary,
        # REQ-027: wire-shape probe records served keys at build time
        # (fail-soft). Opt-in via the request body (``BuildRequest`` allows
        # extras): the demo and e2e pass ``probe_wire_shape: true``; unit
        # tests stay offline by default.
        "probe_wire_shape": bool((body.model_extra or {}).get("probe_wire_shape", False)),
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


@router.post("/connectors/{connector_id}/sync", response_model=SyncResponse, responses=AUTH_401)
def trigger_sync(connector_id: str, body: SyncRequest, db: Session = Depends(get_db), _: None = Security(require_control_token)) -> SyncResponse:
    conn = db.get(Connector, connector_id)
    if conn is None:
        raise HTTPException(status_code=404, detail=f"Connector {connector_id!r} not found")

    release_id = body.release_id or conn.active_release_id
    if not release_id:
        # AR-1-2: no fallback to the latest release. The latest release may
        # be an unapproved (or gate-blocked) candidate that must never
        # execute. Sync requires an explicitly chosen or activated release.
        raise HTTPException(status_code=422, detail="connector has no active release to sync (sync requires an approved, active release)")

    # Validate release exists and belongs to connector
    rel_obj = db.get(ConnectorRelease, release_id)
    if rel_obj is None:
        raise HTTPException(status_code=404, detail=f"Release {release_id!r} not found")
    if rel_obj.connector_id != connector_id:
        raise HTTPException(status_code=422, detail="release belongs to a different connector")
    # REQ-013: an explicitly named release runs only when it is runnable —
    # the active release or an operator-accepted (approved/rollback) one.
    # Anything else (an unapproved candidate, a rejected release) is 422.
    # Gate-blocked builds persist no release (gate-before-persist), so they
    # can never be named here; a gate_blocked provenance flag is still
    # refused explicitly (fail closed).
    if rel_obj.provenance_build_run_id is not None:
        brun = db.get(BuildRun, rel_obj.provenance_build_run_id)
        if brun is not None and isinstance(brun.result, dict) and brun.result.get("gate_blocked"):
            raise HTTPException(
                status_code=422,
                detail=f"Release {release_id!r} was blocked by the verifier gate and cannot sync",
            )
    if release_id != conn.active_release_id:
        accepted = (
            db.execute(
                select(Approval).where(
                    Approval.to_release_id == release_id,
                    Approval.decision.in_(("approve", "rollback")),
                )
            )
            .scalars()
            .first()
        )
        if accepted is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Release {release_id!r} is not active or approved for connector "
                    f"{connector_id!r} (sync needs the active release or an approved one; "
                    "activate a build candidate or approve a repair candidate first)"
                ),
            )

    runner = _get_runner(db)
    checkpoint = body.checkpoint or {}
    try:
        job_id = runner.submit_sync(
            release_id=release_id, checkpoint=checkpoint, spec_vocabulary=body.spec_vocabulary
        )
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
