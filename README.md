# AutoBridge

Generic Connect + Heal engine: compiles declarative connectors from vendor docs, detects drift, repairs
it through a deterministic-first ladder (L0 knowledge → L1 rules → L2 small model → L3 frontier model →
L4 human), and verifies every repair behind a separate trust boundary.

**Status:** skeleton only. No code yet. The plan is `mds/AutoBridge_Implementation_Plan_2026-10-26.md`;
the task board is `mds/PRD-TRACKING.md`.

## Layout (plan §2.2)

```
autobridge/
├── README.md  compose.yaml  .env.example  Makefile  pyproject.toml (uv workspace)   ← W1
├── docs/            architecture.md  connector-spec.md  safety-model.md  benchmark.md  demo.md
├── schemas/         reservation / folio-charge / connector-spec.v1 / discovery-result.v0 / runtime-target
├── src/autobridge/  spec/ runtime/ discovery/ drift/ repair/ llm/ evidence/ control/ engine/ bench/
├── verifier/        own pyproject; oracle, canonical checks, mutation tests, hidden_cases/{dev,heldout}
├── simulators/      own pyproject; common/ atlaspms/ harborpms/ legacystay/ docs/ scenarios/
├── generated/       connectors/<id>/<release>/spec.yaml + build-report.json
├── ui/              Next.js 15 + TypeScript + Tailwind; lib/api (openapi-typescript client)
├── experiments/     benchmark_runs/*.jsonl  scorecard.md
├── mcp_stretch/     Phase 2
├── tests/           unit/  e2e/
├── mds/             planning + agent bookkeeping (see below)
├── ralph-afk.sh     autonomous build loop (Docker Sandboxes; Claude Code or OpenCode per tier)
├── opencode.json    OpenCode permissions + the Spark/Wonder model entries per provider
└── directives-for.sh  prints the DIRECTIVES rules that apply to a change
```

Trust boundaries (plan §1.2, §2.1): `src/autobridge` (engine), `verifier/` (oracle), `simulators/`
(vendors + fault injector) are separate packages, containers and Docker networks. Engine code never
imports or reads the other two.

## Verification ladder (plan §8)

```
make test     # unit, ≥90% coverage per Python package + UI component tests
make up && make smoke   # all services healthz + readyz, negative network checks
make e2e      # tests/e2e/test_00 … test_07 (+ test_s* in Phase 2), random order
make e2e-ui   # Playwright UI journey test_06 (needs `make up` first)
# CI (.github/workflows/ci.yml): make test → make up → make smoke → make e2e → make e2e-ui
make bench    # heldout sha check, matched-budget run → experiments/scorecard.md
make demo     # six demo steps through the control API
```

## The autonomous loop

```
cp .env.example .env                       # SLACK_WEBHOOK, provider keys, model ids
sh ralph-afk.sh sbx1 10                    # auto ladder: baseten → modal → meta → claude
sh ralph-afk.sh --agent claude sbx1 10     # Claude Code only
sh ralph-afk.sh --agent opencode --provider meta --model muse-wonder-1.0 sbx-mini 5
sh ralph-afk.sh --validate sbx-mini 1      # daily directives audit
```

Ralph (planning, iterations, checkpoints, validation) drives one coding-agent runtime per iteration:
Claude Code in its own sandbox, or OpenCode in its own sandbox with a provider/model chosen by tier
(Baseten Spark Open = worker, Modal Spark Open = backup, Meta Spark 1.3 / Wonder = escalation). Failures
are classified per iteration (auth, provider, empty, unfinished task) and move the ladder; the
verification gates (`make lint / typecheck / test`, the workstream's e2e file, the mds/ bookkeeping) are
the same whichever runtime ran. Setup and the tier table: `mds/DEPLOY.md`.

Prerequisites before the first run: `git init` + a GitHub `origin` with a `main` branch (the loop works
in `.worktrees/<sbx>` off `origin/main` and pushes there), `gh auth login`, Docker Sandboxes (`sbx`)
with the four sandboxes named in `mds/DEPLOY.md`, Claude signed in inside `claude-autobridge`,
network policy allowing the three provider hosts.

## mds/ files

| File | Role | Who reads it |
|---|---|---|
| `AutoBridge_Implementation_Plan_2026-10-26.md` | The plan (design + build order). Stable. | Agents read one § at a time |
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
