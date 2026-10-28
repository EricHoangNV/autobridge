# AutoBridge — safety model

> Complete through P1.W6 (plan §4 W6, §2.5–§2.8), final reference as of W10.
> Covers the verifier
> trust boundary, oracle, scenario catalog, semantic invariants, drift
> detection, escalation bundles and the regression gate on every release.
> Phase 2 extensions (MCP guardian, Edge) will extend this page.

## 1. Why a separate verifier

AutoBridge executes connectors but never judges them. A distinct **verifier**
service owns the ground truth. It lives in its own package (`verifier/`),
image (`verifier/Dockerfile` built only from `verifier/`) and Docker network
(`verifier-net` + read-only `/fixtures` mount). The engine (`src/autobridge`)
has no route to it and never imports it — the same invariant the import-linter
enforces between `autobridge` and `verifier`/`simulators` (`make lint-imports`).
Hidden fixtures and answer keys never leave the verifier package.

The engine talks to the verifier only over HTTP:

```
POST /verify { release_id | spec, scenario_id, mode: visible|hidden }
→ { verdict: pass|fail, score, failed_checks: [{name,count}],
    regression: pass|fail, regression_failures?, scenario_id, mode }
```

`mode=hidden` is the benchmark path. The response contains only aggregates
(bounded `failed_checks` names and counts, a 0–1 score). It never returns
fixture rows, record arrays, or raw datasets. Section 6 proves this.

## 2. Oracle (ground truth from seeds)

The oracle regenerates the exact dataset the simulator would serve for a
scenario's seed, using the shared seeded generator (`simulators.common.generator`
+ `simulators.common.faults.apply_dataset_mutations`). No HTTP, no fixture
leakage — only deterministic code and the scenario's `seed` from
`simulators/scenarios/*.yaml`.

```
expected = generator.generate_dataset(seed)          # canonical baseline
mutated  = faults.apply_dataset_mutations(expected)   # as the sim serves it
produced = oracle.simulate_produced_records(mutated, spec, scenario_id)
verdict  = checks.run_all_checks(expected, produced, mutated, spec, scenario_id)
```

`produced` is what the candidate `spec` would emit when run against the
mutated simulator: field renames are followed or missed, pagination strategy
is matched or truncated, retry guards are present or absent, auth failures
return empty, the invalid-patch trap is detected. The check suite then
compares `produced` to `expected` (Section 3).

Seeds are byte-identical: the same seed always yields the same
`properties`/`reservations`/`folios` set, including late postings (00:00–04:00
→ next business date), duplicate injections and nullable fields.

## 3. Canonical checks (verifier) and semantic invariants (engine)

Verifier checks compare `produced` to `expected` (Section 2). Engine
invariants run on the canonical records after mapping, before the sync
outcome is decided.

| Check kind | Where | What it enforces | Bounded name(s) |
|---|---|---|---|
| Record set by key | verifier | reservation/charge ids match ground truth | `record_set_mismatch` |
| No duplicates | verifier + engine `unique_key` | each key appears once | `duplicate_records`, `unique_key` |
| No loss | verifier + engine `no_loss` | produced count ≥ expected | `no_loss`, `loss_detected` |
| Money exact | verifier | `Decimal` with 2dp, exact value per record | `money_mismatch`, `money_format` |
| Money invariant | **engine** | positive `Decimal` 2dp per record; when `compare_to: $.gross_sum` present, actual sum must not be more than 5% below expected (only low triggers, negative allowed for FolioCharge refunds) | `money_invariant` |
| Business-date invariant | **engine** | `business_date` equals `posted_at`→`business_date` via property timezone cutoff 04:00; only late postings near the boundary fail | `business_date_invariant` |
| Count invariant | **engine** | record count equals expected (`expected_count`, `compare_to: $.total`, or `compare_to_value`); over-count also fails | `count_invariant` |
| Business date | verifier | `business_date` equals oracle; `posted_at`→`business_date` via property timezone cutoff 04:00 | `business_date_mismatch`, `date_order` |
| Status mapping | verifier + engine `enum_map on_unknown: block` | status in closed canonical vocab | `unknown_status`, `unknown_enum`, `status_mapping` |
| Field presence | verifier | required fields present (e.g. `guest_name` after rename) | `field_missing` |
| Pagination & transport | verifier | strategy mismatch, repeated cursor, 429/503 without retry, 401/403/404, trap, duplicates, tz shift | `pagination_shape`, `repeated_cursor`, `rate_limited`, `transient_error`, `auth_expired`, `auth_revoked`, `endpoint_not_found`, `invalid_patch_trap` |

Money is `Decimal` end to end (never float). Dates are property-local
business dates: `business_date_for_posted_at(posted_at, tz, cutoff=04:00)`
handles the 04:00 boundary — a `posted_at` at 02:00 UTC in `America/New_York`
lands on the next business date.

### 3.1 Semantic invariants → Degraded with named criteria

Engine invariants are schema-valid but value-wrong drift — they mark the
sync `Degraded` with the failing invariant named in `unmet_criteria` and
classified as `SEMANTIC_VALUE_DRIFT` (never silently `Complete`):

```
# gross_to_net: server returns gross_sum = baseline sum as independent evidence;
# money_invariant compares actual sum vs expected 5% tolerance, only when low.
sync outcome: Degraded  unmet: [money_invariant]  class: SEMANTIC_VALUE_DRIFT  signal: invariant_failure

# tz_boundary_shift: 30% of folio business_dates shifted +1 day via charge_id hash;
# business_date_invariant detects posted_at→business_date mismatch via property_id→tz lookup.
sync outcome: Degraded  unmet: [business_date_invariant]  class: SEMANTIC_VALUE_DRIFT

# status_vocab_change: opaque status not in enum_map on_unknown:block
sync outcome: Blocked  unmet: [unknown_enum, unknown_enum:status]  class: ENUM_NEW_VALUE  (Block before Degraded)

# endpoint_removed: HttpError 404 → Blocked endpoint_not_found → escalate no patch
# token_revoked:    HttpError 403 → Blocked auth_revoked        → escalate no patch
```

Surviving ambiguity after L2/L3 also yields `Degraded` with `open_questions`
(plan §2.5). `tests/e2e/test_05_safety_and_semantic.py` asserts all five
scenarios via fake transports and proves the engine never imports
`verifier`/`simulators`.

For each scenario the catalog lists the *primary* failed check. Secondary
consequences (e.g. `no_loss` caused by a 429 truncation) are filtered so
`failed_checks` exactly matches `expected.failed_checks` from the YAML.
`tests/e2e/test_01_simulators_and_oracle.py` asserts this for all 14 scenarios.

## 4. Scenario catalog (14, plan §2.8)

| Scenario | Drift class | Fixable | Visibility | Expected `failed_checks` |
|---|---|---|---|---|
| `baseline` | none | fixable | dev | *(none)* — pass |
| `rename_guest_id` | FIELD_RENAME | fixable | dev | `field_missing` |
| `offset_to_cursor` | PAGINATION_CHANGE | fixable | dev | `pagination_shape` |
| `cursor_repeat_token` | PAGINATION_BUG | fixable | dev | `repeated_cursor` |
| `rate_limit_429` | RATE_LIMIT | fixable | dev | `rate_limited` |
| `transient_503` | TRANSIENT_ERROR | fixable | dev | `transient_error` |
| `token_expired` | AUTH_EXPIRED | fixable | dev | `auth_expired` |
| `token_revoked` | AUTH_REVOKED | escalate | heldout | `auth_revoked` |
| `status_vocab_change` | STATUS_VOCAB | fixable | dev | `unknown_status` |
| `gross_to_net` | SEMANTIC_VALUE_DRIFT | fixable | dev | `money_mismatch` |
| `tz_boundary_shift` | SEMANTIC_VALUE_DRIFT | fixable | heldout | `business_date_mismatch` |
| `duplicate_late_records` | DUPLICATE_DELIVERY | fixable | dev | `duplicate_records` |
| `endpoint_removed` | CAPABILITY_REMOVED | escalate | heldout | `endpoint_not_found` |
| `invalid_patch_trap` | SEMANTIC_VALUE_DRIFT | escalate | heldout | `invalid_patch_trap` |

`dev` scenarios are visible during ladder tuning; `heldout` are escrowed
until the final benchmark. `fixable` scenarios admit a deterministic or
evidence-backed repair; `escalate` scenarios must escalate (no patch —
`CAPABILITY_REMOVED`/`AUTH_REVOKED` have no recovery, the trap is the
intentionally wrong `net→total_amount` mapping that the verifier must reject).

## 5. Simulators

* **AtlasPMS** — clean OpenAPI 3.1, bearer auth, offset pagination, descriptive
  statuses. Used for the first zero-inference connector.
* **HarborPMS** — messy Markdown narrative docs (one wrong pagination example,
  one undocumented `GET /reservations/{id}/folios` endpoint), cursor
  pagination with a repeated-cursor bug on even requests, nested folios,
  opaque `H1…H5` statuses, ambiguous `date` duplicating `arrival_date` /
  `business_date`, property-local dates. Used to showcase discovery and repair.
* Both share `simulators/common` (seeded generator + fault switchboard +
  admin API `POST /admin/scenario {id, seed}` on `8001`/`admin-net`).
* `LegacyStay` (Phase 2) adds rate limits, misleading examples and nullable
  chaos for unseen-generalization.

Admin writes are authorized by `SIM_ADMIN_TOKEN` (`X-Admin-Token`,
constant-time compare); `X-Admin-Network: admin-net` is only a routing hint
(AR-1-1 — the header value is published in this repo, so it cannot be the
credential). `runtime-sandbox` (vendor-net only) has no token and gets
`403/down` even when it replays the header; smoke asserts exactly that when
the token is configured. The host and `control` (admin-net) proxy send both
headers when the token is set.

## 6. Response schema and leakage guard

The verifier's response schema is bounded:

```json
{
  "verdict": "pass|fail",
  "score": 0.0–1.0,
  "failed_checks": [{"name": "<bounded>", "count": int}],
  "regression": "pass|fail",
  "scenario_id": "…",
  "mode": "visible|hidden"
}
```

In `hidden` mode the same schema is returned but the service also runs
`_ensure_no_fixture_leak` (no top-level `reservations`/`folios`/`records` arrays
and no bulk `RES-`/`CHG-` dumps). The e2e test posts every scenario in both
modes and asserts the leak guard.

Other guarantees proven in W2:

* **Hidden-seed provenance** — the oracle seeds come from YAML but are never
  exposed as fixture rows.
* **Catalog isolation** — each scenario triggers exactly one primary
  `failed_checks` name (unit test in `test_simulators_scenarios.py`).
* **Mutation traps** — flipping a transform or dropping a check in a passing
  spec makes the verifier fail (W2.7 `test_verifier_w27.py`).
* **Invalid-patch trap** — mapping the decoy `net_amount` to `total_amount`
  after `gross→net` is rejected with `invalid_patch_trap`.
* **Regression gate** — `regression_suite` param re-runs previously verified
  scenarios; a release that breaks `baseline` is blocked.

## 7. Regression gate on every candidate release (P1.W6.4)

Every candidate `ConnectorRelease` is verified before it is persisted.
`src/autobridge/engine/verification.py` POSTs the candidate spec to
`POST /verify {spec, scenario_id, regression_suite}` (never imports
`verifier`/`simulators`; `credential_ref` only, secrets never leave the
engine). The verifier returns `{verdict, regression, failed_checks,
regression_failures}` — only bounded names, never fixtures.

Gate logic (`_parse_verifier_response`):

* `allowed = verdict == "pass" and regression == "pass"`
* `verdict fail` → blocked with the first `failed_checks[].name`; the
  plausible-but-wrong patch is rejected because its name is
  `invalid_patch_trap` (net→total_amount after gross→net).
* `regression fail` → blocked with the breaking scenario ids from
  `regression_failures[].scenario_id`; a patch that fixes
  `rename_guest_id` but breaks `baseline` is blocked.

Engine enforcement (`src/autobridge/engine/releases.py`):

```
candidate_spec = apply_patch(base.spec, patch)
maybe_verify(candidate_spec, verifier_url, scenario_id, verified_scenarios)
# raises GateBlockedError when verdict or regression fail
create_release(..., spec=candidate_spec)  # only on pass
```

`verified_scenarios` is the connector's previously verified set
(e.g. `["baseline", "rename_guest_id", "offset_to_cursor"]`);
when `verifier_url` is `None` the gate is skipped for backward compat
(unit tests without Docker). `GateBlockedError` carries `block_reason`
and the raw verifier `result` for the ladder trace.

Proven in `tests/unit/test_autobridge_verification_gate.py` (28 tests):
verdict/regression parsing, `invalid_patch_trap` wording, HTTP 500 and
unreachable handling, `regression_suite` forwarding, `create_release` and
`create_release_from_patch` blocking vs allowing, and a live
`oracle_verdict` check that the trap spec is rejected and a wrong pagination
spec fails `pagination_shape`.

## 8. Five scenarios wired end to end (P1.W6.2)

The safety net proves that semantic and transport drift are distinguished
and handled by the correct ladder path:

| Scenario | Simulator fault | Engine sync outcome | Classifier | Ladder result | E2E proof |
|---|---|---|---|---|---|
| `gross_to_net` | Folio amounts switched gross→net; `gross_sum` = baseline sum as independent evidence | `Degraded` `money_invariant` | `SEMANTIC_VALUE_DRIFT` | `escalate` with evidence bundle (no deterministic rule for net drift; plausible-but-wrong `net→total_amount` is the `invalid_patch_trap` the verifier must reject) | `test_gross_to_net_fake_degraded` + `test_invalid_patch_trap_rejected_via_oracle` |
| `tz_boundary_shift` | 30% folio `business_date` shifted +1 day via `charge_id` hash; `posted_at` unchanged | `Degraded` `business_date_invariant` | `SEMANTIC_VALUE_DRIFT` | `escalate` with bundle | `test_tz_boundary_shift_fake_degraded` |
| `status_vocab_change` | Reservation `status` vocabulary changed | `Blocked` `unknown_enum` | `ENUM_NEW_VALUE` (ENUM before CAPABILITY) | `escalate` or `propose` only with evidence; never silently guessed `Degraded` | `test_status_vocab_change_fake_blocked` |
| `endpoint_removed` | `GET /reservations` → 404 | `Blocked` `endpoint_not_found` | `CAPABILITY_REMOVED` | `escalate` no patch | `test_endpoint_removed_fake_blocked` |
| `token_revoked` | `Authorization` → 403 | `Blocked` `auth_revoked` | `AUTH_REVOKED` | `escalate` no patch | `test_token_revoked_fake_blocked` |

HttpError variants (401 `auth_expired`, 429 `rate_limited`, 503 `transient_error`) also map to `Blocked` with named criteria. `tests/e2e/test_05_safety_and_semantic.py` replays each via a fake `HttpError` client and live via the simulator admin API when the stack is up. Money is `Decimal` 2dp; a timestamp crossing the 04:00 business-date boundary lands on the right local date (§3.1).

## 9. Network topology (excerpt)

```
engine-net   control, worker, runtime-sandbox, ui, bench
vendor-net   runtime-sandbox ↔ sim-atlas/sim-harbor :8000
verifier-net control ↔ verifier, bench ↔ verifier
admin-net    control ↔ sim admin :8001 (+ host 8109/8110 for e2e)
```

No `internal: true` — host ports are required for `make smoke`/`make e2e`.
A dedicated unit test (`test_autobridge_compose.py`) asserts the intended
attachments statically; smoke probes assert reachability and isolation live.
Negative test: `DockerSandbox` cannot reach the verifier or simulator admin
(runtime target allowlist); host and `control` (admin-net) are the only paths
with `X-Admin-Network: admin-net`.

## 10. Escalation bundle (L4)

When no L0/L1 rule matches, the budget is exhausted, or the drift class is
`CAPABILITY_REMOVED` / `AUTH_REVOKED`, the ladder emits an **escalation
bundle** — never a silent guess (plan §2.5, W6.3).

```
EscalationBundle {
  reason: str               // machine-readable, e.g. "CAPABILITY_REMOVED: …"
  question: str             // exactly one precise question ending with "?"
  evidence_ids: str[]       // EvidenceRecord ids supporting the question
  evidence_payloads: dict[] // optional redacted payloads (already redacted)
  suggested_action: str     // hint, e.g. "manual review" or "increase budget"
}
```

Validation (`from_dict`): non-empty `reason`, non-empty `question`, exactly one `?`, ends with `?`.

Storage: `RepairRun.escalation_bundle` (JSON, nullable, alembic `0003_escalation_bundle`).
Every row with `decision == "escalate"` has a non-null bundle whose `question`
passes validation; `decision == "propose"` has `escalation_bundle == null`.
Seeded via `src/autobridge/repair/escalation.py` and persisted by
`RepairController` + `repair_job_handler`.

Producers:

* `escalation_for_no_fix(reason, evidence_ids, question?)` — generic L4 path
  and the two non-fixable classes (`CAPABILITY_REMOVED`, `AUTH_REVOKED`) plus
  `AUTH_EXPIRED` when refresh is not permitted (`oauth2_client_credentials` with
  `token_url` can refresh instead).
* `escalation_from_budget_exhausted(exc, evidence_ids, context)` — L2/L3
  `BudgetExhausted` (probe vs token wording, single question; `Budget` raises at
  ceiling, never silently retries).

Consumers: control API surfaces the bundle on `GET /repairs/{id}` and the
`/repairs/[id]` screen renders the evidence bundle side-by-side with the
question and Approve/Reject actions. The bundle never contains a credential
value (redacted at the `EvidenceRecord` boundary, `evidence/redact.py`);
`tests/e2e/test_05` asserts no `sk-`/secret leaks in bundles.

## 11. Safety invariants — what is guaranteed

* **No silent guessing** — `unknown_enum` → `Blocked`, never `Degraded` with a
  guessed mapping; `CAPABILITY_REMOVED`/`AUTH_REVOKED` → escalate with no patch
  attempted; `BudgetExhausted` → escalate, never retry; surviving ambiguity →
  `Degraded` with `open_questions`.
* **Every sync is typed** — outcome is always `Complete | Degraded | Blocked`
  with the unmet criterion named (`invariant_results[].check`), data readiness
  (`freshness`/`completeness`), fingerprint id and checkpoint. `test_05`
  asserts this for baseline, the five drift scenarios and HttpError variants.
* **Money is Decimal, dates are business-local** — money `Decimal` 2dp end to
  end (§3.1, connector spec `money` transform), business date via
  `date_local` + `business_date_invariant` cutoff 04:00 (property `prop-00x` →
  timezone map); verifier also enforces money exact and business-date equality.
* **Deterministic first** — every `RepairRun` records `model_usage` with
  `frontier_tokens`; known repairs show `frontier_tokens == 0` (L0/L1 only);
  every model call goes through `LLMProvider` + `Budget` and lands in
  `ModelCall`; `test_05` asserts frontier 0 for all W6 escalations.
* **No fixture leakage** — verifier `POST /verify` in `hidden` mode returns
  only bounded `failed_checks` names/counts + score; never fixture rows;
  engine never imports `verifier`/`simulators`; `hidden_cases` never leaves its
  package (`test_05` engine-import negative test + `_ensure_no_fixture_leak`).
* **Secrets never in code/specs/evidence** — specs carry `credential_ref` (env
  key name) never a value; `evidence/redact.py` strips values at the append
  boundary; `generated/connectors/*/reference/spec.yaml` base_url is
  `${ATLAS_BASE_URL}` placeholder; bundles and API responses carry only bounded
  names; `test_05` searches specs/bundles/evidence for leaked `sk-`/secret.
* **Regression gate on every release** — §7; candidate that breaks any
  previously verified scenario or the `invalid_patch_trap` is blocked by
  `GateBlockedError`; `test_05` proves this with a fake verifier and a live
  oracle check (the `net→total_amount` trap).

`tests/e2e/test_05_safety_and_semantic.py` is the W6 acceptance test: 14 fake-path
tests + live admin-oracle checks when the Compose stack is up. The suite is
independent, random-order safe and requires no prior state beyond the reference
specs under `generated/connectors/`.

## 12. Where to go next

W7 added the control plane (`/metrics/model-usage`, `POST /demo/inject-fault` with
label) and the Next.js UI; W8 added the matched-budget benchmark; W10 completed
this page as the final safety reference alongside the [demo script](demo.md).
Phase 2 extends the model with LegacyStay, Edge targets and the MCP Guardian.

