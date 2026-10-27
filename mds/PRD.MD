# AutoBridge — PRD index

**Date:** 2026-10-27
**The plan itself:** `AutoBridge_Implementation_Plan_2026-10-26.md` (companion to `AutoBridge_Plan_2026-10-26_v2.md`, the thesis document, not in this repo).

This file is the stable index agents use to find the right section of the plan. It changes only when the owner (re)plans a feature. **Never edit it or the plan from a task run.** Tasks live in `PRD-TRACKING.md`.

## Where to read what (plan §)

| Topic | Section |
|---|---|
| Design principles (repairs are data, trust boundaries, ladder, outcomes, evidence) | §1 |
| Services, Docker networks | §2.1 |
| Repository layout | §2.2 |
| Core data model (SQLAlchemy) | §2.3 |
| ConnectorSpec v1 (YAML shape, transforms, rules) | §2.4 |
| Repair ladder, drift classes, the 7 deterministic rules | §2.5 |
| Hypothesis engine, probe selector, budgets | §2.6 |
| Verifier (oracle, regression, mutation tests, invalid-patch trap, dev/heldout) | §2.7 |
| Simulators (AtlasPMS, HarborPMS, LegacyStay) and the scenario catalog | §2.8 |
| LLM adapter and budgets | §2.9 |
| Benchmark harness | §2.10 |
| Control plane API endpoints and the four UI screens | §2.11 |
| Quality bar (coverage, healthz/readyz, e2e as deliverable, definition of done) | §3 |
| Phase 1 workstreams W1–W10: deliverables, passing criteria, service confirmation | §4 |
| Phase 2 stretch S1–S7 and entry conditions; slip order; hard dates | §5 |
| Demo script (six steps) | §6 |
| Reusability hooks | §7 |
| Verification ladder (`make test / up+smoke / e2e / bench / demo`, clean clone) | §8 |
| Open risks | §9 |

## Hard dates (plan §5)

- Phase 2 stop-rule checkpoint: end of Nov 1, 2026
- Feature freeze: Nov 2
- Heldout benchmark run: Nov 3
- Submission: Nov 4

## Owner decisions log

Decisions made after the plan was written go here, dated, so agents do not have to re-derive them.

- 2026-10-27: Repo skeleton and ralph loop set up; no code yet. Task ids are `P<phase>.W<workstream>.<n>` (Phase 1) and `P<phase>.S<item>.<n>` (Phase 2).
