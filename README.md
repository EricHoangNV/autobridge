# AutoBridge

Generic Connect + Heal engine: compiles declarative connectors from vendor docs, executes
them on a schedule, detects drift when a vendor changes, repairs the connector through a
deterministic-first ladder (L0 knowledge → L1 rules → L2 small model → L3 frontier model →
L4 human), and verifies every repair behind a separate trust boundary before it ships.

Money is `Decimal` end to end; dates are property-local business dates; every `SyncRun`
ends `Complete | Degraded | Blocked` with the unmet criterion named.

## Quickstart (clean machine: Docker + `uv` + Node 22/`npm` for the UI)

```bash
cp .env.example .env        # fill in provider keys; secrets stay in .env (git-ignored)
make ui-deps                # one-time UI install (`cd ui && npm ci`; also auto-run by `make test`/`make e2e-ui`)
make up                     # build images, start the stack, wait for every /readyz
make smoke                  # healthz/readyz + trust-boundary network probes
make test                   # unit, ≥90% coverage per Python package
make e2e                    # tests/e2e/test_00 … test_07, random order
make e2e-ui                 # Playwright UI journey (needs `make up` first)
make bench                  # heldout sha check → matched-budget run → experiments/scorecard.md
make demo                   # six demo steps through the control API (plan §6)
make down                   # docker compose down -v
```

With the stack up, each simulated vendor has its own documentation site, rendered live from the files
AutoBridge reads in `simulators/docs/`:

| Vendor | Docs site | Source |
|---|---|---|
| AtlasPMS (clean OpenAPI) | http://localhost:8105/developers | `atlas-openapi.yaml` |
| HarborPMS (messy wiki) | http://localhost:8106/developers | `harbor.md` |
| LegacyStay (2000s website) | http://localhost:8111/developers | `legacystay.md` |

CI (`.github/workflows/ci.yml`) runs the same order from a clean clone:
`make test → make up → make smoke → make e2e → make e2e-ui → make demo`.

## How it works

1. **Connect** — a vendor's docs become a declarative `ConnectorSpec` (YAML/JSON) via
   bootstrap (docs → hypotheses → probes → evidence → spec), or start from a reference
   spec under `generated/connectors/<id>/reference/`.
2. **Execute** — the runtime interprets the spec (`GET` operations, auth by
   `credential_ref`, pagination with guards, closed transform set, per-entity checks)
   and records a `SyncRun` with outcome, `data_readiness`, `fingerprint_id` and
   checkpoint. Baseline syncs use zero model calls.
3. **Detect** — fingerprint + classifier assign one of 12 drift classes
   (`FIELD_RENAME`, `PAGINATION_CHANGE`, `SEMANTIC_VALUE_DRIFT`, …).
4. **Repair** — the ladder tries L0 knowledge, then 7 deterministic L1 rules
   (JSON Patch on the spec), then budgeted L2/L3 model calls, else escalates with a
   bundle (exactly one question + redacted evidence). Known patterns repair with
   `frontier_tokens == 0`.
5. **Verify** — the separate verifier service (own package, image, network) runs the
   oracle + canonical checks in visible or hidden mode and returns only bounded
   `failed_checks` names/counts + score — never fixture rows. A regression gate
   re-runs previously verified scenarios before a release persists; the
   intentionally-wrong patch (`invalid_patch_trap`) is always rejected.
6. **Ship** — approve / reject / rollback releases; canary monitor + consumer
   pause/notify (`DependencyRegistration`) on `release.activated`.

Design principles (plan §1): repairs are data (JSON Patch, no generated code);
three trust boundaries (`src/autobridge`, `verifier/`, `simulators/` — separate
packages, images, Docker networks; the engine never imports or reads the other two);
deterministic first; success is a typed outcome, not a 200; coverage ≥ 90% per
package; every service has `/healthz` + `/readyz`; secrets only as `credential_ref`.

## Layout

```
autobridge/
├── README.md  compose.yaml  .env.example  Makefile  pyproject.toml (uv workspace)
├── docs/            architecture.md  connector-spec.md  safety-model.md  benchmark.md  demo.md
├── schemas/         reservation / folio-charge / connector-spec.v1 / runtime-target / discovery-result.v0
├── src/autobridge/  spec/ runtime/ discovery/ drift/ repair/ llm/ evidence/ control/ engine/ bench/
├── verifier/        own pyproject; oracle, canonical checks, hidden_cases/{dev,heldout}
├── simulators/      own pyproject; common/ atlaspms/ harborpms/ docs/ scenarios/ (14 YAMLs)
├── generated/       connectors/<id>/<release>/spec.yaml + build-report.json
├── ui/              Next.js 15 + TypeScript + Tailwind; lib/api (openapi-typescript client)
├── experiments/     benchmark_runs/*.jsonl  scorecard.md
├── mcp_stretch/     Phase 2
├── tests/           unit/  e2e/ (test_00 … test_07 + test_06 Playwright spec)
├── scripts/         wait_readyz.sh  smoke.sh  fake_claude.py  fake_codex.py
├── mds/             planning + agent bookkeeping (plan, PRD index, tracking, notes, directives)
└── alembic/         migrations for the core data model
```

## Docs

| Page | Covers |
|---|---|
| `docs/architecture.md` | Services, networks, health contracts, data model, ladder, e2e harness, control API + UI |
| `docs/connector-spec.md` | ConnectorSpec v1 reference: source/auth, operations, pagination, entities, closed transforms, checks, JSON Patch |
| `docs/safety-model.md` | Verifier trust boundary, oracle, scenario catalog, semantic invariants, regression gate, escalation, guarantees |
| `docs/benchmark.md` | Matched-budget harness, baseline drivers, scoring, `make bench` pipeline, heldout freeze runbook |
| `docs/demo.md` | Six demo steps (plan §6) through the control API, with the evidence each step renders |

## The autonomous loop

```
cp .env.example .env                       # SLACK_WEBHOOK, provider keys, model ids
sh ralph-afk.sh sbx1 10                    # default: OpenCode on Meta (RALPH_AGENT / RALPH_PROVIDER)
sh ralph-afk.sh --agent claude sbx1 10     # Claude Code only
sh ralph-afk.sh --agent opencode --provider meta --model muse-wonder-1.0 sbx-mini 5
sh ralph-afk.sh --validate sbx-mini 1      # daily directives audit
```

Ralph (planning, iterations, checkpoints, validation) drives one coding-agent runtime for
the whole run: Claude Code in its own sandbox, or OpenCode in its own sandbox on one
provider (Baseten Spark Open, Modal Spark Open, or Meta Spark 1.3 / Contributor / Wonder).
There is no fallback: an auth, credit, provider or empty-run failure stops the loop. The verification gates
(`make lint / typecheck / test`, the workstream's e2e file, the mds/ bookkeeping) are the
same whichever runtime ran. Setup and the provider table: `mds/DEPLOY.md`.

Prerequisites before the first run: `git init` + a GitHub `origin` with a `main` branch
(the loop works in `.worktrees/<sbx>` off `origin/main` and pushes there),
`gh auth login`, Docker Sandboxes (`sbx`) with the four sandboxes named in `mds/DEPLOY.md`,
Claude signed in inside `claude-autobridge`, network policy allowing the three provider
hosts.

## mds/ files

| File | Role | Who reads it |
|---|---|---|
| `AutoBridge_Implementation_Plan_2026-10-26.md` | The plan (design + build order). Stable. | Agents, one § at a time |
| `PRD.MD` | Index into the plan; owner-only decisions log | Agents, by § |
| `PRD-TRACKING.md` | Task manifest (`[ ] [>] [~] [x]`) — pick and claim work here | Every loop iteration |
| `BUILD-NOTES.md` | Per-task build notes, keyed by task id | On demand |
| `progress-state.md` / `progress-log.md` | Current state (≤4 KB, in every prompt) / append-only history | Every iteration / on demand |
| `DIRECTIVES.MD` (+ `-INSTRUCTIONS`) | Rules; Quick Reference is in every prompt, § sections via `./directives-for.sh` | Every iteration |
| `LEARNINGS.MD` (+ `-INSTRUCTIONS`) | Non-obvious things that bit us | Before writing a learning |
| `REQUESTS.MD` (+ `-INSTRUCTIONS`) | Owner requests and bugs, open section in every prompt | Every iteration |
| `FEEDBACKS.MD` (+ `-INSTRUCTIONS`) | Review verdicts (security / code / product) | Every iteration (verdicts only) |
| `AUDIT.md` | Last directives audit marker + findings | `--validate` runs |
| `DEPLOY.md` | Runbook (clean-clone check, demo recording) | Owner |
