"""`make record-frontier SCENARIO=<name>` — capture a live frontier climb (REQ-021 item 5).

Runs the hard-semantic climb against the live Muse API (needs
``FRONTIER_API_KEY``; never replay) and writes the redacted recording to
``demo/recordings/<scenario>.json``: prompt sha256 -> {text, tool_calls,
input/output tokens, model, captured_at}. The owner runs this once with
their key and commits the capture; until a capture exists, replay says "no
recording" and the demo labels L3 as skipped instead of inventing an answer.

Prompts carry no credentials by construction (hypothesis ids, drift class
and ranking only), and only the model's own output text is stored.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
from typing import Any

from autobridge.llm.provider import (  # noqa: PLC0415
    Budget,
    Completion,
    MuseProvider,
    prompt_hash,
)
from autobridge.repair.knowledge import KnowledgeCache  # noqa: PLC0415
from autobridge.repair.ladder import run_frontier_climb  # noqa: PLC0415

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = REPO_ROOT / "demo" / "recordings"


class TappingProvider:
    """Wraps a live provider, recording (prompt hash -> redacted response)."""

    def __init__(self, inner: Any, *, model: str) -> None:
        self.inner = inner
        self.model = model
        self.recordings: dict[str, dict[str, Any]] = {}

    def model_for_tier(self, tier: str) -> str:
        return self.inner.model_for_tier(tier)

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 4096,
        tier: str = "small",
        purpose: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> Completion:
        out = self.inner.complete(
            messages, max_tokens=max_tokens, tier=tier, purpose=purpose, tools=tools
        )
        key = prompt_hash(messages)
        self.recordings[key] = {
            "text": out.text,
            "tool_calls": [
                {"id": tc.id, "name": tc.name, "arguments": dict(tc.arguments)}
                for tc in out.tool_calls
            ],
            "input_tokens": out.usage.input_tokens,
            "output_tokens": out.usage.output_tokens,
            "model": self.model,
            "captured_at": datetime.date.today().isoformat(),
        }
        return out


def record_scenario(scenario: str, out_dir: pathlib.Path) -> dict[str, Any]:
    """Run the live climb and write the recording file. Returns a summary."""
    if scenario != "gross_to_net":
        raise SystemExit(f"record-frontier: unknown SCENARIO {scenario!r} (only gross_to_net is climbable)")
    api_key = (os.environ.get("FRONTIER_API_KEY", "") or "").strip()
    if not api_key:
        raise SystemExit("record-frontier: FRONTIER_API_KEY is required (generate a dedicated Muse key)")
    from autobridge.config import frontier_model  # noqa: PLC0415
    from bench.demo import _semantic_climb_inputs  # noqa: PLC0415 (shared engine-derived climb inputs)

    model = frontier_model()
    live = MuseProvider(api_key=api_key)
    tap = TappingProvider(live, model=model)
    # Same detector-derived inputs the demo climbs (REQ-027 item 4): the
    # capture matches the demo's prompt, never a hand-built one.
    inp = _semantic_climb_inputs()
    climb = run_frontier_climb(
        spec=inp["spec"],
        drift_class=inp["drift_class"],
        hypotheses=list(inp["hypotheses"]),
        enumerator_yield_count=inp["yield_count"],
        provider=tap,
        budget=Budget(),
        knowledge_cache=KnowledgeCache(),
        fingerprint_delta=inp["fingerprint_delta"],
        vendor="harbor",
        scenario=scenario,
        validate_against=inp["spec"],
        evidence=dict(inp["evidence"]),
    )
    if not tap.recordings:
        raise SystemExit("record-frontier: live climb made no model calls; nothing to record")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{scenario}.json"
    payload: dict[str, Any] = {
        "_note": (
            f"Live Muse capture for the {scenario} hard-semantic climb "
            f"(model {model}), recorded by make record-frontier. Redacted: prompts are "
            "stored as sha256 hashes only (they carry hypothesis ids, never credentials)."
        )
    }
    payload.update(tap.recordings)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "scenario": scenario,
        "path": str(path),
        "model": model,
        "prompts_recorded": len(tap.recordings),
        "resolved_rung": climb["resolved_rung"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture a live frontier climb as a replay recording")
    parser.add_argument("scenario", nargs="?", default=os.environ.get("SCENARIO", ""))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    args = parser.parse_args(argv)
    if not args.scenario:
        raise SystemExit("record-frontier: SCENARIO is required (make record-frontier SCENARIO=gross_to_net)")
    summary = record_scenario(args.scenario, pathlib.Path(args.out_dir))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
