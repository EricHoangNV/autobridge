# Requests

## Open Requests (read this section only)

Owner priority (2026-10-30 21:30): **REQ-055 next.** Do not start REQ-051 or REQ-052 until the owner says so.

### REQ-055 — Deck 5 keyed: Approve fails on the migrated DB; the dry-run passed for the wrong reason; repair-page contradictions (2026-10-30)
Status: IN_PROGRESS (sbx1 since 2026-10-30 14:31)
Owner keyed check (2026-10-30, host `make local-up`, origin/main 5955fcca, fresh reset+seed, Aside at 1440): deck 4
(Harbor from the wiki, "check added from the docs") and deck 5 up to the decision work — repairing view live, first
proposal failed the dry-run, one retry, second passed ("tested on the current records: the checks pass"), 2 calls,
17,405 tokens, $0.064. Then:
1. **Approve fails (blocker).** `POST /repairs/{id}/approve-proposal` → 422 `sqlite3.IntegrityError: CHECK constraint
   failed: ck_repair_runs_ck_repair_runs_decision … SET decision='approved'`. Root cause: the live DB (`alembic_version
   0014`) still has `CONSTRAINT ck_repair_runs_ck_repair_runs_decision CHECK (decision IN ('propose', 'escalate',
   'reject', 'climbing', 'failed'))` — migration `0013_repair_terminal_decisions.py` drops/creates
   `ck_repair_runs_decision`, but the naming convention stored it under the **doubled** name, so the real constraint was
   never replaced. Fix the migration path so every existing DB (old name, doubled name) ends with the 0013 value set; add
   a migration test that upgrades a DB created at each earlier revision and then approves a proposal and a fix; make
   the unit/e2e tests that approve run against the **migrated** schema, not only `create_all`. Check every other
   `CheckConstraint` touched by migrations for the same doubled-name problem.
2. **The dry-run passed for the wrong reason.** The second proposal was `replace
   /entities/Reservation/checks/3/compare_to "$.total"`, labelled "Repoints money invariant to net total". In Harbor,
   `total` is the **record count** (the wiki: "The `total` field is always included"; 30 per page), not a net total.
   It passed because `runtime/checks.py` ~427–440 silently ignores a `compare_to` value that looks like a count — so the
   change actually switched the totals comparison **off** while claiming to repoint it. Fix: a check that is skipped
   or cannot evaluate during the dry-run counts as **not passed** ("the patched check did not run: `$.total` is not a
   money sum"); the retry message says so (generic wording). The proposal shows, next to the change, what the docs say
   the target field means. If the honest outcome is "stop comparing against the gross total", the model must propose
   that explicitly (e.g. `remove` the check) and the label must say so — the engine still never writes the proposal.
3. **Repair-page contradictions (REQ-054 layout):** (a) while climbing, the headline subtitle says "Approve to ship it
   as release r2" — no action wording until a decision exists; (b) chapter 3 says "No patch proposed — nothing
   verified to apply" while the rail shows the tested proposal — chapter 3 must show the proposal (Now → Proposed) when
   one exists; (c) the verifier tile says "not_verified · no patch to verify (the model escalated)" next to a tested
   proposal — show the dry-run result there; (d) in the rail, **Deny** and **Roll back** render as a tall one-word-per-
   line column (squeezed beside the release-id input) — lay them out readably at 1440 and 1280.
   (e) **The decision rail must stay pinned while scrolling** (owner, 2026-10-30, screenshot: it scrolls away). It is
   taller than the viewport (question + full patch table + raw JSON + buttons + both consequence lists), so `sticky`
   cannot hold it. The rail keeps only the decision: the question (one line), the proposal label + dry-run result,
   Approve / Deny, roll back, and the "If you click Approve / Reject" consequences collapsed (▸). The Now → Proposed
   table and raw JSON live in chapter 3 (see b). Safety net: `position: sticky; max-height: calc(100vh - <top>);
   overflow-y: auto` so a long question still stays pinned. Verify at 1440×900 and 1280×800 by scrolling the deck-5
   escalation page to the bottom: Approve stays visible.
   (f) **No overlap or overflow anywhere** (owner, 2026-10-30: "make sure there are no overlap"). Evidence graph: the
   drift node's box is fixed-width and its label `SEMANTIC_VALUE_DRIFT · 77b78e79` runs past the border — nodes size
   to their content (or wrap/ellipsis with a title). Then add an **automated layout check** (Playwright) that loads the
   connectors list, a connector page, the build page, the knowledge page, and the repair page in each state (propose,
   climbing, escalated with proposal, approved) at 1440×900 and 1280×800 and fails on: text overflowing its box
   (`scrollWidth > clientWidth` on bordered/fixed-width elements), sibling boxes whose rectangles intersect, and
   one-word-per-line squeezes (an action link or label wrapping to > 3 lines). Fix everything it finds.
4. **Show the dry-run gate at work, and stop our retry message steering the model** (owner, 2026-10-30). In the
   keyed run the first proposal (`compare_to "$.net_sum"`) was rejected by the dry-run and the retry produced the
   `$.total` proposal — but **nothing of this is in the UI**: the rung carries `retried`, `retry_error`,
   `proposal_dropped` and the dry-run result, and no UI code reads them; the model-call table shows Outcome
   `model_escalation` and `—`.
   (a) **Retry message carries meanings:** "available: cursor, data, gross_sum, limit, next_cursor, total" listed
   names only, and `total` (the record count) looked like a net total. The retry lists each available field with its
   documented meaning from the docs the connector was built from (e.g. "`total`: number of records",
   "`gross_sum`: sum of gross amounts on this page"); generic, no scenario names.
   (b) **Chapter 2, L3 rung, shows each call:** call 1 → its proposal (op/path/value) → "rejected by dry-run: <reason>";
   call 2 (retry with that error) → its proposal → "passed dry-run" (or rejected → "question only"). The model-call
   table's Outcome column reads "rejected by dry-run" / "passed dry-run" / "no proposal" instead of
   `model_escalation` / `—`.
   Tests: rung trace with a rejected-then-passed retry renders both calls and reasons; Outcome labels; retry prompt
   contains field meanings and no scenario wording.
Tests: migration upgrade from each revision then approve; dry-run marks a skipped check as failed; UI tests for (a)–(d).
Gates unkeyed + `make demo` 8/8. **Owner check after (keyed, through the browser):** decks 4 → 5: break → tested
proposal whose label matches what it does → Approve → verifying sync Complete → Harbor ● Working.


### REQ-054 — Repair page rearranged around the story (UI only, no new API) (2026-10-30)
Status: DONE (sbx1 2026-10-30 -- story layout live: headline+storyline, 3 chapters, decision rail per state, Now-from-spec op tables, fact-once, reduced-motion; ui 244 pass, typecheck+lint clean, make demo 8/8 + test_06 8/8 live unkeyed; owner check: Aside screenshots at 1440 deck 3 + deck 5)
Owner design, read in place (local only, gitignored — **read it, never copy it into the repo, never edit it**):
`/Volumes/OWC-Express-1M2/Development/autobridge/tmp/design/repair-flow/` — `PROPOSAL.md` (read first),
`proposal.html` (static prototype), `app.css`; renders in
`/Volumes/OWC-Express-1M2/Development/autobridge/tmp/design/shots/flow-r2/` (`1440-full*.png`). The look is unchanged
(`tmp/design/DIRECTION.md`); **only order and grouping change**. **UI only: no new API endpoint, no schema change** —
everything shown already comes from existing endpoints (repair, connector, release spec, verifier report, model usage).
1. **New order** (`ui/app/repairs/[id]`): headline (the whole story in one sentence, from facts) + storyline
   **1 happened → 2 did → 3 recommends** (each step links to its chapter; the yellow mark on step 3) → **1 What
   happened** (4 facts · wire before → after · ▸ affected records and evidence) → **2 What the engine did** (ladder
   L0–L4 with the rung that held lit · proof tiles: model calls, verifier, hypotheses/probes · ▸ model usage across
   runs · ▸ evidence graph, both collapsed) → **3 What the engine recommends** (per-operation **Now → Proposed** table
   grouped by operation · ▸ raw JSON Patch with copy, collapsed) → demo "inject fault" (dashed) at the bottom.
2. **Decision rail (sticky on desktop):** Approve (release rN), what Approve does (merged from "What happens when you
   click Approve"), Reject + what it does, ▸ roll back, "Approving as <name>". Below 1100 px it moves after chapter 3
   with a sticky bottom bar (desktop is what gets demoed; narrow only must not break).
3. **Facts only (owner decision): no lede sentences / per-drift-class templates.** Each chapter opens on its facts.
4. **Each fact once:** model cost is a single tile (no repeated 0s); the patch appears once as the Now → Proposed table
   (raw JSON collapsed). **Now values are read from the current active spec**, never placeholders.
5. **Empty slots shrink:** unused rungs read "Not needed"; empty hypotheses/probes become one tile ("The rule needed
   no enumeration"); empty evidence graph / per-run usage live collapsed in chapter 2.
6. **Signature motion:** on load the storyline bars fill 1 → 2 → 3, then Approve pulses once; reduced motion → bars
   drawn full, no pulse.
7. **The other repair states use the same layout** (not in the prototype): **climbing** — storyline step 2 active, the
   rail shows "repairing at <rung>…" (model, elapsed seconds, token counter from REQ-043) and no actions;
   **escalated** — the rail shows the model's question and rationale, the **tested** proposal (REQ-049/053: label,
   effect, dry-run result) with **Approve: <label>**, and **Deny: keep everything as-is, contact the vendor**; no
   proposal → question only, Deny; **approved / rejected / rolled back** — the rail shows the outcome (who, when,
   release) with actions disabled (REQ-053 item 4).
8. **Keep list** (all still present): PROPOSAL.md § "Keep list". `data-testid`s move with their blocks
   (`repair-section-approve` merges into the rail); update `tests/e2e/test_06_ui_journey.spec.ts` and the repair page
   unit tests accordingly. Normalise the inline-`code` font sizes (PROPOSAL.md note).
Tests: page tests for each state (propose, climbing, escalated with/without proposal, approved, rejected); Now values
from the spec; each fact rendered once; reduced motion. Gates: ui tests + typecheck, e2e journey, `make demo` 8/8.
Owner check after: Aside screenshots at 1440 of deck 3 (propose → approved) and deck 5 (climbing → escalated with
proposal → approved).


### REQ-052 — Benchmark quality: the frontier arms must make real, complete, gradable attempts (2026-10-30)
Status: OPEN (owner will start it)
Owner keyed run (2026-10-30, sandbox, `origin/main` 6cf0511b, `BENCH_TRIALS=1 make bench-ablation`, 16 min): engine
arms are right (AutoBridge cold/warm: 6/6 fixable faults fixed, 6/6 unfixable held back, 0 wrong patches). The
frontier arms' **quality** numbers are not trustworthy — they measure our harness, not the model. Owner: focus on
quality, not on the agent's spend (token/cost accounting for the agent arm is out of scope here).
1. **One-shot answers are truncated.** All 12 trials ended at exactly **4,096 output tokens**
   (`bench/one_shot.py` ~375 `max_tokens: int = 4096`; the reasoning model spends the budget thinking, then the spec is
   cut off) → 0/6 fixed measures our cap. Give the one-shot the same output room as the engine's L3
   (`FRONTIER_MAX_TOKENS`, REQ-043/048), and **detect truncation** (`finish_reason == "length"` or no parseable spec):
   such a row is labelled `truncated`, never silently scored as a model failure.
2. **The agent arm must run the model it claims and have time to finish.** Rows carry `model=baseline-claude`
   (`bench/harness.py` ~873 default label) — record the model actually used (`muse-spark-1.3` via OpenCode, from the
   run itself, not a default), and fail the run if it differs from `FRONTIER_MODEL`. Two trials (`token_expired`,
   `transient_503`) hit the 120 s CLI timeout; size the agent's wall-clock budget so a real attempt can finish (same
   budget rule for every arm, pre-registration amended), and label timeouts as `timed_out`, not as failures to fix.
3. **Every frontier attempt is graded, with the reason.** All frontier rows have `verdict` and `failed_checks` =
   null. For each arm × scenario record: did it produce a spec/patch at all, did it apply, the verifier's verdict and
   failed checks, and for unfixable faults whether it correctly held back. The summary reports, per arm, fixable
   fixed / unfixable held back / wrong patches shipped, plus counts of `truncated` / `timed_out` / `no_output`.
4. **Learning curve:** with `FRONTIER_API_KEY` set, the agent column must come from the live agent, not
   `FakeOpenCodeDriver` (today's `experiments/learning_curve.md` still says "fake-opencode", "2000" per repeat); the
   rung table must be filled (it is empty). Quality first: report whether each of the 5 agent repairs was verified.
5. One-shot cost uses a flat placeholder ($15 / M tokens: $0.2163 for 14,420 tokens); use the real Muse prices from
   one table (Spark 1.3: $1.25 in / $0.15 cached / $4.25 out per M). Minor; do not extend to the agent arm.
Tests: truncation detected and labelled; agent model recorded from the run and checked against `FRONTIER_MODEL`;
timeout labelled; every arm × scenario row has a verdict or an explicit `truncated`/`timed_out`/`no_output` label;
learning curve refuses the fake driver when a key is set. Owner check after: keyed `BENCH_TRIALS=1 make
bench-ablation` in the sandbox, per-scenario table of what each frontier arm produced and why it failed or passed.


### REQ-051 — Deck 7: the PABX layout change is detected and repaired live (rename_field, Approve), not only in-process (2026-10-30)
Status: DEFERRED (owner 2026-10-30: low priority; deck 7 now uses the link drop. Do not start until the owner reopens it.)
Owner review (2026-10-30). The scenario definition (`simulators/scenarios/fias_record_layout_change.yaml`) says: "The
connector detects the drift from the handshake LR and repairs the FolioCharge mapping with a JSON Patch through the
ladder (L1 rule, 0 frontier tokens), then postings flow again." On the live stack that never happens:
- the edge agent re-handshakes and stores the announced layouts (`edge/fias_host.py` ~600–614) and sends them in its
  heartbeat (`edge/poller.py` ~265, "so the cloud can detect a firmware layout change");
- the relay stores the heartbeat detail (`edge/relay.py` ~490) and **no live code reads the layouts**;
- the detection exists (`drift/event_feeds.py` `detect_feed_field_rename`: mapped `TA` gone, `TM` new → rename) but
  only `bench/demo.py` step 8 calls it, in-process.
So after `make demo-break SCENARIO=fias_record_layout_change` the live connector keeps reading `TA` while the PABX
sends `TM`, and `docs/demo.md`'s "the edge agent absorbs the firmware change, no repair needed" is wrong.
Fix (wire what exists; no new detection logic, no model call):
1. **Investigate first and report in BUILD-NOTES:** on today's live stack, what happens to the PABX postings' amounts
   after the layout change (dropped silently with the sync still `Complete`, or the sync degrades)?
2. **Live detection:** when the agent's announced layout reaches control, compare it against the connector's active
   spec with `detect_feed_field_rename` (generic: any event-feed connector, not FIAS by name). A mapped wire id that
   disappeared → a drift (`SCHEMA_RENAME`) recorded against the connector, with the before/after layout as evidence;
   the connector goes ▲ Degrading ("the PABX layout changed: TA → TM").
3. **Live repair through the normal ladder:** the drift queues the repair job (REQ-042 path): L0 → **L1
   `rename_field`** → JSON Patch on the FolioCharge mapping (`$.TA` → `$.TM`), verified, **0 model calls**, "fix ready".
   Same repair row, page, and Approve flow as deck 3 (REQ-043/048 views).
4. **Approve** → release r2 → the cloud maps postings with `TM` → the connector is **● Working** again, amounts
   present; the fix is learned like any approved fix (REQ-049 decisions list shows the approval).
5. Docs: `README.md` / `docs/demo.md` deck 7 describe break → ▲ layout changed → repair at L1 → Approve → ●, and drop
   "the agent absorbs it".
Tests: heartbeat with a renamed layout → drift + repair queued; no drift when only fields are added; the L1 patch
applies and postings after r2 carry the amount; e2e on the live stack (break → repair → approve → Working, amounts
present). Gates unkeyed + `make demo` 8/8. Owner check after: the Aside UI rehearsal of deck 7.

### REQ-048 — Deck 5 on screen: an escalation reads as a question, not a disabled Approve (2026-10-30)
Status: DONE (sbx1 2026-10-30 -- escalate top box, single question, header count, in-flight calls, one-repair token cell, budget= L3 cap on row/ledger/page + BudgetExhausted label, funnel deriving, docs rewritten; full gates green unkeyed + make demo 8/8; owner check: Aside run + screenshots)
Owner UI run (2026-10-30, host `make local-up` keyed, `origin/main` 6b677b2d, Aside at 1440×900): `make demo-reset &&
make demo-seed`, then `make demo-break SCENARIO=gross_to_net TARGET=harbor`. The flow works (running repair on top
within ~2 s, "Repairing at L3 … calling muse-spark-1.3 · 4 s so far", then the model's question + rationale, prompt
`l3_patch v1`, 3,530 tokens, $0.0107, Harbor ■ Error "needs your decision"). What a judge would notice:
1. **Repair page leads with the wrong thing for an escalation.** The first screen is a large (disabled) **Approve
   release** button, Reject patch, Roll back to [release id], and "Approve/Reject available only when decision is
   'propose' (current: escalate)". The model's question is two screens down. For `decision == escalate`: the top box
   shows the decision line, **the question** (model's, labelled, or the engine's when no model question), the
   rationale, and what the operator can do now (e.g. edit the spec / build a new release, or Reject to close it —
   only actions that exist); no Approve button at all, no internal wording like "decision is 'propose'". Roll back
   stays available but secondary. For `propose` and `climbing` the top box is unchanged.
2. **Connectors header contradicts the rows.** It reads "nothing waiting for you." while HarborPMS shows ■ Error
   "needs your decision". The header counts from the same connector-state source as the rows: "1 waiting for you"
   (linking to it) whenever any connector is `error`/awaiting a decision.
3. **The question and rationale are printed four times** (L3 rung, L4 rung, escalation bundle, decision section).
   Show them once, in the top/decision box; rungs say "L3: the model asked for a human decision" and "L4: escalated to
   you"; the escalation bundle keeps reason/action without repeating the question.
4. **Harbor's "Model tokens, per repair" cell shows a lone tall `|`** (a one-bar chart). With one repair show the
   number only ("3,530 in 1 repair"); draw bars only for 2+ repairs, scaled so one bar never fills the cell height.
5. **Repairs list says "0 model calls" while the call is in flight.** Show "1 call in flight" (from the running row's
   rung snapshot) until it lands.
6. **Budget vs cap mismatch.** The page shows "of a 40,000 budget" while `FRONTIER_MAX_TOKENS` is 131072. Find the
   40,000 frontier budget; make the repair's frontier budget ≥ the L3 cap (one source of truth, configurable), show the
   budget that actually applies, and on `BudgetExhausted` label it on the repair page.
7. **Small glitches:** sync outcome "took 0 ms" on the faulted sync (show the real duration or omit it); the
   "meridianpms" name overlaps the "Last sync" column at 1440 px (wrap or truncate with title); the running repair
   says "No hypothesis derivation was recorded" until the climb ends (show "deriving…" while `climbing`, or the
   funnel stages as they land).
8. **Docs still describe the old deck-5 outcome**: `README.md` (~123) and `docs/demo.md` (~88, ~198, ~232) say "the
   rejected `net_amount → total_amount` decoy". Rewrite to: the model sees the evidence and either asks for a business
   decision (its question shown) or proposes a patch that the verifier judges. Keep the `invalid_patch_trap`
   catalogue row (that scenario still exists) but do not present it as deck 5's outcome.
Tests: UI component/page tests for the escalate top box (no Approve, question first), the header count, the single
question rendering, the one-repair token cell, the in-flight call count, the running funnel text; a unit test that
the frontier budget is ≥ the L3 cap. Gates unkeyed + `make demo` 8/8. Owner check after: the same Aside run, with
screenshots of the repair page top, Connectors header, and Repairs list during the call.

Resolution: repair page escalate top box leads with the question (no Approve, Reject closes it); question/rationale shown once; header counts error connectors; repairs list shows in-flight calls; one-repair token cell has no chart; repair frontier budget is the L3 cap (row/ledger/page) with a BudgetExhausted label; funnel shows deriving while climbing; deck-5 docs rewritten to ask-or-propose.

### REQ-047 — When the model escalates, every label must say so: reason line, question, verifier line (2026-10-30)
Status: DONE (sbx1 2026-10-30 -- reason/question/rationale/verifier/title/docs fixed, full gates green unkeyed + keyed-shaped escalate test; owner check: keyed step 4 + repair-page screenshots)
Owner run (2026-10-30, sandbox, `origin/main` 28de2ffe, keyed): `make demo` 8/8 (41 s) and 8/8 per step. Step 4's
live L3 (`muse-spark-1.3`, prompt from `config/prompts/l3_patch.md`) now answers well — 4,321 tokens (2,983
reasoning), $0.015:
`{"escalate": "Should Reservation.total_amount map to the vendor net `amount` or remain defined as gross
`$.gross_sum`?", "rationale": "Observed amounts sum to 900.00 versus gross baseline 1000.00 so choosing net versus
gross for total_amount is a business decision the evidence does not settle."}`
The rung entry is right (`model_escalation: L3 asked for a human decision`), but the labels around it contradict it:
1. **Reason line is wrong.** `why_escalated` / `rejection_reason` (demo output, per-run usage table, and wherever the
   repair page and connector activity get their reason text) read *"L3 called live (…): response contained no
   applicable patch (no JSON patch or escalation found in response); raw response: {"escalate": …}"*. The message
   builder predates REQ-044 and does not know the escalate answer. Expected e.g. *"L3 called live (muse-spark-1.3,
   4,321 tokens, 2,983 reasoning): the model asked for a human decision"*.
2. **Wrong question shown.** `escalation_question` is still the engine's canned text ("Harbor totals now read net
   instead of gross — should total_amount track $.gross_amount …"), not the model's question. When the model
   escalates, its question is the escalation question (labelled "The model's question for you:" as REQ-044 asked),
   with the rationale beneath; the canned question is used only when no model question exists.
3. **Verifier line looks false.** `checked_by: live verifier verdict=fail failed=['invalid_patch_trap']` although no
   patch was proposed. Find what the verifier actually checked on this path. If nothing was proposed, say "no patch
   to verify (the model escalated)" and do not show a fail verdict; if a check genuinely ran on something, label what.
4. Step 4's title "gross_to_net rejected, escalated" and docs/demo.md describe the new truth: the model saw the
   evidence and asked for a business decision (and, when it proposes a patch instead, the verifier verdict decides).
Fix all four across `bench/demo.py`, the repair controller / rung ledger, the repair page (`ui/app/repairs/[id]`,
`ModelCallBox`, decision section), connector activity, and docs. Tests: escalate answer → reason line, question,
rationale, and verifier label as above (unit + UI component); patch answer → unchanged behaviour; no answer → the
existing "no applicable patch" wording. Gates keyed-shaped (fake provider returning the escalate JSON above) and
unkeyed: `make demo` 8/8. Owner check after: keyed step 4 + screenshots of the repair page.

### REQ-046 — Fair benchmark: frontier arms get the same context the engine has, prompts in files (2026-10-30)
Status: DONE (sbx1 2026-10-30 -- fair context bundle + file prompts + scorecard provenance + docs/prereg amendment, full gates green unkeyed + BENCH_TRIALS=1 rehearsal 12/12 matched; owner step: keyed BENCH_TRIALS=1 make bench-ablation for live frontier numbers, then close)
Must land **before** any benchmark rehearsal or the pre-registered run (a starved baseline is a strawman judges
will spot).
Owner audit (2026-10-30):
- **Frontier one-shot** (`bench/one_shot.py` `build_one_shot_prompt`): gets the vendor docs and the transform names
  but **never the ConnectorSpec format** — no schema, no example. It must invent the format, then is validated
  against our schema, so its failures partly measure "never saw the format".
- **Frontier agent** (`bench/ablation.py` ~407 → `bench/harness.py` `run_baseline_arm` ~790): a coding agent with a
  one-paragraph brief and paths; unverified whether the schema and an example are reachable from its sandbox.
- **`run_baseline_via_llm_provider`** (`bench/harness.py` ~878): one line, "Build a ConnectorSpec for X from <a path
  the model cannot read>". Appears unused by the ablation — confirm; delete it if unused, otherwise fix it.
Fix:
1. Bench prompts live in files under `config/prompts/bench/` with the same loader/front-matter as REQ-045
   (loaded at bench start; edit + rerun to change). The bench scorecard records each arm's prompt id/version/hash.
2. Both frontier arms receive **exactly the context the engine has** for the same case: the ConnectorSpec JSON
   Schema, the vocabulary version's capabilities (methods, auth, pagination, transforms, checks), **one complete
   example spec for a different vendor** (never the case's own vendor and never held-out material), the vendor docs,
   the visible tests, and a precise answer format. The agent arm's sandbox contains the same files, and its brief
   names them. Nothing from `verifier/` or hidden cases enters any arm.
3. A test asserts the engine arm and both frontier arms are given the same context set for a case (by content
   hash), and the leak audit still passes.
4. docs/ and the pre-registration note describe what each arm is given. Rehearse with `BENCH_TRIALS=1 make
   bench-ablation` unkeyed in the sandbox; the owner runs it keyed after.

### REQ-043 — Show a repair while it is running: the repair row exists from the start of the climb (2026-10-30)
Status: DONE (sbx1 2026-10-30 — code + full gates green unkeyed + live-stack e2e (local-up): running row at job start, rung snapshots, crash-orphan handling, repairing list + page + connector link, FRONTIER_MAX_TOKENS, demo poll waits terminal, docs; keyed owner step outstanding: keyed step-4 live watch + L3-with-131072 report)
Owner run (2026-10-30, sandbox, fresh checkout of `origin/main` at 27e1bc6f with the owner's `.env` incl.
`FRONTIER_API_KEY`): `make demo` 8/8 in 1m04s, and 8/8 with each step alone after `make demo-reset` — REQ-042's keyed
owner step passes. But step 4's live L3 call (`muse-spark-1.3`) spent **8,190 of 8,192 output tokens on reasoning and
returned no text**, so the climb escalated to L4 with "empty response". The owner raised the L3 cap to 131072
(`090f19ea`, `L3_MAX_TOKENS` in `repair/ladder.py`; Muse bills tokens used, not the cap, and accepts 131072 live) and
the demo now waits up to 5 min for a repair job. A long reasoning call can therefore take a minute or more.
Problem: during that time the UI shows almost nothing. The connector reads `▲ Degrading · drift detected … ·
repairing` (`control/connector_state.py`), but the `RepairRun` row is written only **after** the climb finishes
(`repair/controller.py` ~212 and ~440), so the Repairs list is empty and the repair page — which already has a
`climbing` view with a live token counter (`ui/app/repairs/[id]/page.tsx` ~345, `ModelUsageView pending`) — cannot
be reached. The demo moment "break connector → open the Repairs page → watch it fix in real time" does not work.
Fix:
1. When the repair job starts, create the `RepairRun` row at once with a running state (e.g. `decision` null /
   `"climbing"`, add a migration if a column is needed), linked to the drift and the job, and commit it. Update the
   same row (commit after each rung, never holding a transaction across a model call — REQ-042 still holds) as the
   climb moves: L0 → L1 → L2 → L3 "calling `<model>` · Ns" → decision. The final write fills the existing fields;
   no second row (REQ-040's threaded `repair_run_id` stays the only link).
2. Repairs list: the running repair appears at the top within one poll of the break, status **`repairing`** (add it
   to `RepairStatus`), with the rung it is on. Connector row's activity links to it.
3. Repair page for a running repair: "what happened" filled from the drift; "what the engine did" shows the rungs
   so far, the current rung, and during a model call the model id, elapsed seconds and the live token counter; no
   Approve/Reject until a decision exists. Switches to the final view without a reload when the climb ends.
4. A job that crashes or times out leaves the row in a clear terminal state (`failed` with the reason), never stuck
   at `repairing`; a control restart marks orphaned running rows failed.
5. Make the L3 output cap configurable (`FRONTIER_MAX_TOKENS`, default 131072) and show reasoning vs answer tokens
   for the call on the repair page (the ledger already records `reasoning_tokens`).
6. Tests: fake provider that sleeps 30 s → the row exists and `GET /repairs` shows `repairing` with rung L3 within
   2 s of the break; the page test for the running view; crash → `failed`; restart → orphan marked failed; the
   connector-state and Repairs-list mappings.
7. Gates keyed and unkeyed: `make demo` 8/8 both ways, each step alone too. Owner check after: keyed step 4 shows
   the running repair live in the UI, and whether L3 with 131072 room now returns a patch (report tokens used and
   the patch or rejection reason).

### REQ-042 — A model call must never run inside a request or a held transaction: keyed demo fails 3/8 (2026-10-30)
Status: DONE (sbx1 2026-10-30 — code + full gates green unkeyed: deferred repair, background drain, commit-around-calls, demo step-4 polling, docs note; keyed owner step PASSED 2026-10-30: sandbox `make demo` 8/8 with FRONTIER_API_KEY, each step alone 8/8; follow-ups in REQ-043)
Owner run (2026-10-30, sandbox, fresh checkout of `origin/main` at 57670155, the Cycle 5 PASS commit, **with the
owner's `.env` incl. `FRONTIER_API_KEY`**): `make up && make demo-reset && make demo` → **steps 4, 7, 8 failed**:
step 4 `POST /connectors/{id}/sync -> unreachable: "timed out"` (no `Degraded`, no drift row); steps 7 and 8
`POST /connectors -> unreachable: "timed out"`. Each step alone after `demo-reset`: 7 and 8 pass, **4 fails**. The
Cycle 5 audit saw 8/8 because its run had no frontier key (L3 skipped instantly).
Cause: `POST /connectors/{id}/sync` executes the whole chain inline in the request (`control/routes/builds.py`
`trigger_sync` → `control.runner.sync_connector_now`), and since REQ-039 that chain includes the **live L3 frontier
call** for a `SEMANTIC_VALUE_DRIFT`. The request outlives the demo's 10 s client timeout and control stays blocked
(most likely the sync's DB transaction / SQLite write lock is held across the model call), so the next writes time
out too. The scheduler path has the same shape.
Fix (do not raise timeouts to hide it):
1. **No model call inside a request handler, and no DB transaction or write lock held across a model call.** The sync
   (request or scheduler) records the sync run and the drift, then queues the repair as a job; the repair job
   (deterministic rungs, then L2/L3) runs in the worker/scheduler thread, committing before and after each model call.
2. `POST /sync` returns as soon as the sync run and drift are recorded (well under 2 s); the UI and `make demo` follow
   the repair by polling its row (the Repairs page already polls). Step 4 waits on the repair row's state, bounded
   by the L3 budget, and reports it.
3. While a model call is in flight, control stays fully responsive: other connectors can be created, synced and read.
4. Tests: a fake provider that sleeps 30 s → `POST /sync` returns < 2 s, `POST /connectors` and `GET /repairs` answer
   meanwhile, the repair finishes and carries the call; plus the same through the scheduler.
5. **Run the gates keyed**: `make demo` 8/8 with `FRONTIER_API_KEY` set (live L3) and without it (replay or labelled
   skip), each step alone after `make demo-reset` as well. Note in docs/demo.md that the semantic repair arrives a few
   seconds after the break when the frontier call is live.

Owner priority (2026-10-30 03:40): **REQ-039 next**, before anything else.

### REQ-040 — Directive: §4/§7 repair provenance — the sync→repair path re-fetches "the newest RepairRun" unscoped, so concurrent syncs can cross-link candidate releases (2026-10-29)
Status: DONE (sbx1 2026-10-30 -- threaded repair_run_id verified on all paths + 2 new regression tests, full gates green; no owner step left)
Found by the 2026-10-29 directives audit (diff `2cea2072..d2d9c9d2`). In `src/autobridge/engine/detect_repair.py` (~line 466), after `RepairController.handle*` persists a repair, the code re-fetches it with `select(RepairRun).order_by(created_at.desc()).limit(1)` — no connector, drift-event or run-id filter. Since REQ-030 the scheduler ticks every 10 s in-process while manual `POST /sync` calls run concurrently (skip-busy is per-connector only), so two connectors repairing at once can attach a candidate release (`provenance_repair_run_id`), its verifier report id and the returned `repair_run_id` to the *other* connector's repair row. Fix at the source: have the controller/semantic-climb path return the id of the row it persisted (thread it through `LadderResult` or return it alongside), and delete the unscoped re-fetch. Test: two concurrent `detect_and_repair_after_sync` runs on different connectors each link their own candidate/verifier report. Do not paper over it with a `created_at` tie-break.

### REQ-039 — The live semantic break must run the real climb: funnel and the model call on the repair page (2026-10-30)
Status: DONE (sbx1 2026-10-29) — code + full gates green unkeyed + live verified (seed, break→error, demo 8/8, test_08 both passes); remaining OWNER STEP: keyed re-run then make record-frontier SCENARIO=gross_to_net + commit capture
Owner check on 0d294693: Harbor now seeds from its reference release and `make demo-break SCENARIO=gross_to_net
TARGET=harbor` goes `Degraded → SEMANTIC_VALUE_DRIFT → escalate → error` live: good. But the live repair escalates
deterministically (L0 miss → L1 no rule → **L4**, **0 model calls**, no hypothesis funnel), and `bench/demo.py`
`_step4_live` now *asserts* "live engine repair costs 0 model calls (frontier lives in the instrumented climb)". The
funnel and the model call exist only in the instrumented in-process climb recorded via `record-climb`. On camera
that means the repair the break produced shows 0 calls and no funnel, and a second, recorded repair carries the
story: exactly the stand-in the plan rejected. The deck-5 scenario is: break → the engine's own climb → funnel
(24 → 6 → 2 → 1) → L2 skipped (one explanation) → **L3 frontier call (live with `FRONTIER_API_KEY`, replay recording
otherwise)** → proposal rejected by the verifier → operator question → `error`.
Fix: the scheduler/worker repair path for a `SEMANTIC_VALUE_DRIFT` runs the same semantic climb the in-process path
runs (`RepairController._handle_semantic_climb`: hypothesis enumeration from the wire signals and spec, wire filter,
L2 when ≥ 2, L3 through the frontier provider and budget when < 2, verifier on the proposal, escalation question),
with the provider resolved the same way (`resolve_frontier_provider`: live key → live; recording → replay labelled
with its capture date; neither → L3 "skipped: no provider", stated on the page). The live repair row carries the
derivation, the model call(s) and the rejected proposal. Remove the "0 model calls" assertion from `_step4_live`;
assert instead that the repair produced by the break has the funnel and exactly one L3 attempt (live or replay).
`record-climb` stays only as the documented fallback when there is no key and no recording, and is never used by the
UI journey when either exists. Update `test_08_recording_journey` (semantic leg asserts from the break) and the
README/docs wording. Gates: `make test`, keyed `make local-up && make demo-seed && make e2e && make e2e-ui && make
demo` green.

Owner priority (2026-10-29 18:40): **REQ-030 → REQ-031 → REQ-032 → REQ-033** (the UI-first demo), after the findings of
the Muse Wonder audit and the owner's external audit are fixed, and **before** the benchmark rounds (the scheduler
touches the sync/repair path the benchmark measures).

### REQ-030 — UI-first demo, part A: sync scheduler, every step persisted, connector state (2026-10-29)
Status: DONE (sbx1 2026-10-29) — scheduler (interval/skip-busy/threaded/WAL), learn-on-approve (propose caches nothing; approve writes entry with approval id/actor/release id; evict on reject/rollback), connector state server-side (working/degrading/error + activity + last_sync_at), persistence (repair delta/class, verifier report id, hypothesis evidence, ModelCall mode/captured_at/outcome, pabx events, record-climb + pabx endpoints, drift retired on verifying sync), warm_knowledge via approve path, knowledge page approval line, docs/architecture.md §9.1. Gates: make test (autobridge 90.54%, verifier 91.49%, simulators 90.48%, ui 161), lint + typecheck + openapi-check + ui-typecheck green, local-up + e2e (test_03, matrix, knowledge_flow, 027, fias) + make demo 8/8 green unkeyed. Keyed re-run is the owner step (no FRONTIER_API_KEY in sandbox).
Plan: the owner's reviewed plan (summarised here; this text is the spec). **Recording flow:** browser on the left
(UI `localhost:8107`), terminal on the right. Every connector shows Working; the presenter runs one command per
scenario; the UI reacts by itself (state changes, drift and repair appear with all details); **the presenter clicks
every approval** (no auto-apply: every state change must stay visible) and watches the connector return to Working.
Today (owner check, keyed local stack): only `make demo STEP=3` leaves anything in the UI; STEP=1 leaves a connector
with no active release and no sync, STEP=2/7/8 leave connectors that never synced, STEP=4 leaves nothing (its climb
runs in-process). Syncs run only when something calls `POST /sync`; there is no schedule.
1. **Sync scheduler** (worker): syncs every active connector every `AUTOBRIDGE_SYNC_INTERVAL_S` (default 10 in
   local and Compose demo mode; off when unset). Skips a connector while a sync, build, repair or approval for it is
   running. Each sync runs the existing REQ-027 chain (detect → classify → ladder → verifier gate). Never approves.
2. **No auto-apply.** Repairs keep waiting for an operator decision, as today.
2b. **A fix is learned only once it is approved** (owner decision 2026-10-29). Today the L0 knowledge cache is filled
   when the ladder *proposes* (`src/autobridge/repair/controller.py`, the propose path) and evicted on reject/rollback.
   Change it: proposing caches nothing; `approve` (the real approve path, after the release is activated) writes the
   entry, recording the approval id, actor and release id on it; reject caches nothing; rollback evicts as today
   (AR-2-3). A proposal that is still waiting can never be reused at L0, by any connector. Everything that relied on
   propose-time caching follows the rule: `make demo` approves through the API before its "inject again" leg; the
   benchmark's `warm_knowledge` learns by approving its verified dev-split fixes through the same approve path, and
   says so in the arm's provenance; the Knowledge page shows "learned from approval <id> by <actor>". Tests: propose
   then re-run the same drift → L0 miss; approve then re-run → L0 hit; reject → miss; rollback after approve → miss.
3. **Every step persists** as rows the UI reads: builds (including refused builds with their reason), syncs, drift
   events, repair runs with the full ladder trace per rung, the hypothesis derivation (counts and ids per stage:
   generated → family → wire-consistent → distinct), probes, every model call in the `ModelCall` ledger (model, live
   vs replay + capture date, tokens in/out/reasoning, cost, outcome), verifier verdicts, the escalation question, the
   PABX link/postings counters and restart result. No in-process-only results anywhere (demo step 4 included).
4. **Connector state, server-side,** on `GET /connectors` and `GET /connectors/{id}`: `working` (last sync
   `Complete`, no open drift) · `degrading` (drift detected; repair running; verified fix waiting; verifying sync
   after approval) · `error` (repair waiting that used a model or escalated, sync `Degraded` or `Blocked`), plus a
   one-line `activity` ("drift detected 4 s ago · repairing at L1", "fix ready · verified · 0 model calls",
   "needs your decision", "Blocked: auth revoked") and `last_sync_at`.
Tests: scheduler (interval, skip while busy, off when unset, never approves); persistence of each item above; state
and activity for each transition. Gates: `make test`, keyed `make local-up && make e2e && make demo` 8/8.

### REQ-032 — UI-first demo, part C: live states and a repair page that explains itself (2026-10-29)
Status: DONE (sbx1 2026-10-29) — code + docs + full gates green unkeyed (see BUILD-NOTES); keyed re-run (make local-up && make e2e && make demo) is the owner step. Next: REQ-033.
Depends on REQ-030.
1. **Three connector states, everywhere a connector appears** (Connectors list, connector page, simulators index):
   `● Working` green (new token `--ok`, `oklch(80% 0.17 150)`, 10.5:1 on the navy ground), `▲ Degrading` yellow
   (`--live`), `■ Error` red (`--stop`), always colour + shape + word + the `activity` line, never colour alone.
   "last sync N s ago" ticks. A change in the database shows within ~2 s (polling or server events).
2. **Repairs list live:** new repairs appear at the top without reload; each row: connector, drift class, rung, rule,
   model calls, status (`fix ready` / `needs your decision` / `approved` / `applied` / `rejected`).
3. **The repair page answers four questions, in this order, each as its own section with a plain-language heading.
   All content comes from persisted data (REQ-030), never recomputed or invented in the browser:**
   - **What happened.** Connector and vendor; when and by which sync it was detected; the sync outcome and unmet
     criterion; what changed on the wire, shown as before/after (e.g. "pagination: page numbers → cursor
     `next_cursor`", "field `TA` renamed `TM`", "amounts read net instead of gross"), with redacted sample values and
     the evidence records; what it affects (entities, fields, operations, records in the failing sync).
   - **What the engine did.** The rung timeline L0 → L4, each rung with its action and reason in words ("L0: no known
     fix for this vendor and change", "L1: rule page_to_cursor matched", "L2 skipped: only one explanation left,
     nothing to rank"); the **hypothesis funnel** ("24 explanations → 6 about amounts → 2 consistent with what the
     API returned → 1 distinct"), each stage expandable to the hypotheses and the evidence that eliminated the rest;
     probes run; the **model-call box** listing every call (rung, model, live or replay with capture date, tokens
     in/out/reasoning, cost, outcome) and saying **"0 model calls"** explicitly when there were none; the verifier run
     (which checks ran, passed or failed, on which data); time taken.
   - **What the engine decided and recommends.** The decision in one line ("Fix ready: verified, 0 model calls" or
     "Needs your decision: the engine is not confident"); the proposed change as a readable diff against the current
     spec (rendered from the JSON Patch, with the raw patch one click away); confidence and why; the verifier verdict;
     what the change does and does not touch; for escalations, the operator question with the options the engine
     sees (e.g. "map `total_amount` to `$.gross_amount`" / "add a canonical field") and the rejected proposal with the
     reason it was rejected.
   - **What happens when you click Approve** (and Reject), **before** you click, generated from the server's real
     approve/reject path, not hard-coded text: e.g. "creates release r4 from r3 with this change · activates r4 ·
     pauses N dependent consumers until the next sync · the next scheduled sync (in ~N s) runs on r4 · this fix is kept
     as known for AtlasPMS + this change, so the same drift next time resolves at L0 · rollback to r3 stays available";
     Reject: "release discarded · the fix is not kept · the drift stays open". If the server's behaviour differs from
     this example (e.g. when the fix is cached), the text follows the server.
4. **After Approve**, the page follows the outcome live: approval recorded → release activated → verifying sync →
   `Complete` → connector `working` (or what failed). Approve / Reject sit at the top of the page with the decision
   line, one click from the Repairs list.
5. **PABX panel** on the PABX connector page: link state, postings arriving (count rising, last posting time), current
   record layout (v2/v3, announced in the handshake), and the last restart result ("redelivered 1 · lost 0 ·
   duplicated 0").
6. **Knowledge page:** "used by" per vocabulary version after the seed; learned fixes appear as approved repairs land,
   with "reused N×".
Tests: component tests for the states, the four repair sections (an L1 repair with 0 calls; the deck-5 repair with
the funnel, 1 model call, the rejected proposal and the question), the approve preview text against the API, the
after-approve progress, the PABX panel.

Owner priority (2026-10-29 17:00): **REQ-029 next**, before any other open item, so the Muse Wonder audit that follows
this run can close Code Review Cycle 2.
### REQ-029 — Close the last review gaps before the audit: worker release gate, docs_path builds (2026-10-29)
Status: DONE (sbx1 2026-10-29 — close-out: REQ-034 decided (option a) + DONE, both gaps re-verified in code with targeted suites green; remaining OWNER/AUDITOR STEPS: keyed make local-up && make e2e && make demo re-run + auditor flips the Code Review verdict line in FEEDBACKS.MD)
Context: owner re-checked every Code Review Cycle 2 finding against main (2026-10-29): AR-1-1, AR-1-3, AR-1-4,
AR-1-5 and AR-2-1…AR-2-6 are fixed in code. Two gaps remain; fix both so the audit (validate run with Muse Wonder,
queued after this run) has nothing to re-open. The verdict line in FEEDBACKS is the auditor's to change, not yours.
1. **AR-1-2 residual: a worker-run build or repair persists a release without the verifier gate.** `compose.yaml`
   (worker service, the "NOTE (AR-1-2)" comment) says the worker has no `VERIFIER_URL` and no verifier-net route, so
   "a worker-executed build persists without a gate check only when control's inline execution did not claim the
   job first". That is still an unverified release path. Fix fail-closed: no process may persist or activate a
   release without a passing verdict. Either route the worker's verification through control (control already has
   verifier-net), or have the worker leave the job pending/`awaiting_verification` for control to gate, and never
   persist when no verifier is reachable. Same in local mode (`scripts/local-up.sh`). Tests: a worker-executed build
   with the verifier unreachable persists no release and names the reason; with it reachable, the release carries
   the verification report id. Update the compose comment and docs/safety-model.md.
2. **`docs_path` builds silently compile placeholder docs.** Since the AR-2-2 fix, `POST /connectors/{id}/build`
   with only `docs_path` sets `docs_text = f"docs from {body.docs_path}"` (`src/autobridge/control/routes/builds.py`
   ~194–201) and builds a generic spec from that string (owner hit it: Atlas built with `/reservations` + offset
   pagination). Return 422 with "docs_path is an identifier only; send docs_text (or docs_url)" instead, keep the
   reference-spec guard, and make every caller (demo, UI, CLI `ab`, e2e, docs) send `docs_text`. If `docs_path` is
   still wanted as a label, store it on the build as provenance only. Tests for the 422 and for each caller.
Gates: `make test`, keyed `make local-up && make e2e && make demo` 8/8, `make up` likewise. Do not weaken guards.

### REQ-034 — Directive: §2 fault injection — FIAS admin unreachable from control in Compose; /demo/reset and /demo/inject-fault fail for sim-fias-pabx (2026-10-29)
Status: DONE (sbx1 2026-10-29)
Resolution: admin-forwarder sidecar `sim-fias-pabx-admin` (dumb TCP :8001 pipe, stdlib `simulators/admin_fwd.py` with self-dial guard) on admin-net + edge-net, carrying the `sim-fias-pabx` DNS alias on admin-net only; FIAS target `sim-fias-pabx-edge` (edge-only alias). FIAS stays edge-only, host ports and local mode untouched. Verified: 4 acceptance e2e green on `make up` AND `make local-up`, `POST /demo/reset` FIAS targets 200, `make smoke` (new control→:8001 up / :8000 down checks) green, `make demo` 8/8, full gates (lint/typecheck/test 90.30/91.49/90.48 + ui 160) green. Owner note: a same-container dual-home bind was tried first and reverted — Docker publishes a dual-homed container's host ports through one endpoint, orphaning :8118 or exposing :8000; the sidecar keeps every existing host path byte-identical.
**Owner decision (2026-10-29): option (a).** Give the FIAS simulator's admin listener a route on admin-net only, so
control reaches `sim-fias-pabx:8001` for reset and inject; the FIAS data plane (`:8000` and the PABX link) stays on
edge-net and unreachable from control (keep `scripts/smoke.sh`'s control -/-> sim-fias-pabx:8000 check and add a check
that control reaches only the admin port). Do not weaken the reset assertions or the edge isolation.
Directive §2: fault injection goes through `POST /demo/inject-fault` on control, which proxies to the simulator admin API. In Compose, `sim-fias-pabx` is on `edge-net` only (`compose.yaml:273-288`, codified in `tests/unit/test_autobridge_compose.py:152`) and control is deliberately not on edge-net (`scripts/smoke.sh:100` asserts control -/-> sim-fias-pabx:8000), so control cannot resolve `sim-fias-pabx:8001` (`src/autobridge/control/routes/demo.py:97-105`): live `POST /demo/reset` returns `{'target': 'sim-fias-pabx', 'error': '[Errno -5] No address associated with hostname'}` plus the same for `sim-fias-pabx:fias`. Verified 2026-10-29 on a clean `make up` stack (`make demo` 8/8, `make smoke` green): `tests/e2e/test_03_known_repairs_zero_frontier.py::test_page_to_cursor_live_zero_frontier_via_control_api` (test_03:788), `test_05_safety_and_semantic.py::test_semantic_drift_live_gross_to_net_degraded_via_control_api`, `test_model_usage_matrix.py::test_matrix_live_reset_repeatability` and `test_fias_faults.py::test_live_inject_fault_proxies_to_fias` all FAIL in Compose mode (they pass in local mode, where control is a host process using loopback :8119). Owner decision needed — do not weaken the reset assertions or the edge isolation to make them pass: (a) give the FIAS admin listener an admin-net-only route that does not expose the FIAS data plane to control, (b) route FIAS reset/inject through the edge-relay/agent, or (c) re-scope the live tests and demo/reset to a documented mode split where FIAS state is reset by a named, verified alternative path. Acceptance: the four tests above green on `make up` AND on `make local-up`, with FIAS state provably back to baseline after reset in both modes.

### REQ-037 — Stale `.local/ACTIVE` marker misroutes smoke/demo/e2e mode detection (2026-10-29)
Status: DONE (sbx1 2026-10-29)
Local-vs-Compose mode is decided by the mere presence of `.local/ACTIVE` in three places: `scripts/smoke.sh:37`, `bench/demo.py:547-560`, `tests/e2e/conftest.py:296-306`. When the local processes have died without `make local-down` (this audit found exactly that state: stale pids, Docker stack up), smoke silently skips all negative network checks, demo step 1 fails with "Compose hostname leaked into local mode", and e2e live tests take local-mode branches against a Docker stack — all reporting misleading results instead of "stale marker". Fix in one shared way (verify the recorded pids are alive, or probe which stack actually answers and fail loudly on a mismatch) so a stale marker can never masquerade as a running local stack. Acceptance: with a planted stale `.local/ACTIVE` and the Compose stack up, `make smoke`, `make demo` and the e2e spot files behave exactly as without the marker (or stop with a message naming the stale marker); with a genuinely running local stack nothing changes.

Owner priority (2026-10-29 10:40, supersedes the list below): **REQ-027 first.** Finish the iteration in progress
(merge what is green), then REQ-027 before any other REQ, owner check or review item. REQ-023's demo script must
describe the REQ-027 demo, so finish REQ-023 after REQ-027 lands (or update it then).

### REQ-027 — Auto-detect + auto-heal end to end on the live stack, and a demo that proves it (2026-10-29)
Status: IN_PROGRESS (sbx1 2026-10-29 — knowledge-page check + sim-keys defaults MERGED; L3 output-room + reasoning-ledger MERGED; fix-first pair + gate-flake fix MERGED; sub-step 5 CODE DONE (rename heals live via wire-shape probe annotation + wire-resolving rename rule; 9 per-scenario live e2e green; demo redaction-raw-seam + base-release fixes; `make demo` 8/8 twice unkeyed; UI knowledge spec 2/2); remaining: owner keyed-stack run (make demo 8/8 twice, L3 reasoning line, /knowledge state, UI spec), then sub-step 6 bench rerun — DONE 2026-10-29 (cold V=0.333 E=0.833, warm V=0.500 E=0.583, gaps published; REQ-028 owns fixes))
Why: it is the product's headline claim and today only FIAS (demo step 8) shows it. Owner checks on the host
(2026-10-29, main at f61274b8, keyed local stack):
- After REQ-024's honest fix (a62013dc) every engine arm escalates with 0 fixes: the detector output does not carry
  the evidence the L1 rules need (`page_to_cursor`, `rename_field`, ... never fire from a real sync).
- Demo step 3 calls `RULE_REGISTRY["page_to_cursor"]` directly on a hand-built `RepairContext`; its live part only
  injects the fault and lists `/drift` and `/repairs`, never asserting the engine found or fixed anything. Step 4
  hands `run_frontier_climb` the drift class and hand-written hypotheses (`bench/demo.py` ~770–900).
- In local mode the demo's Atlas connector (`base_url http://sim-atlas:8000`, a Compose hostname) syncs
  **Blocked: http_error before any fault**; the simulator log shows no request. No sync → no drift → no repair.
What to do:
1. **Local addresses.** Connectors created by the demo, by builds and by the UI resolve to addresses that work in
   the mode the stack runs in (local: `localhost:<public port>` from `config/demo_targets.yaml`; Compose: service
   names). One place decides it; no Compose hostnames leak into local mode. Baseline sync of every demo connector is
   `Complete` in both modes (`make local-up` and `make up`).
2. **Detector carries the evidence.** A real sync against a faulted simulator produces a drift whose signals and
   fingerprint delta hold what the classifier and L1 rules need (e.g. page→cursor: the old/new pagination shape and
   the cursor field seen on the wire; renames: missing/added paths with sample values). Derive it from observed
   responses only; never from the scenario id, the YAML or the answer key.
3. **The live chain.** Inject fault → the worker's (or `POST /sync`) sync detects → `RepairController.handle` with
   detector-derived inputs only → rule/ladder → verifier gate → repair visible in `/repairs` with rung, rule id and
   model usage → approve → next sync `Complete`. Works for every known-repair scenario that has a rule
   (page_to_cursor, offset_to_cursor, field renames, date/enum ones as applicable) with 0 model calls, and climbs to
   L3/L4 only for the semantic ones (gross_to_net) with the frontier given real evidence (current spec, the failing
   records, the relevant doc excerpt; redacted) instead of only a drift class.
4. **Demo steps 3 and 4 drive the live chain** and assert on what the engine did (drift found by the detector, rule
   id chosen by the controller, verifier verdict, tokens). No hand-built `RepairContext`, hypotheses, drift class or
   yield count anywhere in `bench/demo.py`; hand-built inputs stay in unit tests only. Step output names where each
   input came from.
5. **e2e, no fakes:** one live test per known-repair scenario: inject → sync → repair proposed by the right rule →
   approve → sync Complete; plus the semantic one escalating with a real L3 attempt. Keyed local stack and Compose.
6. **Rerun the honest benchmark** (REQ-024 arms, offline rehearsal) and record the new numbers in
   `experiments/` whatever they are; update REQ-025's caveat text if anything changed.
Gates: `make test`, keyed `make local-up && make e2e && make demo` green, the same on `make up`. Do not weaken any
guard test. Split into sub-steps (1 addresses, 2 detector, 3 chain, 4 demo, 5 e2e, 6 bench) and merge each green.

Owner priority (2026-10-28, updated 19:40): fix the REQ-020 signature regression (owner check after sub-step 6) first, then **REQ-021 next,
before anything else** (it has been skipped twice; REQ-024 and REQ-025 depend on its Muse frontier provider, reset and
model-usage table), then REQ-020 step 7 → REQ-022 (with REQ-026, the per-test timeout, slotted in after the REQ-022 sub-step in progress) → REQ-024 → REQ-025, with REQ-023 (demo plan) applied as each lands.
Do these before any remaining review FAIL item. Constraint for all of them: someone who downloads the repo may have no
Docker and no sandbox; the demo must still run on their machine (REQ-017), and nothing here may assume Docker is
present.

**Owner check after sub-step 3 (2026-10-29, on 6eb0b785): the chain works in-process, not yet through the stack.**
The sub-step's proof (`tests/unit/test_autobridge_detect_repair.py`, the iteration's checks) uses the Atlas
*reference* spec against an in-process faulted client. Owner ran the real path on the keyed local stack through the
control API: `POST /demo/reset` → `POST /connectors` (atlas, `base_url http://localhost:8105`) → `POST
/connectors/{id}/build {"docs_path":"simulators/docs/atlas-openapi.yaml"}` (auto-activated) → `POST /sync`:
1. **Baseline sync is `Blocked: http_error`; the simulator log shows no request at all.** The built release spec has
   `source.base_url: "${ATLAS_BASE_URL}"` (an unset env placeholder, ignoring the connector's base_url and the docs'
   `servers: http://localhost:8105`), `GET /reservations` and `/folios` with `offset` pagination; Atlas is
   `GET /api/v1.3/getReservations` etc. with page pagination (REQ-019). `connector.vendor` is the truncated docs path
   `"simulators/docs/atlas-openapi.ya"`. So building Atlas from its own OpenAPI docs produces a generic placeholder spec,
   and nothing built that way can sync.
2. **A blocked sync opens drift.** Each `Blocked: http_error` sync created a `SEMANTIC_VALUE_DRIFT` DriftEvent and an
   L4 escalation repair (0 tokens). A sync that never reached the vendor has no evidence of drift: classify it as a
   connectivity/config failure (its own outcome, no DriftEvent, no repair), never as semantic drift.
Fix: (a) the docs build reads OpenAPI `servers`, `paths`, parameters and pagination from the document (it already does
for Meridian v2; Atlas v1.3 must compile too), resolves `base_url` from the connector record when the docs or
placeholder disagree, and keeps `vendor` as the connector name; (b) no drift from a sync that got no response;
(c) add the stack-level test the REQ asks for in item 5 now, using exactly the calls above (create → build from docs →
baseline Complete → inject page_to_cursor → sync → `/repairs` shows `page_to_cursor`, rung 1, 0 tokens → approve →
sync Complete), keyed local stack; it must not use the reference spec. Do this before sub-step 4, since the demo
steps will drive exactly this path.

**Owner check (2026-10-29, on 03d097d4): the knowledge page stays empty through the whole demo; fill it as part of
sub-step 4.** `/knowledge` now shows each vocabulary version with "used by: <connectors>" (from `GET /knowledge`
`connectors[].vocabulary`) and learned knowledge on the right. After a full keyed `make demo` (8/8 ok): learned
knowledge is empty, the profiles read `atlaspms vocabulary ?` and `Harbor demo vocabulary ?`, Meridian (step 7) and
the PABX connector (step 8) are not listed, so every version reads "no connector built on it yet". Fix:
(1) every build, through the control API or the demo, records the vocabulary it compiled under on the connector
profile (step 7's v1 attempt is a refused build: show it as refused, and v2 as the release in use); (2) the demo's
repair steps approve their verified repairs through the API, so learned knowledge gets entries, and the "inject
again" leg shows `times_reused` going up with 0 model calls; (3) after `make demo`, `/knowledge` lists atlaspms (v1),
Meridian (v2) and the PABX connector (v3) under their versions, plus at least one learned entry. Add a UI e2e
assertion for that state after the demo.

**Owner check after sub-step 4 (2026-10-29, on 15bd357d): the live chain works, but only with a key nobody has.**
Verified on the keyed local stack: `tests/e2e/test_027_live_chain.py` 2/2 and demo step 3 live (classifier on wire
signals → `page_to_cursor`, rung 1, 0 tokens), 8/8 steps, one learned entry on `/knowledge`. **But only after the
owner exported `ATLAS_API_KEY=sim-demo-key`.** With the shipped defaults (`ATLAS_API_KEY=` in `.env.example`, unset in
local-up and Compose) the test fails at line 144: baseline `Blocked: http_error`, because `runtime/auth.py:64` raises
"Missing env var 'ATLAS_API_KEY'" and the simulator never gets a request. A judge's fresh clone hits exactly this;
the sandbox run that passed must have had the variable set some other way. Fix: every simulator credential the demo
uses gets a working demo value by default, in one place, for both modes (local-up exports e.g.
`ATLAS_API_KEY=${ATLAS_API_KEY:-sim-demo-key}`, the same for the other vendors; Compose passes the same defaults;
`.env.example` documents them as demo values for the simulators, not secrets). Add a fresh-clone check: with a
`.env` copied from `.env.example` and only `make local-up`, `make e2e` and `make demo` are green.
Second: **demo step 4's live L3 call returns nothing.** `L3 called live (muse-spark-1.3, 4,319 tokens): response
contained no applicable patch (empty response: the model returned no text)`. The reasoning model spends the whole
`max_tokens=4096` budget on thinking. Give L3 enough output room (and/or lower reasoning effort if the provider
supports it), count reasoning tokens separately in the ledger, and make sure the step shows a real proposal that the
verifier then judges. Third (labelled honestly already, lower priority): step 4's live leg is Blocked because the
docs-built Harbor spec has no money invariant; infer it from the docs if they state gross totals, otherwise keep the
labelled gap.

**Owner check (2026-10-29, on 96ba3137): demo keys fixed, but `make demo` is now 6/8 on the owner's host. Fix first.**
Default simulator keys verified: with `ATLAS_API_KEY` unset, `test_027_live_chain.py` 2/2. Knowledge data mostly
there (`page_to_cursor` learned, `times_reused: 1`; atlaspms v1, harborpms v1, meridianpms v2 with v1 refused). But
after `POST /demo/reset` and `make demo` on the keyed local stack:
1. **Step 8 fails: the PABX docs build with v3 is rejected.** `spec validation: source.base_url: base_url must be an
   http(s) URL or start with a ${ENV} placeholder [input_value='edge-relay://queue/fias-commands']`. This is the
   "use the connector's base_url verbatim" change from the sub-step 3 owner fix (4ca7586a) meeting the edge
   connector's relay address. So the PABX profile has `vocabulary: ""` and `refused_vocabularies: ["v3"]`. Either
   the `fias_tcp`/edge source accepts its relay address form, or the override applies only to http connectors;
   decide by protocol, not by name.
2. **Step 7 failed its new assertion once** ("knowledge lists meridianpms under v2 with v1 refused"), although
   `GET /knowledge` read right after shows `vocabulary v2, refused ["v1"]`. Most likely the step reads the profile
   before the second build's write is visible. Wait for the build to finish (or read the profile from the build's
   response) instead of reading once.
Gate: `make demo` 8/8 on a keyed local stack after `POST /demo/reset`, run twice in a row.

**Owner re-check (2026-10-29, on 97e682c3): step 8 fixed (PABX now v3), L3 now answers (2,009 tokens, 1,757
reasoning; its patch targets a non-existent `/money_check`, rejected and escalated: fine). `make demo` is 7/8 twice
in a row; step 7 still fails, and it is not a race.** The assertion "knowledge lists meridianpms under v2 with v1
refused" reads the body returned by `_control_call`, which is `_redact_body(...)`: at depth > 3 it replaces values
with `"…"` (`bench/demo.py:735`). `/knowledge` → `connectors` (list) → profile (dict) → `refused_vocabularies`
(list) → `"v1"` sits at depth 4, so the step sees `['…']` while the API really returns `["v1"]`. The polling added in
97e682c3 cannot help. Fix: assert on the raw response (keep `_redact_body` for printing only), and check every other
`_assert_live` that reads nested values for the same trap. Gate: `make demo` 8/8 twice on a keyed local stack.

### REQ-028 — Benchmark-driven engine fixes (owner rehearsal 2026-10-29) (2026-10-29)
Status: BLOCKED (sbx1 2026-10-30 -- code re-verified: fixes (a)(b)(safe-c) + scoped oneshot docs + arm availability + `ran` label + warm-never-worse test all present and green targeted; keyed bench-ablation outstanding)
Blocked: owner run `BENCH_TRIALS=1 make bench-ablation` with FRONTIER_API_KEY set (sandbox has none), record the table, confirm cold 6/6 repairs, warm >= cold, 0 wrong patches, frontier_oneshot tokens > 0; then close.
Priority: right after REQ-027 (the rename fix in progress there covers item 1).
Owner decision (2026-10-29): **the benchmark runs in the sandbox** (OpenCode is there; the host has none). Provide one
documented command that runs `make bench-ablation` (and later `make bench`) inside the sandbox against the stack, with
the same keys and budgets for every arm, and writes results back to `experiments/`. The owner reruns it only after
all REQ-027/REQ-028 fixes land. This is development rehearsal, not
the pre-registered run: iterate until the numbers are honest and good, then the owner does one live run.
Owner ran `BENCH_TRIALS=1 make bench-ablation` on the keyed local stack (main at d169e216; files in
`experiments/benchmark_runs/`). AutoBridge cold: repair scenarios fixed 4/6 (`page_to_cursor`, `offset_to_cursor`,
`cursor_repeat_token`, `meridian_cursor_renamed`), no-patch/question scenarios handled 6/6, 0 wrong patches, 0
tokens. Gaps, in order:
1. **Field renames are never repaired** (`rename_guest_id`, `meridian_state_renamed`: every engine arm V=0, E=0).
   The live detector emits `SCHEMA_RENAME` but the ladder and `rename_field` expect `FIELD_RENAME` (found by the
   REQ-027 iteration in progress). One taxonomy, end to end, and both scenarios verified with 0 tokens.
2. **Warm does worse than cold.** `autobridge_warm` proposes patches where none belong: `rate_limit_429` and
   `transient_503` (expected `no_patch`) come back as verified patches (E=0), and `duplicate_late_records` loses its
   correct escalation. A learned fix from the dev split is being reused on an unrelated drift: the L0 key is too
   coarse (or a no-patch drift matches a cached delta). L0 may only hit when the drift class *and* the wire delta match
   what it learned from; transient/rate-limit/auth drifts never consult or populate L0. Add the invariant as a test:
   on every scenario, warm is never worse than cold (verified and correct-escalation).
   **Root cause, traced by the owner (2026-10-29):** the warm cache from `warm_knowledge(dev split)` has 3 entries;
   one is `atlaspms::5242d3f029ee9349 → offset_to_cursor`. That signature is the *empty* delta (every flag false,
   every detail empty): the offset_to_cursor repair was derived from signals/drift class, but its fingerprint delta
   carries nothing, and it was cached anyway (the existing guard only skips a `None` delta, not an all-empty one).
   `rate_limit_429` (class UNKNOWN), `transient_503` (UNKNOWN) and `duplicate_late_records` (SEMANTIC_VALUE_DRIFT)
   all derive the same empty delta on Atlas, so L0 hits and replays the offset_to_cursor patch at rung 0 — reproduced
   directly with `run_engine_arm` (cold: escalate rung 4; warm: propose rung 0, rule offset_to_cursor). Fix all three:
   (a) never cache, and never look up, a delta that carries no change; (b) the L0 key includes the drift class, so a
   cached pagination fix can never answer an UNKNOWN or semantic drift; (c) offset_to_cursor's derived delta must
   carry the pagination evidence it was repaired from (it is empty today), or it can never be learned safely.
3. **No frontier baseline ran, so there is no comparison yet.** `frontier_oneshot` stops before calling the model:
   `BudgetExhausted: max_frontier_tokens 20000 exceeded (now 312xx)` — the prompt alone is over the ceiling,
   apparently because it carries the whole docs volume. Give it the same docs the engine arm reads for that connector
   (one vendor's docs, not all of `simulators/docs`), so it fits the shared 20k budget; if it still cannot fit, report
   that honestly as the result rather than raising the ceiling for one arm. `frontier_agent` fails with `opencode CLI
   not found` on the host: make the bench say up front which arms can run and why, and document running the bench
   inside the sandbox (OpenCode is there) as the supported path for that arm.
4. **Grading labels:** `success: true` on every engine row while the verifier grade says otherwise; rename the raw
   field (e.g. `ran`) or drop it so nobody reads it as a pass.
Acceptance: rerun `BENCH_TRIALS=1 make bench-ablation` and record the table in BUILD-NOTES each time; target
AutoBridge cold 6/6 repairs, warm ≥ cold everywhere, 0 wrong patches, frontier_oneshot actually called (tokens > 0).

### REQ-021 — Repeatable demo: full reset, pinned vocabulary, and proof of when the frontier model is called (2026-10-28)
Status: IN_PROGRESS (sbx1 since 2026-10-28 19:10; matrix full-climb merged, remaining: owner record-frontier capture + live step-4 check)
Owner: every demo scenario must run the same way every time, including REQ-020's "before" refusal after the vocabulary
has been extended, and the demo must show that the frontier model is called only when cheaper rungs cannot handle the
error. Today: `POST /demo/reset` (`src/autobridge/control/routes/demo.py`) resets only sim-atlas and sim-harbor; the L0
knowledge cache, connectors, releases, runs and budgets survive, so a second run differs (L0 hit, existing release).
`ANTHROPIC_API_KEY` is empty in the owner's `.env`, so no live frontier call is possible yet.
Wanted:
1. **Full reset.** `POST /demo/reset` and `make demo-reset` return the system to a known first-run state: every
   simulator (Atlas, Harbor, LegacyStay, Meridian when it exists) to baseline, the L0 knowledge cache (in-process and
   persisted) emptied, demo connectors with their releases, runs, drift events, approvals and budgets removed (or a fresh
   database), and the model-usage ledger archived per demo session rather than mixed across runs. Idempotent. Test: run
   a scenario, reset, run it again, and assert identical outcome, rung, and token counts.
2. **Pinned vocabulary.** A per-build setting (request field and env default, e.g. `spec_vocabulary: v1 | v2`) that
   limits discovery and the runtime to a vocabulary version. REQ-020's "before" runs with v1 and must refuse with
   `Blocked`; "after" runs with v2 and connects. Both are repeatable forever after v2 ships. The UI and `make demo` show
   which vocabulary a build used, so the demo never pretends v2 does not exist. The refusal itself costs 0 frontier and
   0 small-model tokens: detecting "needs POST with body auth" is deterministic.
3. **Why each rung was used.** Every build and repair run records each rung it tried (L0 → L4) with a one-line reason
   for moving up (e.g. "L0 miss: no known fix for vendor+delta", "L1: no rule precondition matched", "L2 confidence
   0.41 < 0.70", "budget exhausted"), linked to its `ModelCall` rows (tier, purpose, tokens, estimated cost). The repair
   page shows it as the climb (DIRECTION.md signature), and `make demo` prints one table per run:
   scenario | rung that resolved it | model calls | small tokens | frontier tokens | why it escalated.
4. **Scenario matrix test.** `tests/e2e/test_model_usage_matrix.py` declares, for every fault scenario, the expected
   highest rung (e.g. `offset_to_cursor`, `rename_guest_id`, `cursor_repeat_token`, `nested_path_shift` → L0/L1, 0 model
   calls; `rate_limit_429`, `transient_503` → no patch, 0 calls; `gross_to_net` semantic drift → must not be fixed
   below L3, escalates; the refusal → 0 calls). It fails if a scenario calls the frontier when it should not, or skips a
   rung it should have tried. Runs against the live stack (REQ-010 style) and in-process.
**Owner check after sub-step D (2026-10-28 21:10), fix before the next REQ-021 sub-step:**
- *Missing migration.* Local mode keeps `.local/autobridge.db` across runs. After sub-step D, `GET /repairs` answers 500
  (`sqlite3.OperationalError: no such column: repair_runs.ladder_trace`) and demo step 3 fails; a fresh database works.
  Add an Alembic migration for every schema change since the last one (the repo has `alembic/`), run migrations on
  control startup (Docker and local mode) and in `make demo-reset`, and add a test that upgrades a database created by
  the previous schema and then serves `/repairs`. Never require users to delete their database.
- *gross_to_net never climbs.* Demo step 4 shows `rung_attempts: [{rung 4, escalate}]`, 0 model calls: it jumps from
  the start to L4. The demo must show the frontier model used exactly when needed, so this scenario must record the
  real climb: L0 miss (no known fix) → L1 no rule matches (reason) → L2 small model ranks the gross/net hypotheses and
  is not confident enough (confidence and threshold) → L3 Muse frontier model is called (tokens and cost in the
  ledger) and proposes a patch → the verifier rejects it (`invalid_patch_trap`, or if the proposal is correct, it is
  checked and the run still needs operator confirmation per the scenario's rules) → L4 one operator question. With
  `FRONTIER_API_KEY` unset the run uses replay (item 5) or says plainly that L3 was skipped for lack of a provider, never
  silently. Update REQ-021 item 4's matrix test to require an L3 attempt for `gross_to_net`, and assert that every
  other main-script step makes 0 frontier calls. Concretely, sub-step E (`640cd850`) pinned the current skip as
  expected: in `tests/e2e/test_model_usage_matrix.py` the `gross_to_net` and `invalid_patch_trap` rows have
  `"expected_levels": [0, 4]`. Those rows are wrong by owner decision: change them to the full climb
  (`[0, 1, 2, 3, 4]`, with an L3 frontier call recorded and the verifier's rejection of the L3 proposal), then make the
  ladder satisfy them. Do not keep `[0, 4]` because the current code does that.

**Owner check after `65d009fe` (2026-10-28 22:00), fix next:**
- *Migration still skipped for pre-Alembic databases.* The owner's existing `.local/autobridge.db` (created before
  migrations ran) now carries `alembic_version = 0006_repair_ladder_trace` but still has no
  `repair_runs.ladder_trace` column: startup **stamped** an unversioned database as head instead of upgrading it, so
  `GET /repairs` still answers 500 and demo step 3 fails. For a database with tables but no `alembic_version`, detect
  the schema it actually has (stamp the last revision whose columns all exist, or the baseline) and then run
  `upgrade head`; never stamp head blindly. Add a test that starts from a database created by the pre-Alembic schema
  (and one wrongly stamped at head without the column) and ends with every column present and `/repairs` 200.
- *Demo step 4 asserts what the model must answer.* `step4_hard_semantic_failure` (`src/autobridge/bench/demo.py`)
  asserts that the frontier proposal is the `net_amount → total_amount` decoy. With `FRONTIER_API_KEY` set (owner's
  host, live Muse) the proposal is `[]` and the step fails; it only passes on replay, and
  `demo/recordings/gross_to_net.json` says itself that its responses are hand-written stand-ins, not a capture. A demo
  must not depend on the model giving a scripted answer, and stand-ins must not be shown as model output. Fix:
  1. Assert outcomes, not the model's words: L3 was called and logged (tokens, cost); whatever it proposed went through
     the verifier; no unverified patch reached a release; the run ends `Degraded` with one operator question (or, if
     the proposal is correct and verified, the scenario's rule for semantic drift still applies). Show the actual
     proposal and the verifier's verdict on it.
  2. Find out why the live proposal is empty (token cap eaten by reasoning, response parsing, prompt) and fix it; log
     the raw response (redacted) on parse failure. Spark spends most of a small `max_tokens` on reasoning.
  3. Add `make record-frontier SCENARIO=<name>` that runs the climb live with the owner's key and writes a redacted
     recording (model id, timestamp, prompt hash, raw text, tokens) to `demo/recordings/`. The owner runs it; commit the
     capture. Delete the hand-written stand-ins; until a capture exists, replay mode says "no recording" and the demo
     labels L3 as skipped instead of inventing an answer.
  4. Label replayed L3 output in the UI and `make demo` as "replayed from a live Muse capture on <date>".

**Owner check after `ff70f201` (2026-10-28 22:45), fix next:** migration now upgrades both kinds of old database
(verified on the owner's host). Live Muse now returns a proposal in demo step 4, but the step **crashes**:
`patch does not apply: can't replace a non-existent object 'value'`. A model proposal that does not apply, does not
parse, or fails schema validation is a normal outcome, not an error: record it on the rung (`L3: proposal rejected:
patch does not apply (<reason>)`, with the proposal and its tokens), never let the exception escape the ladder, and
continue to L4 with the operator question. Same for every rung that can propose (L2, L3) and for the build path. Add
unit tests with proposals that do not apply, are malformed JSON, and target paths outside the spec, and an e2e
assertion that `make demo` step 4 passes live (owner's host) and in replay. After this lands the owner runs
`make record-frontier SCENARIO=gross_to_net` once and commits the capture.

**Owner check after `6f686e22` + `d5b5ed2a` (2026-10-28 23:05), fix next:**
- *L3 is called live but reported as skipped.* `make demo` step 4 on the owner's host (live Muse, `FRONTIER_API_KEY`
  set): the ledger shows `mode: live`, 2 calls, 544 small + 2 115 frontier tokens, $0.0109, yet the step says
  `rejection_reason: no frontier call available (no FRONTIER_API_KEY, no replay recording); L3 labelled skipped` and
  `l3_proposal: []`. The frontier answered and the engine either could not parse it or dropped it, then mislabelled it.
  The reason must say what happened (e.g. "L3 called live (muse-spark-1.3, 2 115 tokens): response contained no
  applicable patch: <parse error / reason>"), show the raw response (redacted, truncated) on the run, and the
  "skipped" wording is reserved for when no call was made. Then fix why the live response yields no patch (token
  budget for Spark's reasoning, prompt, or parser). Add a test where the provider returns text with no patch and one
  where it returns an unparsable patch, asserting the reason text.
- *Local mode serves a stale UI.* `scripts/local-up.sh` builds the UI only when `ui/.next/BUILD_ID` is missing, so after
  a `git pull` the owner got the old build (no `/knowledge` page, 404). Rebuild when the UI sources changed since the
  last build (store the git tree hash of `ui/` next to the build and compare), and say so in the output.
- *Knowledge page details:* the v2 vocabulary row lists only `POST` and the new features; say "v2 = everything in v1,
  plus: …". Connector profiles show `vocabulary ?`: show the pinned vocabulary. L1 rule rows repeat the rule name
  ("`rename_field` rename_field rule — …"): show the name once with its description.

5. **Reliable frontier calls in the demo.** Two modes, chosen by env: `live` (a real frontier provider; model ids in
   config, never in code) and `replay` (responses recorded from a live run, stored redacted under `tests/fixtures/` or
   `demo/recordings/`, replayed deterministically by prompt hash; a missing recording fails loudly). The UI and
   `make demo` label replayed responses as replayed. Default: `live` when a frontier key is configured, else `replay`.
   **Owner decision (2026-10-28): the frontier tier is Meta Muse** through its OpenAI-compatible chat completions API
   (the same API the ralph loop uses: base URL `https://api.meta.ai/v1`), model Spark (`muse-spark-1.3`) or Wonder
   (`muse-wonder-1.0`), chosen by config. New settings, read only from env, never logged or stored: `FRONTIER_API_KEY`
   (the owner generates a dedicated Muse key), `FRONTIER_BASE_URL` (default `https://api.meta.ai/v1`), `FRONTIER_MODEL`
   (default `muse-spark-1.3`). Implement the OpenAI-compatible provider behind the existing `LLMProvider` Protocol
   (today a stub) with the same `Budget` and `ModelCall` ledger; Anthropic stays available as an alternative. Pass
   these to the engine services in `compose.yaml` (today no LLM key reaches control or worker at all) and to local
   mode (REQ-017); document them in `.env.example`; extend the SSE/evidence redaction to `FRONTIER_API_KEY`. The small
   tier (L2) gets the same treatment (`SMALL_MODEL`, default the same provider and key) unless the plan says otherwise.
   Cost: record estimated cost per call from the provider's published prices in config (Spark $1.25 / $0.15 cached /
   $4.25 per M; Wonder $4 / $0.20 / $20), shown in the per-run table.
6. **Show what the engine knows and what it has learned.** The demo must make learning visible, and keep two things
   apart: the *vocabulary* (what the connector language can express; changes only when code ships, e.g. v1 → v2) and
   *learned knowledge* (what the engine picked up from verified repairs and builds; grows at runtime, cleared by reset).
   - `GET /capabilities` (and `autobridge capabilities` in the CLI): the vocabulary per version, i.e. HTTP methods,
     auth types, pagination strategies, transforms, checks, L1 rules with what each one fixes, and the v1 → v2 diff.
     Also exposed as a tool listing (MCP `tools/list` style: name, description, input schema) so an agent can ask
     "what can you do".
   - `GET /knowledge` (and `autobridge knowledge`): the learned entries, one per known fix: vendor, drift signature,
     the JSON Patch, where it was learned (run id, rung, tokens it cost the first time), when, times reused, tokens saved
     since. Plus each connector's learned profile (bootstrap hypotheses that were confirmed).
   - A UI page "What AutoBridge knows" with both sections, linked from the top bar.
   - Demo flow it must support, repeatably after `make demo-reset`: knowledge list empty → inject a drift → climb to
     L1/L2/L3, approve → a new entry appears on the page → inject the same drift again → L0 hit, 0 model calls, the
     entry's "times reused" goes up. Assert this sequence in an e2e test.

### REQ-024 — Make the held-out claim checkable, and prove the engine beats an untrained frontier model (2026-10-28)
Status: IN_PROGRESS (sbx1 since 2026-10-28 22:48; sub-step A merged, sub-step B code-complete 2026-10-28: opencode default, 5 arms, metrics + CI, curve, prereg, bench wiring; leak-audit false-positive fix merged 2026-10-29; remaining: owner check "on f32b4b4b" FIXED 2026-10-29 (end-to-end engine arms, bench moved out of src/autobridge, rehearsal numbers reported); then live frontier numbers with FRONTIER_API_KEY + Nov 3 held-out run)
Owner: "Ralph never saw the held-out data" and "AutoBridge beats a frontier model on its own because the engine knows
more" must be things a judge can check, not things we say. Never read, print, move or regenerate the private seeds;
never touch `verifier/hidden_cases/heldout/SHA256SUMS`. Everything below that needs the seed files is a script the
**owner** runs; scripts print counts and hashes only, never seed or nonce values.
**A. Held-out integrity, checkable**
1. *Leak audit* `scripts/heldout_leak_audit.py` (owner runs it with `HELDOUT_DIR`): reads each private file's nonce and
   seed in memory and its sha256, then scans every `ralph-logs/*.jsonl` transcript, every blob in the full git history
   (`git rev-list --all` + `git cat-file`), every file under `.worktrees/`, and the sandbox-visible project folder for
   any occurrence of a nonce, a seed file's sha256, or a seed file's raw bytes. Output: one line per source with
   counts ("0 hits in 109 transcripts, 0 in 2 140 blobs …") and exit 1 on any hit. Unit-tested with a fake heldout dir.
   The audit result is written into the scorecard.
2. *Isolation evidence*: a doc section (docs/benchmark.md "Held-out protocol") showing the sandboxes mount only the
   project folder (`sbx ls` workspace column), `HELDOUT_DIR` is outside it, the bench reads it only for heldout runs,
   and the freeze commit (`ff372083`) with its GitHub push time. Add a CI-free check script that prints these facts.
3. *Public timestamp*: document the owner step to create a signed tag `heldout-freeze` on `ff372083` and a GitHub
   release for it (GitHub records the time; optional OpenTimestamps proof of `SHA256SUMS` committed beside it).
4. *Reveal after the run*: after the single Nov 3 run, the owner publishes the seed files under
   `verifier/hidden_cases/heldout/revealed/` (an owner commit). Add `make verify-heldout-reveal` that checks them
   against the frozen `SHA256SUMS` and reruns `make bench-heldout` from them, so anyone can reproduce the scorecard.
   Until the owner does this, the folder must not exist (guard test).
5. *Honest scope* in docs/benchmark.md: what is sealed is the instance data (seeds); the four scenario definitions are
   public and the builder saw them. Unseen-vendor generalization is measured separately on LegacyStay (REQ-019 item 5).
**B. Engine vs. untrained frontier model, same model on every arm**
1. Every arm that calls a frontier model uses the same `FRONTIER_MODEL` through the same provider (REQ-021), so the
   only difference between arms is the engine. Add a coding-agent driver that runs OpenCode headless with that model
   (`BASELINE_DRIVER=opencode`), and make it the default instead of `claude`; keep `claude` / `codex` available.
2. Ablation arms, all under the same matched budget (tokens, wall clock, docs, sandbox, credentials, visible tests; the
   existing `assert_inputs_equal` self-test extended to every arm):
   a. `frontier_oneshot`: the model alone, one prompt with the docs.
   b. `frontier_agent`: the model as a coding agent with tools (the untrained-frontier baseline).
   c. `engine_no_knowledge`: AutoBridge with L0 and L1 disabled (ladder, verifier gate and budgets kept, so only the
      model repairs).
   d. `autobridge_cold`: full AutoBridge, empty learned knowledge.
   e. `autobridge_warm`: full AutoBridge after learning on the dev split only (never on held-out).
   c vs d isolates what the engine's built-in knowledge (rules + known fixes) adds; d vs e isolates learning; b vs d is
   the headline.
3. Metrics per arm, scenario and overall, with mean and 95 % interval over ≥ 5 trials (different seeds derived from the
   private seed for held-out, from the dev seeds otherwise): verified completion rate (judged only by the verifier),
   wrong patches that reached a release (must be 0 for AutoBridge), correct escalations (escalated exactly when the
   scenario says it must), frontier tokens, estimated cost (REQ-021 prices), wall time.
4. *Learning curve* (dev split): the same drift injected 5 times in a row; plot frontier tokens per repair for b, d and
   e. Expected: b pays every time, d pays once then 0, e is 0 from the start. Saved as `experiments/learning_curve.md`
   with the raw rows.
5. *Pre-registration*: before the Nov 3 run, commit `experiments/preregistration.md` (arms, metrics, trials, budgets,
   primary hypothesis, e.g. "on held-out, `autobridge_cold` reaches a verified-completion rate at least that of
   `frontier_agent` with at least 10× fewer frontier tokens", and that results are published whatever they are). The
   scorecard links the pre-registration commit and the leak-audit result.
6. Dev-split rehearsal: `make bench` runs all arms on the dev split now (real numbers, REQ-008), so the protocol is
   debugged before the one held-out run.

**Owner check after sub-step A (2026-10-29, on e1960ca7): the leak audit always FAILs, on false positives.** Owner ran
`HELDOUT_DIR=~/autobridge-heldout python3 scripts/heldout_leak_audit.py` on the host: `FAIL` with transcripts 3 hits
(ralph-logs iter8 2026-10-27, iter5 2026-10-28, iter16 2026-10-29), worktrees 1 hit
(`.worktrees/sbx1/verifier/hidden_cases/heldout/SHA256SUMS`), project 4 (the same four). Classified by token kind
(owner-side, no values printed): **every hit is a sha256 hex from the committed manifest; 0 raw-file hits, 0 nonce
hits.** The sha256 values are public by design (they are in `SHA256SUMS` in the public repo; that is the point of
the freeze), so seeing them proves nothing. Fix: (1) search only for material that is actually secret, the raw seed
file bytes and the nonce (and any other field the seeds hold that is not in the public manifest), never the
manifest's hashes; (2) the public-manifest exclusion must cover every copy of the manifest (worktrees too), by
content as it does now, not by path; (3) report hits per kind (`raw`, `nonce`) in the counts-only output and JSON so
a FAIL says what leaked; (4) unit tests: a transcript quoting the public manifest is a pass, a transcript containing
a nonce or raw bytes is a fail. Expected on the owner's host after the fix: `OK`, 0 hits. Keep "never prints secrets".

**Owner check (2026-10-29, on f32b4b4b): the engine arms are handed the answer; the comparison is not matched. Fix
before REQ-025 builds on it (the beacon run reuses these arms).** In `src/autobridge/bench/ablation.py` the engine
arms call `RepairController.handle(...)` with case-supplied `drift_class` (e.g. `gross_to_net`, which is the answer
label), `fingerprint_delta`, `hypotheses` (hand-written in `_gross_net_hypotheses`, priors 0.41/0.39 chosen so L2
cannot decide) and `enumerator_yield_count`, all built from `config/ablation_cases.yaml`. The frontier arms get docs
and a sandbox and must find the problem themselves. So the engine is benchmarked from a pre-diagnosed state while the
model starts cold: a judge reading the code will say the headline number is rigged, and they would be right.
`assert_all_inputs_equal` passes only because these fields live outside `HarnessInputs`.
Fix: every arm gets exactly the same inputs (the pre-drift spec, the docs, the sandbox URL with the fault served by
the simulator, budgets). The engine arms run the real path end to end: sync against the faulted sandbox → detector
→ classifier → hypothesis engine → ladder → verifier gate, with no case-supplied drift class, delta, hypotheses or
yield count. The case file may hold only what every arm gets (scenario id, connector, pre-drift spec source,
budgets) plus grading data the arms never see. Add a test that fails if any arm receives a field the others do not,
including through `RepairController.handle` kwargs. Report the rehearsal numbers again after the change, whatever
they are (pre-registration: losses are published).
Second, smaller: the same commit widened the W5.7 trust-boundary test (`test_04_bootstrap_unfamiliar_api.py`) so
`src/autobridge/bench` may read `generated/connectors/*/reference/spec.yaml`, without an owner note. Reading the
pre-drift reference spec as an equal input is fine; doing it from inside the engine package is what the test exists
to forbid. Move the benchmark harness out of `src/autobridge` (e.g. a top-level `bench/` package that imports the
engine, like the tests do) and revert the carve-out. From now on, never widen a guard test without an owner note in
REQUESTS saying why.

### REQ-025 — Beacon evaluation: faults generated from public randomness nobody could know in advance (2026-10-28)
Status: IN_PROGRESS (sbx1 since 2026-10-29 00:55; sub-step A merged 2026-10-29; sub-step B code DONE 2026-10-29: client, serving, make eval-freeze/bench-beacon/verify-beacon/beacon-eval, docs, 5 live rehearsals + live repair proof; dependency RESOLVED 2026-10-29 (REQ-024 owner check fix: beacon engine arms now observe instances end-to-end via derive_beacon_diagnostics); remaining: live frontier numbers (needs FRONTIER_API_KEY), the real frozen run)
Owner: prove we could not have prepared for the test even if we wanted to. The private-seed held-out split (REQ-007)
only seals instance data for four public fault types, and the builder has tests for those types. The headline
evaluation becomes a **beacon run**: the test's data *and* fault details are derived from a public random value that
did not exist when the code was frozen. Same boundaries as everywhere: the generator and answer key live on the
simulators/verifier side; the engine never imports, reads or reaches them.
1. **Fault generator from building blocks** (`simulators/common/faultgen.py` + verifier counterpart). Input: a 32-byte
   seed. Output: N fault instances across the five vendors (Atlas, Meridian, Harbor, LegacyStay, FIAS PABX), each a
   family plus seed-chosen parameters, never a fixed scenario:
   - rename: which field, new name from a large word pool (e.g. `guest_name` → `primaryOccupant`);
   - nesting: depth and new parent key;
   - pagination: strategy switch, new parameter and cursor field names;
   - code list: new status codes and the canonical value each one means;
   - semantic: gross ↔ net, minor ↔ major units, a decoy field with a generated name;
   - dates: cutoff hour, time-zone shift, date format;
   - transport and auth: throttle rate, `Retry-After`, outage length, token expired vs revoked, endpoint removed;
   - FIAS: `PS` layout change (field added, renamed, reordered), resync, ACK timeout.
   Each instance carries its **answer key**, kept by the verifier only: the canonical truth data and the correct
   outcome class: `repair` (verifier-accepted patch on hidden cases), `escalate` (no patch; escalated), or
   `degraded_question` (ambiguous semantic: `Degraded` with one operator question, no guessed patch).
   Families and parameter pools are fixed in code before the freeze; only the parameters come from the seed.
2. **Generator tests (before any real run).** Property tests over thousands of random seeds in `make test`: every
   instance is internally consistent (the mutated vendor really serves the mutated shape; FIAS records match the
   announced layout), deterministic for a seed, and its answer key matches the oracle. A **reference solver** that is
   given the instance's parameters must reach the correct outcome class on every instance; an instance the reference
   solver cannot handle is a generator bug (no impossible questions).
3. **Beacon client**, in the bench harness (which never sees generator internals; it passes only the seed to the
   simulators and verifier). Fetch a drand round from
   the League of Entropy mainnet (`https://api.drand.sh/public/<round>` plus the chain info), **verify its signature**
   against the chain's public key with a pinned pure-Python library (fail closed if verification fails), compute the
   round for a UTC time, and derive `seed = sha256(randomness ‖ "autobridge-eval-v1")`, then per instance
   `sha256(seed ‖ instance index)`.
4. **Protocol commit (freeze).** `make eval-freeze ROUND_AFTER=<UTC time> FAULTS=<N>` writes
   `experiments/beacon/protocol.md` (round rule: first round at or after the time; N instances; arms; metrics; "one run,
   no retries, results published whatever they are"; the engine and generator commit) and refuses to run if the target
   round already exists. The owner commits it, tags `eval-freeze`, and pushes before the round time.
5. **The run.** `make bench-beacon`: reads the protocol, refuses if the round is not yet published or if a result for it
   already exists (no re-rolls), fetches and verifies the round, prints the evidence on screen (round number, time,
   randomness, verification URL, freeze commit and its push time), generates the instances, runs every REQ-024 arm on
   the same instances under matched budget, grades with the verifier against the answer keys, and writes
   `experiments/beacon/<round>/` (instances with parameters, per-arm grades, tokens, cost, time, a summary with
   intervals). Runs in Docker and in local mode (REQ-017).
6. **Rehearsals on past rounds.** `make bench-beacon REHEARSE=<round>` uses an already-published round (clearly
   labelled "rehearsal"; output under `experiments/beacon/rehearsals/`) to debug the pipeline and learn the expected
   score range. Run at least five rehearsals before the freeze; record them.
6b. **One unattended command** (owner, 2026-10-28: no time to do this by hand or on camera). `make beacon-eval
   FAULTS=<N> [DELAY_MIN=20]`: checks no ralph loop is running and the tree is clean; runs `eval-freeze` with
   `ROUND_AFTER` = now + DELAY_MIN; commits the protocol, tags `eval-freeze`, pushes both (asks once for "yes" before
   the push, unless `CONFIRM=yes`); records GitHub's push time from the API; waits for the round; runs `bench-beacon`
   unattended; writes a one-page `experiments/beacon/<round>/EVIDENCE.md` (round, time, randomness, verification URL,
   freeze commit and push time, per-arm summary) and a terminal-style `evidence.png` screenshot of it for the deck;
   commits and pushes the results. Resumable: if interrupted after the freeze, rerunning it continues the same round
   (never a new one silently). Total hands-on time: one command and one "yes".
7. **Reproduce.** `make verify-beacon ROUND=<round>`: anyone re-fetches and verifies the round, regenerates the
   instances, checks they match the published ones, and can rerun the grading from the tagged commit.
8. **Docs.** docs/benchmark.md "Beacon evaluation": why (commit-then-reveal with public randomness), what a judge can
   check, the honest scope (fault *families* are known by design: that is the engine's knowledge; the *instances*
   could not be known), and how the REQ-007 held-out split relates (kept as the frozen secondary benchmark: new data for
   known fault types).
Constraint: the REQ-007 held-out files and `SHA256SUMS` stay untouched; `make bench-heldout` keeps working.

---

## Resolved Requests (reference only)

### REQ-053 — First owner demo run (UI, keyed, 2026-10-30): fix what broke (2026-10-30)
Status: DONE (sbx1 2026-10-30)
Owner ran decks 1–7 in the browser on `make local-up` (origin/main f8554ecc), after `make demo-reset && make
demo-seed`. REQ-050's gates passed because `make demo` builds Meridian/PABX through the API; **the build page path
broke**. Every acceptance check below runs **through the UI build page's own requests** (Playwright, or API calls
byte-identical to what `NewConnectorForm` sends), keyed and unkeyed.
1. **Menu label:** top bar "What AutoBridge knows" → **"Knowledge"**. The page title stays.
2. **Connector page layout:** the header facts row overlaps the state line (the "Entities Reservation, FolioCharge ·
   Open drift 0 open" line sits on top of "● Working · in sync · Complete …"). Fix the overlap; give **Sync runs** a
   fixed height with its own scroll.
3. **An approved repair still shows as waiting.** Repair 791a6308 (Atlas `page_to_cursor`) was approved (approval
   eb15e2e6 by Eric, r2 da31da08 active), yet `/connectors` shows "r1 waiting", a "Review repair 791a6308 · needs your
   approval" button, and "Waiting for you" under Recent repairs, while the header says "nothing waiting for you". Every
   "waiting" in the UI must come from the same source as the header (connector state / an approval row exists ⇒ not
   waiting).
4. **After Approve, the button is still live.** Once the approval is recorded it becomes "Approved ✓" (disabled); same
   for Reject/Deny.
5. **Harbor built from the wiki:** the build page must say the check came from the docs ("check added from the docs:
   totals must match `meta.gross_sum`", REQ-049 item 2). The spec has the check; the page never says so.
6. **Deck 5: Approve must turn Harbor green.** The model proposed `replace /entities/Reservation/checks/3/compare_to
   "$.net_sum"` — a field Harbor never sends. It applied, r2 went live, the verifying sync was Degraded
   (`money_invariant`), Harbor stayed "needs your decision", and a second escalation (ddbf32c3, no proposal) followed.
   - **Dry-run every proposal before offering Approve:** apply it to the active spec and evaluate the failing check(s)
     against the **current faulted records** (from the break's sync run, no new vendor call needed); the checks must
     pass. If not, **one retry** to the model with the exact error (e.g. "`$.net_sum` is not a field in the response;
     available: …") — generic wording, no scenario names. Still failing → no proposal is offered (question only). The
     dry-run result is shown with the proposal ("tested on the current records: the check passes").
   - The engine never synthesises the answer; the dry-run only judges the model's proposal.
   - If a verifying sync after an approved proposal still fails: **automatic rollback** to the previous release,
     labelled on the repair page and in Decisions; the original escalation stays the one open item — **never a second
     escalation for the same open drift**.
   - Test: keyed and with a fake model that first proposes a non-existent field, then a valid change → Approve →
     verifying sync Complete → Harbor ● Working.
7. **Meridian v1 → v2:** the vocabulary check runs **before any connector is created**. A refused build creates no
   connector; the build page shows a clear headline: "Refused by vocabulary v1: this API needs request bodies (POST).
   Build with v2." (reason underneath, as today). The v2 build then creates the **one** Meridian connector, whose page
   shows the earlier v1 refusal as history. Same for the PABX on v1/v2.
8. **Meridian built from the UI on v2 is Blocked: `http_error`** — its requests never reach `sim-meridian` (no sync
   calls in `.local/logs/sim-meridian.log`). Find what the UI build path leaves unset (base URL / credential ref /
   auth) compared with how `make demo` builds it, and fix the UI path. Acceptance: UI-built Meridian syncs `Complete`.
9. **PABX built from the UI on v3 is Blocked: `feed_unavailable`** while the edge agent is heartbeating to the relay.
   Same: find what the UI path leaves unwired versus `make demo`, fix it. Acceptance: UI-built PABX ● Working with
   postings flowing; then `fias_link_resync` → reconnects, nothing lost or duplicated (deck 7).
10. **Acceptance run:** one continuous take from `make demo-reset && make demo-seed`, decks 1–7 through the UI (deck 4
   build Harbor, deck 5 break → Approve the model's tested proposal → **Green**, deck 6 Meridian v1 refused (no
   connector) → v2 Working, deck 7 PABX v3 Working → link drop), keyed and unkeyed (unkeyed: deck 5 shows the labelled
   replay/skip path). Report it in BUILD-NOTES with what the screen showed per deck.

Resolution: all 10 items code-complete — UI build path fixed (vocabulary check-first, simulator credential/entities suggestions, probe_wire_shape), approved repairs terminal (waiting derives from decision), dry-run every proposal with one retry then question-only, auto-rollback on failed verifying sync with no second escalation, docs updated; full gates green unkeyed + make demo 8/8 + live UI-path acceptance decks 4-7. Owner keyed steps outstanding: deck-5 Approve-to-green with FRONTIER_API_KEY, Aside screenshots.

### REQ-050 — Decks 6 and 7: build Meridian live (v1 refused, v2 connects) and the PABX live from its spec (2026-10-30)
Status: DONE (sbx1 2026-10-30)
Owner decision (2026-10-30). Today `make demo-seed` builds Meridian twice behind the scenes (v1 refused, v2 release;
`bench/demo_seed.py` ~181–205) and deck 6 only points at the result. The owner wants it built live in the UI, like
Harbor in deck 4. The control API already takes `spec_vocabulary` per build (`control/routes/builds.py` ~176–192); the
Build a connector page (`ui/app/connectors/new`) does not expose it. Scope is exactly:
1. **Seed:** `make demo-seed` no longer creates MeridianPMS **or the PABX edge connector** (opening frame: AtlasPMS
   only; Harbor is already out per REQ-049). `make demo` keeps working stand-alone (its Meridian step builds Meridian itself, as today).
   Update connector-count wording everywhere.
2. **Build page: choose the vocabulary version** for a build (v1 / v2 / v3, defaulting to the current default), with
   each version's one-line release note (the Knowledge page's `RELEASE_NOTE`).
3. **v1 build of the Meridian docs is refused on screen**, with the engine's existing named reason (request bodies /
   POST not in ConnectorSpec v1); no release created; the attempt stays recorded on the connector page (as the seed
   records it today).
4. **v2 build of the same docs connects**: release r1, first sync `Complete`, MeridianPMS ● Working; the connector page
   shows both attempts (v1 refused with its reason, v2 in use) and the Knowledge page lists Meridian under v2.
5. Deck 6 has **no break** (`meridian_cursor_renamed` stays in the catalogue, not in the demo script). README and
   `docs/demo.md` describe deck 6 as: build Meridian on v1 → refused (no POST) → build on v2 → connected.
6. **Deck 7: build the PABX live from its spec** (owner, 2026-10-30). Build a connector → **FIAS PABX docs**
   (`simulators/docs/fias-pabx.md`) → vocabulary **v3** → Build → release → the edge agent connects → postings flow →
   PABX edge **● Working**. Today the seed does this through the API (`bench/demo_seed.py` ~96–106, `runtime_target:
   edge`, base URL `edge-relay://queue/fias-commands`) and the build page cannot: it hard-codes
   `runtime_target: local_process` (`ui/components/NewConnectorForm.tsx` ~351) and expects an http base URL.
   **No runtime choice on the page** — it is a demo of the engine: the engine decides where the connector runs from
   the docs (it already decides the transport by protocol, `discovery/bootstrap.py` ~1189: framed TCP / FIAS → the
   on-premise edge agent and the relay address; HTTP → cloud), and the build page **displays** that decision as a
   message on the result ("runs: cloud" / "runs: on-premise edge agent, via the relay"). No base-URL typing for the
   PABX on camera.
7. **Then deck 7's break is a link drop** (owner, 2026-10-30: the most common real FIAS failure):
   `make demo-break SCENARIO=fias_link_resync` on the live-built PABX connector → the PABX panel shows the link go
   down, the agent re-handshakes (LS/LD/LR/LA), the link comes back, postings resume, **nothing lost, nothing
   duplicated** (counts shown), connector ● Working throughout or back to ● within one tick. No repair, no Approve.
   `fias_record_layout_change` and `agent_restart_inflight` are **not** part of the demo script.
Tests: seed without Meridian or PABX; build page shows the engine's runtime decision (cloud for
HTTP docs, edge agent for the FIAS docs) and sends no runtime choice; FIAS docs v3 build → edge release → postings
→ Working; link drop + resync on it loses and duplicates nothing; build page sends `spec_vocabulary`; v1 refusal shown with reason and no release; v2
build connects; connector page shows both attempts. Gates unkeyed + `make demo` 8/8.
Resolution: seed is AtlasPMS only; build page carries vocabulary release notes, sends no runtime choice, hides base-URL typing for the PABX and shows the engine's runtime decision; framed docs builds set the edge runtime on the record; connector detail lists build attempts (v1 refused with reason, v2 in use); demo step 8 is the link-drop resync proved on relay idempotency keys (0 lost/duplicated); layout change and restart leave the demo script (REQ-051 deferred); README + docs/demo.md decks 6/7 rewritten. Gates green unkeyed: make test (90% gates), ui 231, lint/typecheck/ui-typecheck/openapi-check, make demo 8/8 + STEP=7/8 alone, tests/e2e/test_09 5/5 live. Owner check after: Aside run of decks 6 and 7.

### REQ-049 — Deck 5 Green → Red → Green: Harbor built from its wiki carries the totals check; the model proposes "accept the vendor's change" for the human to approve (2026-10-30)
Status: DONE (sbx1 2026-10-30)
Deck order (owner, 2026-10-30): 1 knowledge · 2 Atlas reusable · 3 Atlas zero-token fix · **4 build Harbor from its wiki · 5 break Harbor right after** · 6 Meridian · 7 PABX · 8 proof. Use this numbering in README/docs/demo.md.
Owner decision (2026-10-30). Today deck 5's totals check comes from the **hand-written** Harbor reference spec
(`generated/connectors/harborpms/reference/spec.yaml` ~127, `money_invariant` vs `$.gross_sum`) loaded by
`make demo-seed`; a Harbor built from its docs gets no such check (discovery never emits `money_invariant`; the wiki
never documents `meta.gross_sum`, though `simulators/harborpms/app.py` ~185 returns it), and the escalation ends Red
with nothing to approve. Wanted flow, one connector, one take: deck 4 builds Harbor from its wiki (● Working, with the
totals check) → deck 5 `gross_to_net` → ■ Error, the model's question **and one proposed change** → presenter clicks
**Approve** ("accept net") → ● Working; or **Deny** ("keep everything as-is, contact the vendor") → stays ■ Error.
1. **Docs.** Harbor's wiki page (devportal) and `simulators/docs/harborpms.md` document the page-level
   `meta.gross_sum` ("sum of the gross amounts of the records on this page") for reservations and folios, in the wiki's
   existing stale-wiki voice. Do not touch LegacyStay (never tuned on) or held-out material.
2. **Discovery adds the check from the docs.** When the docs describe a page-level total for a money field, the built
   spec gets the `money_invariant` check for that field (`compare_to` the documented path); never inferred when the
   docs are silent (never-guess stands). The build page shows it ("check added from the docs: totals must match
   `meta.gross_sum`"). Generic: keyed on "docs describe a total of a money field", not on Harbor.
3. **Seed.** `make demo-seed` no longer creates Harbor (three connectors: Atlas, Meridian, PABX edge). Deck 4 builds it
   in the UI; `make demo` keeps working stand-alone (it builds Harbor from docs itself; the hand-written reference spec
   stays for tests/bench only where they need it). Update the "four connectors" wording everywhere.
4. **The model may propose one change for a human decision — a real proposal, not a canned one.** Extend the L3 answer
   format in `config/prompts/l3_patch.md` generically: besides `{"patch"...}` and `{"escalate"...}`, the model may answer
   `{"escalate": "<question>", "rationale": "...", "proposal": {"label": "<short button text>", "effect": "<what
   Approve does, one sentence>", "patch": [<RFC 6902 ops on the allowed spec pointers>]}}` — "if the operator accepts
   the vendor's new behaviour, this is the change". The prompt explains the option generically (a business decision the
   evidence cannot settle may come with the change the operator could approve) and **must not mention gross, net,
   totals, Harbor or this scenario**; a test fails if any prompt file or engine module contains scenario-specific
   wording for this. The proposal patch passes the existing apply + allowed-pointer check (one retry on error, as
   REQ-044); a malformed proposal is dropped, labelled, and the repair stays a plain escalation. The engine never
   synthesises a proposal itself.
5. **Approve / Deny on the repair page** (escalation top box from REQ-048): the model's question, its rationale, and two
   actions — **Approve: <model's label>** with its effect and the diff, and **Deny: keep everything as-is, contact the
   vendor** (generic, always present; = reject: connector stays ■ Error, drift stays open, note recorded). Approve
   creates release r2 from the proposal, activates it, runs the verifying sync; Harbor → ● Working.
6. **Honest labels.** An approved proposal is recorded and shown as **"business decision by <actor>"**, not "verified
   fix": the verifier confirms r2 syncs and its checks pass but makes no claim that the new meaning is correct, and
   the verifier's trap list (`verifier/invalid_patches.py`) still rejects the same change when the engine tries to
   apply it on its own. Business decisions are **not** learned into L0 knowledge (next time, it asks again); the
   Knowledge page **must** show them (see 9). Rollback to r1 stays available.
7. **Tests:** docs with a documented total → built spec has the check; docs without → none; Harbor seed absent;
   prompt contains no scenario wording; parse escalate+proposal / escalate-only / malformed proposal; proposal patch
   apply-check + retry; Approve creates r2 labelled business decision, no knowledge entry; Deny keeps Error; UI tests for
   both buttons. Gates unkeyed + `make demo` 8/8 (deck order: build Harbor before breaking it).
8. **Owner check after:** keyed, run deck 4 → deck 5 three times from one seed each; report how many runs the model
   proposed a valid "accept the vendor's change" and the labels it chose (a real proposal can vary; if it proposes
   nothing, deck 5 ends Red with the question — no fallback option); then the Aside UI run Green → Red → Approve → Green
   with screenshots.
9. **Every human decision is recorded and shown (owner: "we need to show that we record all decisions").** The
   Knowledge page gets a **Decisions** section, separate from learned knowledge, listing every operator decision on
   every connector, newest first: approve (fix → release, and whether it was learned), approve of a model proposal
   (**business decision**, not learned), deny/reject (kept as-is, contact the vendor), rollback (from → to). Each row:
   when, who, connector, what was decided in one line (e.g. "HarborPMS amounts are net from now on"), the repair and
   release it touched (links). Source of truth is the existing approval/rejection/rollback rows (no second store); a
   reset clears them like the rest. The demo ends by returning to `/knowledge`: learned fixes (Atlas `page_to_cursor`,
   Meridian cursor rename) on one side, the decisions (including Harbor's) listed. Tests cover each decision kind and
   that a business decision never appears as learned knowledge.
Resolution: Harbor wiki + harborpms.md document meta.gross_sum; discovery adds money_invariant from documented totals (generic, build page names it); seed drops Harbor; L3 answers escalate+proposal (generic prompt, apply-check + one retry, malformed dropped); approve-proposal endpoint mints verifier-gated r2 labelled business decision (never learned, rollback kept); Deny keeps error; Knowledge Decisions section lists every decision; deck order + docs rewritten; replay fixture re-keyed for the new prompt/pointer hashes; full gates green unkeyed + make demo 8/8 twice + STEP=4 alone + test_09 e2e 5/5. Owner check after: keyed deck 4→5 x3 + Aside Green→Red→Approve→Green + make record-frontier re-capture (new l3_patch v2 hash).

### REQ-045 — Every model prompt lives in a file loaded at startup, and each one asks a well-posed, specific question (2026-10-30)
Status: DONE (sbx1 2026-10-30)
Owner audit (2026-10-30), after REQ-044 showed the L3 patch prompt was ill-posed: the other engine prompts have the
same defect — they are one-line stubs in code that do not give the model what it needs to answer.
- **L2 ranking** (`repair/ladder.py` `_call_small_model_rank` ~230; callers `ladder.py` ~718, ~1707,
  `discovery/resolver.py` ~346): asks to "rank by prior and evidence" but sends **only hypothesis ids and priors** —
  no evidence, drift, or values. The model can only sort by prior, which code already does; L2 adds nothing.
- **L3 hypothesis generator** (`ladder.py` `_call_frontier_generate` ~315; callers `ladder.py` ~651,
  `resolver.py` ~179): "propose one additional hypothesis" with only the existing ids and "need novel hypothesis for
  unresolved drift" — no drift, evidence or docs. A blind guess.
- **L3 patch** (REQ-044): being fixed in REQ-044; this REQ moves it to a file too.
Fix:
1. **Prompts out of code.** Every engine prompt (system + user template) lives in its own file under
   `config/prompts/` (e.g. `l2_rank.md`, `l3_hypothesis.md`, `l3_patch.md`, plus any other engine model call found by
   `git grep '\.complete('`): Markdown with a small front-matter (`id`, `version`, `purpose`, `tier`, `max_tokens`,
   declared `inputs`, `answer_format`), then the system text and the user template with named placeholders.
   `config/` already ships in the engine images (as `demo_targets.yaml` does); `AUTOBRIDGE_PROMPTS_DIR` overrides the
   directory. No prompt text remains in Python.
2. **Loaded once at startup**, not per call and not hot-reloaded: control/worker load and validate all prompts at
   boot (every declared placeholder is supplied by its call site and none is left unrendered; missing file or bad
   front-matter **fails startup** with the file name). Editing a prompt takes effect on the next engine restart —
   document this in `docs/` ("improve a prompt: edit `config/prompts/<id>.md`, restart").
3. **Traceable.** Each model call records the prompt `id`, `version` and a content hash on the ModelCall ledger and the
   rung entry; the repair page "what the engine did" shows them next to the call (and can show the rendered prompt,
   as REQ-039 already stores the messages). Replay recordings are keyed by the rendered messages as today, so an edited
   prompt shows as "replay missing — re-record" (labelled, never a silent fallback); note it in docs/demo.md.
4. **Each prompt asks a specific, answerable question** — the model is given everything it needs and a closed answer
   format:
   - **L2 rank**: for each hypothesis, its id, a one-line claim, and the evidence that supports or contradicts it (the
     wire signals, the observed vs expected values, the field and entity, redacted); the drift class; the instruction
     to rank by how well each hypothesis explains the evidence, not by prior. Answer: JSON
     `{"ranking": [ids...], "confidence": 0..1, "rationale": "<one sentence>"}`; parse failures fall back to prior
     order **labelled** on the rung.
   - **L3 hypothesis**: the drift class, the signals and evidence, the spec excerpt for the affected entity, the
     relevant docs excerpt (redacted, size-capped), the existing hypotheses with claims, and the hypothesis families the
     engine can verify. Answer: `{"hypothesis": {"id","family","field","claim"}, "rationale"}` or `{"none": "<why>"}`;
     an unknown family is rejected labelled.
   - **L3 patch**: REQ-044's prompt, moved verbatim into `config/prompts/l3_patch.md`.
   Untrusted data (docs, wire values) is fenced and labelled as data, never instructions; secrets never enter a
   prompt (existing redaction seam).
5. Tests: loader (valid, missing file, bad front-matter, unrendered/undeclared placeholder → startup error); each call
   site renders its prompt with all inputs and no secret; L2 output parse + labelled fallback; L3 hypothesis parse /
   `none` / unknown family; ledger records id/version/hash; replay round-trip with the new prompts; a test that fails
   if any string passed as `messages` to `.complete(` is built from a literal in Python instead of the loader.
6. Gates keyed and unkeyed: `make demo` 8/8 both ways. Owner check after: keyed step 4 rung entries show prompt
   id/version; edit one prompt file, restart, and the next call shows the new version.

Resolution: engine prompts (l2_rank, l3_hypothesis, l3_patch + optional l3_patch_retry) live in config/prompts/ with front-matter, loaded+validated at control/worker startup (AUTOBRIDGE_PROMPTS_DIR overrides; bad file fails boot naming it); L2 ranks by evidence with confidence + labelled prior-order fallback, L3-hypothesis answers hypothesis/none with unknown-family rejection, L3 patch renders the REQ-044 question from the file; prompt id/version/hash on every ModelCall (migration 0012) + rung entry, shown on the repair page; replay fixture re-captured for the new hashes. Gates: lint/typecheck/openapi/ui-typecheck green, make test 2749+134+409+202 green (cov >=90%), make demo 8/8 unkeyed live-stack (new-hash L3 skip), matrix 21 + test_05 24 green. Owner step: keyed step-4 check (rung entries show prompt id/version; edit a prompt, restart, next call shows new version).

### REQ-044 — Ask L3 a well-posed question: show the spec it patches, the raw record, and allow an explicit escalation (2026-10-30)
Status: DONE (sbx1 2026-10-30)
Owner run (2026-10-30, sandbox, `origin/main` 8750fad4, keyed): `make demo` 8/8, but step 4's live L3 call
(`muse-spark-1.3`, 8,429 tokens, 8,234 reasoning) returned `patch does not apply: member 'mappings' not found in
{'spec_version': 1, 'connector': …, 'source': …}`. The model is not weak; the question is ill-posed. Today's prompt
(`repair/ladder.py` ~1736–1756, evidence from `repair/semantic.py` `build_l3_evidence`) is only: system "Propose a
JSON Patch … that fixes the drift. Answer with the patch only." + user drift class, hypothesis ids, and evidence
(entity, field, the **wire JSONPath** `$.total_amount`, money check, two key+amount rows, observed vs baseline sum).
Three defects:
1. **The patch target is never named or shown.** A JSON Patch needs paths into one document — the connector spec —
   and the model never sees the spec or its layout, so it invents one (`/mappings/...`). The only "path" it sees is a
   JSONPath into the vendor response, a different thing.
2. **It cannot see the data it would need.** Sample rows are canonical key+amount only; it never sees the raw vendor
   record, so it cannot know which fields (e.g. `gross_amount`) exist. Our own escalation question names
   `$.gross_amount`, but the model is never shown it.
3. **It is not allowed to say "this needs a human".** "Answer with the patch only" forces a guess even when the
   honest answer is a business decision (gross vs net is one).
Fix (L3 prompt and parsing only — no change to the gate, the verifier, or which drifts reach L3):
1. Prompt states the patch applies to **the connector spec**, and includes the spec subtree it may change (the
   entity's `fields`, its checks/transforms as relevant) **with the exact JSON Pointer of each patchable member**
   (e.g. `/entities/Reservation/fields/total_amount/path`), plus the allowed ops (`replace`/`add` on those members;
   the vocabulary's transforms by name). Secrets, base URLs and auth never enter the prompt (redaction as today).
2. Prompt includes one or two **redacted raw vendor records** for the entity (field names and value shapes; money as
   strings; PII/keys redacted by the existing redaction seam), so the model can see which wire fields exist.
3. The answer is **one JSON object**, either `{"patch": [...], "rationale": "<one sentence>"}` or
   `{"escalate": "<the question for the human>", "rationale": "..."}`. Parse both; tolerate a fenced block. An
   `escalate` answer resolves L4 with the model's question shown on the repair page (as the escalation question,
   labelled as the model's), counted as a valid L3 outcome, not an error.
4. Before the verifier, check the patch applies to the spec and touches only allowed pointers; if not, **one**
   retry with the apply error appended (both calls on the ledger and the repair page), then escalate.
5. The repair page "what the engine did" shows the model's rationale (and for a patch, the pointer/value it changed).
6. Replay: the prompt hash changes, so update the replay fixtures/tests; `make record-frontier` stays the owner's
   capture step (note it in docs/demo.md). Update `docs/` where the L3 prompt is described.
7. Tests: prompt contains the spec pointers and a raw-record sample and no secret; parse patch / escalate / fenced /
   garbage; a non-applying patch → one retry with the error → escalate; an `escalate` answer → L4 with the model's
   question; replay round-trip.
8. Gates keyed and unkeyed: `make demo` 8/8 both ways. Owner check after: keyed step 4's L3 answer is either a
   patch that applies to the spec (verifier verdict shown) or an explicit escalation with a sensible question —
   never "does not apply"; report tokens (reasoning vs answer) and the answer.
Resolution: L3 prompt now names the connector-spec patch target with exact JSON Pointers per patchable member, shows redacted raw vendor records, and answers {patch|escalate}+rationale (one retry with the apply error, then escalate; a model escalation resolves L4 with the model's question, labelled on the repair page). Replay fixtures/tests updated (new prompt hashes). Gates: make test + lint/typecheck/openapi/ui-typecheck green, make demo 8/8 unkeyed (L3 explicit skip naming the new hash), matrix + test_05 e2e green live. Owner step: keyed step-4 run (live L3 answer is a spec-applying patch or an explicit escalation, never 'does not apply') + make record-frontier re-capture and commit.

### REQ-041 — Directive: §7 / P2.S6.1 — the REQ-032 repair-page restructure dropped the per-run model-usage chart, and `tests/e2e/test_s6_richer_ui.spec.ts` is red in both modes (2026-10-29)
Status: DONE (sbx1 2026-10-30)
Found by the 2026-10-29 directives audit (diff `2cea2072..d2d9c9d2`). REQ-032 (`cf467198`) restructured `ui/app/repairs/[id]/page.tsx` around `ModelCallBox`/`ModelUsageView`; the P2.S6.1 `ModelUsageChart` (`ui/components/ModelUsageChart.tsx`, still unit-tested) is now imported by no page, and `tests/e2e/test_s6_richer_ui.spec.ts` (3 tests: chart rows, screenshots) fails on the live stack and in mocked mode — verified live in this audit (`make e2e-ui`: 4 failed, incl. these 3; the 4th was the audit-fixed approve-preview locator). Decide: restore the per-run chart on the repair page (appendix, from persisted `ModelCall` rows) and make test_s6's live branch assert live-shaped data instead of the mock run ids (`run-s6-a`/`run-s6-b` can only ever match fixtures), or retire the chart component + its tests and rewrite test_s6 against the ModelCallBox — either way `make e2e-ui` must be green on `make up` and the S6 "model-usage charts per run" deliverable must exist somewhere a user can see it, or be explicitly dropped in `docs/`.
Resolution: restored the per-run chart as a repair-page appendix fed by GET /metrics/model-usage (persisted ModelCall rows, current repair highlighted; no new API surface); test_s6 live branch asserts structure (chart-or-empty, run-id attributes) instead of fixture ids; fixed a test_06 mock-ordering bug where the generic /evidence branch swallowed /runs//evidence (no funnel stages in mocked mode). Verified: make test 2678+134+409+193 green, e2e-ui 10 passed / 9 skipped (live-only) on a prod UI build with mocked control. Remaining OWNER STEP: keyed make e2e-ui on Compose (live branch).

### REQ-031 — UI-first demo, part B: demo-seed, demo-break, demo-heal, build from docs in the UI (2026-10-29)
Status: DONE (sbx1 2026-10-29)
Resolution: Harbor seeds from its reference release via new POST /connectors/{id}/reference-release (verifier-gated); scheduler pauses during reset/seed/heal with owner scoping + reset waits for in-flight jobs; one undecided fault keeps exactly one drift + one repair (dedupe); deterministic live repairs record L0→L4 reasons; step 4 + test_08 assert the live break (record-climb kept as no-key fallback). Remaining: owner keyed re-run + 20x camera acceptance.
Depends on REQ-030.
1. **`make demo-seed`**: from `make demo-reset`, creates the recording's connectors **through the control API only**
   (the calls the UI makes), each baseline-synced `Complete`: AtlasPMS (the reusable reference release), HarborPMS
   (reference release with its money check, used by the semantic scenario), MeridianPMS (built from its docs with
   v2; the refused v1 build recorded with its reason), the Grand Plaza PABX edge connector (v3, link up, postings
   flowing). Clean names, real addresses, no leftover or example.com rows. End state: four connectors `working`,
   0 model tokens, nothing waiting.
2. **`make demo-break SCENARIO=<id> [TARGET=<vendor>]`**: injects one catalogued fault and prints one line ("broke
   AtlasPMS with page_to_cursor — watch Repairs"). It never syncs, detects, repairs or approves; the scheduler and the
   engine do that, visibly. The UI's "Demo: inject fault" drawer offers exactly the same list.
3. **`make demo-heal [TARGET=<vendor>]`**: clears injected faults without a reset (retakes of one scenario).
4. **Build a connector from docs in the UI** (deck 3): the "Build a connector" page works with the simulator docs and
   shows docs read → draft → probes → result → release. Either the page loads the docs text in the browser and sends
   `docs_text`, or the server allows exactly the simulator hosts from `config/demo_targets.yaml` in demo mode. Never
   reopen the AR-2-2 SSRF guard or `docs_path` file reads.
5. `make demo` (terminal version) stays; it may approve through the API as today, but everything it does lands in the
   database (REQ-030 item 3).
Tests: seed end state; demo-break only injects; heal clears; UI build from Harbor docs; SSRF tests still green.

**Owner check (2026-10-29, on 488e9dff): Harbor is seeded from docs, so the semantic scenario never breaks live.**
Item 1 says HarborPMS is seeded from its **reference release with its money check**; `bench/demo_seed.py` builds
`harborpms` (and `atlaspms`) from docs instead (`"harborpms — docs-built release with its checks"`). The docs-built
Harbor spec has no money invariant, so `make demo-break SCENARIO=gross_to_net TARGET=harbor` leaves Harbor `Complete`
and deck 5 never appears in the UI from the break (found by the REQ-033 iteration, which reached for `record-climb`
rows instead). Fix: seed Harbor as the reusable reference release with its money check, through the control API
(the same path demo step 1 uses for Atlas's reusable artifact; add an API route to register a reference release if
none exists, verifier-gated like any release). Then the break → scheduled sync → `Degraded` → climb (funnel, 1 model
call, rejected proposal, operator question) → Harbor `error` happens live, and the REQ-033 UI journey asserts it from
the break, not from `record-climb` rows (keep `record-climb` only as the documented no-key fallback). Atlas may stay
docs-built.

**Owner check (2026-10-30, from the REQ-033 iteration's findings): fix the reset/seed-vs-scheduler race, don't retry
around it.** The REQ-033 iteration found `make demo-seed` failing intermittently after `demo-reset`, and stray
("scar") drift rows appearing ~10 s after a break before any approval. Both come from the sync scheduler running
during reset/seed (and possibly the verifier gate's regression syncs against a still-faulted simulator writing drift
rows). A retry in the seed only hides it; on camera a take must start clean every time. Fix the cause: the scheduler
pauses while `demo-reset` / `demo-seed` / `demo-heal` run (an explicit pause/resume or a lock the scheduler honours),
reset waits for any in-flight sync to finish, and gate-internal syncs never create drift or repair rows. Acceptance:
`make demo-reset && make demo-seed` green 20 times in a row with the scheduler on and no retry; after one
`make demo-break`, exactly one drift and one repair appear for that connector.

### REQ-033 — UI-first demo, part D: README "How to run the demo", docs, UI e2e journeys (2026-10-29)
Status: DONE (sbx1 2026-10-29)
Depends on REQ-031 and REQ-032.
1. **README.md "How to run the demo"**, short enough to follow while recording: start (`make local-up` or `make up`);
   `make demo-reset && make demo-seed`, open `http://localhost:8107`, wait until every connector shows ● Working; then
   one block per scenario with the command, where to look, what should happen and the expected model calls:
   - What AutoBridge knows (Knowledge page); Reusable artifact (Connectors, Atlas Working, 0 tokens)
   - Unfamiliar API: build Harbor from its docs in the UI
   - Zero-token fix: `make demo-break SCENARIO=page_to_cursor` → Atlas Degrading → repair "fix ready · 0 model calls" →
     Approve → Working; learning: break it again → fixed at L0, "reused 1×"
   - Hard semantic failure: `make demo-break SCENARIO=gross_to_net TARGET=harbor` → Harbor Error → funnel, "1 model
     call", rejected proposal, the operator question
   - New kind of API: Meridian page (v1 refused, v2 in use); `make demo-break SCENARIO=meridian_cursor_renamed` →
     fix at L1 → Approve → Working
   - On-premise PABX: `make demo-break SCENARIO=fias_record_layout_change` → `rename_field` TA → TM → Approve →
     postings flow; `make demo-break SCENARIO=agent_restart_inflight` → "lost 0 · duplicated 0"
   - retakes (`make demo-heal`, `make demo-reset && make demo-seed`), the terminal-only `make demo`, settings
     (`AUTOBRIDGE_SYNC_INTERVAL_S`, `FRONTIER_API_KEY`: live vs replay for the semantic scenario).
2. `docs/demo.md`: a "Recording from the UI" section with the same sequence and what to point at on screen.
3. **UI e2e (Playwright, `make e2e-ui`)**: seed → all Working; break `page_to_cursor` → Degrading → "fix ready"
   appears without a manual sync → click Approve → Working, and the four repair sections are present; break
   `gross_to_net` → Error, funnel and "1 model call" (replay when no key) and the question visible; PABX layout break
   → fixed after Approve, postings resume.
Gates: `make test`, keyed `make local-up && make demo-seed && make e2e && make e2e-ui && make demo` green; `make up`
likewise.

Resolution: README 'How to run the demo' + docs/demo.md 'Recording from the UI' (honest live gaps labelled: gross_to_net stays Complete on the docs-built spec; PABX layout break is agent-absorbed); new live-only Playwright spec tests/e2e/test_08_recording_journey.spec.ts (seed frame, zero-token UI approve, L0 re-hit + reused, semantic gap + recorded funnel/call/question, PABX panel), registered in playwright.config.ts (runs first), `make e2e-ui` sources load_dotenv.sh for the control token. Gates: 6/6 journeys green live unkeyed (recorded half after `make demo` step 4), make test 2640+134+408+192 green (90/91/90%), lint/typecheck/ui-typecheck/openapi-check green.

### REQ-036 — Directive: stale rules — DIRECTIVES §§1/2/3/6/7 contradict the shipped code (2026-10-29)
Status: DONE (2026-10-29)
Amend these in place (DIRECTIVES-INSTRUCTIONS: one bullet per rule, <600 chars, no contradictory pairs; Quick Reference itself is within its size limits and needs no change): (1) §1 says a spec "validates against `schemas/connector-spec.v1.json`" — the engine now ships v1/v2/v3 vocabularies (`schemas/connector-spec.v{1,2,3}.json`, `src/autobridge/spec/models.py:4,115` validates against v3 and gates v2/v3 constructs). (2) §2 lists four Docker networks — Compose has five (`edge-net`, `compose.yaml:349-363`) plus `bench` attached to verifier-net (`compose.yaml:336-347`). (3) §6 says `.env.example` lists "every secret with an empty value" — since REQ-027 it deliberately ships labelled simulator demo values (`ATLAS_API_KEY=sim-demo-key` etc., `.env.example:121-130`); the rule should say real secrets are empty and simulator demo values are labelled non-secrets. (4) §7 says "The UI is four screens" — the app now routes `/`, `/connectors`, `/connectors/[id]`, `/repairs`, `/repairs/[id]`, `/knowledge`, `/simulators` (`ui/app/`). (5) §3 "L3 is invoked only when the HypothesisEnumerator yields fewer than two hypotheses" vs the REQ-021 semantic climb: `run_frontier_climb` counts surviving hypotheses after wire filtering, and `RepairController._handle_semantic_climb` (`src/autobridge/repair/controller.py:245-255`) fabricates `enumerator_yield_count=1` when the caller passes None — amend the rule to name which count gates L3, and make the controller derive it (len of surviving hypotheses) instead of defaulting to 1. Acceptance: `grep` finds no bullet contradicting the code above; `./directives-for.sh` output for the touched paths matches the new wording.

Resolution: controller derives L3 gate count as len(surviving hypotheses) + new counted-number test; §§1/2/3/6/7 bullets amended in place (v3 schema, 5 networks + bench, labelled demo values, 8 UI routes, L3 gate semantics). Gates green.
### REQ-038 — Flaky FIAS replay test makes `make test` randomly red (Code Review Cycle 4, AR-4-4) (2026-10-29)
Status: DONE (sbx1 2026-10-29)
`tests/unit/test_simulators_fias_pabx.py::test_replay_postings_resend_same_pno` failed in the Cycle 4 review's full
`make test` (`assert 7 == (6 + 2)` on `postings_sent`, line ~665) and passed on rerun; the earlier owner run also saw
it as "one load-sensitive FIAS timing flake". It races wall-clock deadlines (8 s pumps). Make it deterministic: drive
the PABX link with an injected clock or explicit events (wait for the specific frames/acks, not a time window), so the
assertion holds under load; no retries or longer sleeps. Acceptance: the test passes 50 times in a row alone and inside
the full `make test` under parallel load; same pattern applied to any other FIAS test that waits on wall-clock windows.
Also note for REQ-036 item 5 (AR-4-3): `RepairController._handle_semantic_climb` must pass the real surviving-hypothesis
count to `run_frontier_climb`, never a default `1`; the L3 gate assertion must test a counted number.
Resolution: replay test waits on the postings_sent counter event instead of the first two frames (ACK-timeout retransmits no longer miscount); flood test asserts >= 5 byte-identical records.

### REQ-035 — Directive: §2 no-route to simulator admin — vendor-net peers reach sim:8001; protection is token-only and the default token is empty (2026-10-29)
Status: DONE (2026-10-29)
Resolution: demo default sim-admin-dev-token in .env.example/compose/local-up; §2 authorization-boundary wording; smoke probe_admin_denied (403 vs no-route, fail only on accept); live verified make up + smoke green.
**Owner decision (2026-10-29): options (a) + (c).** (a) Ship a non-empty demo admin token by default in both modes
(`SIM_ADMIN_TOKEN`, labelled a demo value like the simulator demo keys; `.env.example`, `scripts/local-up.sh`,
`compose.yaml`), so a fresh clone never runs header-only. (c) Amend Directive §2 to state an authorization boundary
(token-protected admin API) instead of "no route", and make smoke distinguish "reachable but rejected (401/403)" from
"no route", failing only when an unauthorized admin write is accepted.
Directive §2 says the worker and runtime-sandbox have "no route to … the simulator admin API", and Cycle 1 AR-1-1 flagged the same. Code evidence 2026-10-29: from inside `runtime-sandbox`, `GET http://sim-atlas:8001/healthz` answers `403` — i.e. the admin listener IS reachable from vendor-net; only `SIM_ADMIN_TOKEN` authorization stops it (`simulators/common/admin.py:58-83`, whose comment says the smoke probe "treats 403 as down — preserving the negative check"; `scripts/smoke.sh:49-63` counts any HTTP error as unreachable). And the default is header-only: `compose.yaml` and `scripts/local-up.sh` pass `${SIM_ADMIN_TOKEN:-}` and `.env.example:82` ships it empty, so a fresh-clone stack accepts the published `X-Admin-Network: admin-net` header from any vendor-net peer. Owner decision: either (a) generate/require a non-empty demo admin token by default in both modes (like the sim-demo-key defaults, labelled a demo value), (b) move the admin listener off the vendor-reachable interface, or (c) amend Directive §2 + smoke to claim an authorization boundary (not "no route") and make the probe distinguish 403 (reachable, rejected) from connection failure. Acceptance: smoke's negative admin checks fail when an unauthorized write is ACCEPTED, not when it is merely reachable; a fresh clone (`cp .env.example .env`) is covered either way.

### REQ-023 — Demo plan v2: main script plus a fault-scenario catalogue (2026-10-28)
Status: DONE (2026-10-29)
Resolution: catalogue + per-entry runs + sync test + token_expired matrix row (codeable part); main-script steps and catalogue entries run alone via make demo STEP=/SCENARIO=, docs table sourced from bench/demo.py CATALOGUE.
Owner: the demo plan must include the fault scenarios from REQ-019 … REQ-022. Rule: a step enters `docs/demo.md` and
`make demo` only when the feature behind it has merged and its e2e test is green; until then it stays listed here.
Every step runs after `make demo-reset` (REQ-021) and prints REQ-021's per-run table (rung that resolved it, model
calls, small / frontier tokens, why it escalated). Never show heldout seeds; demo the dev variants only.
**Owner decision (2026-10-28):** the demo video is 3 minutes. Build and test everything below; the owner picks which
steps go into the video. So every main-script step and every catalogue entry must run on its own (`make demo
SCENARIO=…` / `make demo STEP=…`) after a reset, and `make demo` runs the full main script for rehearsal.
**Main script** (the full set; the owner cuts it down for the video):
1. *What AutoBridge knows*: capabilities page (vocabulary v1/v2) and an empty learned-knowledge list. [REQ-021]
2. *Reusable artifact*: the Atlas (Cloudbeds-style) reference release syncs with zero inference. [REQ-019]
3. *Unfamiliar API*: Harbor's messy wiki page (its `/developers` site on screen) bootstraps a connector; the outdated
   offset example and the `H1`–`H5` status codes are resolved from probes, not guessed.
4. *Zero-token recovery + learning*: inject `page_to_cursor` on Atlas → L1 rule, 0 model calls → approve → the fix
   appears on the knowledge page → inject it again → L0 hit, "times reused" + 1. [REQ-019, REQ-021]
5. *Hard semantic failure*: `gross_to_net` → climbs L0 → L1 → L2 → L3 (Muse frontier, live or labelled replay) with
   the reason at each rung; the `invalid_patch_trap` candidate is rejected by the verifier; ends Degraded with one
   question for the operator. Show that this is the only main-script step that calls the frontier model.
6. *New API style*: Meridian (Mews-style) docs with vocabulary v1 → `Blocked`, named reason, 0 model calls; same docs
   with v2 → connector passes the gate and syncs `Complete`; then a Meridian drift (`Limitation` cursor contract
   change) repaired. [REQ-020, REQ-021]
7. *On-premise PABX*: FIAS postings flowing through the edge agent; `fias_record_layout_change` (firmware update
   announces a new `PS` layout) → repaired → postings flow again; then `agent_restart_inflight` → nothing lost, nothing
   duplicated. [REQ-022]
8. *Head-to-head and reproducibility*: bench scorecard (real numbers, REQ-008), the model-usage summary across all
   steps, seeds and release ids, `make demo` to replay.
**Fault-scenario catalogue** (backup and Q&A; each runnable alone with `make demo SCENARIO=<name>` and listed in
docs/demo.md with its expected rung and model calls):
- Transport, no patch, 0 model calls: `rate_limit_429`, `transient_503`,
  `cloud_slow` (FIAS link keeps getting ACKs).
- Auth: `token_expired` → refresh if permitted; `token_revoked` → escalate, no patch, `Blocked`.
- Capability: `endpoint_removed` → escalate, never patched around.
- Known structural drifts at L0/L1, 0 model calls: `rename_guest_id`, `nested_path_shift`, `offset_to_cursor`,
  `page_to_cursor` (Atlas), `cursor_repeat_token` (Harbor loop guard), `fias_record_layout_change`
  (known variant).
- Semantic and data quality: `gross_to_net`, `status_vocab_change` (new Harbor code / Cloudbeds-style
  `not_confirmed`), `tz_boundary_shift` (Atlas zone-less date-times, Meridian UTC → business date),
  `duplicate_late_records`, Atlas money as JSON numbers → Decimal without float drift, `invalid_patch_trap`.
- On-premise reliability: `fias_link_resync`, `fias_ack_timeout`, `agent_restart_inflight`,
  `fias_duplicate_records`, `fias_oversize_frame`, `fias_record_flood`, `session_lost_with_command`, `stale_command`.
- Safety and boundaries: v1 refusal of Meridian and of a FIAS source; inject-fault without the admin token rejected;
  UI writes without the control token rejected; the engine cannot reach the verifier or simulator admin (smoke).
Wanted: `docs/demo.md` rewritten to this structure as steps land (main script on top, catalogue table below with
scenario | vendor | expected outcome | rung | model calls | REQ), `make demo` running the main script and
`make demo SCENARIO=…` any catalogue entry, `docs/demo.md` and `make demo` kept in sync by a test that every catalogue
entry has a scenario file, an e2e test and a row in REQ-021's model-usage matrix.

### REQ-022 — FIAS PABX simulator and a real on-premise edge agent (replaces EdgePOS) (2026-10-28)
Status: DONE (2026-10-29)
Resolution: sub-steps A-E done; final owner check fixed: (1) s2 live test + fault teardown poll /edge/agents until link_up (bounded ~2 heartbeat intervals) instead of a single read; (2) simulators index docs label no longer doubles /developers, label asserted in UI test; plus W5.7 guard carve-out for src/autobridge/bench reading public reference specs (confined to reference/, imports/hidden-fixtures still forbidden). Verified keyed local stack: full make e2e 158 passed/2 skipped; make test + lint + typecheck green.
Owner: make the edge story reflect how hotel on-premise integrations really work. Replace the EdgePOS toy (P2.S2) with
a **FIAS PABX** simulator speaking the public FIAS protocol, and turn the edge worker into an on-premise agent.
**Spec (owner, binding): implement from `simulators/docs/fias-pabx.md`**, the hotel's PABX interface
specification (FIAS) as handed to integration partners. It is the single source of truth for framing, handshake,
record layouts, site settings and PABX behavior; do not go online to rebuild the spec, and do not read any file under
`tmp/`. Do not use any company, product or class name from the owner's private material. Where the spec is silent,
choose the simplest behavior consistent with it and note it in the build note. Section 2.2's "site extension" (the
PABX announces its own layout for records it originates) is intentional: it is what makes the firmware-change
scenario possible. Name things "FIAS PABX" (`simulators/fias_pabx/`, service `sim-fias-pabx`); remove EdgePOS
(`simulators/edgepos/`, `sim-edgepos`, tests, smoke probes, docs) rather than keeping both.

**A. Simulator `sim-fias-pabx`** (edge-net only; reachable by the edge agent, never by control, worker, sandbox or UI;
keep the negative smoke probes). FIAS over TCP:
- Everything in `simulators/docs/fias-pabx.md`: framing and ACK/NAK (§1), link control and the handshake including
  the PABX's own `LR` announcement (§2), keep-alive and all-zero resync (§2.3), the records and fields of §3–§4, the
  operational behavior of §5 (one link, nightly interface restart, firmware layout changes, bursts, `PS` resend until
  `PA`). Site settings are simulator configuration with the spec's values as defaults. Summary of what that covers:
- Link handshake: Link Start `LS`, Link Description `LD` (date, time, version, interface family), one Link Record
  `LR` per record type announcing its field ids, Link Alive `LA`, Link End `LE`. Keep-alive `LA` on an interval.
  All-zero bytes on the line mean "out of sync": restart the handshake.
- PABX → host records: Posting Simple `PS` (call / minibar charge: room `RN`, date `DA`, time `TI`, total amount `TA`,
  sequence `P#`, plus what the spec lists), Room Equipment / status `RE`, Wake-up Answer `WA`, Database Resync request
  `DR`.
- Host → PABX records: Guest In / Change / Out `GI` `GC` `GO`, Wake-up Request / Clear `WR` `WC`, message light
  `XL` / `XD`, key write / delete / read `KR` `KD` `KZ` answered by `KA` (or `KZ`), Posting Answer `PA`.
- Seeded and deterministic from the shared generator (rooms and guests consistent with the PMS simulators, so postings
  land on real in-house guests), with an admin API (token, loopback-only port) for the fault switchboard.
- **Docs page = an uploaded document, viewed** (owner, 2026-10-28; replaces any website look). Assume the hotel's
  spec file was already uploaded to a document store; the PABX's `/developers` page is what a user sees when opening
  it: a document viewer, not a docs site. Look and feel: a file header bar (file name `Grand_Plaza_PABX_Interface_
  Specification_FIAS_v1.0.pdf`, size, "uploaded by Hotel IT · <date>", page count, a download icon that does nothing
  or downloads the `.md`), a grey canvas with white paper pages (A4 proportions, page shadows, page numbers in the
  footer, a running header "Grand Plaza — PABX Interface Specification (FIAS)"), a thumbnail rail on the left, zoom
  controls, and print-document typography (serif body, numbered headings, ruled tables). Content, rendered at load time
  from `simulators/docs/fias-pabx.md` (the single source, as with the other portals): a title page (title, property,
  version, date, "Confidential — for integration partners"), contents, then the spec, with the records presented as
  a **command list**: one table per direction listing each record type, its name, the fields it carries and the
  answer expected. No upload feature, no PDF/DOCX generation and no new engine intake: this is presentation only, via
  `mount_devportal` like the other three portals. A unit test that the page loads and renders the command list.

**B. Edge agent** (the edge worker becomes a proper on-premise agent; generic pattern, done right):
- Outbound only: no inbound port from the cloud. It polls a cloud command queue (a simple queue endpoint on the
  control plane, or a local queue service in compose) and reports through authenticated callbacks to the control
  API (client-credentials style token, never logged).
- Delivery guarantees the verifier can check: a durable local outbox and inbox (survive agent restart);
  at-least-once delivery with idempotency keys so the cloud de-duplicates; answers correlated by request id, never
  by "whatever arrives next"; command expiry enforced (a stale wake-up is never sent, and the cloud is told it
  expired); an ACK timeout reported to the cloud, not dropped; one command in flight per link with a bounded wait.
- Never block the PABX: the agent ACKs the PABX after a durable local write, not after the cloud call, so a slow
  cloud cannot stall the link. Back-pressure and retries with backoff toward the cloud.
- Link supervision: handshake state machine, keep-alive, resync on all-zero bytes, reconnect, spam and oversize
  protection (close the session on a frame over a set limit, or on a long run of identical records), health exposed
  on `/readyz` and in the UI.
- The agent runs a verified, signed release (AR-2-4 HMAC) as today; what it executes is still declarative spec data.

**C. Spec vocabulary** (same approach as REQ-020: before → extend once, generally → after). Today ConnectorSpec is
HTTP-pull only. Add, as JSON-Patchable data, never code: a `protocol: fias_tcp` transport (framing, ACK mode, link
records), event-driven sources (records pushed to the agent instead of polled), record-to-entity mapping (e.g. `PS`
fields → canonical `FolioCharge`; `RE` → room status), and command templates for host → PABX records. "Before": the
v1 engine must refuse a FIAS source cleanly (`Blocked`, named reason, 0 model calls, REQ-021 item 2 pin). "After": the
same FIAS docs page bootstraps a working connector, with no FIAS-specific branch outside the generic transport.

**D. Fault scenarios** (each a YAML in `simulators/scenarios/`, a verifier check, and an e2e test):
- `fias_record_layout_change`: after a reconnect, the PABX announces a different `LR` field set for `PS` (a field
  added, renamed or reordered). The connector detects the drift from the handshake and repairs the mapping with a
  JSON Patch through the ladder (a known variant at 0 frontier tokens).
- `fias_link_resync`: all-zero bytes mid-session; the agent re-handshakes and loses nothing.
- `fias_ack_timeout`: the PABX stops answering a command; the cloud is told it timed out.
- `agent_restart_inflight`: the agent is killed with commands and postings in flight; after restart nothing is lost
  or duplicated downstream.
- `cloud_slow`: callbacks take tens of seconds; the PABX link keeps getting ACKs, the outbox drains later.
- `fias_duplicate_records`: the PABX repeats postings; each charge lands once (idempotent by `P#` + room + time).
- `fias_oversize_frame` and `fias_record_flood`: the session is closed and re-opened cleanly; no crash.
- `session_lost_with_command`: the link drops while a command waits; the command is retried on the new session
  (never broadcast to other links) or reported failed.
- `stale_command`: a wake-up request arrives after its expiry; it is not sent and is reported expired.

**E. Demo and docs.** A demo step "on-premise PABX: the agent keeps working through a PABX firmware change" (layout
change → repair → postings flow again, with REQ-021's per-rung table and model usage), plus a restart-without-loss
moment. Update docs/architecture.md and docs/safety-model.md (edge-net boundary, outbound-only agent, delivery
guarantees), README vendor table, `make up` / smoke / local mode (REQ-017; the agent and PABX run as local processes).
**Simulators index** (owner, 2026-10-28; built here because REQ-022 lands last): one UI page, linked from the "System
status" link in the top bar, listing every simulator: Atlas, Harbor, LegacyStay, Meridian (REQ-020) and FIAS PABX.
Per vendor: name and API style (e.g. "Cloudbeds-style REST", "Mews-style POST/JSON", "FIAS over TCP"), its
`/developers` docs link, public port, live up/down from its `/readyz` (fetched server-side through the UI's `/api`
route, never from the browser to the simulator directly), the active fault scenario if any, and which connectors use
it. Generated from one registry (no hand-kept list in two places), works in local mode, covered by a UI test.

**F. Gates.** Unit and e2e tests for every item above, `make test`, `make up && make smoke && make e2e && make demo`
green; grep proves no EdgePOS remnants and no private names. Split into claimable sub-steps in the build note
(A simulator, B agent, C vocabulary, D scenarios, E demo) and merge each one green.

**Owner check after sub-step B (2026-10-29, on 01e10f3b; fix before sub-step C):**
1. **`make local-up` never comes up with a keyed `.env`.** With `SIM_ADMIN_TOKEN`, `AUTOBRIDGE_CONTROL_TOKEN` and
   `EDGE_RELEASE_SECRET` set (what `.env.example` and REQ-014 tell people to do) and `EDGE_BUNDLE_SIGNATURE` empty,
   edge-worker answers `/readyz` 503 forever: `"bundle":"command bundle is unsigned: set EDGE_BUNDLE_SIGNATURE"`,
   `"link":"not built (bundle rejected)"`. local-up prints only `readyz TIMEOUT waiting for edge-worker (:8114)` after
   the full wait, and the PABX sits at `fias_link: down: dialling 127.0.0.1:5010`. A judge who clones the repo, sets the
   three tokens and runs `make local-up` gets a dead stack with no hint. Keep fail-closed in the agent, but: (a) give a
   one-command way to pin it (e.g. `make edge-sign`, writing `EDGE_BUNDLE_SIGNATURE` into `.env` the way the other
   tokens are documented; never print other secrets), (b) `make local-up` and `make up` check this before starting and
   stop at once with that exact command in the message, instead of a readyz timeout, (c) README/DEPLOY quick start
   include the step. Test: a keyed `.env` without the signature fails fast with the hint; with it, every readyz is green
   and the PABX link shows connected.
2. **The full unit suite crashes 6 tests on macOS since this merge.** On the host (macOS, Homebrew Python 3.12),
   `uv run pytest tests/unit -p no:randomly` fails `test_autobridge_dotenv_loader.py` (4 tests) and
   `test_autobridge_ui_client.py` (2: `npm run typecheck`, `export_openapi.py --check`) with `returncode=-11`: the
   child process segfaults. Every one passes alone. Minimal repro: `uv run pytest
   tests/unit/test_autobridge_edge_agent.py tests/unit/test_autobridge_dotenv_loader.py -p no:randomly` → 1 failed;
   the same with `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` → 40 passed. So it is the macOS fork-safety crash: something
   in the edge-agent tests touches the Objective-C runtime from a thread (likely the system proxy lookup when an
   HTTP client is built with default `trust_env`), and later `fork()`ed children die. The same run also shows
   `PytestUnhandledThreadExceptionWarning: Exception in thread Thread-19 (_serve)` (and 20, 22): `_serve` threads
   are left running/raising after their tests. Linux (the sandbox) never shows this, so verify by reasoning plus the
   tests below, not by reproducing. Fix the cause, do not set the env var: stop and join every thread a test starts
   (fixture teardown), make the agent's and tests' HTTP clients not consult system proxy settings where they do not need
   to, and make the suite fail on unhandled thread exceptions (`filterwarnings = error::pytest.PytestUnhandledThreadExceptionWarning`)
   so a leaked thread is caught on Linux too. Owner re-runs the full suite on macOS after the fix.
   **Owner re-check (2026-10-29, on f64c6956): item 2 is NOT fixed, and my diagnosis above was wrong; correcting it.**
   The full unit suite on macOS still gives 13–14 `returncode=-11` failures (dotenv_loader 4, edge_sign 7,
   ui_client 2, edge_agent 1) and new "Python quit unexpectedly" crash reports (`CoreFoundation: *** multi-threaded
   process forked ***`, `crashed on child side of fork pre-exec`). The leaked-thread/`trust_env` work in 014bc5f9 is
   fine to keep, but it is not the cause: **the crash already happens on e2932600, before REQ-022 began** (6 failures
   there). Bisected minimal repro, 3 tests, fails every time on macOS:
   `uv run pytest -p no:randomly tests/unit/test_autobridge_bench_w85.py::test_main_full_pipeline_writes_scorecard
   tests/unit/test_autobridge_build_docs_guard.py::test_validate_docs_url_unit tests/unit/test_autobridge_dotenv_loader.py`
   → 4 dotenv tests die with -11. Drop either of the first two and all pass. `test_validate_docs_url_unit` calls
   `validate_docs_url("https://example.com/docs.md")`, which runs a **real DNS lookup** in a worker thread
   (`src/autobridge/control/routes/builds.py:68`, `_docs_url_resolves_internal`: ThreadPoolExecutor +
   `socket.getaddrinfo`); on macOS that initializes the system resolver/CoreFoundation machinery off the main thread,
   and once `test_main_full_pipeline_writes_scorecard` has also run, every later `fork()`ed child crashes before exec.
   Fix: unit tests must not do real DNS (they are also not hermetic: offline they fail open silently). Patch the
   resolver in the docs-guard tests (e.g. monkeypatch `_docs_url_resolves_internal` or `socket.getaddrinfo`) and add a
   unit-wide autouse guard that fails any test calling real `socket.getaddrinfo` for a non-literal host; find what
   `test_main_full_pipeline_writes_scorecard` initializes (check for real network/proxy lookups or a thread pool) and
   make it hermetic the same way. Acceptance: the 3-test repro above and the full `uv run pytest tests/unit` pass on
   macOS with zero new crash reports (owner verifies; the sandbox is Linux and cannot show it).
   This item is independent of sub-step D; do it next.

**Owner check (2026-10-29, on 2df1bd33): e2e matrix test is stale since sub-step A.** Against the live local stack,
`tests/e2e/test_model_usage_matrix.py::test_matrix_live_reset_repeatability` fails (19 of 20 pass): it pins the
`/demo/reset` targets to `[sim-atlas, sim-harbor, sim-legacystay, sim-meridian]`, but reset now also returns
`sim-fias-pabx` and `fias-pabx:fias` (added in sub-step A). Derive the expected list from the same registry the reset
uses instead of a hand-kept list, and run `make e2e` against the stack (local mode is enough) before every REQ-022
merge from now on: unit-green alone missed this.

**Owner check (2026-10-29, on e2823f4c): the reset fix is verified (matrix test green); full `make e2e` against the
local stack now gives 144 passed, 2 skipped, 1 failed:** `tests/e2e/test_s1_legacystay.py::test_no_legacystay_specific_rule_or_engine_code`.
The generalization guard says no file under `src/autobridge` may name LegacyStay, but the demo-reset target registry
does: `src/autobridge/control/routes/demo.py` (lines ~40–120: target aliases, reset list, admin port map) and
`src/autobridge/control/schemas.py:353` (the `Literal[...]` of target ids). It dates from REQ-021 sub-step B
(a569ffa6) and went unnoticed because e2e was not run. Do not weaken or special-case the guard: LegacyStay is the
"never seen" vendor and the deck relies on that test. Move the simulator target registry (ids, aliases, admin ports)
out of engine source into a data file the control service loads at start (e.g. `config/demo_targets.yaml`, also used
by local-up/compose where they need it), validate `target` against it at runtime instead of a hard-coded `Literal`
(the OpenAPI can list the ids from the file at export), and keep one registry (the reset-targets fix just made it
one; keep it one). Then `make e2e` against the stack fully green.

**Owner check (2026-10-29, on 3e566c85): the macOS fork crash is fixed (3-test repro and full unit suite: 0 crash
reports, 2730 passed in file order).** One real race left, flaky on the owner's Mac:
`tests/unit/test_autobridge_edge_agent.py::test_link_postings_flow_to_sink` fails **6 of 10 runs alone** with
`postings never PA'd: {... 'postings_sent': 6, 'pa_ok': 5, 'duplicates_seen_by_host': 1, 'resends': 0 ...}`.
Cause (simulators/fias_pabx/pabx.py ~651–662 and `_apply_pa` ~907): the PABX sends a PS and waits for its ACK,
and only *after* that registers the P# in `_unacked`. The host's PA can arrive while the PABX is still inside
`_send_record_and_wait_ack`; `_apply_pa` then finds no `_unacked` entry, counts it as a duplicate and drops it,
so that posting is never confirmed and the test times out. In a demo this is a charge that looks lost. Fix: register
the P# in `_unacked` (under the lock) before the frame goes out, and drop it again only if the send itself fails;
same check for any other record that expects an answer (WR/KR → KA etc.). Add a test that forces the order (PA
delivered before the ACK-wait returns) instead of relying on timing, and check the flakiness: 20 runs alone green.

**Owner check (2026-10-29, on 9ff6445d): race fixed (`test_link_postings_flow_to_sink` 20/20 alone; full unit
2772 passed, 0 crash reports). But sub-step D merged with e2e red, and on a keyed stack it is 8 failed / 150 passed.**
The commit says "1 fail (documented flake)"; on the owner's keyed local stack (`make local-up`, the `.env` with the
three tokens + bundle signature) `tests/e2e` gives:
1. **7 of the new `tests/e2e/test_fias_faults.py` tests fail on auth**, the REQ-015 bug again: `_enqueue` (line ~162)
   gets `401 {"detail":"Missing or invalid control token"}`, and `/v1/postings` (line ~142) returns the 401 body, so
   `.json()["postings"]` is a `KeyError`. The file builds its own client without `control_headers()` from
   `tests/e2e/conftest.py`. Send the token on every control/relay call (use the conftest helper; no second copy).
   They passed in the sandbox only because its stack is unkeyed.
2. **`test_s2_edge_target.py::test_agent_live_command_and_postings` fails at line 337** (`/edge/agents` shows no
   `grand-plaza-pabx` with `link_up`). It runs after `test_fias_faults.py`; most likely a fault test leaves the PABX
   link down (an armed fault, or a closed link still inside the 10 s redial) and the next file inherits it. Each fault
   test must clear its scenario and wait until the link is back up (poll `/readyz` `link_up`) in teardown; do not call
   this a flake or retry around it.
Gate from now on: run `make e2e` against a **keyed** local stack (set the three tokens and `make edge-sign` in the
sandbox `.env`), and do not merge with any e2e failure, "flake" included, without an owner note.

**Owner check (2026-10-29, on 6a294fd8): keyed-stack fixes verified (fault tests green, auth fixed); `make demo` 8/8
including the new PABX step. Two left:**
1. **`test_s2_edge_target.py::test_agent_live_command_and_postings` still fails after the fault file, every time.**
   Repro on a keyed local stack: `pytest tests/e2e/test_fias_faults.py
   tests/e2e/test_s2_edge_target.py::test_agent_live_command_and_postings -p no:randomly` → 13 passed, 1 failed at line
   337 (`/edge/agents` has no `grand-plaza-pabx` with `link_up`). Alone it passes (2/2). The fault teardown waits for
   the PABX link, but the cloud only learns about it from the agent's next heartbeat (`HEARTBEAT_INTERVAL_S = 10`),
   and line 337 reads `/edge/agents` once. Make the teardown (or the test) wait until `/edge/agents` reports
   `link_up` for the agent, bounded by ~2 heartbeat intervals, then the full `make e2e` must be green.
2. **Simulators index shows broken docs URLs:** every row reads `localhost:8105/developers/developers` (the href is
   right; the label appends `/developers` to a URL that already has it, `ui/app/simulators/page.tsx:46`). The page is
   on camera; fix the label and add the label to the UI test.

### REQ-020 — Add a fifth simulator after the Mews Connector API (2026-10-28)
Status: DONE (2026-10-28) — all 8 items landed (simulator+docs portal, v1 before-refusal, v2 schema+runtime+discovery+joins, after-proof + 2 ladder faults, signature-regression fix, demo step 7); gates green, verified live in sbx1 2026-10-28.
Depends on REQ-019 (do it after). A new vendor, fictional name **MeridianPMS** (`sim-meridian`), copies the **Mews
Connector API** shape: the "modern but very different" vendor. Same rule: shape only, no Mews name, branding or doc text.
Read first: https://docs.mews.com/connector-api/guidelines/pagination.md, `.../guidelines/authentication.md`,
`.../guidelines/requests.md`, `.../guidelines/responses.md`, `.../guidelines/environments.md`,
https://docs.mews.com/connector-api/operations/reservations.md, the customers and order items operations, and the
machine-readable spec https://api.mews.com/Swagger/connector/swagger.yaml (match names exactly from it).
What the research already confirmed (2026-10-28):
- Every operation is `POST` with a JSON body, e.g. `POST /api/connector/v1/reservations/getAll/2023-06-06`.
- Auth is in the body, not a header: `ClientToken`, `AccessToken`, `Client` (app name + version).
- Pagination: body `Limitation: {"Count": 1..1000, "Cursor": "<id or null>"}`; the response `Cursor` is the id of the
  last item returned and is `null` only when no data comes back. Stop when `Cursor` is null or fewer than `Count` items.
- Filters: `EnterpriseIds`, `ServiceIds`, `ReservationIds` (max 1000 each), `UpdatedUtc: {StartUtc, EndUtc}` (max
  3 months), `States`.
- Reservation fields are PascalCase: `Id`, `ServiceId`, `AccountId`, `BookerId`, `Number`, `State`, `Origin`,
  `CreatedUtc`, `UpdatedUtc`, `ScheduledStartUtc`, `ScheduledEndUtc`, `ActualStartUtc`, `ActualEndUtc`, `RateId`,
  `AssignedResourceId`. `State`: `Inquired`, `Confirmed`, `Started`, `Processed`, `Canceled`, `Optional`, `Requested`.
- There is no guest name or amount on a reservation: the name comes from customers (`AccountId`), money from order
  items (Amount with currency, net, gross and tax values), and dates are UTC instants, so property-local business dates
  need the enterprise time zone. Those joins and conversions are the point of this vendor.
Wanted:
1. Simulator `simulators/meridianpms/` with reservations getAll (2023-06-06), customers getAll, order items getAll and
   the enterprise/configuration call that gives time zone and currency; seeded from the shared generator; fault
   switchboard and admin API like the others (admin token, loopback-only admin port). Wrong or missing tokens answer
   the way the docs specify (confirm status and body).
2. Compose service `sim-meridian` on vendor-net + admin-net, public `127.0.0.1:8116`, admin `127.0.0.1:8117`
   (loopback-only like the other admin ports); readiness in `make up`, smoke positive and negative probes, local mode
   (REQ-017) included.
3. Docs: `simulators/docs/meridian-openapi.yaml` in the Mews swagger style (the single source), and a `/developers`
   page registered via `mount_devportal` that renders it at load time. Owner (2026-10-28): Meridian is a **modern
   PMS developer portal**, and it must not look like Atlas. Atlas is a single-page API reference (left nav, operation
   per section, dark code panel on the right). Meridian is a guides-first docs site in the style of today's hosted
   docs platforms: a top bar with product switcher (Connector API · Webhooks · Changelog), a search box with a ⌘K
   hint, and light/dark toggle; a left sidebar grouped into **Guides** (Getting started, Authentication, Requests,
   Responses, Pagination, Serialization: written from the YAML's descriptions and the "What the research confirmed"
   notes above, in Meridian's own words) and **Operations** (one page per operation, grouped by resource:
   Reservations, Customers, Order items, Configuration); each operation page shows `POST` + the versioned path, a
   request-body table and a response table with nested objects expandable, a tabbed code sample (cURL / JavaScript /
   Python) with the tokens in the body, an example response, and callouts ("Uses pagination", "Max interval 3 months",
   deprecation note for older versions); a right-hand "On this page" rail; a "Was this page helpful?" footer and a
   last-updated date. Hash routing between pages (one HTML page, no build step, no external fonts or CDNs, works
   offline like the other portals). Include it in the simulators index (REQ-022).
4. **Before** (owner decision 2026-10-28: the demo shows both halves). With the simulator and docs in place and the
   engine still unchanged, run a build from Meridian's docs and record the result in the build note. It must stop
   cleanly: `BuildRun` ends `Blocked` with a named reason (e.g. "operations need POST with a JSON body and body-field
   auth; not expressible in ConnectorSpec v1"), escalated to L4, no release persisted, no half-working connector, no
   crash. If today's bootstrap fails messily instead, fix that first (generally, for any API outside the vocabulary).
   Add an e2e test that pins this behavior for a spec-v1-only engine configuration, so the refusal stays demonstrable.
5. **Extend the spec vocabulary, once and generally** (owner decision: required, not optional). As JSON-Patchable spec
   data, never code, and with no Meridian-specific branch anywhere in the engine (grep must find no "meridian" in
   `src/autobridge` outside tests and fixtures): `ConnectorSpec` operations gain `method: POST` with a JSON body
   template; `auth` type `body_fields` (credential refs placed into named body fields, still never in specs or
   evidence as values); a body-cursor pagination strategy (cursor and page size in named body paths in, cursor path
   out, stop on null cursor or short page); and declarative joins (enrich one entity from another operation by key,
   e.g. reservation → customer name, reservation → sum of order item gross amounts). Bump the spec schema version,
   keep every v1 spec valid and behaving the same, update `connector-spec` JSON schema, docs reader / classifiers /
   hypotheses so discovery can propose these from docs, the runtime interpreter, and JSON Patch repair rules that touch
   the new fields. Verifier oracle and canonical mapping for Meridian, including UTC → property-local business date.
6. **After.** The same Meridian docs, with no other input, now bootstrap a connector that passes the verifier gate and
   syncs `Complete`. Then at least two fault scenarios on Meridian (e.g. the `Limitation` cursor contract changes, a
   renamed field) repaired through the ladder, with a known one at zero frontier tokens. Bench includes Meridian only
   if the plan's matched-budget rules allow; otherwise note it.
7. Demo: add the before/after to `docs/demo.md` and `make demo` as one step ("unfamiliar API style: engine refuses
   safely → vocabulary extended once → same docs connect"), with the evidence each half shows.
**Owner check after sub-step 6 (2026-10-28 19:55) — regression, fix FIRST, before REQ-021:** sub-step 5a
(`c4bef3b2`, ConnectorSpec v2) changed `spec_signature()` for v1 specs. Bisected on the committed reference releases
`generated/connectors/{atlaspms,harborpms}/reference/`: signatures verify at `45b3d8ca` and mismatch at `c4bef3b2` and
every later commit. So every release stored before the upgrade now loads as `ReleaseCorruptError: … signature
mismatch`; `tests/e2e/test_02_reference_sync.py::test_reference_specs_exist_and_validate` fails on the owner's host
(`make e2e`: 1 failed, 118 passed) with or without `EDGE_RELEASE_SECRET`. This breaks item 5's "keep every v1 spec valid
and behaving the same".
Fix: make the signature of a v1 spec byte-identical to before v2 (canonicalise without v2-only fields when they are at
their defaults, or sign by schema version), so the committed reference `build-report.json` files verify **unchanged**.
Do not re-sign or rewrite the committed reference releases to make the test pass. Add regression tests: (1) the
committed reference signatures verify; (2) a golden test pinning `spec_signature()` of each committed v1 reference spec
to its stored value; (3) a v2 spec's signature changes when a v2 field changes. Find out why the sandbox gates did not
catch this (the e2e file was reported green) and close that gap.

**Owner check of sub-step A (2026-10-28, fix first in the next sub-step):** the Meridian portal loads its guides but
shows no Operations: `simulators/devportal/meridian.html` calls `fetch("source")`, which from `/developers` resolves
to `/source` (404). Use the absolute `/developers/source` like the other portals, and add a unit test that the page
references `/developers/source` and a rendering test (or Playwright check) that at least one operation page appears.
8. Gates as REQ-019, README vendor table updated. This is the largest REQ in the queue: split it into claimable
   sub-steps in the build note (4, 5a schema+runtime, 5b discovery, 5c joins, 6, 7) and merge each one green.

### REQ-026 — Per-test timeout so a hanging test fails fast with a stack trace (2026-10-29)
Status: DONE (2026-10-28)
Resolution: pytest-timeout 2.4.0 added (unit default 30 s thread-method; e2e items 180 s via conftest mark), mechanism + mark tests green, LEARNINGS #23; make test green (2283/99/325 pytest + 156 UI), no timeouts hit.
Priority: small; do it right after the REQ-022 sub-step in progress, before the next sub-step (the edge agent and
PABX tests are socket code that can hang).
Why: in REQ-022 sub-step A a hanging FIAS socket test cost ~15 of 78 minutes. The first full run waited for the
shell's 300 s kill with no output; reruns waited 120/100/90/60 s; `--timeout=30` failed ("unrecognized arguments")
because `pytest-timeout` is not installed; the hang was located only by rerunning with `faulthandler`.
Measured 2026-10-29 on main (2cda0f3e): slowest unit test ~4 s (FIAS teardown), whole unit suite 54 s; one e2e test
spent 31 s in setup waiting for the stack. So a flat 30 s limit is safe for unit tests and wrong for e2e.
What to do:
1. Add `pytest-timeout` to the dev dependencies (update the lockfile the usual way, both host and sandbox).
2. Default limit 30 s per test in `[tool.pytest.ini_options]` (`timeout = 30`), with `timeout_method = "thread"` so a
   hung test prints every thread's stack (that is the point: it shows where the hang is) and works on macOS and Linux.
3. `tests/e2e/`: 180 s per test, applied to the whole folder from `tests/e2e/conftest.py` (e.g. add
   `pytest.mark.timeout(180)` to every collected e2e item), not by editing each test. Stack startup in fixtures must
   fit inside it; if a session fixture legitimately needs longer, give that fixture its own explicit wait and say why.
4. No test may raise its own limit above these without a one-line comment giving the measured reason.
5. Tests: a unit test (pytester or a subprocess) proving a deliberately sleeping test fails at the limit with a
   "Timeout" message and a stack trace; and one proving an e2e item gets 180 s.
6. LEARNINGS: one line for agents: run single tests with `-x` and rely on the 30 s limit; never wrap pytest in
   long shell timeouts to find a hang.
Acceptance: `make test` and `make e2e` (stack up, local mode and Docker) green with no timeouts hit; total runtime
unchanged within noise.
Owner note (not for this REQ, just context): on the host, 6 unit tests once failed with returncode -11 (bash/npm
subprocess segfault under load: dotenv loader, ui typecheck, export_openapi --check); they pass alone. If the
timeout work shows them again, note it in BUILD-NOTES; do not raise limits to hide it.

### REQ-017 — Run the demo without Docker (2026-10-28)
Status: DONE (2026-10-28) — make local-up/down (same host ports, ./local db/logs/pids), LocalProcessTarget in local mode via runtime_mode() honoring DOCKER_HOST, /readyz runtime_mode+network_isolation, UI LOCAL badge, smoke skips w/ note, e2e reuses green stack + local-aware edge test, README No-Docker + safety-model §12; verified live incl. DOCKER_HOST=broken acceptance cycle.
Today the only way to run AutoBridge is `make up` (Docker Compose). Someone who downloads the repo may have no Docker
and no sandbox. The demo must run on a plain machine with Python + `uv` and Node/npm.
Wanted:
1. `make local-up` / `make local-down` (or `make dev`): start every service the demo needs as local processes on the
   same host ports as compose (control 8101, verifier 8104, simulators, edge-worker, ui 8107 …), SQLite in a temp or
   `./.local/` dir, logs in `./.local/logs/`, PIDs tracked so `local-down` stops them. Wait for every `/readyz` like
   `make up`. Works on macOS and Linux; note Windows (WSL) in the README.
2. The runtime uses `LocalProcessTarget` instead of `DockerSandboxTarget` when Docker is not available (or when
   `AUTOBRIDGE_RUNTIME=local`), and says so in `/readyz` and in the UI header (e.g. "local mode: no network
   isolation"). Never pretend the sandbox exists.
3. Trust boundaries still hold in code: the engine still never imports verifier/ or simulators/ (import-linter), the
   verifier and simulator admin still run as separate processes, and admin writes still need `SIM_ADMIN_TOKEN` when
   set. What local mode cannot enforce (Docker network separation) is listed in docs/safety-model.md and printed once
   at startup.
4. `make demo`, `make smoke` (with the network-isolation checks skipped and reported as "skipped: local mode") and the
   UI journey work against local mode. `make e2e` runs the live tests against it too.
5. README quickstart gets a "No Docker" section: prerequisites (Python 3.12, uv, Node 22), `cp .env.example .env`,
   `make local-up`, open http://localhost:8107, `make demo`, `make local-down`.
6. Check: in a fresh clone with Docker unavailable (`DOCKER_HOST=unix:///nonexistent.sock`), `make local-up && make
   demo && make local-down` passes.

### REQ-008 — `make bench` writes an all-zero scorecard from the host (2026-10-28)
Status: DONE (2026-10-28) — harness auto-selects host URLs outside container + Makefile bench targets pass --control-api-url/--verifier-url host ports; pre-scorecard probe of /healthz and zero-tool-calls guard exit non-zero naming URL; tests: host URL selection + probe/guard. Real bench run skipped (no compose in sandbox).
Priority: do this first. `make bench-heldout` on Nov 3 has the same bug.
Found by a clean-clone check of f516bc0. With the stack up (`make up`), `make bench` exits 0, but every number for
both arms is zero: verified completion 0.0, 0 tool calls, 0 ms elapsed, 0 frontier tokens, "10 wins, 20 losses".
Cause: the Makefile runs `uv run python -m autobridge.bench.harness` on the host, but `BenchConfig` defaults to the
Docker-internal URLs (`DEFAULT_CONTROL_API_URL = "http://control:8000"`, `DEFAULT_VERIFIER_URL = "http://verifier:8000"`
in `src/autobridge/bench/harness.py`). Those names do not resolve outside the compose network. The host constants
`DEFAULT_CONTROL_API_URL_HOST` (8101) and `DEFAULT_VERIFIER_URL_HOST` (8104) are defined but never used. Every
trial fails, is counted as "no verified completion", and the run still exits 0.
Wanted:
1. Running from the host reaches the stack. Either the harness picks the host URLs when it is not inside a container,
   or the `bench` and `bench-heldout` targets pass `--control-api-url`/`--verifier-url` (or run inside the `bench`
   container). Keep the in-container defaults working.
2. Fail loudly. Before any scorecard is written, if the control API or the verifier is unreachable, or any arm
   finishes with zero tool calls across all its trials, exit non-zero with a message that names the URL. An empty
   scorecard must never be produced silently.
3. Add a test for each: host URL selection, and the zero-trial or unreachable guard.
4. With the stack up, run `make bench` once and record the real dev-split numbers in the build note (not zeros).
Do not touch `verifier/hidden_cases/heldout/SHA256SUMS` or anything under `HELDOUT_DIR`.

### REQ-019 — Reshape the Atlas simulator after the Cloudbeds PMS API (2026-10-28)
Status: DONE (2026-10-28) — Atlas serves the method-style hotel PMS surface (REQ-019 gates green).
Owner wants the simulated vendors to look like real hotel PMS APIs. Atlas (the "clean, documented" vendor) follows the
**Cloudbeds API** shape. Keep the fictional name Atlas/AtlasPMS everywhere (simulator, docs site, UI): copy the API
*shape*, never Cloudbeds' name, logo, branding or doc text. Read the reference before coding and match names exactly:
https://developers.cloudbeds.com/reference/get_getreservations-2.md, `get_getreservation-2.md`, `get_getguest-2.md`,
https://developers.cloudbeds.com/reference/tech-specs.md, and the docs index https://developers.cloudbeds.com/llms.txt
(find the hotels/property and transactions endpoints there; if a field cannot be confirmed, say so in the build note).
What the research already confirmed (2026-10-28):
- Methods are named endpoints under a versioned prefix: `GET /api/v1.3/getReservations`, `GET /api/v1.3/getReservation`
  (`reservationID`), plus the property list (getHotels) and transactions/folio endpoint from the index.
- Auth: `x-api-key: <key>` header (also accept `Authorization: Bearer <token>` as OAuth2). Missing → 401.
- Envelope: `{"success": true, "data": [...], "count": <items on this page>, "total": <all>}`.
- Pagination: page based, `pageNumber` (default 1) and `pageSize` (default 100, max 100). Filters include
  `propertyID`, `status`, `modifiedFrom`/`modifiedTo`, `checkInFrom`/`checkInTo`, `resultsFrom`/`resultsTo`.
- Reservation fields (camelCase): `propertyID`, `reservationID`, `dateCreated`, `dateModified`, `status`, `guestID`,
  `guestName`, `startDate`, `endDate`, `adults`, `children`, `balance` (a JSON **number**), `sourceName`.
  Status enum: `not_confirmed`, `confirmed`, `canceled`, `checked_in`, `checked_out`, `no_show`.
- Dates `YYYY-MM-DD`; date-times `YYYY-MM-DD HH:MM:SS` (no zone; document the zone you choose and keep it a real trap).
- Rate limit 10 req/s per key (the simulator may model it).
Wanted:
1. `simulators/atlaspms/app.py` serves the Cloudbeds-style surface above (old routes removed, not aliased), backed by the
   same seeded generator, with the fault switchboard still working on it. Money arrives as JSON numbers: the engine must
   parse them to `Decimal` without float rounding (DIRECTIVES §4) and the verifier must check that.
2. Replace `simulators/docs/atlas-openapi.yaml` and `.json` (and the `atlaspms-*` aliases) with an OpenAPI 3.1 document
   of the new surface; the `/developers` page re-renders it automatically. Update `simulators/devportal/openapi.js`
   only if the new spec needs it (e.g. the curl example must send `x-api-key`).
3. Update everything that encodes the old Atlas shape: the reference connector (`generated/connectors/`), runtime and
   discovery (page pagination, status mapping, `canceled` → canonical `cancelled`, `not_confirmed` mapping decided and
   documented), verifier oracle, the 14 fault scenarios where they touch Atlas, bench, `make demo`, UI copy, e2e and
   unit tests. Atlas pagination becomes `page`, so demo step 3 (offset → cursor on Atlas) needs a new L1 rule
   `page_to_cursor` (mirroring `offset_to_cursor`, zero frontier tokens, same tests). Keep `offset_to_cursor` covered by
   its existing rule tests and fixtures (not by switching the demo or its tests onto another vendor).
4. Heldout: never touch `verifier/hidden_cases/heldout/SHA256SUMS` or `HELDOUT_DIR`. The heldout scenarios must still
   run against the new Atlas (`make bench-heldout` dry run with the SHA check passing).
5. **LegacyStay stays unseen** (owner, 2026-10-28; plan S1: "unseen generalization vendor, heldout only … no
   vendor-specific rule added"). No L1 rule, hypothesis enumerator, classifier, prompt or knowledge entry may
   reference LegacyStay or be developed or tuned against it; no new tests target it by name except the existing S1
   bootstrap/repair test. Add a guard test that greps `src/autobridge` for `legacystay` (case-insensitive) outside
   comments and fails on a hit. Showing LegacyStay in the demo (its docs page, a run against it) is fine; tuning on it
   is not. Meridian and the FIAS PABX are dev/demo vendors: no heldout cases for them, and the heldout manifest is not
   re-frozen.
6. Gates: `make test`, `make up && make smoke && make e2e && make demo` green; README vendor table updated.

Resolution: simulator serves GET /api/v1.3/getReservations/getReservation/getGuest/getHotels/getTransactions (envelope success/data/count/total, pageNumber/pageSize, x-api-key or Bearer, canceled spelling, JSON-number money, NY zone-less datetimes); old routes removed; OpenAPI yaml/json + aliases + devportal curl regenerated; reference spec maps the new wire (page+total_path, canceled->cancelled, not_confirmed blocked, propertyID currency enum_map); L1 rule page_to_cursor added (offset_to_cursor kept via fixture tests); runtime tokenless-page (total_path/total_reached); discovery learns page params/wire names/auth; oracle+checks handle page_to_cursor; LegacyStay guard test added (no legacystay refs in src/autobridge); gates: make test green (autobridge 91.43%, verifier 93.78%, simulators 96.21%, ui 148 pass), local-up+smoke+e2e+demo green (no Docker in sandbox), README table updated. Classic getTransactions reference page is retired, so its item fields follow v1.3 camelCase conventions (documented unconfirmed); no Cloudbeds name in code/docs/UI (shape only). Agent: opencode meta/muse-spark-1.3-contributor (escalation).

### REQ-016 — `make demo` reports [ok] when its writes fail (2026-10-28)
Status: DONE (2026-10-28) — fail-loud writes (non-2xx fails step, exit non-zero), per-step live/in-process labels; 401+500 unit tests.
With the token missing, `make demo` printed `control POST /connectors -> 401` and `POST /demo/inject-fault -> 401`,
yet every step said `[ok]` and the run exited 0. A demo check that cannot fail protects nothing.
Wanted:
1. Any live control-API call that is part of a step must succeed (2xx) or the step is FAILED and `make demo` exits
   non-zero with the method, path, status and detail.
2. Say clearly in the output which steps ran against the live stack and which were computed in-process.
3. Tests: a 401 and a 500 on a step's write make that step fail.
Resolution: `_finish_step()` fails a step on any non-2xx live control call (method/path/status/detail in error); `main` exits 1; header + per-step `(live)`/`(in-process)` labels; 4 new unit tests; docs/demo.md notes the contract.

### REQ-015 — e2e tests do not send the control token (2026-10-28)
Status: DONE (2026-10-28)
With `AUTOBRIDGE_CONTROL_TOKEN` exported, 6 tests fail with `401 Missing or invalid control token`:
`test_s2_edge_target.py::test_edge_execute_signed_release_live`, `test_s3_discovery_import.py` (import_and_list fake +
live, lifecycle live + fake), `test_s7_cli.py::test_s7_cli_repairs_approve_and_reject_fake`. Ralph's gates miss this
because the sandbox runs with no token.
Wanted:
1. One shared helper in `tests/e2e/conftest.py` for control-API headers (token when set); every live call uses it.
2. In-process (fake) tests set or clear the token themselves with monkeypatch, so they pass whatever the shell has.
3. The CLI sends the token when `AUTOBRIDGE_CONTROL_TOKEN` is set (check `src/autobridge/cli.py`).
4. `make e2e` passes both with no tokens and with all tokens set; add a test matrix note to docs/architecture.md.

Resolution: shared control_headers() in conftest used by every live call; fakes pin token via monkeypatch; CLI token path pinned by unit test; matrix note in docs/architecture.md; e2e fakes green with and without tokens.

### REQ-014 — Host tools ignore `.env`, so tokens break smoke, e2e and demo (2026-10-28)
Status: DONE (2026-10-28) — host targets source scripts/load_dotenv.sh (non-empty KEY=value only, explicit env wins); blank E2E_TIMEOUT/DATABASE_URL/DEMO_SEED/CONTROL_URL/EDGE_* count as unset; loader + empty-handling unit-tested.
Resolution: `make smoke/e2e/demo/bench` just work with tokens set in `.env`; verified by new loader/env unit tests (live stack run not possible in sandbox).
With `SIM_ADMIN_TOKEN`, `AUTOBRIDGE_CONTROL_TOKEN` and `EDGE_RELEASE_SECRET` set in `.env`, compose passes them to the
containers, but `make smoke`, `make e2e`, `make demo` and `make bench` run on the host and never read `.env`. Result:
smoke says the token is unset, e2e has 35 errors (simulator reset rejected), demo writes get 401. It only works after
`set -a; . ./.env; set +a`.
Wanted:
1. The Makefile loads `.env` for host targets when it exists (e.g. a small loader that exports only non-empty
   `KEY=value` lines and ignores comments and inline `# …` comments), so `make demo` just works with tokens set.
2. Empty values never override defaults: code that reads `E2E_TIMEOUT`, `BENCH_*`, `DATABASE_URL` etc. treats `""` as
   unset (today `E2E_TIMEOUT=` crashes conftest with `float('')`; an empty `DATABASE_URL` breaks readyz).
3. Test: with all three tokens set in a temp `.env`, `make smoke` enforces them and needs no manual export.

### REQ-018 — UI in compose cannot load anything: browser calls `http://control:8000` (2026-10-28)
Status: DONE (2026-10-28)
Opening http://localhost:8107/connectors after `make up` shows "Failed to load connectors: Failed to fetch".
Cause: `ui/Dockerfile` bakes `NEXT_PUBLIC_API_URL=http://control:8000` into the browser bundle (confirmed: the string
is in `.next/static/chunks/app/connectors/page-*.js`), and `ui/lib/api/hooks.ts` `getBaseUrl()` and
`ui/lib/api/client.ts` `createApiClient()` use it in the browser. `control` only resolves inside the compose network,
so every browser request fails. It also bypasses the `/api/*` rewrite, so `ui/middleware.ts` never adds the control
token (REQ-012) and browser writes would get 401 even once reachable.
Wanted:
1. In the browser, every control-API call (SWR hooks, the openapi client, SSE in `ui/lib/sse.ts`, the demo drawer,
   approve/reject, build, sync) goes to same-origin `/api/...`. Only server-side code may use `CONTROL_URL`. Drop the
   `NEXT_PUBLIC_API_URL` build arg (or make it unused in the browser).
2. Writes from the browser pass through the middleware and work with `AUTOBRIDGE_CONTROL_TOKEN` set; the token never
   appears in any `.next/static` chunk (add a check: grep the built chunks for the token name and value).
3. Works the same in local mode (REQ-017): the UI's `/api` rewrite points at `CONTROL_URL`, default
   `http://localhost:8101`.
4. A Playwright check that loads /connectors against `make up` with no error banner, and a test that fails if any
   built browser chunk contains `control:8000`. `make e2e-ui` must cover the connectors list, the inject-fault drawer
   and an approve.
5. Small: the "healthz" text link in the top bar (`ui/components/TopBar.tsx:96-99`) looks like debug output on the
   demo screen. Keep the `/healthz` route and its `data-testid` for the e2e journey, but never show the word
   "healthz" to the user: label the link "System status" (or make it the LIVE indicator's link with a tooltip such as
   "All services healthy" / "Control API unreachable"), and move it to a footer or style it as a quiet status detail
   next to LIVE. Update the e2e journey to find it by `data-testid`, not by its text.
Resolution: browser calls same-origin /api only (new ui/lib/api/base.ts shared by hooks/client/SSE/pages/components); rewrite uses server-only CONTROL_URL (compose build arg + runtime env, default localhost:8101); Dockerfile bakes no NEXT_PUBLIC_*; TopBar link relabelled 'System status' (testid kept); Playwright: live no-error-banner + bundle-chunk (no control:8000, no token name) checks, mocked approve click, testid-based healthz assertion; unit hygiene tests (no NEXT_PUBLIC_API_URL / control hostname in browser code, compose CONTROL_URL build arg + env). Verified live: make up + make e2e-ui 7 passed / 4 documented skips; make test all green.

### REQ-013 — Build → sync is broken since AR-1-2; sync also runs unapproved releases (2026-10-28)
Status: DONE (2026-10-28)
Found by a host regression on 207f7366 (stack up). AR-1-2 (76bbff43) removed the sync fallback to the latest release,
but nothing activates a freshly built release: `Connector.active_release_id` is only set by `_activate` in
`src/autobridge/engine/releases.py`, reached from repair approval. So after create → build, `POST
/connectors/{id}/sync` answers `422 connector has no active release`. `tests/e2e/test_s7_cli.py::test_s7_cli_same_journey_as_ui_fake`
fails on exactly this (with and without tokens); the UI's Sync button on a newly built connector will do the same.
Second gap: `trigger_sync` in `src/autobridge/control/routes/builds.py` takes `body.release_id` and only checks that the
release belongs to the connector, not that it passed the gate or was approved, so an unapproved candidate can still run.
Wanted:
1. Decide and document (docs/safety-model.md) how a first build becomes runnable. Recommended: a build whose candidate
   passed the verifier gate is activated as the connector's first release, or the operator activates it explicitly
   (e.g. `POST /connectors/{id}/releases/{release_id}/activate`, write-token protected, actor from AUTOBRIDGE_OPERATOR)
   and the UI and CLI show that step. Either way, a gate-blocked build stays unrunnable.
2. `sync` with an explicit `release_id` accepts only a release that passed the gate and is active or approved; anything
   else is 422 with the reason. Unit tests for both.
3. The demo journey works end to end: create → build → (activate) → sync, in the UI, in the CLI, and in `make demo`.
   Fix `test_s7_cli_same_journey_as_ui_fake` by following the new flow, not by loosening the gate.
Resolution: first build auto-activates when the connector has no active release; later build candidates need POST /connectors/{id}/releases/{rid}/activate (write-token protected, actor=AUTOBRIDGE_OPERATOR; refuses repair/rejected/gate-blocked/foreign); sync with explicit release_id requires active-or-approved (+gate evidence) else 422; UI Activate button + `ab activate`; flow in docs/safety-model.md §7.1; tests: tests/unit/test_autobridge_activation.py (10), e2e s7 journey follows the new flow.

### REQ-012 — Optional bearer token on the control API's write routes (2026-10-28)
Status: DONE (2026-10-28)
The control API has no authentication; the only identity is the server-side `AUTOBRIDGE_OPERATOR` name (REQ-001).
That is fine on localhost (REQ-011), but not if the stack is ever hosted for judges.
Wanted:
1. New setting `AUTOBRIDGE_CONTROL_TOKEN`. When unset or empty, behavior is exactly as today (local runs, tests, demo
   need nothing). When set, every state-changing route (POST/PUT/PATCH/DELETE in `src/autobridge/control/routes`)
   requires `Authorization: Bearer <token>` and answers 401 without it, compared in constant time
   (`hmac.compare_digest`). Read-only GETs, `/healthz`, `/readyz` and the SSE stream stay open.
2. The UI never ships the token to the browser. The UI proxies `/api/*` to the control API through the rewrite in
   `ui/next.config.mjs`; add the header on the server side (Next middleware or a route handler that forwards
   to the control API), reading `AUTOBRIDGE_CONTROL_TOKEN` from the UI container's env. No `NEXT_PUBLIC_` variable.
3. Every other caller sends it when set: `make demo` (`autobridge.bench.demo`), the bench harness, e2e helpers, and
   `scripts/` that call the control API.
4. The token is a credential: never logged, never in API responses, evidence, prompts or the OpenAPI examples; the
   SSE redaction in `control/sse.py` already covers `*_TOKEN` names, so check it covers this one.
5. Tests: unset → write routes open; set → 401 without or with a wrong token, 2xx with the right one; GETs open
   either way. Update the OpenAPI contract (security scheme on the write routes) and `make openapi-check`.
6. `.env.example` gets `AUTOBRIDGE_CONTROL_TOKEN=` with a one-line note (set it before exposing the stack beyond
   localhost); `compose.yaml` passes it to `control` and `ui`.
Resolution: `require_control_token` dep on all 15 POST routes (401 + HTTPBearer scheme + documented 401); open when unset; UI middleware injects server-side; harness/demo/CLI/e2e send when set; compose+env wired; redaction verified; 11 new unit tests + contract fuzz with enforcement on.

### REQ-011 — Publish compose ports on localhost only (2026-10-28)
Status: DONE (2026-10-28)
Every port in `compose.yaml` is published as `"81xx:8000"` with no host IP, so Docker listens on all interfaces. While
the stack runs, anyone on the same network (e.g. hackathon Wi-Fi) can reach the control API (unauthenticated write
routes: approve/reject repairs, inject faults, import/connect connectors), the UI, the simulators, and the verifier on
8104, whose hidden cases must stay out of reach.
Wanted:
1. Every published port binds to loopback: `"127.0.0.1:8101:8000"` and so on, for all services and every second port
   (8109–8112). Container-to-container traffic on the compose networks is unchanged.
2. Optional override for anyone who needs LAN access on purpose: a `AUTOBRIDGE_BIND_HOST` variable, default `127.0.0.1`,
   used as `"${AUTOBRIDGE_BIND_HOST:-127.0.0.1}:8101:8000"`. Document it in `.env.example` with a warning.
3. A test that parses `compose.yaml` and fails if any published port lacks a host IP, or uses `0.0.0.0` as a literal.
4. `make up`, `make smoke`, `make e2e` and `make demo` still pass on the host (localhost URLs are unchanged).
Note the trust-boundary smoke checks: the verifier and simulators stay unreachable from the engine network as before.
Resolution: all 15 published ports bound to `${AUTOBRIDGE_BIND_HOST:-127.0.0.1}` (default loopback); `.env.example` documents the override with a warning; new `test_published_ports_bind_loopback` fails on a missing host IP or a literal `0.0.0.0`; `make up`/`make smoke`/live `test_00`/ `make demo` green on the host, live bindings verified as 127.0.0.1.

### REQ-010 — e2e: run the demo's headline claims against the live stack (2026-10-28)
Status: DONE (2026-10-28)
With Docker hidden (`DOCKER_HOST=unix:///nonexistent.sock`), 72 of the 104 tests in `tests/e2e` still pass: they run
engine code in-process against fake HTTP transports. Only about 32 exercise the running stack. In particular
`test_03` (known repairs spend 0 frontier tokens) passes 5/5 with no stack, and `test_05` passes 20 of 22. These are
claims the demo shows on screen.
Wanted:
1. Live-stack versions of the `test_03` and `test_05` scenarios: drive them through the control API on the running
   compose stack (as `make demo` does), and assert the same things: zero frontier tokens for known repairs; the
   invalid patch rejected; semantic drift ends `Degraded`.
2. Mark the in-process tests clearly (a `fake` or `inprocess` marker, or move them to `tests/integration`) so
   `tests/e2e` means "against the stack". Do not delete the in-process tests.
3. If the stack is not reachable, the live tests fail rather than skip when `AUTOBRIDGE_E2E_REQUIRE_STACK=1`, and the
   `make e2e` target sets it.
Resolution: live/inprocess pytest markers (auto-marked in conftest) + 3 live tests via control API (offset_to_cursor frontier 0, gross_to_net Degraded, invalid_patch_trap rejected) + AUTOBRIDGE_E2E_REQUIRE_STACK=1 set by make e2e (live tests fail, not skip, when unreachable) + fixed control proxy missing X-Admin-Network header (inject/reset 403'd, fault never landed).

### REQ-009 — Clean clone: `make test` and `make e2e-ui` fail without `ui/node_modules` (2026-10-28)
Status: DONE (2026-10-28)
Found by a fresh GitHub clone of f516bc0, following the README (`cp .env.example .env`, then make targets).
- `make test`: 1 failed, 1878 passed. `test_ui_client_compiles` runs `npm run typecheck` in `ui/`, and a fresh clone
  has no `ui/node_modules`. The UI's Vitest run was skipped for the same reason.
- `make e2e-ui`: `playwright: command not found`.
CI installs these itself; the README and the Makefile do not. The README says a clean machine needs only Docker and uv.
Wanted:
1. `make test` and `make e2e-ui` work in a fresh clone. Either a `ui-deps` target (`cd ui && npm ci`, plus
   `npx playwright install chromium` for e2e-ui) that they depend on when `ui/node_modules` is missing, or the
   UI-dependent tests skip loudly with the command to run (as Vitest already does). Prefer installing.
2. README quickstart lists Node and npm as a requirement and the `npm ci` step.
3. Check: a fresh clone into a temp dir, then `make test` passes with 0 failures.
This is what P1.W10.3 (clean-clone check) needs.
Resolution: `ui-deps`/`ui-e2e-deps` Makefile targets (prereqs of `test`/`e2e-ui`), `NODE_PATH` in the `e2e-ui` recipe for the root Playwright config, loud skip in `test_ui_client_compiles` without `node_modules`, 6 contract tests, README Node/npm + install step, CI single `make ui-deps`. Fresh-clone check: `make test` 2225 pass / 0 fail + Vitest 143 pass, `make e2e-ui` exit 0 (9 skips by design, no stack up). Drive-by: 4 pre-existing ruff errors in the REQ-008 bench-harness test fixed.

### REQ-007 — Heldout seeds that no agent has seen (2026-10-27)
Status: DONE (2026-10-27)
Owner decision (option B): the 4 heldout scenarios (`endpoint_removed`, `invalid_patch_trap`, `token_revoked`, `tz_boundary_shift`) keep their public YAMLs and seed for dev use, but the **benchmark's heldout run uses private seeds** that live outside the repo and outside every sandbox, so the scorecard can claim "scored on data no agent saw". Today nothing reads `verifier/hidden_cases/heldout/` and the oracle takes seeds from the public YAMLs, so heldout is not hidden.

- **The secrets (owner-made, never in git or in a sandbox):** a directory given by env `HELDOUT_DIR` (the owner uses `~/autobridge-heldout`), one file per heldout scenario, `<scenario_id>.yaml` with `id`, `seed` (int) and `nonce` (random hex, so the published hashes cannot be brute-forced back to the seed).
- **The commitment (in git):** `verifier/hidden_cases/heldout/SHA256SUMS`, `sha256sum` format over those files. `check_heldout_sha` must verify the files in `HELDOUT_DIR` against this committed manifest (not files in the repo folder), exit 2 on any mismatch or missing file, and refuse to run `make bench-heldout` without `HELDOUT_DIR`.
- **The run:** `make bench-heldout HELDOUT_DIR=...` loads each scenario's private seed after the hash check and uses it end to end: the simulator admin scenario call and the verifier's hidden-mode oracle both use the private seed for that run (dev runs and every other path keep the public YAML seed). Seeds never appear in logs, JSONL output, the scorecard or SSE events (record only the scenario id and the manifest hash).
- **Never read the real directory in tests or agent runs.** Unit tests build a temp `HELDOUT_DIR` with fake seeds and a matching temp manifest: hash pass, hash mismatch (exit 2), missing file, missing env, seed reaches simulator and oracle, seed absent from all outputs.
- **Until this lands, `make bench` exits 2 with "heldout SHA check: missing file": that is expected** (the manifest was committed 2026-10-27 in ff372083; its files are deliberately not in the repo). Do this request first in that case. **Never delete, edit or regenerate `verifier/hidden_cases/heldout/SHA256SUMS`, never create files it lists, and never weaken the check to make it pass.** The fix is the scope above: the gate runs only for heldout runs and checks `HELDOUT_DIR`; dev runs (`make bench`, `test_07`) do not need it.
- Update `docs/benchmark.md` (#Heldout-freeze-runbook) to this flow, and the scorecard header to state "heldout seeds private, manifest <sha of SHA256SUMS>".
Resolution: codeable part done (sbx1 2026-10-27) — gate checks HELDOUT_DIR vs committed manifest (exit 2), dev runs skip it; heldout runs push private seeds to sim admin + verifier seed override; scorecard header records manifest sha; seeds never in outputs. Owner step remains: place private seeds in ~/autobridge-heldout/ (P1.W2.8).

### REQ-006 — Directive: §4 money is Decimal, never float — money transform accepts float input (2026-10-27)
Status: DONE (2026-10-27)
`src/autobridge/runtime/transforms/money.py:49-50` accepts a float (via `str(value)`) and `tests/unit/test_autobridge_transforms*.py::test_money_float_input` codifies `transform_money(42.5) == Decimal("42.50")`. The conversion is exact for short decimals, but it contradicts the "never float" wording: a float that has already lost precision upstream is silently laundered into a Decimal. Owner: either reject float inputs (update the test) or amend §4 to "float inputs are converted via str at the boundary only".
Owner decision (2026-10-27): use Decimal. `transform_money` rejects float input (a clear error naming the field), accepts `Decimal`, `int` and numeric strings; replace `test_money_float_input` with a test that floats are rejected, and check no caller passes floats (JSON parsing of vendor responses must use `Decimal`, e.g. `parse_float=Decimal`).
Resolution: transform_money now rejects float with a field-naming TransformError (field propagated via apply_transform); HttpClient.get parses vendor JSON with parse_float=Decimal; tests replaced/added (float rejection, field name, Decimal HTTP parsing); docs/connector-spec.md money row updated.

### REQ-005 — Directive: §3 model ids in config, not code — literal defaults in src/autobridge/config.py (2026-10-27)
Status: DONE (2026-10-27)
`src/autobridge/config.py:9-12` hard-codes `claude-sonnet-4-5-20250929`, `claude-haiku-4-5-20251001`, `gpt-4o`, `gpt-4o-mini` as defaults (env-overridable), and `src/autobridge/bench/baseline_coding_agent.py:18` repeats a frontier id. Strictly, §3 says model ids live in config, not code. Owner: accept defaults-in-config-module as compliant (amend §3 wording) or remove the literals so a missing env var fails loudly. Minor companion: DIRECTIVES §6 says `.env.example` lists every key with an empty value, but the ralph-loop keys in `.env.example` carry non-empty model ids (not secrets).
Owner decision (2026-10-27): fix it. Remove the literal model-id defaults from `src/autobridge/config.py` and `bench/baseline_coding_agent.py`; model ids come from env/config only, and a missing one fails loudly with a clear message naming the env var. Add the engine's model-id env vars to `.env.example` (empty values) and update tests. `.env.example` companion: secrets stay empty; the ralph-loop model ids are not secrets and may keep their values. Amend DIRECTIVES §6 wording to say "every secret with an empty value".
Resolution: removed the four DEFAULT_* literals (helpers now require_env, naming the var); bench docstring examples use <MODEL_ID_FROM_ENV>; .env.example already listed the four engine vars empty; DIRECTIVES §6 now says "every secret"; tests updated (missing-env fails loudly, api-key tests set model env).

### REQ-003 — Directive: §2 trust boundaries — verifier imports simulators; bench lives in the engine package (2026-10-27)
Status: DONE (2026-10-27)
Daily audit 2026-10-27. DIRECTIVES §2 says shared code goes in a package all three may depend on, never the other way, but plan §2.7 mandates the oracle regenerate ground truth via "the simulator's data generator (shared code)" and `verifier/oracle.py`, `verifier/checks.py`, `verifier/regression.py`, `verifier/app.py` all import `simulators.common.*`. The two documents contradict each other. Owner: either amend DIRECTIVES §2 to name `simulators.common` (generator/faults/scenarios) as the sanctioned shared package the verifier may import, or extract that code into a shared package. Related: plan §2.10 places the harness at `bench/harness.py`, but it is implemented as `src/autobridge/bench/` inside the engine package — the audit removed its `simulators` imports (import-linter contract kept again), yet the harness still reads `simulators/scenarios/*.yaml` and `verifier/hidden_cases/heldout/` from the filesystem; decide whether bench is engine code or a fourth, outside-the-boundaries tool and state it in §2.
Owner decision (2026-10-27): the verifier MAY use the simulators' data generator. Amend DIRECTIVES §2 to name `simulators.common` (generator, faults, scenarios) as the sanctioned shared code the verifier may import (the engine still may not), keep `verifier/` importing it, and make the import-linter contract express exactly that. Bench: no change requested; leave `src/autobridge/bench/` where it is, and only state in §2 that its filesystem reads of scenario YAMLs and the heldout dir are a harness concern.
Resolution: DIRECTIVES §2 amended (sanctioned verifier use of simulators/common + simulators/scenarios, engine still isolated, bench reads are a harness concern); import-linter contract 'verifier must not import autobridge' added (both contracts KEPT); tests/unit/test_autobridge_trust_boundaries.py confines verifier simulators imports to common/scenarios; docs/architecture.md noted.

### REQ-004 — Directive: §7 OpenAPI contract — schemathesis tests claimed but absent (2026-10-27)
Status: DONE (2026-10-27)
DIRECTIVES §7 and plan §4 W7 require schemathesis contract tests; `mds/PRD-TRACKING.md` marks P1.W7.1 done with "schemathesis contract tests pass" and `src/autobridge/control/app.py:5` says "(schemathesis validates it)", but there is no schemathesis dependency or test anywhere in the repo. Tracked as task P1.W7.9 in PRD-TRACKING.md. Until it lands, the done-claim wording should be treated as unverified.
Owner decision (2026-10-27): approved. Implement task P1.W7.9: add schemathesis as a dev dependency and contract tests over the control API's OpenAPI schema, wired into `make test`; then the done-claim on P1.W7.1 and the `control/app.py:5` comment become true.
Resolution: implemented as P1.W7.9 — `schemathesis>=4,<5` dev dependency + `tests/unit/test_autobridge_control_contract.py` (34 tests from the live OpenAPI, seed-pinned, in `make test`); genuine fixes shipped with it (UTC datetimes, Literal target, StrictInt seed, documented 404/400/409/oneOf-422).

### REQ-002 — Redesign the UI to the broadcast direction (2026-10-27)
Status: DONE (2026-10-27)
Progress: slice 5 done (2026-10-27 sbx1) — all 5 slices complete.
Restyle the four screens of `ui/` to the approved design: a dark "live broadcast graphic" look with
one accent (broadcast yellow = "now / needs you"), Archivo (variable width) + Fragment Mono, radius 0.
Desktop (1280 and 1440) is what gets demoed; phone only needs to not break.

**The design is a local reference, not in git.** Read it (never copy it into the repo, never edit it)
at `/Volumes/OWC-Express-1M2/Development/autobridge/tmp/design/`:
- `DIRECTION.md`: tokens, type, status shapes, the screens' grammar, the signature ("the climb"),
  voice, and the **"Hand-off: porting to ui/"** section, which maps each existing component to its
  new shape. Read that section first.
- `site/app.css` (tokens + shared styles), `site/app.js` (the climb, URL states), and the four
  screens: `site/repair.html`, `site/connectors.html`, `site/connector.html`, `site/new.html`.
- `shots/r2/` and `shots/r3/` hold renders of the approved state (PNG) if you need to compare.
Do not commit anything under `tmp/` (it is gitignored; keep it that way).

**Do it in slices, one per iteration**, and record which slice is done under this request:
1. Tokens and shell: CSS variables in `ui/app/globals.css`, Tailwind theme mapping, fonts via
   `next/font/google` (Archivo with the `wdth` axis, Fragment Mono), the top bar with LIVE, the
   labelled "Demo: inject fault" button and healthz. Remove `zinc-*`, `red-*`, `rounded-*` use.
2. `/repairs/[id]`: the call (rung numeral, headline, story, drift trace, five-segment track) and
   the scorebug (verifier, frontier tokens, model usage, Approve / Reject / Rollback), then
   hypotheses, probes, visible tests, patch diff. States: climbing, escalated, error, loading.
3. The climb, driven by the run's SSE ladder events instead of the prototype's timers: the frontier
   counter moves only at L3; the verdict and action appear only once a patch passed. Respect
   `prefers-reduced-motion`.
4. `/connectors` (the rundown, recent repairs, empty state) and `/connectors/[id]` (masthead,
   r14-waiting band, sync runs, releases, spec viewer, fingerprint, dependencies).
5. `/connectors/new` (form + "what the build will do" run sheet) and the Inject Fault drawer.

**Rules:**
- Real data from the control API only; the prototype's numbers are sample content.
- Keep every existing `data-testid`, the e2e UI journey (`tests/e2e/test_06_ui_journey.spec.ts`)
  and the network rule (UI never calls simulators or the verifier). Update unit tests to the new
  markup; do not delete tests to make them pass.
- If REQ-001 (operator name) is done first, keep its "Approving as <name>" line in the new scorebug.
- No new runtime dependencies beyond the fonts; say so in BUILD-NOTES if a build step needs network
  for `next/font/google`.
Resolution: all 4 screens in broadcast grammar (tokens/shell, repair call+scorebug, live climb, rundown+masthead, new run-sheet + dashed Inject Fault drawer); every data-testid kept; unit 128 + touched e2e journeys green (test_06 4/6 in sandbox, 2 detail-page failures pre-existing: no control plane).

### REQ-001 — Show the operator's name on approvals (2026-10-27)
Status: DONE (2026-10-27)
There is no login (plan §2.11 has none, and the demo runs locally), but the human rung of the ladder
should have a name. Add a fixed operator name, set by the server, shown next to every approval action
and recorded on every `Approval`.

- **Config:** new env var `AUTOBRIDGE_OPERATOR` (default `operator`), read by `control`. Add it to
  `.env.example` with a one-line comment.
- **Server sets the actor:** `POST /repairs/{id}/approve|reject` and `POST /releases/{id}/rollback`
  write `Approval.actor` from `AUTOBRIDGE_OPERATOR`. Never take the actor from the request body.
- **API:** expose the name to the UI (for example `GET /operator` → `{"name": "..."}`, or a field on
  an existing endpoint), included in the OpenAPI schema so `ui/lib/api` regenerates.
- **UI (`ui/`, the real Next.js app):** in `RepairActions`, a quiet line under the Approve button:
  "Approving as <name>". Release history (`ReleaseHistory`) shows who approved each release, e.g.
  "r13 · approved by <name> · 13:41".
- **Tests:** unit test that the actor comes from the env var and ignores any client-sent actor; UI
  tests for the "Approving as" line and the release-history name; keep existing `data-testid`s.

Context: the design prototype in `tmp/design/` is local only (gitignored) and not available to the
loop; implement this in `ui/` and `src/autobridge/control/`, matching the current styling.

Resolution: server sets Approval.actor from AUTOBRIDGE_OPERATOR (GET /operator); UI shows 'Approving as <name>' + approved-by per release; compose forwards the var.

