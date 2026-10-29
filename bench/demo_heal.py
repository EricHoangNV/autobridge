"""`make demo-heal [TARGET=<vendor>]` driver (REQ-031 item 3).

Clears injected faults without a reset: simulator scenario slots back to
baseline (including the FIAS link-engine extras) while connectors, runs,
drift, repairs, knowledge and the ledger stay untouched — retakes of one
scenario start from here. Implemented as a sims-only ``POST /demo/reset``,
so the heal path and the reset path can never disagree about what
"simulator baseline" means.

Secrets (DIRECTIVES §6): only the control write token travels (as the
``Authorization`` header when ``AUTOBRIDGE_CONTROL_TOKEN`` is set); the
token value is never printed.
"""

from __future__ import annotations

import argparse
from typing import Any

from bench.demo import DEFAULT_CONTROL_URL, _control_call, env_default
from bench.demo_break import break_targets


def resolve_heal_targets(target: str | None) -> list[str] | None:
    """Validate TARGET; None heals every simulator, else the one named."""
    if not target:
        return None
    mapping = break_targets()
    resolved = mapping.get(target.strip().lower())
    if resolved is None:
        known = ", ".join(sorted(set(mapping.values())))
        raise ValueError(f"unknown TARGET {target!r}; known: {known}")
    return [resolved]


def run_heal(base_url: str, target: str | None) -> dict[str, Any]:
    """POST the sims-only reset; returns the record (fail-loud on failure)."""
    targets = resolve_heal_targets(target)
    payload: dict[str, Any] = {"sims_only": True}
    if targets is not None:
        payload["targets"] = targets
    rec = _control_call(base_url, "POST", "/demo/reset", payload)
    ok = rec.get("status") == 200
    body = rec.get("body") if isinstance(rec.get("body"), dict) else {}
    resets = body.get("resets") if isinstance(body, dict) else None
    record: dict[str, Any] = {
        "targets": targets or "all",
        "ok": bool(ok and isinstance(body, dict) and body.get("sims_only") is True),
        "resets": resets,
        "control_call": rec,
    }
    if not record["ok"]:
        record["error"] = f"heal failed (control {rec.get('status')}: {rec.get('body') or rec.get('error')})"
    return record


def render_text(record: dict[str, Any]) -> str:
    """Short receipt: one line per sim plus the healed line."""
    lines: list[str] = []
    resets = record.get("resets")
    if isinstance(resets, list):
        for entry in resets:
            if not isinstance(entry, dict):
                continue
            status = entry.get("status", entry.get("error", "?"))
            lines.append(f"  sim {entry.get('target')}: {status}")
    if record.get("ok"):
        lines.append("healed — simulators back to baseline; drift clears on the next scheduled sync")
    else:
        lines.append(f"demo-heal FAILED: {record.get('error')}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for ``make demo-heal``."""
    import httpx  # noqa: PLC0415

    parser = argparse.ArgumentParser(description="Clear injected faults without a reset (REQ-031 item 3)")
    parser.add_argument("--control-url", default=env_default("CONTROL_URL", DEFAULT_CONTROL_URL))
    parser.add_argument("--target", default=env_default("TARGET", ""),
                        help="Simulator target (or TARGET=<vendor>); default heals all")
    args = parser.parse_args(argv)
    try:
        target = args.target or None
        resolve_heal_targets(target)
    except ValueError as exc:
        print(f"demo-heal: {exc}")
        return 2
    try:
        with httpx.Client(timeout=3) as client:
            client.get(args.control_url.rstrip("/") + "/healthz").raise_for_status()
    except Exception as exc:
        print(f"demo-heal: control plane unreachable at {args.control_url}: {exc}")
        return 1
    record = run_heal(args.control_url, target)
    print(render_text(record), flush=True)
    return 0 if record.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
