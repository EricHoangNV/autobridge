# Project Instructions

## Git commits

Every commit message ends with a `Co-Authored-By` trailer naming the **model** that wrote the code (not the
provider or the runtime), so the history shows which model committed what:

```
Co-Authored-By: <model name> <model-id@models.noreply>
```

Examples: `Co-Authored-By: Muse Spark Open <muse-spark-open-816b-a42b@models.noreply>`,
`Co-Authored-By: Muse Spark 1.3 <muse-spark-1.3@models.noreply>`,
`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` (Claude models keep Anthropic's address).
The ralph loop prints the exact trailer in its prompt (OpenCode: from the pinned model id; Claude Code: from
the model saved by `/model` in the sandbox); use it verbatim on every commit, claim commits included. If the
prompt leaves the name for you to fill in, or in an interactive session, write your own model name. Never write the provider (Baseten, Modal, Meta),
"OpenCode" or "Claude Code" there.

## Where things are

- The plan: `mds/AutoBridge_Implementation_Plan_2026-10-26.md`. Read one § at a time, never the whole file.
- Tasks: `mds/PRD-TRACKING.md`. Rules: `mds/DIRECTIVES.MD` (Quick Reference first; `./directives-for.sh <paths>` for the rest).
- Bookkeeping instructions: `mds/*-INSTRUCTIONS.MD`. Read the matching one before touching a bookkeeping file.

## Trust boundaries

`src/autobridge` (engine), `verifier/`, `simulators/` are separate packages. Engine code never imports from,
reads, or has a network route to the other two (plan §1.2, DIRECTIVES Quick Reference 2).
