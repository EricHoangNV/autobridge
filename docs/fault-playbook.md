# Fault playbook (test and review reference)

**Who uses this:** tests, reviewers and benchmark graders. When a test sees a fault and the way AutoBridge handled
it, it matches the pair against this dictionary.

**Who must not:** the engine. Nothing under `src/autobridge/` may read, import, copy or encode this file, its tables
or its expected outcomes. The engine decides from the evidence it observes: detector signals, the fingerprint delta,
unmet criteria, records, and vendor docs. This file describes what a correct decision looks like. It is never an
input to that decision.

## 1. The escalation path (every fault)

```
detect ─▶ classify ─▶ L0 known fix ─▶ L1 rule ─▶ L2/L3 model ─▶ verifier gate ─▶ operator approval ─▶ release
   │          │            │              │            │               │
   │          │            │              │            └─ asks ────────┴─ rejects ─▶ L4 operator question
   │          │            └─ replay ─────┴─ patch ────────────────────▶ verifier gate
   │          └─ non-fixable class ─────────────────────────────────────▶ L4 operator question (no patch)
   │          └─ rate limit / transient ────────────────────────────────▶ backoff + retry (no patch)
   └─ nothing detected ─────────────────────────────────────────────────▶ no repair run (sync Complete)
```

| Rung | What it does | Model tokens | Exit |
|---|---|---|---|
| Detect | Diffs the faulted sync against the stored baseline: wire keys, types, pagination, value baselines (money scale, dates), unmet criteria. | 0 | No signal and sync Complete: no repair run. |
| Classify | Names one drift class from the signals (section 2). Anything it cannot name is `UNKNOWN`. | 0 | Non-fixable or backoff classes leave the ladder here. |
| L0 knowledge | Exact replay of a fix seen before, keyed `vendor::class::sha256(delta)`. | 0 | Candidate patch to the verifier. |
| L1 rules | Deterministic rules for clear-cut cases (section 3). A rule declines when the evidence is ambiguous. | 0 | Candidate patch to the verifier. |
| L2 / L3 model | Gets class-neutral evidence: signals, delta, unmet criteria, spec subtree, new wire keys with samples, raw and canonical records. It returns **either** a patch **or** one operator question. | > 0 | A patch goes to the verifier; a question goes to L4. |
| Verifier gate | Applies the patch, re-syncs against the faulted vendor, and checks records and criteria. A separate trust boundary. | 0 | Pass: proposal awaits approval. Fail: not released. |
| L4 operator | One specific question; the connector is `Blocked` or `Degraded` with the unmet criterion named. | 0 | Waits for a person. |

Nothing ships without verifier pass **and** operator approval. Patches are never auto-applied.

## 2. Dictionary by drift class

| Drift class | Typical signals | Deterministic handling (L0/L1) | Reaches the model? | Correct outcome |
|---|---|---|---|---|
| `PAGINATION_CHANGE` | Page or stop-reason shape changed; records lost after page 1. | L1: `page_to_cursor`, `offset_to_cursor`, `cursor_to_offset`, `cursor_next_path_shift`, `nested_path_shift` | Only when no rule matches. | **repair** |
| `SCHEMA_RENAME` | A mapped key vanished or went empty in every record; a new key appeared. | L1 `rename_field` only when the evidence is unambiguous: one field gone, one new key, matching values, a lexically close name. `nested_path_shift` handles moves into a parent. | Yes, for every ambiguous rename. | **repair** if the new name plausibly names the same field; **escalate** (one question) if the name is misleading. |
| `SCHEMA_REMOVED` | A mapped key is gone and no candidate replaces it. | `nested_path_shift` if the key moved under a parent. | Yes. | **escalate** unless the evidence shows where the data went. |
| `SCHEMA_TYPE_CHANGE` | Type changed (string ↔ number), or a value format changed (dates). | L1 `type_coerce_string_number`. | Yes for anything else, such as a new date format. | **repair** when a closed transform expresses it, else **escalate**. |
| `ENUM_NEW_VALUE` | Status or code values outside the mapped vocabulary. | L1 `enum_extend_requires_evidence`: extends only when docs or evidence state the mapping. | Yes. | **repair** when the mapping is documented; **escalate** for a business mapping nobody stated. Never a guessed mapping. |
| `SEMANTIC_VALUE_DRIFT` | Value baseline broke: money scale ×0.5 / ×0.25, gross ↔ net, minor ↔ major units, business-date shift. | None (meaning is never inferred by a rule). | Always. | **repair** when the evidence settles the reading; **degraded_question** for a decoy or an unsettled meaning. |
| `AUTH_EXPIRED` | 401 with an expired token. | Refresh when the spec allows it (oauth2 with `token_url`). | Should not be needed (verify, see section 6). | **no_patch** (refresh) or **escalate**. |
| `AUTH_REVOKED` | Credential permanently rejected. | None. | **Never.** | **escalate**, no patch. |
| `CAPABILITY_REMOVED` | Endpoint or operation gone. | None. | **Never.** | **escalate**, no patch. |
| `RATE_LIMIT` | 429 / Retry-After. | L1 `retry_backoff_only`. | **Never.** | **no_patch** (backoff). |
| `TRANSIENT` | 5xx / outage that clears. | L1 `retry_backoff_only`. | **Never.** | **no_patch** (retry). |
| `UNKNOWN` | Signals the classifier cannot name. | None. | Always, with the raw evidence. | **repair** if the evidence settles it, else **escalate**. |

### Catch-all

Any fault not named in this file is graded by these rules, in order:

1. **Nothing detected and the sync output is correct:** `no_patch`. No repair run and no model call is correct.
2. **Nothing detected but the output is wrong** (silent wrong records): a **detection defect**, whatever the score.
3. **Detected, and the class is non-fixable or backoff:** the class's row in section 2, with no model call.
4. **Detected, everything else:** it must climb L0 → L1 → model with non-empty evidence. The correct end state is
   either a verified patch or one specific operator question. A guessed patch counts as a **wrong patch proposed**,
   even when the verifier blocks it.
5. **A crash, an empty model prompt, or a model answer of "no evidence was provided"** is a harness or engine
   **defect**, never a correct escalation.

## 3. L1 rules (deterministic, 0 tokens)

| Rule | Class | Fires when | Declines when |
|---|---|---|---|
| `page_to_cursor` / `offset_to_cursor` / `cursor_to_offset` | PAGINATION_CHANGE | The served pagination shape matches the target strategy. | The shape is mixed or unknown. |
| `cursor_next_path_shift` | PAGINATION_CHANGE | The next-cursor pointer moved within the envelope. | There is no single candidate. |
| `nested_path_shift` | RENAME / REMOVED / PAGINATION / UNKNOWN | Mapped keys reappear one parent deeper. | Several parents, or values don't match. |
| `rename_field` | SCHEMA_RENAME | One field gone or emptied, one new key, matching values, a lexically close name (generic similarity, no word lists). | Anything else: it climbs to the model. |
| `type_coerce_string_number` | SCHEMA_TYPE_CHANGE | A number is served as a numeric string, or the reverse. | Any other type change. |
| `enum_extend_requires_evidence` | ENUM_NEW_VALUE | The new values' mapping is stated in the docs or evidence. | The mapping is a business decision. |
| `retry_backoff_only` | RATE_LIMIT / TRANSIENT | Always. | — |

## 4. Beacon fault families (answer key)

Source of truth: `simulators/common/faultgen.py` `expected_for`. This table mirrors it for readers; if they differ,
the code wins and this file is stale.

| Family | Parameters | Expected | Expected path |
|---|---|---|---|
| `rename` | field → new name, plausible | repair | L1 `rename_field` when unambiguous, else model patch. |
| `rename` | field → new name, misleading (e.g. departure date → `checkinDate`) | escalate | Model asks one question. |
| `nesting` | entity moved under 1–2 parents | repair | L1 `nested_path_shift` or model patch. |
| `pagination` | offset / page / cursor / meridian body cursor | repair | L1 pagination rule. |
| `code_list` | new codes, all mapping-determinable | repair | L1 enum rule or model. |
| `code_list` | one code with no determinable mapping | escalate | Model asks. |
| `semantic` | `gross_net` / `minor_major`, factor 0.5 or 0.25 | repair | Model patch from baseline-vs-faulted evidence. |
| `semantic` | `decoy` (a tempting net field appears, gross is intact) | degraded_question | Model asks; no patch. |
| `dates` | `cutoff` (meridian) | no_patch | The wire never serves business dates. |
| `dates` | `cutoff` (others), `tz_shift` | repair | Model patch (transform args). |
| `dates` | `format` (`%d/%m/%Y`, `%m-%d-%Y`) | repair | Needs the date input-format argument (REQ-081 item 4). |
| `transport_auth` | `throttle`, `retry_after`, `outage`, `token_expired` | no_patch | Backoff, retry or refresh. |
| `transport_auth` | `token_revoked`, `endpoint_removed` | escalate | L4 directly, no model call. |
| `fias_layout` | wire ids renamed / added / reordered | repair | L1 rename or model patch on `paths`. |
| `fias_link` | link drop | no_patch | The edge agent re-handshakes; nothing is lost or duplicated. |

## 5. Named scenarios (dev, demo and held-out)

| Scenario | Class | Expected | Where it is shown |
|---|---|---|---|
| `page_to_cursor`, `offset_to_cursor`, `meridian_cursor_renamed` | PAGINATION_CHANGE | repair at L1, 0 tokens | Deck 3 (Atlas) |
| `rename_guest_id`, `meridian_state_renamed`, `fias_record_layout_change` | SCHEMA_RENAME | repair | |
| `gross_to_net` | SEMANTIC_VALUE_DRIFT | model patch (L3), operator approves | Deck 5 (Harbor) |
| `tz_boundary_shift` | SEMANTIC_VALUE_DRIFT | repair | |
| `invalid_patch_trap` | SEMANTIC_VALUE_DRIFT | escalate (the tempting patch must not be released) | |
| `status_vocab_change` | ENUM_NEW_VALUE | repair if documented, else escalate | |
| `rate_limit_429`, `transient_503` | RATE_LIMIT / TRANSIENT | no_patch (backoff) | |
| `token_expired` | AUTH_EXPIRED | refresh or escalate | |
| `token_revoked`, `endpoint_removed` | AUTH_REVOKED / CAPABILITY_REMOVED | escalate, no model call | |
| `cursor_repeat_token`, `duplicate_late_records`, `fias_duplicate_records` | runtime (dedupe / loop guard) | no_patch; no record lost or duplicated | |
| `fias_link_resync`, `fias_ack_timeout`, `agent_restart_inflight`, `session_lost_with_command`, `stale_command`, `cloud_slow`, `fias_oversize_frame`, `fias_record_flood` | edge runtime / protocol | no_patch; the edge agent recovers, nothing lost or duplicated | Deck 7 (PABX) |

## 6. Known gaps (as of 2026-11-02)

- REQ-081 is open. Until it lands:
  - semantic faults on specs without a `money_invariant` check reach the model with empty evidence;
  - multi-path (`paths`) fields are hidden from the model;
  - `dates/format` cannot be repaired.
- `AUTH_EXPIRED` is outside the non-fixable and backoff sets, so with a model provider wired it may climb past the
  deterministic refresh/escalate decision. To be confirmed by test.
- Section 5's runtime rows (dedupe, edge recovery) are taken from the scenario catalog
  (`simulators/common/faults.py`) and have not been re-verified against their tests for this file.
