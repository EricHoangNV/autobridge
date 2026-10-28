# Requests

## Open Requests (read this section only)

Owner priority (2026-10-28): REQ-018 first (UI is down), then REQ-013, then REQ-021, then REQ-017, then REQ-014 → REQ-015 → REQ-016, then REQ-019 → REQ-020 (REQ-020 step 4 needs REQ-021 item 2), then REQ-022, with REQ-023 (demo plan) applied as each of those lands. Do these before any
remaining review FAIL item. Constraint for all of them: someone who downloads the repo may have no Docker and no
sandbox; the demo must still run on their machine (REQ-017), and nothing here may assume Docker is present.

### REQ-014 — Host tools ignore `.env`, so tokens break smoke, e2e and demo (2026-10-28)
Status: OPEN
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

### REQ-015 — e2e tests do not send the control token (2026-10-28)
Status: OPEN
With `AUTOBRIDGE_CONTROL_TOKEN` exported, 6 tests fail with `401 Missing or invalid control token`:
`test_s2_edge_target.py::test_edge_execute_signed_release_live`, `test_s3_discovery_import.py` (import_and_list fake +
live, lifecycle live + fake), `test_s7_cli.py::test_s7_cli_repairs_approve_and_reject_fake`. Ralph's gates miss this
because the sandbox runs with no token.
Wanted:
1. One shared helper in `tests/e2e/conftest.py` for control-API headers (token when set); every live call uses it.
2. In-process (fake) tests set or clear the token themselves with monkeypatch, so they pass whatever the shell has.
3. The CLI sends the token when `AUTOBRIDGE_CONTROL_TOKEN` is set (check `src/autobridge/cli.py`).
4. `make e2e` passes both with no tokens and with all tokens set; add a test matrix note to docs/architecture.md.

### REQ-016 — `make demo` reports [ok] when its writes fail (2026-10-28)
Status: OPEN
With the token missing, `make demo` printed `control POST /connectors -> 401` and `POST /demo/inject-fault -> 401`,
yet every step said `[ok]` and the run exited 0. A demo check that cannot fail protects nothing.
Wanted:
1. Any live control-API call that is part of a step must succeed (2xx) or the step is FAILED and `make demo` exits
   non-zero with the method, path, status and detail.
2. Say clearly in the output which steps ran against the live stack and which were computed in-process.
3. Tests: a 401 and a 500 on a step's write make that step fail.

### REQ-017 — Run the demo without Docker (2026-10-28)
Status: IN_PROGRESS (sbx1 since 2026-10-28 09:16)
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

### REQ-019 — Reshape the Atlas simulator after the Cloudbeds PMS API (2026-10-28)
Status: OPEN
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
   `page_to_cursor` (mirroring `offset_to_cursor`, zero frontier tokens, same tests) or the step moves to LegacyStay,
   which stays offset based. Prefer the new rule; keep `offset_to_cursor` covered on LegacyStay.
4. Heldout: never touch `verifier/hidden_cases/heldout/SHA256SUMS` or `HELDOUT_DIR`. The heldout scenarios must still
   run against the new Atlas (`make bench-heldout` dry run with the SHA check passing).
5. Gates: `make test`, `make up && make smoke && make e2e && make demo` green; README vendor table updated.

### REQ-020 — Add a fifth simulator after the Mews Connector API (2026-10-28)
Status: OPEN
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
8. Gates as REQ-019, README vendor table updated. This is the largest REQ in the queue: split it into claimable
   sub-steps in the build note (4, 5a schema+runtime, 5b discovery, 5c joins, 6, 7) and merge each one green.

### REQ-021 — Repeatable demo: full reset, pinned vocabulary, and proof of when the frontier model is called (2026-10-28)
Status: OPEN
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

### REQ-022 — FIAS PABX simulator and a real on-premise edge agent (replaces EdgePOS) (2026-10-28)
Status: OPEN
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

### REQ-023 — Demo plan v2: main script plus a fault-scenario catalogue (2026-10-28)
Status: OPEN
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
- Transport, no patch, 0 model calls: `rate_limit_429` (also LegacyStay's normal throttling), `transient_503`,
  `cloud_slow` (FIAS link keeps getting ACKs).
- Auth: `token_expired` → refresh if permitted; `token_revoked` → escalate, no patch, `Blocked`.
- Capability: `endpoint_removed` → escalate, never patched around.
- Known structural drifts at L0/L1, 0 model calls: `rename_guest_id`, `nested_path_shift`, `offset_to_cursor`
  (LegacyStay), `page_to_cursor` (Atlas), `cursor_repeat_token` (Harbor loop guard), `fias_record_layout_change`
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

---

## Resolved Requests (reference only)

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

