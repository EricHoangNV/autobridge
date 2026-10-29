"""`make demo-break SCENARIO=<id> [TARGET=<vendor>]` driver (REQ-031 item 2).

Injects one catalogued fault and prints one line. It never syncs, detects,
repairs or approves; the scheduler and the engine do that, visibly. The
UI's "Demo: inject fault" drawer offers exactly the same list (see
``ui/components/InjectFaultDrawer.tsx``; ``test_autobridge_demo_break.py``
keeps the drawer's options and this module's map in sync with
``bench.demo.CATALOGUE``).

Secrets (DIRECTIVES §6): only ``credential_ref`` env-key names travel here;
no credential value is printed, logged or stored.
"""

from __future__ import annotations

import argparse
from typing import Any

from bench.demo import (
    CATALOGUE_SCENARIOS,
    DEFAULT_CONTROL_URL,
    REPO_ROOT,
    _control_call,
    catalogue_row,
    env_default,
)

#: Scenarios whose recording runs on HarborPMS even though the catalogue
#: vendor is "both": the semantic scenario and its trap twin.
HARBOR_DEFAULT_SCENARIOS = frozenset({"gross_to_net", "invalid_patch_trap"})

#: Catalogue vendor -> canonical simulator target for the generic break path.
_VENDOR_TARGETS = {
    "atlas": "sim-atlas",
    "harbor": "sim-harbor",
    "meridian": "sim-meridian",
    "fias": "sim-fias-pabx",
    "both": "sim-atlas",
}


class BreakError(ValueError):
    """Unusable break request (unknown scenario or target)."""


def supported_scenarios() -> tuple[str, ...]:
    """Every runnable catalogue scenario ``make demo-break`` accepts."""
    return tuple(CATALOGUE_SCENARIOS)


def break_targets() -> dict[str, str]:
    """Accepted TARGET values (canonical ids + aliases) -> canonical id.

    Derived from ``config/demo_targets.yaml`` — the same registry the
    control plane routes by — never hand-kept here.
    """
    import yaml  # noqa: PLC0415

    doc = yaml.safe_load((REPO_ROOT / "config" / "demo_targets.yaml").read_text(encoding="utf-8"))
    mapping: dict[str, str] = {}
    for item in doc.get("targets", []):
        canonical = str(item["id"])
        mapping[canonical.lower()] = canonical
        for alias in item.get("aliases", []):
            mapping[str(alias).lower()] = canonical
    return mapping


def target_display_name(canonical: str) -> str:
    """Registry display name for the one-line print (falls back to the id)."""
    import yaml  # noqa: PLC0415

    try:
        doc = yaml.safe_load((REPO_ROOT / "config" / "demo_targets.yaml").read_text(encoding="utf-8"))
        for item in doc.get("targets", []):
            if str(item.get("id")) == canonical:
                return str(item.get("display_name") or canonical)
    except Exception:
        pass
    return canonical


def default_target_for(scenario: str) -> str:
    """Where a scenario breaks when TARGET is not given (REQ-031 item 2)."""
    row = catalogue_row(scenario)
    if row is None:
        raise BreakError(f"unknown SCENARIO {scenario!r}; runnable: {', '.join(supported_scenarios())}")
    if scenario in HARBOR_DEFAULT_SCENARIOS:
        return "sim-harbor"
    return _VENDOR_TARGETS.get(str(row.get("vendor", "both")), "sim-atlas")


def resolve_break_target(scenario: str, target: str | None) -> str:
    """Validate SCENARIO + TARGET; returns the canonical simulator id."""
    if not scenario or catalogue_row(scenario) is None:
        raise BreakError(f"unknown SCENARIO {scenario!r}; runnable: {', '.join(supported_scenarios())}")
    if not target:
        return default_target_for(scenario)
    mapping = break_targets()
    resolved = mapping.get(target.strip().lower())
    if resolved is None:
        raise BreakError(f"unknown TARGET {target!r}; known: {', '.join(sorted(set(mapping.values())))}")
    return resolved


def run_break(base_url: str, scenario: str, target: str | None, seed: int | None = None) -> dict[str, Any]:
    """POST one inject-fault; returns the record (fail-loud on any failure)."""
    resolved = resolve_break_target(scenario, target)
    rec = _control_call(
        base_url, "POST", "/demo/inject-fault",
        {"target": resolved, "scenario_id": scenario, **({"seed": seed} if seed is not None else {})},
    )
    ok = rec.get("status") == 200
    admin = rec.get("body", {}).get("admin_response") if isinstance(rec.get("body"), dict) else None
    armed = isinstance(admin, dict) and admin.get("scenario_id") == scenario and "error" not in admin
    record = {
        "scenario": scenario,
        "target": resolved,
        "display": target_display_name(resolved),
        "ok": bool(ok and armed),
        "control_call": rec,
    }
    if not record["ok"]:
        record["error"] = f"inject failed (control {rec.get('status')}, admin {admin})"
    return record


def render_line(record: dict[str, Any]) -> str:
    """The one line ``make demo-break`` prints."""
    if record.get("ok"):
        return f"broke {record['display']} with {record['scenario']} — watch Repairs"
    return f"demo-break FAILED: {record.get('error')}"


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for ``make demo-break``."""
    import httpx  # noqa: PLC0415

    parser = argparse.ArgumentParser(description="Inject one catalogued fault (REQ-031 item 2)")
    parser.add_argument("--control-url", default=env_default("CONTROL_URL", DEFAULT_CONTROL_URL))
    parser.add_argument("--scenario", default=env_default("SCENARIO", ""),
                        help="Catalogue scenario id (or SCENARIO=<id>)")
    parser.add_argument("--target", default=env_default("TARGET", ""),
                        help="Simulator target (or TARGET=<vendor>); defaults per scenario")
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args(argv)
    try:
        resolved = resolve_break_target(args.scenario, args.target or None)
    except BreakError as exc:
        print(f"demo-break: {exc}")
        return 2
    try:
        with httpx.Client(timeout=3) as client:
            client.get(args.control_url.rstrip("/") + "/healthz").raise_for_status()
    except Exception as exc:
        print(f"demo-break: control plane unreachable at {args.control_url}: {exc}")
        return 1
    record = run_break(args.control_url, args.scenario, resolved,
                       args.seed if args.seed is not None else None)
    print(render_line(record), flush=True)
    return 0 if record.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
