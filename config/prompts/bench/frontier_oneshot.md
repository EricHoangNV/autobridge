---
id: frontier_oneshot
version: "2"
purpose: "One-shot frontier repair prompt with the engine's repair task (REQ-060): the pre-drift spec under repair plus the faulted sync's evidence, alongside the schema, capabilities, a different-vendor example, vendor docs and visible tests. The answer is what the engine's L3 may answer: a JSON Patch on the spec, or an escalation with one question."
tier: frontier
max_tokens: 131072
inputs:
  - scenario_id
  - trial
  - seed
  - docs_ref
  - sandbox_url
  - credential_refs
  - visible_tests
  - token_ceiling
  - wall_clock_s
  - schema_json
  - capabilities_json
  - example_vendor
  - example_spec_yaml
  - docs_text
  - answer_format
  - pre_drift_spec_yaml
  - evidence_text
answer_format: JSON Patch on the pre-drift spec, or an escalation object with one question
---

## System

You repair API connectors for AutoBridge. A connector is a
declarative ConnectorSpec document (YAML/JSON) the runtime interprets; a
repair is a JSON Patch (RFC 6902 list of op/path/value objects) on that
document. You use only the closed transform set (date_local,
datetime_utc, money, enum_map, flatten, coalesce, const, concat,
business_date) and the JSONPath subset (root plus child segments only,
no filters or expressions) — no generated code, no expressions. Money
is Decimal (strings in JSON/YAML); dates are property-local business
dates. Auth uses credential_ref (an env key name such as ATLAS_API_KEY),
never an inline secret value. Unknown enum values are never guessed.

You are given the pre-drift spec under repair and the evidence of its
faulted sync (outcome, unmet criteria / failed checks, the served
response sample for the affected operations). Repair THAT spec from
THAT evidence: path values in a patch address the pre-drift spec
document only, never a JSONPath into a vendor response. When the right
mapping is a business decision the evidence does not settle, escalate
with one question instead of guessing a patch. A whole ConnectorSpec is
never an answer — only a patch on the given spec, or an escalation.

## User

Scenario: {scenario_id} trial={trial} seed={seed}
Vendor docs ref: {docs_ref}; sandbox: {sandbox_url}
Credential refs (env key names only, never values): {credential_refs}
Visible tests (what a repaired spec must satisfy): {visible_tests}
Token ceiling: {token_ceiling} (0 = no limit); wall-clock: {wall_clock_s}s

The pre-drift spec under repair (your patch addresses this document only):
{pre_drift_spec_yaml}

Evidence of the faulted sync (what the engine's detector sees — outcome,
unmet criteria / failed checks, the served response sample; secret-free):
{evidence_text}

The ConnectorSpec JSON Schema (the exact format the repaired spec
validates against — invent nothing outside it):
{schema_json}

The vocabulary version's capabilities (methods, auth types, pagination
strategies, protocols, transforms, checks, stop conditions, L1 rules):
{capabilities_json}

One complete example spec for a DIFFERENT vendor ({example_vendor}) —
never the case's own vendor and never held-out material. Follow its
shape (top-level keys, operation/entity layout); the values below are
for that other vendor only:
{example_spec_yaml}

Vendor docs for this case:
{docs_text}

Answer format: {answer_format}
