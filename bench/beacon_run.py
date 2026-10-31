"""Beacon evaluation runs: freeze, run, reproduce (REQ-025 items 4–7).

Protocol (commit-then-reveal with public randomness):

* ``freeze`` writes ``experiments/beacon/protocol.md`` (round rule, N
  instances, arms, metrics, trials, budgets, the engine + generator
  commits, "one run, no retries, results published whatever they are").
  Refuses when a protocol already exists — the freeze is commit-once.
* ``run`` reads the protocol, refuses when the round is not yet published
  or a result for it already exists (no re-rolls), fetches + verifies the
  round (``beacon_client``), prints the evidence, generates nothing itself
  (the simulators/verifier generate internally from the seed it passes),
  pushes each instance to its vendor's simulator admin plane, runs every
  REQ-024 arm on the same instances under matched budget, grades with the
  verifier (``/beacon/records`` + ``/beacon/grade``), and writes
  ``experiments/beacon/<round>/``.
* ``verify-repro`` re-fetches + verifies the round, regenerates the
  instance descriptors, and checks them against the published ones.

Trust boundary (DIRECTIVES §2): bench-side only. This module never imports
``verifier`` and never sees generator internals or answer keys: it passes
only the eval seed (public beacon randomness) to the services over HTTP.
In-process instance serving drives simulator serving code the way the
tests do (see ``bench.faults``) and reads reference specs / vendor docs
from the filesystem. Grading happens server-side; the harness only
combines the two endpoint verdicts.

Instance descriptors (``{index, vendor, family, params}``) are public —
the fault families are known by design; what could not be known in
advance are the *instances* (fixed by a round that did not exist at
freeze time). ``expected`` never appears in any bench-side file.
"""

from __future__ import annotations

import copy
import dataclasses
import datetime as _dt
import json
import os
import pathlib
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, cast

from bench import beacon_client
from bench.beacon_client import BeaconError

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
BEACON_DIR = REPO_ROOT / "experiments" / "beacon"
PROTOCOL_PATH = BEACON_DIR / "protocol.md"
VENDORS_PATH = REPO_ROOT / "config" / "beacon_vendors.yaml"

#: The five REQ-024 ablation arms, in canonical order (item 5: same arms).
ABLATION_ARMS: tuple[str, ...] = (
    "frontier_oneshot",
    "frontier_agent",
    "engine_no_knowledge",
    "autobridge_cold",
    "autobridge_warm",
)

#: Canonical-entity → produced-records key (verifier judges reservations/folios).
ENTITY_RECORD_KEYS: dict[str, str] = {
    "reservation": "reservations",
    "foliocharge": "folios",
    "folio": "folios",
    "property": "properties",
    "guest": "guests",
}


@dataclass(frozen=True)
class BeaconVendor:
    """One vendor's routing + engine-spec source (loaded from config YAML).

    Vendor names live in ``config/beacon_vendors.yaml``, never in engine
    code (unseen-vendor guards). ``http=False`` vendors ride non-HTTP
    transports: no live sync — candidate records are verified in-process
    over the announced feed layout. ``docs`` is the vendor docs path the
    engine's hypothesis engine reads (equal input, same as the frontier
    arms' docs).
    """

    vendor: str
    http: bool
    admin_url: str
    public_url: str
    spec: dict[str, str] | None = None
    docs: str = ""


def load_vendors(path: str | pathlib.Path = VENDORS_PATH) -> list[BeaconVendor]:
    """Load the round-robin vendor table (fail closed on any deviation)."""
    import yaml as _yaml

    try:
        doc = _yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        raise BeaconError(f"cannot read vendor table {path}: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("order"), list) or not doc["order"]:
        raise BeaconError(f"vendor table {path} needs a non-empty 'order' list")
    vendors: list[BeaconVendor] = []
    for entry in doc["order"]:
        if not isinstance(entry, dict):
            raise BeaconError(f"vendor table {path} has a non-mapping entry")
        vendor = entry.get("vendor")
        if not isinstance(vendor, str) or not vendor:
            raise BeaconError(f"vendor table {path} entry needs a 'vendor' name")
        spec = entry.get("spec")
        vendors.append(
            BeaconVendor(
                vendor=vendor,
                http=bool(entry.get("http", True)),
                admin_url=str(entry.get("admin_url", "")).rstrip("/"),
                public_url=str(entry.get("public_url", "")).rstrip("/"),
                spec=dict(spec) if isinstance(spec, dict) else None,
                docs=str(entry.get("docs", "")),
            )
        )
    if len({v.vendor for v in vendors}) != len(vendors):
        raise BeaconError(f"vendor table {path} has duplicate vendors")
    return vendors

#: Non-secret placeholder credential the simulators accept for bench syncs.
#: Simulators accept any non-empty bearer token; this value is a dummy, not
#: a secret, and is only ever used against local/beacon simulators.
BEACON_BENCH_CREDENTIAL = "beacon-bench-placeholder"  # noqa: S105


def _utc_now() -> _dt.datetime:
    return _dt.datetime.now(tz=_dt.UTC)


# ---------------------------------------------------------------------------
# URL resolution
# ---------------------------------------------------------------------------


def _parse_url_map(env_value: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for chunk in env_value.split(","):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        vendor, url = chunk.split("=", 1)
        vendor, url = vendor.strip(), url.strip().rstrip("/")
        if vendor and url:
            out[vendor] = url
    return out


def admin_urls(vendors: list[BeaconVendor] | None = None) -> dict[str, str]:
    """Per-vendor simulator admin URLs (env ``BEACON_ADMIN_URLS`` wins over YAML defaults)."""
    urls = {v.vendor: v.admin_url for v in (vendors if vendors is not None else load_vendors())}
    urls.update(_parse_url_map(os.environ.get("BEACON_ADMIN_URLS", "")))
    return urls


def public_urls(vendors: list[BeaconVendor] | None = None) -> dict[str, str]:
    """Per-vendor simulator public URLs (env ``BEACON_PUBLIC_URLS`` wins over YAML defaults)."""
    urls = {v.vendor: v.public_url for v in (vendors if vendors is not None else load_vendors())}
    urls.update(_parse_url_map(os.environ.get("BEACON_PUBLIC_URLS", "")))
    return urls


def admin_headers() -> dict[str, str]:
    """Admin-plane headers (routing hint + token when configured)."""
    headers = {"X-Admin-Network": "admin-net"}
    token = os.environ.get("SIM_ADMIN_TOKEN", "").strip()
    if token:
        headers["X-Admin-Token"] = token
    return headers


# ---------------------------------------------------------------------------
# Freeze (item 4)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BeaconProtocol:
    """Parsed ``experiments/beacon/protocol.md`` freeze record."""

    round_after_utc: str
    target_round: int
    faults: int
    trials: int
    arms: tuple[str, ...]
    engine_commit: str
    generator_commit: str
    frozen_at_utc: str
    rehearsal: bool = False
    # REQ-066: the freeze pins the frontier model (every arm resolves it
    # through ``autobridge.config.frontier_model``) so the freeze proves
    # the model was fixed before the random value existed. ``None`` =
    # a pre-REQ-066 protocol without the line (still parses; rehearsal-only).
    frontier_model: str | None = None
    # The agent arm's resolved opencode model (``meta/<id>`` form), only
    # written when it differs from ``frontier_model``.
    agent_model: str | None = None


def parse_protocol(path: str | pathlib.Path = PROTOCOL_PATH) -> BeaconProtocol:
    """Parse a frozen protocol file (fail closed on any deviation)."""
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise BeaconError(f"cannot read protocol {path}: {exc}") from exc
    fields: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            stripped = stripped[2:].strip()
        if stripped.startswith("beacon-") and ":" in stripped:
            key, _, value = stripped.partition(":")
            fields[key.strip()] = value.strip()
    required = (
        "beacon-round-after-utc",
        "beacon-target-round",
        "beacon-faults",
        "beacon-trials",
        "beacon-arms",
        "beacon-engine-commit",
        "beacon-generator-commit",
        "beacon-frozen-at-utc",
    )
    missing = [k for k in required if k not in fields]
    if missing:
        raise BeaconError(f"protocol {path} missing fields: {missing}")
    try:
        target_round = int(fields["beacon-target-round"])
        faults = int(fields["beacon-faults"])
        trials = int(fields["beacon-trials"])
    except ValueError as exc:
        raise BeaconError(f"protocol {path} has non-integer numeric fields: {exc}") from exc
    if target_round < 1 or faults < 1 or faults > 10_000 or trials < 1:
        raise BeaconError(f"protocol {path} has out-of-range numeric fields")
    arms = tuple(a.strip() for a in fields["beacon-arms"].split(",") if a.strip())
    if not arms or any(a not in ABLATION_ARMS for a in arms):
        raise BeaconError(f"protocol {path} has unknown arms: {arms}")
    return BeaconProtocol(
        round_after_utc=fields["beacon-round-after-utc"],
        target_round=target_round,
        faults=faults,
        trials=trials,
        arms=arms,
        engine_commit=fields["beacon-engine-commit"],
        generator_commit=fields["beacon-generator-commit"],
        frozen_at_utc=fields["beacon-frozen-at-utc"],
        rehearsal=fields.get("beacon-rehearsal", "false").strip().lower() == "true",
        frontier_model=fields.get("beacon-frontier-model") or None,
        agent_model=fields.get("beacon-agent-model") or None,
    )


def freeze(
    round_after_utc: str,
    faults: int,
    *,
    trials: int = 5,
    arms: tuple[str, ...] = ABLATION_ARMS,
    base_url: str = beacon_client.DRAND_DEFAULT_BASE_URL,
    rehearsal: bool = False,
    protocol_path: str | pathlib.Path | None = None,
) -> pathlib.Path:
    """Freeze the eval protocol (item 4). Refuses when one already exists.

    ``round_after_utc`` is ISO UTC; the target round is the first round
    published at/after it (computed against live chain info, whose period
    and genesis are pinned by verification). Records the engine +
    generator commits (must be committed — dirty tree refuses).
    """
    path = PROTOCOL_PATH if protocol_path is None else pathlib.Path(protocol_path)
    if path.exists():
        raise BeaconError(f"refusing to re-freeze: {path} already exists (commit-once)")
    if not isinstance(faults, int) or isinstance(faults, bool) or faults < 1 or faults > 10_000:
        raise BeaconError("faults must be an int in [1, 10000]")
    if not isinstance(trials, int) or isinstance(trials, bool) or trials < 1:
        raise BeaconError("trials must be a positive int")
    if not arms or any(a not in ABLATION_ARMS for a in arms):
        raise BeaconError(f"unknown arms: {arms}")
    when = _dt.datetime.fromisoformat(round_after_utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.UTC)
    now = _utc_now()
    if when <= now and not rehearsal:
        raise BeaconError("ROUND_AFTER must be in the future for a real freeze (past rounds are rehearsals)")
    info = beacon_client.fetch_chain_info(base_url)
    target_round = beacon_client.round_for_time(
        int(info["genesis_time"]), int(info["period"]), int(when.timestamp())
    )
    result_dir = BEACON_DIR / str(target_round)
    if result_dir.exists():
        raise BeaconError(f"refusing freeze: result dir {result_dir} already exists (no re-rolls)")
    engine_commit = _git_commit()
    generator_commit = _git_commit()
    if _git_dirty():
        raise BeaconError("refusing freeze: git tree is dirty (commit everything first)")
    # REQ-066: pin the frontier model every arm resolves (the same
    # ``autobridge.config.frontier_model()`` the arms use), so the freeze
    # proves the model was fixed before the random value existed. The
    # agent arm's resolved opencode model rides alongside when it differs
    # in form (``meta/<id>``).
    from autobridge import config as _engine_config  # noqa: PLC0415

    pinned_frontier = _engine_config.frontier_model()
    pinned_agent = _engine_config.opencode_model()
    agent_line = (
        f"\n- beacon-agent-model: {pinned_agent}" if pinned_agent != pinned_frontier else ""
    )
    frozen_at = now.isoformat()
    body = f"""# Beacon evaluation protocol (REQ-025 item 4)

One run, no retries, results published whatever they are. Frozen before the
target round exists: the instances (fault parameters per seed) could not be
known at freeze time. Fault *families* are known by design — that is the
engine's everyday knowledge; the honest-scope note in docs/benchmark.md
applies. The frontier model below is pinned: ``bench-beacon`` refuses to
run when the resolved model differs (REQ-066).

- beacon-round-after-utc: {when.isoformat()}
- beacon-target-round: {target_round}
- beacon-faults: {faults}
- beacon-trials: {trials}
- beacon-arms: {", ".join(arms)}
- beacon-engine-commit: {engine_commit}
- beacon-generator-commit: {generator_commit}
- beacon-frozen-at-utc: {frozen_at}
- beacon-rehearsal: {"true" if rehearsal else "false"}
- beacon-frontier-model: {pinned_frontier}{agent_line}
- beacon-chain: {beacon_client.LOE_MAINNET_CHAIN_HASH} scheme={beacon_client.LOE_SCHEME_ID}
- beacon-verify-url: {base_url.rstrip("/")}/public/{target_round}

Metrics per arm, scenario and overall (mean + 95% interval over trials):
verified completion rate (judged only by the verifier), wrong patches that
reached a release (must be 0 for AutoBridge), correct escalations
(escalated exactly when the instance says so), frontier tokens, estimated
cost, wall time.

Engine-arm inputs (REQ-024 owner check on f32b4b4b, fixed): every arm gets
the same inputs (the pre-drift spec, the docs, the served instance,
budgets). The engine arms observe the instance end to end — sync the
served shape (the same simulator serving code the live stack runs,
deterministic from the public seed) → detector → classifier → the
engine's own hypothesis enumerator → ladder → verifier gate — with no
case-supplied drift class, delta, hypotheses or yield count. Instance
parameters, generator internals and answer keys never reach the arms.
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def require_pinned_model(protocol: BeaconProtocol) -> None:
    """Refuse the run unless the resolved frontier model matches the freeze (REQ-066).

    A protocol without the line (older rehearsals) still parses: on a
    rehearsal it warns and runs unpinned; a real run requires the line and
    refuses without it. Any mismatch — frontier or agent form — refuses
    with the expected vs actual model named.
    """
    from autobridge import config as _engine_config  # noqa: PLC0415

    resolved = _engine_config.frontier_model()
    pinned = protocol.frontier_model
    if pinned is None:
        if protocol.rehearsal:
            print(
                "beacon run: no pinned frontier-model in protocol "
                "(pre-REQ-066 rehearsal); running unpinned",
                flush=True,
            )
            return
        raise BeaconError(
            "refusing real run: protocol has no beacon-frontier-model line "
            "(re-freeze with the REQ-066 code; unpinned protocols are rehearsal-only)"
        )
    if resolved != pinned:
        raise BeaconError(
            f"refusing run: resolved frontier model {resolved!r} differs from "
            f"the frozen protocol's beacon-frontier-model {pinned!r} "
            "(the freeze pins the model before the random value exists)"
        )
    agent_pinned = protocol.agent_model
    if agent_pinned is not None:
        resolved_agent = _engine_config.opencode_model()
        if resolved_agent != agent_pinned:
            raise BeaconError(
                f"refusing run: resolved agent model {resolved_agent!r} differs from "
                f"the frozen protocol's beacon-agent-model {agent_pinned!r}"
            )


def _git_commit() -> str:
    import subprocess as _sp

    try:
        out = _sp.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(REPO_ROOT),
        )
    except Exception as exc:
        raise BeaconError(f"cannot determine git commit: {exc}") from exc
    if out.returncode != 0:
        raise BeaconError("cannot determine git commit (not a git checkout?)")
    return out.stdout.strip()


def _git_dirty() -> bool:
    import subprocess as _sp

    try:
        out = _sp.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(REPO_ROOT),
        )
    except Exception:
        return True
    return bool(out.stdout.strip())


# ---------------------------------------------------------------------------
# Simulator push (item 5: the harness passes only the seed)
# ---------------------------------------------------------------------------


def push_beacon(
    eval_seed_hex: str,
    index: int,
    vendor: str,
    *,
    admin_url: str | None = None,
    timeout_s: float = 10.0,
) -> dict[str, Any]:
    """Push ``(eval_seed_hex, index)`` to a vendor's simulator admin plane.

    Returns the public descriptor ``{index, vendor, family, params}``
    (never the answer key). FIAS uses ``/admin/fias/beacon``; the four
    REST vendors use ``/admin/beacon``. Fail closed on transport error,
    non-200 status, or a vendor mismatch in the response.
    """
    import httpx as _httpx

    url = (admin_url or admin_urls().get(vendor) or "").rstrip("/")
    if not url:
        raise BeaconError(f"no admin URL for vendor {vendor!r}")
    path = "/admin/fias/beacon" if vendor == "fias-pabx" else "/admin/beacon"
    try:
        resp = _httpx.post(
            url + path,
            json={"eval_seed_hex": eval_seed_hex, "index": index},
            headers=admin_headers(),
            timeout=timeout_s,
        )
    except Exception as exc:
        raise BeaconError(f"beacon push to {vendor} failed: {type(exc).__name__}: {exc}") from exc
    if resp.status_code != 200:
        raise BeaconError(f"beacon push to {vendor} rejected: HTTP {resp.status_code}")
    try:
        data = resp.json()
    except Exception as exc:
        raise BeaconError(f"beacon push to {vendor}: invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise BeaconError(f"beacon push to {vendor}: unexpected response shape")
    descriptor = data.get("beacon", data)
    if not isinstance(descriptor, dict):
        raise BeaconError(f"beacon push to {vendor}: unexpected response shape")
    if str(descriptor.get("vendor")) != vendor or int(descriptor.get("index", -1)) != index:
        raise BeaconError(f"beacon push to {vendor}: descriptor mismatch {descriptor}")
    for key in ("family", "params"):
        if key not in descriptor:
            raise BeaconError(f"beacon push to {vendor}: descriptor missing {key!r}")
    if "expected" in descriptor or "seed_hex" in descriptor or "dataset_seed" in descriptor:
        raise BeaconError(f"beacon push to {vendor}: descriptor leaks the answer key")
    return {"index": index, "vendor": vendor, "family": str(descriptor["family"]), "params": dict(descriptor["params"])}


# ---------------------------------------------------------------------------
# Engine-arm cases from the public descriptor (filesystem + descriptor only)
# ---------------------------------------------------------------------------


def _vendor_entry(
    vendor: str, vendors: list[BeaconVendor] | None = None
) -> BeaconVendor | None:
    for entry in vendors if vendors is not None else load_vendors():
        if entry.vendor == vendor:
            return entry
    return None


def engine_spec_for(descriptor: dict[str, Any], vendors: list[BeaconVendor] | None = None) -> dict[str, Any] | None:
    """Reference/docs spec for the descriptor's vendor (None when none exists).

    Filesystem sources only (generated reference specs, vendor docs —
    the spec source rides the vendor table, never engine code). REQ-062
    item 2: every beacon vendor has an in-process engine spec, PABX
    included (built from its docs as in the demo, vocabulary from the
    vendor table, default v2).
    """
    from bench import ablation as _ablation

    entry = _vendor_entry(str(descriptor["vendor"]), vendors)
    source = entry.spec if entry is not None else None
    if source is None:
        return None
    try:
        if source["kind"] == "generated":
            return _ablation._load_reference_spec(source["connector"])
        vocabulary = str(source.get("vocabulary", "v2") or "v2")
        return _ablation._docs_spec(source["path"], source["connector"], vocabulary)
    except Exception:
        return None


def beacon_case_for(descriptor: dict[str, Any], vendors: list[BeaconVendor] | None = None) -> Any | None:
    """Equal task setup for a beacon instance (spec + docs, no diagnostics).

    REQ-024 owner check on f32b4b4b: returns an
    :class:`ablation.AblationCase` (scenario id, pre-drift spec,
    connector, vendor docs) built from the vendor table + filesystem
    only — never from generator internals, instance parameters or answer
    keys. The fault is observed at arm time by syncing the served
    instance (see :func:`derive_beacon_diagnostics`). ``None`` only when
    the vendor table carries no spec source for the vendor.
    """
    index = int(descriptor["index"])
    spec = engine_spec_for(descriptor, vendors)
    if spec is None:
        return None
    entry = _vendor_entry(str(descriptor["vendor"]), vendors)
    source = entry.spec if entry is not None else None
    if source is None:
        return None
    from bench import ablation as _ablation

    kind = str(source.get("kind", ""))
    if kind == "generated" or kind == "docs":
        connector_id = str(source["connector"])
    else:
        return None
    docs_ref = entry.docs if entry is not None else ""
    if not docs_ref:
        return None
    return _ablation.AblationCase(
        scenario_id=f"beacon:{index}",
        spec=spec,
        connector_id=connector_id,
        docs_ref=docs_ref,
    )


def beacon_bench_context(
    descriptor: dict[str, Any],
    case: Any,
    inputs: Any,
    eval_seed_hex: str,
) -> Any:
    """Shared repair-task context for a beacon instance (REQ-062 item 6).

    The same bundle every arm gets: the pre-drift spec under repair plus
    the faulted sync's evidence (outcome, unmet criteria / failed checks,
    served response sample), alongside the schema, capabilities, a
    different-vendor example, the vendor docs and the patch-or-escalate
    answer format. Built from the public descriptor + public seed only —
    the same observation the engine arms derive from, never instance
    parameters, generator internals or answer keys. ``fias-pabx``
    evidence is the announced-layout feed sync (framed transport has no
    HTTP wire sample).
    """
    import functools as _functools
    import json as _json

    from bench.context import (  # noqa: PLC0415
        BenchContext,
        capabilities_json_for_version,
        connector_schema_json,
        example_spec_for,
        vendor_docs_text,
    )
    from bench.evidence import render_sync_evidence, spec_to_yaml  # noqa: PLC0415
    from bench.faults import beacon_sync_pair as _beacon_sync_pair  # noqa: PLC0415

    @_functools.lru_cache(maxsize=256)
    def _cached_evidence(spec_json: str, seed_hex: str, index: int, vendor: str) -> str:
        faulted_summary, _baseline_summary = _beacon_sync_pair(
            vendor=vendor, eval_seed_hex=seed_hex, index=index, spec=_json.loads(spec_json)
        )
        return render_sync_evidence(faulted_summary)

    spec = dict(case.spec)
    spec_json = _json.dumps(spec, sort_keys=True, default=str)
    try:
        version_num = int(spec.get("spec_version", 1))
    except (TypeError, ValueError):
        version_num = 1
    version = {1: "v1", 2: "v2"}.get(version_num, "v3")
    _example_vendor, _example_spec, example_yaml = example_spec_for(case.connector_id)
    try:
        evidence_text = _cached_evidence(spec_json, eval_seed_hex, int(descriptor["index"]), str(descriptor["vendor"]))
    except Exception as exc:
        evidence_text = f"(fault evidence unavailable: {type(exc).__name__}: {exc})"
    return BenchContext(
        scenario_id=case.scenario_id,
        connector_id=case.connector_id,
        vocabulary_version=version,
        schema_json=connector_schema_json(),
        capabilities_json=capabilities_json_for_version(version),
        example_vendor=_example_vendor,
        example_spec_yaml=example_yaml,
        docs_text=vendor_docs_text(case.docs_ref),
        visible_tests=tuple(getattr(inputs, "visible_tests", ()) or ()),
        pre_drift_spec_yaml=spec_to_yaml(spec),
        evidence_text=evidence_text,
    )


def derive_beacon_diagnostics(
    spec: dict[str, Any],
    *,
    descriptor: dict[str, Any],
    eval_seed_hex: str,
    vendor: str,
    connector_id: str,
) -> Any:
    """Observe a beacon instance end to end (public descriptor only).

    Serves the instance in-process through the vendor's real app
    (:func:`faults.beacon_sync_pair` — the same serving code the live
    simulator runs, deterministic from the public seed), then runs the
    detector → classifier over the served observations. Never reads
    instance parameters as diagnostics, generator internals or answer
    keys: the only inputs are the pre-drift spec and the public
    ``(eval_seed_hex, index, vendor)`` every arm receives.

    ``fias-pabx`` rides framed/event transport: the observation is the
    announced PS layout (the same handshake the live edge agent
    reports), compared against the spec's mapped wire ids with
    ``detect_feed_field_rename`` — the announced ids and sample values
    come from the served feed records (observation), never from
    instance parameters.
    """
    from bench import ablation as _ablation
    from bench import faults as _faults

    if vendor == "fias-pabx":
        return _derive_fias_diagnostics(
            spec,
            descriptor=descriptor,
            eval_seed_hex=eval_seed_hex,
        )
    faulted_summary, baseline_summary = _faults.beacon_sync_pair(
        vendor=vendor,
        eval_seed_hex=eval_seed_hex,
        index=int(descriptor["index"]),
        spec=spec,
    )
    _ = connector_id  # serving follows the vendor app, not the connector id
    return _ablation.derive_from_summaries(
        spec, baseline_summary=baseline_summary, faulted_summary=faulted_summary
    )


def _derive_fias_diagnostics(
    spec: dict[str, Any],
    *,
    descriptor: dict[str, Any],
    eval_seed_hex: str,
) -> Any:
    """FIAS diagnostics: announced layout vs spec-mapped wire ids (observed).

    Syncs the pre-drift spec against the announced feed (faulted) and
    the mapped baseline, then reports the announced-vs-mapped drift the
    same way the live edge path does. The announced ids are read off
    the served faulted feed records — the handshake observation every
    arm could make — and the signal shape matches the HTTP
    ``keys_changed`` wire evidence the ladder rules consume.
    """
    from autobridge.drift.classifier import classify  # noqa: PLC0415
    from autobridge.drift.event_feeds import (  # noqa: PLC0415
        _feed_operations,
        detect_feed_field_rename,
    )
    from autobridge.drift.signals import DriftSignal  # noqa: PLC0415
    from bench import ablation as _ablation
    from bench import faults as _faults

    index = int(descriptor["index"])
    faulted_summary, baseline_summary = _faults.beacon_sync_pair(
        vendor="fias-pabx",
        eval_seed_hex=eval_seed_hex,
        index=index,
        spec=spec,
    )

    def _wire_keys(summary: Any) -> set[str]:
        keys: set[str] = set()
        op_results = summary.get("operation_results", {})
        if isinstance(op_results, dict):
            for op_result in op_results.values():
                records = op_result.get("records") if isinstance(op_result, dict) else getattr(op_result, "records", None)
                if isinstance(records, list):
                    for rec in records:
                        if isinstance(rec, dict):
                            keys.update(str(k) for k in rec)
        return keys

    wire_records: list[dict[str, Any]] = []
    op_results = faulted_summary.get("operation_results", {})
    if isinstance(op_results, dict):
        for op_result in op_results.values():
            records = op_result.get("records") if isinstance(op_result, dict) else getattr(op_result, "records", None)
            if isinstance(records, list):
                wire_records.extend([r for r in records if isinstance(r, dict)])
                if wire_records:
                    break
    announced = sorted({str(k) for rec in wire_records for k in rec})
    report = detect_feed_field_rename(spec, "PS", announced, sample_records=wire_records)
    entity = report.get("entity")
    op_name: str | None = None
    try:
        for name, _feed in _feed_operations(spec, "PS"):
            op_name = str(name)
            break
    except Exception:
        op_name = None
    # Scope added to genuinely new keys (mirrors the HTTP build-probe
    # annotation: perennial unmapped wire keys are not drift evidence).
    baseline_keys = _wire_keys(baseline_summary)
    mapped_set = set(report.get("mapped_wire_ids", {}).values()) if isinstance(report.get("mapped_wire_ids"), dict) else set()
    scoped_added = [
        fid for fid in (report.get("added_wire", []) or [])
        if fid not in baseline_keys and fid not in mapped_set
    ]
    missing = list(report.get("missing_wire", []) or [])
    added = [
        {"key": fid, "sample": next((r.get(fid) for r in wire_records if isinstance(r, dict) and fid in r), None), "type": "str"}
        for fid in scoped_added
    ]
    signal = DriftSignal(
        kind="keys_changed" if missing else "fingerprint_changed",
        operation=op_name,
        entity=entity if isinstance(entity, str) else None,
        severity="medium",
        message=(
            f"PABX announced layout changed: missing {missing}, new {[a['key'] for a in added]}"
            if missing
            else "PABX announced layout matches the spec mapping"
        ),
        details={
            "entity": entity,
            "missing": missing,
            "added": added,
            "wire_evidence": "feed-announced-layout",
        },
        fingerprint_delta=report.get("delta"),
    )
    if not missing:
        # Mapping intact (pure additions or no change): no drift to repair.
        # No signals — the row states detection found nothing (REQ-062 item 1).
        return _ablation.DerivedDiagnostics(
            drift_class="UNKNOWN",
            fingerprint_delta=report.get("delta"),
            signals=(),
            classification_reason="No drift signals observed",
            faulted_outcome=str(faulted_summary.get("outcome", "")),
        )
    classification = classify([signal])
    return _ablation.DerivedDiagnostics(
        drift_class=classification.drift_class,
        fingerprint_delta=report.get("delta"),
        signals=(signal,),
        classification_reason=classification.reason,
        faulted_outcome=str(faulted_summary.get("outcome", "")),
    )


# ---------------------------------------------------------------------------
# Candidate sync (real runtime, in-process, against the live beacon shape)
# ---------------------------------------------------------------------------


def canonical_to_records(canonical: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Map sync-summary canonical entities to produced-records keys."""
    produced: dict[str, list[dict[str, Any]]] = {"reservations": [], "folios": []}
    if not isinstance(canonical, dict):
        return produced
    for entity, rows in canonical.items():
        if not isinstance(rows, list):
            continue
        key = ENTITY_RECORD_KEYS.get(str(entity).lower())
        if key is None:
            continue
        if key not in produced:
            produced[key] = []
        produced[key].extend([r for r in rows if isinstance(r, dict)])
    return produced


def sanity_unchanged_repairs(
    eval_seed_hex: str,
    descriptors: list[dict[str, Any]],
    vendors: list[BeaconVendor] | None = None,
) -> list[dict[str, Any]]:
    """Flag repair instances whose fault changes nothing the engine can observe.

    REQ-063 item 3: for every instance whose answer key expects ``repair``
    (via :func:`faultgen.expected_for`, the same rule the verifier uses —
    never the answer key itself), sync the pre-drift spec against the
    faulted and baseline shapes in-process and compare the canonical
    output. An output equal to the baseline *with no drift signals* means
    the fault never reaches what the connector reads: a generator bug,
    never an engine failure. A wire-shape drift the detector sees (e.g. a
    renamed pagination cursor on a single-page dataset) stays solvable —
    the ladder repairs from the observed signals — so signals spare the
    instance. Returns one entry per flagged instance (empty when clean).
    Bench-side only — ``verifier/`` stays free of engine imports.
    """
    from bench.faults import beacon_sync_pair as _beacon_sync_pair  # noqa: PLC0415
    from simulators.common import faultgen as _faultgen  # noqa: PLC0415 - simulators-side, like faults.py

    flags: list[dict[str, Any]] = []
    for descriptor in descriptors:
        family = str(descriptor.get("family", ""))
        params = dict(descriptor.get("params", {}) or {})
        try:
            expected = _faultgen.expected_for(family, params)
        except Exception:
            continue
        if expected != "repair":
            continue
        case = beacon_case_for(descriptor, vendors)
        if case is None:
            continue
        vendor = str(descriptor["vendor"])
        try:
            faulted, baseline = _beacon_sync_pair(
                vendor=vendor,
                eval_seed_hex=eval_seed_hex,
                index=int(descriptor["index"]),
                spec=dict(case.spec),
            )
        except Exception:
            # Unjudgeable here (bad seed/index/spec) — the run's own
            # grading surfaces it; the sanity gate only flags silent no-ops.
            continue
        faulted_norm = json.dumps(faulted.get("canonical_records", {}), sort_keys=True, default=str)
        baseline_norm = json.dumps(baseline.get("canonical_records", {}), sort_keys=True, default=str)
        if faulted_norm != baseline_norm:
            continue
        try:
            diag = derive_beacon_diagnostics(
                dict(case.spec),
                descriptor=descriptor,
                eval_seed_hex=eval_seed_hex,
                vendor=vendor,
                connector_id=case.connector_id,
            )
            n_signals = len(diag.signals or ())
        except Exception:
            continue
        if n_signals == 0:
            flags.append(
                {
                    "index": int(descriptor["index"]),
                    "vendor": vendor,
                    "family": family,
                    "params": params,
                    "expected": expected,
                    "faulted_outcome": str(faulted.get("outcome", "")),
                }
            )
    return flags


def sync_candidate(
    candidate_spec: dict[str, Any],
    public_url: str,
    *,
    timeout_s: float = 30.0,
    max_total_delay_s: float = 120.0,
) -> dict[str, Any]:
    """Sync a candidate spec against the live beacon shape (in-process).

    Uses the real engine runtime (``execute_sync``) with an explicit
    vendor-delay budget (AR-2-1: a hostile ``Retry-After`` cannot stall the
    run). Returns the sync summary (outcome, canonical_records, ...).
    """
    from autobridge.engine.sync_run import execute_sync
    from autobridge.runtime.http import HttpClient
    from autobridge.spec.models import load_spec_from_dict

    spec_obj = load_spec_from_dict(candidate_spec)
    client = HttpClient(
        base_url=public_url,
        auth=spec_obj.source.auth,
        retry=spec_obj.source.rate_limit.retry,
        timeout_s=timeout_s,
        max_total_delay_s=max_total_delay_s,
    )
    return execute_sync(candidate_spec, client)


# ---------------------------------------------------------------------------
# Run (item 5)
# ---------------------------------------------------------------------------


@dataclass
class BeaconArmGrade:
    """One arm's graded outcome for one instance × trial (verifier-judged)."""

    arm: str
    instance_index: int
    trial: int
    vendor: str
    family: str
    verified_completion: int = 0
    wrong_patch_released: int = 0
    correct_escalation: int = 0
    frontier_tokens: int = 0
    small_tokens: int = 0
    # REQ-060 item 3 / REQ-062 item 5: the per-row token split the owner
    # picks a limit from (input / output / reasoning as the provider
    # reports them; 0 when unreported).
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float | None = None
    elapsed_ms: float = 0.0
    model: str | None = None
    records_verdict: str | None = None
    # REQ-062 item 4: the failed check names behind a records verdict, so
    # a row that counts a wrong patch names the patch (patch_summary)
    # and the checks it failed.
    records_failed_checks: tuple[str, ...] = ()
    patch_summary: str | None = None
    # REQ-062 item 1: which step blocked a Blocked row (detection with no
    # signals, ladder with no matching rule, verifier gate, ...), so a
    # non-attempt is a stated reason, never a silent 0.
    blocked_step: str | None = None
    grade_reason: str | None = None
    error: str | None = None
    executed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _mean_ci95(values: list[float]) -> dict[str, Any]:
    import math as _math

    n = len(values)
    if n == 0:
        return {"mean": 0.0, "ci95_lo": 0.0, "ci95_hi": 0.0, "n": 0}
    mean = statistics.fmean(values)
    if n == 1:
        return {"mean": mean, "ci95_lo": mean, "ci95_hi": mean, "n": 1}
    sd = statistics.pstdev(values)
    half = 1.96 * sd / _math.sqrt(n)
    return {"mean": mean, "ci95_lo": mean - half, "ci95_hi": mean + half, "n": n}


def _verifier_post(verifier_url: str, path: str, payload: dict[str, Any], timeout_s: float = 30.0) -> dict[str, Any]:
    import httpx as _httpx

    # Money is Decimal end to end: serialize with default=str (ints stay
    # ints; Decimal money becomes its exact string form, which the
    # verifier parses exactly).
    body = json.dumps(payload, default=str)
    try:
        resp = _httpx.post(
            verifier_url.rstrip("/") + path, content=body,
            headers={"Content-Type": "application/json"}, timeout=timeout_s,
        )
    except Exception as exc:
        raise BeaconError(f"verifier {path} unreachable: {type(exc).__name__}: {exc}") from exc
    if resp.status_code != 200:
        raise BeaconError(f"verifier {path}: HTTP {resp.status_code}: {resp.text[:300]}")
    try:
        data = resp.json()
    except Exception as exc:
        raise BeaconError(f"verifier {path}: invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise BeaconError(f"verifier {path}: unexpected response shape")
    return data


def run_instance_trial(
    protocol: BeaconProtocol,
    eval_seed_hex: str,
    descriptor: dict[str, Any],
    trial: int,
    *,
    config: Any,
    runners: dict[str, Any] | None = None,
    provider: Any | None = None,
    knowledge_warm: Any | None = None,
    verifier_url: str | None = None,
    vendors: list[BeaconVendor] | None = None,
    vendor_cfg: BeaconVendor | None = None,
    public_url: str = "",
) -> tuple[list[BeaconArmGrade], dict[str, Any]]:
    """Run all protocol arms on one beacon instance × trial; grade each.

    Engine arms run in-process (RepairController, ladder + budgets kept):
    they observe the instance by syncing the served shape (the same
    simulator serving code the live stack runs, deterministic from the
    public seed — see :func:`derive_beacon_diagnostics`), derive the
    drift class/delta/hypotheses from that observation, and climb. The
    candidate patch is applied to the case spec and synced with the real
    runtime against the live beacon shape; produced records + the reported
    outcome are judged by the verifier (``/beacon/records`` +
    ``/beacon/grade``). Frontier arms run through the shared REQ-024
    runners. No-spec arms are outcome-graded only. Never raises for arm
    failures (records the loss).
    """
    from bench.ablation import assert_all_inputs_equal as _assert_all_inputs_equal
    from bench.harness import make_trial_inputs as _make_inputs

    index = int(descriptor["index"])
    vendor = str(descriptor["vendor"])
    scenario_id = f"beacon:{index}"
    inputs = _make_inputs(config, scenario_id, trial)
    # Matched-budget self-test (plan §2.10): every arm receives the same
    # inputs object; a mismatch is a harness bug and fails loudly.
    _assert_all_inputs_equal({arm: inputs for arm in protocol.arms})
    case = beacon_case_for(descriptor, vendors)
    http_sync = bool(vendor_cfg.http) if vendor_cfg is not None else True
    if not public_url:
        try:
            public_url = public_urls(vendors).get(vendor, "")
        except BeaconError:
            public_url = ""
    grades: list[BeaconArmGrade] = []
    arm_reports: dict[str, Any] = {}

    runners = runners or {}
    prov = provider  # resolved by caller (shared frontier provider)

    for arm in protocol.arms:
        start = time.monotonic()
        grade = BeaconArmGrade(arm=arm, instance_index=index, trial=trial, vendor=vendor, family=str(descriptor["family"]))
        try:
            result = _run_arm(arm, inputs, case, runners, prov, knowledge_warm,
                              descriptor=descriptor, eval_seed_hex=eval_seed_hex)
        except Exception as exc:
            grade.error = f"{type(exc).__name__}: {exc}"[:500]
            grade.executed = True
            grades.append(grade)
            arm_reports[arm] = {"error": grade.error}
            continue
        elapsed_ms = (time.monotonic() - start) * 1000.0
        grade.elapsed_ms = elapsed_ms
        grade.frontier_tokens = int(getattr(result, "frontier_tokens", 0) or 0)
        # REQ-062 item 5: ArmResult.input_tokens carries the frontier
        # tier's input tokens for frontier arms (one-shot prompt +
        # completion) — only engine arms spend the small tier.
        from bench.ablation import ENGINE_ARMS as _ENGINE_ARMS

        if arm in _ENGINE_ARMS:
            grade.small_tokens = int(getattr(result, "input_tokens", 0) or 0)
        else:
            grade.small_tokens = 0
        grade.input_tokens = int(getattr(result, "input_tokens", 0) or 0)
        grade.output_tokens = int(getattr(result, "output_tokens", 0) or 0)
        grade.reasoning_tokens = int(getattr(result, "reasoning_tokens", 0) or 0)
        if case is None and arm in _ENGINE_ARMS:
            # No in-process engine spec for this vendor: the arm cannot
            # attempt the instance here. Recorded as a fail-closed loss
            # with the reason named (REQ-062 item 2 removes this path
            # for every beacon vendor, which all carry a spec now).
            grade.executed = False
        try:
            grade.cost_usd = float(result.cost_usd) if result.cost_usd is not None else None
        except (TypeError, ValueError):
            grade.cost_usd = None
        grade.model = getattr(result, "model", None)
        if not grade.error and getattr(result, "error", None):
            grade.error = str(result.error)[:500]
        arm_reports[arm] = _summarize_result(result)
        _grade_arm_result(grade, descriptor, result, eval_seed_hex, public_url, verifier_url,
                          http_sync=http_sync, vendors=vendors)
        grades.append(grade)
    return grades, arm_reports


def _run_arm(
    arm: str,
    inputs: Any,
    case: Any,
    runners: dict[str, Any],
    provider: Any,
    knowledge_warm: Any,
    *,
    descriptor: dict[str, Any] | None = None,
    eval_seed_hex: str | None = None,
) -> Any:
    """Dispatch one arm (custom runners win, for unit tests/offline runs).

    Engine arms observe the instance through :func:`derive_beacon_diagnostics`
    (public descriptor + public seed only — never instance parameters as
    diagnostics); ``derive=None`` (ablation default) is rejected loudly for
    beacon cases, since ``beacon:<index>`` is not a served sim scenario.

    REQ-062 item 6: frontier arms receive the same repair task as the
    engine — the shared :func:`beacon_bench_context` (pre-drift spec +
    faulted-sync evidence) rendered through the same file-based bench
    prompts — instead of the legacy whole-spec path.
    """
    from bench import ablation as _ablation

    if arm in runners and runners[arm] is not None:
        return runners[arm](inputs)
    if arm == "frontier_oneshot":
        ctx = None
        if case is not None and descriptor is not None and eval_seed_hex:
            try:
                ctx = beacon_bench_context(descriptor, case, inputs, eval_seed_hex)
            except Exception:
                ctx = None
        return _ablation.run_frontier_oneshot_arm(inputs, provider=provider, case=case, context=ctx)
    if arm == "frontier_agent":
        ctx = None
        if case is not None and descriptor is not None and eval_seed_hex:
            try:
                ctx = beacon_bench_context(descriptor, case, inputs, eval_seed_hex)
            except Exception:
                ctx = None
        return _ablation.run_frontier_agent_arm(inputs, case=case, context=ctx)
    if arm in ("engine_no_knowledge", "autobridge_cold", "autobridge_warm"):
        if case is None:
            # No in-process spec for this vendor: the engine cannot
            # propose a patch in-process; report Blocked without a patch and
            # let /beacon/grade judge the outcome class.
            from bench.harness import ArmResult as _ArmResult

            return _ArmResult(arm=arm, inputs=inputs, ran=False,  # type: ignore[arg-type]
                              error="no in-process engine spec for vendor (outcome-graded only)")
        if descriptor is None or eval_seed_hex is None:
            raise BeaconError(f"engine arm {arm!r} needs the public descriptor + eval seed to observe the instance")
        vendor = str(descriptor["vendor"])
        connector_id = str(getattr(case, "connector_id", ""))
        knowledge = knowledge_warm if arm == "autobridge_warm" else None
        # Observe once up front (the same observation the ladder climbs
        # from) so the row can name which step blocked (REQ-062 item 1).
        diag = derive_beacon_diagnostics(
            dict(case.spec),
            descriptor=descriptor,
            eval_seed_hex=eval_seed_hex,
            vendor=vendor,
            connector_id=connector_id,
        )

        def _derive(_spec: dict[str, Any], _case: Any, _inputs: Any) -> Any:
            return diag

        result = _ablation.run_engine_arm(inputs, case, ablation=cast("Any", arm),
                                          provider=provider, knowledge=knowledge, derive=_derive)
        try:
            import dataclasses as _dataclasses

            payload = dict(result.spec) if isinstance(result.spec, dict) else {}
            n_missing, n_added = _signal_wire_counts(getattr(diag, "signals", ()) or ())
            payload["beacon_diagnostics"] = {
                "drift_class": str(getattr(diag, "drift_class", "?")),
                "n_signals": len(getattr(diag, "signals", ()) or ()),
                "n_missing": n_missing,
                "n_added": n_added,
                "classification_reason": str(getattr(diag, "classification_reason", ""))[:300],
            }
            result = _dataclasses.replace(result, spec=payload)
        except Exception:
            pass
        return result
    raise BeaconError(f"unknown arm: {arm}")


def _signal_wire_counts(signals: Any) -> tuple[int, int]:
    """(n_missing, n_added) from the first wire-evidence signal (-1 when absent).

    Lets a Blocked row distinguish "detection saw nothing" from "the
    ladder saw the rename but matched no rule" (REQ-062 item 1).
    """
    for sig in signals or ():
        details = getattr(sig, "details", None)
        if isinstance(sig, dict):
            details = sig.get("details")
        if not isinstance(details, dict):
            continue
        if "missing" not in details and "added" not in details:
            continue
        missing = details.get("missing")
        added = details.get("added")
        return (
            len(missing) if isinstance(missing, list) else -1,
            len(added) if isinstance(added, list) else -1,
        )
    return (-1, -1)


def _summarize_result(result: Any) -> dict[str, Any]:
    from bench.ablation import ENGINE_ARMS as _ENGINE_ARMS

    spec = getattr(result, "spec", None)
    arm_name = getattr(result, "arm", None)
    return {
        "ran": bool(getattr(result, "ran", False)),
        "error": getattr(result, "error", None),
        "model": getattr(result, "model", None),
        "frontier_tokens": int(getattr(result, "frontier_tokens", 0) or 0),
        "small_tokens": int(getattr(result, "input_tokens", 0) or 0) if arm_name in _ENGINE_ARMS else 0,
        "has_patch": bool(isinstance(spec, dict) and spec.get("candidate_patch")),
        "has_spec": bool(isinstance(spec, dict) and len(spec) > 0),
        "decision": spec.get("decision") if isinstance(spec, dict) else None,
        "prompt": getattr(result, "prompt", None),
        "context_hash": getattr(result, "context_hash", None),
    }


def _candidate_spec_for(result: Any, case: Any) -> dict[str, Any] | None:
    """Candidate full spec from an arm result (engine patch or oneshot spec)."""
    spec = getattr(result, "spec", None)
    if not isinstance(spec, dict):
        return None
    patch = spec.get("candidate_patch")
    if patch:
        if case is None:
            return None
        from autobridge.spec.patch import apply_patch as _apply_patch

        candidate = copy.deepcopy(case.spec)
        try:
            return _apply_patch(candidate, list(patch))
        except Exception:
            return None
    # frontier_oneshot returns a full spec dict (no candidate_patch wrapper).
    if spec.get("operations") and spec.get("entities"):
        return dict(spec)
    return None


def _arm_outcome_for(result: Any, has_records: bool) -> dict[str, Any]:
    """Map an arm result to a /beacon/grade outcome report.

    Ladder decision vocabulary (``LadderResult.decision``): ``propose``
    with a non-empty patch (Complete + patch, records judged),
    ``propose`` with an empty patch (backoff-only/refresh, or no drift
    detected: Complete, no patch, no release), ``degraded`` (Degraded +
    one operator question), ``escalate`` (Blocked, carrying the ladder's
    question when it wrote one — REQ-063 item 2). A frontier escalation
    answer (``answer_kind`` ``escalate``, no ladder decision) is Blocked
    with its question recorded. Anything else (arm error, unparseable
    output) is Blocked — fail closed.
    """
    spec = getattr(result, "spec", None)
    decision = spec.get("decision") if isinstance(spec, dict) else None
    patch = spec.get("candidate_patch") if isinstance(spec, dict) else None
    answer_kind = spec.get("answer_kind") if isinstance(spec, dict) else None
    question = spec.get("question") if isinstance(spec, dict) else None
    has_question = isinstance(question, str) and bool(question.strip())
    if decision == "propose" and patch:
        return {"outcome": "Complete", "patch_reached_release": has_records, "operator_question": False}
    if decision == "propose":
        return {"outcome": "Complete", "patch_reached_release": False, "operator_question": False}
    if decision == "degraded":
        return {"outcome": "Degraded", "patch_reached_release": False, "operator_question": True}
    if decision == "escalate":
        return {"outcome": "Blocked", "patch_reached_release": False, "operator_question": has_question}
    if answer_kind == "escalate":
        return {"outcome": "Blocked", "patch_reached_release": False, "operator_question": True}
    if getattr(result, "ran", False) and isinstance(spec, dict) and len(spec) > 0 and not patch:
        # A produced full spec without a patch wrapper (oneshot frontier):
        # synced below when possible; the grade decides.
        return {"outcome": "Complete", "patch_reached_release": has_records, "operator_question": False}
    return {"outcome": "Blocked", "patch_reached_release": False, "operator_question": False}


def _patch_summary_for(result: Any, candidate: dict[str, Any] | None) -> str | None:
    """Bounded one-line summary of what the arm produced (REQ-062 item 4)."""
    spec = getattr(result, "spec", None)
    if not isinstance(spec, dict):
        return None
    patch = spec.get("candidate_patch")
    if isinstance(patch, list) and patch:
        ops = [f"{op.get('op', '?')} {op.get('path', '?')}" for op in patch[:3] if isinstance(op, dict)]
        more = f" +{len(patch) - 3} more" if len(patch) > 3 else ""
        return f"patch ({len(patch)} ops): " + ", ".join(ops) + more
    if spec.get("answer_kind") == "escalate":
        question = str(spec.get("question", ""))[:120]
        return f"escalation, no patch: {question}" if question else "escalation, no patch"
    if spec.get("decision") == "escalate":
        # REQ-063 item 2: an engine L4 escalation is also an escalation with
        # the ladder's question (or none, stated as such) — never silent.
        question = str(spec.get("question", ""))[:120]
        return f"escalation, no patch: {question}" if question else "escalation, no patch (the ladder wrote no question)"
    if candidate is not None:
        return "full spec (no patch wrapper)"
    if getattr(result, "outcome", None):
        return f"outcome label: {result.outcome}"
    return None


def _is_engine_shaped(result: Any) -> bool:
    """Engine-shaped result: an engine arm name or a ladder payload (REQ-062 item 1).

    Custom test runners build bare namespaces without ``arm``; the
    ladder payload (``decision`` + ``ladder_level_reached``, never an
    ``answer_kind``) still identifies them.
    """
    from bench.ablation import ENGINE_ARMS as _ENGINE_ARMS

    if getattr(result, "arm", None) in _ENGINE_ARMS:
        return True
    spec = getattr(result, "spec", None)
    return isinstance(spec, dict) and "decision" in spec and "ladder_level_reached" in spec


def _attempt_summary_for(result: Any, candidate: dict[str, Any] | None) -> str:
    """One-line attempt summary appended to every row's reason (REQ-062 items 1/4)."""
    _raw_spec = getattr(result, "spec", None)
    spec: dict[str, Any] = _raw_spec if isinstance(_raw_spec, dict) else {}
    if _is_engine_shaped(result):
        diag = spec.get("beacon_diagnostics", {}) if isinstance(spec.get("beacon_diagnostics"), dict) else {}
        drift = diag.get("drift_class", "?")
        n_signals = diag.get("n_signals", "?")
        try:
            n = int(n_signals)
        except (TypeError, ValueError):
            n = -1
        decision = spec.get("decision", "?")
        level = spec.get("ladder_level_reached", "?")
        rule = spec.get("rule_id") or "no rule"
        if n == 0 and decision == "propose" and not spec.get("candidate_patch"):
            # REQ-063 item 1: production opens no repair run here — the row
            # reads as "no drift detected", never as an escalation.
            return "engine: no drift detected (0 signals), no repair run opened (Complete, no patch)"
        if decision == "escalate":
            question = spec.get("question") if isinstance(spec.get("question"), str) else ""
            if question and question.strip():
                return f"engine: detected {drift} ({n_signals} signals) -> escalate at L{level} ({rule}) with operator question"
            return (
                f"engine: detected {drift} ({n_signals} signals) -> escalate at L{level} ({rule}); "
                "the ladder wrote no question"
            )
        return f"engine: detected {drift} ({n_signals} signals) -> {decision} at L{level} ({rule})"
    patch = spec.get("candidate_patch") if spec else None
    if isinstance(patch, list) and patch:
        return f"frontier: produced patch (applies: {candidate is not None})"
    if spec.get("answer_kind") == "escalate":
        return "frontier: escalated with a question (no patch)"
    if candidate is not None:
        return "frontier: produced full spec (applies: True)"
    outcome_label = getattr(result, "outcome", None)
    if outcome_label:
        return f"frontier: no patch or escalation ({outcome_label})"
    if getattr(result, "ran", False):
        return "frontier: ran with no gradable patch or escalation"
    return "frontier: no attempt (arm error)"


def _blocked_step_for(result: Any, outcome_report: dict[str, Any]) -> str | None:
    """Which step blocked a Blocked row, and why (REQ-062 item 1)."""
    if outcome_report.get("outcome") != "Blocked":
        return None
    _raw_spec = getattr(result, "spec", None)
    spec: dict[str, Any] = _raw_spec if isinstance(_raw_spec, dict) else {}
    if getattr(result, "error", None) and not getattr(result, "ran", False) and not spec:
        return f"setup: arm errored before attempting ({str(result.error)[:120]})"
    if _is_engine_shaped(result):
        diag = spec.get("beacon_diagnostics", {}) if isinstance(spec.get("beacon_diagnostics"), dict) else {}
        n_signals = diag.get("n_signals", "?")
        drift = diag.get("drift_class", "?")
        try:
            n = int(n_signals)
        except (TypeError, ValueError):
            n = -1
        if n == 0:
            return "detection: no drift signals (the faulted sync looked Complete, nothing observed)"
        n_missing = diag.get("n_missing", -1)
        try:
            n_missing = int(n_missing)
        except (TypeError, ValueError):
            n_missing = -1
        decision = spec.get("decision", "?")
        level = spec.get("ladder_level_reached", "?")
        rule = spec.get("rule_id")
        if n_missing == 0:
            return "detection: announced/served shape breaks no mapping (missing: none) — nothing to repair"
        if decision == "escalate" and not rule:
            base = f"ladder: no rule matched {drift} at L{level}, escalated without a patch"
        else:
            base = f"ladder: escalated at L{level} (rule: {rule or 'none'})"
        # REQ-063 item 2: say whether the escalation carries the operator question.
        question = spec.get("question") if isinstance(spec.get("question"), str) else ""
        if decision == "escalate":
            if question and question.strip():
                return base + " (operator question carried)"
            return base + " (the ladder wrote no question)"
        return base
    if spec.get("answer_kind") == "escalate":
        return "attempt: escalated with a question instead of a patch"
    outcome_label = getattr(result, "outcome", None)
    if outcome_label:
        return f"attempt: no patch or escalation produced ({outcome_label})"
    return "attempt: no gradable output produced"


def _grade_arm_result(
    grade: BeaconArmGrade,
    descriptor: dict[str, Any],
    result: Any,
    eval_seed_hex: str,
    public_url: str,
    verifier_url: str | None,
    *,
    http_sync: bool = True,
    vendors: list[BeaconVendor] | None = None,
) -> None:
    """Judge one arm result via the verifier (never raises; records the loss).

    REQ-062 item 4: ``wrong_patch_released`` counts only rows whose patch
    reached a release (records synced *and* passing) and then failed the
    outcome grade — each such row names the patch
    (:attr:`BeaconArmGrade.patch_summary`) and the failed verifier
    checks (:attr:`BeaconArmGrade.records_failed_checks`), so the
    summary total and the rows agree. A claimed Complete whose records
    fail (or never sync) is a failed attempt, not a release.
    """
    if verifier_url is None:
        grade.error = "no verifier URL (grade skipped, counts as loss)"
        return
    index = int(descriptor["index"])
    vendor = str(descriptor["vendor"])
    case = beacon_case_for(descriptor, vendors)
    candidate = _candidate_spec_for(result, case)
    records: dict[str, list[dict[str, Any]]] = {"reservations": [], "folios": []}
    records_verdict: str | None = None
    records_failed: list[str] = []
    synced = False
    if candidate is not None and http_sync and public_url:
        try:
            summary = sync_candidate(candidate, public_url)
            records = canonical_to_records(summary.get("canonical_records", {}))
            synced = True
        except Exception as exc:
            grade.error = f"candidate sync failed: {type(exc).__name__}: {exc}"[:500]
    elif candidate is not None and not http_sync and vendor == "fias-pabx":
        # Framed transport has no live HTTP sync: verify the patched spec
        # in-process over the announced feed layout (same serving code the
        # live edge agent runs). A Complete feed sync backs the release
        # claim; anything else is a failed attempt, not a release.
        try:
            from bench.faults import beacon_sync_pair as _beacon_sync_pair

            faulted_summary, _baseline_summary = _beacon_sync_pair(
                vendor=vendor, eval_seed_hex=eval_seed_hex, index=index, spec=candidate
            )
            synced = True
            if faulted_summary.get("outcome") == "Complete":
                records_verdict = "pass"
            else:
                records_verdict = "fail"
                unmet = faulted_summary.get("unmet_criteria") or ["sync_not_complete"]
                records_failed = [str(unmet[0])[:120]]
        except Exception as exc:
            grade.error = f"fias candidate verify failed: {type(exc).__name__}: {exc}"[:500]
    if synced and records_verdict is None:
        try:
            judged = _verifier_post(
                verifier_url,
                "/beacon/records",
                {"eval_seed_hex": eval_seed_hex, "index": index,
                 "records": records, "spec": candidate},
            )
            records_verdict = str(judged.get("verdict", "fail"))
            failed = judged.get("failed_checks", [])
            if isinstance(failed, list):
                records_failed = [str(f.get("name", ""))[:120] for f in failed if isinstance(f, dict) and f.get("name")]
        except BeaconError as exc:
            grade.error = f"records grade failed: {exc}"[:500]
            records_verdict = "fail"
        grade.records_verdict = records_verdict
    elif not synced:
        grade.records_verdict = None
    else:
        grade.records_verdict = records_verdict
    grade.records_failed_checks = tuple(records_failed)
    grade.patch_summary = _patch_summary_for(result, candidate)
    outcome_report = _arm_outcome_for(result, synced and records_verdict == "pass")
    # Only a patch that reached a release (records verified) and then
    # failed the outcome grade counts as a wrong patch released.
    grade.blocked_step = _blocked_step_for(result, outcome_report)
    try:
        graded = _verifier_post(
            verifier_url,
            "/beacon/grade",
            {"eval_seed_hex": eval_seed_hex, "index": index, **outcome_report},
        )
    except BeaconError as exc:
        grade.error = f"outcome grade failed: {exc}"[:500]
        return
    reason = str(graded.get("reason", ""))
    attempt = _attempt_summary_for(result, candidate)
    grade.grade_reason = f"{reason} | {attempt}" if reason else attempt
    passed = bool(graded.get("passed", False))
    records_ok = (not synced) or records_verdict == "pass"
    if passed and records_ok:
        grade.verified_completion = 1
        # correct_escalation: passed without a patch on a non-repair instance
        # is exactly escalating when the instance says so (mirror ablation).
        if not outcome_report["patch_reached_release"]:
            grade.correct_escalation = 1
    elif passed and not records_ok:
        grade.correct_escalation = 0
    if outcome_report["outcome"] == "Complete" and outcome_report["patch_reached_release"] and not passed:
        grade.wrong_patch_released = 1


def _placeholder_credential_env() -> None:
    """Ensure the bench credential refs resolve (non-secret placeholder)."""
    for ref in ("ATLAS_API_KEY", "HARBOR_API_KEY", "BENCH_API_KEY"):
        os.environ.setdefault(ref, BEACON_BENCH_CREDENTIAL)


@dataclass
class BeaconRunResult:
    """Everything ``make bench-beacon`` writes for one round."""

    protocol: BeaconProtocol
    eval_seed_hex: str
    randomness_hex: str
    instances: list[dict[str, Any]] = field(default_factory=list)
    grades: list[BeaconArmGrade] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "round": self.protocol.target_round,
            "rehearsal": self.protocol.rehearsal,
            "eval_seed": self.eval_seed_hex,
            "randomness": self.randomness_hex,
            "faults": self.protocol.faults,
            "trials": self.protocol.trials,
            "arms": list(self.protocol.arms),
            "grades": self.summary,
        }


def _min_med_max(values: list[float]) -> dict[str, Any]:
    import statistics as _statistics

    if not values:
        return {"min": 0, "median": 0, "max": 0, "n": 0}
    ordered = sorted(values)
    return {
        "min": ordered[0],
        "median": float(_statistics.median(ordered)),
        "max": ordered[-1],
        "n": len(ordered),
    }


def summarize_grades(grades: list[BeaconArmGrade]) -> dict[str, Any]:
    """Aggregate grades per arm, per (arm, instance) and overall (mean+CI95).

    REQ-060 item 3 / REQ-062 item 5: per-row input / output / reasoning
    tokens are recorded so the owner can pick a limit from the results;
    the summary adds per-arm min / median / max total tokens.
    """
    arms = sorted({g.arm for g in grades})

    def block(items: list[BeaconArmGrade]) -> dict[str, Any]:
        def vals(fn: Any) -> list[float]:
            return [float(fn(g)) for g in items]

        totals = vals(lambda g: g.input_tokens + g.output_tokens)
        return {
            "n": len(items),
            "verified_completion": _mean_ci95(vals(lambda g: g.verified_completion)),
            "wrong_patch_released": {"total": int(sum(g.wrong_patch_released for g in items))},
            "correct_escalation": _mean_ci95(vals(lambda g: g.correct_escalation)),
            "frontier_tokens": _mean_ci95(vals(lambda g: g.frontier_tokens)),
            "small_tokens": _mean_ci95(vals(lambda g: g.small_tokens)),
            "input_tokens": _mean_ci95(vals(lambda g: g.input_tokens)),
            "output_tokens": _mean_ci95(vals(lambda g: g.output_tokens)),
            "reasoning_tokens": _mean_ci95(vals(lambda g: g.reasoning_tokens)),
            "total_tokens": {**_mean_ci95(totals), **_min_med_max(totals)},
            "cost_usd": _mean_ci95([g.cost_usd or 0.0 for g in items]),
            "elapsed_ms": _mean_ci95(vals(lambda g: g.elapsed_ms)),
        }

    by_arm = {arm: block([g for g in grades if g.arm == arm]) for arm in arms}
    return {"overall": block(grades), "by_arm": by_arm}


def run_beacon(
    protocol: BeaconProtocol,
    *,
    config: Any = None,
    runners: dict[str, Any] | None = None,
    provider: Any | None = None,
    knowledge_warm: Any | None = None,
    verifier_url: str | None = None,
    base_url: str = beacon_client.DRAND_DEFAULT_BASE_URL,
    output_dir: str | pathlib.Path | None = None,
) -> BeaconRunResult:
    """Run the frozen protocol (item 5). Fail closed, no re-rolls.

    Refuses when the round is not yet published or a result dir for it
    already exists. Prints the public evidence (round, time, randomness,
    verification URL, freeze commit) to stdout.

    REQ-062 item 6: the beacon run uses the same arms, prompts, context
    and 200k ceiling as the dev ablation — the default
    :class:`HarnessConfig` (200k ceiling, 600 s wall clock, file-based
    bench prompts loaded once here), overridable via ``BENCH_TOKEN_CEILING``
    / ``BENCH_WALL_CLOCK`` like the dev harness.

    REQ-066: the pinned-model gate runs first — a model mismatch (or a
    missing pin on a real run) refuses before any network or arm spend.
    """
    require_pinned_model(protocol)
    from bench.bench_prompts import ensure_bench_prompts_loaded as _ensure_prompts

    out_dir = pathlib.Path(output_dir) if output_dir else BEACON_DIR / str(protocol.target_round)
    # No re-rolls: refuse when result files already exist. A directory that
    # holds only protocol.md (rehearsal freeze, then run) is fine.
    if (out_dir / "instances.json").exists() or (out_dir / "summary.json").exists() or (out_dir / "grades.jsonl").exists():
        raise BeaconError(f"refusing run: {out_dir} already holds results (no re-rolls)")
    info = beacon_client.fetch_chain_info(base_url)
    try:
        latest = beacon_client.fetch_latest(base_url)
        latest_round = int(latest.get("round", 0))
    except BeaconError as exc:
        raise BeaconError(f"cannot determine latest round: {exc}") from exc
    if latest_round < protocol.target_round:
        raise BeaconError(
            f"round {protocol.target_round} not yet published (latest {latest_round}); refusing run"
        )
    verified = beacon_client.fetch_verified_round(base_url, protocol.target_round, chain_info=info)
    eval_seed_hex = verified.eval_seed_hex
    print(f"beacon run: {beacon_client.evidence_line(info, verified)}", flush=True)
    print(f"beacon run: freeze engine={protocol.engine_commit[:12]} generator={protocol.generator_commit[:12]} "
          f"frozen_at={protocol.frozen_at_utc} rehearsal={protocol.rehearsal} "
          f"frontier-model={protocol.frontier_model or 'unpinned'}"
          + (f" agent-model={protocol.agent_model}" if protocol.agent_model else ""), flush=True)
    _placeholder_credential_env()
    _ensure_prompts()
    cfg = config if config is not None else default_beacon_config()
    print(f"beacon run: token_ceiling={cfg.token_ceiling} wall_clock={cfg.wall_clock_s}s", flush=True)
    vendors = load_vendors()
    admin = admin_urls(vendors)
    public = public_urls(vendors)
    result = BeaconRunResult(protocol=protocol, eval_seed_hex=eval_seed_hex, randomness_hex=verified.randomness_hex)
    for index in range(protocol.faults):
        vendor_cfg = vendors[index % len(vendors)]
        descriptor = push_beacon(eval_seed_hex, index, vendor_cfg.vendor, admin_url=admin.get(vendor_cfg.vendor))
        result.instances.append(descriptor)
        print(f"beacon run: instance {index} vendor={vendor_cfg.vendor} family={descriptor['family']}", flush=True)
        # REQ-063 item 3: fail loudly before spending any arm budget on an
        # instance the connector cannot distinguish from the baseline.
        sanity_flags = sanity_unchanged_repairs(eval_seed_hex, [descriptor], vendors)
        if sanity_flags:
            flag = sanity_flags[0]
            raise BeaconError(
                "beacon sanity: instance "
                f"{flag['index']} ({flag['vendor']}/{flag['family']} {flag['params']}) "
                f"expects {flag['expected']} but its faulted canonical output equals "
                "the baseline under the pre-drift spec — generator bug, refusing run"
            )
        for trial in range(protocol.trials):
            grades, _reports = run_instance_trial(
                protocol, eval_seed_hex, descriptor, trial,
                config=cfg, runners=runners, provider=provider,
                knowledge_warm=knowledge_warm, verifier_url=verifier_url,
                vendors=vendors, vendor_cfg=vendor_cfg, public_url=public.get(vendor_cfg.vendor, ""),
            )
            result.grades.extend(grades)
    result.summary = summarize_grades(result.grades)
    _write_results(result, out_dir)
    return result


def _write_results(result: BeaconRunResult, out_dir: pathlib.Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "instances.json").write_text(
        json.dumps(result.instances, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (out_dir / "grades.jsonl").write_text(
        "".join(json.dumps(g.to_dict(), sort_keys=True, default=str) + "\n" for g in result.grades),
        encoding="utf-8")
    (out_dir / "summary.json").write_text(
        json.dumps(result.to_summary_dict(), sort_keys=True, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"beacon run: wrote {out_dir} "
          f"({len(result.instances)} instances, {len(result.grades)} grades)", flush=True)


def _print_pinned_model(out_dir: pathlib.Path) -> None:
    """Print the freeze's pinned frontier model for a results dir (REQ-066).

    Reads the protocol next to the results (rehearsals), else the frozen
    ``experiments/beacon/protocol.md``. A protocol without the line
    (pre-REQ-066) is reported as unpinned, never an error here.
    """
    candidates = [out_dir / "protocol.md"]
    if BEACON_DIR / "protocol.md" not in candidates:
        candidates.append(BEACON_DIR / "protocol.md")
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            protocol = parse_protocol(candidate)
        except BeaconError:
            continue
        if protocol.frontier_model:
            extra = f" agent-model={protocol.agent_model}" if protocol.agent_model else ""
            print(f"beacon verify: pinned frontier-model={protocol.frontier_model}{extra}", flush=True)
        else:
            print("beacon verify: no pinned frontier-model in protocol (pre-REQ-066 freeze)", flush=True)
        return
    print("beacon verify: no protocol found; pinned frontier-model unknown", flush=True)


def verify_repro(
    round: int,
    *,
    base_url: str = beacon_client.DRAND_DEFAULT_BASE_URL,
    output_dir: str | pathlib.Path | None = None,
) -> bool:
    """Anyone re-fetches + verifies the round and checks published instances.

    Regenerates the public descriptors from the verified seed and compares
    them against ``experiments/beacon/<round>/instances.json`` (params
    equality). Returns True on match; raises :class:`BeaconError` on any
    mismatch (fail closed).

    REQ-066: also shows the freeze's pinned frontier model (read from the
    protocol next to the results, else the frozen ``protocol.md``).
    """
    out_dir = pathlib.Path(output_dir) if output_dir else BEACON_DIR / str(round)
    published_path = out_dir / "instances.json"
    if not published_path.is_file():
        raise BeaconError(f"no published instances at {published_path}")
    _print_pinned_model(out_dir)
    published = json.loads(published_path.read_text(encoding="utf-8"))
    if not isinstance(published, list) or not published:
        raise BeaconError(f"published instances at {published_path} are not a non-empty list")
    info = beacon_client.fetch_chain_info(base_url)
    verified = beacon_client.fetch_verified_round(base_url, round, chain_info=info)
    print(f"beacon verify: {beacon_client.evidence_line(info, verified)}", flush=True)
    for entry in published:
        if not isinstance(entry, dict):
            raise BeaconError("published instances contain a non-object entry")
        for key in ("index", "vendor", "family", "params"):
            if key not in entry:
                raise BeaconError(f"published instance missing {key!r}")
    # Regeneration equality goes through the same admin push path the run
    # used (the services generate internally from the seed; the harness
    # never sees generator internals). Re-pushing each published instance
    # to its vendor sim and comparing the returned public descriptor proves
    # the published set regenerates byte-identically (needs the stack).
    try:
        admin = admin_urls()
        for entry in published:
            vendor = str(entry["vendor"])
            url = admin.get(vendor)
            if not url:
                raise BeaconError(f"no admin URL for vendor {vendor!r}")
            descriptor = push_beacon(verified.eval_seed_hex, int(entry["index"]), vendor, admin_url=url)
            if descriptor != {k: entry[k] for k in ("index", "vendor", "family", "params")}:
                raise BeaconError(f"instance {entry['index']} mismatch on regeneration")
    except BeaconError:
        raise
    except Exception as exc:
        raise BeaconError(f"reproduce needs the stack ({type(exc).__name__}: {exc})") from exc
    print(f"beacon verify: {len(published)} instances match the published set", flush=True)
    return True


def _cli_config(token_ceiling: int | None, wall_clock: float | None) -> Any | None:
    """HarnessConfig from CLI flags (None when neither is given → env/defaults).

    Explicit flags compose with the env overrides in :func:`run_beacon`
    (env wins, mirroring the dev harness): a flag only fills a field the
    env leaves unset.
    """
    if token_ceiling is None and wall_clock is None:
        return None
    import os as _os

    from bench.harness import HarnessConfig as _HarnessConfig

    kwargs: dict[str, Any] = {}
    if token_ceiling is not None and not _os.environ.get("BENCH_TOKEN_CEILING", "").strip():
        kwargs["token_ceiling"] = token_ceiling
    if wall_clock is not None and not _os.environ.get("BENCH_WALL_CLOCK", "").strip():
        kwargs["wall_clock_s"] = wall_clock
    return _HarnessConfig(**kwargs) if kwargs else None


def default_beacon_config() -> Any:
    """Default run config: the dev ablation's 200k ceiling, env-overridable.

    REQ-062 item 6: beacon runs use the same 200k token ceiling and 600 s
    wall clock as the dev ablation (``HarnessConfig`` defaults);
    ``BENCH_TOKEN_CEILING`` (``0`` = no limit, mirroring the dev bench)
    and ``BENCH_WALL_CLOCK`` override them.
    """
    from bench.harness import HarnessConfig as _HarnessConfig

    kwargs: dict[str, Any] = {}
    env_ceiling = os.environ.get("BENCH_TOKEN_CEILING", "").strip()
    if env_ceiling:
        try:
            kwargs["token_ceiling"] = int(env_ceiling)
        except ValueError as exc:
            raise BeaconError(f"BENCH_TOKEN_CEILING must be an int: {exc}") from exc
    env_wall = os.environ.get("BENCH_WALL_CLOCK", "").strip()
    if env_wall:
        try:
            kwargs["wall_clock_s"] = float(env_wall)
        except ValueError as exc:
            raise BeaconError(f"BENCH_WALL_CLOCK must be a number: {exc}") from exc
    return _HarnessConfig(**kwargs)


def main(argv: list[str] | None = None) -> int:
    """CLI: ``freeze`` / ``run`` / ``verify-repro`` (wired to make targets)."""
    import argparse

    parser = argparse.ArgumentParser(description="Beacon evaluation runs (REQ-025)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_freeze = sub.add_parser("freeze", help="freeze the eval protocol (item 4)")
    p_freeze.add_argument("--round-after", required=True, help="ISO UTC (first round at/after it is targeted)")
    p_freeze.add_argument("--faults", type=int, required=True)
    p_freeze.add_argument("--trials", type=int, default=5)
    p_freeze.add_argument("--arms", default=",".join(ABLATION_ARMS))
    p_freeze.add_argument("--rehearsal", action="store_true", help="label this a rehearsal (past rounds allowed)")
    p_freeze.add_argument("--base-url", default=beacon_client.DRAND_DEFAULT_BASE_URL)
    p_run = sub.add_parser("run", help="run the frozen protocol (item 5)")
    p_run.add_argument("--protocol", default=str(PROTOCOL_PATH))
    p_run.add_argument("--verifier-url", default=None)
    p_run.add_argument("--base-url", default=beacon_client.DRAND_DEFAULT_BASE_URL)
    p_run.add_argument("--output-dir", default=None)
    p_run.add_argument("--token-ceiling", type=int, default=None,
                       help="shared token ceiling (default 200000; 0 = no limit; BENCH_TOKEN_CEILING wins)")
    p_run.add_argument("--wall-clock", type=float, default=None,
                       help="shared wall-clock seconds (default 600; BENCH_WALL_CLOCK wins)")
    p_verify = sub.add_parser("verify-repro", help="reproduce + check published instances (item 7)")
    p_verify.add_argument("--round", type=int, required=True)
    p_verify.add_argument("--base-url", default=beacon_client.DRAND_DEFAULT_BASE_URL)
    p_verify.add_argument("--output-dir", default=None)
    p_rehearse = sub.add_parser("rehearse", help="rehearsal on a past round (item 6, labelled, not committed)")
    p_rehearse.add_argument("--round", type=int, required=True, help="already-published round")
    p_rehearse.add_argument("--faults", type=int, required=True)
    p_rehearse.add_argument("--trials", type=int, default=1)
    p_rehearse.add_argument("--arms", default=",".join(ABLATION_ARMS))
    p_rehearse.add_argument("--verifier-url", default=None)
    p_rehearse.add_argument("--base-url", default=beacon_client.DRAND_DEFAULT_BASE_URL)
    p_rehearse.add_argument("--token-ceiling", type=int, default=None,
                            help="shared token ceiling (default 200000; 0 = no limit; BENCH_TOKEN_CEILING wins)")
    p_rehearse.add_argument("--wall-clock", type=float, default=None,
                            help="shared wall-clock seconds (default 600; BENCH_WALL_CLOCK wins)")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "freeze":
            path = freeze(
                args.round_after, args.faults, trials=args.trials,
                arms=tuple(a.strip() for a in args.arms.split(",") if a.strip()),
                base_url=args.base_url, rehearsal=args.rehearsal,
            )
            print(f"beacon freeze: wrote {path}", flush=True)
        elif args.cmd == "run":
            protocol = parse_protocol(args.protocol)
            run_beacon(protocol, verifier_url=args.verifier_url, base_url=args.base_url,
                       output_dir=args.output_dir,
                       config=_cli_config(args.token_ceiling, args.wall_clock))
        elif args.cmd == "rehearse":
            info = beacon_client.fetch_chain_info(args.base_url)
            pub_epoch = beacon_client.round_time_utc(
                int(info["genesis_time"]), int(info["period"]), args.round)
            round_after = _dt.datetime.fromtimestamp(pub_epoch, tz=_dt.UTC).isoformat()
            rehearsal_dir = BEACON_DIR / "rehearsals" / str(args.round)
            protocol_path = rehearsal_dir / "protocol.md"
            if not protocol_path.exists():
                freeze(round_after, args.faults, trials=args.trials,
                       arms=tuple(a.strip() for a in args.arms.split(",") if a.strip()),
                       base_url=args.base_url, rehearsal=True, protocol_path=protocol_path)
                print(f"beacon rehearse: froze {protocol_path}", flush=True)
            else:
                print(f"beacon rehearse: reusing {protocol_path}", flush=True)
            protocol = parse_protocol(protocol_path)
            if protocol.target_round != args.round:
                raise BeaconError(
                    f"rehearsal protocol targets round {protocol.target_round}, want {args.round}")
            run_beacon(protocol, verifier_url=args.verifier_url, base_url=args.base_url,
                       output_dir=rehearsal_dir,
                       config=_cli_config(args.token_ceiling, args.wall_clock))
        else:
            verify_repro(args.round, base_url=args.base_url, output_dir=args.output_dir)
    except BeaconError as exc:
        print(f"beacon {args.cmd} failed: {exc}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
