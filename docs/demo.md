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
`make demo-reset && make demo-seed`, and wait until every connector shows
● Working with "last sync N s ago" ticking. Every state badge is colour +
shape + word, never colour alone; the Repairs list polls every 2 s, so new
repairs appear at the top without a reload. The presenter clicks every
approval — nothing auto-applies, so every transition stays on screen.

- **What AutoBridge knows.** Point at `/knowledge` (top bar): vocabulary
  v1, v2, v3 rows each list "used by" their seed connector (atlaspms v1
  with no refusal, meridianpms v2 with v1 refused, PABX edge v3), and the
  right-hand column fills with learned fixes as repairs are approved
  ("learned from approval … by …", "reused N×").
- **Reusable artifact.** On `/connectors`, AtlasPMS ● Working with
  0 model tokens: the reference release from step 1 below, healed
  through the ladder, not rebuilt.
- **Unfamiliar API — build Harbor from its docs in the UI.** On
  `/connectors/new` pick the Harbor docs (the picker lists the simulator
  docs from `config/demo_targets.yaml`; the server fetches the text, the
  browser only sends `docs_text` back, so the AR-2-2 SSRF guard stays
  shut). Watch the five-stage progress — docs read → draft → probes →
  result → release — the same stages as step 2 below, then the new
  connector's page with its spec, releases and sync runs.
- **Zero-token fix.** Left: `make demo-break SCENARIO=page_to_cursor`.
  Right, on `/connectors`: AtlasPMS turns ▲ Degrading ("drift detected
  … · repairing at L1"). On `/repairs`: the new row reads connector
  AtlasPMS, class `PAGINATION_CHANGE`, rung L1, rule `page_to_cursor`,
  "0 model calls", status "fix ready". Open it and scroll the four
  sections in order: **What happened** (connector + detecting sync with
  its outcome and unmet criterion, the wire before/after
  "page → cursor", affected entities/records, redacted samples);
  **What the engine did** (rung timeline "L0: no known fix …" →
  "L1: rule page_to_cursor matched", the hypothesis funnel
  "24 explanations → … → 1 distinct" with expandable stages, probes,
  the model-call box saying "0 model calls", the verifier run);
  **What the engine decided** (the "Fix ready: verified, 0 model calls"
  line, the JSON-Patch diff against the current spec with the raw patch
  one click away); **What happens when you click Approve** (the server's
  own preview: "creates release r2 from r1 … activates r2 … this fix is
  kept as known … rollback to r1 stays available"). Click **Approve** at
  the top: the page follows approval → release → verifying sync →
  Complete, and the connector returns to ● Working. Break it again:
  the same drift resolves at L0 and the Knowledge entry reads "reused 1×".
- **Hard semantic failure.** Left: `make demo-break
  SCENARIO=gross_to_net TARGET=harbor`. Right: the syncs stay Complete —
  the docs-built Harbor spec carries no money invariant, so the semantic
  drift cannot surface live (this is the labelled gap from step 4; the
  step asserts it instead of pretending). After `make demo` has run once,
  open the recorded step-4 repair (`SEMANTIC_VALUE_DRIFT`, decision escalate): the funnel with
  its surviving "net" reading, the model-call box with the single
  frontier call — live ("muse-spark-1.3, N tokens") with
  `FRONTIER_API_KEY` set, otherwise the labelled replay ("replayed from
  a live Muse capture on …") — the rejected `net_amount → total_amount`
  decoy with the trap verdict, and the operator question
  ("map `total_amount` to …" / "add a canonical field"). Point at the
  decision line: "Needs your decision: the engine is not confident".
- **New kind of API.** Meridian's connector page shows the refused v1
  build with its reason and v2 in use (step 7). Break
  `meridian_cursor_renamed`, watch the L1 fix land, **Approve**, Working.
- **On-premise PABX.** The PABX edge connector page carries the PABX
  panel: link state, postings arriving (count rising, last posting
  time), current record layout (v3, announced in the handshake), and the
  last restart result. Break `fias_record_layout_change`: the link drops
  and the agent re-handshakes on the new shape (TM renamed, CX added —
  point at the layout line), postings resume, the connector stays
  Working. No repair lands and none is needed: the edge agent absorbs
  the firmware change (catalogue kind "agent"); the L1 `rename_field`
  (TA → TM) patch is proven in-process by step 8, which also records
  the restart line. Break `agent_restart_inflight`, then compare with
  the recorded row: "lost 0 · duplicated 0".

Retakes are `make demo-heal` (clears faults, keeps connectors) and
`make demo-reset && make demo-seed` (the opening frame). The terminal
`make demo` approves the same repairs through the API; everything it
does lands in the database exactly like the clicks do (REQ-030), so the
repair page, the Knowledge page and this recording agree.
`AUTOBRIDGE_SYNC_INTERVAL_S` sets the scheduler tick (default 10 s);
`FRONTIER_API_KEY` selects live vs replay for the semantic climb.

## 1. Reusable artifact — Atlas release, zero inference

Open the Atlas reference release spec, run a sync, show `ModelUsageLedger = 0`.

```
POST /connectors                       { id: atlaspms, vendor_docs_ref, base_url: ${ATLAS_BASE_URL},
                                         credential_ref: ATLAS_API_KEY, target_entities: [Reservation, FolioCharge] }
POST /connectors/{id}/build            → release `reference` (spec.yaml + build-report.json + sha256 signature)
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

## 4. Hard semantic failure — `gross_to_net`

Inject the money drift; the sync degrades on a named invariant, the plausible-but-wrong
patch is rejected, and the run escalates or lands the correct fix.

```
POST /demo/inject-fault                { target: sim-harbor, scenario_id: gross_to_net, seed }
POST /connectors/{harbor_id}/sync      → SyncRun Degraded, unmet: [money_invariant],
                                         class SEMANTIC_VALUE_DRIFT, signal invariant_failure
POST /verify { spec: net→total_amount } → verdict fail, failed_checks [{invalid_patch_trap}]
                                         (the trap: mapping decoy `net_amount` to `total_amount`)
→ ladder reaches L3 or escalates with EscalationBundle (one question + evidence ids);
  approve the correct patch, or show the bundle in /repairs/[id]
```

Renders in `/repairs/[id]`: `Degraded` with the failing money invariant named,
`invalid_patch_trap` rejection wording, escalation bundle side-by-side with Approve /
Reject. Money stays `Decimal` throughout; the UI never shows a guessed mapping.

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

## 8. On-premise PABX — the agent keeps working through a firmware change (REQ-022)

Postings flow from the FIAS PABX through the on-premise edge agent; a firmware
update announces a new `PS` layout (`TA` renamed to `TM`, `CX` added, fields
reordered). The new shape does not fail the run — it corrupts it silently (the
amount path reads nothing) — so the drift is detected from the handshake `LR`
announcement, not from the sync outcome. Detection plus the `rename_field` L1
rule propose the mapping patch through the ladder (L0 miss → L1, 0 model calls);
the repaired mapping syncs the new shape `Complete` with `Decimal` amounts. A
restart moment replays the same durable outbox: identical idempotency keys, so
each charge lands once — 0 lost, 0 duplicated.

```
POST /demo/inject-fault                { target: sim-fias-pabx, scenario_id: fias_record_layout_change, seed }
GET  /demo/simulators                   → FIAS PABX row shows the active fault; the other rows stay green
POST /repairs (auto) or ladder         → L0 miss → L1 rename_field → JSON Patch (amount path $.TA → $.TM)
POST /verify { spec, scenario_id }     → verdict pass (visible), regression pass
GET  /metrics/model-usage?run_id=…     → frontier_tokens 0 (L0/L1 only); the per-run table reads
                                         "resolved L1 | 0 calls | 0 small / 0 frontier"
```

Renders in the simulators index (live up/down, active fault, connectors using
the PABX) and in `/repairs/[id]` (handshake evidence, ladder badges L0→L1, old/
new mapping diff, verifier verdict). Money stays `Decimal` throughout; the agent
ACKs the PABX after a durable local write, so the link never stalls on the cloud.
Live, step 8 also builds the committed FIAS docs through the control API with
`spec_vocabulary: v3`, so `/knowledge` lists the `PABX edge` connector under v3.
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
API on one `meridianpms` connector (the v1 attempt is a refused build, the v2
build becomes the release in use), so `/knowledge` lists it under v2 with v1
refused.

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
| `fias_record_layout_change` | fias | Handshake detect + L1 mapping patch (step 8) | L1 | 0 | REQ-022 |
| `gross_to_net` | both | Degraded → full climb L0→L4; L3 rejected (step 4) | L4 (via L0,L1,L2,L3) | 1 (replay: frontier; L2 skipped, one surviving reading) | REQ-021/023 |
| `status_vocab_change` | both | Escalate, never guess a mapping | L4 | 0 | REQ-023 |
| `tz_boundary_shift` | both | Degraded + operator question | L4 | 0 | REQ-023 |
| `duplicate_late_records` | both | Escalate (no dedup rule invented) | L4 | 0 | REQ-023 |
| `invalid_patch_trap` | both | L3 decoy proposal rejected → L4 (step 4) | L4 (via L0,L1,L2,L3) | 2 (replay: 1 small + 1 frontier) | REQ-021/023 |
| `meridian_cursor_renamed` | meridian | L1 cursor path patch, verified | L1 | 0 | REQ-020 |
| `meridian_state_renamed` | meridian | L1 rename_field patch, verified | L1 | 0 | REQ-020 |
| `fias_link_resync` | fias | Re-handshake, 0 lost | n/a (agent) | 0 | REQ-022 |
| `fias_ack_timeout` | fias | Timeout reported, link survives | n/a (agent) | 0 | REQ-022 |
| `agent_restart_inflight` | fias | 0 lost, 0 duplicated (step 8) | n/a (agent) | 0 | REQ-022 |
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
