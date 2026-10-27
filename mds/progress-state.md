# Progress

## Current State

Skeleton only (2026-10-27): folder tree per plan §2.2, mds/ bookkeeping, `ralph-afk.sh`. No code, no `pyproject.toml`, no `compose.yaml` yet. The repo is not yet a git repository with an `origin` (owner step before the first loop run).

- Completed: (none)
- Tests: 0 pass / 0 fail / 0 skip (autobridge –, verifier –, simulators –; ui –)
- Stack: `make up` / `make smoke` not available yet (W1)

## Next tasks
1. **P1.W1.1** — Repo scaffold: uv workspace, Makefile targets, lint/typecheck/test with the 90% gate, import-linter contract
2. **P1.W1.2** — compose.yaml with every service, four networks, healthz/readyz, `make up` + `make smoke`
3. **P1.W1.3** — ConnectorSpec v1 models + JSON schema + JSON Patch
- Owner: `git init`, create the GitHub remote, `gh auth login`; create the two Docker sandboxes named in `ralph-afk.sh`; sign Claude in inside `sbx1`; copy `.env.example` to `.env` and fill `SLACK_WEBHOOK` and `ANTHROPIC_API_KEY`
