# ruff: noqa: B008, TC002, TC003, ARG001, SIM102, SIM105, SIM118, UP035, I001, UP006, UP007, RUF012, B027
"""Build and sync endpoints (P1.W7.1) — queue via JobRunner."""

from __future__ import annotations

import concurrent.futures
import ipaddress
import os
import socket
import urllib.parse
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Security
from sqlalchemy.orm import Session

from autobridge.control.demo_targets import normalize_connector_base_url
from autobridge.control.deps import get_db, require_control_token
from autobridge.control.models import Connector

if TYPE_CHECKING:
    from autobridge.control.jobs import JobRunner
from autobridge.control.schemas import (
    AUTH_401,
    BuildRequest,
    BuildResponse,
    SyncRequest,
    SyncResponse,
    VocabularyCheckOperation,
    VocabularyCheckRequest,
    VocabularyCheckResponse,
)

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
    # One sync implementation for the route and the scheduler (REQ-030):
    # the real logic lives in control.runner; this keeps the old name for
    # existing callers (build submit path below).
    from autobridge.control.runner import get_runner  # noqa: PLC0415

    return get_runner(db)


@router.post("/connectors/vocabulary-check", response_model=VocabularyCheckResponse, responses=AUTH_401)
def vocabulary_check(body: VocabularyCheckRequest, db: Session = Depends(get_db), _: None = Security(require_control_token)) -> VocabularyCheckResponse:
    """Pre-creation vocabulary gate (REQ-053 item 7).

    Runs the same deterministic docs → classify → vocabulary-guard path
    the build runs (0 model calls, no network beyond an optional docs_url
    fetch), before any connector is created. A refused vocabulary creates
    no connector and no build: the attempt (name, vocabulary, reason) is
    recorded by name so the later connector built under the same name
    shows the earlier refusal as history. Anything else (docs that parse
    but fail later, or docs that compile) is not a refusal — the build
    itself reports those, on its own connector.
    """
    from autobridge.control.models import RefusedBuildAttempt  # noqa: PLC0415

    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="name is required (history key for a refused build)")
    docs_text: str | None = body.docs_text
    if not docs_text and body.docs_url:
        validate_docs_url(body.docs_url)
        try:
            with httpx.Client(timeout=5) as client:
                r = client.get(body.docs_url)
                r.raise_for_status()
                docs_text = r.text
        except Exception as exc:
            safe_url = _redact_url(body.docs_url)
            safe_err = str(exc).replace(body.docs_url, safe_url)
            raise HTTPException(status_code=422, detail=f"cannot fetch docs_url {safe_url}: {safe_err}") from exc
    if not docs_text and body.docs_path:
        if "generated/connectors" in body.docs_path:
            raise HTTPException(status_code=422, detail="build must not read reference specs")
        raise HTTPException(
            status_code=422,
            detail="docs_path is an identifier only; send docs_text (or docs_url)",
        )
    if not docs_text:
        raise HTTPException(status_code=422, detail="vocabulary check requires docs_text (or docs_url)")

    from autobridge.config import spec_vocabulary_default  # noqa: PLC0415

    spec_vocabulary = body.spec_vocabulary or spec_vocabulary_default()
    docs_source = body.docs_path or body.docs_url or "inline"

    from autobridge.discovery.bootstrap import build_draft_spec  # noqa: PLC0415

    try:
        result = build_draft_spec(
            docs_text,
            source=docs_source,
            connector_id="vocabulary-check",
            vendor=name,
            spec_vocabulary=spec_vocabulary,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"vocabulary check failed: {exc}") from exc
    trace = result.trace if isinstance(result.trace, list) else []
    guard = next(
        (t for t in trace if isinstance(t, dict) and t.get("stage") == "vocabulary_guard"),
        None,
    )
    if guard is None:
        return VocabularyCheckResponse(
            refused=False, reason=None, vocabulary=spec_vocabulary, suggested_vocabulary=None
        )
    reason = str(result.degradation_reason or guard.get("reason") or "vocabulary cannot express these docs")
    suggested = "v3" if guard.get("protocol") else "v2"
    # REQ-058: carry the refused build's facts for the capability-request
    # draft (deterministic, from the parsed docs only). Capped lists.
    iface = getattr(result, "iface", None)
    classification = getattr(result, "classification", None)
    ops = list(getattr(iface, "operations", None) or [])[:50]
    schemas = list(getattr(iface, "schemas", None) or [])[:50]
    auth = getattr(classification, "auth", None)
    refused_methods = guard.get("refused_methods")
    refused_protocol = guard.get("protocol")

    # One row per (name, vocabulary): a repeated refusal replaces the old one.
    db.query(RefusedBuildAttempt).filter(
        RefusedBuildAttempt.connector_name == name,
        RefusedBuildAttempt.spec_vocabulary == spec_vocabulary,
    ).delete()
    db.add(
        RefusedBuildAttempt(
            connector_name=name,
            spec_vocabulary=spec_vocabulary,
            reason=reason,
            docs_source=docs_source,
        )
    )
    db.commit()
    return VocabularyCheckResponse(
        refused=True, reason=reason, vocabulary=spec_vocabulary, suggested_vocabulary=suggested,
        refused_methods=list(refused_methods) if isinstance(refused_methods, list) else None,
        refused_protocol=str(refused_protocol) if refused_protocol is not None else None,
        doc_type=getattr(iface, "doc_type", None),
        auth_kind=getattr(auth, "kind", None),
        operations=[
            VocabularyCheckOperation(
                operation_id=str(getattr(op, "operation_id", "") or ""),
                method=str(getattr(op, "method", "") or ""),
                path=str(getattr(op, "path", "") or ""),
            )
            for op in ops
        ] or None,
        record_types=[str(getattr(s, "name", "") or "") for s in schemas if getattr(s, "name", "")] or None,
        has_body_fields=bool(any(getattr(op, "body_fields", None) or getattr(op, "body_required", None) for op in ops)),
        has_event_feeds=bool(any(str(getattr(op, "method", "") or "").upper() == "EVENT" for op in ops)),
    )


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
        # REQ-029: docs_path is an identifier only, kept as provenance
        # (docs_source below). The server never reads local files (AR-2-2),
        # and it no longer synthesizes placeholder docs from the identifier
        # (that built a generic spec — e.g. Atlas with /reservations +
        # offset pagination — from any string). Callers read the file
        # client-side and send docs_text (or docs_url).
        if "generated/connectors" in body.docs_path:
            raise HTTPException(status_code=422, detail="build must not read reference specs")
        raise HTTPException(
            status_code=422,
            detail="docs_path is an identifier only; send docs_text (or docs_url)",
        )
    if not docs_text:
        raise HTTPException(status_code=422, detail="build requires docs_text (or docs_url)")

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
    # Same execution the scheduler runs (control.runner): validate, submit,
    # inline-execute (detect → classify → ladder → verifier gate).
    from autobridge.control.runner import SyncError, sync_connector_now  # noqa: PLC0415

    try:
        out = sync_connector_now(
            db,
            connector_id,
            release_id=body.release_id,
            checkpoint=body.checkpoint or {},
            spec_vocabulary=body.spec_vocabulary,
        )
    except SyncError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return SyncResponse(
        job_id=str(out["job_id"]),
        sync_run_id=out.get("sync_run_id"),
        state=str(out.get("state")),
        release_id=str(out.get("release_id")),
        drift_found=out.get("drift_found"),
        drift_id=out.get("drift_id"),
        drift_class=out.get("drift_class"),
        repair_pending=out.get("repair_pending"),
        repair_job_id=out.get("repair_job_id"),
        repair_run_id=out.get("repair_run_id"),
        outcome=out.get("outcome"),
    )
