"""`make demo-reset` driver (REQ-021 item 1).

POSTs ``/demo/reset`` to the control API and prints the per-area summary:
new demo session id, per-simulator resets, L0 knowledge entries dropped,
model-usage rows archived, and control-plane rows removed.

Secrets (DIRECTIVES §6): only the control write token travels (as the
``Authorization`` header when ``AUTOBRIDGE_CONTROL_TOKEN`` is set); the
token value is never printed.
"""

from __future__ import annotations

import json
import os
from typing import Any

from bench.demo import control_headers, env_default

DEFAULT_CONTROL_URL = "http://localhost:8101"


def demo_reset(base_url: str) -> tuple[dict[str, Any], int]:
    """POST /demo/reset; returns (body, exit_code). Never raises."""
    import httpx  # noqa: PLC0415

    url = base_url.rstrip("/") + "/demo/reset"
    try:
        with httpx.Client(timeout=30) as client:
            extra: dict[str, Any] = {}
            if headers := control_headers():
                extra["headers"] = headers
            r = client.post(url, json={}, **extra)
            try:
                return r.json(), 0 if r.status_code == 200 else 1
            except Exception:
                return {"_text": (r.text or "")[:300]}, 1
    except Exception as exc:
        return {"error": str(exc)[:300]}, 1


def main() -> int:
    base_url = env_default("CONTROL_URL", DEFAULT_CONTROL_URL)
    body, code = demo_reset(base_url)
    if code != 0:
        print(json.dumps(body, indent=2, default=str))
        return code
    print(f"demo session: {body.get('session_id')}")
    for entry in body.get("resets", []):
        status = entry.get("status", entry.get("error", "?"))
        print(f"  sim {entry.get('target')}: {status}")
    knowledge = body.get("knowledge_cache", {})
    print(
        f"  knowledge cache: {knowledge.get('entries_before')} -> {knowledge.get('entries_after')}"
    )
    usage = body.get("model_usage", {})
    print(f"  model usage archived: {usage.get('archived')} (session {usage.get('session_id')})")
    removed = body.get("removed", {})
    total = sum(int(v) for v in removed.values()) if isinstance(removed, dict) else 0
    print(f"  control-plane rows removed: {total}")
    _ = os.environ.get("AUTOBRIDGE_CONTROL_TOKEN", "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
