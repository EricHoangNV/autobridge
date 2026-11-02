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

import contextlib
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
    # REQ-078 item 7: the frozen budgets — one token ceiling and one wall
    # clock for every arm (the agent included). ``None`` = a pre-REQ-078
    # protocol without the lines (still parses; env/defaults apply).
    token_ceiling: int | None = None
    wall_clock_s: float | None = None


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
    token_ceiling: int | None = None
    wall_clock_s: float | None = None
    if fields.get("beacon-token-ceiling") not in (None, ""):
        try:
            token_ceiling = int(str(fields["beacon-token-ceiling"]).strip())
        except ValueError as exc:
            raise BeaconError(f"protocol {path} has non-integer beacon-token-ceiling: {exc}") from exc
    if fields.get("beacon-wall-clock-s") not in (None, ""):
        try:
            wall_clock_s = float(str(fields["beacon-wall-clock-s"]).strip())
        except ValueError as exc:
            raise BeaconError(f"protocol {path} has non-numeric beacon-wall-clock-s: {exc}") from exc
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
        token_ceiling=token_ceiling,
        wall_clock_s=wall_clock_s,
    )


def _dir_has_results(out_dir: pathlib.Path) -> bool:
    """Whether a round dir already holds run results (no re-rolls)."""
    return (
        (out_dir / "instances.json").exists()
        or (out_dir / "summary.json").exists()
        or (out_dir / "grades.jsonl").exists()
    )


def resolve_protocol_path(
    protocol: str | pathlib.Path | None = None,
    round: int | None = None,
) -> pathlib.Path:
    """Resolve which frozen protocol a run uses (REQ-077 item 1).

    Order: an explicit ``--protocol`` path wins; else ``--round N`` maps
    to ``experiments/beacon/<N>/protocol.md``; else the old single
    ``experiments/beacon/protocol.md`` when present (backward compat);
    else the single unrun per-round protocol of this checkout (a protocol
    whose dir holds no results — a committed prior run's results never
    block a new run). Zero or several unrun candidates is an error (the
    caller names ``--round``).
    """
    if protocol is not None:
        return pathlib.Path(protocol)
    if round is not None:
        try:
            number = int(round)
        except (TypeError, ValueError) as exc:
            raise BeaconError(f"invalid round {round!r}: {exc}") from exc
        return BEACON_DIR / str(number) / "protocol.md"
    if PROTOCOL_PATH.is_file():
        return PROTOCOL_PATH
    candidates: list[pathlib.Path] = []
    if BEACON_DIR.is_dir():
        for child in sorted(BEACON_DIR.iterdir()):
            if not child.is_dir() or not child.name.isdigit():
                continue
            if (child / "protocol.md").is_file() and not _dir_has_results(child):
                candidates.append(child / "protocol.md")
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise BeaconError(
            "no frozen protocol: make eval-freeze ROUND_AFTER=<UTC> FAULTS=<N> first "
            "(a committed prior run's results never block a new freeze)"
        )
    names = ", ".join(p.parent.name for p in candidates)
    raise BeaconError(
        f"several unrun protocols ({names}): pass --round to choose one"
    )


def protocol_target_round(path: str | pathlib.Path) -> int:
    """The target round of a frozen protocol (parsed like :func:`parse_protocol`).

    This is the parsing ``scripts/beacon-eval.sh`` uses (REQ-077 item 1:
    the old ``grep '^beacon-target-round:'`` never matched the freeze's
    ``- beacon-target-round:`` line and exited 1 under ``set -euo pipefail``).
    """
    return parse_protocol(path).target_round


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

    REQ-077 item 1: one protocol per run — the default protocol path is
    ``experiments/beacon/<target-round>/protocol.md`` (the freeze computes
    the target round first). An explicit ``protocol_path`` still wins
    (rehearsals pass ``experiments/beacon/rehearsals/<round>/protocol.md``
    and keep that layout).
    """
    if not isinstance(faults, int) or isinstance(faults, bool) or faults < 1 or faults > 10_000:
        raise BeaconError("faults must be an int in [1, 10000]")
    if not isinstance(trials, int) or isinstance(trials, bool) or trials < 1:
        raise BeaconError("trials must be a positive int")
    if not arms or any(a not in ABLATION_ARMS for a in arms):
        raise BeaconError(f"unknown arms: {arms}")
    if protocol_path is not None and pathlib.Path(protocol_path).exists():
        raise BeaconError(f"refusing to re-freeze: {protocol_path} already exists (commit-once)")
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
    if protocol_path is None:
        path = BEACON_DIR / str(target_round) / "protocol.md"
    else:
        path = pathlib.Path(protocol_path)
    if path.exists():
        raise BeaconError(f"refusing to re-freeze: {path} already exists (commit-once)")
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
    # REQ-078 item 7: freeze the budgets — one token ceiling and one wall
    # clock for every arm (the agent included). Resolved the same way the
    # run resolves them (env wins, else the dev-ablation defaults).
    import os as _os2  # noqa: PLC0415

    from bench.harness import (  # noqa: PLC0415
        DEFAULT_TOKEN_CEILING as _DEFAULT_CEIL,
    )
    from bench.harness import (
        DEFAULT_WALL_CLOCK_S as _DEFAULT_WALL,
    )

    def _frozen_int(name: str, default: int) -> int:
        raw = (_os2.environ.get(name, "") or "").strip()
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError as exc:
            raise BeaconError(f"{name} must be an int: {exc}") from exc

    def _frozen_float(name: str, default: float) -> float:
        raw = (_os2.environ.get(name, "") or "").strip()
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError as exc:
            raise BeaconError(f"{name} must be a number: {exc}") from exc

    frozen_ceiling = _frozen_int("BENCH_TOKEN_CEILING", _DEFAULT_CEIL)
    frozen_wall = _frozen_float("BENCH_WALL_CLOCK", _DEFAULT_WALL)
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
- beacon-token-ceiling: {frozen_ceiling}
- beacon-wall-clock-s: {frozen_wall}
- beacon-chain: {beacon_client.LOE_MAINNET_CHAIN_HASH} scheme={beacon_client.LOE_SCHEME_ID}
- beacon-verify-url: {base_url.rstrip("/")}/public/{target_round}

Metrics per arm, scenario and overall (mean + 95% interval over trials):
verified completion rate (judged only by the verifier), wrong patches that
reached a release (must be 0 for AutoBridge), correct escalations
(Blocked with one operator question on an instance whose key accepts a
question — a no-patch Complete is verified completion, not an
escalation), frontier tokens, estimated
cost, wall time.

Engine-arm inputs (REQ-024 owner check on f32b4b4b, fixed): every arm gets
the same inputs (the pre-drift spec, the docs, the served instance,
budgets). The engine arms observe the instance end to end — sync the
served shape (the same simulator serving code the live stack runs,
deterministic from the public seed) → detector → classifier → the
engine's own hypothesis enumerator → ladder → verifier gate — with no
case-supplied drift class, delta, hypotheses or yield count. Instance
parameters, generator internals and answer keys never reach the arms.

Oracle dataset (REQ-083 A1): every beacon dataset serves all properties
in America/New_York — the one static zone the pre-drift specs carry —
so an unfaulted sync agrees with the oracle on business dates. The
records judge compares produced records against what the pre-drift spec
produces over the unfaulted simulator (REQ-083 A3, one judging regime
for HTTP vendors and FIAS alike).

Removed from the draw (REQ-083 B7): dates/tz_shift has no hour/day
offset arg in the closed transform set (adding one is a plan change),
so no spec patch can express its repair — it is never drawn. Meridian
never draws dates/format either (REQ-083 D18: a UTC-instant vendor
serves no verbatim date field; the cutoff trap stays, keyed no_patch).
Semantic gross_net scaling is likewise patch-inexpressible (no factor
arg) and stays reachable via its accepted escalation.
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
    from bench.evidence import render_fault_vs_baseline, spec_to_yaml  # noqa: PLC0415
    from bench.faults import beacon_sync_pair as _beacon_sync_pair  # noqa: PLC0415

    @_functools.lru_cache(maxsize=256)
    def _cached_evidence(spec_json: str, seed_hex: str, index: int, vendor: str) -> str:
        faulted_summary, baseline_summary = _beacon_sync_pair(
            vendor=vendor, eval_seed_hex=seed_hex, index=index, spec=_json.loads(spec_json)
        )
        return render_fault_vs_baseline(faulted_summary, baseline_summary)

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
    # REQ-083 B6: every arm reads the served docs for the faulted round
    # (announcing the renamed pagination params), not the static file.
    served_docs = None
    if str(descriptor["vendor"]) != "fias-pabx":
        try:
            served_docs = beacon_served_docs(
                str(descriptor["vendor"]), eval_seed_hex, int(descriptor["index"])
            )
        except Exception:
            served_docs = None
    return BenchContext(
        scenario_id=case.scenario_id,
        connector_id=case.connector_id,
        vocabulary_version=version,
        schema_json=connector_schema_json(),
        capabilities_json=capabilities_json_for_version(version),
        example_vendor=_example_vendor,
        example_spec_yaml=example_yaml,
        docs_text=served_docs or vendor_docs_text(case.docs_ref),
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
    report = detect_feed_field_rename(
        spec,
        "PS",
        announced,
        sample_records=wire_records,
        baseline_announced=sorted(_wire_keys(baseline_summary)),
    )
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
            # REQ-078 item 4: the report's rename pairing reaches the
            # ladder (rename_field pairs by value off it).
            **({"rename": dict(report.get("extra", {}).get("rename") or {})} if isinstance(report.get("extra"), dict) and report.get("extra", {}).get("rename") else {}),
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
            faulted_summary=dict(faulted_summary),
        )
    classification = classify([signal])
    return _ablation.DerivedDiagnostics(
        drift_class=classification.drift_class,
        fingerprint_delta=report.get("delta"),
        signals=(signal,),
        classification_reason=classification.reason,
        faulted_outcome=str(faulted_summary.get("outcome", "")),
        faulted_summary=dict(faulted_summary),
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
            expected = _faultgen.expected_for(family, params, str(descriptor["vendor"]))
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


def sync_candidate_url(candidate_spec: dict[str, Any], public_url: str) -> str:
    """Base URL for a candidate sync: vendor host + spec base path.

    REQ-083 A4: ``sync_candidate`` used the vendor public URL verbatim,
    dropping the spec's own base path (e.g. Meridian's
    ``/api/connector/v1``) so every candidate sync 404'd. The public URL
    names the host; the path comes from the spec under repair.
    """
    from autobridge.spec.models import load_spec_from_dict

    spec_obj = load_spec_from_dict(candidate_spec)
    raw_base = str(spec_obj.source.base_url)
    try:
        from autobridge.runtime.http import resolve_env_placeholders as _resolve

        raw_base = _resolve(raw_base)
    except Exception:
        pass
    import urllib.parse as _urlparse

    spec_path = _urlparse.urlsplit(raw_base).path.strip("/")
    host = str(public_url).rstrip("/")
    if not spec_path:
        return host
    return f"{host}/{spec_path}"


def sync_candidate(
    candidate_spec: dict[str, Any],
    public_url: str,
    *,
    timeout_s: float = 30.0,
    max_total_delay_s: float = 120.0,
    client_factory: Any | None = None,
) -> dict[str, Any]:
    """Sync a candidate spec against the live beacon shape (in-process).

    Uses the real engine runtime (``execute_sync``) with an explicit
    vendor-delay budget (AR-2-1: a hostile ``Retry-After`` cannot stall the
    run). Returns the sync summary (outcome, canonical_records, ...).

    ``client_factory`` overrides the HTTP transport (tests serve the
    vendor app in-process through it); ``None`` opens a real socket to
    the joined vendor URL. Auth, retry and the joined base URL are
    identical either way — the A2 reference-repair proof and the live
    grading run the same path.
    """
    from autobridge.engine.sync_run import execute_sync
    from autobridge.runtime.http import HttpClient
    from autobridge.spec.models import load_spec_from_dict

    spec_obj = load_spec_from_dict(candidate_spec)
    client = HttpClient(
        base_url=sync_candidate_url(candidate_spec, public_url),
        auth=spec_obj.source.auth,
        retry=spec_obj.source.rate_limit.retry,
        timeout_s=timeout_s,
        max_total_delay_s=max_total_delay_s,
        client_factory=client_factory,
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
    # REQ-078 item 5: wrong patches *proposed* as well as released — a
    # candidate the verifier rejects on the instance records counts here
    # whether or not it reached a release (an unneeded patch on a
    # no-change instance counts as wrong; see docs/benchmark.md).
    wrong_patch_proposed: int = 0
    correct_escalation: int = 0
    frontier_tokens: int = 0
    small_tokens: int = 0
    # REQ-060 item 3 / REQ-062 item 5: the per-row token split the owner
    # picks a limit from (input / output / reasoning as the provider
    # reports them; 0 when unreported).
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    # REQ-083 B11: in/out per model tier, one basis for every arm
    # (frontier arms spend the frontier tier only; engine arms split).
    frontier_input: int = 0
    frontier_output: int = 0
    small_input: int = 0
    small_output: int = 0
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
    # REQ-078 item 9: the warm arm learns during the run — after each
    # instance a verified fix it produced is learned (the way an approved
    # fix is in production) and is available to later instances. When a
    # warm row reused an in-run learned fix, this names the source
    # instance (None otherwise, and always None for cold).
    warm_used_learned_from: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _mean_ci95(values: list[float], *, bounded: bool = False) -> dict[str, Any]:
    import math as _math

    n = len(values)
    if n == 0:
        return {"mean": 0.0, "ci95_lo": 0.0, "ci95_hi": 0.0, "n": 0}
    mean = statistics.fmean(values)
    if n == 1:
        lo = hi = mean
    else:
        sd = statistics.pstdev(values)
        half = 1.96 * sd / _math.sqrt(n)
        lo, hi = mean - half, mean + half
    if bounded:
        # Rates live in [0, 1]: a CI that escapes the unit interval is a
        # computation artefact, never a result.
        lo, hi = max(0.0, lo), min(1.0, hi)
    return {"mean": mean, "ci95_lo": lo, "ci95_hi": hi, "n": n}


def _per_instance_means(
    items: list[BeaconArmGrade], fn: Any
) -> tuple[list[float], int]:
    """Mean per instance first (over trials), then the instance means.

    REQ-078 item 8: every arm keeps the frozen trial count, so scores are
    averaged per instance first and CIs are computed over instances —
    ``n`` names instances, and one lucky instance cannot dominate the
    headline through trial count.
    """
    by_instance: dict[int, list[float]] = {}
    for g in items:
        by_instance.setdefault(int(g.instance_index), []).append(float(fn(g)))
    means = [sum(v) / len(v) for v in by_instance.values() if v]
    return means, len(means)


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


#: Secret-hint keys masked in persisted model traces (REQ-084 item 3).
#: Same hint set as the evidence redaction (``bench/evidence.py``):
#: credential *names* travel, values never do.
_TRACE_SECRET_HINTS = ("token", "key", "secret", "password", "auth", "credential", "session")

#: Assignment-shaped secrets inside free text (``key: value`` / ``key=value``).
_TRACE_SECRET_RE = None  # compiled lazily (``re`` import is cheap, kept local)


def _trace_secret_re() -> Any:
    global _TRACE_SECRET_RE
    if _TRACE_SECRET_RE is None:
        import re as _re2

        _TRACE_SECRET_RE = (
            _re2.compile(
                r"(?i)\b[\w-]*(token|api[_-]?key|client[_-]?secret|password|authorization|session[_-]?token)\b\s*[:=]\s*\S+"
            ),
            _re2.compile(r"(?i)\b(bearer)\b\s+\S+"),
        )
    return _TRACE_SECRET_RE


def _redact_trace_value(key: str, value: Any, *, _depth: int = 0) -> Any:
    """Redact one traced value: secret-hint keys masked, long text cut.

    Same basis as the evidence redaction (secret names only, never
    values); truncation is gentler (2000 chars) so a persisted L3 prompt
    stays readable enough to trace a live failure offline.
    """
    lowered = str(key).lower()
    if any(hint in lowered for hint in _TRACE_SECRET_HINTS):
        return "***"
    if isinstance(value, dict):
        if _depth >= 4:
            return "{...}"
        return {str(k): _redact_trace_value(str(k), v, _depth=_depth + 1) for k, v in list(value.items())[:50]}
    if isinstance(value, list):
        return [_redact_trace_value(key, v, _depth=_depth + 1) for v in value[:50]]
    if isinstance(value, str):
        key_re, bearer_re = _trace_secret_re()
        scrubbed = key_re.sub(lambda m: m.group(0).split(":")[0].split("=")[0] + ":***", value)
        scrubbed = bearer_re.sub(lambda m: m.group(1) + " ***", scrubbed)
        if len(scrubbed) > 2000:
            return scrubbed[:2000] + "… (truncated)"
        return scrubbed
    return value


def _redact_trace_messages(messages: Any) -> Any:
    """Redact a provider request (list of ``{role, content}`` dicts)."""
    if not isinstance(messages, list):
        return _redact_trace_value("messages", messages)
    out = []
    for msg in messages:
        if isinstance(msg, dict):
            out.append({str(k): _redact_trace_value(str(k), v) for k, v in msg.items()})
        else:
            out.append(_redact_trace_value("message", msg))
    return out


class _TraceRecordingProvider:
    """Provider wrapper recording redacted (request, response) per call.

    REQ-084 item 3: the engine arm's L3 request and response were never
    persisted (in-memory session), so a live failure no in-process
    reproduction explains could not be traced. The wrapper delegates
    every call to the inner provider and appends a redacted copy —
    request messages, response text, tier/purpose/model/usage — to
    :attr:`calls`. Secrets never: key names only (see
    :func:`_redact_trace_value`).
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 1024,
        tier: str = "small",
        purpose: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> Any:
        result = self._inner.complete(
            list(messages), max_tokens=max_tokens, tier=tier, purpose=purpose, tools=tools
        )
        try:
            usage = getattr(result, "usage", None)
            if usage is None:
                usage_dict: dict[str, Any] = {}
            elif isinstance(usage, dict):
                usage_dict = {str(k): int(v) if isinstance(v, (int, float)) else v for k, v in usage.items()}
            else:
                usage_dict = {
                    "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
                    "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
                    "reasoning_tokens": int(getattr(usage, "reasoning_tokens", 0) or 0),
                }
        except Exception:
            usage_dict = {}
        try:
            model = None
            for attr in ("frontier_model", "small_model", "model"):
                candidate = getattr(self._inner, attr, None)
                if isinstance(candidate, str) and candidate:
                    if attr == "frontier_model" and tier != "frontier":
                        continue
                    if attr == "small_model" and tier != "small":
                        continue
                    model = candidate
                    break
        except Exception:
            model = None
        with contextlib.suppress(Exception):
            self.calls.append({
                "tier": str(tier),
                "purpose": str(purpose),
                "model": model,
                "max_tokens": int(max_tokens),
                "request": _redact_trace_messages(messages),
                "response_text": _redact_trace_value("response_text", getattr(result, "text", "") or ""),
                "usage": usage_dict,
            })
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _write_arm_trace(
    trace_dir: str | pathlib.Path,
    index: int,
    trial: int,
    vendor: str,
    family: str,
    arm: str,
    sink: list[dict[str, Any]] | None,
) -> None:
    """Persist one arm's model request/response for an instance (REQ-084/3).

    Written under ``<round>/traces/`` next to ``grades.jsonl`` so a live
    failure is traceable offline. Never raises: tracing must not break a
    run (a failure prints and the run continues without that file).
    """
    try:
        out = pathlib.Path(trace_dir) / "traces"
        out.mkdir(parents=True, exist_ok=True)
        if sink:
            entry = dict(sink[0])
        else:
            entry = {"arm": arm, "calls": [], "brief": None,
                     "note": "no provider-backed model calls recorded (custom runner)"}
        payload = {
            "arm": arm, "instance_index": int(index), "trial": int(trial),
            "vendor": str(vendor), "family": str(family),
            **entry,
        }
        (out / f"instance-{int(index)}-trial-{int(trial)}-{arm}.json").write_text(
            json.dumps(payload, sort_keys=True, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"beacon run: trace write failed for instance {index} arm {arm}: {exc}", flush=True)


def run_instance_trial(
    protocol: BeaconProtocol,
    eval_seed_hex: str,
    descriptor: dict[str, Any],
    trial: int,
    *,
    config: Any = None,
    runners: dict[str, Any] | None = None,
    provider: Any | None = None,
    knowledge_warm: Any | None = None,
    verifier_url: str | None = None,
    vendors: list[BeaconVendor] | None = None,
    vendor_cfg: BeaconVendor | None = None,
    public_url: str = "",
    trace_dir: str | pathlib.Path | None = None,
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
            sink: list[dict[str, Any]] = []
            result = _run_arm(arm, inputs, case, runners, prov, knowledge_warm,
                              descriptor=descriptor, eval_seed_hex=eval_seed_hex,
                              trace_sink=sink if trace_dir is not None else None)
            if trace_dir is not None:
                _write_arm_trace(trace_dir, index, trial, vendor, str(descriptor["family"]), arm, sink)
        except Exception as exc:
            grade.error = f"{type(exc).__name__}: {exc}"[:500]
            grade.executed = True
            grades.append(grade)
            arm_reports[arm] = {"error": grade.error}
            continue
        elapsed_ms = (time.monotonic() - start) * 1000.0
        grade.elapsed_ms = elapsed_ms
        grade.frontier_tokens = int(getattr(result, "frontier_tokens", 0) or 0)
        # REQ-083 B11: in/out per model tier, one basis for every arm.
        # Every runner reports the split (frontier arms spend the frontier
        # tier only); rows carry the split, never a swapped total. Legacy
        # runners without the split fall back to attributing their totals
        # to the frontier tier for frontier arms.
        from bench.ablation import ENGINE_ARMS as _ENGINE_ARMS

        grade.frontier_input = int(getattr(result, "frontier_input", 0) or 0)
        grade.frontier_output = int(getattr(result, "frontier_output", 0) or 0)
        grade.small_input = int(getattr(result, "small_input", 0) or 0)
        grade.small_output = int(getattr(result, "small_output", 0) or 0)
        if (
            grade.frontier_input + grade.frontier_output
            + grade.small_input + grade.small_output == 0
            and arm not in _ENGINE_ARMS
        ):
            grade.frontier_input = int(getattr(result, "input_tokens", 0) or 0)
            grade.frontier_output = int(getattr(result, "output_tokens", 0) or 0)
        # REQ-062 item 5 (superseded by B11's split): legacy small_tokens
        # kept for summary compatibility — engine arms report the small
        # tier's input total here.
        if arm in _ENGINE_ARMS:
            grade.small_tokens = grade.small_input
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
        if arm == "autobridge_warm":
            _beacon_warm_learn(knowledge_warm, index, result, grade)
        grades.append(grade)
    return grades, arm_reports


def _beacon_warm_learn(knowledge_warm: Any, index: int, result: Any, grade: BeaconArmGrade) -> None:
    """In-run warm learning (REQ-078 item 9).

    Instances run in the frozen order; after each instance a verified fix
    the warm arm produced is learned (the way an approved fix is in
    production: vendor + drift class + delta key → patch) and is
    available to later instances. Cold never learns. The dev-split
    pre-warm stays the starting cache. A warm row that reused an in-run
    learned fix reports the source instance on the grade; per-instance
    averaging keeps one lucky reuse from dominating the headline.
    """
    if knowledge_warm is None:
        return
    try:
        spec = getattr(result, "spec", None)
        if not isinstance(spec, dict):
            return
        from autobridge.repair.knowledge import cache_key as _cache_key
    except Exception:
        return
    learn_inputs = spec.get("learn_inputs") if isinstance(spec.get("learn_inputs"), dict) else None
    sources: dict[str, int] = getattr(knowledge_warm, "beacon_learned_sources", None) or {}
    with contextlib.suppress(Exception):
        knowledge_warm.beacon_learned_sources = sources
    # Used a learned fix? An L0 hit whose key came from an earlier
    # beacon instance (never the dev-split pre-warm).
    try:
        if (
            learn_inputs is not None
            and spec.get("ladder_level_reached") == 0
            and spec.get("rule_id")
        ):
            _key = _cache_key(
                str(learn_inputs.get("vendor", "unknown")),
                str(learn_inputs.get("drift_class", "UNKNOWN")),
                learn_inputs.get("delta"),
            )
            if _key in sources:
                grade.warm_used_learned_from = sources[_key]
    except Exception:
        pass
    # Learn this instance's verified fix for later instances.
    try:
        if (
            grade.verified_completion == 1
            and learn_inputs is not None
            and isinstance(spec.get("candidate_patch"), list)
            and spec.get("candidate_patch")
        ):
            _key = _cache_key(
                str(learn_inputs.get("vendor", "unknown")),
                str(learn_inputs.get("drift_class", "UNKNOWN")),
                learn_inputs.get("delta"),
            )
            if _key not in sources:
                knowledge_warm.put(
                    str(learn_inputs.get("vendor", "unknown")),
                    learn_inputs.get("delta"),
                    [dict(op) for op in spec["candidate_patch"] if isinstance(op, dict)],
                    str(spec.get("rule_id") or "beacon-learned"),
                    drift_class=str(learn_inputs.get("drift_class", "UNKNOWN")),
                    approval_id=f"beacon:{index}",
                    actor="beacon-warm",
                )
                sources[_key] = index
                with contextlib.suppress(Exception):
                    knowledge_warm.beacon_learned_sources = sources
    except Exception:
        pass


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
    trace_sink: list[dict[str, Any]] | None = None,
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

    REQ-084 item 3: when *trace_sink* is given, provider-backed arms
    record their redacted model request/response into it (one entry per
    arm); the agent arm records its rendered brief instead (its model
    runs in a CLI subprocess, not through the provider).
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
        recorder: Any | None = None
        effective = provider
        if trace_sink is not None:
            inner = provider if provider is not None else _ablation.ablation_provider()
            recorder = _TraceRecordingProvider(inner)
            effective = recorder
        result = _ablation.run_frontier_oneshot_arm(inputs, provider=effective, case=case, context=ctx)
        if trace_sink is not None:
            trace_sink.append({"arm": arm, "calls": list(recorder.calls) if recorder is not None else [], "brief": None})
        return result
    if arm == "frontier_agent":
        ctx = None
        if case is not None and descriptor is not None and eval_seed_hex:
            try:
                ctx = beacon_bench_context(descriptor, case, inputs, eval_seed_hex)
            except Exception:
                ctx = None
        result = _ablation.run_frontier_agent_arm(inputs, case=case, context=ctx)
        if trace_sink is not None:
            brief = ""
            try:
                from bench.context import messages_to_text as _to_text
                from bench.context import render_frontier_agent_brief as _render_brief

                brief = _to_text(_render_brief(inputs, ctx)).strip() if ctx is not None else ""
            except Exception:
                brief = ""
            trace_sink.append({"arm": arm, "calls": [],
                               "brief": _redact_trace_value("brief", brief)})
        return result
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

        # REQ-084 item 3: record the engine arm's model request/response
        # per instance (redacted) — a live failure no in-process
        # reproduction explains must stay traceable offline.
        engine_recorder: Any | None = None
        engine_effective = provider
        if trace_sink is not None:
            engine_inner = provider if provider is not None else _ablation.ablation_provider()
            engine_recorder = _TraceRecordingProvider(engine_inner)
            engine_effective = engine_recorder
        result = _ablation.run_engine_arm(inputs, case, ablation=cast("Any", arm),
                                          provider=engine_effective, knowledge=knowledge, derive=_derive)
        if trace_sink is not None:
            trace_sink.append({"arm": arm,
                               "calls": list(engine_recorder.calls) if engine_recorder is not None else [],
                               "brief": None})
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
            # REQ-078 item 9: learn inputs for in-run warm learning (vendor
            # + drift class + JSON-safe delta the knowledge key derives
            # from — same key production approval learning uses).
            # REQ-083 B10: the vendor is the spec's connector vendor (what
            # the ladder looks up under), never the descriptor vendor —
            # "atlas" vs "AtlasPMS" never meet in the lowercased key.
            try:
                from autobridge.repair.knowledge import delta_to_json as _delta_to_json
            except Exception:
                _delta_to_json = None  # type: ignore[assignment]
            try:
                _delta = getattr(diag, "fingerprint_delta", None)
                if _delta_to_json is not None:
                    _delta_json: Any = _delta_to_json(_delta)
                else:
                    import dataclasses as _dc2
                    import json as _json2

                    _delta_json = (
                        _dc2.asdict(_delta)
                        if _dc2.is_dataclass(_delta)
                        else (_delta if isinstance(_delta, dict) else None)
                    )
                    _delta_json = _json2.loads(_json2.dumps(_delta_json, sort_keys=True, default=str))
            except Exception:
                _delta_json = None
            payload["learn_inputs"] = {
                "vendor": _spec_vendor_for_learn(dict(case.spec), vendor),
                "drift_class": str(getattr(diag, "drift_class", "UNKNOWN")),
                "delta": _delta_json,
            }
            result = _dataclasses.replace(result, spec=payload)
        except Exception:
            pass
        return result
    raise BeaconError(f"unknown arm: {arm}")


def _spec_vendor_for_learn(spec: dict[str, Any], fallback: str) -> str:
    """Connector vendor for knowledge keys (REQ-083 B10).

    Production approval learning and the ladder lookup both key on the
    spec's ``connector.vendor``; the beacon descriptor vendor
    (``atlas`` vs ``AtlasPMS``) is a different string that never meets
    it in the lowercased cache key. Falls back to the descriptor vendor
    when the spec names none.
    """
    try:
        conn = spec.get("connector", {})
        if isinstance(conn, dict):
            v = conn.get("vendor")
            if isinstance(v, str) and v.strip():
                return v.strip()
    except Exception:
        pass
    return str(fallback)


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


def _candidate_apply_error(result: Any, case: Any) -> str | None:
    """Apply error when a proposed patch does not apply (REQ-081 item 3).

    Returns the error string when the arm proposed a non-empty patch that
    fails to apply to the pre-drift spec, else None. A patch that does
    not apply is a wrong patch proposed, never \"no patch\".
    """
    try:
        spec = getattr(result, "spec", None)
        if not isinstance(spec, dict):
            return None
        patch = spec.get("candidate_patch")
        if not patch or not isinstance(patch, list):
            return None
        if case is None or not isinstance(getattr(case, "spec", None), dict):
            return None
        from autobridge.spec.patch import apply_patch as _apply_patch  # noqa: PLC0415

        try:
            _apply_patch(copy.deepcopy(case.spec), list(patch))
            return None
        except Exception as exc:
            return f"candidate patch does not apply: {type(exc).__name__}: {exc}"[:500]
    except Exception:
        return None


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
            # REQ-081 item 3: never a silent loss — the caller records the
            # apply error via _candidate_apply_error and counts it as a
            # wrong patch proposed.
            return None
    # frontier_oneshot returns a full spec dict (no candidate_patch wrapper).
    if spec.get("operations") and spec.get("entities"):
        return dict(spec)
    return None


def _arm_failure_reason(result: Any, spec: Any) -> str:
    """One-line reason an arm never attempted (REQ-078 item 5)."""
    outcome_label = getattr(result, "outcome", None)
    if isinstance(outcome_label, str) and outcome_label.strip():
        return f"arm outcome {outcome_label.strip()}"[:200]
    error = getattr(result, "error", None)
    if isinstance(error, str) and error.strip():
        return error.strip()[:200]
    if isinstance(spec, dict) and spec.get("decision") in ("timed_out", "truncated", "budget_exhausted", "error", "crashed"):
        return f"arm reported {spec.get('decision')}"[:200]
    if isinstance(spec, dict) and spec.get("budget_exhausted"):
        return "arm ran out of budget (BudgetExhausted)"[:200]
    if not getattr(result, "ran", False):
        return "arm never ran"
    return "arm produced no gradable output"


def _arm_failed(result: Any, spec: Any) -> bool:
    """True when the arm crashed/timed-out/was truncated/ran out of budget/never ran."""
    import re as _re  # noqa: PLC0415

    outcome_label = getattr(result, "outcome", None)
    if isinstance(outcome_label, str) and outcome_label.strip() in (
        "timed_out", "truncated", "budget_exhausted", "error", "crashed", "no_output",
    ):
        return True
    error = getattr(result, "error", None)
    if isinstance(error, str) and error.strip() and not getattr(result, "ran", False):
        return True
    if isinstance(error, str) and _re.search(r"timed?\s*out|truncat|crashed?|budget|traceback|killed|oom", error, _re.IGNORECASE):
        return True
    if isinstance(spec, dict) and spec.get("decision") in ("timed_out", "truncated", "budget_exhausted", "error", "crashed"):
        return True
    if isinstance(spec, dict) and spec.get("budget_exhausted"):
        return True
    return bool(not getattr(result, "ran", False) and not (isinstance(spec, dict) and len(spec) > 0))


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

    REQ-078 item 5: an arm that crashed, timed out, was truncated, ran
    out of budget or never ran reports outcome ``Error`` — its own
    failure class, never an escalation. ``Error`` passes no instance
    (the verifier fails it closed for every expected class).
    """
    spec = getattr(result, "spec", None)
    decision = spec.get("decision") if isinstance(spec, dict) else None
    patch = spec.get("candidate_patch") if isinstance(spec, dict) else None
    answer_kind = spec.get("answer_kind") if isinstance(spec, dict) else None
    question = spec.get("question") if isinstance(spec, dict) else None
    has_question = isinstance(question, str) and bool(question.strip())
    if _arm_failed(result, spec):
        return {
            "outcome": "Error",
            "patch_reached_release": False,
            "operator_question": False,
            "arm_failed": True,
            "failure": _arm_failure_reason(result, spec),
        }
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
            # REQ-078 (reopened 2026-11-02): name the rung that decided — a
            # row whose model was asked never reads "escalate at L4 (no
            # rule)". L3-attempted escalations carry the model's question.
            if bool(spec.get("l3_attempted")):
                if question and question.strip():
                    return (
                        f"engine: detected {drift} ({n_signals} signals) -> "
                        f"L3 model escalated: {question.strip()[:200]}"
                    )
                return (
                    f"engine: detected {drift} ({n_signals} signals) -> "
                    f"L3 model escalated (no question recorded)"
                )
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
            # REQ-078 (reopened 2026-11-02): name the rung that decided.
            if bool(spec.get("l3_attempted")):
                base = f"ladder: L3 model escalated {drift} (no verified patch)"
            else:
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


#: Vendor docs endpoints serving the faulted-round announcement (REQ-083 B6).
_BEACON_DOCS_PATHS: dict[str, str] = {
    "atlas": "/openapi.yaml",
    "harbor": "/_harbor_docs",
    "legacystay": "/_legacystay_docs",
    "meridian": "/_meridian_docs",
}

#: Cache for served faulted docs (REQ-083 B6): one entry per instance.
_SERVED_DOCS_CACHE: dict[tuple[str, str, int], str] = {}


def beacon_served_docs(vendor: str, eval_seed_hex: str, index: int) -> str | None:
    """Faulted-round served docs for *vendor* (REQ-083 B6).

    The vendor app's own docs endpoint with the active beacon instance —
    the same docs every arm reads, carrying the announced pagination
    params for a faulted round. None when unreachable (callers fall back
    to the static docs file).
    """
    from bench.faults import _BEACON_VENDOR_APPS as _APPS

    key = (str(vendor), str(eval_seed_hex), int(index))
    if key in _SERVED_DOCS_CACHE:
        return _SERVED_DOCS_CACHE[key]
    try:
        import importlib as _importlib

        from starlette.testclient import TestClient as _TestClient

        from bench.faults import fake_env as _fake_env
        from bench.faults import isolated_sim_state as _isolated
        from simulators.common import beacon_serve as _beacon

        module_name = _APPS[str(vendor)]
        docs_path = _BEACON_DOCS_PATHS[str(vendor)]
        with _isolated(), _fake_env():
            _beacon.set_beacon(eval_seed_hex, int(index))
            app = _importlib.import_module(module_name).app
            with _TestClient(app) as client:
                resp = client.get(docs_path)
            if resp.status_code != 200:
                return None
            text = resp.text
    except Exception:
        return None
    _SERVED_DOCS_CACHE[key] = text
    return text


#: Cache for unfaulted baseline records (REQ-083 A3): one entry per
#: ``(vendor, eval_seed, index, spec-sha)``; runs hold a handful.
_BASELINE_RECORDS_CACHE: dict[tuple[str, str, int, str], dict[str, list[dict[str, Any]]]] = {}


def unfaulted_baseline_records(
    vendor: str,
    eval_seed_hex: str,
    index: int,
    spec: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Pre-drift spec over the unfaulted simulator (REQ-083 A1/A3).

    The single comparison baseline for the one judging regime: the same
    serving code the arms sync against, with the beacon oracle dataset
    pinned and no instance mutation (HTTP vendors), or the unfaulted
    feed (``fias-pabx``). Used by the A1 preflight (proving the
    apparatus works before it measures) and posted as
    ``baseline_records`` with every ``/beacon/records`` grade.
    """
    import hashlib as _hashlib
    import json as _json

    key = (str(vendor), str(eval_seed_hex), int(index),
           _hashlib.sha256(_json.dumps(spec, sort_keys=True, default=str).encode()).hexdigest()[:16])
    cached = _BASELINE_RECORDS_CACHE.get(key)
    if cached is not None:
        return cached
    from autobridge.engine.sync_run import execute_sync
    from autobridge.spec.models import load_spec_from_dict

    load_spec_from_dict(spec)  # fail fast on an invalid pre-drift spec
    if vendor == "fias-pabx":
        from bench.faults import _FIAS_BASE_ROW as _BaseRow
        from bench.faults import _FIAS_RE as _RE
        from bench.faults import _BeaconFiasFeed as _Feed

        summary = execute_sync(
            spec, None,
            feed_reader=_Feed([dict(_BaseRow)], [dict(r) for r in _RE]),
        )
    else:
        import importlib as _importlib

        from bench.faults import _BEACON_VENDOR_APPS as _APPS
        from bench.faults import _dataset_seed_for as _seed_for
        from bench.faults import _testclient_http_client as _client_for
        from bench.faults import fake_env as _fake_env
        from bench.faults import isolated_sim_state as _isolated
        from simulators.common import beacon_serve as _beacon
        from simulators.common import state as _sim_state

        try:
            module_name = _APPS[str(vendor)]
        except KeyError:
            raise ValueError(f"no in-process beacon serving for vendor {vendor!r}") from None
        dataset_seed = _seed_for(eval_seed_hex, int(index))
        with _isolated(), _fake_env():
            _sim_state.set_scenario("baseline", dataset_seed)
            app = _importlib.import_module(module_name).app
            client = _client_for(app, spec)
            with _beacon.unfaulted_baseline(dataset_seed):
                summary = execute_sync(spec, client)
    records = canonical_to_records(summary.get("canonical_records", {}))
    _BASELINE_RECORDS_CACHE[key] = records
    return records


def _preflight_repair_index(vendor: str, eval_seed_hex: str, pos: int) -> int:
    """First repair-expected instance index for *vendor* at/after *pos*.

    Round-robin vendors repeat every 5 indices; the judged preflight
    instance must expect ``repair`` (an escalate key fails the records
    judge by design). Fails loudly when no repair instance appears
    within 250 indices — the seed cannot prove this vendor.
    """
    from simulators.common import faultgen as _faultgen

    try:
        seed = bytes.fromhex(str(eval_seed_hex).strip())
    except (ValueError, AttributeError) as exc:
        raise BeaconError(f"preflight: bad eval seed hex: {exc}") from exc
    if len(seed) != 32:
        raise BeaconError(f"preflight: eval seed must be 32 bytes, got {len(seed)}")
    order = [v.vendor for v in load_vendors()]
    if vendor not in order:
        raise BeaconError(f"preflight: unknown vendor {vendor!r}")
    step = len(order)
    for index in range(pos, pos + step * 50, step):
        try:
            inst = _faultgen.generate_instance(seed, index)
        except (ValueError, KeyError) as exc:
            raise BeaconError(f"preflight: bad instance {index}: {exc}") from exc
        if str(inst.get("expected", "")) == "repair":
            return index
    raise BeaconError(f"preflight: no repair instance for {vendor} in 50 rounds (seed cannot prove it)")


def _wire_by_op(summary: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Served wire records by operation name (for reference-patch pairing)."""
    out: dict[str, list[dict[str, Any]]] = {}
    op_results = summary.get("operation_results", {})
    if isinstance(op_results, dict):
        for op, res in op_results.items():
            recs = res.get("records") if isinstance(res, dict) else getattr(res, "records", None)
            if isinstance(recs, list):
                out[str(op)] = [r for r in recs if isinstance(r, dict)]
    return out


@contextlib.contextmanager
def _faulted_transport_for(vendor: str, eval_seed_hex: str, index: int, spec: dict[str, Any]) -> Any:
    """In-process faulted serving for ``sync_candidate`` (the real path).

    Mirrors the A2 prover: the same vendor app + beacon slot the arms
    sync against, with the spec's own base path joined (so meridian's
    ``/api/connector/v1`` rides along). Yields a ``client_factory`` for
    :func:`sync_candidate`.
    """
    import importlib as _importlib
    import urllib.parse as _urlparse

    from autobridge.runtime.http import resolve_env_placeholders as _resolve
    from bench import faults as _faults
    from simulators.common import beacon_serve as _beacon

    with _faults.isolated_sim_state(), _faults.fake_env():
        _beacon.set_beacon(eval_seed_hex, index)
        app = _importlib.import_module(_faults._BEACON_VENDOR_APPS[vendor]).app
        base_path = _urlparse.urlsplit(_resolve(str(spec["source"]["base_url"]))).path.rstrip("/")
        fake_base = _faults._FAKE_BASE_URL + base_path

        def factory(**_kw: Any) -> Any:
            from starlette.testclient import TestClient

            return TestClient(app, base_url=fake_base)

        try:
            yield factory
        finally:
            from simulators.common import state as _sim_state

            _sim_state.set_beacon_state(None, None)


def preflight_prove_repairs(
    protocol: BeaconProtocol,
    *,
    eval_seed_hex: str,
    vendors: list[BeaconVendor] | None = None,
    verifier_url: str | None = None,
) -> dict[str, Any]:
    """Prove every drawn ``repair`` instance is reachable by a spec patch.

    REQ-084 item 2: the old preflight proved only that the unfaulted
    baseline passes the judge, while ``reference_patch_for`` was called
    only from a unit test on another seed and entity — so a meridian
    reservation nesting was never proven and no arm could pass it. For
    every drawn instance whose key is ``repair``: build
    ``reference_patch_for``, push it through the real ``sync_candidate``
    path and the judge, and fail loudly with the instance, the patch and
    the failed checks when it does not pass. Rehearsals included (this
    runs inside :func:`preflight_beacon`, which ``run_beacon`` executes
    before the first arm).

    Returns ``{"proved": [indices]}``. Raises :class:`BeaconError` on the
    first unprovable repair — an unreachable ``repair`` key is a harness
    bug (or a wrong key), never a silent arm loss.
    """
    from bench import faults as _faults
    from bench import reference_patch as _refpatch

    vendor_list = list(vendors) if vendors is not None else load_vendors()
    try:
        seed = bytes.fromhex(str(eval_seed_hex).strip())
    except (ValueError, AttributeError) as exc:
        raise BeaconError(f"preflight: bad eval seed hex: {exc}") from exc
    if len(seed) != 32:
        raise BeaconError(f"preflight: eval seed must be 32 bytes, got {len(seed)}")
    proved: list[int] = []
    for index in range(int(protocol.faults)):
        try:
            from simulators.common import faultgen as _faultgen

            inst = _faultgen.generate_instance(seed, index)
        except (ValueError, KeyError) as exc:
            raise BeaconError(f"preflight: bad instance {index}: {exc}") from exc
        if str(inst.get("expected", "")) != "repair":
            continue
        vendor = str(inst.get("vendor", ""))
        family = str(inst.get("family", ""))
        desc = {"index": index, "vendor": vendor}
        spec = engine_spec_for(desc, vendor_list)
        if spec is None:
            raise BeaconError(f"preflight: no pre-drift spec for vendor {vendor!r} (instance {index})")
        try:
            faulted, baseline = _faults.beacon_sync_pair(
                vendor=vendor, eval_seed_hex=str(eval_seed_hex), index=index, spec=spec
            )
        except Exception as exc:
            raise BeaconError(
                f"preflight: repair instance {index} ({vendor}/{family} "
                f"{dict(inst.get('params', {}))}): faulted sync failed: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        patch = _refpatch.reference_patch_for(
            inst, spec,
            faulted_wire=_wire_by_op(faulted), baseline_wire=_wire_by_op(baseline),
        )
        if not patch:
            raise BeaconError(
                f"preflight: no reference patch for repair instance {index} "
                f"({vendor}/{family} {dict(inst.get('params', {}))}) — "
                "unreachable by any patch"
            )
        if vendor == "fias-pabx":
            try:
                from autobridge.spec.patch import apply_patch as _apply_patch

                patched = _apply_patch(dict(spec), list(patch))
                faulted2, _baseline2 = _faults.beacon_sync_pair(
                    vendor=vendor, eval_seed_hex=str(eval_seed_hex), index=index, spec=patched
                )
            except Exception as exc:
                raise BeaconError(
                    f"preflight: repair instance {index} ({vendor}/{family} "
                    f"{dict(inst.get('params', {}))}): patched sync failed: "
                    f"{type(exc).__name__}: {exc} patch={patch}"
                ) from exc
            if str(faulted2.get("outcome", "")) != "Complete":
                raise BeaconError(
                    f"preflight: repair instance {index} ({vendor}/{family} "
                    f"{dict(inst.get('params', {}))}): patched sync "
                    f"{faulted2.get('outcome')} {faulted2.get('unmet_criteria')} patch={patch}"
                )
            produced = canonical_to_records(faulted2.get("canonical_records", {}))
        else:
            try:
                from autobridge.spec.patch import apply_patch as _apply_patch

                patched = _apply_patch(dict(spec), list(patch))
                with _faulted_transport_for(vendor, str(eval_seed_hex), index, spec) as factory:
                    summary = sync_candidate(patched, "http://localhost:9", client_factory=factory)
            except Exception as exc:
                raise BeaconError(
                    f"preflight: repair instance {index} ({vendor}/{family} "
                    f"{dict(inst.get('params', {}))}): candidate sync failed: "
                    f"{type(exc).__name__}: {exc} patch={patch}"
                ) from exc
            if str(summary.get("outcome", "")) != "Complete":
                raise BeaconError(
                    f"preflight: repair instance {index} ({vendor}/{family} "
                    f"{dict(inst.get('params', {}))}): candidate sync "
                    f"{summary.get('outcome')} {summary.get('unmet_criteria')} patch={patch}"
                )
            produced = canonical_to_records(summary.get("canonical_records", {}))
        if verifier_url:
            try:
                base_records = unfaulted_baseline_records(vendor, str(eval_seed_hex), index, spec)
            except Exception as exc:
                raise BeaconError(
                    f"preflight: repair instance {index} ({vendor}/{family}): "
                    f"baseline failed: {type(exc).__name__}: {exc}"
                ) from exc
            try:
                judged = _verifier_post(
                    verifier_url,
                    "/beacon/records",
                    {"eval_seed_hex": str(eval_seed_hex), "index": index,
                     "records": produced, "spec": patched,
                     "baseline_records": base_records},
                )
            except BeaconError as exc:
                raise BeaconError(f"preflight: repair instance {index}: judge unreachable: {exc}") from exc
            if str(judged.get("verdict", "fail")) != "pass":
                raise BeaconError(
                    f"preflight: repair instance {index} ({vendor}/{family} "
                    f"{dict(inst.get('params', {}))}): reference repair fails the judge: "
                    f"{judged.get('failed_checks', [])} patch={patch}"
                )
        proved.append(index)
    return {"proved": proved}


def preflight_beacon(
    protocol: BeaconProtocol,
    *,
    config: Any,
    eval_seed_hex: str,
    verifier_url: str | None,
    vendors: list[BeaconVendor] | None = None,
    public_urls_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Fail-loud pre-run harness self-test (REQ-083 A).

    Proves the apparatus works before it measures — executed by
    :func:`run_beacon` before the first arm, rehearsals included. For
    every vendor in ``config/beacon_vendors.yaml``:

    1. the pre-drift spec exists and the unfaulted sync runs
       (:func:`unfaulted_baseline_records`);
    2. the candidate-sync wiring resolves: vendor host + spec base path
       (:func:`sync_candidate_url`) and every credential ref the spec's
       auth needs (placeholder bench env active — a missing var fails
       naming it, never as an arm ``http_error`` later);
    3. the unfaulted output passes the records judge (HTTP vendors
       against the oracle; ``fias-pabx`` against its own unfaulted
       output — the one judging regime, wired end to end);
    4. every drawn instance whose key is ``repair`` is proven reachable:
       ``reference_patch_for`` through the real ``sync_candidate`` path
       and the judge (REQ-084 item 2) — a meridian reservation nesting
       was never proven before, and no arm could pass it;

    plus once: both frontier drivers' rendered prompts are non-empty and
    the agent CLI child ``PWD`` equals the sandbox dir. Raises
    :class:`BeaconError` on the first failure; returns the per-vendor
    report on success.
    """
    from autobridge.runtime.auth import build_auth_headers as _build_auth
    from autobridge.spec.models import load_spec_from_dict as _load_spec

    vendor_list = list(vendors) if vendors is not None else load_vendors()
    public = dict(public_urls_map) if public_urls_map is not None else public_urls(vendor_list)
    report: dict[str, Any] = {
        "vendors": {},
        "prompts": {},
        "pwd_ok": False,
        "target_round": protocol.target_round,
    }
    for pos, entry in enumerate(vendor_list):
        vendor = str(entry.vendor)
        desc = {"index": pos, "vendor": vendor}
        spec = engine_spec_for(desc, vendor_list)
        if spec is None:
            raise BeaconError(f"preflight: no pre-drift spec for vendor {vendor!r}")
        try:
            url = sync_candidate_url(spec, public.get(vendor, ""))
        except Exception as exc:
            raise BeaconError(f"preflight: {vendor} candidate URL unresolvable: {exc}") from exc
        if not url:
            raise BeaconError(f"preflight: {vendor} candidate URL is empty")
        try:
            spec_obj = _load_spec(spec)
            if entry.http:
                _build_auth(spec_obj.source.auth)
        except Exception as exc:
            raise BeaconError(f"preflight: {vendor} credentials unresolvable: {exc}") from exc
        # The judged instance must expect a repair: an escalate instance
        # fails the records judge by design (non-empty records for an
        # escalate key), which proves nothing about the apparatus.
        judge_index = _preflight_repair_index(vendor, eval_seed_hex, pos)
        try:
            baseline = unfaulted_baseline_records(vendor, eval_seed_hex, judge_index, spec)
        except Exception as exc:
            raise BeaconError(f"preflight: {vendor} unfaulted sync failed: {type(exc).__name__}: {exc}") from exc
        if verifier_url:
            payload: dict[str, Any] = {
                "eval_seed_hex": eval_seed_hex, "index": judge_index,
                "records": baseline, "spec": None,
            }
            if not entry.http:
                # FIAS rides framed transport: the unfaulted feed output is
                # the comparison baseline (the one judging regime).
                payload["baseline_records"] = baseline
            try:
                judged = _verifier_post(verifier_url, "/beacon/records", payload)
            except BeaconError as exc:
                raise BeaconError(f"preflight: {vendor} judge unreachable: {exc}") from exc
            if str(judged.get("verdict", "fail")) != "pass":
                failed = judged.get("failed_checks", [])
                raise BeaconError(
                    f"preflight: {vendor} unfaulted sync fails the judge: {failed}"
                )
        report["vendors"][vendor] = {"url": url, "records": len(baseline.get("reservations", [])) + len(baseline.get("folios", []))}
    # REQ-084 item 2: prove every drawn repair instance is reachable — the
    # reference repair through the real sync_candidate path and the judge.
    # Runs inside the placeholder credential env (same as the arms); without
    # a verifier URL the sync half still runs and only the judge is skipped.
    try:
        _repair_proof = preflight_prove_repairs(
            protocol, eval_seed_hex=eval_seed_hex, vendors=vendor_list,
            verifier_url=verifier_url,
        )
    except BeaconError:
        raise
    except Exception as exc:
        raise BeaconError(f"preflight: repair proof failed: {type(exc).__name__}: {exc}") from exc
    report["repairs"] = _repair_proof
    # Frontier prompt wiring (A4): both drivers' rendered prompts non-empty.
    from bench.context import messages_to_text as _to_text
    from bench.context import render_frontier_agent_brief as _render_brief
    from bench.context import render_frontier_oneshot_prompt as _render_one
    from bench.harness import make_trial_inputs as _make_inputs

    first = vendor_list[0]
    desc0 = {"index": 0, "vendor": str(first.vendor)}
    case0 = beacon_case_for(desc0, vendor_list)
    if case0 is None:
        raise BeaconError(f"preflight: no bench case for vendor {first.vendor!r}")
    inputs0 = _make_inputs(config, "beacon:preflight", 1)
    try:
        ctx0 = beacon_bench_context(desc0, case0, inputs0, eval_seed_hex)
        one_text = _to_text(_render_one(inputs0, ctx0)).strip()
        brief_text = _to_text(_render_brief(inputs0, ctx0)).strip()
    except Exception as exc:
        raise BeaconError(f"preflight: frontier prompt render failed: {type(exc).__name__}: {exc}") from exc
    if not one_text:
        raise BeaconError("preflight: frontier one-shot prompt rendered empty")
    if not brief_text:
        raise BeaconError("preflight: frontier agent brief rendered empty")
    report["prompts"] = {"oneshot_chars": len(one_text), "agent_chars": len(brief_text)}
    # Agent CLI working directory (A4/REQ-078): the child PWD equals the
    # sandbox dir, or the agent never sees its files.
    import pathlib as _pathlib
    import tempfile as _tempfile

    from bench.baseline_coding_agent import _sandbox_env as _child_env

    with _tempfile.TemporaryDirectory(prefix="beacon-preflight-") as tmpd:
        child = _child_env(_pathlib.Path(tmpd))
        assert child is not None
        if child.get("PWD") != str(tmpd):
            raise BeaconError(f"preflight: agent child PWD {child.get('PWD')!r} != sandbox dir {tmpd!r}")
    report["pwd_ok"] = True
    return report


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
    # REQ-081 item 3: a proposed patch that does not apply is an error
    # and a wrong patch proposed, never "no patch".
    _apply_error = _candidate_apply_error(result, case) if candidate is None else None
    if _apply_error is not None:
        grade.error = _apply_error
        grade.wrong_patch_proposed = 1
    records: dict[str, list[dict[str, Any]]] = {"reservations": [], "folios": []}
    records_verdict: str | None = None
    records_failed: list[str] = []
    synced = False
    candidate_outcome: str | None = None
    # REQ-083 B9: a candidate sync that fails for a harness reason is a
    # harness error with the cause — never a wrong patch proposed and
    # never a pass. Only the arm's own failed patch counts as proposed.
    harness_error: str | None = None
    if candidate is not None and http_sync and public_url:
        try:
            summary = sync_candidate(candidate, public_url)
            records = canonical_to_records(summary.get("canonical_records", {}))
            synced = True
            # REQ-081 item 3: a candidate sync that ends non-Complete is
            # recorded with its outcome, not as a silent loss.
            candidate_outcome = str(summary.get("outcome", ""))
            if candidate_outcome != "Complete":
                records_verdict = "fail"
                unmet = summary.get("unmet_criteria") or [f"sync_{candidate_outcome}"]
                records_failed = [str(unmet[0])[:120]]
                grade.error = (
                    f"candidate sync {candidate_outcome}: "
                    f"{str(unmet[0])[:200]}"
                )[:500]
        except Exception as exc:
            harness_error = f"candidate sync failed: {type(exc).__name__}: {exc}"[:500]
            grade.error = harness_error
    elif candidate is not None and not http_sync and vendor == "fias-pabx":
        # Framed transport has no live HTTP sync: verify the patched spec
        # in-process over the announced feed layout (same serving code the
        # live edge agent runs). REQ-078 item 5: a Complete feed sync alone
        # never passes — the produced records are judged against the
        # baseline like every HTTP repair (fail when the sync is not
        # Complete, judge the records when it is).
        try:
            from bench.faults import beacon_sync_pair as _beacon_sync_pair

            faulted_summary, _baseline_summary = _beacon_sync_pair(
                vendor=vendor, eval_seed_hex=eval_seed_hex, index=index, spec=candidate
            )
            synced = True
            if faulted_summary.get("outcome") == "Complete":
                records = canonical_to_records(faulted_summary.get("canonical_records", {}))
            else:
                records_verdict = "fail"
                unmet = faulted_summary.get("unmet_criteria") or ["sync_not_complete"]
                records_failed = [str(unmet[0])[:120]]
        except Exception as exc:
            harness_error = f"fias candidate verify failed: {type(exc).__name__}: {exc}"[:500]
            grade.error = harness_error
    if synced and records_verdict is None:
        try:
            # REQ-083 A3: one judging regime — the pre-drift spec over the
            # unfaulted simulator is the comparison baseline (HTTP and
            # FIAS alike). Falls back to the oracle dataset when the
            # baseline cannot be computed (graded, never blocked on it).
            baseline_records: dict[str, list[dict[str, Any]]] | None = None
            if case is not None:
                try:
                    baseline_records = unfaulted_baseline_records(
                        vendor, eval_seed_hex, index, dict(case.spec)
                    )
                except Exception:
                    baseline_records = None
            judged = _verifier_post(
                verifier_url,
                "/beacon/records",
                {"eval_seed_hex": eval_seed_hex, "index": index,
                 "records": records, "spec": candidate,
                 "baseline_records": baseline_records},
            )
            records_verdict = str(judged.get("verdict", "fail"))
            failed = judged.get("failed_checks", [])
            if isinstance(failed, list):
                records_failed = [str(f.get("name", ""))[:120] for f in failed if isinstance(f, dict) and f.get("name")]
        except BeaconError as exc:
            harness_error = f"records grade failed: {exc}"[:500]
            grade.error = harness_error
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
    accepted = graded.get("accepted", [])
    if not isinstance(accepted, list):
        accepted = []
    if passed and records_ok:
        grade.verified_completion = 1
        # REQ-083 B9: correct_escalation counts only a Blocked-with-question
        # outcome on an instance whose key accepts a question. A no-patch
        # Complete on a throttle is verified_completion, not an escalation.
        if (
            outcome_report["outcome"] == "Blocked"
            and outcome_report.get("operator_question")
            and ("degraded_question" in accepted or "escalate" in accepted)
        ):
            grade.correct_escalation = 1
    if outcome_report["outcome"] == "Complete" and outcome_report["patch_reached_release"] and not passed:
        grade.wrong_patch_released = 1
    # REQ-078 item 5: a candidate the records judge rejects is a wrong
    # patch proposed — counted whether it reached a release or not.
    # REQ-083 B9: harness failures (sync raised, verifier unreachable)
    # never count here and never grade a patch claim as a pass.
    if candidate is not None and records_verdict == "fail" and harness_error is None:
        grade.wrong_patch_proposed = 1
    if candidate is not None and harness_error is not None and outcome_report["outcome"] == "Complete":
        passed = False
        grade.verified_completion = 0
        grade.correct_escalation = 0


#: Placeholder bench credentials per credential ref (REQ-083 A4: the old
#: tuple covered atlas/harbor/bench only, so meridian raised "Missing env
#: var 'MERIDIAN_CLIENT_TOKEN'" — reported as ``http_error`` — and
#: legacystay had nothing). Values are the simulators' documented demo
#: defaults (``scripts/local-up.sh`` / ``compose.yaml`` install the same),
#: never secrets; real env values win via ``setdefault``.
_PLACEHOLDER_CREDENTIAL_DEFAULTS: dict[str, str] = {
    "ATLAS_API_KEY": BEACON_BENCH_CREDENTIAL,
    "HARBOR_API_KEY": BEACON_BENCH_CREDENTIAL,
    "BENCH_API_KEY": BEACON_BENCH_CREDENTIAL,
    "LEGACYSTAY_API_KEY": BEACON_BENCH_CREDENTIAL,
    "MERIDIAN_CLIENT_TOKEN": "meridian-client-token",
    "MERIDIAN_ACCESS_TOKEN": "meridian-access-token",
}

_PLACEHOLDER_CREDENTIAL_REFS = tuple(_PLACEHOLDER_CREDENTIAL_DEFAULTS)


@contextlib.contextmanager
def _placeholder_credential_env() -> Any:
    """Placeholder bench credentials, restored on exit (REQ-079).

    Engine arms resolve ``${ATLAS_API_KEY}`` / ``${HARBOR_API_KEY}`` / …
    placeholders from the process env at sync time, so the run installs
    non-secret fixture values — but a bare ``setdefault`` leaked them
    process-globally, and later unit tests then synced localhost
    connectors for real (DNS-guard teardown flake). Save/restore around
    the run instead; never leak into the caller's environment.
    """
    saved = {ref: os.environ.get(ref) for ref in _PLACEHOLDER_CREDENTIAL_DEFAULTS}
    for ref, default in _PLACEHOLDER_CREDENTIAL_DEFAULTS.items():
        os.environ.setdefault(ref, default)
    try:
        yield
    finally:
        for ref, value in saved.items():
            if value is None:
                os.environ.pop(ref, None)
            else:
                os.environ[ref] = value


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
        # REQ-078 item 8: scores averaged per instance first, CIs over
        # instances with a bounded interval; n names instances.
        _vc_means, _vc_n = _per_instance_means(items, lambda g: g.verified_completion)
        _ce_means, _ = _per_instance_means(items, lambda g: g.correct_escalation)
        verified = _mean_ci95(_vc_means, bounded=True)
        correct = _mean_ci95(_ce_means, bounded=True)
        verified["n_instances"] = _vc_n
        correct["n_instances"] = _vc_n
        return {
            "n": len(items),
            "verified_completion": verified,
            "wrong_patch_released": {"total": int(sum(g.wrong_patch_released for g in items))},
            "wrong_patch_proposed": {"total": int(sum(g.wrong_patch_proposed for g in items))},
            "correct_escalation": correct,
            "frontier_tokens": _mean_ci95(vals(lambda g: g.frontier_tokens)),
            "small_tokens": _mean_ci95(vals(lambda g: g.small_tokens)),
            "input_tokens": _mean_ci95(vals(lambda g: g.input_tokens)),
            "output_tokens": _mean_ci95(vals(lambda g: g.output_tokens)),
            "reasoning_tokens": _mean_ci95(vals(lambda g: g.reasoning_tokens)),
            # REQ-083 B11: in/out per model tier, one basis for every arm.
            "frontier_input": _mean_ci95(vals(lambda g: g.frontier_input)),
            "frontier_output": _mean_ci95(vals(lambda g: g.frontier_output)),
            "small_input": _mean_ci95(vals(lambda g: g.small_input)),
            "small_output": _mean_ci95(vals(lambda g: g.small_output)),
            "total_tokens": {**_mean_ci95(totals), **_min_med_max(totals)},
            "cost_usd": _mean_ci95([g.cost_usd or 0.0 for g in items]),
            "elapsed_ms": _mean_ci95(vals(lambda g: g.elapsed_ms)),
        }

    def paired_diffs(per_arm_instances: dict[str, dict[int, float]], base: str) -> dict[str, Any]:
        """Paired per-instance arm differences vs the base arm (REQ-078/8)."""
        out: dict[str, Any] = {}
        base_map = per_arm_instances.get(base, {})
        for arm, inst_map in sorted(per_arm_instances.items()):
            if arm == base:
                continue
            shared = sorted(set(base_map) & set(inst_map))
            diffs = [inst_map[i] - base_map[i] for i in shared]
            stat = _mean_ci95(diffs)
            stat["n_instances"] = len(shared)
            out[f"{arm}_minus_{base}"] = stat
        return out

    by_arm = {arm: block([g for g in grades if g.arm == arm]) for arm in arms}
    by_family = {
        family: block([g for g in grades if g.family == family])
        for family in sorted({g.family for g in grades})
    }
    # Per-instance verified means per arm, for the paired differences.
    _arm_inst: dict[str, dict[int, float]] = {}
    for arm in arms:
        _by_inst: dict[int, list[float]] = {}
        for g in grades:
            if g.arm == arm:
                _by_inst.setdefault(int(g.instance_index), []).append(float(g.verified_completion))
        _arm_inst[arm] = {i: sum(v) / len(v) for i, v in _by_inst.items() if v}
    summary: dict[str, Any] = {"overall": block(grades), "by_arm": by_arm, "by_family": by_family}
    if "autobridge_cold" in _arm_inst:
        summary["paired_vs_cold"] = paired_diffs(_arm_inst, "autobridge_cold")
    return summary


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

    REQ-077 item 4: ``autobridge_warm`` is pre-warmed from the dev split
    only (learned knowledge built before the first instance, the same for
    real runs and rehearsals — never from beacon instances), so the
    beacon run measures learning; ``autobridge_cold`` starts empty.
    """
    require_pinned_model(protocol)
    from bench.bench_prompts import ensure_bench_prompts_loaded as _ensure_prompts

    out_dir = pathlib.Path(output_dir) if output_dir else BEACON_DIR / str(protocol.target_round)
    # REQ-078 item 8: a completed run (summary.json) never re-rolls. A
    # directory that holds only protocol.md (rehearsal freeze, then run)
    # starts fresh. A mid-run abort (grades.jsonl without summary.json)
    # resumes: already-graded (index, trial) rows are kept, the rest run.
    if (out_dir / "summary.json").exists():
        raise BeaconError(f"refusing run: {out_dir} already holds results (no re-rolls)")
    _resume_grades: list[dict[str, Any]] = []
    _resume_instances: list[dict[str, Any]] = []
    if (out_dir / "grades.jsonl").exists():
        try:
            for line in (out_dir / "grades.jsonl").read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    _resume_grades.append(json.loads(line))
        except Exception as exc:
            raise BeaconError(f"refusing run: cannot read {out_dir}/grades.jsonl for resume: {exc}") from exc
        if (out_dir / "instances.json").exists():
            try:
                _loaded = json.loads((out_dir / "instances.json").read_text(encoding="utf-8"))
                if isinstance(_loaded, list):
                    _resume_instances = [d for d in _loaded if isinstance(d, dict)]
            except Exception:
                _resume_instances = []
        print(f"beacon run: resuming with {len(_resume_grades)} graded row(s) kept", flush=True)
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
    with _placeholder_credential_env():
        _ensure_prompts()
        cfg = config if config is not None else default_beacon_config()
        # REQ-078 item 7: the frozen budgets win for the run — one token
        # ceiling and one wall clock for every arm (the agent included).
        # A pre-REQ-078 protocol without the lines keeps env/defaults.
        if config is None:
            try:
                import dataclasses as _dc_cfg

                _cfg_kwargs: dict[str, Any] = {}
                if protocol.token_ceiling is not None:
                    _cfg_kwargs["token_ceiling"] = int(protocol.token_ceiling)
                if protocol.wall_clock_s is not None:
                    _cfg_kwargs["wall_clock_s"] = float(protocol.wall_clock_s)
                if _cfg_kwargs:
                    cfg = _dc_cfg.replace(cfg, **_cfg_kwargs)
            except Exception:
                pass
        print(f"beacon run: token_ceiling={cfg.token_ceiling} wall_clock={cfg.wall_clock_s}s", flush=True)
        warm = knowledge_warm
        warm_info: dict[str, Any] = {"source": "caller-supplied", "entries": 0, "fixes": []}
        if warm is None and "autobridge_warm" in protocol.arms:
            from bench.ablation import warm_knowledge as _warm_knowledge
            from bench.ablation import warm_provenance as _warm_provenance
            from bench.harness import list_scenarios as _list_dev

            dev_ids = _list_dev(visibility="dev")
            warm = _warm_knowledge(dev_ids, provider=provider, verifier_url=verifier_url)
            try:
                provenance = _warm_provenance(warm)
                fixes = provenance.get("fixes", [])
            except Exception:
                fixes = []
            warm_info = {
                "source": "dev split only (never beacon instances)",
                "entries": len(fixes),
                "fixes": fixes,
            }
            print(f"beacon run: warm cache holds {len(fixes)} dev-split fix(es) "
                  f"(autobridge_warm pre-warmed; autobridge_cold starts empty)", flush=True)
        elif warm is not None:
            try:
                from bench.ablation import warm_provenance as _warm_provenance

                warm_info = {
                    "source": "caller-supplied",
                    "entries": len(_warm_provenance(warm).get("fixes", [])),
                    "fixes": _warm_provenance(warm).get("fixes", []),
                }
            except Exception:
                warm_info = {"source": "caller-supplied", "entries": 0, "fixes": []}
        else:
            warm_info = {"source": "none (no autobridge_warm arm)", "entries": 0, "fixes": []}
            print("beacon run: no autobridge_warm arm — no warm-up (cold only)", flush=True)
        vendors = load_vendors()
        admin = admin_urls(vendors)
        public = public_urls(vendors)
        # REQ-083 A: harness self-test before the first arm (rehearsals
        # included) — proves the apparatus works before it measures.
        _preflight = preflight_beacon(
            protocol, config=cfg, eval_seed_hex=eval_seed_hex,
            verifier_url=verifier_url, vendors=vendors, public_urls_map=public,
        )
        print(f"beacon run: preflight ok ({len(_preflight['vendors'])} vendors, "
              f"prompts one-shot={_preflight['prompts'].get('oneshot_chars', 0)}ch "
              f"agent={_preflight['prompts'].get('agent_chars', 0)}ch, pwd ok)", flush=True)
        result = BeaconRunResult(protocol=protocol, eval_seed_hex=eval_seed_hex, randomness_hex=verified.randomness_hex)
        # Restore resumed rows (grades keep their frozen order; warm re-learns
        # from newly verified fixes — pre-resume in-run learning is not
        # replayed, and the summary says so).
        _done: set[tuple[int, int]] = set()
        for _row in _resume_grades:
            try:
                _grade = BeaconArmGrade(
                    arm=str(_row.get("arm", "?")),
                    instance_index=int(_row.get("instance_index", -1)),
                    trial=int(_row.get("trial", 0)),
                    vendor=str(_row.get("vendor", "?")),
                    family=str(_row.get("family", "?")),
                )
                for _k, _v in _row.items():
                    if hasattr(_grade, _k):
                        with contextlib.suppress(Exception):
                            setattr(_grade, _k, _v)
                result.grades.append(_grade)
                _done.add((_grade.instance_index, _grade.trial))
            except Exception:
                continue
        _resumed_from = len(result.grades)
        _resume_by_index: dict[int, dict[str, Any]] = {}
        for _d in _resume_instances:
            try:
                _resume_by_index[int(_d.get("index", -1))] = _d
            except Exception:
                continue
        out_dir.mkdir(parents=True, exist_ok=True)
        _grades_path = out_dir / "grades.jsonl"
        if _resume_grades:
            print(
                f"beacon run: resumed ({_resumed_from} rows kept; "
                "warm re-learns from newly verified fixes only)",
                flush=True,
            )
        _aborted: str | None = None
        for index in range(protocol.faults):
            vendor_cfg = vendors[index % len(vendors)]
            if index in _resume_by_index:
                descriptor = _resume_by_index[index]
            else:
                try:
                    descriptor = push_beacon(eval_seed_hex, index, vendor_cfg.vendor, admin_url=admin.get(vendor_cfg.vendor))
                except BeaconError as exc:
                    # Infrastructure error (simulator unreachable): abort the
                    # run without recording a loss — no summary is written, so
                    # a rerun resumes from the landed rows.
                    _aborted = f"infrastructure abort at instance {index}: {exc}"
                    print(f"beacon run: {_aborted}", flush=True)
                    break
                _resume_by_index[index] = descriptor
                with contextlib.suppress(Exception):
                    (out_dir / "instances.json").write_text(
                        json.dumps([_resume_by_index[i] for i in sorted(_resume_by_index)], sort_keys=True, indent=2) + "\n",
                        encoding="utf-8",
                    )
            if index >= len(result.instances):
                result.instances.append(descriptor)
            print(f"beacon run: instance {index} vendor={vendor_cfg.vendor} family={descriptor['family']}", flush=True)
            # REQ-063 item 3: fail loudly before spending any arm budget on an
            # instance the connector cannot distinguish from the baseline.
            try:
                sanity_flags = sanity_unchanged_repairs(eval_seed_hex, [descriptor], vendors)
            except BeaconError as exc:
                _aborted = f"infrastructure abort at instance {index} (sanity check): {exc}"
                print(f"beacon run: {_aborted}", flush=True)
                break
            if sanity_flags:
                flag = sanity_flags[0]
                raise BeaconError(
                    "beacon sanity: instance "
                    f"{flag['index']} ({flag['vendor']}/{flag['family']} {flag['params']}) "
                    f"expects {flag['expected']} but its faulted canonical output equals "
                    "the baseline under the pre-drift spec — generator bug, refusing run"
                )
            for trial in range(protocol.trials):
                if (index, trial) in _done:
                    continue
                try:
                    grades, _reports = run_instance_trial(
                        protocol, eval_seed_hex, descriptor, trial,
                        config=cfg, runners=runners, provider=provider,
                        knowledge_warm=warm, verifier_url=verifier_url,
                        vendors=vendors, vendor_cfg=vendor_cfg, public_url=public.get(vendor_cfg.vendor, ""),
                        # REQ-084 item 3: each arm's model request/response
                        # lands under <round>/traces/ next to grades.jsonl.
                        trace_dir=out_dir,
                    )
                except BeaconError as exc:
                    _aborted = f"infrastructure abort at instance {index} trial {trial}: {exc}"
                    print(f"beacon run: {_aborted}", flush=True)
                    break
                except Exception as exc:
                    # A non-infrastructure crash of one trial also aborts
                    # without recording losses for the unrun rows.
                    _aborted = f"abort at instance {index} trial {trial}: {type(exc).__name__}: {exc}"
                    print(f"beacon run: {_aborted}", flush=True)
                    break
                for _g in grades:
                    result.grades.append(_g)
                    # REQ-078 item 8: each grade row lands as it is graded.
                    try:
                        with open(_grades_path, "a", encoding="utf-8") as _fh:
                            _fh.write(json.dumps(_g.to_dict(), sort_keys=True, default=str) + "\n")
                    except Exception:
                        pass
            if _aborted is not None:
                break
        if _aborted is not None:
            print(f"beacon run: aborted ({len(result.grades)} rows landed; rerun resumes)", flush=True)
            return result
        result.summary = summarize_grades(result.grades)
        result.summary["warm"] = warm_info
        if _resumed_from:
            result.summary["resumed_from_rows"] = _resumed_from
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
    p_run.add_argument("--protocol", default=None,
                       help="explicit protocol path (default: resolve by --round, the old single path, or the single unrun per-round protocol)")
    p_run.add_argument("--round", type=int, default=None,
                       help="target round: use experiments/beacon/<round>/protocol.md")
    p_run.add_argument("--verifier-url", default=None)
    p_run.add_argument("--base-url", default=beacon_client.DRAND_DEFAULT_BASE_URL)
    p_run.add_argument("--output-dir", default=None)
    p_run.add_argument("--token-ceiling", type=int, default=None,
                       help="shared token ceiling (default 200000; 0 = no limit; BENCH_TOKEN_CEILING wins)")
    p_run.add_argument("--wall-clock", type=float, default=None,
                       help="shared wall-clock seconds (default 600; BENCH_WALL_CLOCK wins)")
    p_resolve = sub.add_parser("resolve-protocol",
                               help="print the frozen protocol path a run would use (REQ-077 item 1)")
    p_resolve.add_argument("--protocol", default=None)
    p_resolve.add_argument("--round", type=int, default=None)
    p_round = sub.add_parser("protocol-round",
                             help="print a frozen protocol's target round (the beacon-eval.sh parsing)")
    p_round.add_argument("--protocol", required=True)
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
            protocol_path = resolve_protocol_path(args.protocol, args.round)
            protocol = parse_protocol(protocol_path)
            run_beacon(protocol, verifier_url=args.verifier_url, base_url=args.base_url,
                       output_dir=args.output_dir,
                       config=_cli_config(args.token_ceiling, args.wall_clock))
        elif args.cmd == "resolve-protocol":
            print(resolve_protocol_path(args.protocol, args.round), flush=True)
        elif args.cmd == "protocol-round":
            print(protocol_target_round(args.protocol), flush=True)
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
