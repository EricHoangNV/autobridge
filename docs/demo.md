# AutoBridge — demo (plan §6, 3–4 minutes, real traces)

> `make demo` drives all eight steps through the control API (P1.W10.2). This page is
> the script: what each step does, the calls it makes, and the evidence it renders —
> the same evidence the UI screens show. Every step is reproducible from seeds and
> release ids.

Prerequisites: `make up` (stack green) for the live path — `make demo` then
drives every call through the control API (`CONTROL_URL`, default
`http://localhost:8101`) and prints each call's redacted status beside the
evidence. With the stack down it computes the same evidence locally through
the engine (reference specs + deterministic rules + escalation bundle) and
says so in its header/footer, so the script is reproducible anywhere
(`DEMO_SEED=… make demo`, `--json` for the machine-readable report).
Any live control-API call that is part of a step must return 2xx —
otherwise the step is marked FAILED (with method, path, status and detail)
and `make demo` exits non-zero. Each step is labelled `(live)` or
`(in-process)` and the header lists which steps ran live.
Reference specs under
`generated/connectors/{atlaspms,harborpms}/reference/`. Fault injection goes through
the single labelled bridge `POST /demo/inject-fault` (proxied to the simulator admin
API on `admin-net`; every request carries `demo: true, labelled: true`).

Related: [Architecture](architecture.md) §9 (control API + UI),
[Safety model](safety-model.md) §4 (scenario catalog), `docs/benchmark.md`.

## Recording from the UI (REQ-033 — the same story, presenter + browser)

This is the UI-first version of the script above: the presenter runs one
command per scenario in a terminal while the browser (`http://localhost:8107`)
reacts by itself. Start with `make local-up` (or `make up`), then
`make demo-reset && make demo-seed`: the opening frame reads
"0 connectors · ready to build" — every simulator up on its baseline
scenario, no connectors, no repairs, nothing learned, no injected
faults. Every connector in decks 2–7 is built live from its docs; the
seed builds nothing. Every state badge is colour +
shape + word, never colour alone; the Repairs list polls every 2 s, so new
repairs appear at the top without a reload. The presenter clicks every
approval — nothing auto-applies, so every transition stays on screen.

- **Deck 1 — What AutoBridge knows.** Point at `/knowledge` (top bar): each
vocabulary row reads "no connector built on it yet", the right-hand
column reads "Nothing learned yet", and Decisions reads "No operator
decisions yet". On `/connectors` the header reads "0 connectors" and
the empty state leads with **"Build a connector"**.
- **Deck 2 — Build Atlas from its OpenAPI.** On `/connectors/new`, pick the
Atlas docs, keep vocabulary v1 and **Build** — docs read → draft →
probes → result → release r1, first sync Complete, AtlasPMS ● Working
with 0 model calls. `/knowledge` now lists atlaspms under v1; the
Connectors header reads "1 connector".
- **Deck 3 — Zero-token fix.** Left: `make demo-break SCENARIO=page_to_cursor`.
  Right, on `/connectors`: AtlasPMS turns ▲ Degrading ("drift detected
  … · repairing at L1"). On `/repairs`: the new row reads connector
  AtlasPMS, class `PAGINATION_CHANGE`, rung L1, rule `page_to_cursor`,
  "0 model calls", status "fix ready". Open it: the headline tells the whole
  story in one sentence, then the storyline 1 happened → 2 did →
  3 recommends jumps to each chapter. **1 What happened** (connector +
  detecting sync with its outcome and unmet criterion, the wire
  before/after "page → cursor", ▸ affected records and evidence with
  redacted samples); **2 What the engine did** (rung timeline "L0: no
  known fix …" → "L1: rule page_to_cursor matched", proof tiles — model
  calls, verifier, hypotheses/probes — the hypothesis funnel
  "24 explanations → … → 1 distinct" with expandable stages, probes,
  ▸ model usage across runs, ▸ evidence graph); **3 What the engine
  recommends** (per-operation Now → Proposed table grouped by operation,
  raw JSON Patch one click away with copy). The decision rail sits beside
  the story: Approve release r2 with what Approve does, Reject with what
  it does, ▸ roll back, "Approving as …". Click **Approve** in the rail:
  the page follows approval → release → verifying sync →
  Complete, and the connector returns to ● Working. Break it again:
  the same drift resolves at L0 and the Knowledge entry reads "reused 1×".
- **Deck 4 — Build Harbor from its wiki.** On
  `/connectors/new` pick the Harbor docs (the picker lists the simulator
  docs from `config/demo_targets.yaml`; the server fetches the text, the
  browser only sends `docs_text` back, so the AR-2-2 SSRF guard stays
  shut). Watch the five-stage progress — docs read → draft → probes →
  result → release — including the line "check added from the docs:
  totals must match `meta.gross_sum`" — the same stages as step 2 below,
  then the new connector's page with its spec, releases and sync runs,
  ● Working.
- **Deck 5 — Break Harbor right after.** Left: `make demo-break
  SCENARIO=gross_to_net TARGET=harbor`. Right: the scheduled sync
  Degrades on `money_invariant` — HarborPMS turns ▲ Degrading ("drift
  detected …"), then ■ Error ("needs your decision") as the engine
  escalates. On `/repairs`: the new row reads connector HarborPMS,
  class `SEMANTIC_VALUE_DRIFT`, rung L4, decision "escalate".
  Open it: the rail leads with the decision line, the model's question and
  its rationale (or the engine's question when the model offered none)
  plus the one proposed change when the model offered it — with its
  effect and the diff. The chapters read from the break's own rows —
  chapter 2 holds the climb (L0 miss → L1 no rule → L2 skipped with its
  one explanation → exactly one L3 attempt → L4 escalate), the hypothesis
  funnel (generated → amount → wire-consistent → distinct, ending on the
  single "net" reading), the proof tiles and the single frontier call — live
  ("muse-spark-1.3, N tokens") with `FRONTIER_API_KEY` set, otherwise
  the labelled replay ("replayed from a live Muse capture on …"), or the
  explicit L3 skip naming the capture to run when neither exists — and
  the verifier line ("no patch to verify (the model escalated)" when the
  model asked, the verdict on the proposal otherwise). Click
  **Approve: \<the model's label\>** ("accept net"): release r2 from the
  proposal, verifying sync, Harbor back to ● Working — recorded as a
  business decision, never learned, rollback to r1 stays available. Or
  click **Deny** ("keep everything as-is, contact the vendor"): the
  connector stays ■ Error and the drift stays open
  (a semantic drift is never patched without the operator). The L3 question is well-posed: the
  prompt names the patch target (the connector spec), lists the exact
  JSON Pointer of every patchable member, shows redacted raw vendor
  records, and lets the model answer `{"escalate": ...}` — a
  non-applying patch gets one retry with the error, then escalates.
  Every proposal is also dry-run against the break's own records before
  it is offered: a patch that references a wire field the vendor never
  sends, that leaves the failing checks failing, or whose patched check
  cannot evaluate on those records (e.g. a money check repointed at a
  record count — the comparison would silently switch off), gets one
  retry with the exact error, then is dropped to question-only — never
  offered for Approve. A proposal that passes shows "tested on the current records:
  the checks pass", with each changed target field annotated from the
  observed response (e.g. what `$.total` actually carries on the wire). If a verifying sync after an approval still fails,
  the engine rolls back automatically and the original escalation stays
  the one open item (never a second escalation for the same drift).
  The L3 answer may also carry the change the operator could approve
  (`{"escalate": ..., "proposal": {"label", "effect", "patch"}}`) — a
  malformed proposal is dropped and labelled, never guessed. When the
  break's L3 was skipped, `make demo` step 4 additionally records its
  instrumented climb (same inputs) as the documented fallback row; when
  the break attempted L3, no second row is written.
- **Deck 6 — New kind of API.** Build Meridian live, twice, from the same
  docs: on `/connectors/new` pick the Meridian docs, keep vocabulary v1
  and Build — the vocabulary check refuses on screen with the engine's
  named reason (no POST with a body in ConnectorSpec v1) and creates no
  connector; 0 model calls, the attempt recorded by name. "Download
  capability request" drafts the gap (needed vs v1, the docs' operations,
  acceptance tests) as a Markdown file shaped for the build loop — no
  model used. Switch to v2
  (one click): the one Meridian connector is created, release r1, first
  sync Complete, MeridianPMS ● Working. The connector page shows both
  attempts (v1 refused with its reason, v2 in use), `/knowledge`
  lists Meridian under v2 with v1 refused, and the result carries the
  engine's runtime decision ("runs: cloud"). Deck 6 has no break.
- **Deck 7 — On-premise PABX.** Build the PABX live from its spec, twice
from the same docs like deck 6: on `/connectors/new` pick the FIAS PABX
docs — there is no base URL to type and no runtime to choose: the relay
address is fixed and vocabulary v3 is suggested, because the engine runs
framed docs on the on-premise edge agent (the result says "runs:
on-premise edge agent, via the relay"). Keep/switch the vocabulary to
**v2** and Build — the vocabulary check refuses on screen with the
engine's named reason (event-driven feeds are not expressible in
ConnectorSpec v2) and creates no connector; 0 model calls, the attempt
recorded by name. "Download capability request" drafts the gap the same
way as deck 6. Switch to **v3** and Build: release r1, the
edge agent connects, postings flow, PABX edge ● Working; `/knowledge`
lists it under v3 with v2 refused. Then
`make demo-break SCENARIO=fias_link_resync` on the live-built
connector: the PABX panel shows the link go down, the agent
re-handshakes (LS/LD/LR/LA), the link comes back — the relay log still
holds every pre-break posting exactly once (nothing lost, nothing
duplicated) — and the connector
stays ● Working (or is back within one tick). No repair, no Approve.
- **Deck 8 — Proof.** The terminal `make demo` runs all eight steps
  through the API against the same seeds and prints the release ids it
  used; `experiments/scorecard.md` and the benchmark rehearsals carry
  the head-to-head numbers (steps 5–6).

Retakes are `make demo-heal` (clears faults, keeps connectors) and
`make demo-reset && make demo-seed` (the blank opening frame: 0
connectors, every simulator on baseline — Atlas is rebuilt live in deck
2, Harbor in deck 4, Meridian in deck 6, the PABX in deck 7).
The terminal
`make demo` approves the same repairs through the API; everything it
does lands in the database exactly like the clicks do (REQ-030), so the
repair page, the Knowledge page and this recording agree.
`AUTOBRIDGE_SYNC_INTERVAL_S` sets the scheduler tick (default 10 s);
`FRONTIER_API_KEY` selects live vs replay for the semantic climb.

## 1. Reusable artifact — Atlas release, zero inference

Build Atlas from its OpenAPI docs on v1 (in-process evidence opens the
reference release spec instead), run a sync, show `ModelUsageLedger = 0`.

```
POST /connectors                       { id: atlaspms, vendor_docs_ref, base_url: ${ATLAS_BASE_URL},
                                         credential_ref: ATLAS_API_KEY, target_entities: [Reservation, FolioCharge] }
POST /connectors/{id}/build            { docs_text (Atlas OpenAPI), spec_vocabulary: v1 } → release r1 (page pagination, 0 model calls)
POST /connectors/{id}/sync             → SyncRun outcome Complete, data_readiness {current, complete}
GET  /metrics/model-usage?run_id=…     → frontier_tokens 0, small_tokens 0, calls 0
```

Renders in `/connectors/[id]`: spec viewer (YAML), release history, sync run
`Complete`, fingerprint id, model-usage view at zero.

## 2. Unfamiliar API — Harbor from Markdown docs

Start a Harbor build from the messy Markdown docs; show hypotheses, probes, evidence,
the resulting spec, and any L3 call with its token count.

```
POST /connectors                       { id: harborpms, vendor_docs_ref: simulators/docs/harbor.md, … }
POST /connectors/{id}/build            → BuildRun: DocsReader → classifiers → hypothesis enumerators
                                         → probe plan (Budget-capped) → evidence → spec → verify
GET  /runs/{build_run_id}              → ladder trace, probe observations, hypothesis decisions
GET  /runs/{build_run_id}/events       → SSE stream (redacted) of the same trace
GET  /metrics/model-usage?run_id=…     → per-call ledger; L3 calls show model id + input/output tokens (reasoning shown separately)
```

Renders in `/connectors/new` (docs upload → build two-step) and `/repairs/[id]`
(hypotheses + probes + patch diff + verifier verdict + model usage).
The Harbor quirks this exercises: wrong pagination example in the docs, opaque
`H1…H5` status codes, nested folios, ambiguous `date`.

## 3. Zero-token recovery — `page_to_cursor`

Live, every input comes from the engine: the docs build pins
`spec_vocabulary: v1`, the baseline sync is `Complete`, the faulted sync is
detected by the detector (wire signals only), the controller proposes the
`page_to_cursor` rule at L1 with 0 model calls, approval activates the
candidate, and the resync is `Complete`. Then the inject-again leg reactivates
the pre-patch release, injects the same fault, and the engine answers from
learned knowledge (L0, 0 model calls) — `/knowledge` shows `times_reused`
going up.

```
POST /connectors                       { name: atlaspms, base_url: http://sim-atlas:8000, … }
POST /connectors/{atlas_id}/build      { docs_text, spec_vocabulary: v1 } → release (page pagination)
POST /connectors/{atlas_id}/sync       → SyncRun Complete (baseline)
POST /demo/inject-fault                { target: sim-atlas, scenario_id: page_to_cursor, seed }
POST /connectors/{atlas_id}/sync       → DriftEvent PAGINATION_CHANGE (detector, wire signals only)
GET  /repairs?connector_id=…           → L0 miss → L1 rule page_to_cursor, frontier_tokens 0, small_tokens 0
POST /repairs/{id}/approve             → candidate release activated; resync Complete
POST /connectors/{id}/releases/{base}/activate + inject + sync
                                       → L0 knowledge hit (same rule, 0 tokens); approve; resync Complete
GET  /knowledge                        → page_to_cursor entry times_reused 1; atlaspms under vocabulary v1
GET  /metrics/model-usage?run_id=…     → frontier_tokens 0 (L0/L1 only)
```

Renders in `/repairs/[id]`: drift evidence, ladder badges L0→L1, old/new patch diff
side by side, visible tests, verifier verdict, Approve action. The ledger line —
`frontier_tokens == 0` — is the point of the step.

## 4. Hard semantic failure — `gross_to_net` (decide, then approve)

Deck 4 builds Harbor from its wiki docs; deck 5 breaks it right after.
The docs describe the page-level `meta.gross_sum` total, so the built
spec carries the `money_invariant` check and the baseline sync lands
`Complete`. The money drift then degrades the sync on the named
invariant, the engine's own climb runs (funnel → L2 skipped → exactly
one L3 attempt), and the run escalates with one operator question — plus
the one proposed change when the model offered it. The demo approves the
proposal as a business decision (release r2, verifying sync, Harbor back
to `working`); a plain escalation stays red with the question.

The L3 answer is the model's own words: `{"escalate": ...}` asks for a
business decision (its question becomes the escalation question,
labelled "The model's question for you:", with its rationale beneath;
the reason line reads "L3 called live (…): the model asked for a human
decision" and the verifier line reads "no patch to verify (the model
escalated)" — no fail verdict, because no patch was proposed), while
`{"escalate": ..., "proposal": {"label", "effect", "patch"}}` additionally
carries the change the operator could approve. A malformed proposal is
dropped and labelled — the escalation stands on the question alone. The
engine never synthesises a proposal; a proposal the gate refuses
fail-closes (422, the repair stays escalated). The `net_amount →
total_amount` decoy still belongs to the `invalid_patch_trap` catalogue
row: the verifier's trap list rejects it when the engine tries to apply
it on its own.

```
POST /connectors/{harbor_id}/build     { docs_text: simulators/docs/harborpms.md, spec_vocabulary: v1 }
                                       → release r1 with money_invariant vs $.gross_sum
                                         ("check added from the docs: totals must match meta.gross_sum")
POST /connectors/{harbor_id}/sync      → SyncRun Complete (deck 4 green)
POST /demo/inject-fault                { target: sim-harbor, scenario_id: gross_to_net, seed }
POST /connectors/{harbor_id}/sync      → SyncRun Degraded, unmet: [money_invariant],
                                         class SEMANTIC_VALUE_DRIFT, signal invariant_failure
                                       → sync returns at once (repair_pending,
                                         repair_job_id): the live climb runs
                                         in the background repair job, never
                                         in the request
                                       → engine climb L0 miss → L1 no rule → L2 skipped
                                         (one explanation) → L3 frontier attempt
                                         (live, replay, or explicit skip) → proposal
                                         apply-checked (one retry), never released
→ L4 escalate with EscalationBundle (one question + optional model proposal)
POST /repairs/{id}/approve-proposal    → release r2 from the proposal, verifier baseline
                                         gate, activate, approval recorded as a business
                                         decision (never learned); or
                                         422 when the gate refuses (fail-closed)
POST /connectors/{harbor_id}/sync      → verifying sync Complete → drift repaired →
                                         Harbor working (or stays in error on Deny)
```

The semantic repair arrives a few seconds after the break when the
frontier call is live: `POST /sync` records the sync run and the drift
and returns, and the repair row is created at once when the background
job starts (decision `climbing`) — the Repairs list shows it at the top
as `repairing` within one poll, and the repair page is reachable while
the climb runs (rungs so far, the current rung, and during a model call
the model id with elapsed seconds; no Approve/Reject until the climb
writes its decision). The row is filled in place when the job finishes;
a crashed or timed-out job leaves it `failed`, never stuck at
`repairing` (a restart marks orphaned running rows `failed`). The L3
output cap is `FRONTIER_MAX_TOKENS` (default 131072). With no key the
replay (or the explicit skip) runs inline, so the row is final at once.

Renders in `/repairs/[id]`: `Degraded` with the failing money invariant named,
the derivation funnel, the L3 model-call box (or the explicit skip with the
capture hint), the model's question with its rationale and the "no patch to
verify (the model escalated)" verifier line — or, when the model offered a
change, the question with the proposal (label, effect, diff) and the
Approve/Deny actions beside the escalation bundle and roll back. After
Approve the decision line reads "Business decision by \<operator\>" — never
a verified fix — and the business decision is not learned (the next
identical drift asks again). Money
stays `Decimal` throughout; the UI never shows a guessed mapping. Capture
the live prompt for replay with
`make record-frontier SCENARIO=gross_to_net` (keyed stack, after the break).
The L3 prompt hash changes with the prompt: after any change to what the
engine asks (REQ-044 well-posed prompt, REQ-045 prompts-in-files), the previous capture no longer
matches — the repair records an explicit skip naming the new hash — until
the owner re-runs the capture and commits `demo/recordings/gross_to_net.json`.
To improve a prompt, edit `config/prompts/<id>.md` and restart the engine
(prompts load once at boot; the repair page shows each call's prompt
id/version next to it).

## 5. Head-to-head — `/bench/scorecards`

```
GET /bench/scorecards                  → wins and losses as they are: verified completion,
                                         repair success, false-fix/unsafe rate, escalation quality,
                                         elapsed, tool/model calls, frontier tokens, cost
```

Renders in the UI wherever scorecards surface, and in `experiments/scorecard.md`
(written by `make bench`). Read the numbers as they are — wins and losses.

## 6. Reproducibility — seed, release ids, `make demo`

Every step above is reproducible:

* Scenarios come from `simulators/scenarios/*.yaml` with fixed `seed`s; the same seed
  yields the byte-identical dataset (seeded generator + fault switchboard).
* Releases are immutable and content-addressed (`spec_signature` sha256 of the
  canonical JSON); `generated/connectors/<id>/<release>/` holds `spec.yaml` +
  `build-report.json` (connector id, release id, signature, `built_by`).
 * `make demo` replays steps 1–8 against the same seeds and prints the release ids it
   created, so a reviewer can re-fetch any spec, sync, or verifier verdict.

```
make demo    # eight steps via the control API; output is the same evidence the UI renders
## 8. On-premise PABX — built live, the link drops and resyncs with nothing lost (REQ-022, REQ-050)

The FIAS PABX docs (`simulators/docs/fias-pabx.md`, framed interface over
TCP: STX/ETX framing, link handshake, PS postings answered with PA) build
a connector live from the build page with `spec_vocabulary: v3` and no
runtime choice — the engine runs framed docs on the on-premise edge
agent, and the result says "runs: on-premise edge agent, via the relay".
The edge agent connects, postings flow through the relay outbox, the
connector syncs `Complete`, money stays `Decimal` throughout, and a
redelivered durable outbox carries identical idempotency keys, so each
charge lands once — 0 lost, 0 duplicated.

```
Build a connector → FIAS PABX docs → vocabulary v3 → Build
  → release r1 · runs: on-premise edge agent, via the relay
  → edge agent connects → postings flow → ● Working
make demo-break SCENARIO=fias_link_resync   (on the live-built connector)
GET  /edge/agents                            → link down, then back up (LS/LD/LR/LA re-handshake)
GET  /edge/postings                          → every pre-break key still logged exactly once (0 lost, 0 duplicated)
GET  /connectors/{id}                        → ● Working; GET /drift, /repairs → nothing (no rung ran)
```

Renders in the PABX panel on the connector page (link state, postings
arriving, record layout) and in `/knowledge` (`PABX edge` under v3).
Live, step 8 ensures the connector, builds the committed FIAS docs with
`spec_vocabulary: v3`, asserts the engine's edge runtime on the record,
baseline-syncs `Complete`, then drops and resyncs the link and asserts
the counts. The firmware layout change (`fias_record_layout_change`)
and the agent restart (`agent_restart_inflight`) stay in the catalogue
but are not part of the demo script (layout-change live repair is
REQ-051, deferred).
```

## 7. New API style — Meridian refuses safely, then connects (REQ-020)

The same Meridian docs (`simulators/docs/meridian-openapi.yaml`, Mews-style:
every operation is `POST` with a JSON body, auth is `ClientToken` /
`AccessToken` in the body, pages travel in a `Limitation: {Count, Cursor}`
envelope) run through two vocabulary pins back to back:

```
# BEFORE — vocabulary v1 (the engine as it was)
build Meridian docs, spec_vocabulary: v1 → BuildRun Blocked:
  "operations need POST with a JSON body and body-field auth;
   not expressible in ConnectorSpec v1"
  (vocabulary_guard trace stage; no draft spec, no release, 0 model calls)

# AFTER — vocabulary v2 (the extended vocabulary, same docs, no other input)
build Meridian docs, spec_vocabulary: v2 → spec_version 2
  (POST operations, body_fields auth, body_cursor pagination, joins)
POST /connectors/{meridian_id}/sync → SyncRun outcome Complete:
  guest names resolve through the customer join, reservation totals equal
  the summed order-item gross, money Decimal throughout
GET  /metrics/model-usage?run_id=…     → frontier_tokens 0, small_tokens 0, calls 0 (both halves)
```

Renders wherever builds surface, with the vocabulary each half used (`v1` /
`v2`) beside the evidence. The refusal is deterministic — detecting "needs
POST with body auth" costs nothing — so the demo never pretends the v2
vocabulary does not exist: the before half stays repeatable forever after v2
ships (REQ-021 item 2). Live, step 7 drives both halves through the control
API: the v1 vocabulary check refuses before any connector exists (recorded
by name), then the v2 build creates the one `meridianpms` connector and
becomes the release in use, so `/knowledge` lists it under v2 with v1
refused. The build page runs the same check first, so a refused build
never leaves an empty connector behind (REQ-053 item 7).

## Fault-scenario catalogue (REQ-023)

Backup and Q&A: every entry runs on its own after `make demo-reset` with
`make demo SCENARIO=<name>`, and every main-script step runs on its own
with `make demo STEP=<n|name>` (e.g. `make demo STEP=4`,
`make demo STEP=hard_semantic_failure`). `make demo` runs the full main
script for rehearsal. Each run prints REQ-021's per-run table
(scenario | rung that resolved it | model calls | small / frontier tokens |
why it escalated). Never show heldout seeds; demo the dev variants only.
A step enters this page and `make demo` only when the feature behind it
has merged and its e2e test is green.

| scenario | vendor | expected outcome | rung | model calls | REQ |
|---|---|---|---|---|---|
| `rate_limit_429` | both | Backoff only, no patch | L1 | 0 | REQ-023 |
| `transient_503` | both | Backoff only, no patch | L1 | 0 | REQ-023 |
| `cloud_slow` | fias | Link keeps ACKing, outbox drains later | n/a (agent) | 0 | REQ-022/023 |
| `token_expired` | both | Refresh if permitted, else escalate (Blocked) | L0 refresh / L4 escalate | 0 | REQ-023 |
| `token_revoked` | both | Escalate, no patch, Blocked | L4 | 0 | REQ-023 |
| `endpoint_removed` | both | Escalate, never patched around | L4 | 0 | REQ-023 |
| `rename_guest_id` | both | L1 rename_field patch, verified | L1 | 0 | REQ-023 |
| `nested_path_shift` | both | L1 nested_path_shift patch, verified | L1 | 0 | REQ-023 |
| `offset_to_cursor` | both | L1 rule patch, verified | L1 | 0 | REQ-023 |
| `page_to_cursor` | both | L1 rule patch, verified; reusable via L0 (step 3) | L1 | 0 | REQ-019/021 |
| `cursor_repeat_token` | both | L1 rule + repeated_cursor loop guard | L1 | 0 | REQ-023 |
| `fias_record_layout_change` | fias | Handshake detect + L1 mapping patch (catalogue only; live repair REQ-051 deferred) | L1 | 0 | REQ-022 |
| `gross_to_net` | both | Deck 4 builds Harbor from its wiki (money check from the docs), deck 5 breaks it → full climb L0→L4; the model asks and optionally proposes (step 4 approve → business decision, deny → error) | L4 (via L0,L1,L2,L3) | 1 (replay: frontier; L2 skipped, one surviving reading) | REQ-021/023 |
| `status_vocab_change` | both | Escalate, never guess a mapping | L4 | 0 | REQ-023 |
| `tz_boundary_shift` | both | Degraded + operator question | L4 | 0 | REQ-023 |
| `duplicate_late_records` | both | Escalate (no dedup rule invented) | L4 | 0 | REQ-023 |
| `invalid_patch_trap` | both | L3 decoy proposal rejected → L4 (step 4) | L4 (via L0,L1,L2,L3) | 2 (replay: 1 small + 1 frontier) | REQ-021/023 |
| `meridian_cursor_renamed` | meridian | L1 cursor path patch, verified | L1 | 0 | REQ-020 |
| `meridian_state_renamed` | meridian | L1 rename_field patch, verified | L1 | 0 | REQ-020 |
| `fias_link_resync` | fias | Re-handshake, 0 lost (deck 7 live break, step 8) | n/a (agent) | 0 | REQ-022 |
| `fias_ack_timeout` | fias | Timeout reported, link survives | n/a (agent) | 0 | REQ-022 |
| `agent_restart_inflight` | fias | 0 lost, 0 duplicated (catalogue only) | n/a (agent) | 0 | REQ-022 |
| `fias_duplicate_records` | fias | Duplicates land once (idempotency) | n/a (agent) | 0 | REQ-022 |
| `fias_oversize_frame` | fias | Session closes and redials | n/a (agent) | 0 | REQ-022 |
| `fias_record_flood` | fias | Session closes and redials | n/a (agent) | 0 | REQ-022 |
| `session_lost_with_command` | fias | Command retried or reported, never dropped | n/a (agent) | 0 | REQ-022 |
| `stale_command` | fias | Stale command never sent | n/a (agent) | 0 | REQ-022 |
| *(safety)* Meridian v1 refusal | meridian | v1 Blocked, named reason (step 7) | — (deterministic) | 0 | REQ-020/021 |
| *(safety)* FIAS v1/v2 refusal | fias | v1/v2 Blocked, named reason | — (deterministic) | 0 | REQ-022 |
| *(safety)* Atlas money as JSON numbers | both | Decimal throughout, no float drift (step 1) | — (invariant) | 0 | DIRECTIVES §4 |
| *(safety)* inject-fault without admin token | — | Rejected | — | 0 | REQ-015 |
| *(safety)* UI writes without control token | — | 401 | — | 0 | REQ-012 |
| *(safety)* engine isolated from verifier/sim-admin | — | No route (smoke) | — | 0 | DIRECTIVES §2 |

Source of truth for this table is `CATALOGUE` in `bench/demo.py`
(scenario | vendor | expected outcome | rung | model calls | REQ);
`tests/unit/test_autobridge_demo_catalogue.py` keeps them in sync and
checks every entry against its scenario file, its e2e test and
(REQ-021's matrix for ladder entries) its model-usage matrix row.
