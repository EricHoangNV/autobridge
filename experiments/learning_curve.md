# Learning curve (REQ-024 B4)

Scenario: `page_to_cursor` — the same drift injected 5 times in a row.
Metric: frontier tokens per repair for the agent baseline (`frontier_agent`),
full AutoBridge from empty knowledge (`autobridge_cold`), and AutoBridge after
learning on the dev split (`autobridge_warm`).

Provenance: frontier_agent via FakeOpenCodeDriver (model `fake-opencode`); engine arms via ReplayProvider (frontier `muse-spark-1.3`). Fake-labelled rows are rehearsal, not publication numbers: rerun with
`FRONTIER_API_KEY` set for live tokens.

Expected: the agent pays model tokens on every repair (it has no
memory across repairs); the engine pays 0 throughout (deterministic
repairs cost no model tokens by design) and learning shows in the
rung row: cold repairs via the ladder once, then L0 hits; warm is
L0 from the start.

| repeat | frontier_agent | autobridge_cold | autobridge_warm |
| --- | --- | --- | --- |
| 1 | 2000 | 0 | 0 |
| 2 | 2000 | 0 | 0 |
| 3 | 2000 | 0 | 0 |
| 4 | 2000 | 0 | 0 |
| 5 | 2000 | 0 | 0 |

Rung reached per repeat (learning is the route: first repair via the
ladder, then L0 hits):

| repeat | cold rung | cold decision | warm rung | warm decision |
| --- | --- | --- | --- | --- |
| 1 | 1 | propose | 0 | propose |
| 2 | 0 | propose | 0 | propose |
| 3 | 0 | propose | 0 | propose |
| 4 | 0 | propose | 0 | propose |
| 5 | 0 | propose | 0 | propose |

Totals: autobridge_cold=0, autobridge_warm=0, frontier_agent=10000

