"""Releases: ConnectorRelease creation, approve/reject/rollback (P1.W4.5).

A release is immutable once stored (plan §2.3). Creation, approval and
rollback each insert new rows and, for approve/rollback, flip
``Connector.active_release_id`` — they never UPDATE a
``ConnectorRelease``. All functions take a live ``Session`` and flush
but do not commit; callers own the transaction boundary (matches
``engine/sync_run.py`` and ``repair/controller.py``).

Trust boundary: no import of ``verifier`` or ``simulators``.
Secrets never appear: specs carry ``credential_ref`` only.
"""

from __future__ import annotations

import contextlib
import os
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from autobridge.control.models import (
    Approval,
    BuildRun,
    Connector,
    ConnectorRelease,
    RepairRun,
    spec_signature,
)
from autobridge.spec.models import load_spec_from_dict
from autobridge.spec.patch import apply_patch

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class ReleaseError(ValueError):
    """Invalid release operation (missing connector, bad patch, etc.)."""


def next_version(session: Session, connector_id: str) -> int:
    """Next version number for *connector_id* (max + 1, or 1 when none)."""
    max_v = session.execute(
        select(func.coalesce(func.max(ConnectorRelease.version), 0)).where(
            ConnectorRelease.connector_id == connector_id
        )
    ).scalar_one()
    return int(max_v) + 1


def _ensure_connector(session: Session, connector_id: str) -> Connector:
    conn = session.get(Connector, connector_id)
    if conn is None:
        raise ReleaseError(f"Connector {connector_id!r} not found")
    return conn


def _ensure_release(session: Session, release_id: str) -> ConnectorRelease:
    rel = session.get(ConnectorRelease, release_id)
    if rel is None:
        raise ReleaseError(f"ConnectorRelease {release_id!r} not found")
    return rel


def _ensure_repair_run(session: Session, repair_run_id: str) -> RepairRun:
    run = session.get(RepairRun, repair_run_id)
    if run is None:
        raise ReleaseError(f"RepairRun {repair_run_id!r} not found")
    return run


def _require_verified_release(rel: ConnectorRelease) -> None:
    """Fail closed on activation of an unverified release (REQ-029).

    Gate-before-persist means a release persisted under
    ``AUTOBRIDGE_REQUIRE_VERIFIER_GATE=1`` already carries a
    ``verification_report_id``. A pre-gate/legacy row without one must not
    become runnable by approve/activate/rollback while the flag is set:
    persist-time gating alone would leave activation as a bypass.
    Without the flag (unit tests, fakes) activation is unchanged.
    """
    if os.environ.get("AUTOBRIDGE_REQUIRE_VERIFIER_GATE") == "1" and not rel.verification_report_id:
        raise ReleaseError(
            f"release {rel.id!r} has no verification_report_id and cannot be activated "
            "while AUTOBRIDGE_REQUIRE_VERIFIER_GATE=1 (no process may activate a "
            "release without a passing verifier verdict)"
        )


def _maybe_verify(
    spec: dict[str, Any],
    *,
    verifier_url: str | None,
    scenario_id: str,
    verified_scenarios: list[str] | None,
    mode: str = "visible",
) -> str | None:
    """Run the verifier gate; return the report id, or None when skipped.

    Direct ``verifier_url`` first (control-inline path). Without one, the
    gate goes through control's ``POST /internal/verify`` proxy
    (``CONTROL_URL`` env): the worker in Compose has no verifier-net route
    but shares engine-net with control, so it verifies through control
    instead of persisting unverified (REQ-029, AR-1-2 residual). A passed
    gate mints a verification report id the release carries
    (``verification_report_id``).

    Raises GateBlockedError (from verification.py) when verdict or regression
    fail, or when no verifier is reachable at all and
    ``AUTOBRIDGE_REQUIRE_VERIFIER_GATE=1`` (set on control + worker in
    ``compose.yaml`` and ``scripts/local-up.sh``). Without that env flag the
    gate is skipped for backward compat (unit tests without Docker).

    AR-1-5: fail closed. If the gate module itself cannot be imported, raise
    instead of silently skipping verification — an unverified candidate must
    never persist.
    """
    if verifier_url:
        try:
            from autobridge.engine.verification import ensure_gate_passes  # noqa: PLC0415
        except Exception as exc:
            raise ReleaseError(f"verification gate unavailable: {exc}") from exc
        ensure_gate_passes(
            spec,
            scenario_id,
            verifier_url,
            verified_scenarios=verified_scenarios,
            mode=mode,
        )
        from autobridge.engine.verification import mint_verification_report_id  # noqa: PLC0415

        return mint_verification_report_id()
    control_url = os.environ.get("CONTROL_URL", "").strip()
    if control_url:
        try:
            from autobridge.engine.verification import (  # noqa: PLC0415
                CONTROL_TOKEN_ENV,
                ensure_control_gate_passes,
                mint_verification_report_id,
            )
        except Exception as exc:
            raise ReleaseError(f"verification gate unavailable: {exc}") from exc
        ensure_control_gate_passes(
            spec,
            scenario_id,
            control_url,
            token=os.environ.get(CONTROL_TOKEN_ENV) or None,
            verified_scenarios=verified_scenarios,
            mode=mode,
        )
        return mint_verification_report_id()
    if os.environ.get("AUTOBRIDGE_REQUIRE_VERIFIER_GATE") == "1":
        from autobridge.engine.verification import GateBlockedError  # noqa: PLC0415

        raise GateBlockedError(
            "no verifier reachable: VERIFIER_URL and CONTROL_URL are both unset "
            "(worker has no verifier route; set CONTROL_URL so the gate runs "
            "through control's /internal/verify proxy)",
            result=None,
            reason="no_verifier_configured",
        )
    return None


def create_release(
    session: Session,
    *,
    connector_id: str,
    spec: dict[str, Any],
    parent_release_id: str | None = None,
    provenance_build_run_id: str | None = None,
    provenance_repair_run_id: str | None = None,
    verification_report_id: str | None = None,
    verifier_url: str | None = None,
    scenario_id: str = "baseline",
    verified_scenarios: list[str] | None = None,
    mode: str = "visible",
) -> ConnectorRelease:
    """Create a new :class:`ConnectorRelease` from a fully validated spec.

    Steps:
      1. Connector must exist.
      2. If *parent_release_id* given, it must exist and belong to the same connector.
      3. *spec* validates via :func:`load_spec_from_dict` (path-level errors on failure).
      4. Version is ``max_version + 1`` for this connector.
      5. Signature is ``spec_signature(spec)`` (HMAC over canonical JSON when
       ``EDGE_RELEASE_SECRET`` is set, unkeyed sha256 integrity checksum
       otherwise — AR-2-4).

    The row is flushed and returned; caller commits.

    When *verifier_url* is provided, the candidate spec is verified before
    persisting: ``POST /verify {spec, scenario_id, regression_suite}`` must
    return ``verdict pass`` and ``regression pass``. A release that breaks
    any previously verified scenario (``verified_scenarios``) is blocked with
    ``GateBlockedError``; the plausible-but-wrong patch is rejected because its
    ``invalid_patch_trap`` check fails (P1.W6.4).
    """
    _ensure_connector(session, connector_id)

    if parent_release_id is not None:
        parent = _ensure_release(session, parent_release_id)
        if parent.connector_id != connector_id:
            raise ReleaseError("parent release belongs to a different connector")

    # Validate spec (raises on invalid)
    try:
        validated = load_spec_from_dict(spec)
    except Exception as exc:
        raise ReleaseError(f"spec validation failed: {exc}") from exc

    # Canonical JSON for signature uses dumped data
    spec_data: dict[str, Any] = validated.model_dump(mode="json", by_alias=True)

    # Regression gate (before persisting) — REQ-029: direct verifier_url,
    # else control's /internal/verify proxy (CONTROL_URL), else skip unless
    # AUTOBRIDGE_REQUIRE_VERIFIER_GATE=1. A passed gate mints the report id
    # the release carries; a blocked/unreachable gate raises and nothing
    # persists.
    report_id = _maybe_verify(
        spec_data,
        verifier_url=verifier_url,
        scenario_id=scenario_id,
        verified_scenarios=verified_scenarios,
        mode=mode,
    )

    sig = spec_signature(spec_data)
    version = next_version(session, connector_id)

    release = ConnectorRelease(
        connector_id=connector_id,
        version=version,
        spec=spec_data,
        parent_release_id=parent_release_id,
        provenance_build_run_id=provenance_build_run_id,
        provenance_repair_run_id=provenance_repair_run_id,
        verification_report_id=verification_report_id or report_id,
        signature=sig,
    )
    session.add(release)
    session.flush()
    return release


def create_release_from_patch(
    session: Session,
    *,
    connector_id: str,
    base_release_id: str,
    patch: list[dict[str, Any]],
    provenance_repair_run_id: str | None = None,
    provenance_build_run_id: str | None = None,
    verification_report_id: str | None = None,
    verifier_url: str | None = None,
    scenario_id: str = "baseline",
    verified_scenarios: list[str] | None = None,
    mode: str = "visible",
) -> ConnectorRelease:
    """Create a new release by applying *patch* to *base_release_id*'s spec.

    The patch is applied and validated via :func:`apply_patch` (malformed
    or non-applying patches raise :class:`ReleaseError` with the underlying
    cause). The new release's parent is *base_release_id* and its version
    is ``next_version``.

    This is the repair path: a verified patch from a ``RepairRun`` becomes
    a candidate release awaiting approval.

    When *verifier_url* is provided, the candidate spec is verified before
    persisting — a release that breaks any previously verified scenario
    (``verified_scenarios``) is blocked with ``GateBlockedError``; the
    plausible-but-wrong patch is rejected because its ``invalid_patch_trap``
    check fails (P1.W6.4).
    """
    if not patch:
        raise ReleaseError("patch must be a non-empty list of operations")

    _ensure_connector(session, connector_id)
    base = _ensure_release(session, base_release_id)
    if base.connector_id != connector_id:
        raise ReleaseError("base release belongs to a different connector")

    if provenance_repair_run_id is not None:
        _ensure_repair_run(session, provenance_repair_run_id)

    try:
        new_spec = apply_patch(base.spec, patch)
    except Exception as exc:
        raise ReleaseError(f"patch does not apply or yields invalid spec: {exc}") from exc

    return create_release(
        session,
        connector_id=connector_id,
        spec=new_spec,
        parent_release_id=base_release_id,
        provenance_build_run_id=provenance_build_run_id,
        provenance_repair_run_id=provenance_repair_run_id,
        verification_report_id=verification_report_id,
        verifier_url=verifier_url,
        scenario_id=scenario_id,
        verified_scenarios=verified_scenarios,
        mode=mode,
    )


def _activate(session: Session, connector_id: str, release_id: str, *, actor: str | None = None) -> None:
    """Set ``Connector.active_release_id`` to *release_id* (flush only).

    After flipping, pause any ``pause_on_release_change`` dependents and emit
    ``release.activated`` via :mod:`autobridge.engine.monitor` (flush only;
    caller owns commit). The import is local to avoid a circular dependency
    (monitor never imports releases).
    """
    conn = _ensure_connector(session, connector_id)
    rel = _ensure_release(session, release_id)
    if rel.connector_id != connector_id:
        raise ReleaseError("release belongs to a different connector")
    _require_verified_release(rel)
    conn.active_release_id = rel.id
    session.flush()
    # Emit release.activated and pause dependents (plan §2.3 reusability hook).
    try:
        from autobridge.engine.monitor import handle_release_activated  # noqa: PLC0415

        handle_release_activated(
            session,
            connector_id=connector_id,
            new_release_id=release_id,
            actor=actor,
        )
    except Exception:
        # Dependency pause is best-effort: activation must succeed even if the
        # emit path fails (e.g. in tests with a minimal schema). Re-raise only
        # activation errors above; swallow monitor EmitError.
        pass


def approve_release(
    session: Session,
    *,
    repair_run_id: str,
    actor: str,
    from_release_id: str | None,
    to_release_id: str,
    connector_id: str | None = None,
    knowledge_cache: Any | None = None,
) -> Approval:
    """Approve a candidate release: create approval and activate it.

    *repair_run_id* must exist (the ladder run that produced the patch).
    *actor* names the human/policy approver (non-empty).
    *from_release_id* is the previously active release (may be None for first build).
    *to_release_id* is the candidate now becoming active: it must be the
    candidate release produced by *repair_run_id* (``provenance_repair_run_id``
    match). A foreign, stale, or rolled-back release cannot be activated
    under an unrelated repair's audit cover (AR-1-3).
    *connector_id* may be omitted — it is inferred from *to_release_id*.
    *knowledge_cache* receives the learned fix (defaults to the process
    default cache): REQ-030 2b learns a fix only once it is approved —
    proposing caches nothing, approving writes the entry (recording the
    approval id, actor and release id) after the release is activated.

    The connector's ``active_release_id`` is set to *to_release_id*.
    Returns the new :class:`Approval` row.
    """
    if not actor or not actor.strip():
        raise ReleaseError("actor must be a non-empty string")
    run = _ensure_repair_run(session, repair_run_id)
    to_rel = _ensure_release(session, to_release_id)
    # AR-1-3: bind the approval to this repair run's candidate release.
    if to_rel.provenance_repair_run_id != repair_run_id:
        raise ReleaseError("to_release is not the candidate release of this repair run")
    if from_release_id is not None:
        from_rel = _ensure_release(session, from_release_id)
        # Connector consistency check
        if from_rel.connector_id != to_rel.connector_id:
            raise ReleaseError("from_release and to_release belong to different connectors")

    inferred_connector_id = connector_id or to_rel.connector_id
    # Ensure inferred matches from if both given
    if connector_id is not None and connector_id != to_rel.connector_id:
        raise ReleaseError("connector_id does not match to_release's connector")

    # Activate (also emits release.activated + pauses dependents)
    _activate(session, inferred_connector_id, to_release_id, actor=actor.strip())

    approval = Approval(
        repair_run_id=repair_run_id,
        actor=actor.strip(),
        decision="approve",
        from_release_id=from_release_id,
        to_release_id=to_release_id,
    )
    session.add(approval)
    session.flush()
    # REQ-053 item 3: the decision is terminal now — an approval row exists
    # ⇒ the repair no longer waits. Every "waiting" in the UI derives from
    # the decision, one source of truth, so the row must leave the waiting
    # states here (it stayed "propose" forever before, and the UI kept
    # offering Review/Approve on an approved repair).
    was_propose = run.decision == "propose"
    run.decision = "approved"
    session.flush()
    # REQ-030 2b: a fix is learned only once it is approved, after the
    # release is activated. A proposal that is still waiting can never be
    # reused at L0, by any connector. Learning is best-effort: cache
    # failure must not break the approval.
    if was_propose and run.candidate_patch:
        with contextlib.suppress(Exception):
            from autobridge.repair.knowledge import (  # noqa: PLC0415
                NO_LEARN_CLASSES,
                delta_has_content,
                get_default_cache,
            )

            drift_class = run.drift_class or ""
            delta = run.fingerprint_delta
            if drift_class not in NO_LEARN_CLASSES and delta_has_content(delta):
                cache = knowledge_cache if knowledge_cache is not None else get_default_cache()
                cache.put(
                    run.vendor or "unknown",
                    delta,
                    list(run.candidate_patch),
                    run.rule_id or "unknown",
                    drift_class=drift_class,
                    confidence=0.0,
                    approval_id=approval.id,
                    actor=approval.actor,
                    release_id=to_release_id,
                )
    return approval


def activate_release(
    session: Session,
    *,
    connector_id: str,
    release_id: str,
    actor: str,
) -> ConnectorRelease:
    """Explicit operator activation of a build candidate (REQ-013).

    A freshly built release is a *candidate*: it becomes runnable either via
    first-build auto-activation (see ``run_build_handler``) or here, by the
    operator naming it. A gate-blocked build persists no release, so there
    is never anything unrunnable to activate.

    Refusals (all :class:`ReleaseError`, surfaced as HTTP 422):

    * release missing or belonging to another connector;
    * human-rejected candidates (a ``reject`` approval names them);
    * repair candidates (``provenance_repair_run_id`` set) — those activate
      only via repair approval, which binds the audit cover to the run;
    * gate-blocked provenance (a ``gate_blocked`` build result — defense in
      depth; gate-before-persist normally persists nothing on block).

    On success sets ``Connector.active_release_id`` (emits
    ``release.activated`` + pauses dependents via :func:`_activate`) and
    returns the release. Idempotent: activating the already-active release
    succeeds.
    """
    if not actor or not actor.strip():
        raise ReleaseError("actor must be a non-empty string")
    _ensure_connector(session, connector_id)
    rel = _ensure_release(session, release_id)
    if rel.connector_id != connector_id:
        raise ReleaseError("release belongs to a different connector")
    rejected = (
        session.execute(
            select(Approval).where(
                Approval.to_release_id == release_id,
                Approval.decision == "reject",
            )
        )
        .scalars()
        .first()
    )
    if rejected is not None:
        raise ReleaseError("release was rejected and cannot be activated")
    if rel.provenance_repair_run_id is not None:
        raise ReleaseError(
            "repair candidates activate via repair approval, not explicit activation"
        )
    if rel.provenance_build_run_id is not None:
        brun = session.get(BuildRun, rel.provenance_build_run_id)
        if (
            brun is not None
            and isinstance(brun.result, dict)
            and brun.result.get("gate_blocked")
        ):
            raise ReleaseError(
                f"release blocked by verifier gate: {brun.result.get('gate_blocked')}"
            )
    _activate(session, connector_id, release_id, actor=actor.strip())
    return rel


def reject_release(
    session: Session,
    *,
    repair_run_id: str,
    actor: str,
    from_release_id: str | None,
    to_release_id: str | None,
    knowledge_cache: Any | None = None,
) -> Approval:
    """Reject a candidate release: record decision, do not activate.

    Neither the connector's active release nor any existing release is
    mutated. *from/to* are optional (reject may be recorded even before a
    release was created, but when given they are validated to exist).
    *knowledge_cache* names the L0 cache the rejection evicts (defaults to
    the process default — the same cache the approve path learns into).
    """
    if not actor or not actor.strip():
        raise ReleaseError("actor must be a non-empty string")
    run = _ensure_repair_run(session, repair_run_id)
    if from_release_id is not None:
        _ensure_release(session, from_release_id)
    if to_release_id is not None:
        _ensure_release(session, to_release_id)

    approval = Approval(
        repair_run_id=repair_run_id,
        actor=actor.strip(),
        decision="reject",
        from_release_id=from_release_id,
        to_release_id=to_release_id,
    )
    session.add(approval)
    session.flush()
    # REQ-053 item 3: a rejected repair no longer waits (same source of
    # truth as the approve path above).
    run.decision = "reject"
    session.flush()
    # AR-2-3: a human-rejected patch must never be re-proposed at L0.
    # Evict every cache entry holding this run's candidate patch. The
    # eviction is best-effort: cache failure must not break the decision.
    if run.candidate_patch:
        with contextlib.suppress(Exception):
            from autobridge.repair.knowledge import get_default_cache  # noqa: PLC0415

            cache = knowledge_cache if knowledge_cache is not None else get_default_cache()
            cache.evict_patch(list(run.candidate_patch))
    return approval


def rollback_release(
    session: Session,
    *,
    connector_id: str,
    target_release_id: str,
    actor: str,
    repair_run_id: str,
    knowledge_cache: Any | None = None,
    reopen_as: str | None = None,
) -> tuple[ConnectorRelease, Approval]:
    """Rollback *connector_id* to *target_release_id*'s spec.

    Creates a **new** release (version = max+1) whose spec is bit-identical
    to *target_release_id*'s spec, with parent set to the current active
    release. Then creates a rollback :class:`Approval` with
    ``from_release_id = previous active``, ``to_release_id = new release``,
    ``rollback_of_release_id = previous active``, and activates the new
    release.

    Returns ``(new_release, approval)``.

    *reopen_as* (REQ-053 item 6): the repair decision to write instead of
    the terminal ``"rolled_back"``. An automatic rollback after a failed
    verifying sync passes ``"escalate"`` so the original escalation stays
    the one open item (annotated by the caller); a manual rollback leaves
    it ``None`` and the repair ends ``"rolled_back"`` (actions disabled,
    REQ-054).

    Preconditions:
      * connector exists and has an active release (cannot rollback from nothing)
      * target release exists and belongs to same connector
      * repair_run exists (audit trail)
      * actor non-empty
    """
    if not actor or not actor.strip():
        raise ReleaseError("actor must be a non-empty string")
    conn = _ensure_connector(session, connector_id)
    if conn.active_release_id is None:
        raise ReleaseError("connector has no active release to rollback from")
    current_id = conn.active_release_id
    current = _ensure_release(session, current_id)
    target = _ensure_release(session, target_release_id)
    if target.connector_id != connector_id:
        raise ReleaseError("target release belongs to a different connector")
    _ensure_repair_run(session, repair_run_id)

    # Create new release copying target's spec
    new_release = create_release(
        session,
        connector_id=connector_id,
        spec=dict(target.spec),
        parent_release_id=current.id,
        verification_report_id=target.verification_report_id,
    )

    # Approval + activation (also emits release.activated)
    # Activate via helper (sets active_release_id + pause)
    _activate(session, connector_id, new_release.id, actor=actor.strip())

    approval = Approval(
        repair_run_id=repair_run_id,
        actor=actor.strip(),
        decision="rollback",
        from_release_id=current.id,
        to_release_id=new_release.id,
        rollback_of_release_id=current.id,
    )
    session.add(approval)
    session.flush()
    # REQ-053 item 3: a rolled-back repair no longer waits — unless the
    # caller re-opens it (automatic rollback after a failed verifying sync
    # re-opens the original escalation, REQ-053 item 6).
    run = _ensure_repair_run(session, repair_run_id)
    run.decision = reopen_as if reopen_as is not None else "rolled_back"
    session.flush()
    # AR-2-3: the superseded release regressed, so the patch that produced
    # it (its provenance repair run's candidate) must not be re-proposed
    # at L0. Best-effort; cache failure must not break the rollback.
    with contextlib.suppress(Exception):
        from autobridge.repair.knowledge import get_default_cache  # noqa: PLC0415

        cache = knowledge_cache if knowledge_cache is not None else get_default_cache()
        prov_id = current.provenance_repair_run_id
        prov_run = session.get(RepairRun, prov_id) if prov_id else None
        if prov_run is not None and prov_run.candidate_patch:
            cache.evict_patch(list(prov_run.candidate_patch))
    return new_release, approval


def approve_business_decision(
    session: Session,
    *,
    repair_run_id: str,
    actor: str,
    connector_id: str | None = None,
    verifier_url: str | None = None,
    knowledge_cache: Any | None = None,
) -> tuple[ConnectorRelease, Approval]:
    """Approve the model's proposed change on an escalated repair (REQ-049).

    The repair must be ``decision == "escalate"`` with a proposal in its
    escalation bundle (label/effect/patch, carried verbatim from the
    model's answer — the engine never synthesises one). The proposal patch
    is applied to the connector's ACTIVE release spec; the candidate is
    verifier-gated in visible baseline mode: the verifier confirms the new
    release syncs and its checks pass, but makes no claim that the new
    meaning is correct — that is the operator's business decision. The
    candidate then activates through :func:`approve_release` (which binds
    the audit cover to this repair run); the next sync on the new release
    is the verifying sync.

    A business decision is NOT learned into L0 knowledge (the approve path
    learns only ``propose`` runs): the next identical drift asks again.
    Rollback to the previous release stays available (the new release's
    parent is the release it supersedes).

    Raises :class:`ReleaseError` when the repair is not an escalation with
    a proposal, the connector has no active release, the patch does not
    apply, or the verifier gate blocks the candidate.
    """
    import os as _os  # noqa: PLC0415

    if not actor or not actor.strip():
        raise ReleaseError("actor must be a non-empty string")
    run = _ensure_repair_run(session, repair_run_id)
    if run.decision != "escalate":
        raise ReleaseError(
            f"RepairRun {repair_run_id!r} decision is {run.decision!r}, not escalate: "
            "a business decision approves only an escalated repair's proposal"
        )
    already = (
        session.execute(
            select(Approval).where(
                Approval.repair_run_id == repair_run_id,
                Approval.decision == "approve",
            )
        )
        .scalars()
        .first()
    )
    if already is not None:
        raise ReleaseError(
            f"RepairRun {repair_run_id!r} was already approved by {already.actor}: "
            "approving again would mint a duplicate release"
        )
    bundle = run.escalation_bundle if isinstance(run.escalation_bundle, dict) else None
    raw_proposal = bundle.get("proposal") if bundle is not None else None
    if not isinstance(raw_proposal, dict):
        raise ReleaseError(
            f"RepairRun {repair_run_id!r} carries no proposal: "
            "a business decision approves only a model-proposed change"
        )
    label = raw_proposal.get("label")
    effect = raw_proposal.get("effect")
    patch = raw_proposal.get("patch")
    if not isinstance(label, str) or not label.strip():
        raise ReleaseError("escalation proposal has no label")
    if not isinstance(effect, str) or not effect.strip():
        raise ReleaseError("escalation proposal has no effect")
    if not isinstance(patch, list) or not patch:
        raise ReleaseError("escalation proposal has no patch ops")
    for op in patch:
        if not isinstance(op, dict) or not op.get("op") or not op.get("path"):
            raise ReleaseError(f"escalation proposal op is not a patch op: {str(op)[:120]}")

    inferred_connector_id = connector_id
    if inferred_connector_id is None:
        drift_ids = list(run.drift_event_ids or [])
        if drift_ids:
            from autobridge.control.models import DriftEvent  # noqa: PLC0415

            drift = session.get(DriftEvent, drift_ids[0])
            if drift is not None:
                inferred_connector_id = drift.connector_id
    if not inferred_connector_id:
        raise ReleaseError("connector_id is required (no drift link on this repair run)")
    conn = _ensure_connector(session, inferred_connector_id)
    if conn.active_release_id is None:
        raise ReleaseError("connector has no active release to apply the proposal to")

    active_verifier = (
        verifier_url if verifier_url is not None else _os.environ.get("VERIFIER_URL")
    )
    candidate = create_release_from_patch(
        session,
        connector_id=inferred_connector_id,
        base_release_id=conn.active_release_id,
        patch=[dict(op) for op in patch if isinstance(op, dict)],
        provenance_repair_run_id=repair_run_id,
        verifier_url=active_verifier or None,
        scenario_id="baseline",
    )
    session.flush()
    if candidate.verification_report_id:
        # The verifier verdict lives on the repair row too (the candidate
        # release carries the report id) — the repair page reads verdicts
        # from here, never recomputed.
        run.verifier_report_id = candidate.verification_report_id
        session.flush()
    approval = approve_release(
        session,
        repair_run_id=repair_run_id,
        actor=actor.strip(),
        from_release_id=conn.active_release_id,
        to_release_id=candidate.id,
        connector_id=inferred_connector_id,
        knowledge_cache=knowledge_cache,
    )
    return candidate, approval
