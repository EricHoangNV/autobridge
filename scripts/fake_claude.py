#!/usr/bin/env python3
"""Fake ``claude -p`` CLI (P1.W1.9, plan §9).

Mimics the subset of the Claude Code headless CLI the bench harness
drives::

    claude -p "<prompt>" --output-format json [--model <id>] ...

The fake reads ``-p`` / ``--print`` prompt, an optional
``FAKE_CLAUDE_RESPONSES`` env mapping (JSON dict prompt_substr → text),
and ``FAKE_CLAUDE_USAGE`` (``"input,output"``) to craft the response.

Output is a single JSON object to stdout matching
:func:`bench.baseline_coding_agent.build_fake_claude_output`::

    {"type":"result","result":"...","usage":{"input_tokens":..,"output_tokens":..},"model":"...","cost_usd":..}

Exit code 0 on success; 1 on ``FAKE_CLAUDE_EXIT_CODE`` env.

Usage::

    CLAUDE_CLI_PATH=scripts/fake_claude.py uv run python -m bench.harness
    # or directly:
    python scripts/fake_claude.py -p "hello" --output-format json

This script has no network access and never reads real credentials.
"""

from __future__ import annotations

import json
import os
import sys


def _parse_args(argv: list[str]) -> dict[str, str]:
    out: dict[str, str] = {"prompt": "", "model": ""}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-p", "--print") and i + 1 < len(argv):
            out["prompt"] = argv[i + 1]
            i += 2
        elif a == "--model" and i + 1 < len(argv):
            out["model"] = argv[i + 1]
            i += 2
        elif a == "--output-format" and i + 1 < len(argv):
            out["output_format"] = argv[i + 1]
            i += 2
        else:
            i += 1
    return out


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(argv if argv is not None else sys.argv[1:]))
    prompt = args.get("prompt", "")

    # Allow tests to force a failure
    exit_code = int(os.environ.get("FAKE_CLAUDE_EXIT_CODE", "0") or "0")
    if exit_code != 0:
        print(f"fake_claude: forced exit {exit_code}", file=sys.stderr)
        return exit_code

    # Usage override via env "10,20"
    usage_env = os.environ.get("FAKE_CLAUDE_USAGE", "")
    input_tokens = 10
    output_tokens = 20
    if usage_env:
        try:
            a, b = usage_env.split(",", 1)
            input_tokens = int(a.strip())
            output_tokens = int(b.strip())
        except Exception:
            pass

    model = args.get("model") or os.environ.get("FAKE_CLAUDE_MODEL", "") or "fake-claude"

    # Prompt → response mapping via env JSON
    responses_env = os.environ.get("FAKE_CLAUDE_RESPONSES", "")
    text = f"fake response for: {prompt}" if prompt else "fake claude response"
    if responses_env:
        try:
            mapping = json.loads(responses_env)
            if isinstance(mapping, dict):
                # Direct hit first
                if prompt in mapping:
                    text = str(mapping[prompt])
                else:
                    for k, v in mapping.items():
                        if k in prompt:
                            text = str(v)
                            break
        except Exception:
            pass

    # Cost via env
    cost_env = os.environ.get("FAKE_CLAUDE_COST", "")
    cost = None
    if cost_env:
        try:
            cost = float(cost_env)
        except Exception:
            cost = None

    payload: dict[str, object] = {
        "type": "result",
        "result": text,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        "model": model,
    }
    if cost is not None:
        payload["cost_usd"] = cost

    # Emit usage to stderr as well (mirrors real CLI verbose flag) if requested
    if os.environ.get("FAKE_CLAUDE_VERBOSE", ""):
        print(
            json.dumps({"input_tokens": input_tokens, "output_tokens": output_tokens}),
            file=sys.stderr,
        )

    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
