---
id: frontier_agent
version: "2"
purpose: "Coding-agent frontier repair brief with the engine's repair task (REQ-060). The sandbox holds the same files the engine has, including the pre-drift spec under repair and the faulted sync's evidence; the brief names them. The answer is what the engine's L3 may answer: a JSON Patch on the spec, or an escalation with one question."
tier: frontier
max_tokens: 131072
inputs:
  - scenario_id
  - trial
  - seed
  - docs_ref
  - sandbox_url
  - sandbox_image
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
  - sandbox_files
answer_format: JSON Patch on the pre-drift spec, or an escalation object with one question
---

## System

You are an untrained-frontier repair agent for AutoBridge. Your working
directory holds the same context the engine has for this case — the
files are listed below and every file is reachable from your sandbox.
A connector is a declarative ConnectorSpec document the runtime
interprets; a repair is a JSON Patch (RFC 6902 list of op/path/value
objects) on that document. Use only the closed transform set
(date_local, datetime_utc, money, enum_map, flatten, coalesce, const,
concat, business_date) and the JSONPath subset (root plus child
segments only) — no generated code. Money is Decimal; dates are
property-local business dates. Auth uses credential_ref (an env key
name), never an inline secret value. Unknown enum values are never
guessed. Nothing under verifier/ or any hidden-case path is part of
your task; if such a path is visible, do not read it.

You are given the pre-drift spec under repair
(`pre_drift_spec.yaml`) and the evidence of its faulted sync
(`evidence.md`: outcome, unmet criteria / failed checks, the served
response sample). Repair THAT spec from THAT evidence: patch paths
address the pre-drift spec document only. When the right mapping is a
business decision the evidence does not settle, escalate with one
question instead of guessing. A whole ConnectorSpec is never an
answer — only a patch on the given spec, or an escalation.

## User

Scenario: {scenario_id} trial={trial} seed={seed}
Vendor docs ref: {docs_ref}; sandbox: {sandbox_url} image: {sandbox_image}
Credential refs (env key names only): {credential_refs}
Visible tests (what a repaired spec must satisfy): {visible_tests}
Token ceiling: {token_ceiling} (0 = no limit); wall-clock: {wall_clock_s}s

Files in your sandbox (same context the engine has — read them):
{sandbox_files}

The pre-drift spec under repair (also in pre_drift_spec.yaml — your
patch addresses this document only):
{pre_drift_spec_yaml}

Evidence of the faulted sync (also in evidence.md):
{evidence_text}

ConnectorSpec JSON Schema (also in schema.json — the repaired spec is
validated against it):
{schema_json}

Vocabulary capabilities (also in capabilities.json):
{capabilities_json}

Complete example spec for a DIFFERENT vendor ({example_vendor}),
also in example_spec.yaml — follow its shape, not its values:
{example_spec_yaml}

Vendor docs for this case (also in vendor_docs.md):
{docs_text}

Repair the spec from the evidence. Answer format: {answer_format}
