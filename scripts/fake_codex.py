#!/usr/bin/env python3
"""Fake ``codex exec`` CLI (P1.W8.2, plan §2.10).

Mimics the subset of the Codex headless CLI the bench harness drives::

    codex exec --json "<prompt>" [--model <id>] ...

The fake reads the prompt positional, an optional
``FAKE_CODEX_RESPONSES`` env mapping (JSON dict prompt_substr → text),
and ``FAKE_CODEX_USAGE`` (``"input,output"``) to craft the response.

Output is a single JSON object to stdout matching
:func:`autobridge.bench.baseline_coding_agent.build_fake_codex_output`::

    {"output":"...","usage":{"input_tokens":..,"output_tokens":..},"model":"...","cost_usd":..}

Exit code 0 on success; non-zero on ``FAKE_CODEX_EXIT_CODE`` env.

Usage::

    CODEX_CLI_PATH=scripts/fake_codex.py uv run python -m autobridge.bench.harness
    # or directly:
    python scripts/fake_codex.py exec --json "hello" --model my-model

This script has no network access and never reads real credentials.
"""

from __future__ import annotations

import json
import os
import sys


def _parse_args(argv: list[str]) -> dict[str, str]:
    out: dict[str, str] = {"prompt": "", "model": ""}
    i = 0
    # Skip leading "exec" subcommand if present
    if argv and argv[0] == "exec":
        i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--json":
            # Flag, no value; prompt may be next positional if not yet set
            i += 1
        elif a == "--model" and i + 1 < len(argv):
            out["model"] = argv[i + 1]
            i += 2
        elif a == "--output-format" and i + 1 < len(argv):
            i += 2
        elif not a.startswith("-") and not out["prompt"]:
            out["prompt"] = a
            i += 1
        else:
            i += 1
    # Also handle prompt as last positional if not captured (e.g. codex exec --json "hello")
    # fallback: if prompt still empty, take last non-flag arg
    if not out["prompt"] and argv:
        for tok in reversed(argv):
            if not tok.startswith("-") and tok != "exec":
                out["prompt"] = tok
                break
    return out


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(argv if argv is not None else sys.argv[1:]))
    prompt = args.get("prompt", "")

    exit_code = int(os.environ.get("FAKE_CODEX_EXIT_CODE", "0") or "0")
    if exit_code != 0:
        print(f"fake_codex: forced exit {exit_code}", file=sys.stderr)
        return exit_code

    usage_env = os.environ.get("FAKE_CODEX_USAGE", "")
    input_tokens = 10
    output_tokens = 20
    if usage_env:
        try:
            a, b = usage_env.split(",", 1)
            input_tokens = int(a.strip())
            output_tokens = int(b.strip())
        except Exception:
            pass

    model = args.get("model") or os.environ.get("FAKE_CODEX_MODEL", "") or "fake-codex"

    responses_env = os.environ.get("FAKE_CODEX_RESPONSES", "")
    text = f"fake response for: {prompt}" if prompt else "fake codex response"
    if responses_env:
        try:
            mapping = json.loads(responses_env)
            if isinstance(mapping, dict):
                if prompt in mapping:
                    text = str(mapping[prompt])
                else:
                    for k, v in mapping.items():
                        if k in prompt:
                            text = str(v)
                            break
        except Exception:
            pass

    cost_env = os.environ.get("FAKE_CODEX_COST", "")
    cost = None
    if cost_env:
        try:
            cost = float(cost_env)
        except Exception:
            cost = None

    payload: dict[str, object] = {
        "output": text,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        "model": model,
    }
    if cost is not None:
        payload["cost_usd"] = cost

    if os.environ.get("FAKE_CODEX_VERBOSE", ""):
        print(
            json.dumps({"input_tokens": input_tokens, "output_tokens": output_tokens}),
            file=sys.stderr,
        )

    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
