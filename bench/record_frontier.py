"""`make record-frontier SCENARIO=<name>` — capture the live semantic prompt (REQ-039).

Runs the hard-semantic L3 prompt that the *live repair path* recorded on
its rung (``POST /sync`` → detect → climb → escalate) against the live
Muse API (needs ``FRONTIER_API_KEY``; never replay) and writes the
redacted recording to ``demo/recordings/<scenario>.json``:
prompt sha256 -> {text, tool_calls, input/output/reasoning tokens, model,
captured_at}. The owner runs this once with their key and commits the
capture; until a capture exists, replay says "no recording" and the demo
labels L3 as skipped instead of inventing an answer.

The prompt comes from the repair row itself (rung 3 ``messages``), never
reconstructed — so the capture byte-matches what the live climb asked and
replay serves it back. Owner flow (keyed stack): ``make demo-seed`` (or a
keyed ``make demo`` step 4) leaves the semantic repair with the recorded
prompt; then ``make record-frontier SCENARIO=gross_to_net`` captures it.

Prompts carry no credentials by construction (hypothesis ids, drift class
and redacted evidence only), and only the model's own output text is stored.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import urllib.request
from typing import Any

from autobridge.llm.provider import prompt_hash  # noqa: PLC0415

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = REPO_ROOT / "demo" / "recordings"


def _api_get(base_url: str, path: str) -> Any:
    req = urllib.request.Request(base_url.rstrip("/") + path, method="GET")
    token = (os.environ.get("AUTOBRIDGE_CONTROL_TOKEN", "") or "").strip()
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _entry(out: Any, model: str) -> dict[str, Any]:
    """One redacted recording entry (model output only, never the prompt)."""
    return {
        "text": out.text,
        "tool_calls": [
            {"id": tc.id, "name": tc.name, "arguments": dict(tc.arguments)}
            for tc in out.tool_calls
        ],
        "input_tokens": out.usage.input_tokens,
        "output_tokens": out.usage.output_tokens,
        "reasoning_tokens": out.usage.reasoning_tokens,
        "model": model,
        "captured_at": datetime.date.today().isoformat(),
    }


def _live_l3_prompt(base_url: str) -> tuple[str, str, list[dict[str, Any]]]:
    """Newest semantic escalation whose rung 3 carries the exact L3 prompt.

    Returns (repair_id, drift_class, messages). Fails loudly when no live
    climb has recorded a prompt yet (break first, in any mode — even an
    unkeyed break records the prompt on its explicit L3 skip).
    """
    repairs = _api_get(base_url, "/repairs") or []
    if not isinstance(repairs, list):
        raise SystemExit(f"record-frontier: GET /repairs returned {type(repairs).__name__}")
    candidates = [
        r
        for r in repairs
        if isinstance(r, dict)
        and r.get("drift_class") == "SEMANTIC_VALUE_DRIFT"
        and r.get("decision") == "escalate"
    ]
    candidates.sort(key=lambda r: str(r.get("created_at", "")), reverse=True)
    for rep in candidates:
        for attempt in rep.get("ladder_trace") or []:
            if (
                isinstance(attempt, dict)
                and attempt.get("level") == 3
                and isinstance(attempt.get("messages"), list)
                and attempt["messages"]
            ):
                return str(rep.get("id")), "SEMANTIC_VALUE_DRIFT", list(attempt["messages"])
    raise SystemExit(
        "record-frontier: no live semantic repair with an L3 prompt found; "
        "run `make demo-break SCENARIO=gross_to_net TARGET=harbor` (any mode) "
        "and sync once first, then re-run"
    )


def record_scenario(
    scenario: str, out_dir: pathlib.Path, control_url: str | None = None
) -> dict[str, Any]:
    """Call the live model with the recorded prompts; write the capture."""
    if scenario != "gross_to_net":
        raise SystemExit(f"record-frontier: unknown SCENARIO {scenario!r} (only gross_to_net is climbable)")
    api_key = (os.environ.get("FRONTIER_API_KEY", "") or "").strip()
    if not api_key:
        raise SystemExit("record-frontier: FRONTIER_API_KEY is required (generate a dedicated Muse key)")
    from autobridge.config import frontier_model  # noqa: PLC0415
    from autobridge.llm.provider import MuseProvider  # noqa: PLC0415

    model = frontier_model()
    live = MuseProvider(api_key=api_key)
    base = (
        control_url
        or (os.environ.get("CONTROL_URL", "") or "").strip()
        or "http://localhost:8101"
    )
    captured: dict[str, dict[str, Any]] = {}

    # 1. The live-stack prompt, exactly as the break's climb recorded it on
    # rung 3 (byte-exact: the capture replays for the live repair path).
    repair_id, _drift_class, messages = _live_l3_prompt(base)
    out = live.complete(
        messages, max_tokens=8192, tier="frontier", purpose="record-frontier-capture"
    )
    captured[prompt_hash(messages)] = _entry(out, model)

    # 2. The instrumented in-process prompt (same shared builder, fake wire
    # data): the demo's printed derivation replays the same capture shape.
    # The prompts differ only in the failing-records sample (seeded sim
    # vs fake client), so both hashes are captured in one run.
    from autobridge.llm.provider import Budget  # noqa: PLC0415
    from autobridge.repair.knowledge import KnowledgeCache  # noqa: PLC0415
    from autobridge.repair.ladder import run_frontier_climb  # noqa: PLC0415
    from bench.demo import _semantic_climb_inputs  # noqa: PLC0415

    inp = _semantic_climb_inputs()
    climb = run_frontier_climb(
        spec=inp["spec"],
        drift_class=inp["drift_class"],
        hypotheses=list(inp["hypotheses"]),
        enumerator_yield_count=inp["yield_count"],
        provider=live,
        budget=Budget(),
        session=None,
        run_id=None,
        knowledge_cache=KnowledgeCache(),
        fingerprint_delta=inp["fingerprint_delta"],
        vendor="harbor",
        scenario=scenario,
        validate_against=inp["spec"],
        evidence=dict(inp["evidence"]),
    )
    if not climb.get("l3_text"):
        raise SystemExit(
            "record-frontier: live model returned no text for the in-process prompt; nothing to record"
        )
    from autobridge.llm.provider import Completion, Usage  # noqa: PLC0415

    usage = climb.get("usage") or {}
    captured[prompt_hash(climb["l3_messages"])] = _entry(
        Completion(
            text=climb["l3_text"],
            tool_calls=[],
            usage=Usage(
                int(usage.get("frontier_input", 0) or 0),
                int(usage.get("frontier_output", 0) or 0),
                reasoning_tokens=int(usage.get("frontier_reasoning", 0) or 0),
            ),
            replayed=False,
            captured_at="",
        ),
        model,
    )
    repair_note = repair_id
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{scenario}.json"
    payload: dict[str, Any] = {}
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                payload = {}
        except Exception:
            payload = {}
    payload.setdefault(
        "_note",
        f"Live Muse capture for the {scenario} live semantic climb "
        f"(model {model}), recorded by make record-frontier. Redacted: prompts are "
        "stored as sha256 hashes only (they carry hypothesis ids, never credentials).",
    )
    payload.update(captured)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "scenario": scenario,
        "path": str(path),
        "model": model,
        "repair_id": repair_note,
        "prompts_recorded": len(captured),
        "prompt_hashes": sorted(captured),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture the live semantic L3 prompt")
    parser.add_argument("scenario", nargs="?", default=os.environ.get("SCENARIO", ""))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument(
        "--control-url",
        default=(os.environ.get("CONTROL_URL", "") or "").strip() or "http://localhost:8101",
    )
    args = parser.parse_args(argv)
    summary = record_scenario(args.scenario, pathlib.Path(args.out_dir), args.control_url)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
