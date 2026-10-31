"""Fault evidence for the repair benchmark — REQ-060 item 1.

Both frontier arms get, in addition to today's bundle (schema,
capabilities, a different-vendor example, the case's docs), the
pre-drift spec the engine arms sync and the evidence of the faulted
sync the engine's detector sees:

* the faulted sync outcome (``Complete`` | ``Degraded`` | ``Blocked``),
* the unmet criteria / failed checks,
* the served response sample for the affected operations (field names
  with redacted values).

Nothing more: no drift class, no fingerprint delta, no hypotheses, no
``expected`` outcome, nothing under ``verifier/``. The evidence is
rendered from the same in-process observation the engine arms derive
from (``execute_sync`` against the served fault), so the frontier arms
and the engine arms receive the same spec + evidence by content hash
(asserted by the fair-context test).

Secrets (DIRECTIVES §6): only ``credential_ref`` names travel; response
samples redact secret-looking fields and truncate long values.

Trust boundary (DIRECTIVES §2): this module drives simulator serving
code the way the tests do, but never imports ``verifier`` — grading
stays HTTP-only.
"""

from __future__ import annotations

import functools
import json
from typing import Any

#: Operations sampled per evidence bundle (keeps prompts bounded).
_MAX_OPS = 5

#: Records sampled per affected operation.
_MAX_RECORDS_PER_OP = 2

#: Keys whose values are never shown (credential/secret material).
_SECRET_KEY_HINTS = ("token", "key", "secret", "password", "auth", "credential", "session")


def _redact_value(key: str, value: Any, *, _depth: int = 0) -> Any:
    """Redact one sample value: secrets masked, long values truncated."""
    lowered = str(key).lower()
    if any(hint in lowered for hint in _SECRET_KEY_HINTS):
        return "***"
    if isinstance(value, dict):
        if _depth >= 2:
            return "{...}"
        return {str(k): _redact_value(str(k), v, _depth=_depth + 1) for k, v in list(value.items())[:12]}
    if isinstance(value, list):
        return [_redact_value(key, v, _depth=_depth + 1) for v in value[:5]]
    if isinstance(value, str):
        if len(value) > 80:
            return value[:80] + "…"
        return value
    return value


def _records_of(result: Any) -> list[dict[str, Any]]:
    """Raw served records from an operation result (dataclass or dict)."""
    records = getattr(result, "records", None)
    if records is None and isinstance(result, dict):
        records = result.get("records")
    if not isinstance(records, list):
        return []
    return [r for r in records if isinstance(r, dict)]


def render_sync_evidence(summary: dict[str, Any]) -> str:
    """Render the faulted-sync evidence text (no diagnosis, no labels).

    Reads only ``outcome``, ``unmet_criteria``, ``invariant_results``
    and ``operation_results`` — never a drift class, delta, hypothesis
    or expected outcome (those are not on the summary either).
    """
    outcome = str(summary.get("outcome", "unknown"))
    unmet = summary.get("unmet_criteria", []) or []
    unmet_names = [str(u) for u in unmet if str(u).strip()]
    invariant = summary.get("invariant_results", []) or []
    failed = [
        str(f.get("check", ""))
        for f in invariant
        if isinstance(f, dict) and str(f.get("status", "")) == "failed" and str(f.get("check", ""))
    ]
    lines = [
        f"sync outcome: {outcome}",
        f"unmet criteria: {', '.join(unmet_names) if unmet_names else '(none)'}",
        f"failed checks: {', '.join(failed) if failed else '(none)'}",
        "served response sample (affected operations; secret fields redacted):",
    ]
    op_results = summary.get("operation_results", {}) or {}
    names = sorted(op_results) if isinstance(op_results, dict) else []
    shown = 0
    for op_name in names[:_MAX_OPS]:
        records = _records_of(op_results[op_name])
        if not records:
            continue
        shown += 1
        lines.append(f"- operation {op_name}: {len(records)} record(s) served; sample:")
        for record in records[:_MAX_RECORDS_PER_OP]:
            redacted = {str(k): _redact_value(str(k), v) for k, v in list(record.items())[:20]}
            lines.append(f"  {redacted}")
    if shown == 0:
        lines.append("(no records served: the sync ended before the vendor responded)")
    return "\n".join(lines)


def render_fault_evidence(
    spec: dict[str, Any],
    *,
    scenario_id: str,
    connector_id: str,
    seed: int,
) -> str:
    """Observe the served fault and render its evidence (REQ-060 item 1).

    Runs the pre-drift *spec* against the faulted serving for
    (*scenario_id*, *connector_id*, *seed*) — the same observation the
    engine arms derive from — and renders outcome, unmet criteria /
    failed checks and the served response sample. Raises like the sync
    does when the fault cannot be served (unknown scenario, non-HTTP
    transport): a missing observation fails loudly, never silently.
    """
    from autobridge.engine.sync_run import execute_sync  # noqa: PLC0415
    from bench.faults import fake_env, fault_client_for  # noqa: PLC0415

    with fake_env():
        summary = execute_sync(
            dict(spec),
            fault_client_for(scenario_id, seed=seed, connector_id=connector_id),
        )
    return render_sync_evidence(summary)


def spec_to_yaml(spec: dict[str, Any]) -> str:
    """Canonical YAML of the pre-drift spec under repair (stable key order)."""
    import yaml  # noqa: PLC0415

    return yaml.safe_dump(dict(spec), sort_keys=True, default_flow_style=False)


@functools.lru_cache(maxsize=256)
def _cached_spec_and_evidence(
    spec_json: str, scenario_id: str, connector_id: str, seed: int
) -> tuple[str, str]:
    """One observation per (spec, scenario, seed) per process (REQ-060).

    Every arm builds the same context for a trial, and trials share
    seeds — without this the bundle would re-sync the served fault five
    times per trial (once per arm). The observation is deterministic in
    its inputs, so sharing it is exact, not approximate.
    """
    spec = json.loads(spec_json)
    return spec_to_yaml(spec), render_fault_evidence(
        spec, scenario_id=scenario_id, connector_id=connector_id, seed=seed
    )


def spec_and_evidence_for(
    spec: dict[str, Any], *, scenario_id: str, connector_id: str, seed: int
) -> tuple[str, str]:
    """Canonical spec YAML + faulted-sync evidence, cached per inputs."""
    spec_json = json.dumps(dict(spec), sort_keys=True, default=str)
    return _cached_spec_and_evidence(spec_json, scenario_id, connector_id, int(seed))


def healthz() -> dict[str, str]:
    """Health check for the fault-evidence module."""
    return {"status": "ok", "service": "fault_evidence"}
