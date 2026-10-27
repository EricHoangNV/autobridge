# AutoBridge — safety model

> First pass for P1.W2.9 (plan §4 W2, §2.7, §2.8). Covers the verifier
> trust boundary, oracle, scenario catalog and the checks that gate every
> release. Later workstreams extend this page (ladders, escalation, MCP
> guardian); the final version is due in W10.

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

## 3. Canonical checks

| Check | What it enforces | Bounded name(s) |
|---|---|---|
| Record set by key | reservation/charge ids match ground truth | `record_set_mismatch` |
| No duplicates | each key appears once | `duplicate_records`, `unique_key` |
| No loss | produced count ≥ expected | `no_loss`, `loss_detected` |
| Money exact | `Decimal` with 2dp, exact value per record | `money_mismatch`, `money_format` |
| Business date | `business_date` equals oracle; `posted_at`→`business_date` via property timezone cutoff 04:00 | `business_date_mismatch`, `date_order` |
| Status mapping | status in closed canonical vocab | `unknown_status`, `unknown_enum`, `status_mapping` |
| Field presence | required fields present (e.g. `guest_name` after rename) | `field_missing` |
| Pagination & transport | strategy mismatch, repeated cursor, 429/503 without retry, 401/403/404, trap, duplicates, tz shift | `pagination_shape`, `repeated_cursor`, `rate_limited`, `transient_error`, `auth_expired`, `auth_revoked`, `endpoint_not_found`, `invalid_patch_trap` |

Money is `Decimal` end to end (never float). Dates are property-local
business dates: `business_date_for_posted_at(posted_at, tz, cutoff=04:00)`
handles the 04:00 boundary — a `posted_at` at 02:00 UTC in `America/New_York`
lands on the next business date.

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

Admin isolation is enforced by an `X-Admin-Network: admin-net` header.
`runtime-sandbox` (vendor-net only) cannot reach `8001`; smoke asserts it is
`403/down`. The host and `control` (admin-net) proxy include the header.

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

## 8. Network topology (excerpt)

```
engine-net   control, worker, runtime-sandbox, ui, bench
vendor-net   runtime-sandbox ↔ sim-atlas/sim-harbor :8000
verifier-net control ↔ verifier, bench ↔ verifier
admin-net    control ↔ sim admin :8001 (+ host 8109/8110 for e2e)
```

No `internal: true` — host ports are required for `make smoke`/`make e2e`.
A dedicated unit test (`test_autobridge_compose.py`) asserts the intended
attachments statically; smoke probes assert reachability and isolation live.

## 9. Escalation bundle (L4)

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

Storage: `RepairRun.escalation_bundle` (JSON, nullable). Every row with
`decision == "escalate"` has a non-null bundle whose `question` passes
validation (`from_dict` checks: non-empty, exactly one `?`, ends with `?`).
`decision == "propose"` has `escalation_bundle == null`.

Producers:

* `escalation_for_no_fix(reason, evidence_ids, question?)` — generic L4 path
  and the two non-fixable classes (`CAPABILITY_REMOVED`, `AUTH_REVOKED`) plus
  `AUTH_EXPIRED` when refresh is not permitted.
* `escalation_from_budget_exhausted(exc, evidence_ids, context)` — L2/L3
  `BudgetExhausted` (probe vs token wording, single question).

Consumers: control API surfaces the bundle on `GET /repairs/{id}` and the
`/repairs/[id]` screen renders the evidence bundle side-by-side with the
question and Approve/Reject actions. The bundle never contains a credential
value (redacted at the `EvidenceRecord` boundary, `evidence/redact.py`).

W3–W10 extend this model with deterministic repair rules, the hypothesis
engine and the MCP compatibility gateway.

