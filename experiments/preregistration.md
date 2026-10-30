# Pre-registration: engine vs. untrained frontier model (REQ-024 B5)

Committed before the November 3 held-out run. Whatever the numbers say,
they are published as-is: wins and losses both land in the scorecard, and a
result that contradicts the hypothesis below is reported, not re-run.

## Arms (same FRONTIER_MODEL through the same provider on every arm)

| arm | what it is | code |
| --- | --- | --- |
| `frontier_oneshot` | the model alone, one prompt with the docs | `bench.one_shot` via the frontier provider |
| `frontier_agent` | the model as a coding agent with tools (the untrained-frontier baseline) | `opencode run` (default driver, `OPENCODE_MODEL` = same provider+model as `FRONTIER_MODEL`) |
| `engine_no_knowledge` | AutoBridge with L0 and L1 disabled (ladder, verifier gate and budgets kept, so only the model repairs) | `RepairController(..., enable_knowledge=False, enable_rules=False)` |
| `autobridge_cold` | full AutoBridge, empty learned knowledge | fresh `KnowledgeCache` |
| `autobridge_warm` | full AutoBridge after learning on the dev split only (never on held-out) | cache from `warm_knowledge(dev scenarios)` |

`c` vs `d` isolates what the engine's built-in knowledge (rules + known
fixes) adds; `d` vs `e` isolates learning; `b` vs `d` is the headline.

## Matched budget (self-test fails the run on mismatch)

Every scenario × trial builds one `HarnessInputs`; all five arms receive an
equal copy (`assert_all_inputs_equal`, pairwise over arms): same docs
volume, sandbox URL, credentials (refs only), visible tests, token ceiling
and wall clock. Grading is the verifier's hidden mode, by `verify_fn` over
HTTP — never an import, never a guess.

## Metrics (per arm, per scenario and overall; mean + 95 % interval, normal approx)

- verified completion rate (judged only by the verifier),
- wrong patches that reached a release (must be 0 for AutoBridge arms),
- correct escalations (escalated exactly when the scenario says so),
- frontier tokens, small tokens, estimated cost (REQ-021 prices), wall time.

Minimum 5 trials per scenario (different seeds derived from the private
seed for held-out, from the dev seeds otherwise). Intervals below n=5 are
labelled illustrative.

## Primary hypothesis

On held-out, `autobridge_cold` reaches a verified-completion rate at least
that of `frontier_agent` with at least 10× fewer frontier tokens. `c` vs `d`
is expected to show the built-in knowledge doing most deterministic repairs
at 0 model calls; `d` vs `e` is expected to show learning as L0 hits on
repeat drifts (see `learning_curve.md`).

## Held-out integrity (REQ-024 A, committed)

- Leak audit: `scripts/heldout_leak_audit.py` (owner runs with `HELDOUT_DIR`);
  the counts-only result is copied to `<output-dir>/_leak_audit.json` and
  written into the scorecard. Exit 1 on any hit.
- Isolation evidence: `scripts/heldout_isolation_check.py` + `docs/benchmark.md`
  "Held-out protocol".
- Reveal: after the run the owner publishes seeds under
  `verifier/hidden_cases/heldout/revealed/`; `make verify-heldout-reveal`
  checks them against `SHA256SUMS` and reruns the bench from them.

## Rehearsal (REQ-024 B6)

`make bench-ablation` (no stack needed; grading degrades to unverified when
the verifier is down, labels say so) and the full `make bench` (stack up:
scorecard first, then all ablation arms, then the learning curve) run the
identical protocol on the dev split. `experiments/learning_curve.md` holds
the raw repeat-drift rows.

## What could change this plan

Nothing after this commit except the round/seeds it names: scenario set,
arm definitions, metric definitions and the hypothesis are frozen. If the
pipeline breaks on Nov 3, the breakage is published with the numbers, not
patched around.

## Amendment (REQ-046, 2026-10-30): fair context for the frontier arms

What changed and why (published, not hidden): the owner audit found the
frontier arms were starved — the one-shot prompt never showed the
ConnectorSpec format (no schema, no example) while being validated
against it, and the agent brief never guaranteed the schema/example
were reachable. A starved baseline is a strawman, so before any
rehearsal or the pre-registered run both frontier arms receive exactly
the context the engine has for the same case: the ConnectorSpec JSON
Schema, the vocabulary version's capabilities, one complete example
spec for a different vendor (never the case's own, never held-out
material), the vendor docs, the visible tests and a precise answer
format. Prompts live in files under `config/prompts/bench/` (same
loader/front-matter as the engine prompts; loaded at bench start; the
scorecard records each arm's prompt id/version/hash), and a unit test
asserts engine + both frontier arms share one context hash per case.
Scenario set, metrics, budgets, hypothesis and the no-reroll rule are
unchanged; only the frontier arms' inputs are fixed from starved to
fair, in the engine's favor to remove, not add, an excuse.

## Amendment (REQ-052, 2026-10-30): frontier quality — the numbers measure the model, not the harness

What changed and why (published, not hidden): the keyed rehearsal
(`origin/main` 6cf0511b, `BENCH_TRIALS=1 make bench-ablation`, 16 min)
showed the engine arms right (cold/warm 6/6 fixable fixed, 6/6
unfixable held back, 0 wrong patches) but the frontier arms' numbers
measured the harness. Five fixes, all in `bench/` + one provider field
(no engine, verifier or simulator change; scenario set, metrics,
hypothesis and the no-reroll rule unchanged):

1. **Output room.** The one-shot (and the LLM fallback) used a 4096-token
   cap while the engine's L3 uses `FRONTIER_MAX_TOKENS` (default 131072):
   the reasoning model spent the budget thinking, then the spec was cut
   off — 0/6 fixed measured our cap. Every frontier arm now gets the same
   output room as L3. A cut-off answer is detected (`finish_reason ==
   "length"`, new `Completion.finish_reason` field, or a full cap with no
   parseable spec) and the row is labelled `truncated` — never silently
   scored as a model failure.
2. **Agent identity + time.** The agent row carried `model=baseline-claude`
   (a default, not the run) and died at the 120 s CLI timeout. It now
   records the model the run itself reports, runs under the shared
   wall-clock (default 600 s for every arm — same budget rule, self-test
   still holds; `--wall-clock` / `BENCH_WALL_CLOCK` to change it), and a
   live run whose model differs from `FRONTIER_MODEL` fails loudly as
   `model_mismatch`. Timeouts are labelled `timed_out`, not failures.
3. **Every attempt graded.** Produced specs (one-shot text, agent answer
   text) go to the verifier like engine patches; each arm x scenario row
   carries the verdict + failed checks, or an explicit
   `truncated`/`timed_out`/`no_output`/`unverified` label. The summary
   reports per arm: fixable fixed, unfixable held back, wrong patches
   shipped, plus label counts (`render_frontier_quality_table`, and a
   per-scenario `frontier_detail` list in the summary JSON).
4. **Learning curve.** With `FRONTIER_API_KEY` set the agent column comes
   from the live agent driver — passing the fake driver then raises
   instead of publishing rehearsal numbers as live. Each repeat reports
   whether the agent repair verified.
5. **Cost.** The one-shot prices by the resolved model id from the one
   published table (`MODEL_PRICES_PER_MILLION`: Spark $1.25/$4.25 per M);
   the $15/M flat placeholder is gone. (Agent-arm cost still comes from
   the CLI report; out of scope here.)
