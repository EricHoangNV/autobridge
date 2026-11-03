# Requests

## Open Requests (read this section only)

Owner priority (2026-11-03 12:05): **REQ-091** (Architecture Review cycle 7 pre-freeze re-verification, AR-7-9 Critical; REQ-066..089 done; the real beacon freeze waits for this one). One REQ per iteration. REQ-090 (AR-7-3..8) is DEFERRED until after the hackathon: do not start it. REQ-082 is DEFERRED until after the hackathon: do not start it. Do not start REQ-051 until the owner says so.

### REQ-091 — AR-7-9: the `frontier_agent` arm can still write inside its sandbox, and the harness reloads that sandbox and the operator's global OpenCode config as host code (2026-11-03)
Status: DONE (sbx1 2026-11-03 -- all 6 items code-done: default-deny permission config, pristine per-run CLI home, re-prompt in a fresh sandbox copy, preflight proofs, updated + 2 new tests, benchmark docs; full gates green; owner step: keyed rehearsal, then close)
Architecture Review cycle 7 pre-freeze re-verification (`mds/FEEDBACKS.MD`, commit 2df99922, AR-7-9, Critical):
REQ-088 denies `bash`/`webfetch`/`websearch`/`task`/`skill`/`lsp`/`external_directory`, but
`AGENT_PERMISSION_CONFIG` (`bench/baseline_coding_agent.py` ~225-237) still allows `edit` and sets no `"*": "deny"`
baseline, so the agent may write any file inside its sandbox dir, including `.opencode/` plugins or tools and a
changed `opencode.json`. OpenCode loads project plugins from the working directory at startup as host code, and the
REQ-081 item 7 no-text re-prompt (~1181-1211) starts a second CLI in that same, possibly agent-modified, directory.
The child env also passes the operator's real `HOME` plus every `OPENCODE_*`/`XDG_*` variable (~244-250), so the
operator's global OpenCode config, plugins, skills and MCP servers load into the scored arm as well. The agent's
contract needs none of this: it answers inline from the evidence bundle ("Respond with ONLY one JSON object",
`bench/context.py` ~52) and never writes. One root cause (the arm may write, and what it or the operator wrote is
loaded), small scope; the ablation `frontier_agent` arm shares the driver and is covered by the same change:
1. Default-deny permissions: `AGENT_PERMISSION_CONFIG` becomes `"*": "deny"` with only `read`, `glob` and `grep`
   allowed; `edit` is denied. Same single source of truth for the sandbox `opencode.json` and `OPENCODE_PERMISSION`.
2. Pristine CLI home: the child env pins `HOME` and `XDG_CONFIG_HOME`/`XDG_DATA_HOME`/`XDG_CACHE_HOME`/
   `XDG_STATE_HOME` to a fresh empty per-run directory under the harness's temp area, so no operator-global
   OpenCode config, plugin, skill or MCP server loads. The open `OPENCODE_*` and `XDG_*` prefix pass-through goes
   away; only an explicit, documented list of names crosses (keep `agent_child_env_allowed_names` as the one
   definition). The pinned model's provider key variable(s) still cross, as in REQ-088. If the pinned provider needs
   a non-secret provider block to resolve, write it into the sandbox `opencode.json` from code, never copied from
   the operator's config.
3. Never reload a dirtied sandbox: the no-text re-prompt (and any other second CLI invocation on the same case) runs
   in a freshly written copy of the original sandbox files, not in the directory the first invocation ran in. The
   session continuation (`--session <id>`) keeps working from the pristine home of item 2.
4. Preflight (`bench/beacon_run.py` `preflight_beacon`, next to the REQ-088 proofs ~2586-2638): assert the written
   permission file has `"*": "deny"` and `edit` denied, and the child env has no `OPENCODE_*`/`XDG_*` name outside
   the explicit list and a `HOME` under the harness temp area (names and paths only, never values). Fail loud before
   the first scored instance; rehearsals included. No live model call is added to preflight in this REQ.
5. Tests: `tests/unit/test_autobridge_req081.py` ~229-320 asserts the REQ-088 config strings; update them for the
   default-deny config, the pinned home (a parent env with `HOME`, `XDG_CONFIG_HOME` and an `OPENCODE_CONFIG` set
   yields a child whose `HOME` is the per-run dir and which carries none of the operator values), and the re-prompt
   running in a fresh copy (a file planted in the first sandbox dir is absent from the directory the re-prompt
   receives). The fake-agent path still parses the inline JSON answer.
6. `docs/benchmark.md`: state the arm's confinement as it is after this change (read-only file tools inside the
   sandbox dir, pristine CLI home, re-prompt in a fresh copy), replacing the REQ-088 wording.
Then mark DONE after full gates pass; the owner runs the keyed rehearsal and checks the `frontier_agent` arm still
answers. Do not freeze, tag or commit under `experiments/beacon/`.
Resolution: default-deny `AGENT_PERMISSION_CONFIG` (`"*": "deny"`, only read/glob/grep allowed, edit denied — single source of truth for the sandbox `opencode.json` and `OPENCODE_PERMISSION`); child env pins `HOME` + the four `XDG_*` base dirs to a fresh empty per-run CLI home (no open `OPENCODE_*`/`XDG_*` pass-through; only explicit names in `agent_child_env_allowed_names`, provider keys still cross); every CLI invocation runs under the pristine home and the no-text re-prompt runs in a freshly written copy of the original sandbox files (same home, so `--session` continues); session rows resolve from the pristine home first; `claude`/`codex` drivers get the same pristine home; preflight asserts the permission file and the env; `docs/benchmark.md` states the new confinement.


### REQ-090 — Architecture Review cycle 7, remaining findings AR-7-3..AR-7-8 (2026-11-03)
Status: DEFERRED (owner 2026-11-03: after the hackathon; the review's blockers are REQ-088 and REQ-089)
Six non-blocking findings from `mds/FEEDBACKS.MD` cycle 7, kept together so none is lost. Each is one root cause;
when this REQ opens, take them one item per iteration in this order.
1. AR-7-3 (Medium, playbook §6 known gap): unrefreshable `AUTH_EXPIRED` climbs to the frontier model
   (`src/autobridge/repair/controller.py` ~284, ~470-531; `ladder.py` ~1504-1535). Treat a deterministic
   `escalate` decision from the auth probe as terminal before the model gate; refreshable stays a zero-token
   refresh. Test: unrefreshable expiry with a wired `FakeProvider` ends `escalate` with zero `ModelCall` rows.
   DIRECTIVES §3's "no class list gates the model" gets a one-line carve-out naming this.
2. AR-7-4 (Medium): vendor literals in engine discovery (`discovery/hypotheses.py` ~58 `_HARBOR_OPAQUE_MAP` and
   ~762/772; `discovery/bootstrap.py` ~84-87 and ~147-150; `discovery/classifiers.py` ~713). Move the opaque map
   and the URL/credential special cases to config keyed by the vendor table, or derive the opaque hypothesis as
   "unknown, probe must map"; extend the no-vendor-vocabulary guard to fail on vendor literals under `discovery/`.
   Test: a synthetic opaque-code vendor whose codes differ from Harbor's gets no Harbor mapping proposed.
3. AR-7-5 (Medium): no aggregate model-spend ceiling; every decided repair buys a fresh per-climb budget
   (`engine/detect_repair.py` ~1418, `controller.py` ~1832/2361/2433). Add a persisted per-connector rolling
   budget checked before L2/L3, escalating when exhausted, surfaced in the model-usage UI. Test: N decided cycles
   against `FakeProvider`, the (N+1)-th escalates without a `ModelCall`.
4. AR-7-6 (Medium, playbook §6 known gap): live silent-corruption baseline is money-only
   (`drift/value_baseline.py`). Persist a per-release date sketch beside the money sketch and emit a date-value
   signal on shift. Test: simulator scenario serving cutoff-crossing instants under an unchanged schema raises a
   drift event instead of `Complete`.
5. AR-7-7 (Low): verifier `failed_checks[].details` carries up to five fixture record IDs into the engine zone
   (`verifier/app.py` ~184-197, ~252-267; `verifier/checks.py` ~67). Replace `details` with bounded enum reasons
   in `VerifyResponse`/`BeaconRecordsResponse` and make the leak guard fail on any marker. Test: no `RES-`/`CHG-`
   substring in any verifier response mode.
6. AR-7-8 (Low): 2-second list polling across the dashboard (35 `refreshInterval` sites under `ui/`, control
   repair-drain poll `control/app.py` ~184-187). Raise list polls to 10-30 s, lean on the existing SSE run events,
   pause on hidden tabs. Validation: requests per minute with an idle dashboard, before and after.
Do not start until the owner reopens it.

### REQ-087 — The dry-run gate judges checks without the spec's declared zone and refuses correct date repairs (2026-11-03)
Status: DONE (sbx1 2026-11-02 -- all 2 items code-done: shared spec_static_tz helper in runtime/checks.py used by sync_run + dryrun, context_tz forwarded in old-check + patched passes; 4 new tests, full gates green; owner step: keyed rehearsal, then close)
Keyed rehearsal on `27a6858e` (round 6603643, preflight ok for 5 vendors), instance 1, harbor `dates`
`{mode: format, date_format: "%m-%d-%Y"}`, expected repair: all three engine arms' L3 proposed `input_format:
"%m-%d-%Y"` on the three date transforms (the same effect as the generator's reference patch), the dry-run gate
refused it twice with `check(s) still fail on the current records: FolioCharge.business_date_invariant`, and the
arms ended with the fallback question. Reproduced in-process on the same instance: the model's patch and the
reference patch both sync `Complete` with no unmet criteria through `beacon_sync_pair`, and both pass
`dry_run_proposal` as soon as `evaluate_entity_checks` receives `context_tz="America/New_York"` (the spec's static
`timezone_source`). Mechanism: the sync evaluates checks with `context_tz=_spec_static_tz(spec)`
(`src/autobridge/engine/sync_run.py` ~38-54 and ~366-372, REQ-083 A1), but the dry-run's
`_evaluate_checks_on_records` (`src/autobridge/repair/dryrun.py` ~441-481) never passes it, so the business-date
invariant is derived under the legacy per-property map and UTC only. Before the patch the faulted `business_date`
(`10-16-2026`) does not parse and the check skips the record, so no entity fails the old-check pass; after the patch
it parses and fails for every record whose property sits outside the legacy zones. The gate therefore blocks exactly
the repairs it should pass, in both the old-spec and the patched-spec passes. One root cause, small scope; no change
to the checks themselves, the vocabulary or the prompts:
1. One helper for the spec's static declared zone, importable by both the sync and the dry-run without a
   `repair` → `engine` import (for example next to `evaluate_entity_checks` in `src/autobridge/runtime/checks.py`);
   `sync_run` and `dryrun` both use it. `_evaluate_checks_on_records` passes `context_tz` through to
   `evaluate_entity_checks` so the old-check pass and the patched-spec pass judge under the same zone as the sync.
2. Tests (`tests/unit/test_autobridge_req053_dryrun.py` or a sibling): a spec with a static `timezone_source` whose
   records derive business dates under that zone but not under UTC or the legacy map — the dry-run passes a correct
   `input_format` date repair and still refuses a patch that leaves a genuinely wrong business date; the old-check
   pass agrees with `evaluate_entity_checks` called with the same zone.
Then mark DONE after full gates pass; the owner runs the keyed rehearsal. Do not freeze, tag or commit under
`experiments/beacon/`.
Resolution: one shared `spec_static_tz` helper next to `evaluate_entity_checks`; `sync_run` and `dryrun` both use it; `_evaluate_checks_on_records` forwards `context_tz` so both passes judge under the spec's declared zone.

### REQ-084 — Meridian nesting is unreachable by any patch (customers endpoint ignores nesting); preflight proves the drawn repairs; L3 traces kept (2026-11-02)
Status: DONE (sbx1 2026-11-02 -- all 3 items code-done: meridian customers unwrap + derived-endpoint audit, preflight proves every drawn repair, per-instance redacted L3 traces; 12 new tests, full gates green; owner step: keyed rehearsal after REQ-085)
Keyed rehearsal on `0f079c17` (round 6603643, instance 3, meridian `nesting` `{entity: reservation, parents: [stay]}`,
expected repair): every arm fails `field_missing`; the generator's own reference repair fails the same way
(`field_missing` 27 = the non-null guest names). Reproduced in-process. Three small items, harness and simulator
only; no engine change.
1. `simulators/meridianpms/app.py` `customers_get_all` (~637-645) reads `reservation_id` / `guest_name` /
   `updated_at` from the internal dataset record without `_split_nesting`, unlike `to_wire_reservation` (~424) and
   `to_wire_order_item` (~525). Under nesting it serves `Id: "ACC-None"` and drops the names, and no spec patch can
   repoint a join to data that is not served. Unwrap with `_split_nesting(r)` first, as the other two do. Check every
   other derived endpoint in every simulator for the same omission (any place that reads an internal record field
   after a nesting fault must unwrap). Test: the reference nesting repair for a meridian reservation nesting passes
   the judge in-process.
2. The REQ-083 A2 preflight (`bench/beacon_run.py` `preflight_beacon` ~2106-2125) proves only that the unfaulted
   baseline passes the judge; `reference_patch_for` (`bench/reference_patch.py`) is called only from a unit test on
   another seed and entity, so a meridian reservation nesting was never proven. Extend the preflight: for every
   drawn instance whose key is `repair`, build `reference_patch_for`, push it through the real `sync_candidate` and
   the judge, and fail loudly with the instance, the patch and the failed checks when it does not pass. Rehearsals
   included.
3. The engine arm's L3 request and response are not persisted (in-memory sqlite session, `bench/ablation.py`
   ~1180), so a live failure that in-process reproduction cannot explain (instance 1, harbor dates: engine arms
   failed `business_date_mismatch` + `record_set_mismatch`, which no transform value reproduces offline) cannot be
   traced. Write each arm's model request and response per instance, redacted the same way as evidence, under the
   rehearsal/round directory next to `grades.jsonl`. Secrets never: key names only.
Then mark DONE after full gates pass; the owner runs the keyed rehearsal after REQ-085. Do not freeze, tag or commit
under `experiments/beacon/`.

### REQ-086 — `semantic/gross_net` is keyed `repair` but no patch in the closed vocabulary expresses it: key it as a question (2026-11-03)
Status: DONE (sbx1 2026-11-02 -- all 4 items code-done: gross_net -> degraded_question in expected_for + GeneratorBug inverse guard, verifier accepted_outcomes + scaling-manifest proof, reference_patch/protocol text, fault-playbook + benchmark docs; 3 new test files + updated gross_net/decoy tests + 3 margin tests; full gates green; owner step: keyed rehearsal, then close)
Keyed rehearsal on `fed86554` (round 6603643) stopped at the REQ-084 preflight, as designed: "no reference patch for
repair instance 7 (legacystay/semantic {kind: gross_net, factor: 0.25}) — unreachable by any patch". The mutation
scales `total_amount`/`amount` in place by the factor and leaves no gross source on the wire
(`simulators/common/faultgen.py` ~803-813); the `money` transform has `scale` (decimal places) and `minor_units`
(exact /100) only (`src/autobridge/runtime/transforms/money.py` ~46-50), so "multiply by 1/factor" is not a spec
patch. Multiplying money by a factor is a business meaning the operator decides; the engine's correct end state is
one question, which is what every engine arm did on `0f079c17`. Do not add a factor argument to `money`. One root
cause, small scope:
1. `expected_for` (`faultgen.py` ~536-537): `semantic` with `kind == "gross_net"` is `degraded_question`;
   `minor_major` stays `repair` (its reference repair `minor_units: true` is expressible and proven by the
   preflight). `accepted_outcomes_for` (`verifier/beacon.py` ~98-113) for `gross_net` accepts `degraded_question`
   only. The decoy branch is unchanged. Update the generator's recoverability proof (~1216-1218) so it no longer
   asserts a Python inverse for `gross_net`.
2. `bench/reference_patch.py` `_semantic_patch`: docstring and behaviour agree with item 1 (returns None for
   `gross_net`; the preflight never asks for it because the key is no longer `repair`).
3. Docs: `docs/fault-playbook.md` section 4 row `semantic gross_net / minor_major` splits into `minor_major` →
   repair and `gross_net` → degraded_question (one question; a guessed factor is a wrong patch); section 2's
   `SEMANTIC_VALUE_DRIFT` row and `docs/benchmark.md`'s family table say the same.
4. Tests: `expected_for` and `accepted_outcomes_for` for all three semantic kinds; the preflight over a round that
   draws a `gross_net` instance passes without a reference patch; the existing grading tests for a question on a
   `gross_net` instance still pass.
Then mark DONE after full gates pass; the owner runs the keyed rehearsal. Do not freeze, tag or commit under
`experiments/beacon/`.

### REQ-085 — The engine's model can add a transform argument or repoint a join: evidence shows transforms, pointers allow it (2026-11-02)
Status: DONE (sbx1 2026-11-02 -- L3 evidence renders transform steps + joins; add-arg and join-repoint pass the pointer gate; 5 new tests, full gates green; owner step: keyed rehearsal, then close)
Keyed rehearsal on `0f079c17` (round 6603643, instance 1, harbor `dates/format` `%m-%d-%Y`, expected repair): both
frontier arms passed with `add /entities/<E>/fields/<f>/transform/0/args/input_format`; all three engine arms, on
the same model, replaced the whole transform and failed. Not variance: the engine's model is not shown, and not
allowed, the patch the frontier made. General engine evidence; no vocabulary, no fault knowledge.
1. `build_generic_l3_evidence` (`src/autobridge/repair/controller.py` ~1220-1240) renders each field as a bare path
   string and never its current `transform` list, so the model cannot see `business_date` with `cutoff`/`tz` and add
   one argument; it must invent the whole list. Render each field's transform steps (name and args) next to its
   path(s), for every field in the subtree.
2. The allowed L3 pointers (`controller.py` ~1255-1256, checked by exact membership in `repair/ladder.py`
   ~2029-2031) are only `/fields/F/path`, `/paths`, `/paths/N` and `/fields/F/transform`. Add, for each existing
   step `i` of a field's transform, `/fields/F/transform/i/args` and `/fields/F/transform/i/args/<arg>` for the
   arguments the transform declares (`TRANSFORM_ARGS`), so an add-arg or replace-arg patch passes
   `_l3_pointer_error`. Add the entity join pointers (`/entities/E/joins/i/local_key`, `/joins/i/foreign_key`,
   `/joins/i/fields/F/path`) so a join can be repointed after a nesting or rename; render the joins in the evidence
   the same way. Whole-transform replace stays allowed.
3. Tests: the L3 prompt for a field with a `business_date` transform shows the step and its args; an add-arg patch
   and a join repoint pass the pointer gate; a pointer outside the listed members is still rejected. Generic specs,
   not rehearsal instances.
Then mark DONE after full gates pass; the owner runs the keyed rehearsal. Do not freeze, tag or commit under
`experiments/beacon/`.

### REQ-083 — The beacon must be able to credit a correct answer before it measures anyone: harness self-test, one judging regime, honest scoring (2026-11-02)
Status: DONE (sbx1 2026-11-02 -- all 19 items code-done: part A harness self-test + preflight, A2 reference-repair proofs through sync_candidate, A3 one judging regime, A4 URL/credential wiring; part B honest judge/scoring/docs; part C date-only calendar dates + rename evidence pairing + L2 dry-run gate + generator-ref removal; part D projected-wire nesting + meridian format exclusion + IP fictionalization; full gates green; owner step: keyed rehearsal on this commit, then close)
Supersedes the first REQ-083 text (1ef5f511). Owner review (2026-11-02) of the last two days: five rehearsals of round
6603643 each surfaced new harness defects (empty prompt, wrong cwd, unvalidated patches, dropped candidate sync, no
meridian credentials, dropped URL prefix, FIAS judged against the folio dataset). Each fix was right; the pattern is
not. Nothing proves the apparatus works before it measures. At HEAD, three of five vendors cannot pass a `repair`
instance by any patch, the warm arm can never reuse what it learns, and the judge credits no-op patches. Last keyed
rehearsal on `7cd6ca27`: cold 0.70, no_knowledge 0.60, warm 0.60, agent 0.50, oneshot 0.30; those numbers partly
measure harness repair. Fix in the order below; part A before anything else; add a test per item. Everything here is
bench, verifier or simulator work except part C. The engine must keep deciding from observed evidence only: no
vocabulary, no fault-family knowledge, no reads of `docs/fault-playbook.md`, `simulators/` or `verifier/`.

**A. Harness self-test (the root cause).** A parametrised test over every vendor in `config/beacon_vendors.yaml`,
and a preflight that `run_beacon` executes before the first arm (rehearsals included) and that fails loudly:
1. The pre-drift spec synced over the UNFAULTED simulator passes `judge_produced_records`. Today: atlas and harbor
   pass; legacystay fails `field_missing` (`simulators/legacystay/app.py` ~90-115 `_null_chaos` nulls names and
   descriptions the baseline has); meridian fails `money_mismatch` 30/30 (the dataset draws reservation
   `total_amount` at random, `simulators/common/generator.py` ~270, while meridian serves no reservation money and the
   spec sums folio lines) and `business_date_mismatch` (specs carry one static `America/New_York` zone while the
   dataset spans three property zones; `spec/models.py` ~587 allows `static|field` only); fias-pabx fails by
   construction (item 3). Fix each on the side that is wrong: make the dataset's reservation total the sum of its
   folio lines; make the baseline (not the spec) tolerate the legacystay null quirk or drop the quirk; give the spec a
   per-record timezone source (a `field` pointing at the property's zone, as the sims already serve it) or make
   every property NY for the beacon dataset and say so in `protocol.md`.
2. For every family keyed `repair`, the generator's reference repair, expressed as a spec patch in the closed
   transform/pointer vocabulary, applied through the REAL `sync_candidate` path (vendor public URL + spec `base_url`
   path + the credential env the simulator accepts) passes the judge. Today `inverse_canonicalize`
   (`simulators/common/faultgen.py` ~1086-1248) proves recoverability in Python only. Where no spec patch can express
   the repair, the key is unreachable: see part B.
3. One judging regime for all vendors: the records judge compares produced records against what the pre-drift spec
   produces over the unfaulted simulator (not `generate_dataset` directly), for HTTP vendors and FIAS alike. This
   replaces the FIAS-only special case the old item 4 proposed. `verifier/beacon.py` `baseline_for` (~348-350, 415).
4. Each arm's wiring is exercised in the same preflight: the frontier drivers' rendered prompt is non-empty and the
   child `PWD` equals the sandbox dir; the engine arm's candidate sync reaches every vendor (today
   `_PLACEHOLDER_CREDENTIAL_REFS`, `bench/beacon_run.py` ~1924, covers atlas/harbor/bench only, so meridian raises
   "Missing env var 'MERIDIAN_CLIENT_TOKEN'" reported as `http_error`; and `sync_candidate` ~1023-1045 uses the
   public URL without the spec's `/api/connector/v1` base path).

**B. Answer key and judge honesty.**
5. The judge must compare every canonical field, not key presence. `_check_canonical_field_coverage`
   (`verifier/beacon.py` ~438-466) counts a present `None` as covered, and `check_business_date` (`verifier/checks.py`
   ~181-183) compares only `arrival_date` and folio `business_date`; `departure_date`, `status`, `description`,
   `updated_at` are never compared, so an unrepaired atlas `departure_date` rename passes as a repair. Compare values
   (money exact, dates exact, strings exact) for every canonical field the baseline has.
6. Pagination param renames are unobservable: the new request param names (`faultgen.py` ~305-330 pools) are never
   served, announced or documented, the simulator ignores unknown params, yet the verifier demands the exact tokens
   (`verifier/beacon.py` ~499-525). Announce them the way a vendor would: the served vendor docs (the same docs every
   arm reads) describe the new parameters for the faulted round. No arm may get them any other way.
7. `dates/tz_shift` and `dates/cutoff` on multi-zone datasets are keyed `repair` with no transform that can express
   the repair (no hour/day offset arg; one static zone). Either make them expressible (an allow-listed offset, the
   per-record zone from item 1) and prove it through item 2, or remove them from the generator's draw and record the
   removal in `protocol.md`. Do not key `repair` for anything item 2 cannot prove.
8. Key contradictions and collisions: `netTotal`/`totalNet` are in both `RENAME_PLAUSIBLE[total_amount]`
   (expects repair) and `DECOY_POOL` (expects a question) (`faultgen.py` ~205, ~454): remove them from one side.
   `bench/faults.py` ~547-548 gives `TA` and `TM` the same value (1250): give `TM` a distinct value.
9. Scoring: a candidate sync that fails for a harness reason (missing credential, bad URL, verifier `BeaconError`,
   `sync_candidate` raising) is recorded as `error` with the cause and is NOT `wrong_patch_proposed`
   (`bench/beacon_run.py` ~1829-1843, ~1886-1889, ~1921-1923); a raising `sync_candidate` must never grade as a pass.
   `correct_escalation` counts only a Blocked-with-question outcome on an instance whose key accepts a question; a
   no-patch `Complete` on a throttle is `verified_completion`, not an escalation (~1906-1912). `verifier/checks.py`
   ~106-112 reports one loss once.
10. The warm arm never hits its own cache: in-run learning stores under the descriptor vendor (`atlas`,
    `bench/beacon_run.py` ~1326) while the ladder looks up under the spec's `connector.vendor` (`AtlasPMS`,
    `repair/ladder.py` ~1443, `knowledge.py` ~68 lowercases only). Store and look up under the same key; the dev-split
    pre-warm must use the same. Test: a warm reuse within one run is observed (`warm_used_learned_from` set).
11. Token accounting on one basis: the engine arm's `small_tokens` is set from the ladder's total input tokens
    (`bench/beacon_run.py` ~1215-1218, `bench/ablation.py` ~943-949), so engine "small" includes frontier input while
    the frontier arms report in+out as `frontier_tokens`. Report in/out per model tier the same way for every arm.
12. Docs: `docs/benchmark.md` (~548 oracle-baseline framing, ~593 "one L3 call per semantic instance", ~621 FIAS
    "verified in-process") and the rehearsal `protocol.md` ("escalated exactly when the instance says so") must match
    the code after this REQ. State plainly that both arms get the stored last-good baseline.

**C. Engine (small, evidence-only; after A and B).**
13. Date-only values are calendar dates: when a `date_local` / `business_date` input carries no time component, from
    `input_format` without `%H/%M/%S` (`runtime/transforms/date_local.py` ~64-74, `business_date.py` ~67-86) or from
    the YYMMDD wire path (`business_date.py` ~95-119, `date_local.py` ~82-90), return it unchanged: no timezone
    conversion, no cutoff. A time-bearing `input_format` without an offset is vendor wall time: default `naive_as`
    to `source`. `datetime_utc` keeps its midnight-in-source-zone semantics. List `input_format` in the capabilities
    transform args (`control/routes/capabilities.py` ~142), the L3 patch prompt and the frontier evidence. Tests run
    with a non-UTC `context_tz`.
14. Rename evidence pairs old and new values: `build_generic_l3_evidence` (`repair/controller.py` ~973) takes no
    baseline, so the model judges a rename from the new name, one faulted sample and the spec field name. Add the
    vanished key's last-good values next to the new key's values (from the stored baseline/last-good records the
    engine already holds), and let the L1 `rename_field` gate (`repair/rules/rename_field.py` ~253-263, ~309-326)
    require value agreement on more than one sample, declining otherwise. Generic: no name lists.
15. The L2 "confident winner" is promoted with a pointer check only (`repair/controller.py` ~645-676) while the
    comment promises the dry-run; run the dry-run gate before `propose`, as the comment says.
16. Remove generator references from engine comments and tests: `runtime/transforms/base.py` ~48-50,
    `date_local.py` ~62, `runtime/checks.py` ~298; `tests/unit/test_autobridge_req081.py` ~131 imports
    `simulators.common.faultgen.DATE_FORMATS` into an engine test (move that assertion to a bench/verifier test).

**D. Simulator realism (do not bend the sims toward the engine).**
17. Harbor and legacystay nesting wraps the record before their own projection, so their vendor quirks vanish under
    nesting (harbor serves `confirmed` instead of `H1` and drops `date`, `simulators/harborpms/app.py` ~99-105;
    legacystay drops its null quirk). Nest the projected wire record, as meridian/atlas do after 7cd6ca27.
18. Meridian's `dates/format` support adds a raw snake_case `arrival_date: "23/10/2026"` extra to a UTC-instant
    vendor (`simulators/meridianpms/app.py` ~466-471): tuned for solvability, not realism. Serve the format change on
    the field the vendor actually has, or exclude that family for meridian and record it in `protocol.md`.
19. IP and realism: `simulators/docs/fias-pabx.md` ~4 expands FIAS to a real vendor protocol name; `config/demo_targets.yaml`
    ~27, ~57 name real products ("-style"); `simulators/common/generator.py` ~65 uses real people's names. Replace all
    with fictional ones.
Then mark DONE after full gates pass and the part A preflight passes for every vendor; the owner runs the keyed
rehearsal. Do not freeze, tag or commit under `experiments/beacon/`.

### REQ-081 — Rehearsal 6603643 on 8476da5e: evidence and grading gaps that hide or block correct answers (2026-11-02)
Status: DONE (sbx1 2026-11-02 -- all 7 items code-done: semantic signal-seeded evidence + generic fallback, equal baseline in frontier evidence, validate_against + apply-error grading, date input_format on 3 transforms, meridian+atlas nesting on wire, multi-path L3 pointers, agent permission + re-prompt; 12 new tests, full gates green; owner step: keyed rehearsal on this commit, then close)
Owner keyed rehearsal (2026-11-02, `8476da5e`, opencode sandbox, round 6603643, FAULTS=10 TRIALS=1): autobridge_cold 0.60,
engine_no_knowledge 0.60, autobridge_warm 0.40, frontier_agent 0.30, frontier_oneshot 0.20; 0 wrong patches released.
The PWD fix works (all agent sessions ran in `/tmp/bench-agent-*` and read `evidence.md`). Each item below was traced
offline on that commit (FakeProvider, in-process sims). Fix all; add a test per item.
1. **Semantic L3 gets no evidence when the spec has no `money_invariant` check** (legacystay gross_net, instance 7).
   `prepare_climb_inputs` (`src/autobridge/repair/controller.py` ~1243) sends SEMANTIC_VALUE_DRIFT to
   `prepare_semantic_inputs`, which builds money fields only from `money_invariant` checks (`repair/semantic.py`
   ~308-319, ~362); the legacystay spec has none, so 0 readings and `build_l3_evidence` returns `{}` (~499). The L3
   prompt renders "(none recorded)" everywhere and the model answers "no spec, records, or evidence". The detector's
   `money_scale_shift` signal (factor 0.25, `money_field` in its details) never reaches the model. Fix: seed the money
   fields from the signal details, and when the semantic evidence is still empty fall back to
   `build_generic_l3_evidence` (it yields a subtree and records for this instance). Test: render the L3 prompt for this
   instance; it must name the field, the factor, and baseline vs faulted values, with no "(none recorded)".
2. **Equal baseline (owner decision 2026-11-02).** The frontier arms' `evidence.md` shows only the faulted values, so
   a 1/4 scale shift is invisible to them while the engine compares against its stored baseline. Give both frontier
   arms the same baseline sample the engine uses (same records, same redaction), clearly labelled baseline vs faulted.
3. **Candidate patches are not validated in the bench engine arm, and a patch that does not apply vanishes.**
   `bench/ablation.py` (~899-912) calls the ladder without `validate_against`, so the apply check (`ladder.py` ~2039)
   and dry-run gate (~2271) are skipped; then `_candidate_spec_for` (`bench/beacon_run.py` ~1516-1517) swallows the
   apply failure (`except: return None`) and the row reads "Complete (patch=False)" with no error (harbor dates,
   instance 1). Fix: pass `validate_against` as production does; for every arm, a proposed patch that does not apply
   is recorded as an error and counted as a wrong patch proposed, never as "no patch". Likewise a candidate sync that
   ends non-Complete in the HTTP grading path (`beacon_run.py` ~1797-1801) is recorded with its outcome, not as a
   silent loss.
4. **Date input format (owner decision 2026-11-02): add an input-format argument to the date transforms.** No
   transform in the closed set parses `%m-%d-%Y`: `date_local` takes ISO or FIAS YYMMDD only and passes
   "10-15-2026" through unchanged, so the harbor `dates/format` fault (expected `repair`) is unsolvable; the generator
   proves recoverability with Python `strptime` (`simulators/common/faultgen.py` ~1235-1244), not with the transform
   set. Add an optional declarative input format to `date_local` / `business_date` (and `datetime_utc` if it applies),
   with a fixed allow-list of format directives (no free code), in the spec schema, runtime, `capabilities.json`, the
   L3 transform list and the docs. Test: the harbor format fault is repairable with it; each generated date format is
   parseable by it.
5. **Meridian nesting is unsolvable as served** (instance 3). The fault nests the internal record before projection,
   so `to_wire_reservation` (`simulators/meridianpms/app.py` ~362) emits `Id: null`, `ServiceId: "SVC-None"`,
   `AccountId: "ACC-None"`; the entity key and join keys are not patchable and the folio join cannot be recovered.
   Fix: apply nesting to the projected wire record (as a vendor would), so a path patch can repair it; check the other
   vendors' nesting the same way. Extend the detection sweep.
6. **L3 evidence hides multi-path fields** (fias-pabx layout, instance 4: DA->CD). `build_generic_l3_evidence`
   (`controller.py` ~1053) renders only `fields/<f>/path`, and allowed pointers (~1069-1070) are only `/path` and
   `/transform`; `charge_id`, `posted_at`, `business_date` use `paths: [$.DA, $.TI]`, so they render as "" and cannot
   be patched. Fix: render `paths`, allow `/paths` and `/paths/N`.
7. **The frontier agent's run ends with no answer after a rejected tool call** (instance 7: the agent ran a bash
   command, opencode auto-rejected it in headless mode, and the session ended with no final text -> "no_output").
   The bench agent's opencode config sets no `permission` block, so the default "ask" becomes a silent rejection.
   Fix: give the bench agent an explicit permission config (deny what it may not use, allow its sandbox dir), and when
   the stream ends on a tool step with no final answer, re-prompt once in the same session for the final answer.
Then mark DONE after full gates pass; the owner runs the keyed rehearsal. Do not freeze, tag or commit under
`experiments/beacon/`.

### REQ-080 — A renamed field reaches the model as evidence; a misleading new name expects a question (2026-11-02)
Status: DONE (sbx1 2026-11-02 -- answer-key vocabulary removed from engine; generic lexical+value+single-shape L1 gate; full gates green; owner step: keyed rehearsal on this commit, then close)
Owner check (2026-11-02, on `aa2a6fa7`): items 1-3 landed. Item 4 put the answer key into the engine, which is not allowed.
`src/autobridge/repair/rules/rename_field.py` (~39-83) adds `RENAME_PLAUSIBLE_NEW_NAMES`, a hand-written vocabulary per
canonical field, and the L1 rule declines or repairs by looking the new wire name up in it. It mirrors the generator's
`RENAME_PLAUSIBLE` (`simulators/common/faultgen.py` ~454) and covers every `NAME_POOL` name, so on every beacon rename
the engine decides "repair" vs "ask" exactly as the grader expects, without the model and without reasoning over the
evidence. It is also a fixed list of when to ask, which the owner rejected in REQ-078: the engine asks because the
evidence is ambiguous, not because a name is on a list. And for any real name outside the table (e.g. `checkOutTs`)
the rule still auto-repairs on value pairing alone. Finish in this REQ:
- Remove `RENAME_PLAUSIBLE_NEW_NAMES` and `_names_different_field` (and any other name vocabulary) from engine code.
- L1 repairs a rename by itself only when the evidence is unambiguous by a generic, vocabulary-free test: exactly one
  mapped field emptied/vanished, exactly one new wire key, the new key's values match the lost values (type/format and
  value pairing), and the new name is lexically close to the old wire key or the canonical field name (a generic string
  similarity, no word lists). Anything else is not an L1 match: it climbs to the model with the REQ-080 evidence (new
  keys + samples + raw records), and the model patches or asks.
- The answer key's name vocabulary stays in the generator/verifier only (grading data). Engine code never imports,
  copies or derives from it.
- Tests: an engine-side test that fails if any engine source (`src/autobridge/`) contains a `NAME_POOL` name or a
  per-field name vocabulary; L1 repairs a lexically close rename with matching values; L1 declines (climbs) for a
  lexically unrelated name such as `checkinDate` for `endDate`, and for a name outside any list such as `checkOutTs`
  when it is not lexically close; the climb's L3 prompt shows the new key and samples.
Do not freeze, tag or commit under `experiments/beacon/`; the owner runs the keyed rehearsal after this lands.
Previous status: DONE (sbx1 2026-11-02 -- all 5 items code-done: faithful rename serving, detector new-keys-on-empty, L3 evidence with new keys + raw samples, misleading->escalate key + engine gate, 35 new tests + sweep extension + docs; full gates green; owner step: keyed rehearsal on this commit, then close)
Found in the owner keyed rehearsal on `fd2c8388` (round 6603643, instances 0 and 5: atlas `rename`,
`departure_date` -> `reservationStatus` / `checkinDate`). Atlas served `endDate: ""` plus the new key holding exactly the
departure dates, e.g. `{"startDate": "2026-11-03", "endDate": "", "checkinDate": "2026-11-06"}`. Every engine arm
escalated "Which vendor field should Reservation.departure_date map to now that $.endDate is empty 100%?" without ever
being shown the new key. The question was not a judgment over the evidence; the evidence was missing. Three defects:
1. **Simulators keep the old key after a rename.** `simulators/atlaspms/app.py` `to_wire_reservation` (~265-266) always
   emits `endDate` (empty when the record no longer carries `departure_date`); a real vendor rename removes the old key.
   `guestName` in the same function already does it right (emitted only when present). Fix every renameable field in
   every vendor serializer (atlas, harbor, meridian, legacystay, fias-pabx where a rename applies) so a renamed field
   vanishes from the wire and only the new key is served.
2. **New wire keys are reported only when a mapped key is missing.** `src/autobridge/drift/signals.py` (~695-697)
   `continue`s when no covered key is missing, so a mapped key that is present but empty in every record, alongside a
   genuinely new key, produces no `keys_changed` signal with the new keys and their samples. Report the new keys (with
   samples) whenever a mapped field goes empty/null across the sync, not only when its key disappears.
3. **The model's evidence never shows raw vendor records.** `build_generic_l3_evidence`
   (`src/autobridge/repair/controller.py` ~1083-1093) samples only canonical (mapped) records, so unmapped vendor keys
   never reach the L3 prompt. Include the new wire keys and a redacted raw vendor-record sample in the evidence, so the
   engine sees what the frontier agent sees in `evidence.md`. Whether to patch or ask stays the model's decision.
4. **Answer key (owner decision 2026-11-02): a rename to a misleading new name expects a question.** The generator
   picks `new_name` from one shared `NAME_POOL` (`simulators/common/faultgen.py` ~113), so a departure date can land
   in `checkinDate` or `reservationStatus`. When the new name does not plausibly name the field, the expected answer
   is an escalation (one operator question), not a repair; a patch there is not credited as a correct answer. Keep
   `repair` as the expected answer only where the new name plausibly names the same field. Decide this when the
   instance is generated (in the generator / `verifier/beacon.py` answer key), never from engine output.
5. Tests: a served atlas rename has no old key on the wire; the detector emits the new key with a sample when the
   mapped field is empty in every record; the L3 prompt for a rename lists the new key and a raw record sample; the
   answer key for a misleading rename is an escalation. Extend the REQ-078 item-4 detection sweep with these cases.
Do not freeze, tag or commit under `experiments/beacon/`; the owner runs the keyed rehearsal after this lands.

### REQ-078 — When the engine does not know, it asks: model first, operator last; and a beacon that grades it fairly (2026-11-01)
Status: DONE (sbx1 2026-11-02 -- PWD pinned to the sandbox dir in all three agent CLI drivers + parametrized test; full gates green; owner step: keyed rehearsal on this commit, then close)
Owner keyed rehearsal (2026-11-02, `fd2c8388`, opencode sandbox, round 6603643, FAULTS=10 TRIALS=1, live key):
every engine arm 0.30 (0 wrong; model questions now cite the evidence), frontier_agent 0.20 (3 wrong proposed),
frontier_oneshot 0.20. Instances 7-9 failed on every model arm from a network outage on the owner side (not a defect).
On instances 0 and 5 (atlas rename) the agent answered `{"patch": []}` without reading any input: its opencode session
ran in `.worktrees/verify` (the repo), its reads of `evidence.md` / `pre_drift_spec.yaml` there failed, and it gave up.
Cause: `OpenCodeCliDriver.run` (`bench/baseline_coding_agent.py` ~964-985) sets `cwd=` to the sandbox dir but copies
the parent environment, and opencode takes its working directory from the inherited `PWD`, not the process cwd
(owner reproduced in the sandbox: stale `PWD` -> session directory is the repo; `PWD` = sandbox dir -> session
directory is the sandbox dir). So the agent never saw its files, and it sat in the repo where verifier/generator
sources are readable — the item-6 isolation is not in force. Finish in this REQ:
- Set the child's `PWD` to the sandbox dir in `OpenCodeCliDriver.run` (and any other driver that spawns an agent CLI
  with `cwd=`). Add a test that fails if the spawned env's `PWD` differs from `cwd`.
- Mark DONE after the tests and full gates pass; the owner runs the keyed rehearsal on the result (Ralph's sandbox has
  no frontier key). Do not freeze, tag or commit under `experiments/beacon/`.
Previous status: DONE (sbx1 2026-11-01 -- bench engine arm takes the production path (shared prepare_climb_inputs + faulted summary, L3 blocks recorded), reason names L3 vs L4, legacystay gross_net + meridian nesting fixed + sweep extended, 3 new tests, full gates green; unkeyed rehearsal round 6603643 FAULTS=10 TRIALS=1: engine 0.40/0.40/$0/0tok/0 wrong, frontier 0.00 (no key); owner step: keyed rehearsal with live key on this commit, then close)
Owner keyed rehearsal (2026-11-02, `db9f2db6`, opencode sandbox, round 6603643, FAULTS=10 TRIALS=1, live key):
frontier_agent 0.60 ($0.016/run, 4 wrong patches proposed, 0 released), frontier_oneshot 0.20, every engine arm 0.30
(~6k tokens, $0.0008/run, 0 wrong). The engine now reaches the model on every fault, but repaired none: every model
answer is an escalation saying it got nothing — e.g. "Which renamed field mapping should be applied since no spec or
vendor evidence was provided?" (rows: `patch_summary` in grades.jsonl). Finish in this REQ:
- **Item 1 not met: the bench engine arm does not take the production path.** `bench/ablation.py` `run_engine_arm`
  (~888-895) calls `ctrl.handle(provider=…)` with no `evidence=` and no `dry_run_fault=`; production derives both with
  `prepare_climb_inputs(...)` + `build_dry_run_fault(summary)` (`engine/detect_repair.py` ~1302-1320). The arm's
  `DerivedDiagnostics` (ablation ~584) does not even carry the faulted sync summary, so the L3 prompt renders
  "(none recorded)" for spec subtree, raw records and observed evidence (ladder ~1982-1984). Carry the faulted summary
  through the diagnostics (ablation and beacon `derive_beacon_diagnostics`) and call the same input-preparation
  function production calls — one shared function, not a copy. Add a test that renders the L3 prompt from a beacon
  engine arm for a structural drift and fails if any evidence block reads "(none recorded)" or the spec subtree /
  records sample is missing.
- The row's reason text still reads "escalate at L4 (no rule)" when the model was asked; name the rung that decided
  (e.g. "L3 model escalated: <question>").
- Item 4 misses on this round: legacystay `semantic` gross_net (instance 7) is still 0 signals / Complete (silent);
  meridian `nesting` (instance 3) is classified ENUM_NEW_VALUE (11 signals). Fix both and add them to the sweep.
- Then rerun the keyed rehearsal in the opencode sandbox (key from `.env`; `make up` works there) on round 6603643
  and report every arm in the Status line. Do not freeze, tag or commit under `experiments/beacon/`.
Owner check (2026-11-02, on `916ce205`): items 1-7 and 9 landed; two parts are missing, finish them in this REQ.
- **Item 8, simulator defects: no file under `simulators/` changed.** Fix: meridian serializers crash on non-ISO /
  renamed / nested dates (`simulators/meridianpms/app.py` ~306-308, ~382 `fromisoformat`); atlas folio rename never
  serves the new key (`simulators/atlaspms/app.py` ~340-360, no `_extra_wire_fields`); atlas defaults missing amounts
  to `"0"` (~270); meridian `dates/cutoff` never manifests but is keyed `repair` (fix the fault or the answer key in
  `simulators/common/faultgen.py` / `verifier/beacon.py`). Extend the item-4 detection sweep so these instances are
  served without a crash and detected.
- **Item 10, the rehearsal was not run** ("no live stack"). The stack runs inside the opencode sandbox: from the
  worktree, `make up`, then `make bench-beacon REHEARSE=<a published drand round> FAULTS=10 TRIALS=1` (the frontier
  key comes from `.env`), then `make down`. Report every arm's verified score, cost and wrong patches proposed /
  released in the Status line, whatever they are. Do not freeze, tag or commit anything under `experiments/beacon/`.
Owner principle (2026-11-01): **the engine must ASK when it does not know** — any drift that L0/L1 cannot repair climbs
to the model (L2/L3), and only when the model cannot produce a verified fix does it ask the operator. A hard-coded list
of drift classes (or families, vendors, scenarios) that may reach the model is the wrong approach and must go. The
engine must never pass silently when records are wrong. The verifier gate and operator Approve stay: no unverified
patch is ever released, nothing auto-applies.

Found by the owner-session beacon rehearsal (round 6603643 in the opencode sandbox: frontier_agent 0.60, every engine
arm 0.20 with $0 cost) and a three-part review (ladder, a 1,080-instance detection sweep, beacon grading). Fix in this
order; items 1-4 are the engine, 5-8 the beacon. Update `docs/architecture.md`, `docs/safety-model.md`,
`docs/benchmark.md` and `docs/demo.md` in the same commits wherever they describe the old behaviour.

1. **Remove the class gates; every repairable drift climbs.** Today only `SEMANTIC_VALUE_DRIFT` reaches a model:
   `engine/detect_repair.py` ~1183 and ~1270 (and `_submit_deferred_semantic_repair` ~465 hard-codes the class),
   `repair/controller.py` ~272 (`provider is not None` is the only way into the climb; `handle_from_signals`,
   `handle_classified` and the job else-branch ~1465 never pass one) and ~1357, `bench/ablation.py`
   `_MODEL_AMBIGUITY_CLASSES` (~207, ~843). Replace with: L0 → L1 → (no verified patch) → L2/L3 → L4 for every class
   except the true non-fixables (`_NON_FIXABLE`: capability removed, auth revoked → operator, with a specific question)
   and backoff/transient (retry). Also climb, with the failure as evidence, when an L1 rule matches but its patch fails
   the dry-run (`detect_repair.py` ~1111) or yields no valid patch, and for `UNKNOWN` (L3 with the raw evidence, no
   hypotheses needed). The deferred live-model job path applies to every class, not only semantic. The bench engine
   arms take exactly the production path (no bench-only gate).
2. **A verified model patch can be approved and released.** Today `_handle_semantic_climb` always returns
   `escalate` with no patch (controller ~221, ~525): a plain L3 `{"patch": …}` that passed the pointer check and the
   dry-run is recorded and dropped, and the operator gets a canned question; an L2 winner (confidence ≥ 0.70, ladder
   ~2463) is discarded. Make a model patch that passes pointer check + dry-run + verifier a candidate the operator can
   Approve (same release path and verifier gate as an L1 patch; never auto-applied); turn the confident L2 hypothesis
   into a patch through the same checks. Semantic drift keeps today's rule that money semantics need the operator's
   business decision (the model's patch rides as the proposal). Every candidate a model contributed to requires the
   verifier (fail closed if it is unreachable). Learn approved model fixes into L0 like rule fixes.
3. **Class-neutral model evidence.** `build_l3_evidence` (`repair/semantic.py` ~480), `allowed_l3_pointers` (~228),
   `_L3_FIELD_POINTER` (ladder ~1942), the enumerator families (`discovery/hypotheses.py` ~40) and
   `_patch_from_hypothesis` (ladder ~955) are built for money fields only (`gross_sum`, money_invariant, entity field
   transforms). For every class the L3 prompt gets: the drift signals and their messages, the fingerprint delta,
   unmet criteria / failing checks, the failed L1 attempts and why, the relevant spec subtree (operations/pagination,
   entity fields, enum maps) and a faulted-records sample. Allowed patch pointers derive from what the delta touches.
   Prompt text and the fallback operator questions are class-neutral (no "semantic", no "which mapping"). The deferred
   job payload always carries signals, summary/dry-run fault and evidence, and the job's L3 proposal is judged with
   the verifier URL (today `verifier_url=None`, controller ~1442). The `enumerator_yield_count >= 2` L3 skip
   (ladder ~2502) must not block structural drifts.
4. **Detection: no silent wrong records.** The sweep found 142/1080 beacon instances (13%) where the sync is
   Complete with 0 signals while the canonical records are wrong. Fix so each is detected (and then climbs per item 1):
   - money ratio: `_observe_money_scale_shift` tries only ×10/100/1000 (`drift/signals.py` ~779, ~1063); detect any
     constant ratio across ≥ 90% of rows against the last-good sketch (gross→net ×0.25 / ×0.5 is invisible today);
   - dates: a constant date/datetime offset versus the last-good sketch (tz shift, cutoff), and non-ISO values in a
     canonical date field (`"10-13-2026"` passes today); `business_date_invariant` must not depend on one spec shape;
   - a mapped field that turns into a constant zero/default in every row counts as emptied (`signals.py` ~782-791
     counts only None/blank);
   - `mapped_field_emptied` carries the added-key candidates so `rename_field` can pair by value (today it never
     resolves when the old key stays with a blank value);
   - nesting: an added object that contains the missing keys is a path move, not a rename (today `rename_field`
     proposes a wrong patch, e.g. `guest_name → $.localInfo`) or a type change;
   - classifier: `pagination_changed` must not come from runtime counters (`pages_fetched`, `stop_reason`,
     `drift/fingerprint.py` ~161-163) — an aborted sync is not a pagination change; `http_error` / `transient_error`
     unmet criteria map to TRANSIENT (`signals.py` ~1266-1290), not a generic invariant failure;
   - FIAS: mapped ids include multi-path `paths` (`drift/event_feeds.py` ~89, so DA/TI renames are seen); the delta
     compares baseline-announced to current-announced ids (~229-230, today mapped-only vs all-announced, never 1:1);
     the report's `extra.rename` reaches the ladder (`bench/beacon_run.py` `_derive_fias_diagnostics` drops it and
     `ablation.py` ~830 hard-codes `extra={}`).
   Add a detection-sweep test: for a fixed set of seeds × every family × vendor, no instance whose records differ from
   the baseline may end Complete with 0 signals.
5. **Grading is about the act, the same for every arm.**
   - An arm that crashed, timed out, was truncated, ran out of budget or never ran grades as its own failure class,
     never as an escalation (today `beacon_run.py` ~1358 falls through to Blocked/no-patch, which passes every
     `escalate` instance per `verifier/beacon.py` ~266). Escalation passes only when the arm explicitly decided to ask.
   - "No change needed" is a stated answer for every arm: the frontier prompts and evidence say that an empty patch
     means no change (today only the engine can pass `no_patch` instances by finding nothing).
   - Grade "no patch + one question" the same whether reported Degraded or Blocked, for every arm.
   - FIAS repairs are graded on records like HTTP repairs (today a sync that completes passes, ~1531-1550).
   - Report wrong patches *proposed* as well as released; say in the docs that an unneeded patch on a no-change
     instance counts as wrong.
6. **The frontier agent is isolated.** `opencode run` (`bench/harness.py` ~858) has unrestricted read/bash/network:
   it could read `simulators/common/faultgen.py` / `verifier/beacon.py`, regenerate the instance from the committed
   protocol + public round, or use the unauthenticated `/beacon/records` as an oracle. Restrict it to its sandbox
   dir and the vendor sandbox URL only (no repo, no verifier/admin ports). Equal-information check both ways: the
   engine's baseline is the instance's own unmutated dataset (`bench/faults.py` ~450-455), which production never
   has — give every arm the same last-good sketch from a different dataset seed.
7. **Cost, budgets and liveness are real.** Engine model cost is never set in `model_usage` (`ladder.py` ~355-368,
   ~818-822) and `summarize_grades` turns None into 0.0 (~1673); input/output tokens are swapped for engine arms
   (`ablation.py` ~903). Budgets match: one token ceiling for every arm, enforced for the agent too (rehearsal #3:
   221k > 200k); freeze `BENCH_TOKEN_CEILING` / `BENCH_WALL_CLOCK` into `protocol.md`. Before freezing,
   `beacon-eval` checks the live key, runs the frontier-agent preflight (no silent `FakeOpenCodeDriver`) and hits
   `/readyz` on verifier and simulators.
8. **Run safety and honest statistics.**
   - Append each grade row to `grades.jsonl` as it lands and resume from it; abort (never record as a loss) on
     infrastructure errors (today a mid-run crash loses everything and a rerun resamples the frontier arms).
   - `beacon-eval.sh`: a failed freeze commit stops the script (~67 hides it); write the push time into EVIDENCE.md;
     refuse to push a freeze after its round is published; non-TTY `PUSH=yes` without `CONFIRM=yes` fails loudly;
     works when run inside the opencode sandbox (git identity, credentials, stack).
   - Owner decision: every arm keeps the frozen trial count; scores are averaged per instance first and CIs are
     computed over instances, with a bounded interval; paired per-instance arm differences; per-family
     rows in summary and EVIDENCE.md; `n` names instances.
   - Simulator defects that make instances unsolvable: meridian serializers crash on non-ISO / renamed / nested
     dates (`simulators/meridianpms/app.py` ~306-308, ~382); atlas folio rename never serves the new key
     (`atlaspms/app.py` ~340-360); atlas defaults missing amounts to `"0"` (~270); meridian `dates/cutoff` never
     manifests but is keyed `repair`; code_list "repair" instances map new codes to random statuses nothing reveals
     (`faultgen.py` ~335) — owner decision: nothing reveals the meaning, so asking the operator is the correct
     answer there (accept escalate / one question; a guessed mapping is a wrong patch).
   - Owner decision: `transport_auth/token_expired` — escalating is right (the engine can refresh only OAuth
     client-credentials tokens); the answer key accepts an escalation with one question there.
9. **The warm arm learns during the run** (owner decision). L0 replays only an identical drift, so a cache warmed on
   the dev split can never fire on random beacon faults and warm always equals cold. Instead: instances run in the
   frozen order; after each instance a verified fix the warm arm produced is learned (the way an approved fix is in
   production) and is available to later instances; cold never learns. Keep the dev-split pre-warm as the starting
   cache. Report per instance whether warm used a learned fix (rung + source instance) and say in the docs what a
   warm-vs-cold gap can and cannot show.
10. Tests: every class climbs past L1 with a fake provider when no rule fixes it (no class list anywhere); a verified
   model patch becomes an approvable candidate and an unverified one never does; the detection sweep (item 4); a
   crashed arm never passes an `escalate` instance; the agent cannot read the repo or reach the verifier; resume after
   a mid-run abort keeps earlier rows. Then one rehearsal in the opencode sandbox
   (`make bench-beacon REHEARSE=<published round> FAULTS=10 TRIALS=1`), results reported in the Status line whatever
   they are. Full gates green.

Constraints: do not run `make bench-beacon` for real (rehearsal only), `make beacon-eval` or `make eval-freeze`; do not
freeze, tag or commit under `experiments/beacon/`; never touch `verifier/hidden_cases/heldout/` or
`~/autobridge-heldout`. Never widen a guard test without an owner note here.

### REQ-077 — The beacon run is the final benchmark: anyone can rerun it; `make bench` and `make demo` step 4 work as documented (2026-11-01)
Status: DONE (sbx1 2026-11-01 -- all 6 items code-done: per-round beacon + tag per run + PUSH opt-in + ralph guard + grep fix, bench from ablation, demo step4 approved, beacon warm from dev split, Deny sticks; full gates green; owner step: real beacon freeze/run when ready)
Owner decision (2026-11-01): the private held-out split is retired (its seeds cannot be published, so nobody else can
verify a run); the beacon run (REQ-025) is the one final benchmark. The docs were corrected on 2026-11-01 to describe
the code as it is; this REQ makes the code match what a public user needs. Update `docs/benchmark.md` (and the README
lines that mention these commands) in the same commits so the docs stay true.

1. **Anyone can rerun the beacon end to end with fresh values.** Today `make beacon-eval` is single-shot: once the
   owner's run is committed, a user's `make beacon-eval` resumes `experiments/beacon/protocol.md`, reuses the
   `eval-freeze` tag, and `bench-beacon` refuses because results exist; it also pushes to `origin HEAD:main`.
   - One protocol per run: `experiments/beacon/<round>/protocol.md` (the freeze computes the target round first).
     `run` / `verify-repro` / `beacon-eval` resolve the protocol by round; a new `make beacon-eval` always freezes a
     new future round (resume only an unrun protocol of this checkout, never another run's results). Rehearsal
     paths (`experiments/beacon/rehearsals/<round>/`) stay as they are. `parse_protocol` still reads the old single
     path if present.
   - Tag per run: `eval-freeze-<round>`.
   - Push is opt-in: `PUSH=yes` pushes the freeze (before the round) and the results to the current branch's
     upstream; without it both are committed locally and the script prints how to push to a fork before the round for
     a public timestamp. `CONFIRM=yes` keeps its meaning.
   - The ralph guard matches only loops running in this checkout's path (other projects run `ralph-afk.sh` too).
   - Bug found 2026-11-01: `scripts/beacon-eval.sh` reads the round with `grep '^beacon-target-round:'`, but
     `freeze()` writes `- beacon-target-round: N`, so under `set -euo pipefail` the script exits 1 silently right
     after the freeze (no commit, tag or push; every rerun resumes into the same failure). Parse the protocol the
     way `parse_protocol` does; add a test that runs the script's parsing on a real `freeze()` output.
   - EVIDENCE.md points at the round's own protocol and tag.
   - Docs: a short "Run it yourself" part in the beacon section of `docs/benchmark.md` — prerequisites (stack up via
     `make local-up` or `make up`, `FRONTIER_API_KEY` for the frontier arms, what happens without it), the one
     command, roughly how long / how many frontier calls per fault, where results land, and `make verify-beacon`.
2. **`make bench` must not fail by design.** The matched step's CLI arms are synthetic and `_guard_zero_tool_calls`
   refuses the empty scorecard (`bench/harness.py` ~1179-1205, ~1675), so `make bench` exits 1 before the ablation.
   Proposed (owner approves with this REQ): `make bench` runs the dev ablation + learning curve (the real comparison)
   and writes the scorecard from the ablation results; the synthetic matched mode stays only behind
   `--arms matched` for `test_07` / harness tests. Keep the guard for any path that would publish an empty
   scorecard. `make bench-heldout` / `verify-heldout-reveal` stay untouched (retired, not deleted).
3. **`make demo` step 4 post-approve check.** `bench/demo.py` (~1764-1771) asserts the repair decision is still
   `"escalate"` after `approve-proposal`, but since REQ-053 approval sets `"approved"` (`engine/releases.py:403`).
   Assert the REQ-053 shape instead (decision `approved`, approval decision `approve`, escalation bundle with the
   proposal present). Add a unit test that fails on the old assertion.
4. **`autobridge_warm` learns nothing on the beacon run.** `bench/beacon_run.py` `run` / `rehearse` never pass
   `knowledge_warm` and never call `warm_knowledge`, so the warm arm starts from an empty cache exactly like cold and
   the beacon run cannot show learning. Warm it the way the dev ablation does: learned knowledge from the dev split
   only (never from beacon instances), built before the first instance, the same for real runs and rehearsals.
   Record in the run output (and EVIDENCE.md) that warm was warmed on the dev split and how many entries it held.
5. **Deny does not stick.** After **Deny** (approval decision `reject`) the next scheduled sync re-detects the same
   still-live fault; `engine/detect_repair.py` (~121-126, "never dedupe onto a decided repair") treats it as a new
   incident, so a new drift + repair opens and climbs again (another frontier call with a key) and "needs your
   decision" returns within one sync interval. The Deny preview promises "the connector stays in error and the drift
   stays open" (`control/routes/repairs.py:308`). Fix: a re-detection of the same drift (same class + fingerprint
   delta) on the same active release after a `reject` attaches to the denied drift/repair — no new repair, no climb,
   no model call; the connector stays ■ Error and reads as denied / waiting on the vendor. A changed drift or a
   different active release opens a new incident as today. Approved repairs keep today's behaviour (a re-detection
   after an approval is a new incident). Update the Deny wording in `docs/demo.md` / README to match.
6. Tests for item 1: two consecutive freezes target different rounds and write different protocol dirs; a committed
   prior run's results never block a new run; no push without `PUSH=yes`; ralph guard ignores a loop in another
   path. Item 4: the beacon warm arm receives a non-empty dev-split cache and the cold arm an empty one. Item 5: Deny
   then a re-detection of the same drift opens no new repair and makes no model call; a different drift or release
   does; approve-then-redetect unchanged. Full gates green.

Constraints: do not run `make bench`, `make bench-heldout`, `make bench-beacon`, `make beacon-eval` or
`make eval-freeze` for real (unit tests with fakes only); do not freeze, tag or commit under `experiments/beacon/`;
never touch `verifier/hidden_cases/heldout/` or `~/autobridge-heldout`.

### REQ-074 — Repair page round 2: what REQ-073 left on approved repairs (2026-11-01)
Status: DONE (sbx1 2026-11-01 -- all 5 items code-done: tracker replaced-by + live window (version fallback), approved business-decision headline, preview hidden on approved, zero-funnel plain line, verifier one-verdict-no-glyph; 8 new UI tests, full gates green; owner step: browser check of the same repairs to close)
Owner session browser check on `6e99ef96` (local stack, approved repairs from the demo run). REQ-073 items 1 (per-repair
model calls), 5 (no Reject/Roll back after approval) and 7 (Sync runs note on one line) are confirmed live. Remaining:

1. **Approval steps on a repair whose release was later replaced.** Atlas repair `34cca645` (L1 `page_to_cursor`,
   approved → r2 `08b53387`; Atlas later moved r1 → r3 in the deck-3 encore) shows "✓ Approval recorded", then "○ Release
   activated — waiting for activation", "○ Verifying sync — waiting for the next sync", "○ Sync Complete". r2 *was*
   activated and synced Complete before r3 replaced it. Derive the steps from history (activation record / sync runs on
   that release), and say when it was later replaced (e.g. "r2 was live 10:21–10:22, replaced by r3").
2. **Rail headline on an approved business decision reads "Still climbing: the verifier has not run yet"** (Harbor
   repair `77853663`, approved, verifier report 1/1 pass). An approved repair never reads "climbing"; show the approved
   state (e.g. "Business decision: approved — shipped").
3. **Developer text after approval** in the "If you click Approve / Deny…" section: "Already approved by Eric. Approving
   again re-activates the same release." and "Repair decision is 'approved', not 'propose': nothing to approve." On an
   approved repair that section is not shown (there is nothing left to click).
4. **Hypotheses tile on a rule fix still shows zeros** (REQ-073 item 3 not visible on `34cca645`): "Hypotheses · probes 0 ·
   enumerated, then filtered", a bare "0", and "0 resolved without enumeration". With no hypotheses: one plain line or
   no tile, never a bare 0.
5. **Verifier tile repeats itself** on `34cca645`: "✓/— · passed on this patch · pass · report ee21f3a2 · … · passed on
   this patch". One verdict, once; no "✓/—" glyph when the check count is unknown.

Tests: page tests for each item using the shapes above (approved L1 repair whose release was replaced; approved business
decision; approved repair hides the preview section; 0-hypothesis rule fix; verifier without counts). Existing e2e
testids keep working, full gates green. Owner step after: browser check of the same repairs.

### REQ-075 — Directive: §4 silent-corruption detection — live sync path never supplies a baseline, so `money_scale_shift` cannot fire live (2026-11-01)
Status: DONE (sbx1 2026-11-01 -- persisted per-connector money sketches + shared sketch mechanism for bench/beacon; 13 new unit tests, full gates green; owner step: none codeable left -- dev benchmark re-run is the owner session after this REQ)
Found by the 2026-11-01 directives audit (diff `8f1b2dca..origin/main`). REQ-064 B1 (kept by REQ-065) added `money_scale_shift` → `SEMANTIC_VALUE_DRIFT` in `src/autobridge/drift/signals.py:1039`, but it requires `baseline_canonical`, which only the bench/beacon paths supply (`bench/ablation.py:586`, `bench/faults.py:413` via `baseline_summary=`). The live path (`src/autobridge/engine/detect_repair.py` `detect_after_sync`, `src/autobridge/repair/controller.py:744`) calls `signals_from_sync` with no baseline, so a live vendor switching minor↔major units (×100) syncs `Complete` with no drift — the REQ-064 build note already calls this "history-blind (no persisted value sketches)". Fix: persist a bounded per-connector baseline (previous sync's canonical money values or value sketches, never credentials) and pass it as `baseline_summary` on the live detection path, or state the limitation in `docs/architecture.md` and scope B1 to bench/beacon only. Tests: live-path ×100 shift → `SEMANTIC_VALUE_DRIFT` escalation with 0 auto-patch; first sync (no baseline) stays silent.

Owner decision (2026-11-01): **take the first option — persist a live baseline**; do not scope B1 down to bench/beacon.
1. Persist a bounded per-connector baseline after each `Complete` sync (canonical money values or value sketches per
   entity/field, never credentials or raw payloads beyond what is needed) and pass it as `baseline_summary` on the live
   detection path (`detect_after_sync` and the repair controller call). Update `docs/architecture.md` (drop the
   "history-blind" note for B1).
2. Benchmark re-check: the bench/beacon paths must use the same persisted-baseline mechanism as live (no separate
   `baseline_summary=` shortcut), so the bench no longer credits a detection the product cannot make. Add a test that a
   bench-style ×100 fault is detected through the shared mechanism and that a first sync with no stored baseline stays
   silent. Do not run `make bench` / `make bench-heldout` (paid; the owner session runs the dev benchmark after this
   REQ) and do not touch `verifier/hidden_cases/heldout/` or `experiments/beacon/`.

### REQ-076 — Directive: §5 test hermeticity — UI suite flaked once under full `make test` (2026-11-01)
Status: DONE (sbx1 2026-11-01 -- runtime-line race fixed: REQ-050 test now waitFors the settled build-run detail instead of reading the interim 'job starting' line; 10/10 full UI suites green + full make test green; no owner step left)
Found by the 2026-11-01 directives audit: one full `make test` run failed `ui/components/NewConnectorForm.test.tsx > sends no runtime choice; the engine's decision reads cloud for HTTP docs (REQ-050)` (339/340 UI tests passed); the same file passes standalone (26/26) and the full UI suite passes standalone (340/340). REQ-064's build note reports the same pattern ("two full-suite flakes ... both pass standalone"). Find the shared-state/timing cause (no retries or quarantines as the fix), add a regression test or isolation fix, and note the root cause in the build note.

### REQ-072 — A stack restart mid-sync must not wedge a connector in "sync running" (2026-11-01)
Status: DONE (sbx1 2026-11-01 -- orphan sync/build fail at startup + stale-job bound unblocks scheduler + build claims exactly its job; full gates green; owner step: none -- restart/scheduler behaviour is unit-covered, no live stack needed)
Owner local stack, 2026-11-01 10:50: `make local-down && make local-up` while MeridianPMS had a sync in flight.
On startup `reclaim_orphaned_jobs()` (`src/autobridge/control/app.py`, `JobRunner.reclaim_orphaned` in
`src/autobridge/control/jobs.py`) moved the `running` sync job back to `queued` (attempts 1/3), but nothing ever
claims a re-queued job: jobs run by id when submitted, and no worker drains the queue. The queued row made
`connector_busy_reason` (`src/autobridge/control/scheduler.py`) report the connector busy, so the scheduler skipped
it and the UI showed "▲ Degrading · sync running · last sync N m ago" until the owner's session failed the row by
hand (job `88bdef5f…`, local DB only). Any restart during a take can do this.

1. After a restart no job may stay `queued` or `running` with nobody to run it. Orphaned **sync** jobs: mark them
   `failed` with a reason ("orphaned by a restart; the next scheduled sync replaces it") — syncs are periodic, so
   re-running a stale one is not needed. Orphaned **build** and **repair** jobs: either run them (a startup drain in
   the background) or fail them with a reason the UI shows; pick one, keep it consistent with the existing
   `fail_orphaned_repairs` (REQ-043 item 4), and say which in the build note. Never leave a re-queued job no worker
   claims.
2. Belt and braces: `connector_busy_reason` must not treat a job as in flight forever. A `queued` / `running` sync
   job older than a bound (a few sync intervals) with no live worker is stale: the scheduler syncs anyway and the
   stale row is failed with a reason.
3. Tests: restart with an in-flight sync → after startup the connector is not busy and the next tick syncs;
   orphaned build/repair handled as chosen; stale-job bound unblocks the scheduler. Full gates green.
Do not touch the uncommitted UI work in the owner's checkout (`ui/app/connectors/*`, `ui/components/Connector*`,
`ui/app/globals.css`).

### REQ-071 — Deck 7: the PABX link drop stays visible in Sync runs (2026-11-01)
Status: DONE (sbx1 2026-11-01 -- edge link on SyncRun + sync-runs drop note + live-agent event match + docs; full gates green; owner step: keyed walkthrough of deck 7 to close)
Owner demo run, deck 7 (`make demo-break SCENARIO=fias_link_resync`): the PABX connector page shows only the live link
state, so a presenter who switches to the page a few seconds late never sees the drop (up → down → re-handshaking →
up takes ~8-10 s). Owner decision: show it in the existing **Sync runs** table, which is persisted and newest-first.

1. **Each sync of an edge (v3 / `fias_tcp`) connector records the link at sync time:** the agent's `link_up` and its
   reconnect counter (the relay heartbeat's `link_detail` already carries `accepted=N`, which increments on each
   re-established link; parse it or, better, have the agent/relay report it as a field). Store it on the SyncRun
   (no new store if an existing JSON column fits; otherwise a migration).
2. **Sync runs table note (edge connectors only):** when the reconnect counter went up since the previous sync of the
   same connector, the row reads "link dropped, re-handshaked · +N postings, 0 lost, 0 duplicated" (N = records
   delta vs the previous run; lost/duplicated from the relay's idempotency keys, never assumed); when the link was
   down at sync time, the row reads "link down". Short drops between two syncs must still show (that is why the
   counter, not the state at sync time, drives the note). Other connectors' rows are unchanged; keep the table's
   columns aligned (REQ-068 item 8) and the existing testids.
3. **Fix the link-event log** (`/demo/pabx-events`, `src/autobridge/control/routes/demo.py` and its recorder): live
   it records `{"link_up": false, "agent_seen": false}` every 10 s while the agent reports the link up (the panel
   hides this because the live agent wins). Record the real agent state (match the agent the way the PABX panel does
   since REQ-068 item 7).
4. `docs/demo.md` deck 7: one line on where the drop shows after the fact.

Tests: counter increment between two syncs → note on the later row; link down at sync time → "link down"; no
change → no note; non-edge connectors untouched; event recorder matches the live agent. UI test for the row note.
Full gates green. Owner step after: keyed walkthrough of deck 7.

### REQ-070 — Semantic escalation keeps a passing retry proposal (deck 5 Approve path) (2026-11-01)
Status: DONE (sbx1 2026-11-01 -- retry plain-patch adopted as escalation proposal + model question kept + drop reason cleared; no prompt change, full gates green; owner steps: keyed make record-frontier gross_to_net (optional, hashes unchanged) then keyed walkthrough re-run to close)
Owner keyed walkthrough on `df09f082`: `tmp/walkthrough-df09f082/FINDINGS.md` (gitignored; read in place). Everything
else matched the script; this is the one remaining deck 5 defect.

Live trace: call 1 answered `{"escalate": <question>, "rationale", "proposal": {Reservation checks/3 compare_to →
$.net_sum}}`; the REQ-069 gate refused it (FolioCharge.money_invariant still fails) and the one retry ran. Call 2
answered a plain `{"patch": [Reservation checks/3 + FolioCharge checks/2 compare_to → $.net_sum]}`, which passed the
dry-run. `src/autobridge/repair/ladder.py` (~line 2750, "retry answered with a plain patch: supersedes the
escalation") then cleared the model's question and treated it as an ordinary L3 patch; for SEMANTIC_VALUE_DRIFT L4
escalates ("never auto-patched") with the engine's generic question and no proposal (`candidate_patch` None), so the
operator gets no Approve and loses the model's question.

1. When the retry follows a refused **escalation proposal**, the retry's answer is a corrected proposal for that same
   escalation: a passing plain patch becomes the escalation's proposal (operator-approved, never auto-applied), and
   the escalation keeps call 1's question and rationale. Label/effect come from the model (call 1's proposal label
   and effect, or the retry's if it sent them); do not write new label text in code beyond the existing neutral
   fallback. A semantic drift is still never auto-patched. A retry that fails the gate still drops to question-only
   with the model's question kept (not the engine's generic one).
2. Clear call 1's `proposal_dropped` / drop reason when the retry's proposal is adopted, so the record and UI do not
   say "dropped" and "verified" at once. The header must not read "verifier 1/1 checks pass" next to "No patch
   proposed".
3. The retry prompt (`config/prompts/l3_patch_retry.md`) may state the expected answer form for a retried escalation
   proposal (escalate + corrected proposal); no steering toward a specific patch. Re-key replay fixtures if hashes
   change.
4. Note the benchmark / beacon effect (engine arms on semantic faults: an escalation can now carry a passing retry
   proposal; it is still never auto-applied, so verified/wrong-shipped counts should not change) in the build note.

Tests: call-1 escalation proposal refused → retry plain patch passes → escalation with model question + proposal;
retry fails → question-only keeps the model question; drop reason cleared on adoption. Full gates green. Owner step
after: keyed `make record-frontier SCENARIO=gross_to_net`, then the keyed walkthrough re-run (deck 5 must reach
Approve → r2 → verifying sync → ● Working).

### REQ-069 — Demo walkthrough round 3: the dry-run gate checks every failing check; release numbers; rollback wording (2026-11-01)
Status: DONE (sbx1 2026-11-01 -- all 6 items code-done, full gates green; owner steps: keyed record-frontier gross_to_net then keyed walkthrough re-run to close)
Owner keyed browser walkthrough on `6f72867a` (Contributor, blank-slate seed, 2026-11-01 08:27-08:37). Findings and
screenshots: `tmp/walkthrough-6f72867a/FINDINGS.md` (gitignored; read in place from the main checkout, do not edit
`tmp/`). REQ-068 items 2, 4, 5, 6, 7, 8 are confirmed fixed live; keep them working. Fix:

1. **The proposal dry-run gate checks only the climbed entity (engine correctness).** Deck 5 live: Harbor's spec has
   two `money_invariant` checks against `$.gross_sum` (Reservation `total_amount`, FolioCharge `amount`); both fail
   under `gross_to_net`. The model's proposal repointed only `/entities/Reservation/checks/3/compare_to` to
   `$.net_sum`; the dry-run said "tested on the current records: the checks pass" and the verifier "1/1", Approve
   shipped r2, the verifying sync failed `money_invariant` (FolioCharge) and the engine rolled back r2 → r3. The
   gate must evaluate every check that failed in the break's sync (all entities) against the patched spec on the
   break's records, and pass only if all of them pass; a partial proposal gets the existing one retry with the exact
   error (which checks still fail), then is dropped. The verifier gate must not report a pass for a patch that leaves
   a failing check failing. No prompt tuning to steer the model to a specific answer and never synthesise or extend
   a proposal in code; the prompt may state the facts the gate enforces (all failing checks must pass). Note in the
   build note how this changes the dev benchmark / beacon engine arms (stricter gate: fewer proposals offered, never
   more), and whether a re-run is needed. Re-key replay fixtures if prompt hashes change.
2. **Release number before Approve is one too high (REQ-068 item 3 regression).** First occurrence reads "Approve to
   ship it as release r3" then ships r2; Activate-r1 second occurrence reads "r4" then ships r3. The pending
   candidate release already exists, so `max(version)+1` counts it. Show the version the candidate will actually
   ship as (the candidate's own version when it exists). Tests for both cases.
3. **After Activate r1 the connector header still shows r2** (the Releases table correctly marks r1 active). The
   header release and "live since" must follow the active release.
4. **After an automatic rollback the repair page reads as if nothing happened:** "No patch proposed — nothing
   verified to apply", "roll back to r3 stays available", generic headline. Say the approved change failed its
   verifying sync (name the check) and was rolled back automatically, and that the question is still open
   (docs/demo.md already describes this behaviour).
5. **Knowledge Decisions reads "Rolled back Rolled back r2 → r3"** (twin of the "Approved Approved" fix).
6. **PABX "Record layout" row prints raw JSON** (`{"DR": ["DA", "TI"], "PS": [...]}`); show it readably.

Tests: unit/UI tests per item (item 1: two-entity money checks, partial proposal refused with the failing check
named, full proposal passes); existing e2e testids keep working; full gates green. Owner step after: keyed
`make record-frontier SCENARIO=gross_to_net` if the prompt hash changed, then the keyed walkthrough re-run.

### REQ-068 — Demo walkthrough round 2: honest deck 5 Approve path, remaining UI and wording fixes (2026-11-01)
Status: DONE (sbx1 2026-11-01 -- all 8 items code-done: simulator net_sum under gross_to_net (Harbor+LegacyStay) with dry-run/verifier Approve path + replay re-keyed + docs; L3 entity-first raw records; candidate max+1; L0 learned wording; vv fix; connectors layout + Playwright; PABX plain state/filtered postings/heartbeat-on-flip; small cuts; full gates green; owner steps: keyed make record-frontier SCENARIO=gross_to_net then keyed browser walkthrough re-run to close)
Owner keyed browser walkthrough on `338a2ee1` (Contributor, blank-slate seed, decks 1-7 + closing, 2026-11-01
02:12-02:30). Findings and screenshots: `tmp/walkthrough-338a2ee1/FINDINGS.md` (gitignored; read in place from the main
checkout, do not edit `tmp/`). Script to match: `tmp/demo-flow/demo-flow.md` §5.2 (read in place). REQ-067 items 1, 2,
4, 5, 6, 7, 9 are confirmed fixed live; keep them working. Fix:

1. **Deck 5 has no honest Approve path (owner decision: the vendor reports a net total).** `gross_to_net` multiplies
   `total_amount` / `amount` by 0.9 but keeps `meta.gross_sum` and sends no net total, so no single spec change makes
   the money check pass; question-only was the correct answer. Live, the model proposed
   `replace /entities/Reservation/checks/3/compare_to → $.net_sum`, dropped by the dry-run because the field does not
   exist. Change the **simulator only**: under `gross_to_net` Harbor (and the other vendor it applies to) also reports
   the page's net total next to `gross_sum` (`meta.net_sum`, the sum of the net values on that page), the way a real
   vendor that switched to net would. Do not add `net_sum` to the baseline response or to `simulators/docs/harbor.md`
   (the docs describe the pre-change API). No engine tuning, no prompt edit for this, never synthesise a proposal in
   code. Check that a proposal repointing the money check at `$.net_sum` passes the existing dry-run gate and the
   verifier, so Approve → release r2 → verifying sync → ● Working works; record it as a business decision, never
   learned (existing behaviour). Keep `invalid_patch_trap` and the benchmark scenario semantics intact: say in the
   build note whether the dev benchmark / beacon fault catalogue sees the new field and whether any scorecard or
   hidden case needs a re-run (do not regenerate held-out files or `SHA256SUMS`). Update the replay fixture and
   `docs/demo.md` deck 5 as needed.
2. **The L3 prompt shows the wrong entity's raw records.** For the Reservation `total_amount` question the
   "Redacted raw vendor records for the entity" block lists FolioCharge rows (`charge_id`, `amount`). Show records of
   the entity the drift is on. Engine fix with a unit test.
3. **Deck 3 second occurrence, before Approve, reads "Approve to ship it as release r2"**, then ships r3 (r2 already
   exists). Show the release number it will actually get.
4. **Deck 3 second occurrence chapter 2 reads "Fixed by a known rule — no AI needed"** although the learned fix
   answered at L0 ("Known fix: held here", "Rule: Not needed"). Say it was the learned fix (the script's words for
   L0). The Recent repairs row for the L0 repair also reads "Rule page_to_cursor"; make it say learned / known fix.
5. **Builds row shows "vv1" / "vv2" / "vv3"** (`ui/app/connectors/[id]/page.tsx:379` prefixes "v" onto a value that
   already starts with "v").
6. **Connectors list layout at 1440 px:** a Working connector's state line ("in sync · Complete 3 s ago  last sync 5 s
   ago") runs into the Last-sync column and overlaps "102 records, every check passed"; stray "–" marks in the
   Model-tokens cell of a connector with 0 tokens over 2 repairs; "Discovered vendors" sits outside the content gutter.
7. **PABX panel:** the Link state row prints raw debug text (`up · accepted=2 dropped=0 layouts={"DR": [...]}`); show
   plain state (up/down, re-handshaking). "Postings arriving: 420 received · last 11 m ago" right after the build:
   the time must be when the relay received the posting, not a seeded timestamp. The resync burst arrived while the
   panel still read "down": the new postings must arrive after the link is back up (the "postings resume" beat).
8. **Small cuts:** sync-runs table jams Records into Fingerprint ("420572dedd7…"); Releases cell reads "verifier
   passed active" with no separator; Knowledge Decisions reads "Approved Approved page_to_cursor → r3"; chapter 2 reads
   "Small model: tried" when L2 was skipped (say skipped); "live since" shows the release's creation time instead of
   when it went live.

Tests: unit/UI tests per item, Playwright layout assertions for 6, existing e2e testids keep working, full gates green.
Owner step after: keyed walkthrough re-run.

### REQ-066 — Beacon protocol pins the frontier model (2026-11-01)
Status: DONE (sbx1 2026-10-31 -- freeze writes beacon-frontier-model (+ agent form when it differs), parse reads both, bench-beacon refuses on mismatch, real run requires the line, verify-beacon/EVIDENCE.md show the pin, docs updated; 9 new unit tests, full python gates green; owner step: keyed rehearsal/real run exercises the gate live)
The owner switched every frontier call to Muse Spark 1.3 Contributor (`.env` `FRONTIER_MODEL=muse-spark-1.3-contributor`,
price entry in `cca4eb70`). The frozen beacon protocol (`experiments/beacon/protocol.md`, written by `freeze()` in
`bench/beacon_run.py`) names the round, faults, arms and rules but not the model, so the freeze cannot prove the model
was fixed before the random value existed.
Items:
1. `freeze()` writes a `frontier-model: <id>` line (from `autobridge.config.frontier_model()`, the same resolution every
   arm uses, e.g. `muse-spark-1.3-contributor`), and the agent arm's resolved opencode model alongside it if it differs
   in form (`meta/<id>`). `parse_protocol` reads it.
2. `bench-beacon` (real run and rehearsals) refuses to start, with a clear message, when the resolved model differs
   from the protocol's line. A protocol without the line (older rehearsals) still parses; a real run requires it.
3. `verify-beacon` / EVIDENCE.md show the pinned model. Docs (`docs/benchmark.md` beacon section) mention it.
Tests: freeze writes the line; mismatch refuses; missing line on a rehearsal parses. Do not freeze, tag `eval-freeze`,
or commit anything under `experiments/beacon/` except labelled rehearsals.

### REQ-067 — Demo UI: fix the walkthrough findings and make the wording match the demo script (2026-11-01)
Status: DONE (sbx1 2026-10-31 -- all 10 items code-done: decision-box question wraps unclamped; deck-3 second occurrence documented as Activate-r1-then-break (rollback would evict learning); l3_patch/l3_patch_retry v2 ask for the proposal + replay fixture re-keyed; names shrink at word boundaries via container query; approved reads shipped-rN/rollback-rN-1; build copy + r1 automatic-verifier-passed label; in-flight counter reads counted-when-answers; PABX per-agent postings + link_resync burst=5; error/degrading-awaiting reads syncs-paused; chapter3 decided + reuse-x + approve-preview wording; new/updated unit+UI tests + Playwright assertions (items 1/4/9), full gates green; owner step: keyed browser walkthrough re-run to close)
Owner keyed browser walkthrough on `a172e09f` (blank-slate seed, decks 1-7 + closing, 2026-11-01 00:45-00:55). Findings,
screenshots and the per-deck result are in `tmp/walkthrough-a172e09f/FINDINGS.md` (gitignored; read in place from the
main checkout, with the `*.jpg` next to it). The demo script the UI must match is `tmp/demo-flow/demo-flow.md` §5.2
(read in place; do not copy it into the repo, do not edit `tmp/`). Fix every finding there:

1. **Deck 5 decision box truncates the model's question** to "Should..." (repair page, NEEDS YOUR DECISION box). Show
   the full question (wrap; no ellipsis on the question or the rationale).
2. **Deck 3 second occurrence shows nothing.** After the approved r2 (cursor), a second `make demo-break
   SCENARIO=page_to_cursor` is not a drift, so the scripted "resolves at L0 (known fix), reused 1×" beat cannot happen.
   Make the second occurrence real and honest: the learned fix must actually be reused at L0 with 0 tokens and the
   Knowledge page must then read "reused 1×". No faked rows. Choose the mechanism (for example: roll back to r1 from
   the repair page, then inject again; or whatever reproduces the same drift on a live connector), document the exact
   presenter steps in `docs/demo.md`, and report them in the Resolution so the owner updates the deck script.
3. **Deck 5 Approve path is luck.** This run the model escalated with a question and no proposal, so only Deny showed
   and Approve -> Green could not happen. When L3 escalates a semantic business decision, the model must also return
   the concrete change the operator could approve (the existing optional `proposal` in `config/prompts/l3_patch.md`):
   make the prompt ask for it whenever one concrete spec change would apply the other reading. Never invent a proposal
   in code. Also explain the 2 frontier calls (10,035 tokens) on this escalation vs one call in earlier keyed runs, and
   fix it if the second call is not needed.
4. **Connector names wrap mid-word** on the Connectors list ("HarborPM / S", "MeridianP / MS") at 1440 px. Names wrap
   at word boundaries only (or shrink), at 1440x900, the demo layout.
5. **Wrong release numbers after Approve** on the repair page: right after r2 ships it says "Approve to ship it as
   release r3" and "roll back to r2 stays available". After approval it must read r2 shipped, roll back to r1.
6. **Build form copy contradicts behaviour:** "You approve release r1 · Nothing syncs into your systems before that" /
   "Nothing syncs before you approve the release", but r1 goes live and syncs immediately ("Approved by —"). Make the
   copy say what happens (the verifier-passed r1 goes live automatically; later changes need your approval), and
   show who/what approved r1 in the Releases table instead of "—".
7. **Deck 5 live counter reads "0 tokens so far"** during the model call. Show tokens as they stream, or show
   "tokens counted when the model answers" instead of a 0 that looks wrong.
8. **PABX "Postings arriving: 408 received · last 6-7 m ago"** on a connector built a minute earlier, and unchanged
   through the link drop: the timestamp must be this connector's last received posting, and deck 7 must show postings
   resuming after the reconnect (the script's "postings resume ... nothing lost, nothing duplicated"). If the
   simulator emits no postings after the reconnect, make the `fias_link_resync` break emit some (counts must show
   nothing lost, nothing duplicated).
9. **Harbor shows "last sync 3 m ago"** while it waits for a decision. If syncs pause on purpose, say so on the
   connector ("syncs paused until you decide"); otherwise keep syncing.
10. **Wording pass:** every label on the screens the demo shows (Connectors, connector page, build form and build
    progress, Repairs list, repair page, Knowledge) matches the terms in `tmp/demo-flow/demo-flow.md` §5.2 (connector
    states Working / Degrading / Error, the repair page's four sections, "fix ready", "needs your decision", "business
    decision by Eric", "learned from approval ... reused N×"). List any place where the UI and the script disagree and
    which one you changed.

Tests: unit/UI tests per item (Playwright where it is a rendering issue: full question visible, no mid-word wrap at
1440 px, release numbers after approve). `data-testid`s used by `tests/e2e/test_06_ui_journey.spec.ts` keep working.
Full gates green. The owner reruns the keyed browser walkthrough to close.

### REQ-065 — Remove REQ-064 B2 (L3 before L4 for non-semantic classes); keep B1 (2026-10-31)
Status: DONE (sbx1 2026-10-31 -- B2 removed, B1 kept: engine/ladder/job/live/bench back to semantic-only L3, repair+engine diff vs 5d6d3977 empty, 4 adapted tests, full gates green; owner step: keyed make bench-beacon REHEARSE=6599930 FAULTS=12 TRIALS=1 (rehearsal 5), then close)
Owner decision after rehearsal 4 on `450bb445` (`tmp/beacon-rehearsal-6599930-r4/`, gitignored, read in place): B2 is
rolled back because it did not improve results (engine 5/12 either way; L3 fixed 0 faults and added ~2k tokens/fault).
Owner in-process replay of the L3 context showed why: instance 1 (`harbor` ENUM_NEW_VALUE) resolved entity `status` (a
field) so 0 patchable pointers; instance 6 (PAGINATION_CHANGE) signal names no entity so the evidence was `{}`;
instances 4 and 9 (`fias-pabx`) got no evidence (the FIAS beacon derive never attaches `l3_evidence`, and the builder
finds 0 raw PABX records); instance 2 got full evidence and its L3 patch was blocked by the verifier. B1 (silent
corruption detection) stays: it turned instance 7 into a pass and instance 5 from a silent Complete into a question,
with 0 tokens and no new signal on the dev scenarios or demos.

Items:
1. Remove B2 from `450bb445`: the ladder/controller/job/live/bench paths go back to the pre-REQ-064 behaviour (L3 only
   for SEMANTIC_VALUE_DRIFT/UNKNOWN with engine-enumerated hypotheses, "deterministic first, models last"). This
   includes `_L3_PROPOSE_CLASSES`/`_L3_CLIMB_CLASSES` and their use, `prepare_detected_inputs`,
   `build_detected_l3_evidence` (and helpers only it uses), the deferred detected climb in `_run_repair_climb`, the
   `detect_repair.py` B2 wiring, and the bench phase-2 change in `bench/ablation.py`. Remove or adapt the B2 tests in
   `tests/unit/test_autobridge_req064_l3_before_l4.py`; keep every B1 test.
2. Keep B1 exactly: `mapped_field_emptied` -> SCHEMA_RENAME and `money_scale_shift` -> SEMANTIC_VALUE_DRIFT
   (`drift/signals.py`, `drift/classifier.py`, the `baseline_summary` detector input) and the B1 parts of the bench
   wiring. A detected class with no rule escalates at L4 with its question, 0 model tokens (REQ-063 behaviour).
3. Put the ladder docs back ("L3 runs for semantic drift / UNKNOWN after the enumerator"); note in
   `docs/architecture.md` that B1 signals exist and that non-semantic L3 was tried and removed (REQ-064/065).
Done when the diff against `5d6d3977` in `src/autobridge/repair/` and `src/autobridge/engine/` contains only what B1
needs, full gates are green, and the owner reruns `make bench-beacon REHEARSE=6599930 FAULTS=12 TRIALS=1`
(rehearsal 5: expected engine 5/12, ~0 frontier tokens except semantic climbs, 0 wrong). Do not freeze, tag
`eval-freeze`, or commit anything under `experiments/beacon/` except labelled rehearsals.
Resolution: `src/autobridge/repair/` + `src/autobridge/engine/` restored to `5d6d3977` (B2 gone: `_L3_PROPOSE/_CLIMB/_NEVER_CLASSES`, `prepare_detected_inputs`, `build_detected_l3_evidence`, deferred detected climb, live B2 deferral); `bench/ablation.py` keeps only the B1 `baseline_summary` detector input with the semantic-only phase-2 gate restored; `docs/architecture.md` ladder paragraph restored + B1/removal note; B2 test file replaced by `tests/unit/test_autobridge_req065_deterministic_first.py` (4 tests pinning the restored behaviour).

### REQ-063 — Beacon grading: no-drift runs complete, escalation questions count, answer keys match what the connector reads (2026-10-31)
Status: DONE (sbx1 2026-10-31 -- all 3 items code-done: no-signal Complete + question carried/graded both ways + generator fixes with sanity gate; full gates green; owner step: keyed make bench-beacon REHEARSE=6599930 FAULTS=12 TRIALS=1 in the sandbox, then close)
Owner rehearsal 2 on `441efe7f` (after REQ-062): `make bench-beacon REHEARSE=6599930 FAULTS=12 TRIALS=1` in the
sandbox (keyed). Output in `tmp/beacon-rehearsal-6599930-r2/` (gitignored; read it in place from the main checkout).
REQ-062 holds: every row is gradable with a stated reason. Results: frontier_agent verified 5/12, frontier_oneshot 3/12,
autobridge_cold/warm 2/12, engine_no_knowledge 1/12, wrong shipped 0 for every arm. Three grading problems remain
(owner reproduced instances 0, 3, 5, 7, 10 in-process with `bench.faults.beacon_sync_pair`, faulted vs baseline
canonical records, eval seed from `summary.json`):

1. **No drift detected is graded as an escalation.** Instance 10 (`atlas`/`transport_auth`, `retry_after`, expected
   `no_patch`): the faulted sync is Complete and its canonical records equal the baseline, the detector reports 0
   signals, yet the engine arms record `detected UNKNOWN (0 signals) -> escalate at L4 (no rule)` and are graded
   `Blocked`. In production a sync with no drift signals simply completes; no repair run opens. frontier_agent gets
   credit for the same behaviour ("no_patch: complete without a patch").
2. **Escalation questions are lost.** Engine L4 escalations record `question=False`. frontier_agent escalations carry a
   question (`question=True`) but are reported `Blocked`, and `grade_arm_outcome` only accepts `degraded_question` as
   `Degraded` + question, so a no-patch escalation with one operator question can never pass a `degraded_question`
   instance (instance 3, frontier_agent).
3. **Answer keys expect a repair where the connector's output does not change.** Instance 0 (`atlas`/`dates`,
   `cutoff_hour 5`, expected `repair`): the faulted and baseline canonical records are identical under the pre-drift
   Atlas spec (30/30 reservations, 66/66 folios equal). Instance 3 (`meridian`/`semantic` decoy `netTotal`, expected
   `degraded_question`): records identical as well, the connector ignores the decoy field and stays correct.

Items:
1. When the detector reports no drift signals and the faulted sync is Complete, every engine arm reports `Complete`, no
   patch, no question (as production does), and the row says "no drift detected". The ladder is not called. On a
   repair instance this still fails, and the row must still read as a silent miss, never hidden.
2. An escalation (any arm) that carries an operator question reports `operator_question=True`; the engine's L4
   escalation carries the question the ladder wrote (if the ladder writes none, the row says so; do not invent one).
   `grade_arm_outcome` accepts a no-patch escalation with one operator question for `degraded_question`, whether it is
   reported `Degraded` or `Blocked`. `escalate` instances keep passing with or without a question.
3. Find the root cause of instance 0: is the cutoff fault not reaching the Atlas served records (generator/serving
   bug: fix it so the fault changes what the connector reads), or a no-op by design for that vendor (then its expected
   class is `no_patch`)? For the semantic decoy family, `Complete` with no patch is also accepted (the connector output is
   correct), alongside `degraded_question`. Add a bench-side sanity report, run on every rehearsal, that lists each
   instance whose faulted canonical output under the pre-drift spec equals the baseline while its key expects `repair`;
   such an instance is a generator bug (fail the rehearsal loudly). Keep the verifier free of engine imports (the check
   lives in `bench/`).

Out of scope (owner decides separately, do not change): engine detection misses on instance 5 (`atlas`/`rename`, every
folio `business_date` becomes empty, sync still Complete), instance 7 (`legacystay` `minor_major`, every amount ×100,
sync Complete), instance 4 (`fias_layout`), and the ladder going to L4 without trying L3 for detected classes with no L1
rule. Do not change engine detection or the ladder in this request.

Tests: unit tests per item (no-signal engine arm → Complete/no patch; question carried and graded both ways; the
sanity report flags an unchanged-output repair instance). The owner reruns the rehearsal on round 6599930 to close. Do
not freeze, tag `eval-freeze`, or commit anything under `experiments/beacon/` except labelled rehearsals.

### REQ-062 — Beacon evaluation: every arm really attempts every instance, and every row is gradable (2026-10-31)
Status: DONE (sbx1 2026-10-31 -- all 6 items code-done: shared beacon repair context + file prompts for frontier arms, PABX in-process spec + feed serving/derive, /beacon/records fail-closed (judge_error) never 500, wrong_patch only on released+failed with patch/checks named, per-tier token accounting + input/output/reasoning rows, Blocked rows name the step; 16 new/updated unit tests, full gates green; owner step: keyed make bench-beacon REHEARSE=6599930 FAULTS=12 TRIALS=1 in the sandbox, then close)
Owner rehearsal 2026-10-31 on `afd0283a`: `make bench-beacon REHEARSE=6599930 FAULTS=12 TRIALS=1` in the sandbox
(keyed). Output copied to `tmp/beacon-rehearsal-6599930/` (gitignored; read it in place from the main checkout:
`protocol.md`, `instances.json`, `grades.jsonl`, `summary.json`). This is the first beacon rehearsal ever run; the real
freeze (`eval-freeze`, one run, no retries) waits until a rehearsal is clean. Results:
autobridge_cold/warm verified 2/12, correct escalation 1/12, 0 tokens; engine_no_knowledge 1/12; frontier_agent 2/12,
0 escalations, `wrong_patch_released` 5, ~127k tokens/instance; frontier_oneshot 1/12, ~36k tokens.

Findings:
1. **Engine arms never attempt.** On 10/12 instances autobridge_cold/warm end `outcome Blocked (patch=False,
   question=False)` with 0 frontier and 0 small tokens, including `atlas`/`rename` (instance 5), a class the engine
   fixes 18/18 on the dev split. Suspect the beacon path does not run the same detect -> classify -> ladder -> verifier
   path as `bench/ablation.py` (REQ-024 engine arms), or the served fault never reaches the engine.
2. **PABX instances not gradable for engine arms.** Instances 4 and 9 (`fias-pabx`/`fias_layout`): "no in-process
   engine spec for vendor (outcome-graded only)".
3. **Verifier 500.** Instance 2 (`legacystay`/`nesting`) frontier_agent: `records grade failed: verifier
   /beacon/records: HTTP 500: Internal Server Error`.
4. **Wrong-patch count does not match the rows.** frontier_agent `wrong_patch_released` total 5, while its per-row
   `grade_reason`s read e.g. "outcome Complete (patch=False, question=False) not in ['repair']". Reconcile: a wrong
   patch released must name the patch and the failed verifier checks on its row.
5. **One-shot reports small tokens** (e.g. 27,613 `small_tokens` on instance 0) although it only calls the frontier
   tier. Explain or fix the accounting.

Items:
1. Engine arms on beacon instances run the same end-to-end path as the dev ablation (sync the pre-drift spec against
   the served fault -> detector -> classifier -> ladder -> verifier gate, phase 2 with the shared provider), with no
   case-supplied diagnostics. A row that is Blocked must say which step blocked and why.
2. Every beacon vendor has an in-process engine spec, PABX included (built from its docs as in the demo), so no
   instance is "outcome-graded only".
3. Fix the `/beacon/records` 500; a grading error is an explicit row label, never a silent 0.
4. `wrong_patch_released` is counted only from rows whose patch reached a release and failed the verifier; each such
   row names the patch and failed checks. The summary and rows agree (test).
5. Token accounting per arm is correct (one-shot: frontier only, unless a small-tier call really happened and is named).
6. Beacon runs use the same arms, prompts, context and 200k ceiling as the dev ablation (REQ-060/061, `afd0283a`).
Tests: unit tests per item; a rehearsal on round 6599930 (`FAULTS=12 TRIALS=1`) where every row is gradable with a
stated reason, and the engine arms attempt every instance. Do not freeze, tag `eval-freeze`, or commit anything under
`experiments/beacon/` except labelled rehearsals; the owner runs the real freeze.


### REQ-061 — Benchmark: the frontier agent arm runs the same model and returns gradable output (2026-10-31)
Status: DONE (sbx1 2026-10-31 -- all 4 items code-done: derived meta/<FRONTIER_MODEL> model (always -m), stdin=DEVNULL on all CLI drivers, exit-1 reason from stdout error events + tail, live preflight gating agent rows; 10 new/updated unit tests, full gates green; live check: preflight ok (meta/muse-spark-1.3, 17878 tokens) + page_to_cursor agent row ran=True same model 43467/1648/4917 tokens with real page->cursor patch; owner step: keyed BENCH_TRIALS=1 make bench-ablation re-run)
Keyed run 2026-10-31 on `d9317e15` (after REQ-060): `frontier_oneshot` is valid (5/6 fixable fixed, 2/6 held back,
0 wrong shipped), but `frontier_agent` produced nothing on 12/12 rows:
- Every row: `OpenCodeCliError: opencode CLI failed with exit 1:` after ~3 s, stderr empty. The driver
  (`bench/baseline_coding_agent.py:OpenCodeCliDriver.run`) raises with stderr only, so the reason (likely on stdout,
  `--format json` error events) is lost.
- `OPENCODE_MODEL` is unset, so `-m` is omitted and `opencode run` falls back to its default model. In the sandbox that
  default is an OpenAI model with no valid key (`invalid_api_key`, `api.openai.com/v1/responses`); this explains the
  earlier 12/12 `no_output` rows. The repo's `opencode.json` declares provider `meta` with `muse-spark-1.3`.
- Owner probe: `opencode run --format json ...` launched from Python `subprocess.run(capture_output=True)` in the
  sandbox hung past 90 s even for "Reply with ok" (with and without `-m meta/muse-spark-1.3`), while the same command
  from bash returned. Likely waiting on an inherited stdin; check.

Items:
1. **Same model.** When `OPENCODE_MODEL` is unset, the bench derives it from `FRONTIER_MODEL` in the `provider/model`
   form opencode accepts (`meta/muse-spark-1.3`); the agent arm's recorded model must equal the one-shot's (the existing
   `model_mismatch` guard stays). Document it in `.env.example` and `docs/benchmark.md`.
2. **No hang.** The driver runs `opencode` with `stdin=subprocess.DEVNULL` (and whatever else the probe shows is
   needed, e.g. the cwd having the provider config), so it never waits on input.
3. **Failures show their reason.** `OpenCodeCliError` carries the stdout tail and the parsed `error` events; the
   ablation row's `error` names the actual cause. Find and fix the exit-1 cause seen on `d9317e15`.
4. **Live preflight.** Before the ablation's agent rows, one live `opencode run` must return text, the model id and
   token usage; if not, the run stops with the reason (no 12 empty rows).
5. Tests: unit tests for 1-4 (fake CLI script for exit-1 + stdout error, stdin closed, model derivation). Live check in
   the sandbox: the preflight passes and one agent scenario (`page_to_cursor`) returns a gradable row.
Owner step: keyed `BENCH_TRIALS=1 make bench-ablation` re-run in the sandbox.


### REQ-060 — Benchmark: the frontier arms get the engine's repair task, no token limit, agent arm and learning curve fixed (2026-10-31)
Status: DONE (sbx1 2026-10-31 -- all 6 items code-done: repair-task context + patch/escalate grading, 0 = no limit + token stats, live-CLI parsing via session row + fixture, learning-curve live fix, docs; full gates green + BENCH_TRIALS=1 rehearsal 12/12 matched; owner step: keyed re-run in the sandbox)
Keyed run 2026-10-31 on `03a02f55` (`BENCH_TRIALS=1 make bench-ablation`, sandbox) after REQ-052. Owner review of the
prompts and data sent to the models found the frontier arms were asked a different question from the engine:
- `config/prompts/bench/frontier_oneshot.md` and `frontier_agent.md` say "Write the ConnectorSpec": a whole spec from the
  docs. They never receive the pre-drift spec (`bench/context.py:build_bench_context` uses it only to pick the
  vocabulary version) nor any evidence of the fault (failed sync, served wire). The docs describe the pre-drift API, so
  a repair is impossible: one-shot 0/6 fixable (its 2 verifier passes are `rate_limit_429`/`transient_503`, where the
  wire never changed).
- The only answer format is a spec, so a frontier arm cannot hold back: one-shot "unfixable held back" is 0/6 by
  construction; the agent's 6/6 comes only from producing nothing.
- `docs/benchmark.md` ("both frontier arms receive exactly the context the engine has") is not true today.
- Cutoffs: the 20000 shared ceiling counts input + output; the input alone is ~10.3k tokens, a whole spec + reasoning
  is 4-7k more, and Meridian's longer docs pushed 4/12 one-shot rows to `budget_exhausted` before or during the call.
- `frontier_agent`: 12/12 rows `no_output`, model `unknown`, 0 tokens, although `opencode` ran 25-160 s each.
- Learning curve crashed: `AttributeError: 'OpenCodeCliDriver' object has no attribute 'default_model'`; the table in
  `experiments/learning_curve.md` is still the fake driver's.

Items:
1. **Same task as the engine.** Both frontier arms get, in addition to today's bundle (schema, capabilities, a
   different-vendor example, the case's docs): the pre-drift spec the engine arms sync, and the evidence of the
   faulted sync the engine's detector sees (outcome, unmet criteria / failed checks, the served response sample for the
   affected operation(s)), nothing more (no drift class, delta, hypotheses, `expected`, or anything under `verifier/`).
   The answer is what L3 may answer: a JSON Patch on that spec, or an escalation with one question (optionally a
   proposal). The agent keeps its tools and the sandbox URL. A unit test asserts the frontier arms and the engine arms
   receive the same spec + evidence (content hash), and the prompt files stay in `config/prompts/bench/`.
2. **Graded the same way.** A frontier patch is applied to the pre-drift spec and goes to the verifier like an engine
   patch (fixable fixed / wrong shipped); an escalation on an unfixable scenario counts as held back; an escalation on a
   fixable one counts as not fixed. The split table and quality table keep their columns.
3. **No token limit for now (owner 2026-10-31).** `token_ceiling` (and `BENCH_TOKEN_CEILING`) **0 = no limit**, and 0 is
   the default for every arm: `Budget` skips the token check when its limit is 0, `HarnessInputs` accepts 0, and
   nothing is ever `budget_exhausted` on tokens. The per-call output cap stays `FRONTIER_MAX_TOKENS` (131072; the API
   needs a value). The wall clock stays. Record per row input, output and reasoning tokens (when the provider reports
   them) so the owner can pick a limit from the results; the summary adds per-arm min / median / max total tokens.
4. **Agent arm produces gradable output.** Find why `OpenCodeCliDriver` returns no output, model `unknown` and 0
   tokens under the live `opencode` CLI in the sandbox, and fix the parsing (answer, model id, token usage). A unit test
   on a captured real `opencode run` output (stored as a fixture, no secrets) pins it.
5. **Learning curve runs live.** Fix the `default_model` crash; the live run writes `experiments/learning_curve.md`
   with the agent's real tokens per repeat, or fails loudly.
6. Docs: `docs/benchmark.md` describes exactly what each arm receives and the no-limit default.
Tests: unit tests for items 1-5; unkeyed `BENCH_TRIALS=1 make bench-ablation` rehearsal 12/12 matched. Owner step:
keyed re-run in the sandbox.


### REQ-059 — Blank-slate demo: the seed creates no connectors; every connector is built live from its docs (2026-10-30)
Status: DONE (sbx1 2026-10-30 -- blank seed + readiness checks, demo step-1 builds Atlas itself, UI empty states, docs deck order; make demo 8/8 from blank, test_09 5/5, test_08 five legs green live, full gates green; owner check: Aside run of the blank-slate decks)
Run **after REQ-058** (needs its build-page fix: a simulator pick resets every field). Owner decision (2026-10-30):
start from nothing — deck 1 Knowledge (nothing built, nothing learned) → deck 2 build **Atlas** from its OpenAPI on v1
→ ● Working, syncing with 0 model calls → deck 3 break Atlas (`page_to_cursor`) → L1 fix → Approve → deck 4 build
**Harbor** from its wiki → deck 5 `gross_to_net` → tested proposal → Approve → Green → deck 6 **Meridian** v1 refused
(Download capability request) → v2 → Working → deck 7 **PABX** **v2 refused** → v3 → Working → `fias_link_resync`.
1. `make demo-seed` creates **no connectors**: it resets to a clean, ready stack and checks it (every simulator up and
   on its baseline scenario, no connectors, no repairs, knowledge empty, no injected faults) and prints the opening
   frame ("0 connectors · ready to build"). The Connectors page's empty state leads with **"Build a connector"**.
2. `make demo` (terminal-only) still runs stand-alone: every step that needs a connector builds it itself (Atlas
   included), as the Meridian/PABX/Harbor steps already do.
3. Knowledge page with nothing built reads cleanly (each vocabulary "no connector built on it yet", "Nothing learned
   yet"); Connectors header "0 connectors".
4. Docs: `README.md` and `docs/demo.md` describe the blank-slate take with this deck order (deck 7 shows the refusal
   on **v2**, then v3).
Tests: seed leaves zero connectors and passes its readiness checks; `make demo` 8/8 from a blank seed; UI-identical
builds of all four from a blank seed (keyed and unkeyed) reach ● Working; REQ-055's layout check covers the empty
Connectors and Knowledge pages.


### REQ-058 — Refused build → "Download capability request": the engine drafts the gap for the build loop (2026-10-30)
Status: DONE (sbx1 2026-10-30 -- refusal facts on vocabulary-check, deterministic Markdown download, pick resets every field, developers-portal link; ui 294 pass, test_10 18/18 + test_06 green mocked, full gates green; owner check: deck 6 refusal, download, open the file)
Owner (2026-10-30): when a vocabulary refuses the docs, the product should draft the capability request itself (the
gap, the vendor's spec, acceptance tests) so "refused → new language version" needs no human writing in between,
only an approval of the result. For now: **prove the point with a download**; handing it to a coding-agent loop is
roadmap (deck 6 says so). UI only, **no new API and no model call** — everything comes from the refused build's
result/trace, the docs text already on the page, and `GET /capabilities`.
1. On the build page, when a build is refused by the vocabulary check (REQ-053 item 7 headline), show a secondary
   action **"Download capability request"** next to "Build with vN+1". It downloads a Markdown file
   `capability-request-<vendor>-<vocab>.md`, assembled deterministically from facts:
   - **Title & summary:** "<Vendor> docs cannot be expressed in ConnectorSpec <vN>: <refusal reason>".
   - **The gap:** what the docs need vs what <vN> can express — from the trace's `vocabulary_guard` entry
     (`refused_methods`, `protocol`, …) and `GET /capabilities` for <vN> (methods, auth, pagination, protocols, joins,
     body templates, event feeds, command templates): a two-column "needed / available in vN" table.
   - **The vendor's spec:** source (docs target / file name), size, and the operations/records the docs describe
     (names, methods, paths/record types), plus the full docs text in a collapsed appendix (secrets never included —
     credential names only, as today).
   - **Acceptance tests (generic, from the facts):** the same docs build on the new version and the first sync is
     `Complete`; every connector on <vN> keeps a byte-identical spec signature (the REQ-020 regression rule); the
     refusal on <vN> stays (refuse safely); the verifier's checks pass on the new release; a drift on the new
     connector heals on the ladder like any other.
   - **Format:** the same shape as an entry in `mds/REQUESTS.MD` (title, context, numbered fix items, tests), so it
     can be dropped into the build loop's queue as-is. Footer: "Drafted by AutoBridge from a refused build on <date>;
     no model was used."
2. Same for every refusal kind (POST bodies on v1, event feeds over TCP on v1/v2). Unit tests: the Markdown for the
   Meridian-on-v1 and PABX-on-v2 refusals contains the gap table, the operations, the acceptance tests, and no secret;
   deterministic (same input → same file). Must pass REQ-055's no-overlap check.
Owner check after: deck 6 — v1 refused → Download capability request → open the file → it reads like REQ-020.
3. **Build page: picking a simulator must reset every field it fills** (owner bug, 2026-10-30). In
   `ui/components/NewConnectorForm.tsx` the base URL (~377) and the vendor name (~376) are filled **only when empty**,
   so after the first pick they keep the previous simulator's values (owner saw AtlasPMS docs with base URL
   `edge-relay://queue/fias-commands` and name "PABX edge"). On every simulator pick, set docs, base URL (edge relay
   for the FIAS PABX, else the simulator's base), credential, vendor name and the default vocabulary for that
   simulator — values the user typed by hand for the **current** simulator are kept only until the next pick. Test:
   pick Atlas → PABX → Atlas → Harbor and assert every field each time.
4. **Show the docs link** the moment a simulator is picked: under the docs picker, the simulator's developers portal
   URL as a link that opens in a new tab (e.g. AtlasPMS → `http://localhost:8105/developers`; from `developers_url`,
   already in the simulator row). Test: link text/href per simulator, `target="_blank"` with `rel="noopener"`.


### REQ-057 — Connector page: spec viewer with a fixed height, fingerprints grouped and collapsed (2026-10-30)
Status: DONE (sbx1 2026-10-30 -- spec viewer fixed height + inner scroll + toc jump, fingerprints grouped/collapsed with drift links, Signature header + full-value tooltips; ui 284 pass, test_10 16/16 + test_06 green mocked, full gates green; owner check: Aside screenshots at 1440)
Owner review of the connector page (2026-10-30, Atlas). UI only, no new API.
1. **Spec viewer ("Spec, rN"):** the YAML block grows to the full spec length and makes the page very long. Give it a
   fixed height (≈ 60 vh, with a sensible min/max) with its own scroll; the section list on its left (spec_version,
   connector, source, operations, entities, capabilities, health, commands) scrolls **inside** the viewer to that
   section; Copy YAML and the release picker stay visible above it.
2. **Fingerprint:** one row per sync run, all showing the same fingerprint (`3520bd6c039e71e2 (Complete)` × 28+).
   Group **consecutive runs with the same fingerprint** into one line ("3520bd6c… · 28 syncs · Complete · 09:05 →
   09:32 PM"), with ▸ to expand the run ids; a **fingerprint change** (drift) starts a new group and is highlighted
   (e.g. "changed at 09:09 PM — drift 77b78e79", linking to the repair). The whole Fingerprint section is
   collapsible and **collapsed by default**, showing a one-line summary ("2 fingerprints · last change 09:09 PM").
3. Must pass REQ-055's automated no-overlap/overflow check (add the connector page with a long spec and 30+ sync runs
   to it). Keep existing `data-testid`s.
4. **Full value on hover, and labels for hashes** (owner, 2026-10-30): every truncated hash/id on the connector page
   (sync run fingerprint `3520bd6c…`, run ids, release signature, release ids) shows its **full value in a tooltip**
   on mouse-over (and is keyboard-focusable with the same tooltip). The Releases table's unlabeled trailing value
   (`4a5aa12461c6…`, the release's HMAC-SHA256 spec signature, `ReleaseHistory.tsx` ~55) gets a column header
   **"Signature"** and a tooltip saying what it is ("proves this release's spec is exactly the one that was verified
   and approved"). Same for "Fingerprint" in Sync runs ("shape of the vendor's responses; a change means drift").
   Explicitly also (owner): the **Spec** section — the release picker ("r1 — 0705e3b5 (active)") and the viewer
   header ("Spec — r1 (0705e3b5)") show the full release id and the full signature on hover, with the same
   "Signature" label/explanation; and the **Fingerprint** section (the grouped list from item 2) — each group's
   fingerprint and each expanded run id show the full value on hover, with the "shape of the vendor's responses"
   explanation on the section title.
Tests: grouping (same fingerprint merged, change starts a new group and links its drift), collapsed by default, spec
viewer height + inner scroll + section jump.


### REQ-056 — Repair page you can scan: plain-language summary per step, technical detail behind a toggle (2026-10-30)
Status: DONE (sbx1 2026-10-30 -- plain scan layer live: lookup tables, chapter plain headlines + tech toggles, ladder chips, dry-run line, rail line; ui 266 pass, layout 16/16, test_06 + s6 green mocked, full gates green; owner check: Aside screenshots at 1440 deck 3 + deck 5)
Run **after REQ-055** (same page). Owner: "I need a way for users to scan the page and get the summary right away.
Right now it's a lot of technical info, and it's very confusing … 'Semantic value drift' is still cryptic." UI only;
no new API (all numbers already come back from existing endpoints). Still **facts only** (REQ-054): no per-drift
narrative sentences — plain wording comes from **two small lookup tables + the real numbers**.
1. **Rule per step:** one plain-language headline + 2–3 key facts; everything technical goes behind a
   **"Technical details ▸"** toggle (closed by default) in that step. A reader of the storyline strip alone knows what
   happened, what the engine did and what is asked of them.
2. **Storyline strip** (top): per step a plain headline + one fact, e.g. deck 5: "Amounts no longer add up · 10% below
   Harbor's own total" → "Asked the AI model · rules couldn't decide" → "Accept net amounts? · tested · reversible";
   deck 3: "Paging changed · Atlas now returns cursors" → "Fixed by a known rule · no AI needed" → "Approve the fix ·
   verified · reversible".
3. **Step 1 — What happened** (convey: what broke, in business terms, and the impact). Headline from the plain drift
   name and the failed check's plain meaning; facts: the observed vs expected numbers from the check's evidence (e.g.
   "Reservation amounts are 10% below Harbor's page total (900.00 vs 1,000.00)"), records affected per entity, sync
   state and since when. Technical details ▸: drift class code, unmet check code, sync/evidence ids, wire before/after,
   evidence records.
4. **Step 2 — What the engine did** (convey: how far it had to go, whether AI was used, what was checked). Headline by
   the rung that held (e.g. "Fixed by a known rule — no AI needed" / "Rules couldn't decide, so it asked the AI
   model"). The ladder as five plain chips with outcomes: **Known fix · Rule · Small model · AI model · You**, the
   rung that held lit, AI chip showing calls + tokens. **Dry-run line (always, when a proposal exists):** "Our test on
   today's records: passed" or, after a retry, "The model's first idea used a field <vendor> doesn't send — our test
   rejected it; the second idea passed" (from REQ-055 item 4 data). Technical details ▸: rung reasons, funnel, model-call
   table, usage across runs, evidence graph.
5. **Step 3 — Recommendation** (convey: what the engine suggests, what changes, is it safe). Headline: the fix or the
   model's proposal in plain words; facts: **What changes** (the patch in plain words, from the check/field lookup,
   e.g. "Stop comparing reservation totals with Harbor's gross total"), **Tested** (verifier / dry-run result),
   **Reversible** (roll back to rN), and for a model proposal **"Your business decision — not learned for next time"**.
   Technical details ▸: Now → Proposed table, raw JSON Patch.
6. **Lookup tables** (one place in `ui/lib/`, unit-tested, every code used anywhere has an entry; unknown code → the
   code itself, never blank). **All wording is written by the LLM once, at build time (owner decision (a)):** you (the
   loop's model) generate the plain-language text for **every placeholder** — every drift class, every check kind,
   every repair state's headline and decision-rail line, every rung chip outcome, the "what changes" phrasing per
   patch shape (replace/remove/add on a mapping, a check, pagination, auth) and the dry-run lines — and ship it as
   fixed text in those tables. **No model call at runtime** for any of it (the page must not change a repair's model
   calls; deck 3 stays "0 model calls"). Plain words a hotel operator understands, no internal codes, ≤ 8 words per
   headline. List the full generated wording in BUILD-NOTES for the owner to review. Starting draft below (replace or
   improve freely):
   - Drift class → plain name: `SEMANTIC_VALUE_DRIFT` "Values changed meaning" · `PAGINATION_CHANGE` "Paging changed" ·
     `PAGINATION_BUG` "Paging is broken" · `SCHEMA_RENAME` "A field was renamed" · `STATUS_VOCAB` "Status codes
     changed" · `DUPLICATE_DELIVERY` "Records arrive twice" · `RATE_LIMIT` "The vendor is throttling us" ·
     `TRANSIENT_ERROR` "The vendor is briefly down" · `CLOUD_SLOW` "The connection is slow" · `AUTH_EXPIRED` "Access
     expired" · `AUTH_REVOKED` "Access was revoked" · `CAPABILITY_REMOVED` "The vendor removed something we use" ·
     `PROTOCOL_VIOLATION` "The device broke the protocol" · `LINK_RESYNC` "The device link dropped" · `ACK_TIMEOUT`
     "The device stopped answering" · `SESSION_LOST` "The device session dropped mid-command" · `STALE_COMMAND` "An
     outdated command arrived" · `AGENT_RESTART` "The on-site agent restarted".
   - Check → plain meaning: `money_invariant` "amounts don't add up to the vendor's total" · `count_invariant` "record
     counts don't match" · `no_loss` "records went missing" · `unique_key` "duplicate records" · `date_order`
     "dates are out of order" · `business_date_invariant` "postings land on the wrong business day" · `http_error`
     "the vendor's API returned errors" · `feed_unavailable` "the device feed is unavailable".
7. Same treatment for every repair state (propose, climbing, escalated with / without proposal, approved, rejected,
   rolled back) and the decision rail's top line. Keep all `data-testid`s (technical blocks keep theirs inside the
   toggle); REQ-055's automated no-overlap check must still pass.
Tests: lookup completeness (every drift class in `simulators/scenarios/` and every check kind has an entry); storyline
and step headlines for deck 3 and deck 5 fixtures; technical toggles closed by default and contain the old content;
dry-run line in both forms. Owner check after: Aside screenshots at 1440 of deck 3 and deck 5 — readable in 5 seconds.


### REQ-055 — Deck 5 keyed: Approve fails on the migrated DB; the dry-run passed for the wrong reason; repair-page contradictions (2026-10-30)
Status: DONE (sbx1 2026-10-30: all items code-done, 39e1d798 + bbb8797a; the owner keyed check runs in the owner's overnight sandbox run after the daily validation — nothing left for the loop)
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
5. **No dollar amounts in the UI; keep token counts** (owner, 2026-10-30: "rates may/will change, so cost does not
   mean much here"). Remove the Cost column and the "· $total" from the model-call table (`ui/components/
   ModelCallBox.tsx` ~91, ~100), the "Est. $…" in `ModelUsageView.tsx` (~120), and the per-call cost and "· $total" in
   `RungLedger.tsx` (~117, ~130). Keep calls and tokens (in / out / reasoning, totals, budget). The API, ledger and
   benchmark keep their cost fields unchanged (UI only). Update the affected UI tests.
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
Status: DONE (sbx1 2026-10-30 -- all 5 items code-done: L3-sized one-shot + truncation labels, agent model record/check + shared 600s wall-clock + timed_out, verifier-graded frontier attempts + quality table + frontier_detail, live-agent learning curve + verified column, Spark prices; full gates green unkeyed + 12/12 rehearsal; owner step: keyed BENCH_TRIALS=1 make bench-ablation, then close)
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

### REQ-089 — AR-7-2: control-plane writes are open when `AUTOBRIDGE_CONTROL_TOKEN` is empty, and the shipped defaults leave it empty (2026-11-03)
Status: DONE (sbx1 2026-11-03 -- all 4 items code-done: control-demo-token default in compose/local-up/.env.example + EDGE_TOKEN chain, lifespan warning/info lines, smoke 401/200 checks, warning+defaults unit tests + safety-model docs; full gates + live stack green; owner step: none codeable left)
Architecture Review cycle 7 (AR-7-2, High): `require_control_token` returns immediately when the token is unset or
empty (`src/autobridge/control/deps.py` ~51-63); Compose ships `${AUTOBRIDGE_CONTROL_TOKEN:-}` for `control`,
`worker`, `edge-relay` and `ui` (`compose.yaml` 56, 136, 234, 410) and `scripts/local-up.sh` leaves it unset, so
every state-changing route (approve, reject, rollback, inject-fault, connector create/build/sync) is open by
default, with no startup warning. The review's claim that the UI cannot send the token is not correct:
`ui/middleware.ts` injects `Authorization: Bearer` on write methods when the UI container has the token, which
narrows the fix to the defaults and the warning. The `SIM_ADMIN_TOKEN` pattern already exists (`sim-admin-dev-token`,
a labelled demo value, not a secret), and `EDGE_TOKEN` already chains to the control token. Small scope:
1. A labelled demo default for the control token everywhere the simulator token has one: `compose.yaml` (all four
   services, one value), `scripts/local-up.sh`, and any `.env.example`/README line that lists the demo defaults.
   An explicit value always wins. The default must not be a real credential and must never be committed as one.
2. `src/autobridge/control/app.py` `lifespan` (~67): when the effective token is empty, log one loud warning at
   startup that control-plane writes are open; when set, log that writes require the token (name only, never the
   value).
3. Prove every in-repo caller of a write route works with the default set, because the token is an existing
   feature they already handle: `make smoke` and `make demo` on a fresh clone, `tests/e2e/test_06_ui_journey.spec.ts`
   (UI approve through the middleware), the CLI, the edge relay drain and `bench/demo*.py`. Add to the smoke an
   unauthenticated `POST` to one write route (approve or inject-fault) asserting 401, and an authenticated one
   asserting success.
4. Tests: `tests/unit/test_autobridge_control_token.py` covers the warning path (empty → warning text present; set
   → no value in any log line). `docs/safety-model.md` states the default: writes require the token out of the box,
   demo value labelled, loopback binding still the default.
Then mark DONE after full gates pass. Do not freeze, tag or commit under `experiments/beacon/`.
Resolution: control-plane writes require AUTOBRIDGE_CONTROL_TOKEN out of the box (labelled demo default control-demo-token, explicit value wins); startup logs posture; smoke + full live stack verified.

### REQ-088 — AR-7-1: the `frontier_agent` arm's sandbox allows `bash`/`webfetch` and inherits the whole environment, so the agent can reach the answer key (2026-11-03)
Status: DONE (sbx1 2026-11-03 -- all 4 items code-done: deny-all permission config shared between sandbox file + OPENCODE_PERMISSION env, allowlist child env from scratch, preflight permission+allowlist proofs, flipped/added tests + docs; full gates green; owner step: keyed rehearsal, then close)
Architecture Review cycle 7 (`mds/FEEDBACKS.MD`, AR-7-1, Critical): the sandbox `opencode.json` the harness writes
(`bench/context.py` ~438-456) grants `"bash": "allow"` and `"webfetch": "allow"`; the agent is a host subprocess
(`bench/baseline_coding_agent.py` ~1010-1021) whose shell has the user's full filesystem and network reach, so the
repo checkout, `simulators/common/faultgen.py`, `verifier/`, `docs/fault-playbook.md` and the verifier's
`/beacon/records` are one command away. The child env (~990-1005) is a denylist copy of `os.environ`, so any other
secret and any inherited `OPENCODE_PERMISSION` (the ralph loop exports one that allows `bash`, `webfetch` and
`external_directory`) ride along. The agent's task needs none of this: the brief is answered inline ("Respond with
ONLY one JSON object", `bench/context.py` ~52) from the evidence bundle in the sandbox dir, and no file write, shell
or fetch is part of the contract. The persisted agent traces record no tool calls, so past rehearsal scores cannot
be audited either way. One root cause (the arm is confined by file-tool permissions only), small scope, the ablation
`frontier_agent` arm shares the driver and is covered by the same change:
1. `opencode.json` in the sandbox dir denies `bash`, `webfetch`, `websearch`, `task`, `skill`, `lsp` and
   `external_directory`; `read`, `glob`, `grep`, `edit` stay allowed inside the sandbox dir. The child env becomes
   an allowlist built from scratch, not a scrubbed copy: `PATH`, `HOME`, `PWD` (the sandbox dir), `TMPDIR`, `LANG`,
   `TERM`, the `XDG_*`/`OPENCODE_*` variables the CLI needs for its own config and auth store, and the frontier
   provider's key variable(s) the CLI reads for the pinned model; `OPENCODE_PERMISSION` is set explicitly to the
   same deny config so an inherited value can never loosen it. Nothing else crosses.
2. Preflight (`bench/beacon_run.py` `preflight_beacon`, next to the agent child `PWD` proof ~2572-2583): assert the
   written permission file denies `bash`/`webfetch`/`websearch`/`external_directory` and the child env contains no
   variable outside the allowlist (name check only, values never printed), before the first scored instance;
   rehearsals included. Fail loud with the offending key name or permission.
3. Tests: `tests/unit/test_autobridge_req081.py` ~249 currently asserts `bash == "allow"` — flip it; add the env
   allowlist test (an environment seeded with `VERIFIER_URL`, `SIM_ADMIN_TOKEN`, `AUTOBRIDGE_CONTROL_TOKEN`,
   `OPENCODE_PERMISSION` and an unrelated secret produces a child env with none of them and `PWD` = sandbox dir);
   the fake-agent path still parses the inline JSON answer.
4. `docs/benchmark.md`: state the arm's confinement as it is after this change (file tools inside the sandbox dir,
   no shell, no fetch, allowlisted env), replacing the current isolation wording.
Then mark DONE after full gates pass; the owner runs the keyed rehearsal. Do not freeze, tag or commit under
`experiments/beacon/`.
Resolution: agent confined to in-sandbox file tools with an allowlisted env; preflight fails loud before the first scored instance.


### REQ-028 — Benchmark-driven engine fixes (owner rehearsal 2026-10-29) (2026-10-29)
Status: DONE (2026-11-03 -- superseded: every item was re-verified under the beacon grading rework (REQ-062, REQ-063, REQ-078, REQ-083); the keyed bench-ablation is replaced by the keyed beacon rehearsal)
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
Resolution: folded into the beacon REQs; no separate keyed ablation is pending.

### REQ-024 — Make the held-out claim checkable, and prove the engine beats an untrained frontier model (2026-10-28)
Status: DONE (2026-11-03 -- superseded: the held-out split is retired; the beacon run (REQ-025, REQ-062..083) is the checkable claim and the engine-vs-frontier comparison)
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
Resolution: replaced by the beacon evaluation; its arms, grading and preflight live under REQ-062..083.

### REQ-021 — Repeatable demo: full reset, pinned vocabulary, and proof of when the frontier model is called (2026-10-28)
Status: DONE (2026-11-03 -- superseded in part by the beacon work: full reset, pinned vocabulary and the model-call proof are in (REQ-039/042/059/077); the owner's `make record-frontier SCENARIO=gross_to_net` capture is an owner step after the code freeze, not engine work)
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
Resolution: reset, vocabulary pin and frontier-call provenance landed under later REQs; the replay capture for the Harbor semantic step is recorded by the owner after the freeze.



### REQ-079 — Directive: §5 test hermeticity — full `make test` flakes with teardown DNS-guard errors on scheduler/activation tests (2026-11-02)
Status: DONE (sbx1 2026-11-02 -- two cross-test leaks fixed; full make test green, no DNS-guard trips)
Found by the 2026-11-02 directives audit (diff `d2a13125..b773926b`). Two consecutive full `make test` runs in sbx1: 3068 passed, then 4 (run 1) / 3 (run 2) teardown ERRORs — `tests/unit/conftest.py:88` asserts no real DNS lookups, and a background thread resolves `localhost` during whichever test is running (`test_autobridge_activation.py::*`, `test_autobridge_req030_scheduler_state.py::test_busy_reason_connector_not_found`). All affected tests pass standalone and pairwise (38/38, 3x). Same family as REQ-076 and the Cycle-6 `bench/stack_mode.py` 127.0.0.1 probe fix; a stale `.local/ACTIVE` (Oct 30, no live stack) is present in the worktree. The audited range touches no scheduler/thread/localhost code, so the leak pre-dates it: find the thread that outlives its test (scheduler tick, probe, or job worker resolving a `localhost` base/control URL), stop or join it at test end, and add a regression test. No retries or quarantines as the fix (REQ-076 rule). Until then `make test` is not reliably green, which blocks the §5 gate.
Resolution: (1) `_scheduler_loop` now joins the in-flight pass on shutdown instead of abandoning its worker thread (the thread resolved the default edge-relay URL after monkeypatch teardown, tripping the next test); (2) `run_beacon` placeholder credentials (ATLAS/HARBOR/BENCH_API_KEY) are save/restored via context manager instead of leaking process-globally (later localhost syncs then resolved for real); raw `os.environ` sets in unit tests converted to `monkeypatch.setenv`; 3 regression tests (scheduler join, placeholder restore, resume-path no-leak); full `make test` green with no teardown errors (autobridge 3069 @90.05%, verifier 139 @90.80%, simulators 422 @90.48%, UI 350).


### REQ-073 — Repair page and Sync runs UI fixes from the owner's demo run (2026-11-01)
Status: DONE (sbx1 2026-11-01 -- all 7 items code-done: per-repair model calls + empty state, single verifier verdict without duplicate label, plain hypotheses line, record-derived approval steps, no Reject/Rollback after approval + release-list rollback + no dev text, full-width sync-run note; full gates green; owner step: keyed walkthrough to close)
Owner demo run on the local stack, 2026-11-01 ~11:10, repair page of an approved L1 Atlas fix
(`/repairs/<id>#did`, 0 model calls) and the PABX connector page.

1. **"Model usage across runs" shows other repairs' tokens (bug).** The repair page appendix renders
   `GET /metrics/model-usage` (every run with model calls) and only highlights the current one. On a 0-call Atlas repair
   the only bar is Harbor's deck-5 run (`l3-generate-patch, l3-generate-patch-retry · 13,284 tok`) right under "Model
   calls 0". On a repair page show only this repair's model calls (filter server- or client-side); none → "No model
   calls in this repair". Keep any cross-run view off the repair page.
2. **Verifier tile contradicts itself** on an approved, verified fix: a bar glyph "━━/—", "checks pass on this patch",
   then "fail report ee21f3a2", "no visible checks recorded", and the label "VERIFIER, HIDDEN FIXTURES" under the title
   "Verifier". Show one consistent verdict from the verifier report (pass/fail, N of M checks when known, the report
   id once), never "fail" for a passing report; drop the duplicate label.
3. **Hypotheses tile is noise on a rule fix:** "Hypotheses · probes 0 · enumerated, then filtered", a stray "0" under
   the tiles, and "0 resolved without enumeration". When no hypotheses were generated, show one plain line (e.g. "no
   hypotheses needed: a rule matched") or hide the tile; never a bare "0".
4. **Approval steps go stale after approval.** The rail's step list on an approved repair shows "✓ Approval recorded",
   then "○ Release activated — waiting for activation", "○ Verifying sync — waiting for the next sync", "○ Sync
   Complete", yet "✓ Connector working". Derive each step from the real records (release active, first sync on the new
   release and its outcome) so a shipped fix shows all steps done.
5. **Once a patch is approved, remove "Reject patch" and the Roll back controls** from the repair page (rail and
   any sticky bar), for rule/learned fixes and business decisions alike (owner decision). Rolling back stays possible
   from the connector page (Releases → Activate an earlier release); where the repair page says "roll back to rN stays
   available" after approval, point there instead. Before approval, Approve / Reject / Deny stay as today.
6. **Developer text in the rail:** "Approve/Reject available only when decision is “propose” (current: approved)."
   (`ui/components/RepairActions.tsx`) and a raw "release id" text box next to "Roll back to". Remove; the state is
   already shown ("Approved ✓"). If a rollback control remains anywhere before approval, it picks a release from a list
   (r1, r2 …), never a typed id.
7. **Sync runs table (REQ-071 note) wraps over five lines** in the narrow Outcome column on the connector page
   ("link dropped, re- / handshaked · +5 / postings, 0 lost, / 0 duplicated"). Give the note room (e.g. a full-width
   line under the row, or a wider column); keep Records/Fingerprint aligned and the REQ-071 testids.

Do not edit the owner's uncommitted work in progress: `ui/components/ConnectorsTable.tsx`,
`ui/components/ConnectorStateBadge.tsx`, `ui/app/connectors/page.tsx` (+ its test); if `ui/app/globals.css` needs
changes, add them in a new block at the end of the file. Tests: UI tests per item (0-call repair shows no other run;
approved repair shows no Reject/Roll back; steps all done after a Complete verifying sync), existing e2e testids keep
working, full gates green. Update `docs/demo.md` where it promises rollback on the repair page. Owner step after: keyed
walkthrough.
Resolution: all 7 items code-done with UI tests per item; `docs/demo.md` rollback promises now point at the connector page; existing e2e testids kept (test_06, s6, test_10 layout, test_08 green mocked); full gates green (autobridge/verifier/simulators coverage gates + UI 340 + lint/typecheck/openapi).

### REQ-064 — Engine: catch silent corruption, and try L3 before escalating when no rule fits (2026-10-31)
Status: DONE (sbx1 2026-10-31 -- all 3 items code-done: B1a/B1b silent-corruption signals + existing drift classes, B2 L3-before-L4 with l3_patch context on live/bench/job paths + ladder docs; 26 new + 1 updated tests, full gates green; owner step: keyed make bench-beacon REHEARSE=6599930 FAULTS=12 TRIALS=1 in the sandbox (rehearsal 4), then close)
Owner rehearsal 3 on `5d6d3977` (after REQ-063): `make bench-beacon REHEARSE=6599930 FAULTS=12 TRIALS=1`, keyed, in
the sandbox. Output in `tmp/beacon-rehearsal-6599930-r3/` (gitignored; read in place from the main checkout). Grading is
now clean. Verified: frontier_agent 6/12 (~77k tokens/fault), frontier_oneshot 5/12, autobridge_cold/warm 4/12 (~330),
engine_no_knowledge 3/12; wrong shipped 0 for every arm. The engine loses on two patterns (per-row `grade_reason`):

- **Silent corruption, reported Complete** (no arm fixes these):
  - instance 5 (`atlas`/`rename` folio `business_date` -> `totalNet`): every folio's canonical `business_date`
    becomes `""`, 0 drift signals.
  - instance 7 (`legacystay`/`semantic` `minor_major` factor 0.5): every reservation `total_amount` and folio `amount`
    is ×100 (e.g. 174.69 -> 17469.00), 0 drift signals.
- **Detected, but escalated at L4 without trying L3:** instances 0 (SEMANTIC_VALUE_DRIFT after an L2 call), 1 and 8
  (ENUM_NEW_VALUE), 2 (SCHEMA_TYPE_CHANGE), 4 and 9 (`fias-pabx` SCHEMA_RENAME), 6 (PAGINATION_CHANGE): "no rule
  matched X at L4, escalated without a patch". Both frontier arms verified 4 and 9.

Items:
1. **Detection (B1).** Raise drift signals for both silent-corruption shapes, from the pre-drift spec and the run's own
   observations (no instance parameters, no answer keys, no verifier imports):
   a. a field the spec maps that was populated in the baseline/previous observations and is now empty or missing in
      most rows (the rename that leaves the old path empty);
   b. a money field whose values shift by a power of ten across most rows while record keys and counts stay the same
      (minor/major units).
   Each signal is classified into an existing drift class (or a new one if none fits; say which) so the ladder climbs.
   Thresholds documented and conservative: no new signal on the dev ablation's 12 scenarios where none fired before,
   the demo decks, or the blank-slate demo (existing tests stay green; add a regression test for each).
2. **L3 before L4 (B2).** When a drift class is detected and no L0/L1 rule produces a patch (L2 included where it runs
   today), the ladder calls L3 with the same repair context and prompt shape as today (`config/prompts/l3_patch.md`:
   patch or escalate with one question), within the existing budget/ceiling, before escalating. The verifier gate is
   unchanged: an L3 patch reaches a release only if it passes; otherwise the run escalates with the question. Record
   on the row which rung produced the outcome and its tokens. Unfixable dev scenarios must still be held back with 0
   wrong patches shipped.
3. Update the ladder description wherever the docs say L3 runs only for UNKNOWN/SEMANTIC (plan cross-refs, docs/,
   knowledge page copy), to: "L3 runs when a drift is detected and no deterministic rule fits".

Tests: unit tests for both detection shapes (fires on the faulted shape, silent on the baseline and on dev scenarios),
L3 called for a detected class with no rule (fake provider), and L3 not called when L1 fixes it. Full gates green. The
owner reruns `make bench-beacon REHEARSE=6599930 FAULTS=12 TRIALS=1` (rehearsal 4) to close. Do not freeze, tag
`eval-freeze`, or commit anything under `experiments/beacon/` except labelled rehearsals.
Resolution: engine detects both silent-corruption shapes (mapped_field_emptied wire-key-gone-dark → SCHEMA_RENAME; money_scale_shift exact power-of-ten with same keys/counts → SEMANTIC_VALUE_DRIFT, baseline-compared) and climbs L3 with the same repair context/prompt before escalating (structural proposals become verifier-gated candidates; ambiguity still escalates with the question); unkeyed engine-arm behaviour byte-identical on all 12 dev scenarios; docs say L3 runs when a drift is detected and no deterministic rule fits.

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

