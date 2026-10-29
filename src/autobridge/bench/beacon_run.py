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
``verifier`` or ``simulators`` and never sees generator internals or
answer keys: it passes only the eval seed (public beacon randomness) to
the services over HTTP and reads ``simulators/scenarios``-style public
files (reference specs, vendor docs) from the filesystem. Grading happens
server-side; the harness only combines the two endpoint verdicts.

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

from autobridge.bench import beacon_client
from autobridge.bench.beacon_client import BeaconError

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
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
    transports: no live sync, outcome-graded only.
    """

    vendor: str
    http: bool
    admin_url: str
    public_url: str
    spec: dict[str, str] | None = None


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
    frozen_at = now.isoformat()
    body = f"""# Beacon evaluation protocol (REQ-025 item 4)

One run, no retries, results published whatever they are. Frozen before the
target round exists: the instances (fault parameters per seed) could not be
known at freeze time. Fault *families* are known by design — that is the
engine's everyday knowledge; the honest-scope note in docs/benchmark.md
applies.

- beacon-round-after-utc: {when.isoformat()}
- beacon-target-round: {target_round}
- beacon-faults: {faults}
- beacon-trials: {trials}
- beacon-arms: {", ".join(arms)}
- beacon-engine-commit: {engine_commit}
- beacon-generator-commit: {generator_commit}
- beacon-frozen-at-utc: {frozen_at}
- beacon-rehearsal: {"true" if rehearsal else "false"}
- beacon-chain: {beacon_client.LOE_MAINNET_CHAIN_HASH} scheme={beacon_client.LOE_SCHEME_ID}
- beacon-verify-url: {base_url.rstrip("/")}/public/{target_round}

Metrics per arm, scenario and overall (mean + 95% interval over trials):
verified completion rate (judged only by the verifier), wrong patches that
reached a release (must be 0 for AutoBridge), correct escalations
(escalated exactly when the instance says so), frontier tokens, estimated
cost, wall time.

Caveat (REQ-024 owner check on f32b4b4b, open at freeze time): the engine
arms run in-process from descriptor-built repair inputs (drift class,
fingerprint delta, hypotheses) — a pre-diagnosed state — while the
frontier arms start from docs and the live sandbox. Deltas carry this
caveat until the real end-to-end engine path lands in ablation.py.
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


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


#: Beacon family → canonical drift class (plan §2.5 taxonomy).
DRIFT_CLASS_FOR_FAMILY: dict[str, str] = {
    "rename": "SCHEMA_RENAME",
    "nesting": "SCHEMA_RENAME",
    "pagination": "PAGINATION_CHANGE",
    "code_list": "ENUM_NEW_VALUE",
    "semantic": "SEMANTIC_VALUE_DRIFT",
    "dates": "SEMANTIC_VALUE_DRIFT",
    "transport_auth": "TRANSIENT",
    "fias_layout": "SCHEMA_RENAME",
    "fias_link": "TRANSIENT",
}

#: transport_auth kind → canonical drift class override.
DRIFT_CLASS_FOR_TRANSPORT_KIND: dict[str, str] = {
    "throttle": "RATE_LIMIT",
    "retry_after": "RATE_LIMIT",
    "outage": "TRANSIENT",
    "token_expired": "AUTH_EXPIRED",
    "token_revoked": "AUTH_REVOKED",
    "endpoint_removed": "CAPABILITY_REMOVED",
}

def engine_spec_for(descriptor: dict[str, Any], vendors: list[BeaconVendor] | None = None) -> dict[str, Any] | None:
    """Reference/docs spec for the descriptor's vendor (None when none exists).

    Filesystem sources only (generated reference specs, vendor docs — the
    same sanctioned bench exception as ``ablation_case_for_scenario``);
    the spec source rides the vendor table, never engine code.
    Non-HTTP vendors have no connector spec: engine arms cannot propose an
    in-process patch there (outcome-graded only, documented).
    """
    from autobridge.bench import ablation as _ablation

    vendor = str(descriptor["vendor"])
    source: dict[str, str] | None = None
    for entry in vendors if vendors is not None else load_vendors():
        if entry.vendor == vendor:
            source = entry.spec
            break
    if source is None:
        return None
    try:
        if source["kind"] == "generated":
            return _ablation._load_reference_spec(source["connector"])
        return _ablation._docs_spec(source["path"], source["connector"], "v2")
    except Exception:
        return None


def beacon_case_for(descriptor: dict[str, Any], vendors: list[BeaconVendor] | None = None) -> Any | None:
    """Build engine-arm repair inputs from a public beacon descriptor.

    Returns an :class:`ablation.AblationCase` (spec, drift class,
    fingerprint delta, extra, hypotheses) built from filesystem sources +
    descriptor fields only — never from generator internals or answer keys.
    ``None`` when the vendor has no in-process spec (FIAS): the arms are
    outcome-graded only for those instances.
    """
    from autobridge.bench import ablation as _ablation
    from autobridge.drift.fingerprint import FingerprintDelta as _FD

    family = str(descriptor["family"])
    params = dict(descriptor.get("params", {}))
    index = int(descriptor["index"])
    spec = engine_spec_for(descriptor, vendors)
    if spec is None:
        return None

    def fd(**kwargs: Any) -> Any:
        base: dict[str, Any] = dict(
            fingerprint_changed=True,
            pagination_changed=False,
            keys_changed=False,
            types_changed=False,
            changed_entities=[],
            pagination_details={},
            keys_details={},
            types_details={},
        )
        base.update(kwargs)
        return _FD(**base)

    drift_class = DRIFT_CLASS_FOR_FAMILY.get(family, "UNKNOWN")
    extra: dict[str, Any] = {}
    hypotheses: tuple[Any, ...] = ()
    if family == "rename":
        entity = "Reservation" if str(params.get("entity")) == "reservation" else "Folio"
        new_name = str(params.get("new_name", ""))
        field = str(params.get("field", ""))
        delta = fd(types_changed=True, changed_entities=[entity],
                   types_details={entity: {"previous": {field: "str"}, "current": {new_name: "str"}}})
        extra = {"new_path": f"$.{new_name}"}
    elif family == "nesting":
        entity = "Reservation" if str(params.get("entity")) == "reservation" else "Folio"
        delta = fd(types_changed=True, changed_entities=[entity])
    elif family == "pagination":
        strategy = str(params.get("strategy", ""))
        delta = fd(pagination_changed=True, changed_entities=["Reservation"],
                   pagination_details={"list_reservations": {"previous": {}, "current": {"strategy": strategy}}})
        extra = {"target_strategy": strategy}
    elif family == "code_list":
        delta = fd(types_changed=True, changed_entities=["Reservation"])
    elif family == "semantic":
        delta = fd(changed_entities=["Reservation"])
        hypotheses = _semantic_hypotheses(params)
    elif family == "dates":
        delta = fd(changed_entities=["Reservation", "Folio"])
    elif family == "transport_auth":
        drift_class = DRIFT_CLASS_FOR_TRANSPORT_KIND.get(str(params.get("kind", "")), "TRANSIENT")
        delta = fd()
    elif family in ("fias_layout", "fias_link"):
        delta = fd()
    else:
        return None
    return _ablation.AblationCase(
        scenario_id=f"beacon:{index}",
        spec=spec,
        drift_class=drift_class,
        fingerprint_delta=delta,
        extra=extra,
        hypotheses=hypotheses,
    )


def _semantic_hypotheses(params: dict[str, Any]) -> tuple[Any, ...]:
    """Amount hypotheses for a semantic instance (L2 ranks, never guesses)."""
    from autobridge.discovery.docs_reader import Provenance as _Provenance
    from autobridge.discovery.hypotheses import Hypothesis as _Hypothesis

    kind = str(params.get("kind", ""))
    if kind == "decoy":
        return ()
    decoy = str(params.get("decoy_field", "net_amount")) if kind == "decoy" else "net_amount"
    prov = (_Provenance(source="beacon:semantic", line=1, snippet="served amount scale ambiguous"),)
    gross = str(params.get("gross_field", "total_amount"))
    return (
        _Hypothesis(id="gross_is_canonical", family="amount",
                    claim=f"{gross} must stay gross",
                    candidate_mapping_fragment={"family": "amount", "field": gross},
                    prior=0.41, evidence_for=prov),
        _Hypothesis(id="scaled_is_canonical", family="amount",
                    claim=f"served {decoy} tracks canonical",
                    candidate_mapping_fragment={"family": "amount", "field": decoy},
                    prior=0.39, evidence_for=prov),
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
    cost_usd: float | None = None
    elapsed_ms: float = 0.0
    model: str | None = None
    records_verdict: str | None = None
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

    Engine arms run in-process (RepairController, ladder + budgets kept)
    from cases built out of the public descriptor; the candidate patch is
    applied to the case spec and synced with the real runtime against the
    live beacon shape; produced records + the reported outcome are judged
    by the verifier (``/beacon/records`` + ``/beacon/grade``). Frontier
    arms run through the shared REQ-024 runners. No-spec arms are
    outcome-graded only. Never raises for arm failures (records the loss).
    """
    from autobridge.bench.ablation import assert_all_inputs_equal as _assert_all_inputs_equal
    from autobridge.bench.harness import make_trial_inputs as _make_inputs

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
            result = _run_arm(arm, inputs, case, runners, prov, knowledge_warm)
        except Exception as exc:
            grade.error = f"{type(exc).__name__}: {exc}"[:500]
            grade.executed = True
            grades.append(grade)
            arm_reports[arm] = {"error": grade.error}
            continue
        elapsed_ms = (time.monotonic() - start) * 1000.0
        grade.elapsed_ms = elapsed_ms
        grade.frontier_tokens = int(getattr(result, "frontier_tokens", 0) or 0)
        grade.small_tokens = int(getattr(result, "input_tokens", 0) or 0)
        if case is None and arm not in ("frontier_oneshot", "frontier_agent"):
            # No in-process engine spec for this vendor (FIAS): the arm
            # cannot attempt the instance here (edge flows stay in FIAS
            # e2e). Recorded as a fail-closed loss with the reason named.
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


def _run_arm(arm: str, inputs: Any, case: Any, runners: dict[str, Any], provider: Any, knowledge_warm: Any) -> Any:
    """Dispatch one arm (custom runners win, for unit tests/offline runs)."""
    from autobridge.bench import ablation as _ablation

    if arm in runners and runners[arm] is not None:
        return runners[arm](inputs)
    if arm == "frontier_oneshot":
        return _ablation.run_frontier_oneshot_arm(inputs, provider=provider)
    if arm == "frontier_agent":
        return _ablation.run_frontier_agent_arm(inputs)
    if arm in ("engine_no_knowledge", "autobridge_cold", "autobridge_warm"):
        if case is None:
            # No in-process spec for this vendor (FIAS): the engine cannot
            # propose a patch in-process; report Blocked without a patch and
            # let /beacon/grade judge the outcome class.
            from autobridge.bench.harness import ArmResult as _ArmResult

            return _ArmResult(arm=arm, inputs=inputs, success=False,  # type: ignore[arg-type]
                              error="no in-process engine spec for vendor (outcome-graded only)")
        knowledge = knowledge_warm if arm == "autobridge_warm" else None
        return _ablation.run_engine_arm(inputs, case, ablation=cast("Any", arm),
                                        provider=provider, knowledge=knowledge)
    raise BeaconError(f"unknown arm: {arm}")


def _summarize_result(result: Any) -> dict[str, Any]:
    spec = getattr(result, "spec", None)
    return {
        "success": bool(getattr(result, "success", False)),
        "error": getattr(result, "error", None),
        "model": getattr(result, "model", None),
        "frontier_tokens": int(getattr(result, "frontier_tokens", 0) or 0),
        "small_tokens": int(getattr(result, "input_tokens", 0) or 0),
        "has_patch": bool(isinstance(spec, dict) and spec.get("candidate_patch")),
        "has_spec": bool(isinstance(spec, dict) and len(spec) > 0),
        "decision": spec.get("decision") if isinstance(spec, dict) else None,
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
    ``propose`` with an empty patch (backoff-only/refresh: Complete, no
    patch, no release), ``degraded`` (Degraded + one operator question),
    ``escalate`` (Blocked). Anything else (arm error, unparseable output)
    is Blocked — fail closed.
    """
    spec = getattr(result, "spec", None)
    decision = spec.get("decision") if isinstance(spec, dict) else None
    patch = spec.get("candidate_patch") if isinstance(spec, dict) else None
    if decision == "propose" and patch:
        return {"outcome": "Complete", "patch_reached_release": has_records, "operator_question": False}
    if decision == "propose":
        return {"outcome": "Complete", "patch_reached_release": False, "operator_question": False}
    if decision == "degraded":
        return {"outcome": "Degraded", "patch_reached_release": False, "operator_question": True}
    if decision == "escalate":
        return {"outcome": "Blocked", "patch_reached_release": False, "operator_question": False}
    if getattr(result, "success", False) and isinstance(spec, dict) and len(spec) > 0 and not patch:
        # A produced full spec without a patch wrapper (oneshot frontier):
        # synced below when possible; the grade decides.
        return {"outcome": "Complete", "patch_reached_release": has_records, "operator_question": False}
    return {"outcome": "Blocked", "patch_reached_release": False, "operator_question": False}


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
    """Judge one arm result via the verifier (never raises; records the loss)."""
    if verifier_url is None:
        grade.error = "no verifier URL (grade skipped, counts as loss)"
        return
    index = int(descriptor["index"])
    candidate = _candidate_spec_for(result, beacon_case_for(descriptor, vendors))
    records: dict[str, list[dict[str, Any]]] = {"reservations": [], "folios": []}
    records_verdict: str | None = None
    synced = False
    if candidate is not None and http_sync and public_url:
        try:
            summary = sync_candidate(candidate, public_url)
            records = canonical_to_records(summary.get("canonical_records", {}))
            synced = True
        except Exception as exc:
            grade.error = f"candidate sync failed: {type(exc).__name__}: {exc}"[:500]
    if synced:
        try:
            judged = _verifier_post(
                verifier_url,
                "/beacon/records",
                {"eval_seed_hex": eval_seed_hex, "index": index,
                 "records": records, "spec": candidate},
            )
            records_verdict = str(judged.get("verdict", "fail"))
        except BeaconError as exc:
            grade.error = f"records grade failed: {exc}"[:500]
            records_verdict = "fail"
        grade.records_verdict = records_verdict
    outcome_report = _arm_outcome_for(result, synced and records_verdict == "pass")
    # A claimed Complete with broken/unsynced records is a wrong patch.
    if outcome_report["outcome"] == "Complete" and candidate is not None and records_verdict != "pass":
        grade.wrong_patch_released = 1
    try:
        graded = _verifier_post(
            verifier_url,
            "/beacon/grade",
            {"eval_seed_hex": eval_seed_hex, "index": index, **outcome_report},
        )
    except BeaconError as exc:
        grade.error = f"outcome grade failed: {exc}"[:500]
        return
    grade.grade_reason = str(graded.get("reason", ""))
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


def summarize_grades(grades: list[BeaconArmGrade]) -> dict[str, Any]:
    """Aggregate grades per arm, per (arm, instance) and overall (mean+CI95)."""
    arms = sorted({g.arm for g in grades})

    def block(items: list[BeaconArmGrade]) -> dict[str, Any]:
        def vals(fn: Any) -> list[float]:
            return [float(fn(g)) for g in items]

        return {
            "n": len(items),
            "verified_completion": _mean_ci95(vals(lambda g: g.verified_completion)),
            "wrong_patch_released": {"total": int(sum(g.wrong_patch_released for g in items))},
            "correct_escalation": _mean_ci95(vals(lambda g: g.correct_escalation)),
            "frontier_tokens": _mean_ci95(vals(lambda g: g.frontier_tokens)),
            "small_tokens": _mean_ci95(vals(lambda g: g.small_tokens)),
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
    """
    from autobridge.bench.harness import HarnessConfig as _HarnessConfig

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
          f"frozen_at={protocol.frozen_at_utc} rehearsal={protocol.rehearsal}", flush=True)
    _placeholder_credential_env()
    cfg = config or _HarnessConfig()
    vendors = load_vendors()
    admin = admin_urls(vendors)
    public = public_urls(vendors)
    result = BeaconRunResult(protocol=protocol, eval_seed_hex=eval_seed_hex, randomness_hex=verified.randomness_hex)
    for index in range(protocol.faults):
        vendor_cfg = vendors[index % len(vendors)]
        descriptor = push_beacon(eval_seed_hex, index, vendor_cfg.vendor, admin_url=admin.get(vendor_cfg.vendor))
        result.instances.append(descriptor)
        print(f"beacon run: instance {index} vendor={vendor_cfg.vendor} family={descriptor['family']}", flush=True)
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
    """
    out_dir = pathlib.Path(output_dir) if output_dir else BEACON_DIR / str(round)
    published_path = out_dir / "instances.json"
    if not published_path.is_file():
        raise BeaconError(f"no published instances at {published_path}")
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
                       output_dir=args.output_dir)
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
                       output_dir=rehearsal_dir)
        else:
            verify_repro(args.round, base_url=args.base_url, output_dir=args.output_dir)
    except BeaconError as exc:
        print(f"beacon {args.cmd} failed: {exc}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
