# AutoBridge — ConnectorSpec v1

> Reference for the declarative connector document the runtime interprets.
> Every spec validates against `schemas/connector-spec.v1.json` before it is
> stored, executed or patched. Invalid specs are rejected with path-level errors
> (e.g. `entities.Reservation.fields.status.transform[0].args.map: missing`).

Related: [Architecture](architecture.md) §5, `src/autobridge/spec/models.py`,
`schemas/connector-spec.v1.json`, plan §2.4.

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
| `atlaspms` | AtlasPMS | `generated/connectors/atlaspms/reference/spec.yaml` | `${ATLAS_BASE_URL}` → `http://sim-atlas:8000` in Compose, `http://localhost:8105` on the host | bearer `ATLAS_API_KEY` | offset (`limit`/`offset`/`next_offset`) | clear English (`confirmed`…) |
| `harborpms` | HarborPMS | `generated/connectors/harborpms/reference/spec.yaml` | `${HARBOR_BASE_URL}` → `http://sim-harbor:8000` / `8106` | bearer `HARBOR_API_KEY` (Harbor is permissive, but the spec still carries a `credential_ref`) | cursor (`cursor`/`next_cursor` base64) | opaque codes `H1`…`H5` |
| `legacystay` | LegacyStay | bootstrapped from `simulators/docs/legacystay.md` — no reference spec (Phase 2 S1, unseen-generalization vendor) | `${LEGACYSTAY_BASE_URL}` → `http://sim-legacystay:8000` / `8111` | bearer `LEGACYSTAY_API_KEY` (strict, missing/empty → 401) | offset (`limit`/`offset`/`next_offset`) | clear English (`confirmed`…) |

Both reference specs sync `Complete` with `ModelCall` count `0` — the runtime
never calls an LLM for a baseline sync.

Build report (`build-report.json`) records `connector_id`, `release_id`,
sha256 `signature` of the canonical JSON form, `built_by`, and any caller
metadata. Signature mismatches raise `ReleaseCorruptError`.

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
  `edge-net`; `sandbox` is the Docker-isolated target in W3.6).

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

  Secret values are read from the environment at call time, never logged, never
  stored in the spec, release, evidence or API response.

* `rate_limit.retry` — `statuses` (e.g. `[429, 503]`), `backoff` (`exponential |
  linear | constant`), `max_attempts` 1..10. The `HttpClient` retries on those
  statuses with exponential backoff (`Retry-After` overrides when numeric).

* `timezone_source` — how the runtime resolves property-local tz for
  `date_local` / `business_date`:

  ```yaml
  timezone_source: { kind: static, value: America/New_York }  # all properties share one tz
  timezone_source: { kind: field, path: $.property.tz }        # tz varies per record (looked up from the record)
  ```

  IANA names or `UTC` only. A `field` kind is evaluated per record via JSONPath;
  missing → `UTC` fallback.

### 2.3 `operations`

Keyed by name (`[a-z][a-z0-9_]*`). Each operation is a `GET` with:

* `path` — relative (must start with `/`, no `$` or spaces).
* `params` — query strings. Values may contain `{{checkpoint.<op>}}` templates
  resolved from the caller's checkpoint dict (e.g. incremental sync).
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
| `page` | `page_param`, `limit_param` | optional `next_path` | `page += 1` |

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

Atlas uses `offset` (`next_offset` / `total` / `limit` / `offset`);
Harbor uses `cursor` (`next_cursor` / `total` / `cursor` / `limit`).
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
