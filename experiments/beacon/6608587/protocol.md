# Beacon evaluation protocol (REQ-025 item 4)

One run, no retries, results published whatever they are. Frozen before the
target round exists: the instances (fault parameters per seed) could not be
known at freeze time. Fault *families* are known by design — that is the
engine's everyday knowledge; the honest-scope note in docs/benchmark.md
applies. The frontier model below is pinned: ``bench-beacon`` refuses to
run when the resolved model differs (REQ-066).

- beacon-round-after-utc: 2026-11-03T06:50:28.991242+00:00
- beacon-target-round: 6608587
- beacon-faults: 25
- beacon-trials: 1
- beacon-arms: frontier_oneshot, frontier_agent, engine_no_knowledge, autobridge_cold, autobridge_warm
- beacon-engine-commit: 0bf9ae23e02641d9b368e70b1b054aca8e6d6ce4
- beacon-generator-commit: 0bf9ae23e02641d9b368e70b1b054aca8e6d6ce4
- beacon-frozen-at-utc: 2026-11-03T06:30:29.144092+00:00
- beacon-rehearsal: false
- beacon-frontier-model: muse-spark-1.3-contributor
- beacon-agent-model: meta/muse-spark-1.3-contributor
- beacon-token-ceiling: 200000
- beacon-wall-clock-s: 600.0
- beacon-chain: 8990e7a9aaed2ffed73dbd7092123d6f289930540d7651336225dc172e51b2ce scheme=pedersen-bls-chained
- beacon-verify-url: https://api.drand.sh/public/6608587

Metrics per arm, scenario and overall (mean + 95% interval over trials):
verified completion rate (judged only by the verifier), wrong patches that
reached a release (must be 0 for AutoBridge), correct escalations
(Blocked with one operator question on an instance whose key accepts a
question — a no-patch Complete is verified completion, not an
escalation), frontier tokens, estimated
cost, wall time.

Engine-arm inputs (REQ-024 owner check on f32b4b4b, fixed): every arm gets
the same inputs (the pre-drift spec, the docs, the served instance,
budgets). The engine arms observe the instance end to end — sync the
served shape (the same simulator serving code the live stack runs,
deterministic from the public seed) → detector → classifier → the
engine's own hypothesis enumerator → ladder → verifier gate — with no
case-supplied drift class, delta, hypotheses or yield count. Instance
parameters, generator internals and answer keys never reach the arms.

Oracle dataset (REQ-083 A1): every beacon dataset serves all properties
in America/New_York — the one static zone the pre-drift specs carry —
so an unfaulted sync agrees with the oracle on business dates. The
records judge compares produced records against what the pre-drift spec
produces over the unfaulted simulator (REQ-083 A3, one judging regime
for HTTP vendors and FIAS alike).

Removed from the draw (REQ-083 B7): dates/tz_shift has no hour/day
offset arg in the closed transform set (adding one is a plan change),
so no spec patch can express its repair — it is never drawn. Meridian
never draws dates/format either (REQ-083 D18: a UTC-instant vendor
serves no verbatim date field; the cutoff trap stays, keyed no_patch).
Semantic gross_net scaling is likewise patch-inexpressible (no factor
arg): it is keyed degraded_question (one operator question; a guessed
factor is a wrong patch).
