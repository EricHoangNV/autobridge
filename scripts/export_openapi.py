#!/usr/bin/env python3
"""Export the control plane OpenAPI schema to ui/openapi.json (P1.W7.3).

Run: uv run python scripts/export_openapi.py [--check]

--check: fail if the on-disk ui/openapi.json differs from the live schema
(used in CI to ensure the typed client was regenerated).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "ui" / "openapi.json"


def get_schema() -> dict:
    from autobridge.control.app import app

    return app.openapi()  # type: ignore[no-untyped-call]


def main() -> int:
    p = argparse.ArgumentParser(description="Export control OpenAPI to ui/openapi.json")
    p.add_argument("--check", action="store_true", help="fail if on-disk file is stale")
    args = p.parse_args()

    schema = get_schema()
    rendered = json.dumps(schema, indent=2, sort_keys=True) + "\n"

    if args.check:
        if not OUT.exists():
            print(f"FAIL: {OUT} missing — run: uv run python scripts/export_openapi.py", file=sys.stderr)
            return 1
        existing = OUT.read_text(encoding="utf-8")
        # Compare parsed to avoid whitespace-only diffs but report as stale
        if json.loads(existing) != schema:
            print(f"FAIL: {OUT} is stale — run: uv run python scripts/export_openapi.py", file=sys.stderr)
            return 1
        print(f"ok: {OUT} up to date ({len(schema['paths'])} paths)")
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(rendered, encoding="utf-8")
    print(f"wrote {OUT} ({len(schema['paths'])} paths, {len(schema.get('components', {}).get('schemas', {}))} schemas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
