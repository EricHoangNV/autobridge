# Project Instructions

## Git commits

Every commit message ends with a `Co-Authored-By` trailer naming the **model** that wrote the code (not the
provider or the runtime), so the history shows which model committed what:

```
Co-Authored-By: <model name> <model-id@models.noreply>
```

Examples: `Co-Authored-By: Muse Spark Open <muse-spark-open-1026@models.noreply>`,
`Co-Authored-By: Muse Spark 1.3 <muse-spark-1.3@models.noreply>`,
`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` (Claude models keep Anthropic's address).
The ralph loop prints the exact trailer in its prompt (OpenCode: from the pinned model id; Claude Code: from
the model saved by `/model` in the sandbox); use it verbatim on every commit, claim commits included. If the
prompt leaves the name for you to fill in, write your own model name. Commits made directly from the owner's
Claude Code session on the host (outside the ralph loop) carry **no** `Co-Authored-By` trailer at all. Never write the provider (Baseten, Modal, Meta),
"OpenCode" or "Claude Code" there.

The commit **author** is always the repo owner's git identity, set by the loop. Never run `git config user.name` /
`user.email` or pass `--author`: worktrees share one `.git/config`, so that would relabel every later commit.

## Where things are

- The plan: `mds/AutoBridge_Implementation_Plan_2026-10-26.md`. Read one § at a time, never the whole file.
- Tasks: `mds/PRD-TRACKING.md`. Rules: `mds/DIRECTIVES.MD` (Quick Reference first; `./directives-for.sh <paths>` for the rest).
- Bookkeeping instructions: `mds/*-INSTRUCTIONS.MD`. Read the matching one before touching a bookkeeping file.

## Trust boundaries

`src/autobridge` (engine), `verifier/`, `simulators/` are separate packages. Engine code never imports from,
reads, or has a network route to the other two (plan §1.2, DIRECTIVES Quick Reference 2).
