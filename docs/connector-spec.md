# AutoBridge — ConnectorSpec v1/v2/v3

> Reference for the declarative connector document the runtime interprets.
> Every spec validates against `schemas/connector-spec.v2.json` before it is
> stored, executed or patched. Invalid specs are rejected with path-level errors
> (e.g. `entities.Reservation.fields.status.transform[0].args.map: missing`).
> Version 1 documents still validate and behave exactly as before; v2 adds
> POST operations, `body_fields` auth and `body_cursor` pagination (§10).

Related: [Architecture](architecture.md) §5, `src/autobridge/spec/models.py`,
`schemas/connector-spec.v2.json` (`schemas/connector-spec.v1.json` kept for
reference), plan §2.4.

## 1. File layout — `generated/connectors/`

Reference connectors are stored write-once via `src/autobridge/spec/registry.py`:

```
generated/connectors/<connector_id>/<release_id>/spec.yaml
                                          /build-report.json
```

`release_id` is `reference` for the baseline that must sync `Complete` in
`tests/e2e/test_02_reference_sync.py`. Releases are immutable; a change
creates a new directory.

| Connector | Vendor | Spec | Base URL (env placeholder) | Auth | Pagination | Status vocab |
|---|---|---|---|---|---|---|
| `atlaspms` | AtlasPMS | `generated/connectors/atlaspms/reference/spec.yaml` | `${ATLAS_BASE_URL}` → `http://sim-atlas:8000` in Compose, `http://localhost:8105` on the host | `api_key_header` `x-api-key` `ATLAS_API_KEY` (OAuth2 `Bearer` accepted as alternative) | page (`pageNumber`/`pageSize`/`$.total`, tokenless) | `canceled` → `cancelled`; `not_confirmed` unmapped → `Block` (no canonical counterpart) |
| `harborpms` | HarborPMS | `generated/connectors/harborpms/reference/spec.yaml` | `${HARBOR_BASE_URL}` → `http://sim-harbor:8000` / `8106` | bearer `HARBOR_API_KEY` (Harbor is permissive, but the spec still carries a `credential_ref`) | cursor (`cursor`/`next_cursor` base64) | opaque codes `H1`…`H5` |
| `legacystay` | LegacyStay | bootstrapped from `simulators/docs/legacystay.md` — no reference spec (Phase 2 S1, unseen-generalization vendor) | `${LEGACYSTAY_BASE_URL}` → `http://sim-legacystay:8000` / `8111` | bearer `LEGACYSTAY_API_KEY` (strict, missing/empty → 401) | offset (`limit`/`offset`/`next_offset`) | clear English (`confirmed`…) |

Both reference specs sync `Complete` with `ModelCall` count `0` — the runtime
never calls an LLM for a baseline sync.

Build report (`build-report.json`) records `connector_id`, `release_id`,
sha256 `signature` of the canonical JSON form, `built_by`, and any caller
metadata. Signature mismatches raise `ReleaseCorruptError`. The canonical
form omits spec-v2-only fields when they sit at their defaults, so a v1
spec hashes byte-identically to before v2 existed — committed v1 releases
keep verifying unchanged, while any v2 field actually set moves the
signature.

## 2. Top-level structure

```yaml
spec_version: 1
connector: { id: harborpms, vendor: HarborPMS, runtime_target: cloud }
source:
  base_url: ${HARBOR_BASE_URL}
  auth: { type: bearer, credential_ref: HARBOR_API_KEY }
  rate_limit: { max_rps: 5, retry: { statuses: [429, 503], backoff: exponential, max_attempts: 5 } }
  timezone_source: { kind: static, value: America/New_York }  # or { kind: field, path: $.property.tz }
operations:
  list_reservations:
    method: GET
    path: /reservations
    params: { property_id: "{{checkpoint.reservations}}" }   # optional checkpoint template
    pagination: { strategy: cursor, cursor_param: cursor, limit_param: limit, next_path: $.next_cursor, items_path: $.data, stop_when: [null_cursor, repeated_cursor] }
    checkpoint: { field: $.updated_at, mode: max }
  list_folios:
    method: GET
    path: /folios
    pagination: { strategy: cursor, cursor_param: cursor, limit_param: limit, next_path: $.next_cursor, items_path: $.data, stop_when: [null_cursor, repeated_cursor] }
    checkpoint: { field: $.posted_at, mode: max }
entities:
  Reservation:
    from: list_reservations
    key: $.reservation_id
    fields:
      reservation_id: { path: $.reservation_id }
      arrival_date:   { path: $.arrival_date, transform: [date_local, { tz: source }] }
      departure_date: { path: $.departure_date, transform: [date_local, { tz: source }] }
      status:         { path: $.status, transform: [{ name: enum_map, args: { map: { H1: confirmed, H2: checked_in, H3: checked_out, H4: cancelled, H5: no_show }, on_unknown: block } }] }
      total_amount:   { path: $.total_amount, transform: [money, { scale: 2 }] }
      currency:       { path: $.currency }
      guest_name:     { path: $.guest_name }
      property_id:    { path: $.property_id }
      updated_at:     { path: $.updated_at }
    checks:
      - { kind: unique_key }
      - { kind: no_loss, compare_to: $.total }
      - { kind: date_order, before: arrival_date, after: departure_date }
  FolioCharge:
    from: list_folios
    key: $.charge_id
    fields:
      charge_id:     { path: $.charge_id }
      reservation_id: { path: $.reservation_id }
      property_id:   { path: $.property_id }
      business_date: { path: $.business_date, transform: [business_date, { tz: source, cutoff: 4 }] }
      amount:        { path: $.amount, transform: [money, { scale: 2 }] }
      currency:      { path: $.currency }
      description:   { path: $.description }
      posted_at:     { path: $.posted_at }
    checks:
      - { kind: unique_key }
      - { kind: no_loss, compare_to: $.total }
capabilities:
  read: [Reservation, FolioCharge]
  action: []
health:
  canary: { every: 60s, operation: list_reservations, max_pages: 1 }
  fingerprint: { keys: true, types: true, pagination_shape: true }
```

### 2.1 `connector`

* `id` — slug `[a-z0-9_-]`, also the directory name under `generated/connectors/`.
* `vendor` — display name.
* `runtime_target` — `cloud | local | sandbox | edge` (`edge` is the Phase 2 S2
  simulated edge worker: executes only signed releases on the isolated
  `edge-net` — HMAC-signed with `EDGE_RELEASE_SECRET` when set, unkeyed
  integrity checksum otherwise (AR-2-4); `sandbox` is the Docker-isolated target in W3.6).

### 2.2 `source`

* `base_url` — `http(s)://…` or `${ENV_VAR}` placeholder (upper snake). The
  runtime expands it via `os.environ`; missing env → `KeyError`. Never embeds
  credentials (`user:pass@` is rejected).
* `auth` — credentials by reference only:

  ```yaml
  auth: { type: bearer, credential_ref: ATLAS_API_KEY }             # Authorization: Bearer <value>
  auth: { type: api_key_header, credential_ref: KEY, header_name: X-Api-Key }
  auth: { type: basic, credential_ref: BASIC_PASS, username: alice }    # Basic alice:<value>
  auth: { type: oauth2_client_credentials, credential_ref: OAUTH_SECRET, username: client_id, token_url: https://…/token, scopes: [read] }
  ```

  Spec v2 adds `body_fields` auth (tokens in the JSON body, never headers —
  see §10):

  ```yaml
  auth:
    type: body_fields
    body_fields: { ClientToken: VENDOR_CLIENT_TOKEN, AccessToken: VENDOR_ACCESS_TOKEN }
  ```

  Secret values are read from the environment at call time, never logged, never
  stored in the spec, release, evidence or API response.

* `rate_limit.retry` — `statuses` (e.g. `[429, 503]`), `backoff` (`exponential |
  linear | constant`), `max_attempts` 1..10. The `HttpClient` retries on those
  statuses with exponential backoff (`Retry-After` overrides when numeric,
  capped at 60 s per sleep; cumulative retry delay per sync job is bounded
  and trips `Blocked` with `sync_deadline_exceeded` instead of stalling).

* `timezone_source` — how the runtime resolves property-local tz for
  `date_local` / `business_date`:

  ```yaml
  timezone_source: { kind: static, value: America/New_York }  # all properties share one tz
  timezone_source: { kind: field, path: $.property.tz }        # tz varies per record (looked up from the record)
  ```

  IANA names or `UTC` only. A `field` kind is evaluated per record via JSONPath;
  missing → `UTC` fallback.

### 2.3 `operations`

Keyed by name (`[a-z][a-z0-9_]*`). Each operation is a `GET` (v1 and v2) or a
`POST` (v2 only, with a JSON body template — see §10) with:

* `path` — relative (must start with `/`, no `$` or spaces).
* `params` — query strings. Values may contain `{{checkpoint.<op>}}` templates
  resolved from the caller's checkpoint dict (e.g. incremental sync).
* `body` — v2 POST only: static JSON template for the request body (v2
  `GET` with `body` is rejected). Templates resolve like `params`, nested.
* `pagination` — see §3.
* `checkpoint` — `field` (JSONPath into the raw record) + `mode: max | min`.
  The interpreter computes the value over all fetched records for incremental
  cursors.

### 2.4 `entities`

Keyed by canonical name (`Reservation`, `FolioCharge` in v1; PascalCase).

* `from` — operation name that supplies the raw records.
* `key` — JSONPath to the canonical key (`$.reservation_id` / `$.charge_id`);
  used for deduplication and `unique_key` checks.
* `fields` — map of canonical field name → `{ path, transform }`:

  * `path` — JSONPath subset (root `$` plus child segments `$.a`, `$.a[0]`,
    `$.a[*]`, `$.a['x']`; no filters `?()`, no `..`, no expressions).
  * `transform` — ordered list of closed transforms (see §4). Forms accepted:

    ```yaml
    transform: money                              # bare name
    transform: [money, { scale: 2 }]             # name + args merged
    transform: [{ name: enum_map, args: {…} }]   # explicit mapping
    transform: [flatten, { name: money, args: { scale: 2 } }]  # chain
    ```

    Every transform is `{"name": …, "args": {…}}` after normalization. Adding a
    name is a plan change.

* `checks` — per-entity invariants that drive the `SyncRun` outcome (§5):

  ```yaml
  checks:
    - { kind: unique_key }                               # no duplicate keys
    - { kind: no_loss, compare_to: $.total }             # produced count ≥ $.total from first raw page
    - { kind: date_order, before: arrival_date, after: departure_date }  # departure ≥ arrival
    - { kind: money_invariant, field: total_amount }     # money field present + positive Decimal (semantic)
    - { kind: business_date_invariant, field: business_date, reference_field: posted_at, cutoff: 4 }  # business_date derives from posted_at
    - { kind: count_invariant, compare_to: $.total }     # strict count match (semantic drift alias)
  ```

  The last three are **semantic invariants** (P1.W6.1): schema-valid but
  value-wrong drift. They fail with `Degraded` and the invariant name in
  `unmet_criteria` (e.g. `money_invariant`). The drift classifier maps
  `invariant_failure` + `money_invariant` etc. to `SEMANTIC_VALUE_DRIFT`.

### 2.5 `capabilities` and `health`

* `capabilities.read` — entities this connector emits (must name an `entities`
  key). `action` is empty in the demo.
* `health.canary` — `operation` + `every` (`60s`/`5m`/`1h`) + `max_pages`.
  The canary monitor `GET`s the operation with `max_pages=1`.
* `health.fingerprint` — `keys | types | pagination_shape` toggles for the
  `fingerprint_id` hash (see `src/autobridge/engine/sync_run.py`).

## 3. Pagination

The interpreter supports three strategies; each page is fetched via
`HttpClient` with retry, then `evaluate_stop` decides whether to continue:

| Strategy | Params sent | `next_token` extraction | State advance |
|---|---|---|---|
| `offset` | `offset_param`, `limit_param` | `next_path` e.g. `$.next_offset` → int | `offset = int(next_token)` fallback `offset + len(items)` |
| `cursor` | `cursor_param` (omitted on first page), `limit_param` | `$.next_cursor` → opaque base64 index | `cursor = next_token`, `previous_cursor` retained for cycle detection |
| `page` | `page_param`, `limit_param` | optional `next_path`; optional `total_path` (e.g. `$.total`) | `page += 1` |
| `body_cursor` (v2, §10) | page size at `count_path`, cursor at `cursor_path` in the POST body | `next_path` (cursor out) | `cursor = next_token` (first request sends no cursor) |

Tokenless page pagination: when `next_path` is absent (or yields no token)
and `strategy == page`, the interpreter compares fetched records against the
advertised `total_path` count and keeps fetching while behind it. Reaching
the total is an explicit `total_reached` stop (not a drift signal); a
missing/unparseable total stops after the first page via the implicit
`no_next_token` guard. Every tokenless `page` pagination must list
`empty_page` in `stop_when` (enforced by bootstrap visible tests).

`stop_when` is a closed name set:

* `null_cursor` — `next_token` is `null` or `""`.
* `repeated_cursor` — cursor strategy only; `next_token` equals current or
  previous cursor (Harbor's known bug every 2nd page when the fault is active).
* `empty_page` — page returned no items.
* `no_next` — `next_token is None`.
* `page_limit` — `max_pages` cap (safety).

Implicit guards `no_next_token` and `empty_page_implicit` stop the loop even
when not listed; `repeated_cursor` is only surfaced when the spec opts in.
A `repeated_cursor` stop marks the run `Degraded` with `unmet_criteria:
[no_loss]`.

Atlas uses `page` (`pageNumber` / `pageSize` / `$.total`, tokenless);
Harbor uses `cursor` (`next_cursor` / `total` / `cursor` / `limit`).

Atlas notes (REQ-019):

* Status: vendor `canceled` maps to canonical `cancelled`. Vendor
  `not_confirmed` has no canonical counterpart and is deliberately absent
  from the map: with `on_unknown: block` it ends `Blocked` with the unmet
  criterion named, never guessed.
* Currency is not on the reservation list items; it lives on the property
  list. The reference connector materializes that vendor fact as an
  `enum_map` on `propertyID` (all seeded properties covered, unknown
  property → `Block`). Discovery leaves the draft `currency` path on the
  documented field and raises no silent default.
* Zone-less date-times (`dateModified`, `transactionDateTime`) are
  property-local times rendered in `America/New_York` for every property
  (documented vendor quirk). The engine interprets them with
  `timezone_source: static America/New_York`, so the round-trip is exact;
  assuming UTC misreads them by the offset — the real trap.
The reference Harbor spec includes `repeated_cursor` so the cursor-repeat
fault is detected as `Degraded` rather than looping.

## 4. Transforms — closed registry

`src/autobridge/runtime/transforms/` — 9 names, no user code:

| Name | Purpose | Key args |
|---|---|---|
| `date_local` | UTC instant → property-local calendar date (midnight crossing handled) | `tz: <iana> | source`, default `UTC` |
| `datetime_utc` | Local instant → UTC ISO (`+00:00`) | `tz: <iana> | source` |
| `money` | String/int/`Decimal` → `Decimal` quantized (float input is rejected) | `scale: 0..6` (default 2, uses `ROUND_HALF_UP`, strips `$`, `,`) |
| `enum_map` | Vendor code → canonical vocab; unknown `block` | `map: { from: to, … }, on_unknown: block` |
| `flatten` | `[[a],[b]]` → `[a,b]` | — |
| `coalesce` | First non-empty | `default` / `value` / `fallback` / `values: […]` |
| `const` | Replace with constant | `value: any` (required) |
| `concat` | Join / prefix-suffix | `separator`, `prefix`, `suffix`, `values: […]` |
| `business_date` | `posted_at` UTC → business date via cutoff | `tz: <iana> | source`, `cutoff: 0..23` (default `4`) |

`enum_map` with `on_unknown: block` is load-bearing: an unknown vendor value
raises `TransformBlocked` and the sync handler maps it to `Blocked` with
`unmet_criteria: ["unknown_enum", "unknown_enum:<field>"]`. Money is `Decimal`
end to end (never float); the canonical schemas require an exact string
`"123.45"` with 2 fraction digits.

`business_date` implements the hotel late-posting rule: `posted_at`
`00:00`–`03:59` local (cutoff `4`) counts to the prior business date.
A pure `YYYY-MM-DD` input is returned as-is (already a business date).

## 5. Sync outcome

`src/autobridge/engine/sync_run.py:execute_sync` executes every operation,
maps raw records to canonical via the pipeline, runs checks, then:

* `Blocked` — any `TransformBlocked` (unknown enum) → `unmet_criteria`
  includes `unknown_enum` + `unknown_enum:<field>`.
* `Degraded` — `repeated_cursor` (→ `no_loss`) or any check failure
  (`unique_key`, `no_loss`, `date_order`, `transform_error`).
* `Complete` — otherwise.

`data_readiness` follows the outcome: `Complete → {freshness: current,
completeness: complete}`, `Degraded → {current, partial}` (stale when zero
records), `Blocked → {stale, incomplete}`. `fingerprint_id` is a sha256 over
the spec signature + pagination shape + keys sample + types histogram, so any
drift in keys, types or pagination changes the id.

Both reference specs sync `Complete` on a clean simulator baseline.

## 6. JSONPath subset and validation

Every path field (`field.path`, `entity.key`, `checkpoint.field`,
`pagination.next_path / items_path`, `timezone_source.path`,
`compare_to`) must match `^\$(\.<ident>|\[\d+\]|\['…'\]|\["…"\]|\[\*\])*$` —
only root-child segments, numeric indexes, quoted keys and `[*]`.
Filters (`[?()]`), recursive descent (`..`), operators and parentheses are
rejected with a path-level `ValueError` naming the field.

## 7. JSON Patch and releases

Repairs are RFC 6902 JSON Patches (`src/autobridge/spec/patch.py`) applied to
a copy, then re-validated against the spec and schema. Helpers: `apply_patch`,
`diff_specs`, `reverse_patch`, `validate_patch_shape`. Stored releases are
write-once; rollback creates a new release that restores the prior `spec`.

## 8. Atlas vs Harbor — reference difference

The only behavioral differences are:

* **Auth** — Atlas enforces `Bearer` (missing/empty → 401); Harbor is
  permissive in dev but still carries a `credential_ref`. Both specs use
  `bearer`.

* **Pagination** — Atlas `offset`, Harbor `cursor` with `repeated_cursor`
  guard.

* **Status vocabulary** — Atlas identity map (`confirmed` → `confirmed`);
  Harbor opaque `H1` → `confirmed` etc.:

  ```yaml
  # Atlas
  map: { confirmed: confirmed, checked_in: checked_in, checked_out: checked_out, cancelled: cancelled, no_show: no_show }
  # Harbor
  map: { H1: confirmed, H2: checked_in, H3: checked_out, H4: cancelled, H5: no_show }
  ```

* **Money / dates** — identical pipelines (`money` with `scale: 2`,
  `date_local`/`business_date` with `tz: source`).

See `simulators/docs/atlaspms-openapi.json` and `simulators/docs/harbor.md`
for the vendor contract each spec was built from.

## 9. LegacyStay — bootstrapped third vendor (Phase 2 S1)

LegacyStay (`simulators/legacystay/app.py`, docs `simulators/docs/legacystay.md`,
host ports `8111` public / `8112` admin) is the heldout-only unseen-generalization
vendor: wire-compatible canonical shapes, but strict `Bearer` auth (401 without
it), `offset` pagination, plain-English statuses, an ambiguous `date` duplicator,
nullable chaos (`guest_name` / `description` often null), and an always-on
baseline throttle (roughly every 8th request `429` + `Retry-After: 1` — a
different cadence from the catalog's every-3rd `rate_limit_429`, so the generic
retry rule cannot be overfit to it).

Its docs carry three deliberate traps the generic pipeline ignores without any
vendor-specific code: an outdated cursor-envelope example (reality is `offset`),
a wrong `active` / `in-house` status example (reality is the plain words), and
the ambiguous `date` note. Bootstrap from docs alone yields a valid spec
(`id: legacystay`, `${LEGACYSTAY_BASE_URL}`, `LEGACYSTAY_API_KEY`) that syncs
`Complete` with `ModelCall` count `0` and passes the verifier oracle; a
`rename_guest_id` drift repairs via the generic `rename_field` L1 rule with
`frontier_tokens == 0` and a verified release. Proven in
`tests/e2e/test_s1_legacystay.py`, which also asserts no file under
`src/autobridge` mentions the vendor.

## 10. Spec v2 — POST, `body_fields` auth, `body_cursor` (REQ-020.5a)

Version 2 extends the vocabulary once, generally: no vendor branch anywhere in
the engine (`grep` finds no vendor name in `src/autobridge` outside tests and
fixtures). `spec_version: 1` documents validate unchanged; v2 features with
`spec_version: 1` are rejected with a `requires spec_version 2` error, which
is what the REQ-020 item-4 "before" refusal pins.

* `operations.<name>.method: POST` with an optional `body` template (static
  JSON; checkpoint templates resolve nested). `GET` with `body` is rejected.
* `auth.type: body_fields` with `body_fields: {<dotted body path>: <CREDENTIAL_REF>}`.
  Secrets are read from the environment into the request body per call, never
  headers, never logged, never stored.
* `pagination.strategy: body_cursor` with `cursor_path` (cursor in), optional
  `count_path` + `count_value` 1..1000 (page size in), `next_path` (cursor
  out), `items_path`, `stop_when`. The first request sends no cursor; the
  loop stops on null cursor, repeated cursor, empty page — or a short page
  (fewer items than requested), the body-cursor end-of-data signal.
  `cursor_param`/`offset_param`/`page_param`/`limit_param` query params stay
  untouched.

Example (shape, not a real vendor):

```yaml
spec_version: 2
source:
  base_url: ${VENDOR_BASE_URL}
  auth:
    type: body_fields
    body_fields: { ClientToken: VENDOR_CLIENT_TOKEN, AccessToken: VENDOR_ACCESS_TOKEN }
operations:
  list_reservations:
    method: POST
    path: /api/connector/v1/reservations/getAll/2023-06-06
    body: { EnterpriseIds: [e1], Limitation: { Count: 100 } }
    pagination:
      strategy: body_cursor
      cursor_path: Limitation.Cursor
      count_path: Limitation.Count
      count_value: 100
      next_path: $.Cursor
      items_path: $.Reservations
      stop_when: [null_cursor, repeated_cursor, empty_page]
```

## 11. Discovery proposes v2 from docs (REQ-020.5b)

Discovery stays deterministic and vendor-neutral; the same docs that v1
refuses compile under v2 with zero model calls. The build opts in per call
with `spec_vocabulary="v2"` (`"v1"` is the default, so the item-4 "before"
refusal stays demonstrable; REQ-021 makes this a per-build setting).

* Reader: parses `requestBody` (allOf-resolved, so shared auth bases plus
  per-operation filters expose every field), records required body fields,
  the body paging envelope (cursor + count leaves, e.g.
  `Limitation.Cursor` / `Limitation.Count` with the documented maximum) and
  the response item-array and cursor fields. No `securitySchemes` plus
  required credential-like body fields (`token`/`secret`/`password` in the
  name) means `body_fields` auth. Component schemas are allOf-resolved too.
* Classifiers: `auth.kind == body_fields` with the field names (high
  confidence from docs, medium when inferred without an auth section);
  `pagination_hint == body_cursor` per POST operation with the envelope in
  and the response cursor out (high confidence). GET probes never override a
  `body_cursor` guess — a query probe cannot validate a body contract.
* Hypotheses: POST operations with the body-paging signal get a fourth
  `body_cursor` hypothesis carrying the documented request/response paths;
  GET operations keep the classic offset/cursor/page three. Probes stay
  GET-only: a POST-only interface yields no probe candidates and the
  selector returns `None` gracefully instead of inventing probes.
* Compile: operation names from generic English synonyms
  (reservation/booking, order-item/folio/charge/transaction,
  customer/guest, configuration/enterprise; singular fetches keep the
  `get_*` skip names), wire names canonical-first then normalized then
  alias (PascalCase included, so earlier vendors never change), status maps
  case-insensitive (`Confirmed` → `confirmed`), checkpoints from the
  documented instant fields. The body template holds the page-size leaf and
  the caller-identity field; secrets never appear — `body_fields` auth maps
  each field to `<BASE>_<SNAKE>` refs (also exposed as
  `runtime.auth.spec_auth_refs`, with `primary_credential_ref` for the
  single-ref Connector row; read-time redaction already sweeps every
  credential-like env name).

Joins (reservation → customer name, reservation → order-item money sums,
UTC instants → enterprise time-zone business dates) are item 5c: 5b fields
that need a join compile to honest fallbacks (`$.guest_name`,
`$.total_amount`, `$.business_date`) and validate, but do not sync money or
names yet.

## 12. Declarative joins (REQ-020.5c)

Some vendors split an entity across operations: the reservation carries no
name and no money — the name lives in customers, the money in order items.
`entities.<Name>.joins` (spec v2 only; v1 rejects any join) enriches base
records from other operations by key before field mapping runs. A join is
data: `{from_operation, local_key, foreign_key, cardinality, as, fields}`,
JSON-Patchable like everything else, interpreted — never code.

* `one` attaches computed scalars from the first match (e.g. reservation →
  customer by `$.AccountId` = `$.Id`); `many` aggregates over all matches
  (e.g. reservation → order items by `$.AccountId`).
* A join field reads one `path` (or several `paths` combined into a list
  for `one`, e.g. `[FirstName, LastName]` through `concat`), then runs the
  closed transform set. `many` needs an `aggregate`: `first`, exact-Decimal
  `sum` (never float), `count`, `min`, `max`. No match — or all-null — is
  honest `None`, omitted by the mapper, never invented.
* Enrichment lands under the reserved `_joined.<as>` namespace, so entity
  fields read `$._joined.customer.full_name` or
  `$._joined.folio.total_amount` with ordinary JSONPath. Vendor records
  never carry `_joined`, so enrichment cannot collide with wire data.
* Discovery proposes joins from documented schemas when either side's
  description names the relationship (join/match/link): a name authority
  (`Id` + first/last names, carrying no foreign key itself) feeds a `one`
  join; a line-items operation (same foreign key + money leaf with a
  currency sibling, e.g. `UnitAmount.GrossValue` with `UnitAmount.Currency`,
  nested one level from the reader) feeds a `many` sum join; folios gain a
  reverse `one` join to reservations for the parent id. Field paths
  redirect to `$._joined...`; without a partner the documentable fallbacks
  stay. Lifecycle status spellings with an unambiguous canonical
  counterpart normalize (`Started` → `checked_in`, `Processed` →
  `checked_out`, `Optional` → `no_show`); genuinely ambiguous states
  (`Inquired`, `Requested`) stay unmapped and Block via `on_unknown`.
* Runtime: `runtime/joins.py` resolves after fetch, before mapping
  (`sync_run` for full syncs; the canary probe resolves with partial data —
  known-but-unfetched joins attach `None`). Money stays `Decimal` end to
  end; a float aggregate input raises instead of laundering precision.

## 13. Spec v3 — event-driven transport (REQ-022.C)

Version 3 extends the vocabulary once, generally: a framed on-premise
protocol (FIAS over TCP) joins the HTTP transports, with no vendor branch
anywhere in the engine (`grep` finds no vendor name in `src/autobridge`
outside tests and fixtures). `spec_version: 1` and `spec_version: 2`
documents validate and behave exactly as before; every v3-only construct
with an older version is rejected with a `requires spec_version 3` error,
which is what the REQ-022 item-C "before" refusal pins (v1 *and* v2 refuse
a FIAS source, deterministically, at 0 model calls).

* `source.protocol: fias_tcp` (default `http`) with `source.link` as data:
  `ack_mode`, `max_frame_bytes`, `announced_records` (the host's `LR`
  interest set). A `fias://` base URL names the site's listen contract
  (e.g. `fias://listen:5010`); `http` sources keep `http(s)` URLs.
* `operations.<feed>.method: EVENT` with `feed: {record, via: edge_relay,
  dedupe_keys}`. Feeds name no path, body, pagination, checkpoint or
  params — pushed records are not paged or positioned; delivery is
  at-least-once and the reader folds resends on `dedupe_keys` before
  mapping. The control plane drains feeds through the edge relay outbox
  with the relay operator token the spec's auth names.
* `auth.type: edge_relay` with `credential_ref` naming the env key of the
  relay operator token (read at runtime, never stored). No vendor
  credential exists on this transport.
* `entities.<Name>.fields.<f>.paths: [...]` (v3 only): one value per path,
  fed as a list into the pipeline — e.g. `paths: [$.DA, $.TI]` plus
  `concat` turns a wire date and time into one datetime string. A field
  with no path at all is allowed only when the pipeline starts with
  `const` (site facts stated outright, needing no input).
* `commands:` templates (v3 only): host-originated record data keyed by
  command kind — `{record, fields: {<wire id>: {param}}, defaults, now,
  expect}`. Placeholders substitute strictly (a missing param rejects the
  command before anything reaches the link); `expect` is `ack` or the
  answer record the agent waits for.
* Discovery: framed-protocol docs (record tables, framing contract, site
  facts) parse into EVENT feed candidates, a charge/status role proposal,
  and command templates; a FIAS source builds only under `spec_vocabulary:
  "v3"`. Capabilities (`GET /capabilities`, `autobridge capabilities`,
  the knowledge page) list v3 as "everything in v2, plus" the event
  transport.

Example (shape, from the FIAS interface document):

```yaml
spec_version: 3
connector: { id: fiaspabx, vendor: Grand, runtime_target: edge }
source:
  base_url: fias://listen:5010
  protocol: fias_tcp
  auth: { type: edge_relay, credential_ref: FIAS_API_KEY }
  timezone_source: { kind: static, value: America/New_York }
  link: { ack_mode: true, max_frame_bytes: 65536, announced_records: [PS, RE] }
operations:
  feed_ps:
    method: EVENT
    feed: { record: PS, via: edge_relay, dedupe_keys: [RN, P#, DA, TI] }
entities:
  FolioCharge:
    from: feed_ps
    key: $['P#']
    fields:
      charge_id:
        paths: [$.RN, $['P#'], $.DA, $.TI]
        transform: [{ name: concat, args: { separator: "-" } }]
      amount:
        path: $['TA']
        transform: [{ name: money, args: { scale: 2, minor_units: true } }]
      business_date:
        paths: [$.DA, $.TI]
        transform:
          - { name: concat, args: { separator: " " } }
          - { name: business_date, args: { tz: source, naive_as: source } }
    checks: [{ kind: unique_key }]
capabilities: { read: [FolioCharge, RoomStatus], action: [gi, wr, kr] }
commands:
  wr:
    record: WR
    fields: { RN: "{room_number}", DA: "{wake_up_date_yymmdd}", TI: "{wake_up_time_hhmm00}" }
    expect: ack
```

Wire notes (closed-set extensions, not new transforms): `money` accepts
`minor_units: true` (value counts minor currency units, divided by 100
before quantizing — exact `Decimal`, never float); the date transforms
parse framed wire `YYMMDD[ HHMMSS]` wall time (years 2000+YY) and take
`naive_as: source` for property-local wall timestamps (`date_local` and
`business_date` still assume UTC by default; `datetime_utc` still assumes
the source timezone by default — legacy behavior preserved).
