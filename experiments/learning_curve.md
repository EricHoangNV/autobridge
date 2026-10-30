# Learning curve (REQ-024 B4)

Scenario: `page_to_cursor` — the same drift injected 5 times in a row.
Metric: frontier tokens per repair for the agent baseline (`frontier_agent`),
full AutoBridge from empty knowledge (`autobridge_cold`), and AutoBridge after
learning on the dev split (`autobridge_warm`).

Provenance: frontier_agent via FakeOpenCodeDriver (model `fake-opencode`); engine arms via ReplayProvider (frontier `muse-spark-1.3`). Fake-labelled rows are rehearsal, not publication numbers: rerun with
`FRONTIER_API_KEY` set for live tokens.

Expected (REQ-030 2b: proposing caches nothing): the agent pays
model tokens on every repair (it has no memory across repairs).
Cold repeats re-derive at L1 with 0 model tokens each — L0 hits
only after an approval teaches the fix. Warm hits L0 from the
start only when its dev-split fixes verified (see provenance);
without a verifier it repeats cold exactly.

| repeat | frontier_agent | autobridge_cold | autobridge_warm |
| --- | --- | --- | --- |
| 1 | 2000 | 0 | 0 |
| 2 | 2000 | 0 | 0 |
| 3 | 2000 | 0 | 0 |
| 4 | 2000 | 0 | 0 |
| 5 | 2000 | 0 | 0 |

Rung reached per repeat (learning is the route once the detector
gap closes: first repair via the ladder, then L0 hits):

| repeat | cold rung | cold decision | warm rung | warm decision |
| --- | --- | --- | --- | --- |
| 1 | 1 | propose | 1 | propose |
| 2 | 1 | propose | 1 | propose |
| 3 | 1 | propose | 1 | propose |
| 4 | 1 | propose | 1 | propose |
| 5 | 1 | propose | 1 | propose |

Totals: autobridge_cold=0, autobridge_warm=0, frontier_agent=10000

