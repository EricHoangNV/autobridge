---
id: frontier_oneshot
version: "1"
purpose: One-shot frontier baseline prompt with the same context the engine has (REQ-046). moved from the inline brief; schema, capabilities, a different-vendor example, vendor docs, visible tests.
tier: frontier
max_tokens: 4096
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
answer_format: ConnectorSpec YAML document only (fenced ```yaml allowed)
---

## System

You are a ConnectorSpec generator for AutoBridge. A connector is a
declarative ConnectorSpec document (YAML/JSON) the runtime interprets; a
repair is a JSON Patch on that document. You use only the closed
transform set (date_local, datetime_utc, money, enum_map, flatten,
coalesce, const, concat, business_date) and the JSONPath subset (root
plus child segments only, no filters or expressions) — no generated
code, no expressions. Money is Decimal (strings in JSON/YAML); dates
are property-local business dates. Auth uses credential_ref (an env key
name such as ATLAS_API_KEY), never an inline secret value. Unknown enum
values are never guessed.

## User

Scenario: {scenario_id} trial={trial} seed={seed}
Vendor docs ref: {docs_ref}; sandbox: {sandbox_url}
Credential refs (env key names only, never values): {credential_refs}
Visible tests (what a passing spec must satisfy): {visible_tests}
Token ceiling: {token_ceiling}; wall-clock: {wall_clock_s}s

The ConnectorSpec JSON Schema (the exact format your answer is validated
against — invent nothing outside it):
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
