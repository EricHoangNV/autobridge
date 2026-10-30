# ruff: noqa: B008, TC002, TC003, ARG001, SIM102, SIM105, SIM118, UP035, I001, UP006, UP007, RUF012, B027
"""Demo inject-fault endpoint (P1.W7.1) — proxies to simulator admin."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Security
from sqlalchemy import update
from sqlalchemy.orm import Session

from autobridge.control.demo_targets import get_demo_targets, in_compose_network, public_base_url_for_target
from autobridge.control.deps import get_db, require_control_token
from autobridge.control.models import (
	Approval,
	BuildRun,
	Connector,
	ConnectorRelease,
	DependencyRegistration,
	DiscoveryRecord,
	DriftEvent,
	EvidenceRecord,
	Job,
	ModelCall,
	ModelCallArchive,
	PabxLinkEvent,
	RepairRun,
	SyncRun,
)
from autobridge.control.schemas import AUTH_401, DOMAIN_ERROR_422, DemoDocsOut, DemoResetRequest, InjectFaultRequest, InjectFaultResponse, PabxEventOut, PabxRestartRequest, RecordClimbRequest, RecordClimbResponse, SchedulerPauseRequest, SimulatorConnectorRef, SimulatorInfo, SimulatorsOut

router = APIRouter(prefix="/demo", tags=["demo"])

# Demo target registry (REQ-022 owner check 2026-10-29): every id, alias,
# host admin port and extra reset comes from config/demo_targets.yaml,
# loaded once at startup. This module names no simulator — the "never
# seen" vendor proof scans src/autobridge/*.py and fails on any vendor
# literal there, so keep it that way (including in comments and messages).
_REGISTRY = get_demo_targets()

# Normalize target aliases to canonical simulator ids (registry-derived;
# kept under the historical names so existing imports keep working).
TARGET_MAP: dict[str, str] = dict(_REGISTRY.alias_map)


def _resolve_target(target: str) -> str:
    key = target.strip().lower()
    resolved = TARGET_MAP.get(key)
    if not resolved:
        known = ", ".join(t.id for t in _REGISTRY.targets)
        raise HTTPException(status_code=422, detail=f"unknown demo target {target!r}; known targets: {known}")
    return resolved


# Canonical simulator ids POST /demo/reset walks, in call order (REQ-022
# owner check 2026-10-29): the endpoint loop and every test that asserts
# the reset entries derive the expected list from here via
# expected_reset_targets() — never hand-keep the list in a second place.
RESET_TARGETS: tuple[str, ...] = _REGISTRY.reset_targets

# Extra admin resets beyond the shared ``/admin/reset`` slot, keyed by
# canonical id: (entry-label suffix, admin path).
EXTRA_RESETS: dict[str, tuple[tuple[str, str], ...]] = dict(_REGISTRY.extra_resets)


def expected_reset_targets() -> list[str]:
    """Entry labels POST /demo/reset reports, in order (registry-derived)."""
    entries: list[str] = []
    for target in RESET_TARGETS:
        entries.append(target)
        for suffix, _ in EXTRA_RESETS.get(target, ()):
            entries.append(f"{target}:{suffix}")
    return entries


def _admin_headers() -> dict[str, str]:
    """Headers proving admin authorization to the simulator admin API.

    ``X-Admin-Network`` is only a routing hint (AR-1-1); the real credential
    is ``SIM_ADMIN_TOKEN`` (server-side env only, never logged or echoed).
    Sent when configured — which is the default since REQ-035 ships a
    non-empty demo token, so header-only sends happen only when the
    operator explicitly empties it.
    """
    headers = {"X-Admin-Network": "admin-net"}
    token = os.environ.get("SIM_ADMIN_TOKEN", "")
    if token:
        headers["X-Admin-Token"] = token
    return headers


def _admin_url_for_target(resolved: str) -> str:
    # Inside Docker Compose, each simulator's admin API answers on its
    # container port 8001 at its canonical id as hostname.
    # From host (when not in Docker), we use localhost ports 8109/8110
    # Mode decider is shared with public base URLs (demo_targets:
    # in_compose_network) — one place, so admin and data-plane routing
    # can never disagree about which stack mode this process runs in.
    if in_compose_network():
        # In container: use service DNS
        return f"http://{resolved}:8001"
    # Host mode: map to host ports (registry-derived, single source).
    # Literal 127.0.0.1, not "localhost": same loopback, but no DNS lookup
    # (unit tests are hermetic — the DNS guard fails real lookups).
    port = _REGISTRY.host_admin_ports[resolved]
    return f"http://127.0.0.1:{port}"


@router.post("/inject-fault", response_model=InjectFaultResponse, responses={**DOMAIN_ERROR_422, **AUTH_401})
def inject_fault(body: InjectFaultRequest, _: None = Security(require_control_token)) -> InjectFaultResponse:
    resolved = _resolve_target(body.target)
    base = _admin_url_for_target(resolved)

    # Also consider env overrides for admin URLs (tests may set SIM_ADMIN_URL)
    env_key = f"{resolved.upper().replace('-', '_')}_ADMIN_URL"
    if os.environ.get(env_key):
        base = os.environ[env_key]
    # Generic override
    if os.environ.get("SIMULATOR_ADMIN_URL"):
        base = os.environ["SIMULATOR_ADMIN_URL"]

    admin_response: dict[str, Any] | None = None
    # Try to POST to /admin/scenario
    url = f"{base.rstrip('/')}/admin/scenario"
    payload: dict[str, Any] = {"id": body.scenario_id}
    if body.seed is not None:
        payload["seed"] = body.seed
    # Pass through extra fields
    if body.model_extra:
        for k, v in body.model_extra.items():
            if k not in payload:
                payload[k] = v

    try:
        with httpx.Client(timeout=5) as client:
            # The simulator admin API requires proof of admin authorization
            # (simulators/common/admin.py): header-only writes get 403 when
            # SIM_ADMIN_TOKEN is set, and the labelled fault never lands.
            r = client.post(url, json=payload, headers=_admin_headers())
            try:
                admin_response = r.json() if r.content else {"status_code": r.status_code}
            except Exception:
                admin_response = {"status_code": r.status_code, "text": r.text[:500]}
            # Also include status for transparency
            if isinstance(admin_response, dict):
                admin_response["_http_status"] = r.status_code
                admin_response["_url"] = url
    except Exception as exc:
        # Do not fail the demo endpoint if admin is unreachable — return labelled response with error
        admin_response = {"error": str(exc), "_url": url}

    return InjectFaultResponse(
        labelled=True,
        demo=True,
        target=resolved,
        scenario_id=body.scenario_id,
        seed=body.seed,
        admin_response=admin_response,
        message=f"[DEMO] Injected fault {body.scenario_id!r} into {resolved} (labelled demo action)",
    )


@router.post("/scheduler/pause", response_model=dict, responses=AUTH_401)
def scheduler_pause(
    body: SchedulerPauseRequest | None = Body(default=None),
    db: Session = Depends(get_db),
    _: None = Security(require_control_token),
) -> dict[str, Any]:
    """Pause the sync scheduler (REQ-031 owner check).

    The seed script (``make demo-seed``) pauses before its first control
    call and resumes in a ``finally`` after its last one, so no scheduled
    sync races a half-seeded connector. ``POST /demo/reset`` pauses and
    resumes itself around its work (nested-safe: a resume only clears its
    owner's pause). Stale pauses expire instead of wedging the scheduler.
    """
    from autobridge.control.scheduler import pause_scheduler  # noqa: PLC0415

    req = body or SchedulerPauseRequest()
    out = pause_scheduler(db, req.owner or "manual", req.reason or "")
    db.commit()
    return {"labelled": True, "demo": True, **out}


@router.post("/scheduler/resume", response_model=dict, responses=AUTH_401)
def scheduler_resume(
    body: SchedulerPauseRequest | None = Body(default=None),
    db: Session = Depends(get_db),
    _: None = Security(require_control_token),
) -> dict[str, Any]:
    """Clear a scheduler pause owned by ``owner`` (keeps a foreign owner's)."""
    from autobridge.control.scheduler import resume_scheduler  # noqa: PLC0415

    req = body or SchedulerPauseRequest()
    out = resume_scheduler(db, req.owner or "manual")
    db.commit()
    return {"labelled": True, "demo": True, **out}


def _reset_wait_s() -> float:
    """How long ``POST /demo/reset`` waits for in-flight jobs (env knob)."""
    try:
        return max(0.0, float(os.environ.get("AUTOBRIDGE_RESET_WAIT_S", "90") or "90"))
    except ValueError:
        return 90.0


@router.post("/reset", response_model=dict, responses=AUTH_401)
def demo_reset(
    _: None = Security(require_control_token),
    db: Session = Depends(get_db),
    body: DemoResetRequest | None = Body(default=None),
) -> dict[str, Any]:
    """Demo helper: return the system to a known first-run state (REQ-021 item 1).

    Idempotent; every section tolerates an already-empty state:

    1. every simulator back to baseline via its admin ``/admin/reset``
       (``resets`` keeps the historical shape so existing callers keep
       working);
    2. the L0 knowledge cache (in-process singleton) emptied — learned fixes
       from verified repairs do not leak into the next session;
    3. the model-usage ledger archived per demo session (rows copied to
       ``model_call_archive`` stamped with the new ``session_id``) and the
       live ledger emptied;
    4. demo control-plane state removed in FK-safe order: evidence,
       approvals, repair/sync/build runs, drift events, releases,
       dependencies, discovery records, jobs, connectors.

    ``model_call_archive`` rows are history, not state: they are never
    deleted here.

    REQ-031 item 3 (``make demo-heal``): ``{"sims_only": true}`` runs only
    section 1 — simulator fault state (scenario slots back to baseline,
    including the FIAS link-engine extras) cleared without touching
    connectors, runs, drift, repairs, knowledge or the ledger. ``targets``
    limits section 1 to named registry targets (ids or aliases); unknown
    names are 422.
    """
    request = body or DemoResetRequest()
    if request.targets:
        # Resolve aliases through the registry; unknown names fail closed.
        reset_targets = [_resolve_target(t) for t in request.targets]
    else:
        reset_targets = list(RESET_TARGETS)
    session_id = f"demo-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"

    # Migrate first (REQ-021 owner check): a database file kept across runs
    # may predate the newest columns (e.g. model_call_archive); reset must
    # upgrade it, never ask the operator to delete it.
    from autobridge.control.migrate import ensure_migrated  # noqa: PLC0415

    ensure_migrated(db.get_bind())

    # REQ-031 owner check: pause the scheduler while reset/seed/heal run,
    # and wait for any in-flight sync to finish — never reset mid-sync
    # (rows for deleted connectors, SQLite lock races, scar drifts from a
    # tick landing between the reset and the seed's first build). Owner
    # scoped with takeover: a pause owned by someone else is kept (a
    # nested reset inside a paused seed must not resume early); a pause
    # owned by us — including one a crashed run left behind — is taken
    # over and resumed at our exits below.
    from autobridge.control.scheduler import (  # noqa: PLC0415
        pause_scheduler,
        resume_scheduler,
        scheduler_pause_status,
        wait_for_quiescent,
    )

    pause_owner = "demo-heal" if request.sims_only else "demo-reset"
    pause_status = scheduler_pause_status(db)
    pause_mine = (not pause_status.get("paused")) or pause_status.get("owner") in (
        "",
        pause_owner,
    )
    if pause_mine:
        pause_scheduler(
            db, pause_owner, "demo reset in progress (scheduler paused)"
        )
        db.commit()

    def _resume_if_mine() -> None:
        if pause_mine:
            resume_scheduler(db, pause_owner)
            db.commit()

    try:
        wait_for_quiescent(db, timeout_s=_reset_wait_s())
    except TimeoutError as exc:
        _resume_if_mine()
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    resets: list[dict[str, Any]] = []
    for target in reset_targets:
        base = _admin_url_for_target(target)
        # Same env overrides as inject-fault (tests point these at scratch sims).
        env_key = f"{target.upper().replace('-', '_')}_ADMIN_URL"
        if os.environ.get(env_key):
            base = os.environ[env_key]
        if os.environ.get("SIMULATOR_ADMIN_URL"):
            base = os.environ["SIMULATOR_ADMIN_URL"]
        url = f"{base.rstrip('/')}/admin/reset"
        try:
            with httpx.Client(timeout=5) as client:
                # Admin writes require admin authorization (see inject-fault).
                r = client.post(url, json={}, headers=_admin_headers())
                resets.append({"target": target, "status": r.status_code})
        except Exception as exc:
            resets.append({"target": target, "error": str(exc)})
        for suffix, path in EXTRA_RESETS.get(target, ()):
            extra_url = f"{base.rstrip('/')}{path}"
            try:
                with httpx.Client(timeout=5) as client:
                    r = client.post(extra_url, json={}, headers=_admin_headers())
                    resets.append({"target": f"{target}:{suffix}", "status": r.status_code})
            except Exception as exc:
                resets.append({"target": f"{target}:{suffix}", "error": str(exc)})

    # REQ-031: sims-only heal — simulator fault state cleared, everything
    # the engine learned or recorded stays (connectors, runs, knowledge,
    # ledger). Retakes of one scenario start from here.
    if request.sims_only:
        from autobridge.repair.knowledge import get_default_cache as _knowledge_size  # noqa: PLC0415

        kept = _knowledge_size().size()
        _resume_if_mine()
        return {
            "labelled": True,
            "demo": True,
            "session_id": session_id,
            "sims_only": True,
            "resets": resets,
            "knowledge_cache": {"entries_before": kept, "entries_after": kept},
            "model_usage": {"archived": 0, "session_id": session_id},
            "removed": {},
        }

    # L0: drop learned fixes (in-process singleton; no persisted form exists —
    # RepairRun rows holding the same patches are wiped below).
    from autobridge.repair.knowledge import get_default_cache, reset_default_cache  # noqa: PLC0415

    knowledge_before = get_default_cache().size()
    reset_default_cache()
    knowledge_after = get_default_cache().size()

    # Ledger: archive per demo session, then empty the live table.
    archived = 0
    for call in db.query(ModelCall).all():
        db.add(
            ModelCallArchive(
                session_id=session_id,
                run_id=call.run_id,
                provider=call.provider,
                model=call.model,
                tier=call.tier,
                input_tokens=call.input_tokens,
                output_tokens=call.output_tokens,
                reasoning_tokens=call.reasoning_tokens,
                latency_ms=call.latency_ms,
                cost_estimate=call.cost_estimate,
                purpose=call.purpose,
                mode=call.mode or "live",
                captured_at=call.captured_at or "",
                outcome=call.outcome,
                prompt_id=call.prompt_id or "",
                prompt_version=call.prompt_version or "",
                prompt_hash=call.prompt_hash or "",
            )
        )
        archived += 1
    db.query(ModelCall).delete()

    # Domain state, children before parents (Postgres-safe; SQLite is lenient).
    db.execute(update(Connector).values(active_release_id=None))
    removed: dict[str, int] = {}
    for model in (
        EvidenceRecord,
        Approval,
        RepairRun,
        SyncRun,
        DriftEvent,
        BuildRun,
        ConnectorRelease,
        DependencyRegistration,
        DiscoveryRecord,
        Job,
        Connector,
    ):
        removed[model.__tablename__] = db.query(model).delete()
    db.commit()

    _resume_if_mine()
    return {
        "labelled": True,
        "demo": True,
        "session_id": session_id,
        "resets": resets,
        "knowledge_cache": {"entries_before": knowledge_before, "entries_after": knowledge_after},
        "model_usage": {"archived": archived, "session_id": session_id},
        "removed": removed,
    }


# The shared scenario slot reports this id when no fault is active
# (simulators/common/state.py DEFAULT_SCENARIO); the index shows no
# active fault then. The value is compared, never imported: engine code
# must not import simulator packages (trust boundary, plan §1.2).
_BASELINE_SCENARIO = "baseline"


def _admin_base_for_target(resolved: str) -> str:
    """Admin base URL honoring the same env overrides as inject-fault/reset."""
    base = _admin_url_for_target(resolved)
    env_key = f"{resolved.upper().replace('-', '_')}_ADMIN_URL"
    if os.environ.get(env_key):
        base = os.environ[env_key]
    if os.environ.get("SIMULATOR_ADMIN_URL"):
        base = os.environ["SIMULATOR_ADMIN_URL"]
    return base


def _probe_scenario(base: str) -> dict[str, Any] | None:
    """GET the simulator's admin scenario slot; None when not reachable."""
    url = f"{base.rstrip('/')}/admin/scenario"
    try:
        with httpx.Client(timeout=3) as client:
            r = client.get(url, headers=_admin_headers())
    except Exception:
        return None
    if r.status_code != 200:
        return None
    try:
        data = r.json()
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _connector_uses_target(connector: Connector, target_id: str, docs_base: str, public_port: int) -> bool:
    """A connector uses a simulator when its docs ref names the simulator's
    docs file, or its base URL names the simulator id or public port."""
    docs_ref = (connector.vendor_docs_ref or "").lower()
    if docs_base and docs_base.lower() in docs_ref:
        return True
    base_url = (connector.base_url or "").lower()
    return bool(base_url) and (target_id.lower() in base_url or str(public_port) in base_url)


@router.get("/simulators", response_model=SimulatorsOut)
def list_simulators(db: Session = Depends(get_db)) -> SimulatorsOut:
    """Simulators index data (REQ-022 E): every registry simulator with its
    display metadata, live up/down probed server-side from the control
    plane (the browser never calls a simulator directly), the active fault
    scenario if any, and the connectors that use it."""
    connectors = db.query(Connector).all()
    rows: list[SimulatorInfo] = []
    for target in _REGISTRY.targets:
        probed = _probe_scenario(_admin_base_for_target(target.id))
        scenario_id = (probed or {}).get("scenario_id")
        docs_base = target.docs_source.rsplit("/", 1)[-1] if target.docs_source else ""
        used_by = [
            SimulatorConnectorRef(id=c.id, name=c.name)
            for c in connectors
            if _connector_uses_target(c, target.id, docs_base, target.public_port)
        ]
        rows.append(
            SimulatorInfo(
                id=target.id,
                display_name=target.display_name or target.id,
                api_style=target.api_style,
                public_port=target.public_port,
                docs_source=target.docs_source,
                developers_url=f"http://localhost:{target.public_port}/developers",
                live=probed is not None and isinstance(scenario_id, str),
                active_scenario=scenario_id if isinstance(scenario_id, str) and scenario_id != _BASELINE_SCENARIO else None,
                connectors=used_by,
            )
        )
    return SimulatorsOut(simulators=rows)


@router.get("/docs", response_model=DemoDocsOut, responses=DOMAIN_ERROR_422)
def demo_docs(target: str) -> DemoDocsOut:
    """Simulator docs text through the demo bridge (REQ-031 item 4).

    The browser cannot be asked to fetch a caller-supplied URL (that would
    reopen the AR-2-2 SSRF surface the build route guards), so the control
    plane fetches the simulator's own developers portal
    (``/developers/source``) over the registry-derived public address and
    hands the text to the browser, which sends it back as ``docs_text`` on
    the build. The URL is constructed server-side from
    ``config/demo_targets.yaml`` — callers name a registry target, never a
    URL — so there is no fetch-what-you-are-told primitive here.
    """
    try:
        resolved = _resolve_target(target or "")
    except HTTPException as exc:
        # Unknown names are 404 (nothing by that name exists), never 422:
        # the OpenAPI contract suite rejects 422 for schema-valid requests.
        raise HTTPException(status_code=404, detail=exc.detail) from exc
    entry = next(t for t in _REGISTRY.targets if t.id == resolved)
    try:
        base = public_base_url_for_target(resolved)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"no public docs address for {resolved!r}: {exc}") from exc
    url = f"{base.rstrip('/')}/developers/source"
    try:
        with httpx.Client(timeout=5) as client:
            r = client.get(url)
            r.raise_for_status()
            text = r.text
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"cannot fetch developer docs for {resolved!r}: {exc}") from exc
    return DemoDocsOut(
        target=resolved,
        display_name=entry.display_name or resolved,
        docs_source=entry.docs_source,
        characters=len(text),
        docs_text=text,
    )


@router.post("/record-climb", response_model=RecordClimbResponse, responses={**DOMAIN_ERROR_422, **AUTH_401})
def record_climb(body: RecordClimbRequest, db: Session = Depends(get_db), _: None = Security(require_control_token)) -> RecordClimbResponse:
    """Persist the demo's instrumented semantic climb (REQ-030 item 3).

    The climb really ran — provider really called, tokens really spent,
    proposal really judged — but it ran in the demo process (against the
    reference spec) because the docs-built spec carries no money invariant
    yet, so no engine sync can detect the semantic drift live. Recording
    it here puts the rung trace, hypothesis derivation, model-call box and
    verifier verdict on the repair page as rows, instead of leaving them
    in process memory. Never used by the engine: ``POST /sync`` and the
    scheduler only ever write engine-derived rows.
    """
    from decimal import Decimal  # noqa: PLC0415

    from autobridge.evidence.store import EvidenceStore  # noqa: PLC0415

    conn = db.get(Connector, body.connector_id)
    if conn is None:
        raise HTTPException(status_code=404, detail=f"Connector {body.connector_id!r} not found")

    drift = DriftEvent(
        connector_id=body.connector_id,
        drift_class=body.drift_class,
        severity="high",
        evidence_ids=[],
        source_sync_run_id=None,
        status="open",
    )
    db.add(drift)
    db.flush()

    ladder_trace = [dict(a) for a in (body.attempts or [])]
    ladder_trace.append(
        {
            "level": 4,
            "action": "escalate",
            "reason": (
                f"No verified fix for {body.scenario or body.drift_class} "
                "(semantic drift is never auto-patched) — recorded from the "
                "demo's instrumented climb; one operator question"
            ),
            "drift_class": body.drift_class,
        }
    )
    run = RepairRun(
        drift_event_ids=[drift.id],
        ladder_level_reached=4,
        vendor=body.vendor or None,
        drift_class=body.drift_class,
        candidate_patch=None,
        rule_id=None,
        hypothesis_ids=[h.id for h in body.hypotheses],
        visible_test_results=[],
        verifier_report_id=None,
        decision="escalate",
        model_usage=dict(body.model_usage or {}),
        ladder_trace=ladder_trace,
        escalation_bundle={
            "reason": f"{body.scenario or body.drift_class}: semantic drift, no verified fix",
            "question": body.escalation_question,
            "evidence_ids": [],
        },
    )
    db.add(run)
    db.flush()

    call_ids: list[str] = []
    for call in body.model_calls:
        try:
            cost = Decimal(str(call.cost))
        except Exception:
            cost = Decimal("0")
        row = ModelCall(
            run_id=run.id,
            provider=call.provider,
            model=call.model,
            tier=call.tier,
            input_tokens=call.input_tokens,
            output_tokens=call.output_tokens,
            reasoning_tokens=call.reasoning_tokens,
            latency_ms=call.latency_ms,
            cost_estimate=cost,
            purpose=call.purpose,
            mode=call.mode or "live",
            captured_at=call.captured_at or "",
            outcome=call.outcome,
        )
        db.add(row)
        db.flush()
        call_ids.append(row.id)

    store = EvidenceStore(db)
    store.append(
        kind="hypothesis",
        payload={
            "drift_class": body.drift_class,
            "scenario": body.scenario,
            "enumerator_yield_count": body.enumerator_yield_count,
            "stages": [
                {
                    "name": "distinct",
                    "count": len(body.hypotheses),
                    "ids": [h.id for h in body.hypotheses],
                    "families": sorted({h.family for h in body.hypotheses if h.family}),
                    "source": "demo instrumented climb (engine enumerator on the Harbor docs)",
                }
            ],
            "recorded_via": "POST /demo/record-climb",
        },
        run_id=f"repair_runs:{run.id}",
        parent_ids=[],
    )
    if body.verifier_verdict is not None:
        store.append(
            kind="verification",
            payload={
                "scenario": body.scenario,
                "verdict": dict(body.verifier_verdict),
                "proposal": list(body.proposal or []),
                "recorded_via": "POST /demo/record-climb",
            },
            run_id=f"repair_runs:{run.id}",
            parent_ids=[],
        )
    db.flush()
    db.commit()
    return RecordClimbResponse(drift_id=drift.id, repair_run_id=run.id, model_call_ids=call_ids)


@router.post("/pabx-restart", responses=AUTH_401)
def record_pabx_restart(body: PabxRestartRequest, db: Session = Depends(get_db), _: None = Security(require_control_token)) -> PabxEventOut:
    """Persist a PABX restart report (REQ-030 item 3, PABX panel source)."""
    if db.get(Connector, body.connector_id) is None:
        raise HTTPException(status_code=404, detail=f"Connector {body.connector_id!r} not found")
    event = PabxLinkEvent(
        connector_id=body.connector_id,
        kind="restart",
        payload={
            "redelivered": body.redelivered,
            "lost": body.lost,
            "duplicated": body.duplicated,
            "unique_keys": body.unique_keys,
        },
    )
    db.add(event)
    db.flush()
    db.commit()
    db.refresh(event)
    return PabxEventOut(
        id=event.id,
        connector_id=event.connector_id,
        kind=event.kind,
        payload=dict(event.payload),
        created_at=event.created_at,
    )


@router.get("/pabx-events", response_model=list[PabxEventOut])
def list_pabx_events(connector_id: str | None = None, db: Session = Depends(get_db)) -> list[PabxEventOut]:
    """PABX link snapshots + restart reports, newest first (PABX panel source)."""
    query = db.query(PabxLinkEvent)
    if connector_id:
        query = query.filter(PabxLinkEvent.connector_id == connector_id)
    rows = query.order_by(PabxLinkEvent.created_at.desc()).limit(100).all()
    return [
        PabxEventOut(
            id=e.id,
            connector_id=e.connector_id,
            kind=e.kind,
            payload=dict(e.payload or {}),
            created_at=e.created_at,
        )
        for e in rows
    ]
