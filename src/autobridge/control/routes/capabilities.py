# ruff: noqa: B008, TC002, TC003, ARG001, SIM102, SIM105, SIM118, UP035, I001, UP006, UP007, RUF012, B027
"""What the engine knows: vocabulary (capabilities) and learned knowledge (REQ-021 item 6)."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, get_args

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from autobridge.control.deps import get_db
from autobridge.control.models import Approval, BuildRun, Connector, ConnectorRelease, DriftEvent, ModelCall, RepairRun
from autobridge.control.schemas import (
    CapabilitiesOut,
    CapabilityRuleOut,
    CapabilityToolOut,
    CapabilityVersionOut,
    KnowledgeConnectorOut,
    KnowledgeLearnedFromOut,
    KnowledgeOut,
)

router = APIRouter(tags=["knowledge"])

#: Spec v2-only vocabulary (REQ-020 item 5): the generic, vendor-neutral
#: delta the pinned vocabulary gates on. v1 is everything else.
V2_ONLY_METHODS = frozenset({"POST"})
V2_ONLY_AUTH_TYPES = frozenset({"body_fields"})
V2_ONLY_PAGINATION = frozenset({"body_cursor"})

#: Spec v3-only vocabulary (REQ-022 sub-step C): the event-driven
#: transport. v3 is everything in v2, plus these.
V3_ONLY_METHODS = frozenset({"EVENT"})
V3_ONLY_AUTH_TYPES = frozenset({"edge_relay"})
V3_ONLY_PROTOCOLS = frozenset({"fias_tcp"})

V1_V2_DIFF = [
    "operations gain method POST with a JSON body template (v1 is GET-only)",
    "auth gains body_fields: credential refs placed into named JSON body fields (values never stored)",
    "pagination gains body_cursor: cursor and page size in named body paths, stop on null cursor or short page",
    "entities gain declarative joins: enrich one entity from another operation by key",
]

V2_V3_DIFF = [
    "source gains protocol fias_tcp with link settings as data (framing, ACK mode, announced link records)",
    "operations gain method EVENT over event feeds: records pushed to the agent, drained via the edge relay",
    "auth gains edge_relay: the relay operator token by credential_ref (never a vendor credential)",
    "fields gain multi-path mapping: one value per path fed as a list into the transform pipeline",
    "entities gain command templates: host-originated record templates (field id -> {param}) as data",
]


def _rule_fixes(rule_id: str, module: Any) -> str:
    """First docstring line of a rule module, without the repeated rule name.

    Rule docstrings open with their own id (``"page_to_cursor rule — ..."``)
    and the knowledge page already renders the id beside the text, so strip
    the leading ``<id>[ rule] —|:`` prefix and return the description once
    (REQ-021 owner check 2026-10-28).
    """
    import re as _re  # noqa: PLC0415 - local to keep route import-light

    doc = (getattr(module, "__doc__", "") or "").strip().splitlines()
    first = doc[0].strip() if doc else ""
    stripped = _re.sub(
        r"^" + _re.escape(rule_id) + r"(?:\s+rule)?\s*[—–\-:]\s*",
        "",
        first,
    )
    return stripped or first


def capabilities_payload() -> dict[str, Any]:
    """Build the vocabulary payload from code (registries + spec Literals)."""
    from typing import get_type_hints  # noqa: PLC0415

    from autobridge.repair.registry import RULE_REGISTRY  # noqa: PLC0415
    from autobridge.runtime.transforms import TRANSFORMS  # noqa: PLC0415
    from autobridge.spec import models as spec_models  # noqa: PLC0415

    auth_hints = get_type_hints(spec_models.AuthSpec)
    pag_hints = get_type_hints(spec_models.Pagination)
    op_hints = get_type_hints(spec_models.OperationSpec)
    src_hints = get_type_hints(spec_models.SourceSpec)
    methods = sorted(get_args(op_hints["method"]))
    auth_types = sorted(get_args(auth_hints["type"]))
    pagination = sorted(get_args(pag_hints["strategy"]))
    protocols = sorted(get_args(src_hints["protocol"]))

    def _version(which: str) -> dict[str, Any]:
        if which == "v1":
            only_v2 = False
            only_v3 = False
        elif which == "v2":
            only_v2 = True
            only_v3 = False
        else:
            only_v2 = False
            only_v3 = True
        return {
            "methods": sorted(
                m
                for m in methods
                if (m in V3_ONLY_METHODS) == only_v3 and (m in V2_ONLY_METHODS) == (only_v2 and not only_v3)
            ),
            "auth_types": sorted(
                a
                for a in auth_types
                if (a in V3_ONLY_AUTH_TYPES) == only_v3 and (a in V2_ONLY_AUTH_TYPES) == (only_v2 and not only_v3)
            ),
            "pagination_strategies": sorted(
                p for p in pagination if (p in V2_ONLY_PAGINATION) == (only_v2 and not only_v3)
            ),
            "protocols": sorted(
                p
                for p in protocols
                if (p in V3_ONLY_PROTOCOLS) == only_v3 and not (only_v2 and not only_v3)
            ),
            "joins": only_v2 or only_v3,
            "body_templates": only_v2 or only_v3,
            "event_feeds": only_v3,
            "command_templates": only_v3,
            "multi_path_mapping": only_v3,
        }

    rules = [
        {
            "id": entry.id,
            "fixes": _rule_fixes(entry.id, entry.module),
            "confidence": entry.confidence,
            "verification_level": entry.verification_level,
            "produces_patch": entry.produces_patch,
        }
        for entry in RULE_REGISTRY.values()
    ]
    return {
        "versions": {"v1": _version("v1"), "v2": _version("v2"), "v3": _version("v3")},
        "transforms": list(TRANSFORMS),
        "checks": sorted(get_args(spec_models.CheckKind)),
        "stop_conditions": sorted(get_args(spec_models.StopCondition)),
        "rules": rules,
        "v1_v2_diff": list(V1_V2_DIFF),
        "v2_v3_diff": list(V2_V3_DIFF),
        "tools": [dict(t) for t in AGENT_TOOLS],
    }


#: Curated agent surface (MCP tools/list style). These are the operations an
#: agent can invoke through the control API; descriptions name the contract.
AGENT_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "build_connector",
        "description": "Build a ConnectorSpec draft from vendor docs (POST /connectors/{id}/build). Pin spec_vocabulary v1|v2|v3.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "connector_id": {"type": "string"},
                "docs_text": {"type": "string"},
                "spec_vocabulary": {"type": "string", "enum": ["v1", "v2", "v3"]},
            },
            "required": ["connector_id"],
        },
    },
    {
        "name": "sync_release",
        "description": "Execute a release; ends Complete | Degraded | Blocked with the unmet criterion named.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "connector_id": {"type": "string"},
                "release_id": {"type": "string"},
                "spec_vocabulary": {"type": "string", "enum": ["v1", "v2", "v3"]},
            },
            "required": ["connector_id"],
        },
    },
    {
        "name": "inject_fault",
        "description": "Labelled demo fault injection through the control plane (POST /demo/inject-fault).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "scenario_id": {"type": "string"},
                "seed": {"type": "integer"},
            },
            "required": ["target", "scenario_id"],
        },
    },
    {
        "name": "get_repair_ledger",
        "description": "Rung-by-rung ledger for a repair: each rung tried, why, and its linked model calls.",
        "inputSchema": {
            "type": "object",
            "properties": {"repair_id": {"type": "string"}},
            "required": ["repair_id"],
        },
    },
    {
        "name": "approve_repair",
        "description": "Approve a proposed repair candidate release (records Approval.actor).",
        "inputSchema": {
            "type": "object",
            "properties": {"repair_id": {"type": "string"}},
            "required": ["repair_id"],
        },
    },
    {
        "name": "reset_demo",
        "description": "Return to a known first-run state: sims to baseline, L0 emptied, ledger archived, state wiped.",
        "inputSchema": {"type": "object", "properties": {}},
    },
)


@router.get("/capabilities", response_model=CapabilitiesOut)
def get_capabilities() -> CapabilitiesOut:
    payload = capabilities_payload()
    return CapabilitiesOut(
        versions={k: CapabilityVersionOut(**v) for k, v in payload["versions"].items()},
        transforms=list(payload["transforms"]),
        checks=list(payload["checks"]),
        stop_conditions=list(payload["stop_conditions"]),
        rules=[CapabilityRuleOut(**r) for r in payload["rules"]],
        v1_v2_diff=list(payload["v1_v2_diff"]),
        v2_v3_diff=list(payload["v2_v3_diff"]),
        tools=[CapabilityToolOut(**t) for t in payload["tools"]],
    )


def _canonical_patch(patch: Any) -> str:
    return json.dumps(patch or [], sort_keys=True, separators=(",", ":"), default=str)


def knowledge_payload(db: Session) -> dict[str, Any]:
    """Derive learned knowledge from stored runs (no extra tables).

    One entry per verified repair group — repairs that proposed the same
    patch for the same vendor/rule and carry at least one approve Approval.
    The first run is where it was learned (run id, rung, tokens, cost,
    time); later runs are reuses (L0 hits, 0 model calls). Connector
    profiles come from successful builds: vocabulary used plus confirmed
    bootstrap hypotheses.
    """
    runs = db.execute(select(RepairRun).order_by(RepairRun.created_at)).scalars().all()
    approvals = db.execute(select(Approval)).scalars().all()
    approved_repairs: set[str] = {
        a.repair_run_id for a in approvals if a.decision == "approve" and a.repair_run_id
    }

    groups: dict[tuple[str, str, str], list[RepairRun]] = {}
    for run in runs:
        if run.decision != "propose" or not run.candidate_patch or not run.rule_id:
            continue
        key = ((run.vendor or "unknown"), run.rule_id, _canonical_patch(run.candidate_patch))
        groups.setdefault(key, []).append(run)

    run_ids = [r.id for group in groups.values() for r in group]
    call_rows: list[Any] = []
    if run_ids:
        call_rows = list(db.execute(select(ModelCall).where(ModelCall.run_id.in_(run_ids))).scalars().all())
    cost_by_run: dict[str, Decimal] = {}
    for call in call_rows:
        cost_by_run[call.run_id] = cost_by_run.get(call.run_id, Decimal("0")) + (call.cost_estimate or Decimal("0"))

    drift_by_id: dict[str, DriftEvent] = {}
    drift_ids = {did for group in groups.values() for r in group for did in (r.drift_event_ids or [])}
    if drift_ids:
        for drift in db.execute(select(DriftEvent).where(DriftEvent.id.in_(drift_ids))).scalars().all():
            drift_by_id[drift.id] = drift

    entries: list[dict[str, Any]] = []
    for (vendor, rule_id, _patch_key), group in sorted(groups.items(), key=lambda kv: kv[1][0].created_at):
        if not any(r.id in approved_repairs for r in group):
            continue  # learned entries need a verified (approved) repair
        first = group[0]
        first_usage = dict(first.model_usage or {})
        first_tokens = int(first_usage.get("frontier_tokens", 0) or 0) + int(first_usage.get("small_tokens", 0) or 0)
        first_cost = cost_by_run.get(first.id, Decimal("0"))
        reuses = len(group) - 1
        drift_class = ""
        for did in first.drift_event_ids or []:
            if did in drift_by_id:
                drift_class = drift_by_id[did].drift_class
                break
        entries.append(
            {
                "vendor": vendor,
                "drift_class": drift_class,
                "drift_signature": first.drift_signature or "",
                "rule_id": rule_id,
                "patch": list(first.candidate_patch or []),
                "learned_from": {
                    "run_id": first.id,
                    "rung": first.ladder_level_reached,
                    "tokens": first_tokens,
                    "cost": str(first_cost),
                    "at": first.created_at,
                },
                "times_reused": reuses,
                "tokens_saved": first_tokens * reuses,
                "cost_saved": str(first_cost * reuses),
                "approvals": sum(1 for r in group if r.id in approved_repairs),
            }
        )

    connectors: list[dict[str, Any]] = []
    for conn in db.execute(select(Connector).order_by(Connector.created_at)).scalars().all():
        builds = (
            db.execute(
                select(BuildRun)
                .where(BuildRun.connector_id == conn.id)
                .order_by(BuildRun.created_at)
            )
            .scalars()
            .all()
        )
        successful = [b for b in builds if isinstance(b.result, dict) and b.result.get("success")]
        confirmed: list[str] = []
        for build in successful:
            for entry in build.ladder_trace or []:
                if not isinstance(entry, dict):
                    continue
                hyp = entry.get("hypothesis")
                if isinstance(hyp, str) and hyp and entry.get("decision") not in ("escalate", "degraded"):
                    if hyp not in confirmed:
                        confirmed.append(hyp)
        vocabulary = ""
        last_built_at = None
        if successful:
            last = successful[-1]
            vocabulary = str((last.inputs or {}).get("spec_vocabulary", "") or "")
            last_built_at = last.created_at
        if not vocabulary:
            # Builds that predate the pinned-vocabulary input (or connectors
            # built outside the build route) still show their vocabulary:
            # fall back to the latest release's spec schema version.
            latest = (
                db.execute(
                    select(ConnectorRelease)
                    .where(ConnectorRelease.connector_id == conn.id)
                    .order_by(ConnectorRelease.version.desc())
                )
                .scalars()
                .first()
            )
            if latest is not None:
                schema_version = (latest.spec or {}).get("spec_version") if isinstance(latest.spec, dict) else None
                if schema_version in (1, 2, 3):
                    vocabulary = f"v{schema_version}"
        connectors.append(
            {
                "connector_id": conn.id,
                "name": conn.name,
                "vocabulary": vocabulary,
                "builds": len(builds),
                "confirmed_hypotheses": confirmed,
                "last_built_at": last_built_at,
            }
        )

    return {
        "entries": entries,
        "connectors": connectors,
        "counts": {
            "entries": len(entries),
            "reuses": sum(e["times_reused"] for e in entries),
            "tokens_saved": sum(e["tokens_saved"] for e in entries),
        },
    }


@router.get("/knowledge", response_model=KnowledgeOut)
def get_knowledge(db: Session = Depends(get_db)) -> KnowledgeOut:
    from autobridge.control.schemas import KnowledgeEntryOut  # noqa: PLC0415

    payload = knowledge_payload(db)
    return KnowledgeOut(
        entries=[
            KnowledgeEntryOut(
                **{**e, "learned_from": KnowledgeLearnedFromOut(**e["learned_from"])},
            )
            for e in payload["entries"]
        ],
        connectors=[KnowledgeConnectorOut(**c) for c in payload["connectors"]],
        counts=dict(payload["counts"]),
    )
